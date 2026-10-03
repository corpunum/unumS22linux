#!/usr/bin/env python3
"""Pinned extracted-C regression for NPU system-resume failure ownership."""
from __future__ import annotations

import hashlib
import importlib.util
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
HELPER_PATH = ROOT / "tools/hardware/test-npu-probe-unwind.py"
BUILD_PROFILE_PATH = ROOT / "tools/hardware/build-npu-six-profile.py"
PATCH_PATH = ROOT / "tools/hardware/npu-system-resume-error-unwind.patch"
NPU13_PATCH_PATH = ROOT / "tools/hardware/npu-interface-open-unwind.patch"
HARNESS_PATH = ROOT / "tools/hardware/npu-system-resume-error-unwind-harness.c"

SOURCE_TREE_ENV = "S22_NPU_PROBE_SOURCE_TREE"
COMPOSED_TREE_ENV = "S22_NPU_SYSTEM_COMPOSED_SOURCE_TREE"
PINNED_BASE = "4e5c5ad7d950e4de0688b5663965f2075654b2ad"
DERIVED_COMMIT = "3fca50941422439b2019db2e4a3dc1016b2138a1"
COMPOSED12_COMMIT = "e9c3016233a72ceccb13e537f0b7ef72426582b9"
DEFAULT_DERIVED_TREE = Path("/home/corpunum/s22-workers/camera-kernel-build-20260927")
DEFAULT_COMPOSED_TREE = Path("/home/corpunum/s22-linux/builds/npu-native-twelve-kernel-20261003")

SYSTEM_C = "drivers/vision/npu/core/npu-system.c"
DEVICE_C = "drivers/vision/npu/core/npu-device.c"
HWDEV_C = "drivers/vision/npu/core/npu-hw-device.c"
INTERFACE_C = "drivers/vision/npu/core/interface/hardware/npu-interface.c"
SYSTEM_SHA256 = "96eaa6bf1511f3e6414e3e376d62592229454a2ea1687d760b7bb8e5952b1a05"
DEVICE_SHA256 = "98be21e422ca864cc971dfe6a78e71b292d7691cb625c1f029bf4502100644ab"
COMPOSED_DEVICE_SHA256 = "a281fd35f2311951328d797824b8dbb165639bfc7977da30044bb764688cdc11"
HWDEV_SHA256 = "b052462aa4919458a2b86c7ba0aed78bfdb938690cd58d5dcd01bdf0d75a312b"
INTERFACE_SHA256 = "c2deaa0abd990184b64373bb13983f048b925e0f5f421f6623de7de85f667108"
RAW_HWDEV_SHA256 = "14617a6f8e5b08e1bb169618daa8544f2680ad6709cb9f3b9730919d4dc8e16f"
RAW_INTERFACE_SHA256 = INTERFACE_SHA256
NPU13_PATCH_SHA256 = "95e63b45d60e0a2611c1f2dcab4428e5658a03ff9954b8197d175ac6fec139cb"
FROZEN_NPU14_COMMIT = "4a22948184f101f2bd80d2f44eef44b46004b790"
FROZEN_NPU14_PATCH_SHA256 = "464a78b43f7ef0cc7e26b5f69075980460211f9c789d0a418b36d44be548968e"
MAX_PATCH_BYTES = 512 * 1024


