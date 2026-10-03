#!/usr/bin/env python3
"""Bounded public-source extracted-C regression for imgloader shutdown status."""
from __future__ import annotations

import argparse
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
SOURCE_LOADER_PATH = ROOT / "tools/hardware/test-npu-probe-unwind.py"
BUILD_PROFILE_PATH = ROOT / "tools/hardware/build-npu-six-profile.py"
NPU13_PATCH_PATH = ROOT / "tools/hardware/npu-interface-open-unwind.patch"
NPU14_PATCH_PATH = ROOT / "tools/hardware/npu-system-resume-error-unwind.patch"
STATUS_PATCH_PATH = ROOT / "tools/hardware/npu-imgloader-shutdown-status.patch"
HARNESS_PATH = ROOT / "tools/hardware/npu-imgloader-shutdown-status-harness.c"

PINNED_BASE = "4e5c5ad7d950e4de0688b5663965f2075654b2ad"
COMPOSED12_COMMIT = "e9c3016233a72ceccb13e537f0b7ef72426582b9"
DEFAULT_COMPOSED_TREE = Path(
    "/home/corpunum/s22-linux/builds/npu-native-twelve-kernel-20261003")
COMPOSED_TREE_ENV = "S22_NPU_STATUS_COMPOSED_SOURCE_TREE"
MAX_PATCH_BYTES = 512 * 1024

IMGLOADER_C = "drivers/soc/samsung/imgloader.c"
IMGLOADER_H = "include/soc/samsung/imgloader.h"
SYSTEM_C = "drivers/vision/npu/core/npu-system.c"
BINARY_C = "drivers/vision/npu/core/npu-binary.c"
MFC_C = "drivers/media/platform/exynos/mfc/mfc_core_ops.c"
SOURCE_HASHES = {
    IMGLOADER_C: "e9898c3ac9b0c5210603028b7c0b7bc3623869762a99f35438d3d480d33e27f7",
    IMGLOADER_H: "5b000a5eba96435dc011f239ea867334385c5ca94541d31b190e25b296440cec",
    SYSTEM_C: "96eaa6bf1511f3e6414e3e376d62592229454a2ea1687d760b7bb8e5952b1a05",
    BINARY_C: "87b08d398d3468de827853544177af5a1264a35ee69a4648a728e35306ef5d36",
    MFC_C: "99c26285e07223a26625f006f89241c8b07b19772b0c4f9f49c965798778c669",
}

NATIVE_EIGHT_PATCHES = (
    ("npu-session-lifecycle-fix.patch",
     "1554436cb6624c542f9e04ac22a3b3545e55f94c59d3025ee6bdc1ec43168251"),
    ("npu-refcount-lifecycle-profile.patch",
     "8385e4210a807f96f972757cd6ca74a8077b0127ab112d8b877d012ccdc3cb7b"),
    ("npu-default-boot-callback-fix.patch",
     "f5ce216e34df11d8c6adee4a99c36d63f73593cf379e29de3a9de828ec2ee1e7"),
    ("npu-probe-unwind-fix.patch",
     "d3e2e590d4d3c956b10c724a15db996dacd07def204332f50a1c0513f56b4948"),
    ("npu-shutdown-lifecycle-profile.patch",
     "b986e1896305fda55f1d702ed6f12dde646e4a84b3ce91009203b9d77b7a00e7"),
    ("npu-shutdown-error-propagation.patch",
     "08374e96792f24d1e0e4fbca594bfce35537af8acace9296b66f27b531564e43"),
    ("npu-mailbox-missing-callback-reclaim.patch",
     "f109b57381b3f2afcf2638b518f50db59ef8c9f784debd949488c74f8ea5c39b"),
    ("npu-mailbox-debug-walk-bounds.patch",
     "20c700bfa11f13836c76c88cca28a4f8dfa459e5cf146292a814880cd5850b29"),
)
NPU13_PATCH_SHA256 = "95e63b45d60e0a2611c1f2dcab4428e5658a03ff9954b8197d175ac6fec139cb"
NPU14_PATCH_SHA256 = "f1af656f9e1b8ca2bf15e934031e0adf828c442267a17761ba64f7d7c611729a"
STATUS_PATCH_SHA256 = "e7355e8906b22f1decf00de59cd644aac7bfcf6d861d97cdb3ff9bf084428385"


