#!/usr/bin/env python3
"""Verify NPU shutdown first-error propagation in pinned extracted driver C."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
FULL_PROFILE_TEST = ROOT / "tools/hardware/test-npu-full-lifecycle-profile.py"
ERROR_PATCH = ROOT / "tools/hardware/npu-shutdown-error-propagation.patch"
ERROR_PATCH_SHA256 = "08374e96792f24d1e0e4fbca594bfce35537af8acace9296b66f27b531564e43"
FROZEN_OWNERSHIP_SHA256 = "a8af77122b4049fd38e21adfd01a8577d3e8f9bef1f68d0cfa091a5884a4d9f3"


def check(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    check(spec is not None and spec.loader is not None,
          f"cannot load frozen profile helper {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


FULL = load_module(FULL_PROFILE_TEST, "s22_npu_full_profile_error_helpers")
RECON = FULL.RECON
OWN = FULL.OWN


def make_error_harness(prelude: str) -> str:
    prelude = FULL.make_shutdown_harness(prelude)
    replacements = (
        ("static struct test_state T;\nstatic struct npu_device D;",
         "static struct test_state T;\n"
         "static int early_close_ret;\n"
         "static int proto_close_ret;\n"
         "static struct npu_device D;"),
        ("static int __npu_device_early_close(struct npu_device *device) {\n"
         "    (void)device; event(5); return 0;\n"
         "}",
         "static int __npu_device_early_close(struct npu_device *device) {\n"
         "    (void)device; event(5); return early_close_ret;\n"
         "}"),
        ("static int proto_drv_close(struct npu_device *device) {\n"
         "    (void)device; event(6); return 0;\n"
         "}",
         "static int proto_drv_close(struct npu_device *device) {\n"
         "    (void)device; event(6); return proto_close_ret;\n"
         "}"),
        ("static void reset_fixture(void) {\n"
         "    memset(&T, 0, sizeof(T)); memset(&D, 0, sizeof(D));",
         "static void reset_fixture(void) {\n"
         "    early_close_ret = 0; proto_close_ret = 0;\n"
         "    memset(&T, 0, sizeof(T)); memset(&D, 0, sizeof(D));"),
    )
    for old, new in replacements:
        check(prelude.count(old) == 1,
              f"frozen ownership harness shim anchor changed: {old[:72]!r}")
        prelude = prelude.replace(old, new, 1)

    functions = r"""
static void expect_device_shutdown_trace(const char *message) {
    expect(T.sequence == 4 && T.events[0] == 5 && T.events[1] == 6 &&
           T.events[2] == 7 && T.events[3] == 10,
           message);
    expect(T.suspend_calls == 1 &&
           test_bit(NPU_DEVICE_STATE_OPEN, &D.state),
           "shutdown calls suspend once without changing device-open ownership");
}

static void test_shutdown_result_case(int early_ret, int proto_ret,
                                      int suspend_ret, int expected,
                                      const char *message) {
    reset_fixture();
    early_close_ret = early_ret;
    proto_close_ret = proto_ret;
    T.suspend_ret = suspend_ret;
    expect(npu_device_shutdown(&D) == expected, message);
    expect_device_shutdown_trace(
        "shutdown continues early-close/protocol/DHCP/suspend order");
    release_fixture();
}

static void test_baseline_shutdown_false_success(void) {
    test_shutdown_result_case(-EIO, 0, 0, 0,
                              "baseline loses early-close error to later success");
    test_shutdown_result_case(0, -EBADR, 0, 0,
                              "baseline loses protocol-close error to later success");
    test_shutdown_result_case(-EIO, -EBADR, -EHOSTDOWN, -EHOSTDOWN,
                              "baseline incorrectly prefers later suspend error");

    struct file file;
    reset_fixture(); memset(&file, 0, sizeof(file));
    file.private_data = &S.vctx;
    S.vctx.state = BIT(NPU_VERTEX_POWER);
    D.vertex.normal_count = 1;
    proto_close_ret = -EBADR;
    expect(npu_vertex_close(&file) == 0,
           "baseline false success escapes the close ownership caller");
    expect(T.sequence == 10 && T.events[3] == 5 && T.events[4] == 6 &&
           T.events[5] == 7 && T.events[6] == 10 && T.events[7] == 60 &&
           T.events[8] == 70 && T.events[9] == 80,
           "baseline continues through hardware and session/open-ref release");
    expect(T.session_close_calls == 1 && T.open_put_calls == 1 &&
           !test_bit(NPU_DEVICE_ERR_STATE_SHUTDOWN_UNCERTAIN, &D.err_state),
           "baseline frees the session instead of taking the caller quarantine");
    release_fixture();
    puts("BASELINE_REPRODUCED early/protocol close false success and caller free");
}

