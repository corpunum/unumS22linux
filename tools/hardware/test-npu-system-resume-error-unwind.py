#!/usr/bin/env python3
"""Pinned extracted-C regression for NPU system-resume failure ownership."""
from __future__ import annotations

import hashlib
import importlib.util
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
HELPER_PATH = ROOT / "tools/hardware/test-npu-probe-unwind.py"
STACK_PATH = ROOT / "tools/hardware/test-npu-candidate-stack.py"
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
NPU13_PATCH_SHA256 = "95e63b45d60e0a2611c1f2dcab4428e5658a03ff9954b8197d175ac6fec139cb"
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
STACK = load_module(STACK_PATH, "s22_npu_resume_stack_loader")


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
    root = root.expanduser().resolve()
    check(root.is_dir(), f"{label} tree is absent: {root}")
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
    paths = ((SYSTEM_C, SYSTEM_SHA256), (DEVICE_C, DEVICE_SHA256))
    old = SOURCE_LOADER.SOURCE_SHA256.get(DEVICE_C)
    SOURCE_LOADER.SOURCE_SHA256[DEVICE_C] = DEVICE_SHA256
    try:
        loaded = {relative: SOURCE_LOADER.fetch_source(relative)
                  for relative, _ in paths}
    finally:
        if old is None:
            SOURCE_LOADER.SOURCE_SHA256.pop(DEVICE_C, None)
        else:
            SOURCE_LOADER.SOURCE_SHA256[DEVICE_C] = old
    for relative, expected in paths:
        check(digest(loaded[relative]) == expected,
              f"raw public {relative} digest mismatch")
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


def apply_one(tree: Path, patch_path: Path, label: str) -> None:
    patch = patch_path.read_bytes()
    check(len(patch) <= MAX_PATCH_BYTES, f"patch exceeds bounded size: {patch_path.name}")
    checked = subprocess.run(
        ["git", "apply", "--check", "--whitespace=error-all", str(patch_path)],
        cwd=tree, capture_output=True, text=True, check=False, timeout=10,
    )
    check(checked.returncode == 0,
          f"ordinary git apply --check failed for {label}:\n{checked.stderr}")
    applied = subprocess.run(
        ["git", "apply", "--whitespace=error-all", str(patch_path)],
        cwd=tree, capture_output=True, text=True, check=False, timeout=10,
    )
    check(applied.returncode == 0,
          f"ordinary git apply failed for {label}:\n{applied.stderr}")


def build_composed_source(composed_root: Path, destination: Path) -> dict[str, dict[str, bytes]]:
    sources = read_pinned_local(
        composed_root, COMPOSED12_COMMIT,
        ((SYSTEM_C, SYSTEM_SHA256), (DEVICE_C, COMPOSED_DEVICE_SHA256),
         (HWDEV_C, HWDEV_SHA256), (INTERFACE_C, INTERFACE_SHA256)),
        "composed NPU12 kernel source",
    )
    for relative, data in sources.items():
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    check(digest(NPU13_PATCH_PATH.read_bytes()) == NPU13_PATCH_SHA256,
          "NPU13 interface patch bytes changed")
    apply_one(destination, NPU13_PATCH_PATH, "NPU13 interface patch after NPU12")
    before = {
        SYSTEM_C: (destination / SYSTEM_C).read_bytes(),
        DEVICE_C: (destination / DEVICE_C).read_bytes(),
        HWDEV_C: (destination / HWDEV_C).read_bytes(),
    }
    apply_one(destination, PATCH_PATH, "NPU14 system resume patch after NPU12+13")
    after = {
        SYSTEM_C: (destination / SYSTEM_C).read_bytes(),
        DEVICE_C: (destination / DEVICE_C).read_bytes(),
        HWDEV_C: (destination / HWDEV_C).read_bytes(),
    }
    return {"before": before, "after": after}


