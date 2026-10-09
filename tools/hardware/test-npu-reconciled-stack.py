#!/usr/bin/env python3
"""Apply the reviewed NPU refcount profile after lifecycle, then execute C."""
from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
STACK_PATH = ROOT / "tools/hardware/test-npu-candidate-stack.py"
REFCOUNT_TEST_PATH = ROOT / "tools/hardware/test-npu-refcount-transaction.py"
PATCH_DIR = ROOT / "tools/hardware"
REFCOUNT_HEADER = "drivers/vision/npu/core/npu-hw-device.h"
REFCOUNT_SOURCE = "drivers/vision/npu/core/npu-hw-device.c"
VERTEX_SOURCE = "drivers/vision/npu/core/npu-vertex.c"

PATCH_SERIES = (
    ("npu-session-lifecycle-fix.patch",
     "1554436cb6624c542f9e04ac22a3b3545e55f94c59d3025ee6bdc1ec43168251"),
    ("npu-refcount-lifecycle-profile.patch",
     "8385e4210a807f96f972757cd6ca74a8077b0127ab112d8b877d012ccdc3cb7b"),
    ("npu-default-boot-callback-fix.patch",
     "f5ce216e34df11d8c6adee4a99c36d63f73593cf379e29de3a9de828ec2ee1e7"),
    ("npu-probe-unwind-fix.patch",
     "d3e2e590d4d3c956b10c724a15db996dacd07def204332f50a1c0513f56b4948"),
)
PATCH_NAMES = tuple(name for name, _ in PATCH_SERIES)


