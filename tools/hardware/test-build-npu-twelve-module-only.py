#!/usr/bin/env python3
"""Hardware-free regression tests for the pinned NPU module-only wrapper.

The tests import and execute the real collect_plan()/execute_build() code with
temporary paths and controlled subprocess/monitor shims. No Kbuild command is
started, and no path under the real kernel build directory is written.
"""
from __future__ import annotations

from contextlib import ExitStack
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch


WRAPPER_PATH = Path(__file__).with_name("build-npu-twelve-module-only.py").resolve()
SPEC = importlib.util.spec_from_file_location("npu_twelve_wrapper_under_test", WRAPPER_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("could not load the production wrapper")
WRAPPER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = WRAPPER
SPEC.loader.exec_module(WRAPPER)


def require(condition: bool, message: str) -> None:
    # Do not use `assert`: this suite is also run under Python -O.
    if not condition:
        raise RuntimeError(message)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


PASS_RESOURCES = {
    "captured_at_utc": "2030-01-01T00:00:00Z",
    "builds_path": "temporary-fixture",
    "mem_available_bytes": 16 * WRAPPER.GIB,
    "disk_free_bytes": 30 * WRAPPER.GIB,
    "minimum_start_memory_bytes": 12 * WRAPPER.GIB,
    "minimum_start_disk_bytes": 24 * WRAPPER.GIB,
    "memory_gate_met": True,
    "disk_gate_met": True,
    "launch_resource_gate_met": True,
}
FAIL_RESOURCES = {
    **PASS_RESOURCES,
    "mem_available_bytes": 7 * WRAPPER.GIB,
    "memory_gate_met": False,
    "launch_resource_gate_met": False,
}


class ResourceFeed:
    def __init__(self, snapshots: list[dict[str, object]]):
        self.snapshots = snapshots
        self.calls = 0

    def __call__(self) -> dict[str, object]:
        index = min(self.calls, len(self.snapshots) - 1)
        self.calls += 1
        return dict(self.snapshots[index])


class BuildOutcome:
    def __init__(self, *, filter_rc: int = 0, olddefconfig_rc: int = 0,
                 mutate_config: bool = False, module_rc: int = 0,
                 resource_abort: bool = False, system_exit_zero: bool = False,
                 release: str | None = "5.10.260-g4e5c5ad7d950"):
        self.filter_rc = filter_rc
        self.olddefconfig_rc = olddefconfig_rc
        self.mutate_config = mutate_config
        self.module_rc = module_rc
        self.resource_abort = resource_abort
        self.system_exit_zero = system_exit_zero
        self.release = release
        self.popen_calls: list[dict[str, object]] = []
        self.module_calls: list[dict[str, object]] = []


class Fixture:
    CONFIG = b"CONFIG_EXYNOS_NPU=m\nCONFIG_LOCALVERSION=\"-g4e5c5ad7d950\"\n"
    FILTERED = b"fixture filtered Module.symvers\n"

    def __init__(self, root: Path, outcome: BuildOutcome,
                 resources: list[dict[str, object]] | None = None,
                 *, existing_output: bool = False):
        self.root = root
        self.outcome = outcome
        self.builds = root / "builds"
        self.builds.mkdir()
        self.source = root / "source"
        self.source.mkdir()
        self.config_export = root / "input.config"
        self.config_export.write_bytes(self.CONFIG)
        self.baseline_symvers = root / "baseline.symvers"
        self.baseline_symvers.write_bytes(b"fixture baseline\n")
        self.symvers_helper = root / "prepare-symvers.py"
        self.symvers_helper.write_text("# controlled subprocess identity\n", encoding="ascii")
        self.output = self.builds / "module-out"
        self.filtered = self.builds / "filtered.symvers"
        if existing_output:
            self.output.mkdir()
            (self.output / "owner-sentinel").write_bytes(b"preserve me\n")
        self.source_info = {
            "worktree": str(self.source),
            "head": "fixture-source-head",
            "tree": "fixture-source-tree",
            "parent": "fixture-source-parent",
            "clean": True,
            "changed_source_files": ["drivers/vision/npu/core/npu-log.c"],
        }
        self.toolchain = {
            "python_executable": "/controlled/python3",
            "tools": [{"name": "clang", "sha256": "fixture-toolchain"}],
        }
        snapshots = resources or [PASS_RESOURCES]
        self.resources = ResourceFeed(snapshots)

    def plan(self) -> dict[str, object]:
        return {
            "source": self.source_info,
            "wrapper": {"path": str(WRAPPER_PATH), "sha256": "fixture-wrapper"},
            "toolchain": self.toolchain,
        }

    def patches(self) -> ExitStack:
        stack = ExitStack()
        values = (
            ("BUILDS", self.builds),
            ("SOURCE", self.source),
            ("OUTPUT", self.output),
            ("FILTERED_SYMVERS", self.filtered),
            ("BASELINE_SYMVERS", self.baseline_symvers),
            ("CONFIG_EXPORT", self.config_export),
            ("SYMVERS_HELPER", self.symvers_helper),
            ("CONFIG_SHA256", sha256(self.CONFIG)),
            ("FILTERED_SYMVERS_SHA256", sha256(self.FILTERED)),
            ("BUILD_ENV", {"PATH": "/controlled/bin", "LC_ALL": "C",
                            "LOCALVERSION": "", "TMPDIR": "/tmp"}),
            ("resource_snapshot", self.resources),
            ("verify_source", lambda: self.source_info),
        )
        for name, value in values:
            stack.enter_context(patch.object(WRAPPER, name, value))

        outcome = self.outcome
        filtered_path = self.filtered
        config_export = self.config_export

        class ControlledPopen:
            def __init__(inner_self, command, *, cwd, env, stdout, stderr,
                         start_new_session):
                argv = list(command)
                outcome.popen_calls.append({
                    "argv": argv,
                    "cwd": str(cwd),
                    "env": dict(env),
                    "start_new_session": start_new_session,
                })
                require(start_new_session is True,
                        "owned subprocess was not placed in a new session")
                if "--output" in argv:
                    output_index = argv.index("--output") + 1
                    require(Path(argv[output_index]) == filtered_path,
                            "filter subprocess received an unexpected output path")
                    filtered_path.write_bytes(Fixture.FILTERED)
                    inner_self.returncode = outcome.filter_rc
                    stdout.write(b"controlled Symvers filter\n")
                elif argv[-1:] == ["olddefconfig"]:
                    output_argument = next(
                        (item[2:] for item in argv if item.startswith("O=")), None
                    )
                    require(output_argument is not None,
                            "olddefconfig did not receive an explicit output directory")
                    copied_config = Path(output_argument) / ".config"
                    require(copied_config.read_bytes() == config_export.read_bytes(),
                            "olddefconfig began with a config other than the pinned copy")
                    require(not (Path(output_argument) / "include/config/kernel.release").exists(),
                            "test fixture unexpectedly had kernel.release before module prepare")
                    if outcome.mutate_config:
                        copied_config.write_bytes(copied_config.read_bytes() + b"# changed\n")
                    inner_self.returncode = outcome.olddefconfig_rc
                    stdout.write(b"controlled olddefconfig\n")
                else:
                    raise RuntimeError(f"unexpected real subprocess command: {argv!r}")

            def wait(inner_self):
                return inner_self.returncode

        stack.enter_context(patch.object(WRAPPER.subprocess, "Popen", ControlledPopen))
        return stack

    def monitor(self):
        outcome = self.outcome

        class ControlledMonitor:
            _cleanup_results: list[dict[str, object]] = []

            @staticmethod
            def run_monitored_build(command, source, output, env, phase_info):
                argv = list(command)
                outcome.module_calls.append({
                    "argv": argv,
                    "source": str(source),
                    "output": str(output),
                    "env": dict(env),
                    "phase_info": phase_info,
                })
                require(argv[-1:] == ["drivers/vision/npu.ko"],
                        f"module target was not the sole requested target: {argv!r}")
                require("Image" not in argv and "modules" not in argv and "vmlinux" not in argv,
                        "wrapper requested an image/full-modules target")
                require(not (output / "include/config/kernel.release").exists(),
                        "release existed before the simulated module prepare boundary")
                if outcome.system_exit_zero:
                    raise SystemExit(0)
                if outcome.module_rc == 0:
                    if outcome.release is not None:
                        release_path = output / "include/config/kernel.release"
                        release_path.parent.mkdir(parents=True, exist_ok=True)
                        release_path.write_text(outcome.release + "\n", encoding="ascii")
                    module_path = output / "drivers/vision/npu.ko"
                    module_path.parent.mkdir(parents=True, exist_ok=True)
                    module_path.write_bytes(b"controlled module fixture\n")
                return outcome.module_rc, outcome.resource_abort

        return ControlledMonitor()


def execute_scenario(outcome: BuildOutcome,
                     resources: list[dict[str, object]] | None = None,
                     *, existing_output: bool = False) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="npu12-orchestration-") as temporary:
        fixture = Fixture(Path(temporary), outcome, resources,
                          existing_output=existing_output)
        with fixture.patches():
            try:
                code = WRAPPER.execute_build(fixture.plan(), fixture.monitor())
                error = None
            except BaseException as caught:
                code = None
                error = caught
            output_exists = fixture.output.exists()
            sentinel = (fixture.output / "owner-sentinel").read_bytes() \
                if (fixture.output / "owner-sentinel").exists() else None
            phase_records = []
            phase_path = fixture.output / "module-only-phase.jsonl"
            if phase_path.is_file():
                phase_records = [json.loads(line) for line in phase_path.read_text().splitlines()]
            build_phase_path = fixture.output / "build-phase.json"
            build_phase = json.loads(build_phase_path.read_text()) \
                if build_phase_path.is_file() else None
            return {
                "code": code,
                "error": error,
                "output_exists": output_exists,
                "sentinel": sentinel,
                "popen_calls": list(outcome.popen_calls),
                "module_calls": list(outcome.module_calls),
                "phase_records": phase_records,
                "build_phase": build_phase,
            }