def verify_patch_order_and_source_identities(public: dict[str, bytes]) -> dict[str, bytes]:
    for relative, expected in ((SYSTEM_C, SYSTEM_SHA256), (DEVICE_C, DEVICE_SHA256)):
        check(digest(public[relative]) == expected,
              f"raw public identity is not pinned for {relative}")

    derived_root = Path(os.environ.get(SOURCE_TREE_ENV, str(DEFAULT_DERIVED_TREE)))
    derived = read_pinned_local(
        derived_root, DERIVED_COMMIT,
        ((SYSTEM_C, SYSTEM_SHA256), (DEVICE_C, DEVICE_SHA256)),
        "clean 3fca-derived fixture",
    )
    composed_root = Path(os.environ.get(COMPOSED_TREE_ENV, str(DEFAULT_COMPOSED_TREE)))
    composed_current = read_pinned_local(
        composed_root, COMPOSED12_COMMIT,
        ((SYSTEM_C, SYSTEM_SHA256), (DEVICE_C, COMPOSED_DEVICE_SHA256)),
        "clean composed NPU12 fixture",
    )
    check(derived == public,
          "raw public and clean-derived source bytes must match exactly")
    check(composed_current[SYSTEM_C] == public[SYSTEM_C],
          "NPU12 source must preserve the system file targeted by NPU14")

    # Verify existing NPU12 patch pins and add the NPU13 patch before NPU14 in
    # an isolated three-file copy. Neither actual kernel worktree is mutated.
    STACK.patch_paths()
    with tempfile.TemporaryDirectory(prefix="npu-system-resume-compose-") as temp:
        composite = Path(temp)
    output = build_composed_source(composed_root, composite)
    check(output["before"][SYSTEM_C] == public[SYSTEM_C],
          "NPU12+13 baseline system source must match raw pinned source")
    check(output["after"][SYSTEM_C] != output["before"][SYSTEM_C],
          "NPU14 patch must alter the real extracted npu-system.c")
    check(output["after"][DEVICE_C] != output["before"][DEVICE_C],
          "NPU14 patch must alter the real extracted npu-device.c")

    # Separately prove ordinary apply to the 3fca clean-derived source fixture.
    with tempfile.TemporaryDirectory(prefix="npu-system-resume-derived-") as temp:
        derived_copy = Path(temp)
        for relative, data in derived.items():
            target = derived_copy / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        apply_one(derived_copy, PATCH_PATH, "NPU14 patch on clean 3fca fixture")
    print("SOURCE: supplied raw-pinned system/device bytes match exact SHA-256 values")
    print(f"SOURCE: clean derived fixture {DERIVED_COMMIT} verified; ordinary apply passed")
    print(f"SOURCE: clean composed NPU12 {COMPOSED12_COMMIT}; NPU13 then NPU14 ordinary apply passed")
    return output["before"], output["after"]


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
                baseline: bool, boot_ioctl: bool) -> str:
    variant = "boot" if boot_ioctl else "runtime"
    tag = "base" if baseline else "fixed"
    source_file = directory / f"npu-system-resume-{variant}-{tag}-{level[2:]}.c"
    binary = directory / f"npu-system-resume-{variant}-{tag}-{level[2:]}"
    source_file.write_text(text, encoding="utf-8")
    command = [*cc, "-std=c11", "-Wall", "-Wextra", "-Werror",
               "-Wno-unused-parameter", "-Wno-unused-function",
               "-Wno-unused-variable", "-Wno-unused-but-set-variable",
               "-pthread", level]
    if baseline:
        command.append("-DEXPECT_BASELINE=1")
    if boot_ioctl:
        command.append("-DTEST_BOOT_IOCTL=1")
    command.extend((str(source_file), "-o", str(binary)))
    built = subprocess.run(command, capture_output=True, text=True,
                           check=False, timeout=30)
    check(built.returncode == 0,
          f"actual extracted source did not compile at {level}/{variant}/"
          f"{'baseline' if baseline else 'patched'}:\n{built.stderr}")
    result = subprocess.run([str(binary)], capture_output=True, text=True,
                            check=False, timeout=15)
    check(result.returncode == 0,
          f"actual extracted source failed at {level}/{variant}/"
          f"{'baseline' if baseline else 'patched'}:\n"
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
    if args.local_only:
        derived_root = Path(os.environ.get(SOURCE_TREE_ENV,
                                           str(DEFAULT_DERIVED_TREE)))
        public = read_pinned_local(
            derived_root, DERIVED_COMMIT,
            ((SYSTEM_C, SYSTEM_SHA256), (DEVICE_C, DEVICE_SHA256)),
            "raw-pinned source bytes from clean derived fixture",
        )
        print("SOURCE: --local-only; clean-derived file bytes match raw pinned SHA-256 values")
    else:
        public = load_public_source()
    composed_baseline, patched = verify_patch_order_and_source_identities(public)
    template = HARNESS_PATH.read_text(encoding="utf-8")
    baseline = render_harness(template, composed_baseline[SYSTEM_C],
                              composed_baseline[DEVICE_C], composed_baseline[HWDEV_C])
    fixed = render_harness(template, patched[SYSTEM_C], patched[DEVICE_C],
                           patched[HWDEV_C])
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
                        "PASS: STM inverse failure retains ownership and stops lower teardown",
                    ):
                        check(text in fixed_output,
                              f"runtime-PM patched regression missing at {level}: {text}")
                total += 2
                print(f"C: {level} {'CONFIG_NPU_USE_BOOT_IOCTL' if boot_ioctl else 'runtime-PM'} baseline+patched passed")
        print(f"PASS: {total} extracted-C compile/run jobs across O0/O2 and both resume callers")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
