#!/usr/bin/env python3
"""Host-test explicit duplicate ABOX callbacks re-kicking retained work."""

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
HARNESS = TOOLS / "audio-ipc-worker-pm-harness.c"
SCENARIOS = TOOLS / "audio-ipc-duplicate-rekick-harness.c"
PATCH = TOOLS / "audio-ipc-duplicate-rekick.patch"

BASELINE_FAILURES = (
    "FAIL: pending BE STOP duplicate schedules the retained FIFO exactly once",
    "FAIL: pending FE STOP duplicate schedules the retained FIFO exactly once",
    "FAIL: full-queue duplicate kicks worker once instead of retrying insertion",
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def command(args: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess:
    completed = subprocess.run(
        args, cwd=cwd, check=False, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True, timeout=30,
    )
    require(completed.returncode == 0,
            f"command failed ({completed.returncode}): {shlex.join(args)}\n{completed.stdout}")
    return completed


def replace_once(source: str, old: str, new: str, label: str) -> str:
    require(source.count(old) == 1,
            f"expected one {label} injection point, found {source.count(old)}")
    return source.replace(old, new)


def instrument_template(template: str) -> str:
    template = replace_once(
        template,
        "static u64 abox_pcm_trigger_trace_seq;\n",
        "static u64 abox_pcm_trigger_trace_seq;\n"
        "static bool g_duplicate_trace_queue_enabled;\n"
        "static int g_duplicate_queue_trace_calls;\n"
        "static int g_queue_work_under_lock_calls;\n"
        "static int g_flush_work_under_lock_calls;\n"
        "static bool duplicate_queue_lock_held(void);\n",
        "trace instrumentation globals",
    )
    template = replace_once(
        template,
        "\t++g_queue_work_calls;\n\treturn 1;\n",
        "\t++g_queue_work_calls;\n"
        "\tif (duplicate_queue_lock_held())\n"
        "\t\t++g_queue_work_under_lock_calls;\n"
        "\treturn 1;\n",
        "queue_work lock assertion",
    )
    template = replace_once(
        template,
        "\t++g_flush_work_calls;\n",
        "\t++g_flush_work_calls;\n"
        "\tif (duplicate_queue_lock_held())\n"
        "\t\t++g_flush_work_under_lock_calls;\n",
        "flush_work lock assertion",
    )
    template = replace_once(
        template,
        "static bool trace_abox_pcm_trigger_queue_enabled(void) { return false; }",
        "static bool trace_abox_pcm_trigger_queue_enabled(void)\n"
        "{ return g_duplicate_trace_queue_enabled; }",
        "queue trace switch",
    )
    template = replace_once(
        template,
        "\t(void)sequence; (void)mono_ns; (void)ipc_id; (void)channel;\n"
        "\t(void)message_type; (void)result; (void)attempt; (void)atomic;\n"
        "\t(void)sync;\n",
        "\t(void)sequence; (void)mono_ns; (void)ipc_id; (void)channel;\n"
        "\t(void)message_type; (void)result; (void)attempt; (void)atomic;\n"
        "\t(void)sync;\n"
        "\t++g_duplicate_queue_trace_calls;\n",
        "queue trace counter",
    )
    template = replace_once(
        template,
        "static bool g_lock_initialized;\n",
        "static bool g_lock_initialized;\n\n"
        "static bool duplicate_queue_lock_held(void)\n"
        "{\n"
        "\tint ret;\n\n"
        "\tif (!g_lock_initialized)\n"
        "\t\treturn false;\n"
        "\tret = pthread_mutex_trylock(&g_abox.ipc_queue_lock);\n"
        "\tif (ret == 0) {\n"
        "\t\tpthread_mutex_unlock(&g_abox.ipc_queue_lock);\n"
        "\t\treturn false;\n"
        "\t}\n"
        "\treturn true;\n"
        "}\n",
        "queue-lock observation shim",
    )
    template = replace_once(
        template,
        "\tg_trace_sequence = 0;\n\tabox_pcm_trigger_trace_seq = 0;\n",
        "\tg_trace_sequence = 0;\n\tabox_pcm_trigger_trace_seq = 0;\n"
        "\tg_duplicate_trace_queue_enabled = false;\n"
        "\tg_duplicate_queue_trace_calls = 0;\n"
        "\tg_queue_work_under_lock_calls = 0;\n"
        "\tg_flush_work_under_lock_calls = 0;\n",
        "trace and lock counter reset",
    )

    guard = "#ifndef EXPECT_BASELINE_PM_BUG\n"
    guard_index = template.rfind(guard)
    require(guard_index >= 0, "PM harness main/test suffix not found")
    scenarios = SCENARIOS.read_text()
    require(scenarios.endswith("\n"), "scenario harness lacks final newline")
    return template[:guard_index] + scenarios


def render_harness(pm_test, source_test, template: str,
                   sources: dict[str, bytes]) -> str:
    return pm_test.render_harness(source_test, template, sources)


def compile_and_run(cc: str, level: str, source: str, directory: Path,
                    *, baseline: bool) -> subprocess.CompletedProcess:
    suffix = level[2:]
    source_file = directory / f"audio-ipc-duplicate-rekick-{suffix}.c"
    binary = directory / f"audio-ipc-duplicate-rekick-{suffix}"
    source_file.write_text(source)
    args = [
        *shlex.split(cc), "-std=c11", "-pthread", level,
        "-Wall", "-Wextra", "-Werror", "-Wno-unused-parameter",
        "-Wno-unused-function", "-Wno-sign-compare",
    ]
    if baseline:
        args.append("-DEXPECT_DUPLICATE_REKICK_BUG=1")
    args.extend([str(source_file), "-o", str(binary)])
    compiled = command(args)
    require(not compiled.stdout,
            f"unexpected compiler output: {compiled.stdout}")
    return subprocess.run(
        [str(binary)], check=False, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True, timeout=10,
    )


def apply_patch(root: Path, patch: Path, label: str) -> None:
    command(["git", "-C", str(root), "apply", "--check", str(patch)])
    command(["git", "-C", str(root), "apply", str(patch)])
    print(f"PASS: ordinary git apply --check and apply: {label}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-tree", type=Path, default=None,
                        help="verified derived kernel tree; default uses capped public pinned fixtures")
    parser.add_argument("--cc", default=os.environ.get("CC", "cc"))
    args = parser.parse_args()

    spec = importlib.util.spec_from_file_location("audio_ipc_worker_pm_test", PM_TEST)
    require(spec is not None and spec.loader is not None,
            "cannot load existing pinned worker-PM test helpers")
    pm_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pm_module)
    error_test, source_test = pm_module.load_source_test()
    try:
        fixtures, identity = source_test.load_sources(args.source_tree)
    except source_test.SourceFixtureUnavailable as error:
        print(f"SKIP: SOURCE_FIXTURE_UNAVAILABLE: {error}")
        return 77

    pm_module.verify_pinned_pm_semantics(fixtures)
    require(HARNESS.is_file() and SCENARIOS.is_file() and PATCH.is_file(),
            "required pinned worker harness, scenarios, or patch is missing")
    template = instrument_template(HARNESS.read_text())

    with tempfile.TemporaryDirectory(prefix="abox-duplicate-rekick-") as temporary:
        fixture_root = Path(temporary)
        for relative, content in fixtures.items():
            target = fixture_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        command(["git", "init", "-q", str(fixture_root)])
        command(["git", "-C", str(fixture_root), "add", *fixtures.keys()])

        apply_patch(fixture_root, error_test.OBSERVATION_PATCH,
                    "observation patch first")
        apply_patch(fixture_root, error_test.ERROR_PATCH,
                    "queue/state linearization patch second")
        apply_patch(fixture_root, pm_module.PM_PATCH,
                    "worker PM error patch third")
        before_sources = {relative: (fixture_root / relative).read_bytes()
                          for relative in fixtures}
        baseline = render_harness(pm_module, source_test, template,
                                  before_sources)

        apply_patch(fixture_root, PATCH,
                    "duplicate pending-work re-kick patch fourth")
        after_sources = {relative: (fixture_root / relative).read_bytes()
                         for relative in fixtures}
        patched = render_harness(pm_module, source_test, template,
                                 after_sources)

        with tempfile.TemporaryDirectory(prefix="abox-duplicate-rekick-c-") as c_temp:
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
                        f"{len(BASELINE_FAILURES)} expected duplicate re-kick assertions failed",
                        f"pre-fix source did not reproduce only duplicate re-kick defects at {level}:\n"
                        f"{before.stdout}")
                print(f"{level}: PASS: composed pre-fix source reproduces only duplicate no-rekick gap")

                after = compile_and_run(args.cc, level, patched, c_root,
                                        baseline=False)
                expected = "PASS: ABOX duplicate re-kick, PM FIFO, queue bounds, and generic paths"
                require(after.returncode == 0 and after.stdout.strip() == expected,
                        f"patched extracted production C failed at {level}:\n{after.stdout}")
                print(f"{level}: {after.stdout.strip()}")

    print(f"PASS: pinned source fixture {identity}")
    print("PASS: four patches composed in order; actual queue, worker, scheduler, FE/BE paths extracted")
    print("PASS: queue_work/flush_work asserted outside ipc_queue_lock; host-only, no device interaction")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
