#!/usr/bin/env python3
"""Host-test the pinned ABOX async-worker runtime-PM error path."""

from __future__ import annotations

import argparse
import importlib.util
import os
from pathlib import Path
import shlex
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "tools/hardware"
ERROR_TEST = TOOLS / "test-audio-ipc-error-path.py"
HARNESS = TOOLS / "audio-ipc-worker-pm-harness.c"
PM_PATCH = TOOLS / "audio-ipc-worker-pm.patch"
PM_SOURCE_FILES = {
    "include/linux/pm_runtime.h": "8a5982620fd46a59346c9568f9fcf57790509d81421b610000dd09a25c0daac1",
    "drivers/base/power/runtime.c": "569c8d962a549f86f22af0800c1ba6fa337fddb280b623128fb960b563539d04",
    "include/linux/dev_printk.h": "0ddbf1de2356d80047cda83b0bcfcebbfe57dda5410ab5283c86c3ac84ab1695",
    "sound/soc/samsung/abox/abox_failsafe.c": "29df1a56899cee66b98c4945d53fef51462833a854ff3de37a83fa907f579f79",
}
FUNCTION_SPECS = (
    ("/* ACTUAL_QUEUE_EMPTY_FUNCTION */", "sound/soc/samsung/abox/abox.c", "__abox_ipc_queue_empty"),
    ("/* ACTUAL_QUEUE_FULL_FUNCTION */", "sound/soc/samsung/abox/abox.c", "__abox_ipc_queue_full"),
    ("/* ACTUAL_QUEUE_PUT_FUNCTION */", "sound/soc/samsung/abox/abox.c", "abox_ipc_queue_put"),
    ("/* ACTUAL_QUEUE_GET_FUNCTION */", "sound/soc/samsung/abox/abox.c", "abox_ipc_queue_get"),
    ("/* ACTUAL_PROCESS_IPC_FUNCTION */", "sound/soc/samsung/abox/abox.c", "__abox_process_ipc"),
    ("/* ACTUAL_WORKER_FUNCTION */", "sound/soc/samsung/abox/abox.c", "abox_process_ipc"),
    ("/* ACTUAL_SCHEDULER_FUNCTION */", "sound/soc/samsung/abox/abox.c", "abox_schedule_ipc"),
    ("/* ACTUAL_GENERIC_REQUEST_FUNCTION */", "sound/soc/samsung/abox/abox.c", "abox_request_ipc"),
    ("/* ACTUAL_SPECIALIZED_REQUEST_FUNCTION */", "sound/soc/samsung/abox/abox.c", "abox_request_pcm_trigger_ipc"),
    ("/* ACTUAL_RDMA_BACKEND_FUNCTION */", "sound/soc/samsung/abox/abox_rdma.c", "abox_rdma_backend"),
    ("/* ACTUAL_TRIGGER_HELPER_FUNCTION */", "sound/soc/samsung/abox/abox_rdma.c", "abox_rdma_trigger_ipc"),
    ("/* ACTUAL_FE_TRIGGER_FUNCTION */", "sound/soc/samsung/abox/abox_rdma.c", "abox_rdma_trigger"),
    ("/* ACTUAL_BE_MUTE_FUNCTION */", "sound/soc/samsung/abox/abox_rdma.c", "abox_rdma_mute_stream"),
)
BASELINE_FAILURES = (
    "FAIL: failed get_sync reference is balanced once with put_noidle",
    "FAIL: failed resume returns before dequeue and preserves FIFO contents",
    "FAIL: failed resume never sends IPC while runtime resume failed",
    "FAIL: failed resume returns before Calliope gating",
    "FAIL: failed resume does not enter successful worker PM-release path",
    "FAIL: failed resume emits a rate-limited function-and-errno log",
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def import_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    require(spec is not None and spec.loader is not None,
            f"cannot load pinned source helper: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_source_test():
    error_test = import_module(ERROR_TEST, "audio_ipc_error_path_test")
    source_test = error_test.load_source_test()
    source_test.SOURCE_FILES.update(PM_SOURCE_FILES)
    return error_test, source_test


def command(args: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess:
    completed = subprocess.run(
        args, cwd=cwd, check=False, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True, timeout=30,
    )
    require(completed.returncode == 0,
            f"command failed ({completed.returncode}): {shlex.join(args)}\n{completed.stdout}")
    return completed


def verify_pinned_pm_semantics(sources: dict[str, bytes]) -> None:
    pm_runtime = sources["include/linux/pm_runtime.h"].decode()
    runtime_core = sources["drivers/base/power/runtime.c"].decode()
    dev_printk = sources["include/linux/dev_printk.h"].decode()
    failsafe = sources["sound/soc/samsung/abox/abox_failsafe.c"].decode()

    require("incremented in all cases, even if it returns an error code." in pm_runtime,
            "pinned pm_runtime_get_sync documentation changed")
    require("return __pm_runtime_resume(dev, RPM_GET_PUT);" in pm_runtime,
            "pinned pm_runtime_get_sync no longer uses RPM_GET_PUT")
    require("ret = __pm_runtime_resume(dev, RPM_GET_PUT);" in pm_runtime and
            "if (ret < 0) {\n\t\tpm_runtime_put_noidle(dev);" in pm_runtime,
            "pinned resume_and_get no longer balances a failed get_sync with put_noidle")
    require("atomic_add_unless(&dev->power.usage_count, -1, 0);" in pm_runtime,
            "pinned put_noidle no longer drops only the runtime-PM usage reference")
    require("if (rpmflags & RPM_GET_PUT)\n\t\tatomic_inc(&dev->power.usage_count);" in runtime_core,
            "pinned runtime core no longer increments before resume")
    require("#define dev_err_ratelimited(dev, fmt, ...)" in dev_printk,
            "pinned device logger lacks rate-limited error logging")
    require("BUG_ON(error && (abox_failsafe_abox_data->debug_mode == DEBUG_MODE_DRAM));" in failsafe and
            "schedule_work(&abox_failsafe_report_work);" in failsafe and
            "abox_silent_reset(data, true);" in failsafe,
            "pinned failsafe behavior changed; do not infer it is safe for PM-resume errors")


def render_harness(source_test, harness: str,
                   sources: dict[str, bytes]) -> str:
    abox_source = sources["sound/soc/samsung/abox/abox.c"].decode()
    abox_header = sources["sound/soc/samsung/abox/abox.h"].decode()
    rendered = harness.replace(
        "/* ACTUAL_IPC_RETRY_DEFINE */",
        source_test.c_define(abox_source, "IPC_RETRY"),
    )
    rendered = rendered.replace(
        "/* ACTUAL_QUEUE_SIZE_DEFINE */",
        source_test.c_define(abox_header, "ABOX_IPC_QUEUE_SIZE"),
    )
    for marker, relative, function_name in FUNCTION_SPECS:
        function = source_test.c_block(sources[relative].decode(), function_name)
        require(rendered.count(marker) == 1,
                f"missing or repeated extracted-C marker: {marker}")
        rendered = rendered.replace(marker, function)
    return rendered


def compile_and_run(cc: str, level: str, source: str, directory: Path,
                    *, baseline: bool) -> subprocess.CompletedProcess:
    source_file = directory / f"audio-ipc-worker-pm-{level[2:]}.c"
    binary = directory / f"audio-ipc-worker-pm-{level[2:]}"
    source_file.write_text(source)
    args = [
        *shlex.split(cc), "-std=c11", "-pthread", level,
        "-Wall", "-Wextra", "-Werror", "-Wno-unused-parameter",
        "-Wno-unused-function", "-Wno-sign-compare",
    ]
    if baseline:
        args.append("-DEXPECT_BASELINE_PM_BUG=1")
    args.extend([str(source_file), "-o", str(binary)])
    compile_result = command(args)
    require(not compile_result.stdout,
            f"unexpected compiler output: {compile_result.stdout}")
    return subprocess.run(
        [str(binary)], check=False, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True, timeout=10,
    )


def apply_patch(source_root: Path, patch: Path, label: str) -> None:
    command(["git", "-C", str(source_root), "apply", "--check", str(patch)])
    command(["git", "-C", str(source_root), "apply", str(patch)])
    print(f"PASS: ordinary git apply --check and apply: {label}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-tree", type=Path, default=None,
                        help="exact derived source tree; omitted uses capped pinned public fixtures")
    parser.add_argument("--cc", default=os.environ.get("CC", "cc"))
    args = parser.parse_args()

    error_test, source_test = load_source_test()
    try:
        fixtures, source_identity = source_test.load_sources(args.source_tree)
    except source_test.SourceFixtureUnavailable as error:
        print(f"SKIP: SOURCE_FIXTURE_UNAVAILABLE: {error}")
        return 77

    verify_pinned_pm_semantics(fixtures)
    require(HARNESS.is_file() and PM_PATCH.is_file(),
            "required PM harness or patch is missing")
    harness_template = HARNESS.read_text()
    with tempfile.TemporaryDirectory(prefix="abox-worker-pm-") as temporary:
        temp_root = Path(temporary)
        for relative, content in fixtures.items():
            target = temp_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        command(["git", "init", "-q", str(temp_root)])
        command(["git", "-C", str(temp_root), "add", *fixtures.keys()])

        apply_patch(temp_root, error_test.OBSERVATION_PATCH,
                    "observation patch first")
        apply_patch(temp_root, error_test.ERROR_PATCH,
                    "queue/state linearization second")
        before_sources = {relative: (temp_root / relative).read_bytes()
                          for relative in fixtures}
        baseline = render_harness(source_test, harness_template, before_sources)

        apply_patch(temp_root, PM_PATCH,
                    "worker runtime-PM error repair third")
        after_sources = {relative: (temp_root / relative).read_bytes()
                         for relative in fixtures}
        patched = render_harness(source_test, harness_template, after_sources)

        with tempfile.TemporaryDirectory(prefix="abox-worker-pm-c-") as c_temp:
            c_root = Path(c_temp)
            for level in ("-O0", "-O2"):
                before = compile_and_run(args.cc, level, baseline, c_root,
                                         baseline=True)
                failures = tuple(line for line in before.stdout.splitlines()
                                 if line.startswith("FAIL: "))
                require(before.returncode == 1 and
                        set(failures) == set(BASELINE_FAILURES) and
                        len(failures) == len(BASELINE_FAILURES) and
                        before.stdout.splitlines()[-1] ==
                        f"{len(BASELINE_FAILURES)} expected baseline PM assertions failed",
                        f"baseline did not reproduce only the PM-resume defect at {level}:\n"
                        f"{before.stdout}")
                print(f"{level}: PASS: original worker reproduces failed-resume dequeue/send")

                after = compile_and_run(args.cc, level, patched, c_root,
                                        baseline=False)
                expected = "PASS: extracted ABOX PM worker, queue retention, and explicit FIFO retry"
                require(after.returncode == 0 and after.stdout.strip() == expected,
                        f"patched actual extracted C failed at {level}:\n{after.stdout}")
                print(f"{level}: {after.stdout.strip()}")

    print(f"PASS: pinned source fixture {source_identity}")
    print("PASS: PM API/core/logger/failsafe semantics verified from pinned source")
    print("PASS: extracted production queue, FE/BE helper, and worker C; no device interaction")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
