#!/usr/bin/env python3
"""Host-only gates for the one-shot Bluetooth registration adapter."""
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shlex
import struct
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


adapter = load("bt_hci_registration_adapter",
               ROOT / "tools/hardware/run-bt-hci-bridge-once.py")
device_guard = load("s22_device_trial_guard",
                    ROOT / "tools/hardware/device-trial-guard.py")
observer_fixtures = load("bt_hci_observer_fixtures",
                         ROOT / "tools/hardware/test-audio-recovery-observer.py")
observer = observer_fixtures.observer


def synthetic_aarch64_elf():
    data = bytearray(512)
    data[:6] = b"\x7fELF\x02\x01"
    struct.pack_into("<H", data, 18, 183)
    struct.pack_into("<Q", data, 40, 64)
    struct.pack_into("<HH", data, 58, 64, 1)
    struct.pack_into("<I", data, 68, 7)  # SHT_NOTE
    struct.pack_into("<QQ", data, 88, 128, 36)
    build_id = bytes.fromhex("0123456789abcdef0123456789abcdef01234567")
    struct.pack_into("<III", data, 128, 4, len(build_id), 3)
    data[140:144] = b"GNU\0"
    data[144:164] = build_id
    return bytes(data), build_id.hex()


FIXTURE_ARTIFACT, FIXTURE_BUILD_ID = synthetic_aarch64_elf()
FIXTURE_ARTIFACT_SHA256 = hashlib.sha256(FIXTURE_ARTIFACT).hexdigest()
# Test-only pin for the source under fixture validation, never the production map.
TEST_FIXTURE_BUILD_INPUT_SHA256 = {
    "tools/hardware/bt-h4-ibs-bridge.c":
        "32bfebfc96864b6f5150195518fc7da996f62b7b21311d79c9bfea2725f9b1a1",
}


@contextlib.contextmanager
def fixture_artifact_pins():
    with mock.patch.multiple(
            adapter,
            EXPECTED_ARTIFACT_SHA256=FIXTURE_ARTIFACT_SHA256,
            EXPECTED_ARTIFACT_SIZE=len(FIXTURE_ARTIFACT),
            EXPECTED_ARTIFACT_BUILD_ID=FIXTURE_BUILD_ID):
        yield


def fixture_source_tree(root):
    """Copy only reviewed source inputs into an isolated host-test tree."""
    relative_paths = dict.fromkeys((*adapter.BUILD_INPUT_SHA256,
                                   *adapter.REVIEWED_RUNNER_SHA256))
    for relative in relative_paths:
        source = ROOT / relative
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(source.read_bytes())


@contextlib.contextmanager
def fixture_source_manifest(root):
    """Use test-only source pins for the copied tree; restore production pins."""
    fixture_source_tree(root)
    build_inputs = dict(adapter.BUILD_INPUT_SHA256)
    build_inputs.update(TEST_FIXTURE_BUILD_INPUT_SHA256)
    reviewed_runners = dict(adapter.REVIEWED_RUNNER_SHA256)
    with mock.patch.multiple(adapter,
                             BUILD_INPUT_SHA256=build_inputs,
                             REVIEWED_RUNNER_SHA256=reviewed_runners):
        yield


class FakeBoard:
    __file__ = str(ROOT / "tools/hardware/run-bt-board-once.py")
    SOURCE = None
    BINARY = None
    DEST = None
    TRACE_DIR = None
    EXTRA_SOURCES = []
    NOTE = None


