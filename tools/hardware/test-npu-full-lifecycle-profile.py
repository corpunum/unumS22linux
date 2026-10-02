#!/usr/bin/env python3
"""Apply the reviewed five-patch NPU profile and run final extracted C."""
from __future__ import annotations

import difflib
import hashlib
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
RECONCILED_TEST = ROOT / "tools/hardware/test-npu-reconciled-stack.py"
SHUTDOWN_TEST = ROOT / "tools/hardware/test-npu-shutdown-ownership.py"
PROFILE_PATCH = ROOT / "tools/hardware/npu-shutdown-lifecycle-profile.patch"
PROFILE_SHA256 = "b986e1896305fda55f1d702ed6f12dde646e4a84b3ce91009203b9d77b7a00e7"
FROZEN_SHUTDOWN_SHA256 = "a8af77122b4049fd38e21adfd01a8577d3e8f9bef1f68d0cfa091a5884a4d9f3"
SHUTDOWN_SOURCE_TREE_ENV = "S22_NPU_SHUTDOWN_SOURCE_TREE"
EXPECTED_FINAL_PROFILE_FILES = {
    "drivers/vision/npu/core/npu-device.c",
    "drivers/vision/npu/core/npu-device.h",
    "drivers/vision/npu/core/npu-hw-device.c",
    "drivers/vision/npu/core/npu-vertex.c",
}