def test_collect_plan_returns_verified_toolchain() -> None:
    toolchain = {"python_executable": "/fixture/python", "tools": ["clang"]}
    monitor = type("MonitorPin", (), {
        "MIN_REMAINING_MEM": 8 * WRAPPER.GIB,
        "MIN_REMAINING_DISK": 16 * WRAPPER.GIB,
        "CLEANUP_STAGE_TIMEOUTS": (30.0, 15.0, 5.0),
    })()
    with ExitStack() as stack:
        stack.enter_context(patch.object(WRAPPER, "verify_configuration", lambda: None))
        stack.enter_context(patch.object(WRAPPER, "verify_source", lambda: {"head": "fixture"}))
        stack.enter_context(patch.object(
            WRAPPER, "verify_expected_hash", lambda path, expected, label, **kwargs: expected
        ))
        stack.enter_context(patch.object(WRAPPER, "load_pinned_monitor", lambda: monitor))
        stack.enter_context(patch.object(WRAPPER, "verify_toolchain", lambda: toolchain))
        stack.enter_context(patch.object(WRAPPER, "verify_reserved_outputs", lambda: None))
        stack.enter_context(patch.object(WRAPPER, "resource_snapshot", lambda: dict(PASS_RESOURCES)))
        plan = WRAPPER.collect_plan()
    require(plan.get("toolchain") == toolchain,
            "collect_plan dropped its verified toolchain mapping")


