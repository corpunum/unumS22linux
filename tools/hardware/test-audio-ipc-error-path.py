#!/usr/bin/env python3
"""Extract and exercise the pinned ABOX trigger error path on the host.

The test runs the production trigger helper and its PCM trigger caller from
pinned ABOX source. It first reproduces the pre-fix stale-state behavior, then
applies the existing IPC observation patch followed by this repair and checks
the extracted functions at C -O0 and -O2. It never accesses audio hardware.
"""

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
SOURCE_TEST = TOOLS / "test-audio-ipc-observation.py"
HARNESS = TOOLS / "audio-ipc-error-path-harness.c"
OBSERVATION_PATCH = TOOLS / "audio-ipc-observation-fix.patch"
ERROR_PATCH = TOOLS / "audio-ipc-error-path-2026-10-02.patch"
FUNCTION_MARKERS = (
    "/* ACTUAL_TRIGGER_IPC_FUNCTION */",
    "/* ACTUAL_TRIGGER_CALLER_FUNCTION */",
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def load_source_test():
    spec = importlib.util.spec_from_file_location("audio_ipc_observation_test", SOURCE_TEST)
    require(spec is not None and spec.loader is not None,
            "cannot load pinned source-fixture helper")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def command(args: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess:
    completed = subprocess.run(
        args, cwd=cwd, check=False, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True, timeout=30,
    )
    require(completed.returncode == 0,
            f"command failed ({completed.returncode}): {shlex.join(args)}\n{completed.stdout}")
    return completed


def extract_function(source_test, source: str, name: str) -> str:
    return source_test.c_block(source, name)


def render_harness(source_test, rdma_source: str) -> str:
    harness = HARNESS.read_text()
    for marker, function_name in zip(
        FUNCTION_MARKERS,
        ("abox_rdma_trigger_ipc", "abox_rdma_trigger"),
        strict=True,
    ):
        function = extract_function(source_test, rdma_source, function_name)
        require(harness.count(marker) == 1, f"harness marker is missing/duplicated: {marker}")
        harness = harness.replace(marker, function)
    return harness


def compile_and_run(cc: str, level: str, source: str, directory: Path) -> subprocess.CompletedProcess:
    source_file = directory / f"audio-ipc-error-{level[2:]}.c"
    binary = directory / f"audio-ipc-error-{level[2:]}"
    source_file.write_text(source)
    compile_result = command([
        *shlex.split(cc), "-std=c11", level, "-Wall", "-Wextra", "-Werror",
        "-Wno-unused-parameter", "-Wno-unused-function", str(source_file), "-o", str(binary),
    ])
    require(not compile_result.stdout, f"unexpected compiler output: {compile_result.stdout}")
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
                        help="exact derived ABOX source tree; omitted uses capped public fixtures")
    parser.add_argument("--cc", default=os.environ.get("CC", "cc"))
    args = parser.parse_args()

    source_test = load_source_test()
    try:
        fixtures, source_identity = source_test.load_sources(args.source_tree)
    except source_test.SourceFixtureUnavailable as error:
        print(f"SKIP: SOURCE_FIXTURE_UNAVAILABLE: {error}")
        return 77

    require(HARNESS.is_file() and OBSERVATION_PATCH.is_file() and ERROR_PATCH.is_file(),
            "required harness or ordered patch is missing")
    baseline_rdma = fixtures["sound/soc/samsung/abox/abox_rdma.c"].decode()
    before = render_harness(source_test, baseline_rdma)

    with tempfile.TemporaryDirectory(prefix="abox-trigger-error-") as temporary:
        temp_root = Path(temporary)
        for relpath, content in fixtures.items():
            target = temp_root / relpath
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        command(["git", "init", "-q", str(temp_root)])
        command(["git", "-C", str(temp_root), "add", *fixtures.keys()])

        apply_patch(temp_root, OBSERVATION_PATCH, "observation first")
        apply_patch(temp_root, ERROR_PATCH, "error-path repair second")
        patched_rdma = (temp_root / "sound/soc/samsung/abox/abox_rdma.c").read_text()
        after = render_harness(source_test, patched_rdma)

        with tempfile.TemporaryDirectory(prefix="abox-trigger-error-c-") as c_temp:
            c_root = Path(c_temp)
            for level in ("-O0", "-O2"):
                baseline = compile_and_run(args.cc, level, before, c_root)
                required_failures = (
                    "FAIL: failed START preserves the last accepted state",
                    "FAIL: failed START did not suppress the later request",
                    "FAIL: retry still requests START",
                    "FAIL: failed STOP preserves the last accepted state",
                    "FAIL: failed STOP did not suppress the later request",
                )
                baseline_failures = tuple(
                    line for line in baseline.stdout.splitlines()
                    if line.startswith("FAIL: ")
                )
                require(baseline.returncode == 1 and
                        set(baseline_failures) == set(required_failures) and
                        len(baseline_failures) == len(required_failures) and
                        baseline.stdout.splitlines()[-1] ==
                        f"{len(required_failures)} host assertions failed",
                        f"baseline did not reproduce only the expected stale-state failures at {level}:\n{baseline.stdout}")
                print(f"{level}: PASS: baseline reproduces START/STOP stale-state failures")

                patched = compile_and_run(args.cc, level, after, c_root)
                require(patched.returncode == 0,
                        f"patched extracted production functions failed at {level}:\n{patched.stdout}")
                require(patched.stdout.strip() ==
                        "PASS: extracted trigger helper and caller scenarios",
                        f"unexpected patched harness output at {level}: {patched.stdout}")
                print(f"{level}: {patched.stdout.strip()}")

    print(f"PASS: pinned source fixture {source_identity}")
    print("PASS: five extracted production-path scenarios; no device interaction")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
