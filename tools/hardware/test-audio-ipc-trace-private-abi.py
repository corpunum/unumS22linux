#!/usr/bin/env python3
"""Test private ABOX trace metadata against the composed pinned production C."""

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
PM_TEST = TOOLS / "test-audio-ipc-worker-pm.py"
PM_HARNESS = TOOLS / "audio-ipc-worker-pm-harness.c"
HARNESS = TOOLS / "audio-ipc-trace-private-abi-harness.c"
PATCH = TOOLS / "audio-ipc-trace-private-abi.patch"
DUPLICATE_PATCH = TOOLS / "audio-ipc-duplicate-rekick.patch"
DUPLICATE_HARNESS = TOOLS / "audio-ipc-duplicate-rekick-harness.c"

BASELINE_FAILURES = (
    "FAIL: baseline struct abox_ipc preserves its exported ABI size",
    "FAIL: baseline concurrent workers preserve both message/trace pairs",
)
MUTATION_FAILURES = {
    "OMIT_ZERO_OVERWRITE": (
        "FAIL: zero-sequence put overwrites reused private metadata before dequeue",
        "FAIL: actual put zero-overwrites the reused slot before traced dequeue",
    ),
    "OMIT_CLEAR": (
        "FAIL: actual queue_get clears the consumed private sequence slot",
    ),
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def import_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    require(spec is not None and spec.loader is not None,
            f"cannot load fixture helper: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_helpers():
    pm_test = import_module(PM_TEST, "audio_ipc_worker_pm_test")
    return pm_test, *pm_test.load_source_test()


def command(args: list[str], *, cwd: Path | None = None,
            timeout: int = 30) -> subprocess.CompletedProcess:
    completed = subprocess.run(
        args, cwd=cwd, check=False, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True, timeout=timeout,
    )
    require(completed.returncode == 0,
            f"command failed ({completed.returncode}): {shlex.join(args)}\n"
            f"{completed.stdout}")
    return completed


def apply_patch(source_root: Path, patch: Path, label: str) -> None:
    command(["git", "-C", str(source_root), "apply", "--check", str(patch)])
    command(["git", "-C", str(source_root), "apply", str(patch)])
    print(f"PASS: ordinary git apply --check and apply: {label}")


def marker_section(text: str, name: str) -> str:
    begin = f"/* {name}_BEGIN */"
    end = f"/* {name}_END */"
    require(text.count(begin) == 1 and text.count(end) == 1,
            f"missing or repeated harness section: {name}")
    return text.split(begin, 1)[1].split(end, 1)[0]


def owner_source_block(source: str) -> str:
    start = source.find("struct abox_ipc_trace_owner {")
    end_marker = "\n\n/* For only external static functions */"
    end = source.find(end_marker, start)
    require(start >= 0 and end > start,
            "private owner registry block is missing from patched ABOX source")
    return source[start:end]


def replace_function(source_test, source: str, name: str,
                     replacement: str) -> str:
    old = source_test.c_block(source, name)
    require(source.count(old) == 1,
            f"expected one host shim definition for {name}")
    return source.replace(old, replacement)


def render_harness(source_test, sources: dict[str, bytes],
                   fragments: str, *, patched: bool) -> str:
    template = PM_HARNESS.read_text()
    state = marker_section(fragments, "ABI_TRACE_STATE")
    scenarios = marker_section(fragments, "ABI_SCENARIOS")
    duplicate_scenarios = DUPLICATE_HARNESS.read_text()
    duplicate_start = duplicate_scenarios.find("static void reset_duplicate_world")
    duplicate_end = duplicate_scenarios.find(
        "\n#ifdef EXPECT_DUPLICATE_REKICK_BUG\nint main", duplicate_start)
    require(duplicate_start >= 0 and duplicate_end > duplicate_start,
            "existing duplicate-rekick C scenarios cannot be composed")
    require(scenarios.count("/* ABI_DUPLICATE_SCENARIOS */") == 1,
            "duplicate scenario insertion marker is ambiguous")
    scenarios = scenarios.replace(
        "/* ABI_DUPLICATE_SCENARIOS */",
        duplicate_scenarios[duplicate_start:duplicate_end],
        1,
    )

    template = template.replace(
        "#include <errno.h>",
        "#define _XOPEN_SOURCE 700\n#include <errno.h>\n"
        "#include <sched.h>\n#include <stdlib.h>",
        1,
    )
    template = template.replace(
        "typedef pthread_mutex_t spinlock_t;",
        "typedef pthread_mutex_t spinlock_t;\n"
        "static __thread int g_host_queue_lock_depth;",
        1,
    )
    template = template.replace(
        "pthread_mutex_lock(lock); \\\n} while (0)",
        "pthread_mutex_lock(lock); \\\n\t++g_host_queue_lock_depth; \\\n} while (0)",
        1,
    )
    template = template.replace(
        "pthread_mutex_unlock(lock); \\\n} while (0)",
        "--g_host_queue_lock_depth; \\\n\tpthread_mutex_unlock(lock); \\\n} while (0)",
        1,
    )
    old_device = """struct device {
	void *driver_data;
	int usage_count;
	bool runtime_active;
};"""
    new_device = """struct device {
	void *driver_data;
	int usage_count;
	bool runtime_active;
	bool fail_devm_allocation;
	bool fail_devm_action;
	void *devm_allocation;
	void (*devm_action)(void *);
	void *devm_action_argument;
};"""
    require(template.count(old_device) == 1, "PM harness device shim changed")
    template = template.replace(old_device, new_device)
    require(template.count("\tu64 trace_seq;\n") == 1,
            "PM harness IPC-layout marker is ambiguous")
    template = template.replace(
        "\tu64 trace_seq;\n",
        "\tu64 trace_seq;\n" if not patched else "",
        1,
    )

    for name in ("trace_abox_pcm_trigger_queue_enabled",
                 "trace_abox_pcm_trigger_send_enabled",
                 "trace_abox_pcm_trigger_queue",
                 "trace_abox_pcm_trigger_send"):
        old = source_test.c_block(template, name)
        template = template.replace(old, "", 1)
    template = template.replace("static int g_failures;", state +
                                "\nstatic int g_failures;", 1)

    queue_work_replacement = """static int queue_work(struct workqueue_struct *queue,
		struct work_struct *work)
{
	(void)queue;
	(void)work;
	__sync_add_and_fetch(&g_queue_work_calls, 1);
	if (g_host_queue_lock_depth > 0)
		__sync_add_and_fetch(&g_queue_work_under_lock_calls, 1);
	return 1;
}"""
    template = replace_function(source_test, template, "queue_work",
                                queue_work_replacement)
    flush_work_replacement = """static void flush_work(struct work_struct *work)
{
	(void)work;
	__sync_add_and_fetch(&g_flush_work_calls, 1);
	if (g_host_queue_lock_depth > 0)
		__sync_add_and_fetch(&g_flush_work_under_lock_calls, 1);
}"""
    template = replace_function(source_test, template, "flush_work",
                                flush_work_replacement)

    send_replacement = """static int abox_ipc_send(struct device *dev, const ABOX_IPC_MSG *msg,
		size_t size, void *response, size_t response_size)
{
	int sent_index;
	(void)size;
	(void)response;
	(void)response_size;
	abi_wait_before_send();
	__sync_add_and_fetch(&g_send_calls, 1);
	if (!dev->runtime_active)
		__sync_add_and_fetch(&g_unpowered_send_calls, 1);
	pthread_mutex_lock(&g_abi_trace_lock);
	if (msg->ipcid == IPC_PCMPLAYBACK &&
			msg->msg.pcmtask.msgtype == PCM_PLTDAI_TRIGGER) {
		sent_index = __sync_fetch_and_add(&g_sent_trigger_count, 1);
		if (sent_index < (int)ARRAY_SIZE(g_sent_triggers))
			g_sent_triggers[sent_index] =
				msg->msg.pcmtask.param.trigger;
	}
	pthread_mutex_unlock(&g_abi_trace_lock);
	return g_send_result;
}"""
    template = replace_function(source_test, template,
                                "abox_ipc_send", send_replacement)

    # Use atomic host counters in the two-worker race; production code remains
    # the exact extracted C and all assertions run after both threads join.
    for variable in (
        "g_queue_work_calls", "g_flush_work_calls", "g_mdelay_calls",
        "g_failsafe_calls", "g_resume_calls", "g_put_noidle_calls",
        "g_put_autosuspend_calls", "g_mark_busy_calls", "g_calliope_calls",
        "g_pm_error_log_calls", "g_sched_clock_calls",
    ):
        template = template.replace(f"++{variable};",
                                    f"__sync_add_and_fetch(&{variable}, 1);")
    template = template.replace("return (unsigned long long)++g_clock;",
                                "return (unsigned long long)__sync_add_and_fetch(&g_clock, 1);")
    template = template.replace("return (u64)++g_clock;",
                                "return (u64)__sync_add_and_fetch(&g_clock, 1);")
    template = template.replace("return ++*counter;",
                                "return __sync_add_and_fetch(counter, 1);")

    if patched:
        owner_block = owner_source_block(
            sources["sound/soc/samsung/abox/abox.c"].decode())
        marker = "/* ACTUAL_QUEUE_EMPTY_FUNCTION */"
        require(template.count(marker) == 1,
                "private owner registry insertion point is ambiguous")
        template = template.replace(marker, owner_block + "\n" + marker, 1)

    abox_source = sources["sound/soc/samsung/abox/abox.c"].decode()
    abox_header = sources["sound/soc/samsung/abox/abox.h"].decode()
    template = template.replace(
        "/* ACTUAL_IPC_RETRY_DEFINE */",
        source_test.c_define(abox_source, "IPC_RETRY"),
    )
    template = template.replace(
        "/* ACTUAL_QUEUE_SIZE_DEFINE */",
        source_test.c_define(abox_header, "ABOX_IPC_QUEUE_SIZE"),
    )
    for marker, relative, function_name in source_test_function_specs():
        function = source_test.c_block(sources[relative].decode(), function_name)
        require(template.count(marker) == 1,
                f"missing or repeated actual-C extraction marker: {marker}")
        template = template.replace(marker, function)

    main_marker = "test_existing_sender_error_and_pm_balance();"
    require(template.count(main_marker) == 1,
            "PM harness main marker changed")
    template = template.replace(
        main_marker,
        main_marker + "\n\trun_duplicate_rekick_scenarios();"
        "\n\ttest_private_abi_scenarios();",
        1,
    )
    main_marker = "static void test_negative_resume(void)"
    # Insert scenario definitions before the PM main-only block, after actual
    # source C and shared PM assertions have all been declared.
    require(template.count(main_marker) == 1,
            "PM harness scenario insertion point changed")
    template = template.replace(main_marker,
                                scenarios + "\n" + main_marker, 1)
    return template


def source_test_function_specs():
    return (
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


def verify_source_contract(source_test, original: dict[str, bytes],
                           before: dict[str, bytes],
                           patched: dict[str, bytes]) -> None:
    header_path = "sound/soc/samsung/abox/abox.h"
    original_header = original[header_path].decode()
    before_header = before[header_path].decode()
    patched_header = patched[header_path].decode()
    original_ipc = source_test.c_block(original_header, "abox_ipc", kind="struct")
    before_ipc = source_test.c_block(before_header, "abox_ipc", kind="struct")
    patched_ipc = source_test.c_block(patched_header, "abox_ipc", kind="struct")
    require("trace_seq" not in original_ipc and "trace_seq" in before_ipc,
            "baseline ABI regression was not reproduced from composed patches")
    require(patched_ipc == original_ipc,
            "patched struct abox_ipc does not exactly match the pinned ABI source")
    for name in ("abox_data",):
        require(source_test.c_block(patched_header, name, kind="struct") ==
                source_test.c_block(original_header, name, kind="struct"),
                f"patched struct {name} differs from pinned ABI source")

    source = patched["sound/soc/samsung/abox/abox.c"].decode()
    worker = source_test.c_block(source, "abox_process_ipc")
    require("static struct abox_ipc ipc" not in worker and
            "struct abox_ipc ipc;" in worker,
            "worker IPC scratch remains shared across distinct owner workqueues")
    probe = source_test.c_block(source, "samsung_abox_probe")
    registration = """ret = abox_ipc_trace_owner_register(dev, data);
	if (ret)
		dev_warn_ratelimited(dev,
				"%s: PCM IPC trace correlation unavailable: %d\\n",
				__func__, ret);"""
    require(registration in probe and
            probe.index("abox_ipc_trace_owner_register") <
            probe.index("alloc_workqueue"),
            "trace allocation failure is not explicit degraded diagnostics before work setup")
    remove = source_test.c_block(source, "samsung_abox_remove")
    require(remove.index("destroy_workqueue(data->ipc_workqueue)") <
            remove.index("snd_soc_unregister_component(dev)") <
            remove.index("abox_ipc_trace_owner_unregister_data(data)"),
            "sidecar retirement is not ordered after existing worker/component stop")
    owner = owner_source_block(source)
    require("if (removed)\n\t\tsynchronize_rcu();" in owner and
            "if (!trace_owner)\n\t\t*trace_seq = 0;" in source,
            "owner retirement or no-sidecar degraded correlation contract changed")
    print("PASS: exact pinned struct abox_ipc and abox_data layouts restored in source")
    print("PASS: probe allocation failure is nonfatal, visible, and suppresses false correlation")
    print("PASS: owner lookup/retirement is keyed per instance and RCU synchronized")


def mutate_sidecar_source(sources: dict[str, bytes], mutation: str) -> dict[str, bytes]:
    relative = "sound/soc/samsung/abox/abox.c"
    source = sources[relative].decode()
    if mutation == "OMIT_ZERO_OVERWRITE":
        old = """\t\tif (trace_owner)
\t\t\ttrace_owner->trace_seq[data->ipc_queue_end] = *trace_seq;
"""
        new = """\t\tif (trace_owner && *trace_seq)
\t\t\ttrace_owner->trace_seq[data->ipc_queue_end] = *trace_seq;
"""
    elif mutation == "OMIT_CLEAR":
        old = """\t\tif (trace_owner) {
\t\t\t*trace_seq = trace_owner->trace_seq[data->ipc_queue_start];
\t\t\ttrace_owner->trace_seq[data->ipc_queue_start] = 0;
\t\t}
"""
        new = """\t\tif (trace_owner)
\t\t\t*trace_seq = trace_owner->trace_seq[data->ipc_queue_start];
"""
    else:
        raise RuntimeError(f"unknown source mutation: {mutation}")
    require(source.count(old) == 1,
            f"mutation target is not unique in actual queue source: {mutation}")
    mutated = dict(sources)
    mutated[relative] = source.replace(old, new, 1).encode()
    return mutated


def compile_and_run(cc: str, level: str, source: str, directory: Path,
                    *, baseline: bool,
                    mutation: str | None = None) -> subprocess.CompletedProcess:
    source_file = directory / f"audio-ipc-trace-abi-{level[2:]}.c"
    binary = directory / f"audio-ipc-trace-abi-{level[2:]}"
    source_file.write_text(source)
    args = [
        *shlex.split(cc), "-std=c11", "-pthread", level,
        "-Wall", "-Wextra", "-Werror", "-Wno-unused-parameter",
        "-Wno-unused-function", "-Wno-sign-compare",
    ]
    if baseline:
        args.append("-DEXPECT_BASELINE_PRIVATE_ABI_BUG=1")
    if mutation:
        args.append(f"-DMUTATION_{mutation}=1")
    args.extend([str(source_file), "-o", str(binary)])
    compiled = command(args)
    require(not compiled.stdout,
            f"unexpected compiler output: {compiled.stdout}")
    return subprocess.run([str(binary)], check=False,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          text=True, timeout=15)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-tree", type=Path, default=None,
                        help="verified derived source tree; omitted uses capped pinned fixtures")
    parser.add_argument("--cc", default=os.environ.get("CC", "cc"))
    args = parser.parse_args()

    pm_test, error_test, source_test = load_helpers()
    try:
        original, source_identity = source_test.load_sources(args.source_tree)
    except source_test.SourceFixtureUnavailable as error:
        print(f"SKIP: SOURCE_FIXTURE_UNAVAILABLE: {error}")
        return 77

    fragments = HARNESS.read_text()
    require(PATCH.is_file() and DUPLICATE_PATCH.is_file() and
            PM_HARNESS.is_file(),
            "one or more immutable composition patches or harnesses are missing")
    with tempfile.TemporaryDirectory(prefix="abox-trace-private-abi-") as temporary:
        temp_root = Path(temporary)
        for relative, content in original.items():
            target = temp_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        command(["git", "init", "-q", str(temp_root)])
        command(["git", "-C", str(temp_root), "add", *original.keys()])

        apply_patch(temp_root, error_test.OBSERVATION_PATCH,
                    "observation trace patch first")
        apply_patch(temp_root, error_test.ERROR_PATCH,
                    "queue/state linearization patch second")
        apply_patch(temp_root, pm_test.PM_PATCH,
                    "runtime-PM ownership patch third")
        apply_patch(temp_root, DUPLICATE_PATCH,
                    "explicit duplicate re-kick patch fourth")
        before = {relative: (temp_root / relative).read_bytes()
                  for relative in original}
        baseline_harness = render_harness(source_test, before, fragments,
                                          patched=False)

        apply_patch(temp_root, PATCH,
                    "private trace metadata ABI patch fifth")
        after = {relative: (temp_root / relative).read_bytes()
                 for relative in original}
        verify_source_contract(source_test, original, before, after)
        command(["git", "-C", str(temp_root), "diff", "--check"])
        patched_harness = render_harness(source_test, after, fragments,
                                         patched=True)

        with tempfile.TemporaryDirectory(prefix="abox-trace-private-abi-c-") as c_temp:
            c_root = Path(c_temp)
            for level in ("-O0", "-O2"):
                base = compile_and_run(args.cc, level, baseline_harness,
                                       c_root, baseline=True)
                failures = tuple(line for line in base.stdout.splitlines()
                                 if line.startswith("FAIL: "))
                require(base.returncode == 1 and set(failures) ==
                        set(BASELINE_FAILURES) and
                        len(failures) == len(BASELINE_FAILURES) and
                        base.stdout.splitlines()[-1] ==
                        f"{len(BASELINE_FAILURES)} host assertions failed",
                        f"composed baseline did not reproduce only ABI/scratch defects at {level}:\n"
                        f"{base.stdout}")
                print(f"{level}: PASS: composed baseline reproduces ABI and concurrent scratch defects")
                print(f"{level}: PASS: four-patch baseline direct-get/physical-slot zero case passes")

                fixed = compile_and_run(args.cc, level, patched_harness,
                                        c_root, baseline=False)
                expected = "PASS: extracted ABOX PM worker, queue retention, and explicit FIFO retry"
                require(fixed.returncode == 0 and
                        fixed.stdout.strip() == expected,
                        f"patched actual extracted C failed at {level}:\n{fixed.stdout}")
                print(f"{level}: {fixed.stdout.strip()}")

                for mutation, expected_failures in MUTATION_FAILURES.items():
                    mutated_sources = mutate_sidecar_source(after, mutation)
                    mutated_harness = render_harness(
                        source_test, mutated_sources, fragments, patched=True)
                    mutated = compile_and_run(
                        args.cc, level, mutated_harness, c_root,
                        baseline=False, mutation=mutation)
                    failures = tuple(line for line in mutated.stdout.splitlines()
                                     if line.startswith("FAIL: "))
                    require(mutated.returncode == 1 and
                            failures == expected_failures and
                            mutated.stdout.splitlines()[-1] ==
                            f"{len(expected_failures)} host assertions failed",
                            f"{mutation} control did not fail only its dedicated assertion at {level}:\n"
                            f"{mutated.stdout}")
                    print(f"{level}: PASS: {mutation} mutation is caught by its dedicated assertion")

    print(f"PASS: bounded pinned source fixture {source_identity}")
    print("PASS: extracted queue, scheduler, FE/BE helpers, worker, registry, and teardown C")
    print("PASS: existing duplicate re-kick, full-queue, generic, and sync paths included")
    print("PASS: source/layout/ownership checks are host evidence only, not module CRC evidence")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
