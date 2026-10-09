#!/usr/bin/env python3
"""Host-only executable fake-procfs tests for the TrustZone progress collector."""
import importlib.util
import json
import os
from pathlib import Path
import shlex
import shutil
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


    def execute_collector(self, updates=(), process_cap=None, task_cap=None,
                          instrument_scandir=False):
        environment = os.environ.copy()
        environment["PATH"] = environment.get("PATH", "/usr/bin:/bin")
        command = shlex.split(EVIDENCE.render_remote(self.proc))
        self.assertEqual(command[:2], ["python3", "-c"])
        program = command[2]
        if process_cap is not None:
            program = program.replace("MAX_PROCESSES = 4096",
                                      f"MAX_PROCESSES = {process_cap}", 1)
        if task_cap is not None:
            program = program.replace("MAX_SCAN = 4096", f"MAX_SCAN = {task_cap}", 1)
        if instrument_scandir:
            self.assertIsNotNone(process_cap)
            self.assertIsNotNone(task_cap)
            guard_lines = [
                "_real_scandir = os.scandir",
                '_guard_counts = {"process": 0, "task": 0}',
                "class _GuardedScandir:",
                "    def __init__(self, iterator, kind, limit):",
                "        self.iterator, self.kind, self.limit = iterator, kind, limit",
                "    def __iter__(self):",
                "        return self",
                "    def __next__(self):",
                "        if _guard_counts[self.kind] >= self.limit:",
                '            raise AssertionError("collector requested past visit cap")',
                "        entry = next(self.iterator)",
                "        _guard_counts[self.kind] += 1",
                "        return entry",
                "    def __enter__(self):",
                "        return self",
                "    def __exit__(self, *_args):",
                "        self.iterator.close()",
                "def _guarded_scandir(path):",
                '    kind = "process" if path == PROC_ROOT else "task"',
                f'    limit = {{"process": {process_cap}, "task": {task_cap}}}[kind]',
                "    return _GuardedScandir(_real_scandir(path), kind, limit)",
                "os.scandir = _guarded_scandir",
                "_original_emit_sample = emit_sample",
                "def emit_sample(sample_index):",
                '    _guard_counts["process"] = 0',
                '    _guard_counts["task"] = 0',
                "    return _original_emit_sample(sample_index)",
            ]
            guard = "\n".join(guard_lines)
            marker = "\ndef all_consistent(values):"
            self.assertIn(marker, program)
            program = program.replace(marker, "\n" + guard + marker, 1)
        replacement = []
        for path, value in updates:
            replacement.extend((
                f"with open({str(path)!r}, 'w', encoding='utf-8') as update_file:",
                f"    update_file.write({value!r})",
            ))
        replacement.append("time.sleep(0)")
        self.assertIn("time.sleep(SAMPLE_INTERVAL)", program)
        program = program.replace(
            "time.sleep(SAMPLE_INTERVAL)", "\n".join(replacement), 1)
        result = subprocess.run(
            [*command[:2], program],
            capture_output=True, text=True, timeout=15, env=environment, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertLessEqual(len(result.stdout.encode("utf-8")), EVIDENCE.MAX_CAPTURE_BYTES)
        return json.loads(result.stdout)

    def report_for(self, updates=()):
        return self.execute_collector(updates)

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
        self.assertNotIn("100003", serialized)
        self.assertNotIn("200006", serialized)
        self.assertNotIn("300009", serialized)
        self.assertNotIn("27182818", serialized)
        self.assertNotIn("31415926", serialized)
        self.assertNotIn("tz_worker_handler", serialized)
        self.assertNotIn("1357911", public_report)
        self.assertNotIn("2468022", public_report)
        self.assertNotIn("9753100", public_report)
        self.assertNotIn(BOOT_ID, public_report)
        for private_identity in ("100003", "200006", "300009", "27182818",
                                 "31415926", "16180339", "1357911", "2468022",
                                 "9753100"):
            self.assertNotIn(private_identity, public_report)

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

    def test_remote_uint64_overflow_and_oversized_status_fail_closed(self):
        worker_dir = self.task_paths["worker"]
        (worker_dir / "schedstat").write_text(
            "18446744073709551616 2000 3\n")
        (worker_dir / "status").write_text("x" * 65537)
        capture = self.report_for([(self.proc / "uptime", "102.0 0.0")])
        worker_samples = [task for sample in capture["sampling"]["samples"]
                          for task in sample["tasks"] if task["role"] == "worker"]
        self.assertEqual(len(worker_samples), 2)
        for task in worker_samples:
            self.assertFalse(task["schedstat_available"])
            self.assertFalse(task["status_available"])
            self.assertIsNone(task["run_time_ns"])
            self.assertIsNone(task["voluntary_context_switches"])
        report = EVIDENCE.build_report(capture)
        self.assertFalse(report["capture"]["complete_for_comparison"])
        self.assertEqual(report["assessment"]["scheduler_activity"], "unknown")
        self.assertEqual(report["assessment"]["context_switch_activity"], "unknown")

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

    def test_process_visit_cap_is_reported_incomplete_without_eager_glob(self):
        for offset in range(EVIDENCE.MAX_PROCESSES + 8):
            task_root = self.proc / str(900000 + offset) / "task"
            task_root.mkdir(parents=True)
        capture = self.report_for()
        for sample in capture["sampling"]["samples"]:
            self.assertTrue(sample["enumeration"]["scan_capped"])
            self.assertFalse(sample["enumeration"]["complete"])
        report = EVIDENCE.build_report(capture)
        self.assertFalse(report["capture"]["procfs_enumeration_complete"])
        self.assertEqual(report["assessment"]["scheduler_activity"], "unknown")

    def test_scandir_iterators_never_request_entries_past_process_or_task_budget(self):
        process_limited = self.execute_collector(
            process_cap=4, task_cap=8, instrument_scandir=True)
        self.assertTrue(all(sample["enumeration"]["scan_capped"]
                            for sample in process_limited["sampling"]["samples"]))

        for task_path in self.task_paths.values():
            shutil.rmtree(task_path.parents[1])
        malformed_task = self.proc / "123/task/not-a-tid"
        malformed_task.mkdir(parents=True)
        task_limited = self.execute_collector(
            process_cap=64, task_cap=1, instrument_scandir=True)
        for sample in task_limited["sampling"]["samples"]:
            self.assertTrue(sample["enumeration"]["scan_capped"])
            self.assertEqual(sample["tasks_scanned"], 1)


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
        self.assertIn("python3 -c", calls[0][0])
        self.assertNotIn("adb", calls[0][0])
        self.assertNotIn("/system/bin/sh", calls[0][0])
        shell_check = subprocess.run(
            ["/bin/sh", "-n"], input=calls[0][0], capture_output=True,
            text=True, check=False)
        self.assertEqual(shell_check.returncode, 0, shell_check.stderr)

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
        self.assertEqual(observed["project_root"], EVIDENCE.ARTIFACT_ROOT)

    def test_source_helper_and_recovery_artifact_roots_are_separate(self):
        observed = {}

        class Audio:
            @staticmethod
            def run_trusted_remote(transport, host, command, **kwargs):
                observed.update(transport=transport, host=host, command=command, **kwargs)
                return "fixture-result"

        def load_helper(name, path):
            observed["helper_source"] = path
            return Audio

        with tempfile.TemporaryDirectory(prefix="tz-progress-roots-") as temporary:
            source_root = Path(temporary) / "source-worktree"
            artifact_root = Path(temporary) / "recovery-root"
            with mock.patch.object(EVIDENCE, "ROOT", source_root), \
                    mock.patch.object(EVIDENCE, "ARTIFACT_ROOT", artifact_root), \
                    mock.patch.object(EVIDENCE, "_load_module", side_effect=load_helper):
                EVIDENCE._default_remote("read-only-fixture", 9)

            self.assertNotEqual(source_root, artifact_root)
            self.assertEqual(observed["helper_source"],
                             source_root / "tools/hardware/audio-recovery-reboot-once.py")
            self.assertEqual(observed["project_root"], artifact_root)

    def test_source_and_artifact_roots_may_be_identical(self):
        observed = {}

        class Audio:
            @staticmethod
            def run_trusted_remote(transport, host, command, **kwargs):
                observed.update(transport=transport, host=host, command=command, **kwargs)
                return "fixture-result"

        def load_helper(name, path):
            observed["helper_source"] = path
            return Audio

        with tempfile.TemporaryDirectory(prefix="tz-progress-same-root-") as temporary:
            shared_root = Path(temporary) / "checkout"
            with mock.patch.object(EVIDENCE, "ROOT", shared_root), \
                    mock.patch.object(EVIDENCE, "ARTIFACT_ROOT", shared_root), \
                    mock.patch.object(EVIDENCE, "_load_module", side_effect=load_helper):
                EVIDENCE._default_remote("read-only-fixture", 9)

            self.assertEqual(observed["helper_source"],
                             shared_root / "tools/hardware/audio-recovery-reboot-once.py")
            self.assertEqual(observed["project_root"], shared_root)

    def test_missing_artifact_root_fails_in_wrapper_before_any_ssh_call(self):
        with tempfile.TemporaryDirectory(prefix="tz-progress-route-") as temporary:
            artifact_root = Path(temporary) / "artifact-root"
            tools_dir = artifact_root / "tools"
            tools_dir.mkdir(parents=True)
            wrapper = tools_dir / "s22-ssh"
            # Copy only the reviewed wrapper script; intentionally omit its host-key file.
            shutil.copyfile(EVIDENCE.ROOT / "tools/s22-ssh", wrapper)
            wrapper.chmod(0o755)
            fake_bin = Path(temporary) / "fake-bin"
            fake_bin.mkdir()
            ssh_marker = Path(temporary) / "unexpected-ssh-call"
            fake_ssh = fake_bin / "ssh"
            fake_ssh.write_text(
                "#!/bin/sh\n: > " + shlex.quote(str(ssh_marker)) + "\nexit 91\n")
            fake_ssh.chmod(0o755)
            known_hosts = artifact_root / "evidence/native-linux-20260919/native-v2-known-hosts"
            self.assertFalse(known_hosts.exists())

            audio = EVIDENCE._load_module(
                "s22_tz_progress_missing_root_audio",
                EVIDENCE.ROOT / "tools/hardware/audio-recovery-reboot-once.py")
            deployer = EVIDENCE._load_module(
                "s22_tz_progress_missing_root_deployer",
                EVIDENCE.ROOT / "tools/hardware/deploy-audio-recovery.py")

            def load_source_helper(_name, path):
                self.assertEqual(path,
                                 EVIDENCE.ROOT / "tools/hardware/audio-recovery-reboot-once.py")
                return audio

            with mock.patch.object(EVIDENCE, "ARTIFACT_ROOT", artifact_root), \
                    mock.patch.object(EVIDENCE, "_load_module", side_effect=load_source_helper), \
                    mock.patch.object(deployer, "verified_ssh_path", return_value=str(fake_bin)), \
                    mock.patch.object(audio, "_trusted_deployer", return_value=deployer):
                result = EVIDENCE._default_remote("true", 5)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Missing verified phone host-key file", result.stderr)
            self.assertFalse(ssh_marker.exists())

    def test_remote_collector_has_no_android_or_adb_path(self):
        rendered = EVIDENCE.render_remote()
        self.assertIn("python3 -c", rendered)
        self.assertNotIn("/system/bin/sh", rendered)
        self.assertNotIn("adb", rendered)
        self.assertIn("MAX_PROCESSES = 4096", rendered)
        self.assertIn("MAX_SCAN = 4096", rendered)
        self.assertIn("MAX_TARGETS = 16", rendered)
        self.assertIn("os.scandir(PROC_ROOT)", rendered)
        self.assertIn("os.read(fd", rendered)
        self.assertNotIn("for process_dir in", rendered)


if __name__ == "__main__":
    unittest.main()