def check(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    check(spec is not None and spec.loader is not None,
          f"cannot load existing pinned NPU source/stack loader: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


SOURCE_LOADER = load_module(HELPER_PATH, "s22_npu_resume_source_loader")
BUILD_PROFILE = load_module(BUILD_PROFILE_PATH, "s22_npu_resume_build_profile")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def git_output(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True,
        check=False, timeout=10,
    )
    check(result.returncode == 0,
          f"git {' '.join(args)} failed in {root}: {result.stderr.strip()}")
    return result.stdout.strip()


def read_pinned_local(root: Path, commit: str, paths: tuple[tuple[str, str], ...],
                      label: str) -> dict[str, bytes]:
    root = root.expanduser()
    check(not root.is_symlink(), f"{label} tree must not be a symlink: {root}")
    root = root.resolve()
    check(root.is_dir(),
          f"{label} tree is absent or a symlink: {root}")
    check(git_output(root, "rev-parse", "HEAD") == commit,
          f"{label} tree must be {commit}")
    check(not git_output(root, "status", "--porcelain"),
          f"{label} tree must be clean")
    if commit == DERIVED_COMMIT:
        unchanged = subprocess.run(
            ["git", "-C", str(root), "diff", "--quiet", PINNED_BASE, commit,
             "--", *(relative for relative, _ in paths)],
            capture_output=True, text=True, check=False, timeout=10,
        )
        check(unchanged.returncode == 0,
              f"{label} paths changed from raw pinned source")
    loaded = {}
    for relative, expected in paths:
        data = (root / relative).read_bytes()
        check(len(data) <= SOURCE_LOADER.MAX_SOURCE_BYTES,
              f"{label} file exceeds existing source bound: {relative}")
        check(digest(data) == expected,
              f"{label} digest mismatch for {relative}: {digest(data)}")
        loaded[relative] = data
    return loaded


def load_public_source() -> dict[str, bytes]:
    paths = (
        (SYSTEM_C, SYSTEM_SHA256),
        (DEVICE_C, DEVICE_SHA256),
        (HWDEV_C, RAW_HWDEV_SHA256),
        (INTERFACE_C, RAW_INTERFACE_SHA256),
    )
    old = {relative: SOURCE_LOADER.SOURCE_SHA256.get(relative)
           for relative, _ in paths}
    for relative, expected in paths:
        SOURCE_LOADER.SOURCE_SHA256[relative] = expected
    try:
        loaded = {relative: SOURCE_LOADER.fetch_source(relative)
                  for relative, _ in paths}
    finally:
        for relative, _ in paths:
            if old[relative] is None:
                SOURCE_LOADER.SOURCE_SHA256.pop(relative, None)
            else:
                SOURCE_LOADER.SOURCE_SHA256[relative] = old[relative]
    for relative, expected in paths:
        check(digest(loaded[relative]) == expected,
              f"raw public {relative} digest mismatch")
    print(f"SOURCE: bounded public fetch from {SOURCE_LOADER.SOURCE_URL}/{PINNED_BASE}")
    return loaded


def function_body(source: str, marker: str) -> str:
    start = source.find(marker)
    while start >= 0:
        brace = source.find("{", start)
        semicolon = source.find(";", start)
        if semicolon >= 0 and (brace < 0 or semicolon < brace):
            start = source.find(marker, semicolon + 1)
            continue
        check(brace >= 0, f"pinned source function has no body: {marker}")
        depth = 0
        for end in range(brace, len(source)):
            if source[end] == "{":
                depth += 1
            elif source[end] == "}":
                depth -= 1
                if depth == 0:
                    return source[start:end + 1]
        raise RuntimeError(f"unterminated pinned function: {marker}")
    raise RuntimeError(f"pinned source function not found: {marker}")


def enum_definition(source: str, marker: str) -> str:
    start = source.find(marker)
    check(start >= 0, f"pinned source enum not found: {marker}")
    brace = source.find("{", start)
    end = source.find("};", brace)
    check(brace >= 0 and end >= 0, f"pinned source enum is incomplete: {marker}")
    return source[start:end + 2]


def patch_paths(patch: bytes) -> set[str]:
    text = patch.decode("utf-8", errors="strict")
    paths = set(re.findall(r"^diff --git a/(\S+) b/\S+$", text, re.MULTILINE))
    if not paths:
        paths = set(re.findall(r"^--- a/(\S+)$", text, re.MULTILINE))
    return paths


def apply_selected_patch(tree: Path, patch: bytes, label: str,
                         selected: tuple[str, ...]) -> tuple[str, ...]:
    check(len(patch) <= MAX_PATCH_BYTES, f"patch exceeds bounded size: {label}")
    touched = tuple(sorted(patch_paths(patch).intersection(selected)))
    if not touched:
        return ()
    text = patch.decode("utf-8", errors="strict")
    for check_only in (True, False):
        command = ["git", "apply", "--whitespace=error-all"]
        if check_only:
            command.append("--check")
        for relative in touched:
            command.extend(("--include", relative))
        command.append("-")
        result = subprocess.run(
            command, cwd=tree, input=text, capture_output=True, text=True,
            check=False, timeout=10,
        )
        operation = "--check" if check_only else "apply"
        check(result.returncode == 0,
              f"ordinary selected-path git apply {operation} failed for {label}:\n"
              f"{result.stderr}")
    return touched


def copy_sources(sources: dict[str, bytes], destination: Path) -> None:
    for relative, data in sources.items():
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


def read_selected_sources(tree: Path) -> dict[str, bytes]:
    return {relative: (tree / relative).read_bytes()
            for relative in (SYSTEM_C, DEVICE_C, HWDEV_C, INTERFACE_C)}


def legacy_npu14_patch() -> bytes:
    relative = str(PATCH_PATH.relative_to(ROOT))
    result = subprocess.run(
        ["git", "-C", str(ROOT), "show",
         f"{FROZEN_NPU14_COMMIT}:{relative}"],
        capture_output=True, check=False, timeout=10,
    )
    check(result.returncode == 0,
          "frozen pre-correction NPU14 patch commit is unavailable")
    check(digest(result.stdout) == FROZEN_NPU14_PATCH_SHA256,
          "frozen pre-correction NPU14 patch digest changed")
    return result.stdout


def verify_optional_local_fixtures(public: dict[str, bytes]) -> None:
    raw_paths = ((SYSTEM_C, SYSTEM_SHA256), (DEVICE_C, DEVICE_SHA256),
                 (HWDEV_C, RAW_HWDEV_SHA256),
                 (INTERFACE_C, RAW_INTERFACE_SHA256))
    configured_derived = os.environ.get(SOURCE_TREE_ENV)
    derived_root = Path(configured_derived).expanduser() if configured_derived else DEFAULT_DERIVED_TREE
    if configured_derived or derived_root.exists() or derived_root.is_symlink():
        derived = read_pinned_local(derived_root, DERIVED_COMMIT, raw_paths,
                                    "optional clean 3fca-derived fixture")
        check(derived == public,
              "public raw-pinned sources differ from supplied clean-derived fixture")
        print(f"SOURCE: optional clean derived fixture {DERIVED_COMMIT} verified")
    else:
        print("SOURCE: optional clean derived fixture absent; public route continues")

    configured_composed = os.environ.get(COMPOSED_TREE_ENV)
    composed_root = Path(configured_composed).expanduser() if configured_composed else DEFAULT_COMPOSED_TREE
    npu12_paths = ((SYSTEM_C, SYSTEM_SHA256), (DEVICE_C, COMPOSED_DEVICE_SHA256),
                   (HWDEV_C, HWDEV_SHA256), (INTERFACE_C, INTERFACE_SHA256))
    if configured_composed or composed_root.exists() or composed_root.is_symlink():
        composed = read_pinned_local(composed_root, COMPOSED12_COMMIT,
                                     npu12_paths,
                                     "optional clean composed NPU12 fixture")
        changed = subprocess.run(
            ["git", "-C", str(composed_root), "diff", "--name-only",
             "872bffb8ea2ea657f94d10b866dc655b5718d6db",
             COMPOSED12_COMMIT, "--", SYSTEM_C, DEVICE_C, HWDEV_C, INTERFACE_C],
            capture_output=True, text=True, check=False, timeout=10,
        )
        check(changed.returncode == 0 and not changed.stdout.strip(),
              "NPU9-12 source history unexpectedly changes a selected NPU source input")
        print("SOURCE: optional NPU12 fixture and NPU8-to-NPU12 selected-path history verified")
    else:
        print("SOURCE: optional composed NPU12 fixture absent; public composition hash checks remain active")


def compose_public_source(public: dict[str, bytes]) -> dict[str, dict[str, bytes]]:
    selected = (SYSTEM_C, DEVICE_C, HWDEV_C, INTERFACE_C)
    npu12_hashes = {
        SYSTEM_C: SYSTEM_SHA256,
        DEVICE_C: COMPOSED_DEVICE_SHA256,
        HWDEV_C: HWDEV_SHA256,
        INTERFACE_C: INTERFACE_SHA256,
    }
    patch_specs = BUILD_PROFILE.NATIVE_EIGHT_PATCHES
    for name, expected in patch_specs:
        path = ROOT / "tools/hardware" / name
        check(path.is_file(), f"pinned native-eight source patch is missing: {name}")
        data = path.read_bytes()
        check(digest(data) == expected,
              f"pinned native-eight patch digest mismatch: {name}")
    check(digest(NPU13_PATCH_PATH.read_bytes()) == NPU13_PATCH_SHA256,
          "NPU13 interface patch bytes changed")
    check(digest(PATCH_PATH.read_bytes()) != FROZEN_NPU14_PATCH_SHA256,
          "current NPU14 patch unexpectedly equals its pre-correction predecessor")

    before_npu14: dict[str, bytes]
    prior_npu14: dict[str, bytes]
    after_npu14: dict[str, bytes]
    with tempfile.TemporaryDirectory(prefix="npu-system-resume-compose-") as temp:
        parent = Path(temp)
        npu12 = parent / "npu12"
        copy_sources(public, npu12)
        for name, _expected in patch_specs:
            path = ROOT / "tools/hardware" / name
            touched = apply_selected_patch(npu12, path.read_bytes(), name, selected)
            if touched:
                print(f"SOURCE: selected-path ordinary apply {name}: {', '.join(touched)}")
        npu12_sources = read_selected_sources(npu12)
        for relative, expected in npu12_hashes.items():
            check(digest(npu12_sources[relative]) == expected,
                  f"NPU12 selected-source hash mismatch for {relative}: "
                  f"{digest(npu12_sources[relative])}")
        print("SOURCE: public raw source + pinned native-eight profile patches reproduce exact NPU12 selected hashes")

        npu13 = parent / "npu13"
        copy_sources(npu12_sources, npu13)
        apply_selected_patch(npu13, NPU13_PATCH_PATH.read_bytes(),
                             "NPU13 interface patch after selected NPU12 stack",
                             selected)
        before_npu14 = read_selected_sources(npu13)

        legacy = parent / "legacy-npu14"
        copy_sources(before_npu14, legacy)
        apply_selected_patch(legacy, legacy_npu14_patch(),
                             "frozen pre-correction NPU14 patch after NPU13",
                             selected)
        prior_npu14 = read_selected_sources(legacy)

        fixed = parent / "corrected-npu14"
        copy_sources(before_npu14, fixed)
        apply_selected_patch(fixed, PATCH_PATH.read_bytes(),
                             "corrected NPU14 patch after NPU13",
                             selected)
        after_npu14 = read_selected_sources(fixed)

    check(before_npu14[SYSTEM_C] == public[SYSTEM_C],
          "NPU12+NPU13 system source must match raw pinned system source")
    check(after_npu14[SYSTEM_C] != prior_npu14[SYSTEM_C] and
          after_npu14[DEVICE_C] == prior_npu14[DEVICE_C],
          "correction must be confined to the NPU14 system source path")
    check(after_npu14[DEVICE_C] != before_npu14[DEVICE_C],
          "NPU14 patch must still alter the actual extracted npu-device.c")

    # Optional fixture checks are corroboration only; public composition above
    # is the runnable source route and does not depend on either private tree.
    verify_optional_local_fixtures(public)
    with tempfile.TemporaryDirectory(prefix="npu-system-resume-derived-") as temp:
        derived_root = Path(os.environ.get(SOURCE_TREE_ENV, str(DEFAULT_DERIVED_TREE)))
        if derived_root.exists() or derived_root.is_symlink():
            derived = read_pinned_local(
                derived_root, DERIVED_COMMIT,
                ((SYSTEM_C, SYSTEM_SHA256), (DEVICE_C, DEVICE_SHA256)),
                "clean 3fca-derived fixture",
            )
            derived_copy = Path(temp)
            copy_sources(derived, derived_copy)
            apply_selected_patch(derived_copy, PATCH_PATH.read_bytes(),
                                 "corrected NPU14 patch on clean 3fca-derived fixture",
                                 (SYSTEM_C, DEVICE_C))
            print("SOURCE: ordinary corrected-patch apply passed on optional clean-derived fixture")
    print("SOURCE: selected NPU12->NPU13->NPU14 apply order verified on public exact-SHA inputs")
    return {"before": before_npu14, "precorrection": prior_npu14,
            "after": after_npu14}


def render_harness(template: str, system_bytes: bytes, device_bytes: bytes,
                   hwdev_bytes: bytes) -> str:
    system = system_bytes.decode("utf-8")
    device = device_bytes.decode("utf-8")
    hwdev = hwdev_bytes.decode("utf-8")
    enums = "\n\n".join((
        enum_definition(system, "enum npu_system_resume_steps"),
        enum_definition(system, "enum npu_system_resume_soc_steps"),
    ))
    allocator = function_body(system, "int npu_system_alloc_fw_dram_log_buf(")
    system_functions = "\n\n".join(function_body(system, marker) for marker in (
        "int npu_system_open(",
        "int npu_system_close(",
        "static int npu_system_soc_resume(",
        "int npu_system_soc_suspend(",
        "int npu_system_resume(",
        "int npu_system_suspend(",
    ))
    device_functions = "\n\n".join(function_body(device, marker) for marker in (
        "static int __npu_device_power_on(",
        "int npu_device_bootup(",
        "static int npu_device_runtime_suspend(",
        "static int npu_device_runtime_resume(",
    ))
    hwdev_function = function_body(hwdev, "static int npu_hwdev_default_boot(")
    for marker, content in (
        ("/* ACTUAL_RESUME_ENUMS */", enums),
        ("/* ACTUAL_FW_LOG_ALLOCATOR */", allocator),
        ("/* ACTUAL_SYSTEM_OPEN_CLOSE_SOC_RESUME_SUSPEND */", system_functions),
        ("/* ACTUAL_DEVICE_POWER_ON_BOOTUP_RUNTIME_RESUME */", device_functions),
        ("/* ACTUAL_HWDEV_DEFAULT_BOOT */", hwdev_function),
    ):
        check(template.count(marker) == 1, f"harness marker is not unique: {marker}")
        template = template.replace(marker, content, 1)
    return template


def compile_run(cc: list[str], level: str, text: str, directory: Path,
                baseline: bool, boot_ioctl: bool,
                precorrection: bool = False) -> str:
    variant = "boot" if boot_ioctl else "runtime"
    tag = "base" if baseline else "pre-correction" if precorrection else "fixed"
    source_file = directory / f"npu-system-resume-{variant}-{tag}-{level[2:]}.c"
    binary = directory / f"npu-system-resume-{variant}-{tag}-{level[2:]}"
    source_file.write_text(text, encoding="utf-8")
    command = [*cc, "-std=c11", "-Wall", "-Wextra", "-Werror",
               "-Wno-unused-parameter", "-Wno-unused-function",
               "-Wno-unused-variable", "-Wno-unused-but-set-variable",
               "-pthread", level]
    if baseline:
        command.append("-DEXPECT_BASELINE=1")
    if precorrection:
        command.append("-DEXPECT_PRECORRECTION=1")
    if boot_ioctl:
        command.append("-DTEST_BOOT_IOCTL=1")
    command.extend((str(source_file), "-o", str(binary)))
    built = subprocess.run(command, capture_output=True, text=True,
                           check=False, timeout=30)
    check(built.returncode == 0,
          f"actual extracted source did not compile at {level}/{variant}/"
          f"{'baseline' if baseline else 'pre-correction' if precorrection else 'patched'}:\n{built.stderr}")
    result = subprocess.run([str(binary)], capture_output=True, text=True,
                            check=False, timeout=15)
    check(result.returncode == 0,
          f"actual extracted source failed at {level}/{variant}/"
          f"{'baseline' if baseline else 'pre-correction' if precorrection else 'patched'}:\n"
          f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}")
    return result.stdout


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--cc", default=os.environ.get("CC", "cc"))
    parser.add_argument(
        "--local-only", action="store_true",
        help="use the exact SHA-pinned clean derived source when public fetch is unavailable",
    )
    args = parser.parse_args()
    print(f"PY: sys.flags.optimize={sys.flags.optimize}")
    if args.local_only:
        derived_root = Path(os.environ.get(SOURCE_TREE_ENV,
                                           str(DEFAULT_DERIVED_TREE)))
        public = read_pinned_local(
            derived_root, DERIVED_COMMIT,
            ((SYSTEM_C, SYSTEM_SHA256), (DEVICE_C, DEVICE_SHA256),
             (HWDEV_C, RAW_HWDEV_SHA256),
             (INTERFACE_C, RAW_INTERFACE_SHA256)),
            "raw-pinned source bytes from clean derived fixture",
        )
        print("SOURCE: --local-only; four clean-derived source files match raw pinned SHA-256 values")
    else:
        public = load_public_source()
    composed = compose_public_source(public)
    template = HARNESS_PATH.read_text(encoding="utf-8")
    baseline = render_harness(template, composed["before"][SYSTEM_C],
                              composed["before"][DEVICE_C],
                              composed["before"][HWDEV_C])
    precorrection = render_harness(
        template, composed["precorrection"][SYSTEM_C],
        composed["precorrection"][DEVICE_C],
        composed["precorrection"][HWDEV_C])
    fixed = render_harness(template, composed["after"][SYSTEM_C],
                           composed["after"][DEVICE_C],
                           composed["after"][HWDEV_C])
    compiler = shlex.split(args.cc)
    check(compiler, "C compiler command cannot be empty")

    with tempfile.TemporaryDirectory(prefix="npu-system-resume-c-build-") as temp:
        directory = Path(temp)
        total = 0
        for boot_ioctl in (True, False):
            for level in ("-O0", "-O2"):
                base_output = compile_run(compiler, level, baseline, directory,
                                         baseline=True, boot_ioctl=boot_ioctl)
                fixed_output = compile_run(compiler, level, fixed, directory,
                                          baseline=False, boot_ioctl=boot_ioctl)
                precorrection_output = compile_run(
                    compiler, level, precorrection, directory,
                    baseline=False, boot_ioctl=boot_ioctl, precorrection=True)
                expected_base = (
                    "REPRO: firmware error is reported as runtime-resume success",
                    "REPRO: bootup error path directly closes with the NPU CPU still on",
                    "REPRO: failed CPU inverse is masked and its ownership is erased",
                    "REPRO: suspend returns success after interface-close failure",
                    "REPRO: failed CPU-off is masked and later clock teardown proceeds",
                    "REPRO: firmware-buffer allocation failure returns success",
                    "REPRO: failed CPU-on can leave partial CPU state without a resume owner bit",
                    "REPRO: runtime-suspend callback sees false success after interface close error",
                    "REPRO: bootup closes partial firmware-buffer owner after swallowed allocation error",
                )
                expected_fixed = (
                    "PASS: runtime caller gets original firmware error after owned rollback",
                    "PASS: bootup error caller closes only after completed resume rollback",
                    "PASS: failed inverse retains ownership and blocks retry/open/close",
                    "PASS: interface inverse failure preserves lower-layer ownership",
                    "PASS: CPU inverse failure retains its state and lower resources",
                    "PASS: actual partial global log allocation is quarantined after failed resume",
                    "PASS: resume/suspend success roundtrip supports a clean explicit retry",
                    "PASS: uncertain CPU acquisition is quarantined without guessed inverse",
                    "PASS: actual runtime-suspend caller propagates cleanup error and retains ownership",
                    "PASS: bootup caller cannot close memory with partial global buffers",
                )
                if boot_ioctl:
                    base_required = (*expected_base[1:7], expected_base[8])
                    fixed_required = (*expected_fixed[1:8], expected_fixed[9])
                else:
                    base_required = (expected_base[0], *expected_base[3:4],
                                     expected_base[4], expected_base[5], expected_base[6],
                                     expected_base[7], expected_base[8])
                    fixed_required = (expected_fixed[0], *expected_fixed[3:6],
                                      expected_fixed[6], expected_fixed[7],
                                      expected_fixed[8], expected_fixed[9],
                                      "PASS: runtime PM failed-resume reference remains balanced")
                for text in base_required:
                    check(text in base_output,
                          f"required baseline failure reproduction missing at {level}/{boot_ioctl}: {text}")
                for text in fixed_required:
                    check(text in fixed_output,
                          f"required patched ownership regression missing at {level}/{boot_ioctl}: {text}")
                check("REPRO: first firmware log allocation failure returns success" in base_output,
                      "baseline first-allocation failure repro is missing")
                check("PASS: first actual log allocation failure returns -ENOMEM without buffer ownership" in fixed_output,
                      "patched first-allocation failure regression is missing")
                check("REPRO: pre-correction NPU14 requests firmware shutdown before unknown-SoC quarantine" in precorrection_output,
                      f"pre-correction ordering negative control missing at {level}/{boot_ioctl}")
                check("PASS: actual NPU hwdev callback balances PM on failure and success" in base_output and
                      "PASS: actual NPU hwdev callback balances PM on failure and success" in fixed_output,
                      "actual default hwdev callback refcount regression is missing")
                if not boot_ioctl:
                    for text in (
                        "REPRO: clock-prepare failure is reported as resume success",
                        "REPRO: failed STM-enable can leave live STM and CPU under false success",
                        "REPRO: STM-disable error is masked and cleanup continues",
                    ):
                        check(text in base_output,
                              f"runtime-PM baseline reproduction missing at {level}: {text}")
                    for text in (
                        "PASS: clock-prepare failure propagates and rolls back earlier owned stages",
                        "PASS: uncertain STM acquisition is quarantined without guessed inverse",
                        "PASS: partial STM-enable failure preserves firmware and wake ownership before quarantine",
                        "PASS: STM inverse failure retains ownership and stops lower teardown",
                    ):
                        check(text in fixed_output,
                              f"runtime-PM patched regression missing at {level}: {text}")
                else:
                    check("PASS: partial CPU_ON failure preserves firmware and wake ownership before quarantine" in fixed_output,
                          f"configured CPU_ON pre-teardown guard missing at {level}")
                total += 3
                print(f"C: {level} {'CONFIG_NPU_USE_BOOT_IOCTL' if boot_ioctl else 'runtime-PM'} baseline+pre-correction+patched passed")
        print(f"PASS: {total} extracted-C compile/run jobs across O0/O2, both callers, and the pre-correction control")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