class AdapterTests(unittest.TestCase):
    def test_actual_patch_probe_main_metadata_contract(self):
        # Execute the actual pinned included C main, with hardware primitives
        # replaced by host stubs. No private payload/header or phone required.
        source = (ROOT / 'tools/hardware/bt-qca6490-patch-version-probe.c').read_text()
        main = source[source.index('int main(int argc, char **argv)'):]
        stubs = '''#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
static int patch_self_test(void) { return 0; }
static int validate_live_wlan_baseline(void) { return atoi(getenv("CHECK_RESULT")); }
static int parse_rdev(const char *s,unsigned *a,unsigned *b) { return 1; }
static int run_probe(const char *a,const char *b,unsigned c,unsigned d,bool e)
{ puts("UNEXPECTED_HARDWARE_OPERATION"); return 99; }
'''
        board = adapter.load_board()
        with tempfile.TemporaryDirectory(prefix='bt-check-contract-') as temporary:
            path = Path(temporary)
            (path / 'main.c').write_text(stubs + main)
            subprocess.run(['cc','-std=c11','-Wall','-Werror',str(path/'main.c'),
                            '-o',str(path/'probe')], check=True, capture_output=True)
            for status in (0, -16, -100):
                result = subprocess.run([str(path/'probe'),'--check-live-wlan'],
                    env={**os.environ,'CHECK_RESULT':str(status)},capture_output=True,text=True)
                self.assertEqual(result.returncode, int(status != 0))
                self.assertEqual(result.stdout, '')
                self.assertEqual(result.stderr, '')
                board.LIVE_WLAN_CHECK_STDOUT = 'live_wlan_metadata_only=0\n'
                self.assertFalse(board.live_wlan_check_valid(result))
                board.LIVE_WLAN_CHECK_STDOUT = ''
                self.assertEqual(board.live_wlan_check_valid(result), status == 0)
            for result in (subprocess.CompletedProcess([],0,'unexpected',''),
                           subprocess.CompletedProcess([],0,'','warning'),
                           subprocess.CompletedProcess([],255,'','')):
                self.assertFalse(board.live_wlan_check_valid(result))

    def test_consumed_preflight_identity_is_not_accepted(self):
        for name in ('bt-hci-registration-20260926','bt-hci-registration-20260927'):
            with self.subTest(name=name):
                with self.assertRaisesRegex(adapter.GateError, 'exact controller-registration'):
                    adapter.run_trial(name, observer=None, board=None)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="bt-registration-adapter-")
        self.root = Path(self.temp.name)
        self.guard_root = self.root / "host-state"
        self.receipt_root = self.root / "observer"
        self.out_patch = mock.patch.object(observer, "OUT", self.receipt_root)
        self.out_patch.start()
        self.board = FakeBoard()
        self.events = []
        self.receipt_path = observer.observer_receipt_path(
            observer.OUT, observer.TRIAL_ID)
        self.write_receipt(observer_fixtures.completed_observer())

    def tearDown(self):
        self.out_patch.stop()
        self.temp.cleanup()

    def write_receipt(self, value):
        self.receipt_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.receipt_path.parent, 0o700)
        fd = os.open(self.receipt_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream)

    def dependencies(self, **overrides):
        values = {
            "observer": observer,
            "board": self.board,
            "workspace": self.root,
            "trusted_root": self.root,
            "local_validator": lambda: self._local_ok(),
            "transport_validator": lambda: self.events.append("transport"),
            "snapshot_reader": lambda: self.snapshot(),
            "candidate_hasher": lambda: self.hash_candidate(),
            "controller_reader": lambda: self.controllers(),
            "board_invoker": lambda board, name: self.invoke_board(board, name),
            "target_fs_checker": lambda: self.target_fs(),
            "remote_reserver": lambda: self.reserve_remote(),
            "operation_lock_factory": lambda project, trial, kind:
                device_guard.acquire_operation_lock(
                    project, trial, kind, state_root=self.guard_root),
        }
        values.update(overrides)
        return values

    def _local_ok(self):
        self.events.append("local")
        return adapter.EXPECTED_ARTIFACT_SHA256

    def snapshot(self, **changes):
        self.events.append("snapshot")
        return observer_fixtures.snapshot(**changes)

    def hash_candidate(self, value=adapter.EXPECTED_RECOVERY_SHA256):
        self.events.append("candidate_hash")
        return value

    def controllers(self, names=(), boot_id="candidate-boot-id"):
        self.events.append("controllers")
        return {"boot_id": boot_id, "controllers": list(names)}

    def invoke_board(self, board, name, *, kernel_delta=b""):
        self.events.append("board_main")
        self.assertEqual(name, adapter.TRIAL_ID)
        self.assertEqual(board.BINARY, adapter.ARTIFACT)
        self.assertEqual(board.DEST, adapter.DEST)
        self.assertEqual(board.TRACE_DIR, adapter.TRIAL_TRACE_DIR)
        path = adapter.trial_receipt_directory(self.root, name)
        path.mkdir(parents=True, mode=0o700)
        os.chmod(path, 0o700)
        health = {"boot_id": "candidate-boot-id", "pid1": "native-guardian",
                  "profile": "qwen4b", "model": "ok", "temperature": 36.0}
        trace_data = b"host-only syscall trace\n"
        (path / "strace.txt").write_bytes(trace_data)
        os.chmod(path / "strace.txt", 0o600)
        before_kernel = b"[0.500000] retained pre-trial boundary\n"
        kernel_delta = kernel_delta or b"\n"
        (path / "before-kernel.txt").write_bytes(before_kernel)
        (path / "after-kernel.txt").write_bytes(before_kernel + kernel_delta)
        os.chmod(path / "before-kernel.txt", 0o600)
        os.chmod(path / "after-kernel.txt", 0o600)
        (path / "kernel-delta.txt").write_bytes(kernel_delta)
        os.chmod(path / "kernel-delta.txt", 0o600)
        receipt = {
            "same_boot": True, "returncode": 0, "kernel_capture_exit": 0,
            "after_metadata_exit": 0, "after_vote_check": 0,
            "strace_capture_exit": 0,
            "before": health, "after": health,
            "after_metadata": json.dumps({"boot_id": "candidate-boot-id",
                                           "device_fds": [], "independent_usb": True}),
            "after_vote_check_stdout": "",
            "after_vote_check_stderr": "",
            "uart_output": "bridge_registered_hci=0\nbridge_transport_mode=h4-no-ibs\n"
                           "bridge_result=0 commands=1 events=1 ibs_wake_rx=0 ibs_ack_rx=0 queued=0\n"
                           "pty_cleanup_ioctl_result=0\n",
            "uart_stderr": "baud_probe_result=0\n",
            "strace_capture_path": adapter.TRIAL_DIR + "/" + adapter.TRACE_NAME,
            "strace_capture_bytes": len(trace_data),
            "strace_capture_sha256": hashlib.sha256(trace_data).hexdigest(),
        }
        receipt_path = path / "receipt.json"
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
        os.chmod(receipt_path, 0o600)
        return "host fake only"

    def target_fs(self):
        self.events.append("target_fs")
        return {"device_id": adapter.EXPECTED_S22_DEVICE_ID,
                "free_bytes": adapter.MIN_S22_FREE_BYTES,
                "free_inodes": adapter.MIN_S22_FREE_INODES,
                "trial_absent": True}

    def reserve_remote(self):
        marker = self.guard_root / f"{adapter.TRIAL_ID}.json"
        self.assertEqual(json.loads(marker.read_text())['status'], "pending")
        self.events.append("reserve")
        return {"reserved": True, "device_id": adapter.EXPECTED_S22_DEVICE_ID,
                "mode": 0o700}

    def run_trial(self, **overrides):
        return adapter.run_trial(adapter.TRIAL_ID,
                                 **self.dependencies(**overrides))

    def test_happy_preflight_orders_all_fake_reads_before_board_main(self):
        result = self.run_trial()
        self.assertEqual(result["trial_identity"], adapter.TRIAL_ID)
        self.assertEqual(result["recovery_sha256"], adapter.EXPECTED_RECOVERY_SHA256)
        self.assertEqual(self.events, ["local", "transport", "snapshot",
                                       "candidate_hash", "controllers", "target_fs",
                                       "reserve", "board_main", "snapshot",
                                       "candidate_hash", "controllers"])
        marker = json.loads((self.guard_root / f"{adapter.TRIAL_ID}.json").read_text())
        self.assertEqual(marker["status"], "complete")
        self.assertEqual(marker["outcome"], "success")
        self.assertEqual(marker["receipt_sha256"], hashlib.sha256(
            adapter.trial_receipt_directory(self.root) .joinpath("receipt.json").read_bytes()
        ).hexdigest())

    def test_lost_pretrial_kernel_boundary_keeps_real_guard_unknown(self):
        def overwritten(board, name):
            result = self.invoke_board(board, name)
            path = adapter.trial_receipt_directory(self.root, name)
            (path / "after-kernel.txt").write_bytes(b"[2.000000] later record only\n")
            (path / "kernel-delta.txt").write_bytes(b"[2.000000] later record only\n")
            return result
        with self.assertRaisesRegex(adapter.GateError, "boundary lost"):
            self.run_trial(board_invoker=overwritten)
        marker = json.loads((self.guard_root / f"{adapter.TRIAL_ID}.json").read_text())
        self.assertEqual(marker["status"], "unknown")

    def test_kernel_capture_window_rejects_incomplete_or_filtered_away_evidence(self):
        before = b"[1.000000] boundary\n"
        after = before + b"[2.000000] later record\n"
        self.assertTrue(adapter.validate_kernel_capture_window(
            before, after, b"[2.000000] later record\n")["pre_trial_boundary_retained"])
        for truncated in (b"", after[:-1], b"x" * 4194304 + b"\n"):
            with self.subTest(size=len(truncated)):
                with self.assertRaises(adapter.GateError):
                    adapter.validate_kernel_capture_window(before, truncated, b"\n")
        with self.assertRaisesRegex(adapter.GateError, "does not match"):
            adapter.validate_kernel_capture_window(before, after, b"\n")

    def test_kernel_window_rejects_unordered_severity_records(self):
        before = b"[1.0] baseline\n"
        delta = b"[2.0] benign\n"
        for record in (b"Kernel panic - not syncing\n",
                       b"INFO: task tz:42 blocked for more than 120 seconds\n",
                       b"[bad] malformed\n", b"[1..2] invalid\n",
                       b"[999999999999999] implausible\n"):
            for pre, post in ((before, before + delta + record),
                              (before + record, before + record + delta)):
                with self.subTest(record=record, pre=pre):
                    with self.assertRaises(adapter.GateError):
                        adapter.validate_kernel_capture_window(pre, post, delta)

    def test_bad_completed_observer_receipt_stops_before_live_reads_and_board(self):
        receipt = observer_fixtures.completed_observer(status="incomplete")
        self.write_receipt(receipt)
        with self.assertRaisesRegex(adapter.GateError, "observer receipt is invalid"):
            self.run_trial()
        self.assertEqual(self.events, ["local", "transport"])

    def test_changed_source_or_artifact_provenance_stops_before_phone_reads(self):
        self.events.clear()
        def bad_local_provenance():
            self.events.append("local")
            raise adapter.GateError("prebuilt bridge artifact SHA-256 mismatch")
        with self.assertRaisesRegex(adapter.GateError, "artifact SHA-256 mismatch"):
            self.run_trial(local_validator=bad_local_provenance)
        self.assertEqual(self.events, ["local"])

    def test_live_build_id_mismatch_stops_before_hash_controller_or_board(self):
        with self.assertRaisesRegex(RuntimeError, "GNU build ID"):
            self.run_trial(snapshot_reader=lambda: self.snapshot(
                gnu_build_id="0" * 40))
        self.assertEqual(self.events, ["local", "transport", "snapshot"])

    def test_live_full_recovery_hash_mismatch_stops_before_controller_or_board(self):
        with self.assertRaisesRegex(adapter.GateError, "full RECOVERY hash"):
            self.run_trial(candidate_hasher=lambda: self.hash_candidate("0" * 64))
        self.assertEqual(self.events, ["local", "transport", "snapshot", "candidate_hash"])

    def test_bad_persistent_filesystem_result_stops_before_reservation_or_board(self):
        with self.assertRaisesRegex(adapter.GateError, "persistent trial storage"):
            self.run_trial(target_fs_checker=lambda: {
                "device_id": -1, "free_bytes": 0, "free_inodes": 0,
                "trial_absent": True})
        self.assertNotIn("reserve", self.events)
        self.assertNotIn("board_main", self.events)
        marker = self.guard_root / f"{adapter.TRIAL_ID}.json"
        self.assertFalse(marker.exists())

    def test_false_cleanup_or_trace_evidence_never_terminalizes_marker(self):
        def bad_receipt(board, name):
            self.invoke_board(board, name)
            path = adapter.trial_receipt_directory(self.root, name) / "receipt.json"
            receipt = json.loads(path.read_text(encoding="utf-8"))
            receipt["uart_stderr"] = "stage=power_off_failed\n"
            path.write_text(json.dumps(receipt), encoding="utf-8")
            os.chmod(path, 0o600)
            return "host fake with false cleanup"

        with self.assertRaisesRegex(adapter.GateError, "WLAN vote restoration"):
            self.run_trial(board_invoker=bad_receipt)
        marker = json.loads((self.guard_root / f"{adapter.TRIAL_ID}.json").read_text())
        self.assertEqual(marker["status"], "unknown")
        self.assertNotIn("candidate_hash", self.events[7:])

    def test_new_hung_task_in_kernel_delta_keeps_trial_unknown(self):
        kernel_delta = (
            b"[ 241.0] INFO: task bt_worker:42 blocked for more than 120 seconds.\n"
        )
        with self.assertRaisesRegex(adapter.GateError, "fatal or hung-task indicator"):
            self.run_trial(board_invoker=lambda board, name: self.invoke_board(
                board, name, kernel_delta=kernel_delta))
        marker = json.loads((self.guard_root / f"{adapter.TRIAL_ID}.json").read_text())
        self.assertEqual(marker["status"], "unknown")

    def test_plain_h4_completion_requires_progress_and_exact_cleanup_profile(self):
        adapter.configure_board(self.board)
        self.invoke_board(self.board, adapter.TRIAL_ID)
        path = adapter.trial_receipt_directory(self.root, adapter.TRIAL_ID) / 'receipt.json'
        original = json.loads(path.read_text())
        cases = [
            {'uart_stderr':'baud_probe_result=-1\n'},
            {'uart_stderr':'stage=power_off_and_vote_restored\n'},
            {'uart_stderr':'baud_probe_result=0\nwarning\n'},
            {'uart_output':original['uart_output'].replace('h4-no-ibs','h4-ibs')},
            {'uart_output':original['uart_output'].replace('events=1','events=0')},
            {'uart_output':original['uart_output'].replace('commands=1','commands=0')},
            {'uart_output':original['uart_output'].replace('queued=0','queued=1')},
        ]
        for replacement in cases:
            with self.subTest(replacement=replacement):
                path.write_text(json.dumps({**original, **replacement}))
                with self.assertRaises(adapter.GateError):
                    adapter.validate_completed_trial_receipt(
                        self.root, adapter.TRIAL_ID, require_live=False)
    def test_trace_only_delta_is_distinct_and_does_not_block_completion(self):
        kernel_delta = (
            b"[ 241.0] Call trace:\n"
            b"[ 241.1] schedule+0x70/0x110\n"
        )
        classification = adapter.classify_kernel_delta(kernel_delta)
        self.assertEqual(classification["assessment"], "trace_only")
        self.assertEqual(classification["fatal_indicators"], [])
        self.assertEqual(classification["hung_task_warning_count"], 0)
        self.assertFalse(classification["liveness_unresolved"])

        self.run_trial(board_invoker=lambda board, name: self.invoke_board(
            board, name, kernel_delta=kernel_delta))
        marker = json.loads((self.guard_root / f"{adapter.TRIAL_ID}.json").read_text())
        self.assertEqual(marker["status"], "complete")

    def test_new_postflight_hung_task_keeps_trial_unknown(self):
        hung_snapshot = observer_fixtures.snapshot()
        kernel = hung_snapshot["kernel_log_classification"]
        kernel.update({
            "assessment": "hung_task_warning",
            "hung_task_warning_count": 1,
            "hung_task_names": ["bt_worker"],
            "call_trace_count": 1,
            "liveness_unresolved": True,
            "liveness_review_status": "pinned_wait_stacks_matched_progress_unmeasured",
            "source_wait_stacks": {
                "source_commit": observer.EXPECTED_TZ_SOURCE_COMMIT,
                "warning_count": 1, "matched_count": 1, "unmatched_count": 0,
                "progress_measured": False,
                "matches": [{"task_name": "bt_worker", "wait_path": "fixture"}],
                "unmatched_task_names": [],
            },
        })
        snapshots = [observer_fixtures.snapshot(), hung_snapshot]

        def read_snapshot():
            self.events.append("snapshot")
            return snapshots.pop(0)

        with self.assertRaisesRegex(adapter.GateError,
                                    "kernel log reports fatal/hung indicators"):
            self.run_trial(snapshot_reader=read_snapshot)
        marker = json.loads((self.guard_root / f"{adapter.TRIAL_ID}.json").read_text())
        self.assertEqual(marker["status"], "unknown")

    def test_postflight_candidate_mismatch_keeps_operation_unknown(self):
        returned = iter((adapter.EXPECTED_RECOVERY_SHA256, "0" * 64))

        def candidate_reader():
            return self.hash_candidate(next(returned))

        with self.assertRaisesRegex(adapter.GateError, "post-trial full RECOVERY hash"):
            self.run_trial(candidate_hasher=candidate_reader)
        marker = json.loads((self.guard_root / f"{adapter.TRIAL_ID}.json").read_text())
        self.assertEqual(marker["status"], "unknown")
        self.assertIn("board_main", self.events)

    def test_live_boot_must_match_the_completed_observer_receipt(self):
        self.write_receipt(observer_fixtures.completed_observer(boot_id="different-boot"))
        with self.assertRaisesRegex(adapter.GateError, "boot ID differs"):
            self.run_trial()
        self.assertEqual(self.events, ["local", "transport", "snapshot"])

    def test_native_model_and_wlan_baseline_failures_stop_before_board(self):
        cases = (
            {"native_ready": False},
            {"readiness": dict(observer_fixtures.snapshot()["readiness"],
                                model_api_health=False)},
            {"network_state": {"ready": False, "interfaces": []}},
        )
        for changes in cases:
            with self.subTest(changes=changes):
                self.events.clear()
                with self.assertRaises(RuntimeError):
                    self.run_trial(snapshot_reader=lambda c=changes: self.snapshot(**c))
                self.assertNotIn("board_main", self.events)

    def test_controller_must_be_absent_and_checked_on_the_same_boot(self):
        failures = (
            {"controller_reader": lambda: self.controllers(["hci0"])},
            {"controller_reader": lambda: self.controllers(boot_id="stale-boot")},
        )
        for changes in failures:
            with self.subTest(changes=changes):
                self.events.clear()
                with self.assertRaises(adapter.GateError):
                    self.run_trial(**changes)
                self.assertNotIn("board_main", self.events)

    def test_existing_named_receipt_path_refuses_before_preflight(self):
        path = adapter.trial_receipt_directory(self.root)
        path.parent.mkdir(parents=True)
        path.mkdir()
        with self.assertRaisesRegex(adapter.GateError, "already exists"):
            self.run_trial()
        self.assertEqual(self.events, [])

    def test_wrong_trial_name_refuses_before_any_preflight(self):
        with self.assertRaisesRegex(adapter.GateError, "exact controller-registration"):
            adapter.run_trial("other-trial", **self.dependencies())
        self.assertEqual(self.events, [])

    def test_default_production_provenance_rejects_modified_bridge_before_remote(self):
        production_build_inputs = adapter.BUILD_INPUT_SHA256
        artifact = self.root / "must-not-be-read"

        def validate_production_sources():
            return adapter.validate_local_provenance(
                root=ROOT, artifact_path=artifact, private_headers=())

        with self.assertRaises(adapter.GateError) as failure:
            self.run_trial(local_validator=validate_production_sources,
                           operation_lock_factory=lambda *args: contextlib.nullcontext())
        self.assertEqual(
            str(failure.exception),
            "build source fingerprint changed: tools/hardware/bt-h4-ibs-bridge.c",
        )
        self.assertFalse(artifact.exists())
        self.assertEqual(self.events, [])
        self.assertIs(adapter.BUILD_INPUT_SHA256, production_build_inputs)

    def test_actual_provenance_gate_rejects_fixture_source_tampering_and_artifact_binary(self):
        production_build_inputs = adapter.BUILD_INPUT_SHA256
        production_reviewed_runners = adapter.REVIEWED_RUNNER_SHA256
        production_artifact_sha256 = adapter.EXPECTED_ARTIFACT_SHA256

        with tempfile.TemporaryDirectory(prefix="bt-fixture-source-tamper-") as temporary:
            root = Path(temporary)
            artifact = root / "missing-artifact"
            with fixture_source_manifest(root):
                self.assertIsNot(adapter.BUILD_INPUT_SHA256, production_build_inputs)
                self.assertIsNot(adapter.REVIEWED_RUNNER_SHA256,
                                 production_reviewed_runners)
                bridge = root / "tools/hardware/bt-h4-ibs-bridge.c"
                bridge.write_bytes(bridge.read_bytes() + b"\n/* fixture tamper */\n")
                with self.assertRaisesRegex(
                        adapter.GateError,
                        "source fingerprint changed: tools/hardware/bt-h4-ibs-bridge.c"):
                    adapter.validate_local_provenance(
                        root=root, artifact_path=artifact, private_headers=())
            self.assertIs(adapter.BUILD_INPUT_SHA256, production_build_inputs)
            self.assertIs(adapter.REVIEWED_RUNNER_SHA256,
                          production_reviewed_runners)

        with tempfile.TemporaryDirectory(prefix="bt-fixture-artifact-mismatch-") as temporary:
            root = Path(temporary) / "sources"
            artifact = Path(temporary) / "probe"
            changed = bytearray(FIXTURE_ARTIFACT)
            changed[0] ^= 0xff
            artifact.write_bytes(changed)
            os.chmod(artifact, 0o700)
            with fixture_source_manifest(root):
                with fixture_artifact_pins():
                    with self.assertRaisesRegex(adapter.GateError,
                                                "artifact SHA-256 mismatch"):
                        adapter.validate_local_provenance(
                            root=root, artifact_path=artifact, private_headers=())
                self.assertEqual(adapter.EXPECTED_ARTIFACT_SHA256,
                                 production_artifact_sha256)
            self.assertIs(adapter.BUILD_INPUT_SHA256, production_build_inputs)
            self.assertIs(adapter.REVIEWED_RUNNER_SHA256,
                          production_reviewed_runners)

    def test_replaced_temp_artifact_after_preflight_never_reaches_remote_stage(self):
        with tempfile.TemporaryDirectory(prefix="bt-artifact-toctou-") as temporary:
            provenance_root = Path(temporary) / "provenance-sources"
            workspace = Path(temporary) / "workspace"
            trusted_root = Path(temporary) / "trusted"
            hardware = workspace / "tools/hardware"
            gpu = workspace / "tools/gpu-compat"
            hardware.mkdir(parents=True)
            gpu.mkdir(parents=True)
            (trusted_root / "tools/gpu-compat").mkdir(parents=True)
            board_source = ROOT / "tools/hardware/run-bt-board-once.py"
            board_copy = hardware / board_source.name
            board_copy.write_bytes(board_source.read_bytes())
            source = hardware / "source.c"
            accepted_source = hardware / "accepted.c"
            source.write_text("int source;\n", encoding="utf-8")
            accepted_source.write_text("int accepted;\n", encoding="utf-8")
            (workspace / "tools/hardware/run-bt-version-once.py").write_text(
                "# test fixture\n", encoding="utf-8")
            (gpu / "run-trial.py").write_text("# workflow hash fixture\n",
                                             encoding="utf-8")
            remote_log = Path(temporary) / "remote.log"
            (trusted_root / "tools/gpu-compat/run-trial.py").write_text(
                "import json,pathlib,subprocess,shlex,os\n"
                f"log=pathlib.Path({str(remote_log)!r})\n"
                "def phone_health(): return {'boot_id': 'host-only-fake'}\n"
                "def remote(command,timeout=25):\n"
                "    with log.open('a',encoding='utf-8') as stream: stream.write(command+'\\n')\n"
                "    if command == 'dmesg': return subprocess.CompletedProcess([],0,'','')\n"
                "    if command.startswith('python3 -I -c '):\n"
                "        return subprocess.run(shlex.split(command),capture_output=True,text=True,timeout=timeout,env={**os.environ,'PYTHONOPTIMIZE':'1'})\n"
                "    return subprocess.CompletedProcess([],0,'','')\n",
                encoding="utf-8")

            artifact = Path(temporary) / "probe"
            artifact.write_bytes(FIXTURE_ARTIFACT)
            os.chmod(artifact, 0o700)
            stage_calls = []
            class RemoteCounter:
                def run_approved_ssh_wrapper(self, *args, **kwargs):
                    stage_calls.append((args, kwargs))
                    return subprocess.CompletedProcess([], 0, b"", b"")

            board = adapter.load_board()
            board.__file__ = str(board_copy)
            board.ROOT = workspace
            board.SOURCE = source
            board.ACCEPTED_SOURCE = accepted_source
            board.BINARY = artifact
            board.DEST = str(Path(temporary) / "staging/probe")
            board.EXTRA_SOURCES = []
            board.HOST_TIMEOUT = 3
            board.accepted_runner = lambda: SimpleNamespace(
                METADATA="import json,sys; assert sys.flags.optimize == 0; "
                         "print(json.dumps({'device_fds': [], 'independent_usb': {}}))")
            fake_observer = SimpleNamespace(_trusted_deployer=lambda: RemoteCounter())

            def validate_then_use_later():
                self.events.append("fixture_provenance")
                return adapter.validate_local_provenance(
                    root=provenance_root, artifact_path=artifact, private_headers=())

            def board_invoker(board, name):
                self.assertEqual(name, adapter.TRIAL_ID)
                # Replace only after adapter preflight; actual board.main then
                # rereads BINARY and builds its dynamic staging digest.
                replacement = bytearray(FIXTURE_ARTIFACT)
                replacement[-1] ^= 0xff
                artifact.write_bytes(replacement)
                self.events.append("replacement")
                board.ROOT = workspace
                board.SOURCE = source
                board.ACCEPTED_SOURCE = accepted_source
                board.BINARY = artifact
                board.DEST = str(Path(temporary) / "staging/probe")
                board.EXTRA_SOURCES = []
                self.events.append("board_main")
                return adapter.run_board_main(
                    board, name, fake_observer, trusted_root=trusted_root)

            dependencies = self.dependencies(
                board=board,
                workspace=self.root,
                trusted_root=trusted_root,
                local_validator=validate_then_use_later,
                board_invoker=board_invoker,
            )
            production_build_inputs = adapter.BUILD_INPUT_SHA256
            production_reviewed_runners = adapter.REVIEWED_RUNNER_SHA256
            with fixture_artifact_pins():
                with fixture_source_manifest(provenance_root):
                    with self.assertRaisesRegex(
                            adapter.GateError,
                            "staged bridge artifact SHA-256 mismatch"):
                        adapter.run_trial(adapter.TRIAL_ID, **dependencies)
                self.assertIs(adapter.BUILD_INPUT_SHA256, production_build_inputs)
                self.assertIs(adapter.REVIEWED_RUNNER_SHA256,
                              production_reviewed_runners)

            self.assertEqual(
                self.events,
                ["fixture_provenance", "transport", "snapshot", "candidate_hash",
                 "controllers", "target_fs", "reserve", "replacement", "board_main"],
            )
            self.assertEqual(stage_calls, [])
            remote_commands = remote_log.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(remote_commands), 2)
            self.assertTrue(remote_commands[0].startswith("python3 -I -c "))
            self.assertEqual(remote_commands[1], "dmesg")
            self.assertFalse(any(command.startswith("timeout ")
                                 for command in remote_commands))
            self.assertFalse(Path(board.DEST).exists())

    def test_execute_without_separate_owner_ack_refuses_before_imports(self):
        error = io.StringIO()
        with mock.patch.object(adapter, "load_observer", side_effect=AssertionError), \
                mock.patch.object(adapter, "load_board", side_effect=AssertionError), \
                contextlib.redirect_stderr(error):
            rc = adapter.main([adapter.TRIAL_ID, "--execute"])
        if __debug__:
            self.assertEqual(rc, 1)
            self.assertIn("separate owner authorization", error.getvalue())
        else:
            self.assertEqual(rc, 2)
            self.assertIn("optimized Python is refused", error.getvalue())

    def test_authorized_cli_dispatches_only_exact_name_to_injected_board(self):
        fake = {"observer": observer, "board": self.board,
                "workspace": self.root, "trusted_root": self.root,
                "local_validator": lambda: None,
                "transport_validator": lambda: None,
                "snapshot_reader": lambda: observer_fixtures.snapshot(),
                "candidate_hasher": lambda: adapter.EXPECTED_RECOVERY_SHA256,
                "controller_reader": lambda: {"boot_id": "candidate-boot-id",
                                                "controllers": []},
                "board_invoker": lambda board, name: "host fake only"}
        with mock.patch.object(adapter, "load_observer", return_value=observer), \
                mock.patch.object(adapter, "load_board", return_value=self.board), \
                mock.patch.object(adapter, "run_trial", return_value={"trial_identity": adapter.TRIAL_ID}) as run:
            rc = adapter.main([adapter.TRIAL_ID, "--execute",
                               "--ack-separate-controller-authorization"],
                              dependencies=fake)
        if __debug__:
            self.assertEqual(rc, 0)
            run.assert_called_once()
        else:
            self.assertEqual(rc, 2)
            run.assert_not_called()

    def test_supervisor_timeout_is_reported_as_failure_not_trial_success(self):
        output = io.StringIO()
        error = io.StringIO()
        with mock.patch.object(adapter, "run_trial",
                               side_effect=subprocess.TimeoutExpired("remote", 45)), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
            rc = adapter.main([adapter.TRIAL_ID, "--execute",
                               "--ack-separate-controller-authorization"],
                              dependencies={})
        if __debug__:
            self.assertEqual(rc, 1)
            self.assertEqual(output.getvalue(), "")
            self.assertIn("refusing controller-registration trial", error.getvalue())
        else:
            self.assertEqual(rc, 2)
            self.assertIn("optimized Python is refused", error.getvalue())

    def test_optimized_cli_refuses_before_loading_transport_or_board(self):
        code = "\n".join((
            "import importlib.util,sys",
            f"s=importlib.util.spec_from_file_location('adapter',{str(ROOT / 'tools/hardware/run-bt-hci-bridge-once.py')!r})",
            "m=importlib.util.module_from_spec(s)",
            "s.loader.exec_module(m)",
            "calls=[]",
            "m.load_observer=lambda: calls.append('observer')",
            "m.load_board=lambda: calls.append('board')",
            f"rc=m.main([{adapter.TRIAL_ID!r},'--execute','--ack-separate-controller-authorization'])",
            "print('rc=%d calls=%s'%(rc,calls))",
            "if rc != 2 or calls: raise SystemExit(1)",
        ))
        result = subprocess.run([sys.executable, "-O", "-c", code], cwd=ROOT,
                                capture_output=True, text=True, timeout=3)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("calls=[]", result.stdout)
        self.assertIn("optimized Python is refused", result.stderr)

    def test_actual_board_stage_keeps_asserts_active_under_remote_pythonoptimize(self):
        with tempfile.TemporaryDirectory(prefix="bt-stage-isolated-") as temporary:
            workspace = Path(temporary) / "workspace"
            trusted_root = Path(temporary) / "trusted"
            hardware = workspace / "tools/hardware"
            gpu = workspace / "tools/gpu-compat"
            hardware.mkdir(parents=True)
            gpu.mkdir(parents=True)
            (trusted_root / "tools/gpu-compat").mkdir(parents=True)
            board_source = ROOT / "tools/hardware/run-bt-board-once.py"
            board_copy = hardware / board_source.name
            board_copy.write_bytes(board_source.read_bytes())
            (hardware / "source.c").write_text("int source;\n", encoding="utf-8")
            (hardware / "accepted.c").write_text("int accepted;\n", encoding="utf-8")
            (workspace / "tools/hardware/run-bt-version-once.py").write_text(
                "# test fixture\n", encoding="utf-8")
            (gpu / "run-trial.py").write_text("# workflow hash fixture\n", encoding="utf-8")
            (trusted_root / "tools/gpu-compat/run-trial.py").write_text(
                "import json, subprocess, shlex, os\n"
                "def phone_health(): return {'boot_id': 'host-only-fake'}\n"
                "def remote(command, timeout=25):\n"
                "    if command == 'dmesg':\n"
                "        return subprocess.CompletedProcess([], 0, '', '')\n"
                "    if command.startswith('python3 -I -c '):\n"
                "        return subprocess.run(shlex.split(command),capture_output=True,text=True,timeout=timeout,env={**os.environ,'PYTHONOPTIMIZE':'1'})\n"
                "    return subprocess.CompletedProcess([], 0, '', '')\n",
                encoding="utf-8")

            binary = workspace / "builds/probe"
            binary.parent.mkdir()
            binary.write_bytes(FIXTURE_ARTIFACT)
            destination = workspace / "staging/probe"
            board = adapter.load_board()
            board.__file__ = str(board_copy)
            board.ROOT = workspace
            board.SOURCE = hardware / "source.c"
            board.ACCEPTED_SOURCE = hardware / "accepted.c"
            board.BINARY = binary
            board.DEST = str(destination)
            board.EXTRA_SOURCES = []
            board.HOST_TIMEOUT = 3
            board.accepted_runner = lambda: SimpleNamespace(
                METADATA="import json,sys; assert sys.flags.optimize == 0; "
                         "print(json.dumps({'device_fds': [], 'independent_usb': {}}))")

            class LocalStageDeployer:
                command = None
                process = None

                def run_approved_ssh_wrapper(self, path, command, *, input_data,
                                             timeout, project_root):
                    self.command = command
                    words = shlex.split(command)
                    self.process = subprocess.run(
                        words, input=input_data + b"tamper", capture_output=True,
                        timeout=timeout,
                        env={**os.environ, "PYTHONOPTIMIZE": "1"})
                    return self.process

            deployer = LocalStageDeployer()
            fake_observer = SimpleNamespace(_trusted_deployer=lambda: deployer)
            with fixture_artifact_pins():
                with self.assertRaises(RuntimeError) as failure:
                    adapter.run_board_main(board, adapter.TRIAL_ID, fake_observer,
                                           trusted_root=trusted_root)

            self.assertEqual(shlex.split(deployer.command)[:2], ["python3", "-I"])
            self.assertNotEqual(deployer.process.returncode, 0)
            self.assertIn(b"AssertionError", deployer.process.stderr)
            self.assertIn("AssertionError", str(failure.exception))
            self.assertFalse(destination.exists())

    def test_persistent_storage_scripts_reserve_exclusively_and_never_truncate_trace(self):
        with tempfile.TemporaryDirectory(prefix="bt-remote-storage-") as temporary:
            root = Path(temporary) / "srv/s22"
            root.mkdir(parents=True, mode=0o700)
            os.chmod(root, 0o700)
            params = {
                "root": str(root), "expected_device": root.stat().st_dev,
                "expected_uid": os.geteuid(), "min_free_bytes": 0,
                "min_free_inodes": 0, "namespace": "trial-space",
                "trial_name": "exact-trial",
            }

            def isolated_run(script):
                return subprocess.run(
                    [sys.executable, "-I", "-c", script], capture_output=True,
                    text=True, timeout=5,
                    env={**os.environ, "PYTHONOPTIMIZE": "1"})

            preflight = isolated_run(adapter.render_target_fs_preflight_script(**params))
            self.assertEqual(preflight.returncode, 0, preflight.stderr)
            state = json.loads(preflight.stdout)
            self.assertEqual(state["device_id"], root.stat().st_dev)
            self.assertGreater(state["free_bytes"], 0)
            self.assertGreater(state["free_inodes"], 0)
            trial_dir = root / "trial-space/exact-trial"
            trace = trial_dir / "exact-trial.strace"
            stage = trial_dir / "bt-qca6490-hci-bridge-probe"

            reserve_params = {**params, "trace_name": "exact-trial.strace"}
            reserve = isolated_run(adapter.render_reserve_target_dir_script(**reserve_params))
            self.assertEqual(reserve.returncode, 0, reserve.stderr)
            self.assertEqual(json.loads(reserve.stdout), {
                "reserved": True, "device_id": root.stat().st_dev, "mode": 0o700})
            self.assertTrue(trial_dir.is_dir())
            self.assertFalse(stage.exists())
            self.assertFalse(trace.exists())

            # A later invocation must fail at exclusive mkdir; it cannot reuse
            # the stale directory or truncate an existing trace path.
            trace.write_text("preserve-this-trace\n", encoding="utf-8")
            os.chmod(trace, 0o600)
            before = trace.read_bytes()
            repeated = isolated_run(adapter.render_reserve_target_dir_script(**reserve_params))
            self.assertNotEqual(repeated.returncode, 0)
            self.assertEqual(trace.read_bytes(), before)
            stale_preflight = isolated_run(adapter.render_target_fs_preflight_script(**params))
            self.assertNotEqual(stale_preflight.returncode, 0)

            low_space = isolated_run(adapter.render_target_fs_preflight_script(
                **{**params, "min_free_bytes": 1 << 80}))
            self.assertNotEqual(low_space.returncode, 0)

            link_root = Path(temporary) / "srv/link-root"
            link_root.mkdir(parents=True, mode=0o700)
            os.chmod(link_root, 0o700)
            link_namespace = link_root / "link-space"
            link_namespace.mkdir(mode=0o700)
            os.chmod(link_namespace, 0o700)
            outside = Path(temporary) / "outside"
            outside.mkdir(mode=0o700)
            os.chmod(outside, 0o700)
            (link_namespace / "linked-trial").symlink_to(outside, target_is_directory=True)
            link_params = {
                "root": str(link_root), "expected_device": link_root.stat().st_dev,
                "expected_uid": os.geteuid(), "min_free_bytes": 0,
                "min_free_inodes": 0, "namespace": "link-space",
                "trial_name": "linked-trial",
            }
            link_reserve_params = {**link_params, "trace_name": "linked-trial.strace"}
            linked_preflight = isolated_run(
                adapter.render_target_fs_preflight_script(**link_params))
            self.assertNotEqual(linked_preflight.returncode, 0)
            linked_reservation = isolated_run(
                adapter.render_reserve_target_dir_script(**link_reserve_params))
            self.assertNotEqual(linked_reservation.returncode, 0)

    def test_board_main_routes_trace_to_unique_dir_and_remote_metadata_ignores_optimize(self):
        with tempfile.TemporaryDirectory(prefix="bt-board-trace-") as temporary:
            base = Path(temporary)
            workspace = base / "workspace"
            trusted_root = base / "trusted"
            hardware = workspace / "tools/hardware"
            gpu = workspace / "tools/gpu-compat"
            hardware.mkdir(parents=True)
            gpu.mkdir(parents=True)
            (trusted_root / "tools/gpu-compat").mkdir(parents=True)
            board_source = ROOT / "tools/hardware/run-bt-board-once.py"
            board_copy = hardware / board_source.name
            board_copy.write_bytes(board_source.read_bytes())
            source = hardware / "probe.c"
            accepted_source = hardware / "accepted.c"
            source.write_text("int probe;\n", encoding="utf-8")
            accepted_source.write_text("int accepted;\n", encoding="utf-8")
            (workspace / "tools/hardware/run-bt-version-once.py").write_text(
                "# fixture\n", encoding="utf-8")
            (gpu / "run-trial.py").write_text("# workflow fixture\n", encoding="utf-8")

            trace_dir = base / "reserved-private-trial"
            trace_dir.mkdir(mode=0o700)
            os.chmod(trace_dir, 0o700)
            trace_path = trace_dir / f"{adapter.TRIAL_ID}.strace"
            remote_log = base / "remote-commands.log"
            fake_trial = trusted_root / "tools/gpu-compat/run-trial.py"
            fake_trial.write_text(
                "import json,pathlib,shlex,subprocess,os\n"
                f"log=pathlib.Path({str(remote_log)!r})\n"
                "def phone_health(): return {'boot_id':'host-boot','pid1':'native-guardian','profile':'qwen4b','model':'ok','temperature':36.0}\n"
                "def remote(command,timeout=25):\n"
                "    with log.open('a',encoding='utf-8') as stream: stream.write(command+'\\n')\n"
                "    if command.startswith('python3 -I -c '):\n"
                "        return subprocess.run(shlex.split(command),capture_output=True,text=True,timeout=timeout,env={**os.environ,'PYTHONOPTIMIZE':'1'})\n"
                "    if command == 'dmesg': return subprocess.CompletedProcess([],0,'[ 1.0] baseline\\n','')\n"
                "    if command.endswith('--check-live-wlan'): return subprocess.CompletedProcess([],0,'','')\n"
                "    if command.startswith('timeout '):\n"
                "        words=shlex.split(command); trace=pathlib.Path(words[words.index('-o')+1])\n"
                "        fd=os.open(trace,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)\n"
                "        os.write(fd,b'host-only-strace\\n'); os.fsync(fd); os.close(fd)\n"
                "        return subprocess.CompletedProcess([],0,'bridge_registered_hci=0\\nbridge_result=0 commands=0\\npty_cleanup_ioctl_result=0\\n','stage=power_off_and_vote_restored\\n')\n"
                "    if command.startswith('head -c '):\n"
                "        path=pathlib.Path(shlex.split(command)[-1]); return subprocess.CompletedProcess([],0,path.read_text(),'')\n"
                "    return subprocess.CompletedProcess([],0,'','')\n",
                encoding="utf-8")

            binary = workspace / "builds/probe"
            binary.parent.mkdir()
            binary.write_bytes(FIXTURE_ARTIFACT)
            os.chmod(binary, 0o700)
            stage_destination = base / "remote-stage/probe"
            board = adapter.load_board()
            board.__file__ = str(board_copy)
            board.ROOT = workspace
            board.SOURCE = source
            board.ACCEPTED_SOURCE = accepted_source
            board.BINARY = binary
            board.DEST = str(stage_destination)
            board.TRACE_DIR = trace_dir
            board.LIVE_WLAN_CHECK_STDOUT = ''
            board.EXTRA_SOURCES = []
            board.HOST_TIMEOUT = 5
            board.accepted_runner = lambda: SimpleNamespace(
                METADATA="import json,sys; assert sys.flags.optimize == 0; "
                         "print(json.dumps({'device_fds': [], 'independent_usb': {}}))")

            class HostStageDeployer:
                def __init__(self):
                    self.commands = []

                def run_approved_ssh_wrapper(self, path, command, *, input_data,
                                             timeout, project_root):
                    self.commands.append(command)
                    words = shlex.split(command)
                    prefix = (
                        "import os,pathlib,types; _orig=pathlib.Path.stat; "
                        "pathlib.Path.stat=lambda self,*a,**k: types.SimpleNamespace("
                        "st_uid=0,st_mode=_orig(self,*a,**k).st_mode);\n")
                    process = subprocess.run(
                        [sys.executable, "-I", "-c", prefix + words[3]],
                        input=input_data, capture_output=True, timeout=timeout,
                        env={**os.environ, "PYTHONOPTIMIZE": "1"})
                    return subprocess.CompletedProcess(
                        [], process.returncode, process.stdout, process.stderr)

            deployer = HostStageDeployer()
            fake_observer = SimpleNamespace(_trusted_deployer=lambda: deployer)
            with fixture_artifact_pins():
                # If the runner falls back to python3 -c, remote optimization
                # strips this assertion and falsely accepts the metadata.
                board.accepted_runner = lambda: SimpleNamespace(
                    METADATA="import json,sys; assert sys.flags.optimize == 1; "
                             "print(json.dumps({'boot_id':'host-boot','device_fds': [], 'independent_usb': True}))")
                with self.assertRaisesRegex(RuntimeError, "Read-only metadata preflight failed"):
                    adapter.run_board_main(board, adapter.TRIAL_ID, fake_observer,
                                           trusted_root=trusted_root)
                self.assertFalse(stage_destination.exists())

                board.accepted_runner = lambda: SimpleNamespace(
                    METADATA="import json,sys; assert sys.flags.optimize == 0; "
                             "print(json.dumps({'boot_id':'host-boot','device_fds': [], 'independent_usb': True}))")
                adapter.run_board_main(board, adapter.TRIAL_ID, fake_observer,
                                       trusted_root=trusted_root)

            self.assertTrue(trace_path.is_file())
            self.assertEqual(trace_path.read_text(encoding="utf-8"), "host-only-strace\n")
            commands = remote_log.read_text(encoding="utf-8").splitlines()
            metadata = [command for command in commands
                        if command.startswith("python3 -I -c ")]
            self.assertEqual(len(metadata), 3)
            execution = next(command for command in commands
                             if command.startswith("timeout "))
            words = shlex.split(execution)
            self.assertEqual(words[words.index("-o") + 1], str(trace_path))
            self.assertEqual(len(deployer.commands), 1)
            self.assertTrue(shlex.split(deployer.commands[0])[0:2] == ["python3", "-I"])


if __name__ == "__main__":
    unittest.main()