def test_successful_path_is_module_only_and_delays_release_check() -> None:
    result = execute_scenario(BuildOutcome())
    require(result["code"] == 0, f"controlled successful path failed: {result['error']!r}")
    require(len(result["popen_calls"]) == 2,
            "expected only filter and olddefconfig controlled subprocesses")
    require(len(result["module_calls"]) == 1,
            "expected exactly one monitored module-target call")
    phase = result["module_calls"][0]["phase_info"]
    require(phase.get("toolchain", {}).get("python_executable") == "/controlled/python3",
            "verified toolchain did not reach the module phase record")
    argv = result["module_calls"][0]["argv"]
    require(argv[-1] == "drivers/vision/npu.ko",
            "successful path did not request exactly the NPU module target")
    require(result["build_phase"]["phase"] == "build_finished",
            "successful path did not write a finished phase receipt")
    names = [record["phase"] for record in result["phase_records"]]
    require("kernel_release_verified" in names,
            "successful path did not record the post-module release check")


def test_olddefconfig_failures_stop_before_module() -> None:
    changed = execute_scenario(BuildOutcome(mutate_config=True))
    require(changed["code"] == 2 and not changed["module_calls"],
            "config mismatch did not refuse before module target")
    nonzero = execute_scenario(BuildOutcome(olddefconfig_rc=9))
    require(nonzero["code"] == 2 and not nonzero["module_calls"],
            "nonzero olddefconfig did not refuse before module target")