def check(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    check(spec is not None and spec.loader is not None,
          f"cannot load existing host helper {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


STACK = load_module(STACK_PATH, "s22_npu_candidate_stack_helpers")
REF = load_module(REFCOUNT_TEST_PATH, "s22_npu_refcount_transaction_helpers")


def verify_digest(label: str, data: bytes, expected: str) -> None:
    actual = hashlib.sha256(data).hexdigest()
    check(actual == expected,
          f"SHA-256 mismatch for {label}: expected {expected}, got {actual}")


def patch_paths(repo_root: Path = ROOT) -> tuple[Path, ...]:
    paths = []
    for name, expected in PATCH_SERIES:
        path = repo_root / "tools/hardware" / name
        check(path.is_file(), f"required ordered patch is missing: {name}")
        data = path.read_bytes()
        check(len(data) <= STACK.HELPERS.MAX_SOURCE_BYTES,
              f"patch exceeds bounded input limit: {name}")
        verify_digest(name, data, expected)
        paths.append(path)
    return tuple(paths)


def validate_order(paths: tuple[Path, ...]) -> None:
    observed = tuple(path.name for path in paths)
    check(observed == PATCH_NAMES,
          f"wrong patch order/input: expected {PATCH_NAMES!r}, got {observed!r}")


def git_apply(root: Path, patch_path: Path, *, check_only: bool = False,
              include: tuple[str, ...] = ()) -> subprocess.CompletedProcess[str]:
    command = ["git", "apply", "--whitespace=error-all"]
    for pattern in include:
        command.extend(("--include", pattern))
    if check_only:
        command.append("--check")
    command.append(str(patch_path))
    return subprocess.run(
        command, cwd=root, capture_output=True, text=True,
        check=False, timeout=10,
    )


def apply_patch_series(root: Path, paths: tuple[Path, ...]) -> bytes:
    validate_order(paths)
    lifecycle_vertex = b""
    for index, path in enumerate(paths):
        checked = git_apply(root, path, check_only=True)
        check(checked.returncode == 0,
              f"ordered --check failed for {path.name}:\n{checked.stderr}")
        applied = git_apply(root, path)
        check(applied.returncode == 0,
              f"ordered apply failed for {path.name}:\n{applied.stderr}")
        if index == 0:
            lifecycle_vertex = (root / VERTEX_SOURCE).read_bytes()
        print(f"PASS ordered patch: --check + apply {path.name}")
    check(lifecycle_vertex, "lifecycle patch did not produce a vertex snapshot")
    return lifecycle_vertex


def diff_sections(data: bytes) -> tuple[bytes, ...]:
    lines = data.splitlines(keepends=True)
    starts = [index for index, line in enumerate(lines)
              if line.startswith(b"diff --git ")]
    return tuple(
        b"".join(lines[start:starts[index + 1] if index + 1 < len(starts) else len(lines)])
        for index, start in enumerate(starts)
    )


def check_profile_derivation(paths: tuple[Path, ...], legacy_paths: tuple[Path, ...],
                             temp: Path, sources: dict[str, bytes]) -> dict[str, bytes]:
    """Prove the profile carries every legacy C/H hunk and no vertex hunk."""
    legacy_sections = diff_sections(legacy_paths[1].read_bytes())
    profile_sections = diff_sections(paths[1].read_bytes())
    check(len(legacy_sections) == 3,
          f"legacy refcount patch section count changed: {len(legacy_sections)}")
    check(tuple(section.rstrip(b"\n") for section in profile_sections) ==
          tuple(section.rstrip(b"\n") for section in legacy_sections[:2]),
          "refcount profile C/H hunks differ from the exact legacy C/H hunks")
    profile_files = tuple(re.findall(rb"^diff --git a/(.*?) b/", paths[1].read_bytes(), re.MULTILINE))
    check(profile_files == (REFCOUNT_SOURCE.encode(), REFCOUNT_HEADER.encode()),
          f"profile modifies unexpected files: {profile_files!r}")
    vertex_hunk = legacy_sections[2]
    for fragment in (
        b"+p_err:", b"+\tmutex_unlock(&vertex->lock);",
        b" p_err_check:", b"-p_err:",
    ):
        check(fragment in vertex_hunk,
              f"legacy vertex unlock hunk changed unexpectedly: {fragment!r}")
    profile_text = paths[1].read_text(encoding="utf-8")
    check("superseded" in profile_text and "lock_held/out_unlock" in profile_text,
          "profile does not document why the baseline vertex hunk is superseded")

    legacy_root = temp / "legacy-refcount-c-h-reference"
    STACK.write_fixture(legacy_root, sources)
    filtered = (REFCOUNT_SOURCE, REFCOUNT_HEADER)
    checked = git_apply(legacy_root, legacy_paths[1], check_only=True, include=filtered)
    check(checked.returncode == 0,
          "exact legacy C/H hunks no longer apply to pinned base source:\n" + checked.stderr)
    applied = git_apply(legacy_root, legacy_paths[1], include=filtered)
    check(applied.returncode == 0,
          "cannot materialize legacy C/H comparison source:\n" + applied.stderr)
    expected = {relative: (legacy_root / relative).read_bytes() for relative in filtered}
    print("PASS profile derivation: canonical ref patch applied with explicit C/H-only comparison filter; both diff sections preserved verbatim, vertex unlock explicitly superseded")
    return expected


def normalized(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def compare_legacy_refcount_effects(expected: dict[str, bytes],
                                    combined: dict[str, bytes]) -> None:
    check(expected[REFCOUNT_HEADER] == combined[REFCOUNT_HEADER],
          "combined header differs from canonical refcount C/H profile result")
    for relative, marker in REF.PATCHED_FUNCTIONS:
        expected_body = REF.function_body(expected[relative].decode("utf-8"), marker)
        combined_body = REF.function_body(combined[relative].decode("utf-8"), marker)
        check(expected_body and combined_body,
              f"cannot compare legacy refcount function body {marker}")
        check(expected_body == combined_body,
              f"legacy refcount function body changed in combined stack: {marker}")

    legacy_c = expected[REFCOUNT_SOURCE].decode("utf-8")
    combined_c = combined[REFCOUNT_SOURCE].decode("utf-8")
    legacy_probe = normalized(STACK.HELPERS.function_body(
        legacy_c, "static int npu_hwdev_probe("))
    combined_probe = normalized(STACK.HELPERS.function_body(
        combined_c, "static int npu_hwdev_probe("))
    check(legacy_probe and combined_probe, "cannot compare refcount probe registrations")
    for fragment in (
        "hdev->ops.init_abort = npu_hwdev_dsp_init_abort;",
        "hdev->ops.init_abort = npu_hwdev_dnc_init_abort;",
        "hdev->ops.init_abort = npu_hwdev_npu_init_abort;",
        "npu_hw_ref_setup(&hdev->boot_cnt, hdev, npu_hw_ref_open, npu_hw_ref_close, NULL);",
        "npu_hw_ref_setup(&hdev->init_cnt, hdev, npu_hw_ref_init, npu_hw_ref_deinit, npu_hw_ref_abort_init);",
    ):
        check(fragment in legacy_probe,
              f"canonical refcount probe setup omitted expected effect: {fragment}")
        check(fragment in combined_probe,
              f"combined probe lost canonical refcount setup effect: {fragment}")
    check(combined_probe.count("npu_hw_ref_setup(") == 2,
          "combined probe has unexpected hw refcount setup calls")
    print(f"PASS legacy C/H semantics: {len(REF.PATCHED_FUNCTIONS)} exact helper/callback bodies and probe registrations retained")


def check_lifecycle_vertex_preserved(lifecycle_vertex: bytes,
                                    combined: dict[str, bytes]) -> str:
    final_vertex = combined[VERTEX_SOURCE]
    check(final_vertex == lifecycle_vertex,
          "profile or later patches modified lifecycle-owned npu-vertex.c")
    body = STACK.HELPERS.function_body(
        final_vertex.decode("utf-8"), "int npu_hwdev_normal_bootup(")
    check(body, "final combined normal_bootup caller body is unavailable")
    for marker in (
        "bool lock_held = false;", "lock_held = true;", "lock_held = false;",
        "goto out_unlock;", "p_err:", "out_unlock:",
        "if (lock_held)", "mutex_unlock(&vertex->lock);",
    ):
        check(marker in body,
              f"combined lifecycle caller lost conditional-lock cleanup: {marker}")
    check("p_err_check:" not in body,
          "combined lifecycle caller regressed to baseline-only error label")
    print("PASS superseded vertex hunk: lifecycle-owned vertex source unchanged; conditional unlock retained")
    return body


def check_patch_input_negatives(temp: Path, paths: tuple[Path, ...],
                                sources: dict[str, bytes]) -> None:
    def write_patch_inputs(root: Path, *, omit: str = "", tamper: str = "") -> None:
        patch_dir = root / "tools/hardware"
        patch_dir.mkdir(parents=True, exist_ok=True)
        for path in paths:
            if path.name == omit:
                continue
            data = path.read_bytes()
            if path.name == tamper:
                data += b"\n# altered frozen patch input\n"
            (patch_dir / path.name).write_bytes(data)

    missing_root = temp / "missing-patch-input"
    write_patch_inputs(missing_root, omit=PATCH_NAMES[1])
    try:
        patch_paths(missing_root)
    except RuntimeError as error:
        check(str(error) == f"required ordered patch is missing: {PATCH_NAMES[1]}",
              f"missing patch was rejected unexpectedly: {error}")
    else:
        raise RuntimeError("mandatory patch reader accepted a missing refcount profile")

    tampered_root = temp / "tampered-patch-input"
    write_patch_inputs(tampered_root, tamper=PATCH_NAMES[1])
    try:
        patch_paths(tampered_root)
    except RuntimeError as error:
        check(f"SHA-256 mismatch for {PATCH_NAMES[1]}" in str(error),
              f"tampered patch was rejected unexpectedly: {error}")
    else:
        raise RuntimeError("mandatory patch reader accepted a tampered refcount profile")

    order_root = temp / "wrong-order-source"
    STACK.write_fixture(order_root, sources)
    before = {relative: hashlib.sha256((order_root / relative).read_bytes()).hexdigest()
              for relative in STACK.STACK_FILES + STACK.PREFLIGHT_FILES}
    swapped = (paths[1], paths[0], *paths[2:])
    try:
        apply_patch_series(order_root, swapped)
    except RuntimeError as error:
        check(str(error).startswith("wrong patch order/input:"),
              f"wrong patch order was rejected for unexpected reason: {error}")
    else:
        raise RuntimeError("ordered patch applicator accepted swapped lifecycle/profile inputs")
    after = {relative: hashlib.sha256((order_root / relative).read_bytes()).hexdigest()
             for relative in STACK.STACK_FILES + STACK.PREFLIGHT_FILES}
    check(before == after, "wrong-order rejection modified the temporary source fixture")
    print("PASS negative patch inputs: missing, SHA-tampered, and swapped-order inputs rejected before source mutation")


def run_combined_c_regressions(temp: Path, combined: dict[str, bytes]) -> None:
    compiler = shutil.which("cc") or shutil.which("gcc")
    check(compiler is not None, "host C compiler (cc/gcc) is required")
    helpers = REF.read_source_bytes(combined, REF.PATCHED_FUNCTIONS)
    caller = REF.read_source_bytes(combined, REF.NORMAL_CALLER_FUNCTIONS)
    check("out_unlock:" in caller and "if (lock_held)" in caller,
          "actual combined caller extraction does not contain conditional unlock")
    caller_harness = REF.CALLER_HARNESS
    replacements = (
        ("static void npu_sessionmgr_regHW(struct npu_session *session)\n{ (void)session; register_calls++; }",
         "static int npu_sessionmgr_regHW(struct npu_session *session)\n{ (void)session; register_calls++; return 0; }"),
        ("static int npu_session_NW_CMD_POWER_NOTIFY(struct npu_session *session, bool on)\n{ (void)session; (void)on; power_notify_calls++; return power_notify_result; }",
         "static int npu_session_NW_CMD_POWER_NOTIFY(struct npu_session *session, bool on)\n{ (void)session; power_notify_calls++; if (on) { power_notify_on_calls++; return power_notify_result; } power_notify_off_calls++; return power_notify_off_result; }"),
        ("static void npu_hwdev_hwacg(struct npu_system *system, int id, bool on)\n{ (void)system; (void)id; (void)on; }",
         "static int npu_hwdev_hwacg(struct npu_system *system, int id, bool on)\n{ (void)system; (void)id; (void)on; return 0; }"),
        ("static void npu_session_restore_cnt(struct npu_session *session)\n{ (void)session; restore_calls++; }",
         "static int npu_session_restore_cnt(struct npu_session *session)\n{ (void)session; restore_calls++; return 0; }"),
        ("static int power_notify_result;\n", "static int power_notify_result;\nstatic int power_notify_off_result;\nstatic int power_notify_on_calls;\nstatic int power_notify_off_calls;\nstatic int unregister_calls;\nstatic int shutdown_calls;\n"),
        ("    power_notify_result = 0;\n", "    power_notify_result = 0;\n    power_notify_off_result = 0;\n"),
        ("    power_notify_calls = 0;\n", "    power_notify_calls = 0;\n    power_notify_on_calls = 0;\n    power_notify_off_calls = 0;\n    unregister_calls = 0;\n    shutdown_calls = 0;\n"),
    )
    for old, new in replacements:
        check(caller_harness.count(old) == 1,
              f"imported caller harness shim anchor changed: {old[:60]!r}")
        caller_harness = caller_harness.replace(old, new, 1)
    anchor = "/* Exact extracted normal-bootup implementation, with only service shims. */"
    lifecycle_shims = (
        "static int npu_sessionmgr_unregHW(struct npu_session *session)\n"
        "{ (void)session; unregister_calls++; return 0; }\n"
        "static int npu_hwdev_shutdown(struct npu_device *device, unsigned int hids)\n"
        "{ (void)device; (void)hids; shutdown_calls++; return 0; }\n"
        "static void npu_device_set_emergency_err(struct npu_device *device)\n"
        "{ (void)device; }\n\n"
    )
    check(caller_harness.count(anchor) == 1,
          "imported caller harness service-shim insertion point changed")
    caller_harness = caller_harness.replace(anchor, lifecycle_shims + anchor, 1)
    caller_harness = caller_harness.replace(
        "expect(ret == -EHOSTDOWN && power_notify_calls == 1,\n        \"power-notify error return changed\");",
        "expect(ret == -EHOSTDOWN && power_notify_on_calls == 1 &&\n"
        "           power_notify_off_calls == 1 && unregister_calls == 1 &&\n"
        "           shutdown_calls == 1,\n"
        "        \"power-notify error/compensation unwind changed\");",
        1,
    )
    check("power-notify error/compensation unwind changed" in caller_harness,
          "lifecycle caller harness did not verify inverse power/ref unwind")

    for optimization in ("-O0", "-O2"):
        helper_output = REF.compile_and_run(
            compiler, temp, REF.PATCHED_HARNESS, helpers,
            f"combined-refcount-{optimization[2:]}.c", "passes", optimization,
        )
        check("PASS actual patched C: all transactional refcount regressions" in helper_output,
              f"combined refcount harness omitted its pass receipt at {optimization}")
        print(helper_output, end="")
        caller_output = REF.compile_and_run(
            compiler, temp, caller_harness, caller,
            f"caller-combined-{optimization[2:]}.c", "passes", optimization,
        )
        for marker in (
            "propagated hwdev error unlocks exactly once",
            "vref error unlocks once",
            "unrelated POWER_NOTIFY error unlocks once",
            "secure timeout has no double-unlock",
            "successful warm boot remains unchanged",
        ):
            check(marker in caller_output,
                  f"combined caller harness omitted {marker!r} at {optimization}")
        print(caller_output, end="")
    print("PASS combined actual-C refcount helpers and normal_bootup caller at C -O0/-O2")


def check_bootup_stays_refused(source_root: Path, sources: dict[str, bytes],
                              temp: Path) -> None:
    captured = io.StringIO()
    with contextlib.redirect_stdout(captured):
        STACK.run_preflight(source_root, sources, temp)
    report = captured.getvalue()
    check("bootup_ready=false" in report and "bootup_authorized=false" in report,
          "full-stack preflight output does not preserve the BOOTUP refusal")
    print("PASS BOOTUP gate after full four-patch stack: readiness and authorization remain false")
    for line in report.splitlines():
        if line.startswith("FIRMWARE_FIXTURES ") or line.startswith("PREFLIGHT "):
            print(line)


def main() -> int:
    try:
        sources, source_identity = STACK.load_fixtures()
        paths = patch_paths()
        legacy_paths = STACK.patch_paths()
        print(f"SOURCE_FIXTURE {source_identity}; pinned base {STACK.PINNED_BASE}")
        print("PATCH_ORDER " + " -> ".join(PATCH_NAMES))
        print("PASS SHA-256 and byte caps: four reconciled patches plus exact source fixtures")

        with tempfile.TemporaryDirectory(prefix="s22-npu-reconciled-stack-") as name:
            temp = Path(name)
            expected_ref_sources = check_profile_derivation(
                paths, legacy_paths, temp, sources)
            check_patch_input_negatives(temp, paths, sources)

            legacy_blocker_root = temp / "legacy-blocker-regression"
            STACK.write_fixture(legacy_blocker_root, sources)
            legacy_output = io.StringIO()
            with contextlib.redirect_stdout(legacy_output):
                diagnostic = STACK.assert_known_ordered_conflict(
                    legacy_blocker_root, legacy_paths)
            check("npu-vertex.c:1224" in diagnostic,
                  "preserved canonical-stack blocker regression no longer reproduces")
            check("lock_held/out_unlock balance retained" in legacy_output.getvalue(),
                  "preserved historical blocker test omitted lifecycle lock evidence")
            print("PASS preserved historical blocker regression: unprofiled canonical ref patch still rejects at npu-vertex.c:1224")

            combined_root = temp / "combined-source"
            STACK.write_fixture(combined_root, sources)
            lifecycle_vertex = apply_patch_series(combined_root, paths)
            combined_sources = {
                relative: (combined_root / relative).read_bytes()
                for relative in STACK.STACK_FILES + STACK.PREFLIGHT_FILES
            }
            compare_legacy_refcount_effects(expected_ref_sources, combined_sources)
            check_lifecycle_vertex_preserved(lifecycle_vertex, combined_sources)
            run_combined_c_regressions(temp, combined_sources)
            check_bootup_stays_refused(combined_root, sources, temp)

        print("RESULT four-patch source profile and host C regressions passed; WIP source evidence only; shutdown-ownership patch, kernel build/runtime and device validation remain excluded")
        return 0
    except STACK.SourceFixtureUnavailable as error:
        print(f"SOURCE_FIXTURE_UNAVAILABLE {error}", file=sys.stderr)
        return 77
    except (RuntimeError, OSError, subprocess.SubprocessError) as error:
        print(f"FAIL {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
