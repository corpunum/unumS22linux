#!/usr/bin/env python3
"""Focused host-only policy tests for the recovery observer and HCI smoke."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


SCRIPT = Path(__file__).with_name("audio-recovery-reboot-once.py")
SPEC = importlib.util.spec_from_file_location("audio_recovery_observer", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise ImportError(f"cannot import observer at {SCRIPT}")
observer = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = observer
SPEC.loader.exec_module(observer)


def flash_receipt(**changes):
    value = {
        "mode": "flash",
        "partition_written": "recovery",
        "before_sha256": observer.EXPECTED_BASE_SHA256,
        "readback_sha256": observer.EXPECTED_FLASH_SHA256,
        "reboot_performed": False,
    }
    value.update(changes)
    return value


def completed_observer(**changes):
    value = {
        "schema": "s22-hci-recovery-observer/v1",
        "status": "completed",
        "target": "recovery",
        "candidate_sha256": observer.EXPECTED_FLASH_SHA256,
        "build_id": observer.EXPECTED_BUILD_ID,
        "actual_mode": "RECOVERY",
        "boot_id": "candidate-boot-id",
        "reboot_requests": 1,
        "observed_seconds": observer.OBSERVATION_SECONDS,
        "continuous_uptime_seconds": observer.MIN_UPTIME_SECONDS,
        "readiness": True,
        "assistant_idle": True,
        "no_serious_fault": True,
    }
    value.update(changes)
    return value


class ObserverPolicyTests(unittest.TestCase):
    def test_flash_receipt_requires_recovery_target_candidate_and_flash_mode(self):
        observer.validate_flash_receipt(flash_receipt())
        cases = (
            flash_receipt(partition_written="boot"),
            flash_receipt(mode="stage"),
            flash_receipt(readback_sha256="0" * 64),
            flash_receipt(reboot_performed=True),
        )
        for receipt in cases:
            with self.subTest(receipt=receipt), self.assertRaises(observer.ObserverError):
                observer.validate_flash_receipt(receipt)

    def test_snapshot_requires_candidate_build_and_recovery_mode(self):
        state = {
            "pid1": "native-guardian",
            "native_ready": True,
            "persistent_ready": {"status": "ready"},
            "model": {"status": "ok"},
            "assistant_idle": True,
            "serious_fault": False,
            "uptime_seconds": 180,
            "build_id": observer.EXPECTED_BUILD_ID,
            "boot_reset_first_record": "[ 900] / R / INFORM3(12345674) > RECOVERY >",
        }
        observer.validate_snapshot(state, post_reboot=True)
        for changes in (
            {"boot_reset_first_record": "[ 900] / R / INFORM3(12345674) > NORMAL >"},
            {"build_id": "wrong-build"},
            {"assistant_idle": False},
            {"serious_fault": True},
            {"uptime_seconds": 179},
        ):
            altered = dict(state, **changes)
            with self.subTest(changes=changes), self.assertRaises(observer.ObserverError):
                observer.validate_snapshot(altered, post_reboot=True)

    def test_hci_mode_rejects_wrong_target_mode_and_incomplete_receipts(self):
        valid = completed_observer()
        self.assertTrue(observer.observer_receipt_valid(valid))
        invalid = (
            completed_observer(target="boot"),
            completed_observer(status="running"),
            completed_observer(candidate_sha256="0" * 64),
            completed_observer(observed_seconds=observer.OBSERVATION_SECONDS - 1),
        )
        for receipt in invalid:
            with self.subTest(receipt=receipt):
                self.assertFalse(observer.observer_receipt_valid(receipt))

    def test_disconnect_after_reboot_request_never_duplicates_request(self):
        with tempfile.TemporaryDirectory(prefix="s22-observer-once-") as temporary:
            root = Path(temporary)
            marker = root / "reboot-attempted.json"
            result_path = root / "request-result.json"
            calls = []

            def disconnect(command, **kwargs):
                calls.append(command)
                raise subprocess.TimeoutExpired(command, kwargs["timeout"])

            with self.assertRaisesRegex(observer.ObserverError, "UNKNOWN"):
                observer.request_reboot_once(marker, result_path, runner=disconnect)
            self.assertEqual(len(calls), 1)
            self.assertEqual(observer.read_json(result_path)["outcome"], "UNKNOWN")
            with self.assertRaises(FileExistsError):
                observer.request_reboot_once(marker, result_path, runner=disconnect)
            self.assertEqual(len(calls), 1)

    def test_hci_mode_requires_observer_receipt_before_creating_marker(self):
        with tempfile.TemporaryDirectory(prefix="s22-observer-hci-gate-") as temporary:
            root = Path(temporary)
            calls = []

            def never_called(command, **kwargs):
                calls.append(command)
                return subprocess.CompletedProcess(command, 0, "{}", "")

            with self.assertRaisesRegex(observer.ObserverError, "completed observer"):
                observer.run_hci_once(root, "trial", completed_observer(target="boot"),
                                       runner=never_called)
            self.assertEqual(calls, [])
            self.assertFalse((root / "hci-candidate-socket-attempted.json").exists())

    def test_hci_probe_contract_does_not_attach_scan_or_pair(self):
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("socket.AF_BLUETOOTH", source)
        self.assertIn("socket.SOCK_RAW", source)
        self.assertNotIn("HCI_CHANNEL_USER", source)
        self.assertNotIn("hcitool scan", source)
        self.assertNotIn("bluetoothctl pair", source)

    def test_host_enumeration_redacts_addresses_ssids_and_tailscale_names(self):
        outputs = {
            "lsusb": "Bus 001 Device 002: ID 04e8:6860 Samsung Device\n",
            "ip": "eth0 UP 00:11:22:33:44:55\n",
            "nmcli": "wlan0:wifi:connected\n",
            "tailscale": (
                '{"BackendState":"Running","Self":{"DNSName":"private-phone.ts.net"},'
                '"Peer":{"peer-id":{"Online":true,"DNSName":"other-device.ts.net",'
                '"TailscaleIPs":["100.101.102.103"]}}}'
            ),
        }

        def fake_run(command, **kwargs):
            key = command[0]
            if key == "ip":
                key = "ip"
            elif key == "nmcli":
                key = "nmcli"
            elif key == "tailscale":
                key = "tailscale"
            return subprocess.CompletedProcess(command, 0, outputs[key], "")

        def available(name):
            return "/usr/bin/" + name if name in outputs else None

        with mock.patch.object(observer.shutil, "which", side_effect=available):
            inventory = observer.redacted_host_enumeration(runner=fake_run)
        rendered = str(inventory)
        for private_value in ("00:11:22:33:44:55", "private-phone.ts.net",
                              "other-device.ts.net", "100.101.102.103"):
            self.assertNotIn(private_value, rendered)


if __name__ == "__main__":
    unittest.main(verbosity=2)