def test_resource_gates_fail_closed() -> None:
    initial = execute_scenario(BuildOutcome(), [FAIL_RESOURCES])
    require(isinstance(initial["error"], WRAPPER.BuildPreparationError),
            "failed initial resource gate did not raise the wrapper refusal")
    require(not initial["output_exists"] and not initial["module_calls"],
            "failed initial resource gate created output or invoked a target")

    before_build = execute_scenario(BuildOutcome(), [PASS_RESOURCES, FAIL_RESOURCES])
    require(before_build["code"] == 2 and not before_build["module_calls"],
            "failed pre-build resource gate did not refuse module compilation")

    aborted_zero = execute_scenario(BuildOutcome(resource_abort=True))
    require(aborted_zero["code"] == 3,
            "resource_abort accompanied by zero exit was treated as success")
    require(aborted_zero["build_phase"]["phase"] == "resource_abort",
            "resource-abort phase receipt was not written")


def test_zero_system_exit_is_failure() -> None:
    result = execute_scenario(BuildOutcome(system_exit_zero=True))
    require(result["code"] == 2,
            "monitor SystemExit(0) was not converted to wrapper failure")
    require(result["build_phase"]["phase"] == "wrapper_failure",
            "SystemExit(0) failure phase was not recorded")


def test_output_path_is_exclusive() -> None:
    result = execute_scenario(BuildOutcome(), existing_output=True)
    require(isinstance(result["error"], FileExistsError),
            "existing output directory was not rejected exclusively")
    require(result["sentinel"] == b"preserve me\n",
            "existing output contents were changed")
    require(not result["popen_calls"] and not result["module_calls"],
            "existing output refusal launched a child")


def test_kernel_release_must_be_generated_and_pinned_after_module_target() -> None:
    absent = execute_scenario(BuildOutcome(release=None))
    require(absent["code"] == 2 and len(absent["module_calls"]) == 1,
            "missing post-target kernel.release was not rejected at the right boundary")
    require(absent["build_phase"]["phase"] == "wrapper_failure",
            "missing kernel.release did not leave a failure receipt")

    wrong = execute_scenario(BuildOutcome(release="5.10.260-unexpected"))
    require(wrong["code"] == 2 and len(wrong["module_calls"]) == 1,
            "incorrect post-target kernel.release was not rejected")
    require(wrong["build_phase"]["phase"] == "wrapper_failure",
            "incorrect kernel.release did not leave a failure receipt")


def main() -> int:
    cases = (
        ("collect_plan_toolchain", test_collect_plan_returns_verified_toolchain),
        ("successful_module_only_path", test_successful_path_is_module_only_and_delays_release_check),
        ("olddefconfig_fail_closed", test_olddefconfig_failures_stop_before_module),
        ("resource_gates_fail_closed", test_resource_gates_fail_closed),
        ("system_exit_zero_fail_closed", test_zero_system_exit_is_failure),
        ("output_exclusivity", test_output_path_is_exclusive),
        ("kernel_release_boundary", test_kernel_release_must_be_generated_and_pinned_after_module_target),
    )
    completed = []
    for name, test in cases:
        test()
        completed.append(name)
    print(json.dumps({
        "status": "PASS",
        "suite": "npu_twelve_actual_execute_build_orchestration",
        "python_version": sys.version.splitlines()[0],
        "python_optimize_level": sys.flags.optimize,
        "pythonoptimize_env": os.environ.get("PYTHONOPTIMIZE"),
        "cases": completed,
        "real_kbuild_started": False,
        "real_kernel_build_paths_written": False,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
