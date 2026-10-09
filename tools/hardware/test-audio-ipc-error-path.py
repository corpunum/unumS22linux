#!/usr/bin/env python3
"""Exercise pinned ABOX FE/BE trigger queue ownership on a pthread host."""

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
CORE_SOURCE_FILES = {
    "sound/soc/soc-dai.c": "c3d9189234854f90c05a36adf91b86c4c13f4ca7f47dd4ce672cf3b53d18777a",
    "sound/soc/soc-dapm.c": "4347e3150fc1ceb1065bd4f3607c692f14a9e3f8852ea42611d0fe533ac1c7d2",
}
FUNCTION_SPECS = (
    ("/* ACTUAL_QUEUE_EMPTY_FUNCTION */", "sound/soc/samsung/abox/abox.c", "__abox_ipc_queue_empty"),
    ("/* ACTUAL_QUEUE_FULL_FUNCTION */", "sound/soc/samsung/abox/abox.c", "__abox_ipc_queue_full"),
    ("/* ACTUAL_QUEUE_PUT_FUNCTION */", "sound/soc/samsung/abox/abox.c", "abox_ipc_queue_put"),
    ("/* ACTUAL_QUEUE_GET_FUNCTION */", "sound/soc/samsung/abox/abox.c", "abox_ipc_queue_get"),
    ("/* ACTUAL_PROCESS_IPC_FUNCTION */", "sound/soc/samsung/abox/abox.c", "__abox_process_ipc"),
    ("/* ACTUAL_SCHEDULER_FUNCTION */", "sound/soc/samsung/abox/abox.c", "abox_schedule_ipc"),
    ("/* ACTUAL_GENERIC_REQUEST_FUNCTION */", "sound/soc/samsung/abox/abox.c", "abox_request_ipc"),
    ("/* ACTUAL_SPECIALIZED_REQUEST_FUNCTION */", "sound/soc/samsung/abox/abox.c", "abox_request_pcm_trigger_ipc"),
    ("/* ACTUAL_RDMA_REQUEST_FUNCTION */", "sound/soc/samsung/abox/abox_rdma.c", "abox_rdma_request_ipc"),
    ("/* ACTUAL_RDMA_BACKEND_FUNCTION */", "sound/soc/samsung/abox/abox_rdma.c", "abox_rdma_backend"),
    ("/* ACTUAL_TRIGGER_HELPER_FUNCTION */", "sound/soc/samsung/abox/abox_rdma.c", "abox_rdma_trigger_ipc"),
    ("/* ACTUAL_FE_TRIGGER_FUNCTION */", "sound/soc/samsung/abox/abox_rdma.c", "abox_rdma_trigger"),
    ("/* ACTUAL_BE_MUTE_FUNCTION */", "sound/soc/samsung/abox/abox_rdma.c", "abox_rdma_mute_stream"),
    ("/* ACTUAL_SOC_DAI_RET_FUNCTION */", "sound/soc/soc-dai.c", "_soc_dai_ret"),
    ("/* ACTUAL_DIGITAL_MUTE_FUNCTION */", "sound/soc/soc-dai.c", "snd_soc_dai_digital_mute"),
    ("/* ACTUAL_DAPM_LINK_EVENT_FUNCTION */", "sound/soc/soc-dapm.c", "snd_soc_dai_link_event"),
)
BASELINE_FAILURES = (
    "FAIL: failed START preserves accepted state",
    "FAIL: failed START remains explicitly retryable",
    "FAIL: failed STOP preserves accepted state",
    "FAIL: failed STOP remains explicitly retryable",
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
    module.SOURCE_FILES.update(CORE_SOURCE_FILES)
    return module


def command(args: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess:
    completed = subprocess.run(
        args, cwd=cwd, check=False, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True, timeout=30,
    )
    require(completed.returncode == 0,
            f"command failed ({completed.returncode}): {shlex.join(args)}\n{completed.stdout}")
    return completed


def render_harness(source_test, sources: dict[str, bytes]) -> str:
    harness = HARNESS.read_text()
    abox_source = sources["sound/soc/samsung/abox/abox.c"].decode()
    abox_header = sources["sound/soc/samsung/abox/abox.h"].decode()
    harness = harness.replace(
        "/* ACTUAL_IPC_RETRY_DEFINE */",
        source_test.c_define(abox_source, "IPC_RETRY"),
    )
    harness = harness.replace(
        "/* ACTUAL_QUEUE_SIZE_DEFINE */",
        source_test.c_define(abox_header, "ABOX_IPC_QUEUE_SIZE"),
    )
    has_state_api = False
    for marker, relpath, function_name in FUNCTION_SPECS:
        source = sources[relpath].decode()
        if function_name == "abox_request_pcm_trigger_ipc" and function_name not in source:
            function = ""
        else:
            function = source_test.c_block(source, function_name)
            if function_name == "abox_request_pcm_trigger_ipc":
                has_state_api = True
        require(harness.count(marker) == 1,
                f"harness marker is missing/duplicated: {marker}")
        harness = harness.replace(marker, function)
    if has_state_api:
        harness = harness.replace(
            "/* ACTUAL_QUEUE_STATE_API_DEFINE */",
            "#define HAS_PCM_TRIGGER_QUEUE_STATE 1",
        )
    else:
        marker = "/* ACTUAL_QUEUE_STATE_API_DEFINE */"
        require(harness.count(marker) == 1,
                "queue-state feature marker is missing/duplicated")
        harness = harness.replace(marker, "")
    return harness


def compile_and_run(cc: str, level: str, source: str,
                    directory: Path) -> subprocess.CompletedProcess:
    source_file = directory / f"audio-ipc-error-{level[2:]}.c"
    binary = directory / f"audio-ipc-error-{level[2:]}"
    source_file.write_text(source)
    compile_result = command([
        *shlex.split(cc), "-std=c11", "-pthread", level,
        "-Wall", "-Wextra", "-Werror", "-Wno-unused-parameter",
        "-Wno-unused-function", "-Wno-sign-compare",
        str(source_file), "-o", str(binary),
    ])
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
                        help="exact derived source tree; omitted uses capped public fixtures")
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
    with tempfile.TemporaryDirectory(prefix="abox-trigger-error-") as temporary:
        temp_root = Path(temporary)
        for relpath, content in fixtures.items():
            target = temp_root / relpath
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        command(["git", "init", "-q", str(temp_root)])
        command(["git", "-C", str(temp_root), "add", *fixtures.keys()])

        apply_patch(temp_root, OBSERVATION_PATCH, "observation first")
        baseline_sources = {
            relpath: (temp_root / relpath).read_bytes()
            for relpath in fixtures
        }
        baseline = render_harness(source_test, baseline_sources)

        apply_patch(temp_root, ERROR_PATCH, "queue-linearized error-path repair second")
        patched_sources = {
            relpath: (temp_root / relpath).read_bytes()
            for relpath in fixtures
        }
        patched = render_harness(source_test, patched_sources)

        with tempfile.TemporaryDirectory(prefix="abox-trigger-error-c-") as c_temp:
            c_root = Path(c_temp)
            for level in ("-O0", "-O2"):
                before = compile_and_run(args.cc, level, baseline, c_root)
                failures = tuple(
                    line for line in before.stdout.splitlines()
                    if line.startswith("FAIL: ")
                )
                require(before.returncode == 1 and
                        set(failures) == set(BASELINE_FAILURES) and
                        len(failures) == len(BASELINE_FAILURES) and
                        before.stdout.splitlines()[-1] ==
                        f"{len(BASELINE_FAILURES)} expected baseline assertions failed",
                        f"baseline did not reproduce only the expected queue-state errors at {level}:\n"
                        f"{before.stdout}")
                print(f"{level}: PASS: extracted pre-repair C reproduces rejected START/STOP state")

                after = compile_and_run(args.cc, level, patched, c_root)
                require(after.returncode == 0 and after.stdout.strip() ==
                        "PASS: extracted ABOX FE/BE queue, retry, FIFO, and DAPM paths",
                        f"patched extracted production paths failed at {level}:\n{after.stdout}")
                print(f"{level}: {after.stdout.strip()}")

    print(f"PASS: pinned source fixture {source_identity}")
    print("PASS: pthread-controlled host C; no device interaction")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
