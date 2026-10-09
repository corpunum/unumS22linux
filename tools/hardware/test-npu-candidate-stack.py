#!/usr/bin/env python3
"""Reproduce the pinned NPU patch-stack context conflict without a kernel build."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
HELPER_PATH = ROOT / "tools/hardware/test-npu-probe-unwind.py"
PREFLIGHT_PATH = ROOT / "tools/hardware/npu-boot-preflight.py"
PINNED_BASE = "4e5c5ad7d950e4de0688b5663965f2075654b2ad"
SOURCE_COMMIT = "3fca50941422439b2019db2e4a3dc1016b2138a1"
SOURCE_TREE_ENV = "S22_NPU_PROBE_SOURCE_TREE"

PATCHES = (
    ("npu-session-lifecycle-fix.patch",
     "1554436cb6624c542f9e04ac22a3b3545e55f94c59d3025ee6bdc1ec43168251"),
    ("npu-refcount-transaction-fix.patch",
     "09414c886c37e55e23767f33fcdba19f5ab8099a6df3a2d5618972449d64a935"),
    ("npu-default-boot-callback-fix.patch",
     "f5ce216e34df11d8c6adee4a99c36d63f73593cf379e29de3a9de828ec2ee1e7"),
    ("npu-probe-unwind-fix.patch",
     "d3e2e590d4d3c956b10c724a15db996dacd07def204332f50a1c0513f56b4948"),
)
PATCH_DIR = ROOT / "tools/hardware"
STACK_FILES = (
    "drivers/vision/npu/core/npu-protodrv.c",
    "drivers/vision/npu/core/npu-session.c",
    "drivers/vision/npu/core/npu-vertex.c",
    "drivers/vision/npu/core/npu-hw-device.c",
    "drivers/vision/npu/core/npu-hw-device.h",
    "drivers/vision/npu/core/npu-clock.c",
    "drivers/vision/npu/core/npu-clock.h",
)
PREFLIGHT_FILES = (
    "drivers/vision/npu/core/include/npu-binary.h",
    "drivers/vision/npu/core/npu-system.c",
    "drivers/vision/npu/core/npu-stm.c",
)
EXTRA_SOURCE_SHA256 = {
    "drivers/vision/npu/core/npu-session.c":
        "f12525cfdc1217188e10b9457f02c889c8270dfdcc440e73d45034e41c8ccd07",
    "drivers/vision/npu/core/npu-protodrv.c":
        "246952fc5b29985f88277b76dce6fcf12c1694695e0f6111cffa249c0f38f5a9",
    "drivers/vision/npu/core/include/npu-binary.h":
        "f9901adc1e7e27547fb48ef4437ac59b1317286cd08fa056868de3711f29657e",
    "drivers/vision/npu/core/npu-stm.c":
        "f2fbf5acc751b72775976b04af5d21035edbb05de24400a35b49cdf2bae93684",
}


class SourceFixtureUnavailable(Exception):
    pass


def check(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    check(spec is not None and spec.loader is not None,
          f"cannot load helper module {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


HELPERS = load_module(HELPER_PATH, "s22_npu_probe_fixture_helpers")
PREFLIGHT = load_module(PREFLIGHT_PATH, "s22_npu_boot_preflight")


def verify_digest(label: str, data: bytes, expected: str) -> None:
    actual = hashlib.sha256(data).hexdigest()
    check(actual == expected,
          f"SHA-256 mismatch for {label}: expected {expected}, got {actual}")


def load_extra_fixture(relative: str, expected: str) -> bytes:
    configured_root = os.environ.get(SOURCE_TREE_ENV)
    if configured_root:
        source_root = Path(configured_root).expanduser()
        unchanged = subprocess.run(
            ["git", "-C", str(source_root), "diff", "--quiet", PINNED_BASE,
             SOURCE_COMMIT, "--", relative],
            capture_output=True, text=True, check=False, timeout=10,
        )
        check(unchanged.returncode == 0,
              f"extra fixture differs from pinned source: {relative}")
        path = source_root / relative
        check(path.is_file(), f"pinned fixture missing: {relative}")
        check(path.stat().st_size <= HELPERS.MAX_SOURCE_BYTES,
              f"pinned fixture exceeds {HELPERS.MAX_SOURCE_BYTES} bytes: {relative}")
        data = path.read_bytes()
        check(len(data) <= HELPERS.MAX_SOURCE_BYTES,
              f"pinned fixture exceeds {HELPERS.MAX_SOURCE_BYTES} bytes: {relative}")
        verify_digest(relative, data, expected)
        return data

    prior = HELPERS.SOURCE_SHA256.get(relative)
    HELPERS.SOURCE_SHA256[relative] = expected
    try:
        return HELPERS.fetch_source(relative)
    except HELPERS.SourceFixtureUnavailable as error:
        raise SourceFixtureUnavailable(str(error)) from error
    finally:
        if prior is None:
            del HELPERS.SOURCE_SHA256[relative]
        else:
            HELPERS.SOURCE_SHA256[relative] = prior


def load_fixtures() -> tuple[dict[str, bytes], str]:
    sources, identity = HELPERS.load_sources()
    for relative, data in sources.items():
        check(len(data) <= HELPERS.MAX_SOURCE_BYTES,
              f"source fixture exceeds {HELPERS.MAX_SOURCE_BYTES} bytes: {relative}")
    HELPERS.check_dt_and_callers(sources)
    for relative, expected in EXTRA_SOURCE_SHA256.items():
        data = load_extra_fixture(relative, expected)
        sources[relative] = data
    for relative in STACK_FILES + PREFLIGHT_FILES:
        check(relative in sources,
              f"source manifest is missing required fixture {relative}")
    return sources, identity


def patch_paths(repo_root: Path = ROOT) -> tuple[Path, ...]:
    paths = []
    for name, expected in PATCHES:
        path = repo_root / "tools/hardware" / name
        check(path.is_file(), f"required ordered patch is missing: {name}")
        data = path.read_bytes()
        check(len(data) <= HELPERS.MAX_SOURCE_BYTES,
              f"patch exceeds bounded input limit: {name}")
        verify_digest(name, data, expected)
        paths.append(path)
    return tuple(paths)


def write_fixture(destination: Path, sources: dict[str, bytes]) -> None:
    for relative in STACK_FILES + PREFLIGHT_FILES:
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(sources[relative])
        # Pinned kernel source files are mode 100755 in the source tree.
        target.chmod(0o755)


def git_apply(root: Path, patch_path: Path, check_only: bool = False,
              timeout: int = 10) -> subprocess.CompletedProcess[str]:
    command = ["git", "apply", "--whitespace=error-all"]
    if check_only:
        command.append("--check")
    command.append(str(patch_path))
    return subprocess.run(
        command, cwd=root, capture_output=True, text=True,
        check=False, timeout=timeout,
    )


def apply_checked(root: Path, patch_path: Path) -> None:
    checked = git_apply(root, patch_path, check_only=True)
    check(checked.returncode == 0,
          f"ordered patch check unexpectedly failed: {patch_path.name}\n{checked.stderr}")
    applied = git_apply(root, patch_path)
    check(applied.returncode == 0,
          f"ordered patch application unexpectedly failed: {patch_path.name}\n{applied.stderr}")


def expect_rejected(root: Path, patch_path: Path, label: str,
                    required_fragments: tuple[str, ...] = ()) -> str:
    result = git_apply(root, patch_path, check_only=True)
    diagnostic = result.stderr.strip()
    check(result.returncode != 0,
          f"negative application unexpectedly accepted {label}")
    for fragment in required_fragments:
        check(fragment in diagnostic,
              f"{label} failed for an unexpected reason; missing {fragment!r}:\n{diagnostic}")
    return diagnostic


def assert_session_lock_balance(source_root: Path) -> None:
    path = source_root / "drivers/vision/npu/core/npu-vertex.c"
    text = path.read_text(encoding="utf-8")
    body = HELPERS.function_body(text, "int npu_hwdev_normal_bootup(")
    check(body, "lifecycle-patched normal_bootup body is unavailable")
    for marker in (
        "bool lock_held = false;",
        "lock_held = true;",
        "lock_held = false;",
        "goto out_unlock;",
        "p_err:",
        "out_unlock:",
        "if (lock_held)",
        "mutex_unlock(&vertex->lock);",
    ):
        check(marker in body,
              f"lifecycle replacement lost its lock-state cleanup marker: {marker}")
    check("p_err_check:" not in body,
          "lifecycle replacement unexpectedly retained the baseline p_err_check label")


def assert_known_ordered_conflict(source_root: Path, paths: tuple[Path, ...]) -> str:
    apply_checked(source_root, paths[0])
    assert_session_lock_balance(source_root)
    conflict = git_apply(source_root, paths[1], check_only=True)
    diagnostic = conflict.stderr.strip()
    check(conflict.returncode != 0,
          "known baseline-only npu-vertex refcount hunk unexpectedly applied after lifecycle replacement")
    check("patch failed: drivers/vision/npu/core/npu-vertex.c:1224" in diagnostic and
          "drivers/vision/npu/core/npu-vertex.c: patch does not apply" in diagnostic,
          f"ordered stack rejected for an unexpected hunk/reason:\n{diagnostic}")
    print("PASS ordered prefix: lifecycle patch check/apply; lock_held/out_unlock balance retained")
    print("EXPECTED_INTEGRATION_BLOCKER refcount patch fails at npu-vertex.c:1224")
    print(diagnostic)
    print("NOT_APPLIED later ordered hunks: default-boot callback, probe-unwind")
    return diagnostic


def check_swapped_order(source_root: Path, paths: tuple[Path, ...]) -> None:
    apply_checked(source_root, paths[1])
    diagnostic = expect_rejected(
        source_root, paths[0], "refcount-before-lifecycle order",
        ("patch failed: drivers/vision/npu/core/npu-vertex.c:1188",
         "drivers/vision/npu/core/npu-vertex.c: patch does not apply"),
    )
    print("PASS negative swapped-order apply: refcount then lifecycle is rejected at npu-vertex.c:1188")
    print(diagnostic)


def check_missing_manifest_patch(temp: Path, paths: tuple[Path, ...]) -> None:
    """Exercise the same required-input reader used by main, before apply."""
    controlled_root = temp / "missing-manifest-repo"
    patch_dir = controlled_root / "tools/hardware"
    patch_dir.mkdir(parents=True, exist_ok=True)
    for index, path in enumerate(paths):
        if index == 1:
            continue
        data = path.read_bytes()
        verify_digest(path.name, data, PATCHES[index][1])
        (patch_dir / path.name).write_bytes(data)
    diagnostic = ""
    try:
        patch_paths(controlled_root)
    except RuntimeError as error:
        diagnostic = str(error)
    check(diagnostic ==
          "required ordered patch is missing: npu-refcount-transaction-fix.patch",
          "mandatory patch input reader did not reject the missing refcount input")
    check(not (patch_dir / "npu-refcount-transaction-fix.patch").exists(),
          "controlled manifest unexpectedly contains the omitted refcount patch")
    print("PASS negative manifest-loader: exact other three patches verified; missing refcount rejected before apply")
    print(diagnostic)


def check_missing_apply_path(source_root: Path, paths: tuple[Path, ...],
                             temp: Path) -> None:
    apply_checked(source_root, paths[0])
    missing = temp / "intentionally-missing-refcount.patch"
    diagnostic = expect_rejected(source_root, missing, "missing required patch")
    print("PASS negative git-apply missing-path diagnostic: nonexistent patch file is rejected")
    print(diagnostic)


def check_mutated_patch(source_root: Path, paths: tuple[Path, ...],
                        temp: Path) -> None:
    original = paths[1].read_text(encoding="utf-8")
    needle = " \tvertex->normal_count = 1;\n"
    check(original.count(needle) == 1,
          "refcount patch mutation anchor changed; do not guess a replacement")
    mutated = temp / "mutated-refcount.patch"
    mutated.write_text(original.replace(needle,
        " \tvertex->normal_count = 999;\n", 1), encoding="utf-8")
    diagnostic = expect_rejected(
        source_root, mutated, "mutated patch context",
        ("drivers/vision/npu/core/npu-vertex.c", "patch does not apply"),
    )
    print("PASS negative mutated-patch apply: altered hunk context rejected")
    print(diagnostic)


def check_mutated_source(source_root: Path, patch_path: Path) -> None:
    vertex = source_root / "drivers/vision/npu/core/npu-vertex.c"
    original = vertex.read_text(encoding="utf-8")
    needle = "\tvertex->normal_count = 1;\n"
    check(original.count(needle) == 1,
          "pinned source mutation anchor changed; do not guess a replacement")
    vertex.write_text(original.replace(needle,
        "\tvertex->normal_count = 999;\n", 1), encoding="utf-8")
    diagnostic = expect_rejected(
        source_root, patch_path, "mutated source context",
        ("drivers/vision/npu/core/npu-vertex.c", "patch does not apply"),
    )
    print("PASS negative mutated-source apply: changed pinned context rejected")
    print(diagnostic)


def bounded_artifact_argument(path: Path, expected: str, label: str,
                              temp: Path) -> tuple[Path, str]:
    if not path.is_file():
        return temp / f"unavailable-{label}", "unavailable: file absent"
    size = path.stat().st_size
    if size > HELPERS.MAX_SOURCE_BYTES:
        return temp / f"unavailable-{label}", (
            f"unavailable: {size} bytes exceeds bounded fixture limit "
            f"{HELPERS.MAX_SOURCE_BYTES}")
    data = path.read_bytes()
    actual = hashlib.sha256(data).hexdigest()
    if actual != expected:
        return temp / f"unavailable-{label}", (
            f"unavailable: hash mismatch ({actual})")
    return path, f"verified exact hash {actual}"


def run_preflight(source_root: Path, sources: dict[str, bytes],
                  temp: Path) -> None:
    config = temp / "s5e9925_defconfig"
    config.write_bytes(sources[HELPERS.S5E9925_CONFIG])
    aie_path = ROOT / "rootfs/npu-firmware-closure-20260921/vendor/firmware/AIE.bin"
    rules_path = ROOT / "rootfs/npu-firmware-closure-20260921/vendor/firmware/dsp_reloc_rules.bin"
    aie, aie_status = bounded_artifact_argument(
        aie_path, PREFLIGHT.EXPECTED_AIE, "AIE.bin", temp)
    rules, rules_status = bounded_artifact_argument(
        rules_path, PREFLIGHT.EXPECTED_DSP_RULES, "dsp_reloc_rules.bin", temp)
    command = [
        sys.executable, str(PREFLIGHT_PATH),
        "--source", str(source_root), "--config", str(config),
        "--aie", str(aie), "--dsp-rules", str(rules),
    ]
    result = subprocess.run(
        command, capture_output=True, text=True, check=False, timeout=10,
    )
    check(result.returncode == 2,
          f"host preflight returned unexpected status {result.returncode}:\n{result.stdout}\n{result.stderr}")
    check(result.stdout, "host preflight emitted no JSON result")
    try:
        report = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"host preflight did not emit valid JSON: {error}") from error
    check(report.get("device_access") is False and report.get("staging") is False,
          "host preflight unexpectedly reports device access or staging")
    check(report.get("bootup_ready") is False and
          report.get("bootup_authorized") is False and
          report.get("live_probe_validated") is False,
          "host preflight promoted BOOTUP readiness, authorization, or live probe")
    check(report.get("exit_code") == result.returncode,
          "host preflight JSON/process status disagree")
    check(report.get("artifact_preflight_pass") is False,
          "missing/unverified public firmware fixtures were reported as passing")
    artifacts = report.get("checks", {}).get("artifacts", {})
    check(artifacts.get("AIE.bin", {}).get("match") is False and
          artifacts.get("dsp_reloc_rules.bin", {}).get("match") is False,
          "host preflight did not report both absent/unverified firmware inputs as mismatches")
    print("PASS host preflight on lifecycle-patched temp source/config: BOOTUP remains refused")
    print(f"FIRMWARE_FIXTURES AIE.bin {aie_status}; dsp_reloc_rules.bin {rules_status}")
    print("PREFLIGHT status=2; artifact_preflight_pass=false; bootup_ready=false; bootup_authorized=false")


def main() -> int:
    try:
        sources, identity = load_fixtures()
        paths = patch_paths()
        print(f"SOURCE_FIXTURE {identity}; pinned base {PINNED_BASE}")
        print("PATCH_ORDER " + " -> ".join(path.name for path in paths))
        print("PASS pinned hashes: all four required patch inputs and source fixtures verified")

        with tempfile.TemporaryDirectory(prefix="s22-npu-candidate-stack-") as name:
            temp = Path(name)
            prefix = temp / "ordered-prefix"
            write_fixture(prefix, sources)
            assert_known_ordered_conflict(prefix, paths)
            run_preflight(prefix, sources, temp)

            swapped = temp / "swapped-order"
            write_fixture(swapped, sources)
            check_swapped_order(swapped, paths)

            check_missing_manifest_patch(temp, paths)

            missing = temp / "missing-patch"
            write_fixture(missing, sources)
            check_missing_apply_path(missing, paths, temp)

            mutated_patch_source = temp / "mutated-patch-source"
            write_fixture(mutated_patch_source, sources)
            check_mutated_patch(mutated_patch_source, paths, temp)

            mutated_source = temp / "mutated-pinned-source"
            write_fixture(mutated_source, sources)
            check_mutated_source(mutated_source, paths[1])

        print("RESULT expected integration blocker reproduced; later ordered hunks and shutdown patch untested; NOT READY TO BUILD")
        return 0
    except (SourceFixtureUnavailable, HELPERS.SourceFixtureUnavailable) as error:
        print(f"SOURCE_FIXTURE_UNAVAILABLE {error}", file=sys.stderr)
        return 77
    except (RuntimeError, OSError, subprocess.SubprocessError) as error:
        print(f"FAIL {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