def check(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    check(spec is not None and spec.loader is not None,
          f"cannot load reviewed host helper {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


RECON = load_module(RECONCILED_TEST, "s22_npu_reconciled_helpers")
OWN = load_module(SHUTDOWN_TEST, "s22_npu_shutdown_helpers")
STACK = RECON.STACK


def load_exact_sources() -> tuple[dict[str, bytes], Path, str]:
    configured = os.environ.get(STACK.SOURCE_TREE_ENV)
    check(configured,
          f"{STACK.SOURCE_TREE_ENV} must name the clean derived source fixture")
    source_root = Path(configured).expanduser().resolve()
    check(source_root.is_dir(), f"configured source fixture is missing: {source_root}")
    prior_shutdown_root = os.environ.get(SHUTDOWN_SOURCE_TREE_ENV)
    check(not prior_shutdown_root or Path(prior_shutdown_root).expanduser().resolve() == source_root,
          "NPU profile loaders must use the same exact local source fixture")
    os.environ[SHUTDOWN_SOURCE_TREE_ENV] = str(source_root)

    stack_sources, stack_identity = STACK.load_fixtures()
    shutdown_sources, shutdown_identity = OWN.load_sources()
    sources = dict(stack_sources)
    for relative, data in shutdown_sources.items():
        if relative in sources:
            check(sources[relative] == data,
                  f"overlapping source fixtures differ: {relative}")
        sources[relative] = data
    return sources, source_root, f"{stack_identity}; {shutdown_identity}"


def write_exact_fixture(root: Path, sources: dict[str, bytes]) -> None:
    # Both loaders supply exact SHA-verified inputs. Materialize the stack
    # fixture first, then add only paths not already written. The equality
    # check in load_exact_sources() prevents ambiguity, and no core path is
    # rewritten by the second writer.
    STACK.write_fixture(root, sources)
    stack_paths = set(STACK.STACK_FILES + STACK.PREFLIGHT_FILES)
    check(stack_paths.issubset(sources),
          "merged source fixture is missing a stack/preflight input")
    check(set(OWN.SOURCE_FILES).issubset(sources),
          "merged source fixture is missing a shutdown-ownership input")
    remaining = {relative: data for relative, data in sources.items()
                 if relative not in stack_paths}
    OWN.write_sources(root, remaining)


def apply_plain_patch(root: Path, path: Path, label: str) -> None:
    for check_only in (True, False):
        command = ["git", "apply", "--whitespace=error-all"]
        if check_only:
            command.append("--check")
        command.append(str(path))
        result = subprocess.run(
            command, cwd=root, capture_output=True, text=True,
            check=False, timeout=15,
        )
        check(result.returncode == 0,
              f"{label} {'--check' if check_only else 'apply'} failed:\n{result.stderr}")


def patch_bytes(path: Path, label: str, expected: str) -> bytes:
    check(path.is_file(), f"required patch is missing: {label}")
    data = path.read_bytes()
    check(len(data) <= STACK.HELPERS.MAX_SOURCE_BYTES,
          f"patch exceeds bounded input limit: {label}")
    actual = hashlib.sha256(data).hexdigest()
    check(actual == expected,
          f"SHA-256 mismatch for {label}: expected {expected}, got {actual}")
    return data


def check_unoverlapped_shutdown_effects(
    final: dict[str, bytes], standalone: dict[str, bytes],
) -> None:
    for relative in (OWN.DEVICE_H, OWN.DEVICE_C):
        check(final[relative] == standalone[relative],
              f"combined profile changed frozen shutdown effects in {relative}")

    hdev_final = OWN.function_body(final[OWN.HW_C].decode(),
                                   "int npu_hwdev_recovery_shutdown(")
    hdev_standalone = OWN.function_body(standalone[OWN.HW_C].decode(),
                                        "int npu_hwdev_recovery_shutdown(")
    # The refcount lifecycle prefix also edits this body to propagate each
    # checked put failure. Compare the shutdown patch's added guards directly,
    # and leave complete behavior to the final extracted-C runs below.
    for marker in (
        'hdev->status == NPU_HWDEV_STATUS_ERROR)\n'
        '\t\t\t\treturn -EIO;',
        'atomic_read(&hdev->init_cnt.refcount) <= 0)\n'
        '\t\t\t\tcontinue;',
        'atomic_read(&hdev->boot_cnt.refcount) <= 0)\n'
        '\t\t\t\treturn -EINVAL;',
    ):
        check(marker in hdev_final and marker in hdev_standalone,
              f"combined recovery-shutdown lost frozen ownership guard: {marker!r}")
    check(hdev_final.count("ret = npu_hw_ref_put(device, &hdev->init_cnt);") == 1 and
          hdev_final.count("ret = npu_hw_ref_put(device, &hdev->boot_cnt);") == 1 and
          hdev_final.count("if (ret)\n\t\t\t\t\treturn ret;") >= 2,
          "combined recovery-shutdown lost lifecycle checked-put propagation")

    unchanged_vertex_functions = (
        ("static inline int check_emergency(", True),
        ("static inline int check_emergency_vctx(", True),
        ("static int npu_vertex_open(", True),
        ("int npu_hwdev_secure_bootup(", True),
        ("int npu_hwdev_secure_bootdown(", True),
        ("int npu_hwdev_normal_bootdown(", True),
        ("static int npu_vertex_bootup(", True),
        ("static int __npu_vertex_bootup(", False),
    )
    final_vertex = final[OWN.VERTEX_C].decode()
    standalone_vertex = standalone[OWN.VERTEX_C].decode()
    for marker, secure_mode in unchanged_vertex_functions:
        final_body = OWN.function_body(final_vertex, marker, secure_mode)
        standalone_body = OWN.function_body(standalone_vertex, marker, secure_mode)
        check(final_body and final_body == standalone_body,
              f"combined profile changed frozen shutdown effect in {marker}")

    print("PASS frozen shutdown effects retained: device gates/recovery, ref draining, "
          "open/boot quarantine, secure paths, normal bootdown, and recovery caller")


def check_quarantine_and_lifecycle_gates(final: dict[str, bytes],
                                         lifecycle_vertex: bytes) -> None:
    OWN.check_source_gates(final)
    vertex = final[OWN.VERTEX_C].decode()
    close = OWN.function_body(vertex, "static int npu_vertex_close(", False)
    check(close.count("NPU_DEVICE_ERR_STATE_SHUTDOWN_UNCERTAIN") >= 4,
          "close must keep its pre-lock, post-lock, and failure quarantine paths")
    check(close.find("ret = __vref_put(&vertex->boot_cnt)") <
          close.find("ret = npu_hwdev_shutdown(device, hids)") <
          close.find("ret = npu_session_close(session)"),
          "close must retain protocol/ref, hardware-off, session-free order")
    check("vctx->state &= ~BIT(NPU_VERTEX_POWER);" in close and
          "if (!ret && power_error)" in close,
          "lifecycle POWER state and POWER_NOTIFY return handling must survive merge")

    bootup_functions = (
        ("int npu_hwdev_secure_bootup(", True),
        ("int npu_hwdev_secure_bootdown(", True),
        ("int npu_hwdev_normal_bootup(", True),
        ("int npu_hwdev_normal_bootdown(", True),
    )
    bodies = {marker: OWN.function_body(vertex, marker, secure)
              for marker, secure in bootup_functions}
    check(all(bodies.values()), "one or more final boot-control C bodies are missing")
    for marker, _ in bootup_functions:
        body = bodies[marker]
        lock_at = body.find("mutex_lock_interruptible(&vertex->lock)")
        postlock_check = body.find("ret = check_emergency_vctx(vctx);", lock_at)
        check(lock_at >= 0 and postlock_check > lock_at,
              f"missing post-lock quarantine check in {marker}")

    for marker, count_name in (
        ("int npu_hwdev_secure_bootup(", "normal_count"),
        ("int npu_hwdev_normal_bootup(", "secure_count"),
    ):
        body = bodies[marker]
        wait_start = body.find("while (retry)")
        reacquire = body.find("mutex_lock(&vertex->lock)", wait_start)
        gate = body.find("check_emergency_vctx(vctx)", reacquire)
        count_test = body.find(f"if (!vertex->{count_name})", reacquire)
        check(wait_start >= 0 and reacquire > wait_start and
              gate > reacquire and count_test > gate,
              f"missing post-wait quarantine check in {marker}")

    integrated_normal = OWN.function_body(vertex, "int npu_hwdev_normal_bootup(")
    lifecycle_normal = STACK.HELPERS.function_body(
        lifecycle_vertex.decode(), "int npu_hwdev_normal_bootup(")
    entry_check = (
        "\tlock_held = true;\n"
        "\tret = check_emergency_vctx(vctx);\n"
        "\tif (ret)\n"
        "\t\tgoto out_unlock;\n\n"
    )
    wait_check = (
        "\t\t\tlock_held = true;\n"
        "\t\t\tret = check_emergency_vctx(vctx);\n"
        "\t\t\tif (ret)\n"
        "\t\t\t\tgoto out_unlock;\n"
    )
    check(integrated_normal.count(entry_check) == 1 and
          integrated_normal.count(wait_check) == 1,
          "normal bootup quarantine gates must use lifecycle-owned lock tracking")
    stripped = integrated_normal.replace(entry_check, "\tlock_held = true;\n\n", 1)
    stripped = stripped.replace(wait_check, "\t\t\tlock_held = true;\n", 1)
    check(stripped == lifecycle_normal,
          "normal bootup composition changed lifecycle cleanup outside two quarantine gates:\n" +
          "".join(difflib.unified_diff(
              lifecycle_normal.splitlines(keepends=True),
              stripped.splitlines(keepends=True),
              fromfile="four-patch-prefix", tofile="combined-minus-gates")))
    check("bool lock_held = false;" in integrated_normal and
          "if (lock_held)" in integrated_normal and
          integrated_normal.count("out_unlock:") == 1,
          "normal bootup must retain conditional reverse-unwind lock ownership")

    print("PASS quarantine/lifecycle gates: four post-lock checks, two wait reacquisition checks, "
          "and lifecycle lock tracking retained")


def make_shutdown_harness(prelude: str) -> str:
    main_anchor = "int main(int argc, char **argv) {"
    check(prelude.count(main_anchor) == 1,
          "shutdown harness main insertion point changed")
    case = r"""
static void test_lifecycle_power_error_preserves_close_order(void) {
    struct file file;
    reset_fixture(); memset(&file, 0, sizeof(file));
    file.private_data = &S.vctx;
    S.vctx.state = BIT(NPU_VERTEX_POWER);
    D.vertex.normal_count = 1;
    T.power_notify_ret = -EHOSTDOWN;
    expect(npu_vertex_close(&file) == -EHOSTDOWN,
           "close preserves lifecycle POWER_NOTIFY error after teardown");
    expect(T.unreg_hw_calls == 1 && T.power_notify_calls == 1 &&
           T.boot_put_calls == 1 && T.hw_shutdown_calls == 1 &&
           T.session_close_calls == 1 && T.open_put_calls == 1,
           "POWER_NOTIFY error still completes ordered safe close");
    expect(T.events[0] == 1 && T.events[1] == 2 && T.events[2] == 50 &&
           T.events[3] == 5 && T.events[4] == 6 && T.events[5] == 7 &&
           T.events[6] == 10 && T.events[7] == 60 && T.events[8] == 70 &&
           T.events[9] == 80,
           "close retains protocol-before-hardware-before-session-free order");
    expect(!(S.vctx.state & BIT(NPU_VERTEX_POWER)) &&
           test_bit(NPU_DEVICE_ERR_STATE_EMERGENCY, &D.err_state) &&
           !test_bit(NPU_DEVICE_ERR_STATE_SHUTDOWN_UNCERTAIN, &D.err_state),
           "POWER_NOTIFY error preserves lifecycle state/error classification");
    release_fixture();
    puts("PASS combined lifecycle close C: POWER_NOTIFY error and reverse teardown order");
}
"""
    prelude = prelude.replace(main_anchor, case + "\n" + main_anchor, 1)
    call_anchor = "    test_normal_bootdown_error();\n"
    check(prelude.count(call_anchor) == 1,
          "shutdown harness final-case insertion point changed")
    prelude = prelude.replace(
        call_anchor,
        call_anchor + "    test_lifecycle_power_error_preserves_close_order();\n",
        1,
    )
    return prelude


def run_combined_refcount_regressions(temp: Path,
                                     final: dict[str, bytes]) -> None:
    # The pre-existing refcount harness predates the shutdown profile and lacks
    # its hardware-error status enum constant. Extend only that host shim with
    # the matching constant from the reviewed shutdown harness.
    original = RECON.REF.PATCHED_HARNESS
    status_line = next(
        (line for line in OWN.PRELUDE.splitlines()
         if line.startswith("#define NPU_HWDEV_STATUS_ERROR ")),
        None,
    )
    check(status_line is not None,
          "shutdown extracted-C shim lacks its hardware error status constant")
    shim = original.replace(
        "#include <string.h>\n",
        "#include <string.h>\n" + status_line + "\n",
        1,
    )
    check(shim != original,
          "refcount extracted-C harness string include anchor changed")
    check("    unsigned int status;\n" in shim,
          "refcount extracted-C hwdev status field is missing")
    RECON.REF.PATCHED_HARNESS = shim
    try:
        RECON.run_combined_c_regressions(temp, final)
    finally:
        RECON.REF.PATCHED_HARNESS = original


def main() -> int:
    sources, source_root, source_identity = load_exact_sources()
    patch_paths = RECON.patch_paths()
    check(PROFILE_PATCH.is_file(), "required shutdown-lifecycle profile is missing")
    patch_bytes(PROFILE_PATCH, PROFILE_PATCH.name, PROFILE_SHA256)
    patch_bytes(OWN.OWN_PATCH, OWN.OWN_PATCH.name, FROZEN_SHUTDOWN_SHA256)
    print(f"SOURCE_FIXTURE {source_identity}")
    print("PATCH_ORDER " + " -> ".join(
        [*(path.name for path in patch_paths), PROFILE_PATCH.name]))
    print("PASS SHA-256 and source equality: exact four-patch prefix, shutdown input, and profile")

    compiler = shutil.which("cc") or shutil.which("gcc")
    check(compiler is not None, "host C compiler cc/gcc is required")
    with tempfile.TemporaryDirectory(prefix="s22-npu-full-lifecycle-profile-") as name:
        temp = Path(name)
        standalone_root = temp / "standalone-shutdown"
        standalone_root.mkdir()
        OWN.write_sources(standalone_root, sources)
        OWN.apply_patch(standalone_root, OWN.OWN_PATCH,
                        "frozen standalone shutdown ownership patch")
        standalone = {relative: (standalone_root / relative).read_bytes()
                      for relative in OWN.SOURCE_FILES}

        full_root = temp / "full-five-patch-source"
        full_root.mkdir()
        write_exact_fixture(full_root, sources)
        lifecycle_vertex = RECON.apply_patch_series(full_root, patch_paths)
        after_four = {relative: (full_root / relative).read_bytes()
                      for relative in sources}

        frozen_check = subprocess.run(
            ["git", "apply", "--check", "--whitespace=error-all",
             str(OWN.OWN_PATCH)],
            cwd=full_root, capture_output=True, text=True,
            check=False, timeout=15,
        )
        check(frozen_check.returncode != 0 and "npu-vertex.c:313" in frozen_check.stderr,
              "frozen shutdown patch must retain the reviewed line-313 context blocker")
        print("PASS frozen patch blocker: ordinary --check still fails at npu-vertex.c:313")

        apply_plain_patch(full_root, PROFILE_PATCH,
                          "ordered shutdown-lifecycle profile")
        final = {relative: (full_root / relative).read_bytes()
                 for relative in sources}
        changed = {relative for relative in sources
                   if after_four[relative] != final[relative]}
        check(changed == EXPECTED_FINAL_PROFILE_FILES,
              f"profile changed unexpected source paths: {sorted(changed)!r}")
        check_unoverlapped_shutdown_effects(final, standalone)
        check_quarantine_and_lifecycle_gates(final, lifecycle_vertex)

        run_combined_refcount_regressions(temp, final)

        functions = OWN.extract_functions(final)
        prelude = make_shutdown_harness(OWN.PRELUDE)
        old_prelude = OWN.PRELUDE
        OWN.PRELUDE = prelude
        try:
            for optimization in ("-O0", "-O2"):
                result = OWN.compile_and_run(
                    compiler, temp, f"full-profile-shutdown-{optimization[2:]}",
                    functions, optimization,
                )
                check(result.returncode == 0,
                      f"full-profile final extracted C failed at {optimization}: "
                      f"{result.stdout}{result.stderr}")
                marker = "PASS combined lifecycle close C: POWER_NOTIFY error and reverse teardown order"
                check(marker in result.stdout,
                      f"combined close case missing at {optimization}")
                print(result.stdout, end="")
        finally:
            OWN.PRELUDE = old_prelude

        RECON.check_bootup_stays_refused(source_root, final, temp)
        print("PASS full five-patch extracted-C regressions at C -O0/-O2")
        print("LIMIT host shims only; no kernel build, module, firmware, device, or BOOTUP evidence")
    return 0


if __name__ == "__main__":
    sys.exit(main())