def check(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def verify_status_patch(data: bytes) -> None:
    check(len(data) <= MAX_PATCH_BYTES, "provider-status patch exceeds byte bound")
    check(digest(data) == STATUS_PATCH_SHA256,
          "pinned provider-status patch bytes changed")


def verify_status_patch_integrity_controls() -> None:
    original = STATUS_PATCH_PATH.read_bytes()
    verify_status_patch(original)
    # A trailing newline leaves the touched paths and applicable diff unchanged.
    # It must still fail the exact-byte provenance gate, before source fetching.
    for label, changed in (("applicable diff mutation", original + b"\n"),
                           ("empty patch", b""),
                           ("oversized patch", b"x" * (MAX_PATCH_BYTES + 1))):
        try:
            verify_status_patch(changed)
        except RuntimeError:
            continue
        raise RuntimeError(f"provider-status integrity control accepted {label}")
    print("PATCH: provider-status exact pin and three rejection controls passed")


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    check(spec is not None and spec.loader is not None,
          f"cannot load required source/stack helper: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


SOURCE_LOADER = load_module(SOURCE_LOADER_PATH, "s22_npu_status_source_loader")
BUILD_PROFILE = load_module(BUILD_PROFILE_PATH, "s22_npu_status_build_profile")


def verify_hash(relative: str, data: bytes, label: str) -> None:
    actual = digest(data)
    check(actual == SOURCE_HASHES[relative],
          f"{label} hash mismatch for {relative}: {actual}")
    check(len(data) <= SOURCE_LOADER.MAX_SOURCE_BYTES,
          f"{label} source exceeds byte bound: {relative}")


def load_public_sources() -> dict[str, bytes]:
    check(tuple(BUILD_PROFILE.NATIVE_EIGHT_PATCHES) == NATIVE_EIGHT_PATCHES,
          "native-eight patch selection differs from the explicit pinned tuple")
    old = {relative: SOURCE_LOADER.SOURCE_SHA256.get(relative)
           for relative in SOURCE_HASHES}
    SOURCE_LOADER.SOURCE_SHA256.update(SOURCE_HASHES)
    try:
        sources = {relative: SOURCE_LOADER.fetch_source(relative)
                   for relative in SOURCE_HASHES}
    finally:
        for relative, expected in old.items():
            if expected is None:
                SOURCE_LOADER.SOURCE_SHA256.pop(relative, None)
            else:
                SOURCE_LOADER.SOURCE_SHA256[relative] = expected
    for relative, data in sources.items():
        verify_hash(relative, data, "raw public pinned")
    print(f"SOURCE: raw public files verified at {PINNED_BASE} ({len(sources)} files)")
    return sources


def git_output(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True,
        check=False, timeout=10,
    )
    check(result.returncode == 0,
          f"git {' '.join(args)} failed in optional fixture {root}: "
          f"{result.stderr.strip()}")
    return result.stdout.strip()


def verify_optional_composed_fixture(public: dict[str, bytes], *,
                                     skip_default: bool) -> None:
    configured = os.environ.get(COMPOSED_TREE_ENV)
    root = Path(configured).expanduser() if configured else DEFAULT_COMPOSED_TREE
    if not configured and (skip_default or
                           not (root.exists() or root.is_symlink())):
        state = "skipped by flag" if skip_default else "absent"
        print(f"SOURCE: optional NPU12 composed fixture {state}; public route continues")
        return
    check(not root.is_symlink(),
          f"optional NPU12 fixture must not be a symlink: {root}")
    check(root.is_dir(), f"optional NPU12 fixture is absent: {root}")
    check(git_output(root, "rev-parse", "HEAD") == COMPOSED12_COMMIT,
          f"optional NPU12 fixture must be {COMPOSED12_COMMIT}")
    check(not git_output(root, "status", "--porcelain"),
          "optional NPU12 fixture must be clean")
    for relative, expected in SOURCE_HASHES.items():
        data = (root / relative).read_bytes()
        verify_hash(relative, data, "optional NPU12")
        check(data == public[relative],
              f"optional NPU12 bytes differ from public pin for {relative}")
    print(f"SOURCE: optional read-only NPU12 fixture {COMPOSED12_COMMIT} verified")


def function_body(source: str, marker: str) -> str:
    start = source.find(marker)
    while start >= 0:
        brace = source.find("{", start)
        semicolon = source.find(";", start)
        if semicolon >= 0 and (brace < 0 or semicolon < brace):
            start = source.find(marker, semicolon + 1)
            continue
        check(brace >= 0, f"pinned function has no body: {marker}")
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


def definition(source: str, marker: str) -> str:
    start = source.find(marker)
    check(start >= 0, f"pinned declaration not found: {marker}")
    brace = source.find("{", start)
    end = source.find("};", brace)
    check(brace >= 0 and end >= 0,
          f"pinned declaration is incomplete: {marker}")
    return source[start:end + 2]


def enum_definition(source: str, marker: str) -> str:
    start = source.find(marker)
    check(start >= 0, f"pinned enum not found: {marker}")
    brace = source.find("{", start)
    end = source.find("};", brace)
    check(brace >= 0 and end >= 0, f"pinned enum is incomplete: {marker}")
    return source[start:end + 2]


def patch_paths(patch: bytes) -> set[str]:
    text = patch.decode("utf-8", errors="strict")
    paths = set(re.findall(r"^diff --git a/(\S+) b/\S+$", text, re.MULTILINE))
    if not paths:
        paths = set(re.findall(r"^--- a/(\S+)$", text, re.MULTILINE))
    return paths


def copy_sources(sources: dict[str, bytes], destination: Path) -> None:
    for relative, data in sources.items():
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


def apply_selected_patch(tree: Path, patch: bytes, label: str,
                         selected: tuple[str, ...]) -> tuple[str, ...]:
    check(len(patch) <= MAX_PATCH_BYTES, f"patch exceeds byte bound: {label}")
    touched = tuple(sorted(patch_paths(patch).intersection(selected)))
    if not touched:
        return ()
    patch_text = patch.decode("utf-8", errors="strict")
    for check_only in (True, False):
        command = ["git", "apply", "--whitespace=error-all"]
        if check_only:
            command.append("--check")
        for relative in touched:
            command.extend(("--include", relative))
        command.append("-")
        result = subprocess.run(
            command, cwd=tree, input=patch_text, capture_output=True,
            text=True, check=False, timeout=10,
        )
        phase = "--check" if check_only else "apply"
        check(result.returncode == 0,
              f"ordinary selected-path git apply {phase} failed for {label}:\n"
              f"{result.stderr}")
    return touched


def read_sources(tree: Path, paths: tuple[str, ...]) -> dict[str, bytes]:
    return {relative: (tree / relative).read_bytes() for relative in paths}


def verify_abi_and_callers(sources: dict[str, bytes],
                           fixed: dict[str, bytes]) -> None:
    header = sources[IMGLOADER_H].decode("utf-8")
    fixed_header = fixed[IMGLOADER_H].decode("utf-8")
    desc = definition(header, "struct imgloader_desc {")
    fixed_desc = definition(fixed_header, "struct imgloader_desc {")
    ops = definition(header, "struct imgloader_ops {")
    fixed_ops = definition(fixed_header, "struct imgloader_ops {")
    check(desc == fixed_desc and ops == fixed_ops,
          "provider status patch must not change descriptor/ops struct ABI")
    legacy_proto = "extern void imgloader_shutdown(struct imgloader_desc *desc);"
    check(header.count(legacy_proto) == 1 and
          fixed_header.count(legacy_proto) == 1,
          "existing exported void API declaration must remain unchanged")
    check("extern int imgloader_shutdown_status(struct imgloader_desc *desc);"
          in fixed_header,
          "new checked API declaration is missing from enabled header branch")
    check("static inline int imgloader_shutdown_status(struct imgloader_desc *desc) { return 0; }"
          in fixed_header,
          "config-disabled checked API stub is missing")

    mfc = sources[MFC_C].decode("utf-8")
    npu_binary = sources[BINARY_C].decode("utf-8")
    direct_mfc = re.findall(r"\bimgloader_shutdown\s*\(", mfc)
    check(len(direct_mfc) == 2,
          f"pinned MFC source call inventory changed: expected 2, got {len(direct_mfc)}")
    check(re.search(r"\.shutdown\s*=\s*NULL\s*,", npu_binary) is not None,
          "pinned NPU provider ops must still have no shutdown callback")
    print("SOURCE: legacy API declaration, structs, MFC callers, and NPU NULL callback verified")


def compose_sources(public: dict[str, bytes], *, skip_default_fixtures: bool
                    ) -> tuple[dict[str, bytes], dict[str, bytes]]:
    check(digest(NPU13_PATCH_PATH.read_bytes()) == NPU13_PATCH_SHA256,
          "pinned NPU13 patch bytes changed")
    check(digest(NPU14_PATCH_PATH.read_bytes()) == NPU14_PATCH_SHA256,
          "pinned corrected NPU14 patch bytes changed")
    selected = (SYSTEM_C,)
    stack_source_paths = (SYSTEM_C,)
    with tempfile.TemporaryDirectory(prefix="npu-imgloader-status-source-") as temp:
        root = Path(temp)
        npu14 = root / "npu14"
        copy_sources({SYSTEM_C: public[SYSTEM_C]}, npu14)
        for name, expected in NATIVE_EIGHT_PATCHES:
            path = ROOT / "tools/hardware" / name
            check(not path.is_symlink() and path.is_file(),
                  f"explicit native-eight patch is missing: {name}")
            patch = path.read_bytes()
            check(digest(patch) == expected,
                  f"native-eight patch digest mismatch: {name}")
            apply_selected_patch(npu14, patch, name, selected)
        pre_npu13 = (npu14 / SYSTEM_C).read_bytes()
        check(pre_npu13 == public[SYSTEM_C],
              "native-eight stack unexpectedly changes selected NPU system source")
        apply_selected_patch(npu14, NPU13_PATCH_PATH.read_bytes(),
                             "NPU13 after native-eight", selected)
        apply_selected_patch(npu14, NPU14_PATCH_PATH.read_bytes(),
                             "corrected NPU14 after NPU13", selected)
        npu14_system = (npu14 / SYSTEM_C).read_bytes()
        npu14_sources = dict(public)
        npu14_sources[SYSTEM_C] = npu14_system

        fixed = root / "fixed"
        copy_sources(npu14_sources, fixed)
        status_patch = STATUS_PATCH_PATH.read_bytes()
        verify_status_patch(status_patch)
        check(patch_paths(status_patch) == {IMGLOADER_C, IMGLOADER_H, SYSTEM_C},
              "provider-status patch must touch only provider C/header and NPU system C")
        apply_selected_patch(
            fixed, status_patch, "NPU imgloader shutdown status after NPU14",
            (IMGLOADER_C, IMGLOADER_H, SYSTEM_C))
        fixed_sources = read_sources(
            fixed, (IMGLOADER_C, IMGLOADER_H, SYSTEM_C, BINARY_C, MFC_C))
        verify_abi_and_callers(public, fixed_sources)
        baseline_sources = dict(npu14_sources)
        return baseline_sources, fixed_sources


def render_harness(template: str, sources: dict[str, bytes],
                   *, baseline: bool) -> str:
    img_c = sources[IMGLOADER_C].decode("utf-8")
    img_h = sources[IMGLOADER_H].decode("utf-8")
    system = sources[SYSTEM_C].decode("utf-8")
    types = "\n\n".join((
        definition(img_h, "struct imgloader_desc {"),
        definition(img_h, "struct imgloader_ops {"),
    ))

    release = function_body(
        img_c, "static int imgloader_release_fw_permission(")
    if baseline:
        provider = function_body(
            img_c, "void imgloader_shutdown(struct imgloader_desc *desc)")
    else:
        provider = function_body(
            img_c, "int imgloader_shutdown_status(struct imgloader_desc *desc)")
        provider += "\n\n" + function_body(
            img_c, "void imgloader_shutdown(struct imgloader_desc *desc)")
    disabled_api_parts = [function_body(
        img_h, "static inline void imgloader_shutdown(struct imgloader_desc *desc)")]
    if not baseline:
        disabled_api_parts.append(function_body(
            img_h,
            "static inline int imgloader_shutdown_status(struct imgloader_desc *desc)"))
    disabled_api = "\n\n".join(disabled_api_parts)
    provider_code = (
        "#if TEST_IMGLOADER_ENABLED\n" + release + "\n\n" + provider +
        "\n#else\n" + disabled_api + "\n#endif"
    )

    if baseline:
        enabled_wrappers = function_body(
            system, "void npu_imgloader_shutdown(struct npu_system *system)")
        disabled_wrappers = function_body(
            system, "static void npu_imgloader_shutdown(__attribute__((unused))")
    else:
        enabled_wrappers = "\n\n".join((
            function_body(system,
                          "static int npu_imgloader_shutdown_status(struct npu_system *system)"),
            function_body(system,
                          "void npu_imgloader_shutdown(struct npu_system *system)"),
        ))
        disabled_wrappers = "\n\n".join((
            function_body(system,
                          "static int npu_imgloader_shutdown_status(__attribute__((unused))"),
            function_body(system,
                          "static void npu_imgloader_shutdown(__attribute__((unused))"),
        ))
    wrappers = ("#if TEST_IMGLOADER_ENABLED\n" + enabled_wrappers +
                "\n#else\n" + disabled_wrappers + "\n#endif")

    enums = "\n\n".join((
        enum_definition(system, "enum npu_system_resume_steps"),
        enum_definition(system, "enum npu_system_resume_soc_steps"),
    ))
    open_close = "\n\n".join(function_body(system, marker) for marker in (
        "int npu_system_open(struct npu_system *system)",
        "int npu_system_close(struct npu_system *system)",
    ))
    suspend_functions = "\n\n".join(function_body(system, marker) for marker in (
        "int npu_system_soc_suspend(struct npu_system *system)",
        "int npu_system_suspend(struct npu_system *system)",
    ))
    for marker, content in (
        ("/* ACTUAL_IMGLOADER_TYPES */", types),
        ("/* ACTUAL_NPU_RESUME_ENUMS */", enums),
        ("/* ACTUAL_IMGLOADER_RELEASE_PERMISSION */", release),
        ("/* ACTUAL_IMGLOADER_SHUTDOWN_API */", provider_code),
        ("/* ACTUAL_NPU_IMGLOADER_WRAPPERS */", wrappers),
        ("/* ACTUAL_NPU_OPEN_CLOSE */", open_close),
        ("/* ACTUAL_NPU_SOC_SUSPEND_AND_SUSPEND */", suspend_functions),
    ):
        # The provider helper is included under the build-configured API block.
        if marker == "/* ACTUAL_IMGLOADER_RELEASE_PERMISSION */":
            content = ""
        check(template.count(marker) == 1, f"harness marker is not unique: {marker}")
        template = template.replace(marker, content, 1)
    return template


def compile_and_run(cc: list[str], level: str, source: str,
                    directory: Path, *, baseline: bool,
                    boot_ioctl: bool, imgloader_enabled: bool,
                    s2mpu_config: bool, secure_mode: bool = False) -> str:
    variant = "baseline" if baseline else "status"
    route = "boot" if boot_ioctl else "runtime"
    config = f"img{int(imgloader_enabled)}-s2mpu{int(s2mpu_config)}"
    if secure_mode:
        config += "-secure"
    stem = f"imgloader-status-{variant}-{route}-{config}-{level[2:]}"
    source_file = directory / f"{stem}.c"
    binary = directory / stem
    source_file.write_text(source, encoding="utf-8")
    command = [
        *cc, "-std=c11", "-Wall", "-Wextra", "-Werror",
        "-Wno-unused-parameter", "-Wno-unused-function",
        "-Wno-unused-variable", "-Wno-unused-but-set-variable", level,
        f"-DTEST_IMGLOADER_ENABLED={int(imgloader_enabled)}",
        f"-DTEST_S2MPU_CONFIG={int(s2mpu_config)}",
    ]
    if baseline:
        command.append("-DEXPECT_BASELINE=1")
    if boot_ioctl:
        command.append("-DTEST_BOOT_IOCTL=1")
    if secure_mode:
        command.append("-DTEST_SECURE_MODE=1")
    command.extend((str(source_file), "-o", str(binary)))
    built = subprocess.run(command, capture_output=True, text=True,
                           check=False, timeout=30)
    check(built.returncode == 0,
          f"actual extracted C failed to compile at {stem}:\n{built.stderr}")
    result = subprocess.run([str(binary)], capture_output=True, text=True,
                            check=False, timeout=15)
    check(result.returncode == 0,
          f"actual extracted C failed at {stem}:\n"
          f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}")
    return result.stdout


def verify_npu_guards(sources: dict[str, bytes], *, fixed: bool) -> None:
    system = sources[SYSTEM_C].decode("utf-8")
    if fixed:
        status_wrapper = function_body(
            system, "static int npu_imgloader_shutdown_status(struct npu_system *system)")
        check("return ret > 0 ? -EIO : ret;" in status_wrapper,
              "NPU status wrapper must retain negative-errno convention for PM callers")
    for marker, label in (
        ("int npu_system_open(struct npu_system *system)", "open"),
        ("int npu_system_close(struct npu_system *system)", "close"),
        ("int npu_system_resume(struct npu_system *system, u32 mode)", "resume"),
    ):
        body = function_body(system, marker)
        check("system->resume_steps || system->resume_soc_steps" in body and
              "return -EBUSY;" in body,
              f"NPU14 {label} guard must reject residual ownership")
    resume = function_body(
        system, "int npu_system_resume(struct npu_system *system, u32 mode)")
    resume_guard = resume.find(
        "if (system->resume_steps || system->resume_soc_steps)")
    first_resume_action = resume.find("npu_system_alloc_fw_dram_log_buf(system)")
    check(resume_guard >= 0 and "return -EBUSY;" in resume[resume_guard:] and
          first_resume_action > resume_guard,
          "runtime BOOTUP/resume must refuse outstanding shutdown ownership before firmware work")
    suspend = function_body(system, "int npu_system_suspend(struct npu_system *system)")
    cpu_guard = suspend.find("NPU_SYS_RESUME_SOC_CPU_ON_UNCERTAIN")
    interface_close = suspend.find("npu_interface_close(system)")
    provider_marker = ("npu_imgloader_shutdown_status(system)" if fixed else
                       "npu_imgloader_shutdown(system)")
    provider_shutdown = suspend.find(provider_marker)
    check(cpu_guard >= 0 and interface_close > cpu_guard and
          provider_shutdown > interface_close,
          "CPU uncertainty, interface close, and provider shutdown order changed")
    print("SOURCE: NPU status errno normalization; NPU14 resume/open/close and runtime BOOTUP refusal; CPU/interface/provider order verified")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cc", default=os.environ.get("CC", "cc"))
    parser.add_argument(
        "--skip-optional-local-fixtures", action="store_true",
        help="skip checking only the host-default NPU12 fixture; explicit env path remains required",
    )
    args = parser.parse_args()
    print(f"PY: sys.flags.optimize={sys.flags.optimize}")
    verify_status_patch_integrity_controls()
    public = load_public_sources()
    verify_optional_composed_fixture(
        public, skip_default=args.skip_optional_local_fixtures)
    baseline_sources, fixed_sources = compose_sources(
        public, skip_default_fixtures=args.skip_optional_local_fixtures)
    verify_npu_guards(fixed_sources, fixed=True)
    verify_npu_guards(baseline_sources, fixed=False)

    template = HARNESS_PATH.read_text(encoding="utf-8")
    baseline = render_harness(template, baseline_sources, baseline=True)
    fixed = render_harness(template, fixed_sources, baseline=False)
    compiler = shlex.split(args.cc)
    check(compiler, "C compiler command cannot be empty")
    total = 0
    with tempfile.TemporaryDirectory(prefix="npu-imgloader-status-c-build-") as temp:
        directory = Path(temp)
        for imgloader_enabled, s2mpu_config in ((True, True), (True, False),
                                                (False, True)):
            for boot_ioctl in (True, False):
                for level in ("-O0", "-O2"):
                    outputs = []
                    for source, is_baseline in ((baseline, True), (fixed, False)):
                        output = compile_and_run(
                            compiler, level, source, directory,
                            baseline=is_baseline, boot_ioctl=boot_ioctl,
                            imgloader_enabled=imgloader_enabled,
                            s2mpu_config=s2mpu_config)
                        outputs.append(output)
                        if imgloader_enabled and s2mpu_config:
                            required = (
                                ("REPRO: provider permission-release errno is swallowed; CPU_OFF follows",
                                 "REPRO: provider callback failure is swallowed; CPU_OFF follows",
                                 "REPRO: provider notify failure is swallowed; CPU_OFF follows")
                                if is_baseline else
                                ("PASS: permission-release failure preserves ownership and is one-shot",
                                 "PASS: callback failure after permission release is quarantined once",
                                 "PASS: notify failure after permission release is quarantined once")
                            )
                            for text in required:
                                check(text in output,
                                      f"required {'baseline' if is_baseline else 'status'} scenario missing at {level}/{boot_ioctl}: {text}")
                        check("PASS: clean S2MPU-supported route" in output and
                              "PASS: clean non-S2MPU route" in output and
                              "PASS: CPU_ON uncertainty still refuses" in output and
                              "PASS: legacy exported void shutdown signature" in output,
                              f"common API/clean-route/refusal coverage missing at {level}/{boot_ioctl}")
                    total += 2
                    print(f"C: {level} {'BOOT_IOCTL' if boot_ioctl else 'runtime-PM'} "
                          f"imgloader={int(imgloader_enabled)} "
                          f"S2MPU={int(s2mpu_config)} baseline+status passed")
        for boot_ioctl in (True, False):
            for level in ("-O0", "-O2"):
                for source, is_baseline in ((baseline, True), (fixed, False)):
                    output = compile_and_run(
                        compiler, level, source, directory,
                        baseline=is_baseline, boot_ioctl=boot_ioctl,
                        imgloader_enabled=True, s2mpu_config=True,
                        secure_mode=True)
                    check("PASS: secure warm-boot skip preserves existing provider behavior"
                          in output,
                          f"secure warm-boot route missing at {level}/{boot_ioctl}")
                    total += 1
                print(f"C: {level} {'BOOT_IOCTL' if boot_ioctl else 'runtime-PM'} "
                      "CONFIG_NPU_SECURE_MODE baseline+status passed")
    print(f"PASS: {total} extracted-C compile/run jobs at O0/O2 across baseline/status, "
          "BOOT_IOCTL/runtime-PM, S2MPU and config-disabled routes")
    print(f"PATCH: {digest(STATUS_PATCH_PATH.read_bytes())}")
    print(f"SOURCE: corrected NPU system SHA-256 {digest(fixed_sources[SYSTEM_C])}")
    print(f"SOURCE: corrected provider SHA-256 {digest(fixed_sources[IMGLOADER_C])}")
    print(f"SOURCE: corrected header SHA-256 {digest(fixed_sources[IMGLOADER_H])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
