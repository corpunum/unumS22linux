#!/usr/bin/env python3
"""Host-only executable fake-procfs tests for the TrustZone progress collector."""
import importlib.util
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


SOURCE = Path(__file__).with_name("tz-progress-evidence.py")
SPEC = importlib.util.spec_from_file_location("tz_progress_evidence", SOURCE)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("could not load TrustZone evidence module")
EVIDENCE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = EVIDENCE
SPEC.loader.exec_module(EVIDENCE)


BOOT_ID = "12345678-1234-5678-9abc-def012345678"
RELEASE = "5.10.260-g4e5c5ad7d950"
TASKS = {
    "worker": ("tz_worker_threa", 27182818, 1357911),
    "iwlog": ("tz_iwlog_thread", 31415926, 2468022),
    "chub_log": ("chub_log_kthrea", 16180339, 9753100),
}


def stat_line(tid, comm, start_ticks, state="S"):
    after_comm = [state] + ["0"] * 18 + [str(start_ticks)]
    return f"{tid} ({comm}) {' '.join(after_comm)}\n"


class FakeProcfs(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="tz-progress-procfs-")
        self.root = Path(self.temporary.name)
        self.proc = self.root / "proc"
        (self.proc / "sys/kernel/random").mkdir(parents=True)
        (self.proc / "sys/kernel").mkdir(parents=True, exist_ok=True)
        (self.proc / "uptime").write_text("100.0 0.0\n")
        (self.proc / "sys/kernel/osrelease").write_text(RELEASE + "\n")
        (self.proc / "sys/kernel/random/boot_id").write_text(BOOT_ID + "\n")
        for name, value in (
            ("hung_task_timeout_secs", "120"), ("hung_task_warnings", "0"),
            ("hung_task_panic", "0"), ("watchdog_thresh", "10"),
            ("panic_on_warn", "0"), ("tainted", "0"),
        ):
            (self.proc / "sys/kernel" / name).write_text(value + "\n")
        self.task_paths = {}
        for index, (role, (comm, tid, start_ticks)) in enumerate(TASKS.items(), start=1):
            self.task_paths[role] = self.add_task(index * 100003, tid, comm, start_ticks, role)
        self.fake_bin = self.root / "bin"
        self.fake_bin.mkdir()

    def tearDown(self):
        self.temporary.cleanup()

    def add_task(self, pid, tid, comm, start_ticks, role):
        task_dir = self.proc / str(pid) / "task" / str(tid)
        task_dir.mkdir(parents=True)
        (task_dir / "comm").write_text(comm + "\n")
        (task_dir / "stat").write_text(stat_line(tid, comm, start_ticks))
        (task_dir / "status").write_text(
            "Name:\tfixture\nvoluntary_ctxt_switches:\t10\n"
            "nonvoluntary_ctxt_switches:\t2\n")
        (task_dir / "schedstat").write_text("1000 2000 3\n")
        (task_dir / "wchan").write_text("schedule\n")
        if role == "worker":
            frames = (
                "__schedule+0x1/0x2", "schedule+0x1/0x2", "tz_worker_handler+0x1/0x2",
                "smpboot_thread_fn+0x1/0x2", "kthread+0x1/0x2", "ret_from_fork+0x1/0x2",
            )
            (task_dir / "stack").write_text("\n".join(frames) + "\n")
        elif role == "iwlog":
            frames = (
                "__schedule+0x1/0x2", "schedule+0x1/0x2", "tz_iwlog_kthread_handler+0x1/0x2",
                "kthread+0x1/0x2", "ret_from_fork+0x1/0x2",
            )
            (task_dir / "stack").write_text("\n".join(frames) + "\n")
        return task_dir

    def install_fake_sleep(self, updates=()):
        lines = ["#!/bin/sh"]
        for path, value in updates:
            lines.append(
                "printf '%s\\n' " + shlex.quote(value) + " > " + shlex.quote(str(path)))
        sleeper = self.fake_bin / "sleep"
        sleeper.write_text("\n".join(lines) + "\n")
        sleeper.chmod(0o755)

    def execute_collector(self):
        environment = os.environ.copy()
        environment["PATH"] = str(self.fake_bin) + os.pathsep + environment.get("PATH", "/usr/bin:/bin")
        result = subprocess.run(
            ["/bin/sh", "-s"], input=EVIDENCE.render_remote(self.proc),
            capture_output=True, text=True, timeout=15, env=environment, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertLessEqual(len(result.stdout.encode("utf-8")), EVIDENCE.MAX_CAPTURE_BYTES)
        return json.loads(result.stdout)

    def report_for(self, updates=()):
        self.install_fake_sleep(updates)
        return self.execute_collector()

    def test_executable_collector_uses_clipped_names_and_emits_only_sanitized_capture(self):
        updates = [(self.proc / "uptime", "102.0 0.0")]
        for task_dir in self.task_paths.values():
            updates.extend((
                (task_dir / "status", "Name:\tfixture\nvoluntary_ctxt_switches:\t11\n"
                                      "nonvoluntary_ctxt_switches:\t3\n"),
                (task_dir / "schedstat", "2500 3500 4\n"),
            ))
        capture = self.report_for(updates)
        report = EVIDENCE.build_report(capture)
        serialized = json.dumps(capture, sort_keys=True)
        public_report = json.dumps(report, sort_keys=True)

        self.assertEqual(capture["schema"], EVIDENCE.CAPTURE_SCHEMA)
        self.assertTrue(capture["boot"]["boot_identity_consistent"])
        self.assertTrue(capture["boot"]["kernel_release_consistent"])
        self.assertTrue(report["capture"]["complete_for_comparison"])
        self.assertEqual(report["assessment"]["scheduler_activity"], "observed")
        self.assertEqual(report["assessment"]["context_switch_activity"], "observed")
        self.assertEqual(report["assessment"]["tee_request_completion"],
                         "unknown_not_measured_by_procfs_counters")
        self.assertTrue(report["tasks"]["worker"]["expected_wait_stack_in_both_samples"])
        self.assertTrue(report["tasks"]["iwlog"]["expected_wait_stack_in_both_samples"])
        self.assertFalse(report["tasks"]["chub_log"]["source_wait_path_supported"])
        self.assertNotIn(BOOT_ID, serialized)
        self.assertNotIn("27182818", serialized)
        self.assertNotIn("31415926", serialized)
        self.assertNotIn("tz_worker_handler", serialized)
        self.assertNotIn("1357911", public_report)
        self.assertNotIn("2468022", public_report)
        self.assertNotIn("9753100", public_report)
        self.assertNotIn(BOOT_ID, public_report)

    def test_boot_identity_change_blocks_counter_comparison(self):
        new_boot = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        task_dir = self.task_paths["worker"]
        updates = [
            (self.proc / "uptime", "102.0 0.0"),
            (self.proc / "sys/kernel/random/boot_id", new_boot),
            (task_dir / "schedstat", "2500 3500 4\n"),
        ]
        report = EVIDENCE.build_report(self.report_for(updates))
        self.assertFalse(report["capture"]["boot_identity_consistent"])
        self.assertFalse(report["capture"]["complete_for_comparison"])
        self.assertEqual(report["tasks"]["worker"]["scheduler_counters"], "unknown_incomplete_or_inconsistent")
        self.assertEqual(report["assessment"]["scheduler_activity"], "unknown")
        self.assertTrue(report["capture"]["identity_or_counter_uncertainty"])

    def test_task_starttime_change_blocks_counter_comparison(self):
        worker_dir = self.task_paths["worker"]
        _comm, tid, _start = TASKS["worker"]
        updates = [
            (self.proc / "uptime", "102.0 0.0"),
            (worker_dir / "stat", stat_line(tid, TASKS["worker"][0], 1357912)),
            (worker_dir / "schedstat", "2500 3500 4\n"),
        ]
        report = EVIDENCE.build_report(self.report_for(updates))
        self.assertTrue(report["capture"]["boot_identity_consistent"])
        self.assertFalse(report["capture"]["complete_for_comparison"])
        self.assertTrue(report["capture"]["task_identity_uncertainty"])
        self.assertEqual(report["tasks"]["worker"]["same_task_pairs"], 0)
        self.assertEqual(report["tasks"]["worker"]["scheduler_counters"], "unknown_incomplete_or_inconsistent")

    def test_missing_boot_identity_fails_closed(self):
        (self.proc / "sys/kernel/random/boot_id").unlink()
        capture = self.report_for([(self.proc / "uptime", "102.0 0.0")])
        report = EVIDENCE.build_report(capture)
        self.assertIsNone(report["capture"]["boot_identity_consistent"])
        self.assertFalse(report["capture"]["complete_for_comparison"])
        self.assertEqual(report["assessment"]["scheduler_activity"], "unknown")

    def test_missing_scheduler_counter_keeps_aggregate_unknown(self):
        (self.task_paths["worker"] / "schedstat").unlink()
        capture = self.report_for([(self.proc / "uptime", "102.0 0.0")])
        report = EVIDENCE.build_report(capture)
        self.assertFalse(report["capture"]["complete_for_comparison"])
        self.assertFalse(report["tasks"]["worker"]["counter_coverage_complete"])
        self.assertEqual(report["tasks"]["worker"]["scheduler_counters"],
                         "unknown_incomplete_or_inconsistent")
        self.assertEqual(report["assessment"]["scheduler_activity"], "unknown")

    def test_procfs_read_error_blocks_otherwise_positive_counter_delta(self):
        unreadable = self.proc / "777777/task/777777"
        unreadable.mkdir(parents=True)
        updates = [(self.proc / "uptime", "102.0 0.0"),
                   (self.task_paths["worker"] / "schedstat", "2500 3500 4\n")]
        report = EVIDENCE.build_report(self.report_for(updates))
        self.assertFalse(report["capture"]["procfs_enumeration_complete"])
        self.assertFalse(report["capture"]["complete_for_comparison"])
        self.assertEqual(report["assessment"]["scheduler_activity"], "unknown")

    def test_target_enumeration_cap_is_reported_as_incomplete(self):
        task_root = self.proc / "999999/task"
        task_root.mkdir(parents=True)
        for offset in range(EVIDENCE.MAX_TARGETS + 1):
            tid = 500000 + offset
            task_dir = task_root / str(tid)
            task_dir.mkdir()
            (task_dir / "comm").write_text("tz_worker_threa\n")
        capture = self.report_for([(self.proc / "uptime", "102.0 0.0")])
        for sample in capture["sampling"]["samples"]:
            self.assertTrue(sample["enumeration"]["target_capped"])
            self.assertFalse(sample["enumeration"]["complete"])
            self.assertEqual(len(sample["tasks"]), EVIDENCE.MAX_TARGETS)
        report = EVIDENCE.build_report(capture)
        self.assertFalse(report["capture"]["procfs_enumeration_complete"])
        self.assertEqual(report["assessment"]["scheduler_activity"], "unknown")


class CaptureValidation(unittest.TestCase):
    def test_rejects_oversized_or_malformed_task_bounds(self):
        with tempfile.TemporaryDirectory(prefix="tz-progress-input-") as temporary:
            path = Path(temporary) / "capture.json"
            path.write_bytes(b" " * (EVIDENCE.MAX_CAPTURE_BYTES + 1))
            with self.assertRaises(EVIDENCE.EvidenceError):
                EVIDENCE.load_capture(path)

        self.assertFalse(EVIDENCE._is_uint(True))
        self.assertFalse(EVIDENCE._is_uint(EVIDENCE._UINT64_MAX + 1))

    def test_capture_command_uses_the_native_shell_and_injected_usb_route(self):
        calls = []

        def remote(command, timeout):
            calls.append((command, timeout))
            return subprocess.CompletedProcess(["ssh"], 0, '{"schema":"fixture"}\n', "")

        value = EVIDENCE.capture_remote(remote=remote)
        self.assertEqual(value, {"schema": "fixture"})
        self.assertEqual(calls[0][1], 15)
        self.assertIn("sh -c", calls[0][0])
        self.assertNotIn("adb", calls[0][0])
        self.assertNotIn("/system/bin/sh", calls[0][0])

    def test_default_remote_delegates_only_to_the_existing_sealed_usb_helper(self):
        observed = {}

        class Audio:
            @staticmethod
            def run_trusted_remote(transport, host, command, **kwargs):
                observed.update(transport=transport, host=host, command=command, **kwargs)
                return "fixture-result"

        with mock.patch.object(EVIDENCE, "_load_module", return_value=Audio):
            result = EVIDENCE._default_remote("read-only-fixture", 9)
        self.assertEqual(result, "fixture-result")
        self.assertEqual(observed["transport"], "usb")
        self.assertIsNone(observed["host"])
        self.assertEqual(observed["command"], "read-only-fixture")
        self.assertEqual(observed["timeout"], 9)
        self.assertEqual(observed["project_root"], EVIDENCE.ROOT)

    def test_remote_collector_has_no_android_or_adb_path(self):
        rendered = EVIDENCE.render_remote()
        self.assertIn("#!/bin/sh", rendered)
        self.assertNotIn("/system/bin/sh", rendered)
        self.assertNotIn("adb", rendered)
        self.assertIn("MAX_SCAN=4096", rendered)
        self.assertIn("MAX_TARGETS=16", rendered)
        self.assertIn("head -c 32769", rendered)


if __name__ == "__main__":
    unittest.main()