static void test_final_shutdown_error_propagation(void) {
    test_shutdown_result_case(0, 0, 0, 0,
                              "all shutdown callbacks succeed");
    test_shutdown_result_case(-EIO, 0, 0, -EIO,
                              "early-close error survives later successful callbacks");
    test_shutdown_result_case(0, -EBADR, 0, -EBADR,
                              "protocol-close error survives later successful suspend");
    test_shutdown_result_case(0, 0, -EHOSTDOWN, -EHOSTDOWN,
                              "suspend error is returned when it is the first error");
    test_shutdown_result_case(-EIO, -EBADR, -EHOSTDOWN, -EIO,
                              "first early-close error wins across multiple failures");
    test_shutdown_result_case(0, -EBADR, -EHOSTDOWN, -EBADR,
                              "first protocol-close error wins over suspend failure");
    test_shutdown_result_case(-EIO, 0, -EHOSTDOWN, -EIO,
                              "first early-close error wins over suspend failure");

    reset_fixture();
    D.state &= ~BIT(NPU_DEVICE_STATE_OPEN);
    expect(npu_device_shutdown(&D) == -EINVAL,
           "already-closed device remains rejected");
    expect(T.sequence == 0,
           "already-closed rejection performs no teardown callbacks");
    release_fixture();

    struct file file;
    reset_fixture(); memset(&file, 0, sizeof(file));
    file.private_data = &S.vctx;
    S.vctx.state = BIT(NPU_VERTEX_POWER);
    D.vertex.normal_count = 1;
    proto_close_ret = -EBADR;
    expect(npu_vertex_close(&file) == -EBADR,
           "close caller receives actual protocol-close failure");
    expect(T.sequence == 7 && T.events[0] == 1 && T.events[1] == 2 &&
           T.events[2] == 50 && T.events[3] == 5 && T.events[4] == 6 &&
           T.events[5] == 7 && T.events[6] == 10,
           "close preserves pre-existing power/ref and device-shutdown order");
    expect(T.unreg_hw_calls == 1 && T.power_notify_calls == 1 &&
           T.boot_put_calls == 1 && T.hw_shutdown_calls == 0 &&
           T.session_close_calls == 0 && T.session_undo_open_calls == 0 &&
           T.open_put_calls == 0,
           "close failure retains session/open ref and skips later teardown/free");
    expect(test_bit(NPU_DEVICE_ERR_STATE_SHUTDOWN_UNCERTAIN, &D.err_state) &&
           test_bit(NPU_DEVICE_ERR_STATE_EMERGENCY, &D.err_state) &&
           D.vertex.normal_count == 1 &&
           D.vertex.boot_cnt.refcount.counter == 0 &&
           D.vertex.open_cnt.refcount.counter == 1 &&
           !(S.vctx.state & BIT(NPU_VERTEX_POWER)),
           "close caller latches uncertainty while retaining session ownership");
    expect(T.lock_depth == 0 && T.lock_acquires == T.lock_releases,
           "error-propagating close releases its vertex lock");
    release_fixture();
    puts("PASS shutdown first-error propagation, ordered teardown, and close quarantine");
}
"""
    main_anchor = "int main(int argc, char **argv) {"
    check(prelude.count(main_anchor) == 1,
          "frozen ownership harness main insertion point changed")
    prelude = prelude.replace(main_anchor, functions + "\n" + main_anchor, 1)

    tests_anchor = "    test_recovery_error_and_retry();"
    tests = (
        "    if (argc > 1 && !strcmp(argv[1], \"baseline_shutdown_false_success\")) {\n"
        "        test_baseline_shutdown_false_success();\n"
        "        return 0;\n"
        "    }\n"
        "    if (argc > 1 && !strcmp(argv[1], \"final_shutdown_error_propagation\")) {\n"
        "        test_final_shutdown_error_propagation();\n"
        "        return 0;\n"
        "    }\n"
    )
    check(prelude.count(tests_anchor) == 1,
          "frozen ownership harness test insertion point changed")
    return prelude.replace(tests_anchor, tests + tests_anchor, 1)


def source_snapshot(root: Path, sources: dict[str, bytes]) -> dict[str, bytes]:
    return {relative: (root / relative).read_bytes() for relative in sources}


def main() -> int:
    FULL.check_input_guardrails()
    try:
        sources, _source_root, source_identity, source_bytes = FULL.load_exact_sources()
    except (FULL.STACK.SourceFixtureUnavailable,
            FULL.STACK.HELPERS.SourceFixtureUnavailable,
            OWN.SourceFixtureUnavailable) as error:
        print(f"SKIP exact pinned source fixture unavailable; exit 77: {error}")
        return 77

    patch_paths = RECON.patch_paths()
    FULL.patch_bytes(FULL.PROFILE_PATCH, FULL.PROFILE_PATCH.name,
                     FULL.PROFILE_SHA256)
    FULL.patch_bytes(OWN.OWN_PATCH, OWN.OWN_PATCH.name,
                     FROZEN_OWNERSHIP_SHA256)
    FULL.patch_bytes(ERROR_PATCH, ERROR_PATCH.name, ERROR_PATCH_SHA256)
    print(f"SOURCE_FIXTURE {source_identity}")
    print(f"SOURCE_UNION_BYTES {source_bytes}")
    print("PATCH_ORDER " + " -> ".join(
        [*(path.name for path in patch_paths), FULL.PROFILE_PATCH.name,
         ERROR_PATCH.name]))

    compiler = shutil.which("cc") or shutil.which("gcc")
    check(compiler is not None, "host C compiler cc/gcc is required")
    with tempfile.TemporaryDirectory(prefix="s22-npu-shutdown-error-") as name:
        temp = Path(name)
        full_root = temp / "full-six-patch-source"
        full_root.mkdir()
        FULL.write_exact_fixture(full_root, sources)
        RECON.apply_patch_series(full_root, patch_paths)

        frozen_check = subprocess.run(
            ["git", "apply", "--check", "--whitespace=error-all",
             str(OWN.OWN_PATCH)],
            cwd=full_root, capture_output=True, text=True,
            check=False, timeout=15,
        )
        check(frozen_check.returncode != 0 and "npu-vertex.c:313" in frozen_check.stderr,
              "frozen ownership patch no longer reports its reviewed overlap")
        FULL.apply_plain_patch(full_root, FULL.PROFILE_PATCH,
                               "fifth shutdown-lifecycle profile")
        after_five = source_snapshot(full_root, sources)
        FULL.apply_plain_patch(full_root, ERROR_PATCH,
                               "sixth shutdown-error propagation patch")
        final = source_snapshot(full_root, sources)
        changed = {relative for relative in sources
                   if after_five[relative] != final[relative]}
        check(changed == {OWN.DEVICE_C},
              f"sixth patch changed unexpected source paths: {sorted(changed)!r}")
        print("PASS all six ordinary git apply --check/apply steps; only npu-device.c changed in patch six")
        print("PASS frozen ownership patch SHA and reviewed overlap preserved")

        baseline_functions = OWN.extract_functions(after_five)
        final_functions = OWN.extract_functions(final)
        prior_prelude = OWN.PRELUDE
        OWN.PRELUDE = make_error_harness(OWN.PRELUDE)
        try:
            for optimization in ("-O0", "-O2"):
                baseline = OWN.compile_and_run(
                    compiler, temp, f"baseline-mask-{optimization[2:]}",
                    baseline_functions, optimization,
                    "baseline_shutdown_false_success",
                )
                check(baseline.returncode == 0 and
                      "BASELINE_REPRODUCED early/protocol close false success and caller free"
                      in baseline.stdout,
                      f"pre-fix actual C false success did not reproduce at {optimization}: "
                      f"{baseline.stdout}{baseline.stderr}")
                print(baseline.stdout, end="")

                final_result = OWN.compile_and_run(
                    compiler, temp, f"final-propagation-{optimization[2:]}",
                    final_functions, optimization,
                    "final_shutdown_error_propagation",
                )
                check(final_result.returncode == 0 and
                      "PASS shutdown first-error propagation, ordered teardown, and close quarantine"
                      in final_result.stdout,
                      f"final actual C regressions failed at {optimization}: "
                      f"{final_result.stdout}{final_result.stderr}")
                print(final_result.stdout, end="")

                ownership = OWN.compile_and_run(
                    compiler, temp, f"final-ownership-{optimization[2:]}",
                    final_functions, optimization,
                )
                check(ownership.returncode == 0 and
                      "PASS extracted NPU shutdown/recovery C ownership regressions"
                      in ownership.stdout,
                      f"existing extracted ownership regressions failed at {optimization}: "
                      f"{ownership.stdout}{ownership.stderr}")
                print(ownership.stdout, end="")
        finally:
            OWN.PRELUDE = prior_prelude

        FULL.STACK.run_preflight(full_root, final, temp)
        print("PASS BOOTUP readiness and authorization remain false after patch six")
        print("LIMIT extracted source C with host shims only; no kernel, firmware, module, runtime, or device acceptance")
    return 0


if __name__ == "__main__":
    sys.exit(main())
