#!/usr/bin/env python3
"""Focused host-only policy tests for the RECOVERY observer and HCI smoke."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import struct
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
        "bytes": 100663296,
        "reboot_performed": False,
    }
    value.update(changes)
    return value


def snapshot(boot_id="candidate-boot-id", uptime=240, **changes):
    slots, idle = observer.summarize_slots([
        {"id": 0, "is_processing": False, "prompt": "not retained"},
    ])
    value = {
        "pid1": "native-guardian",
        "native_ready": True,
        "persistent_ready": {"ready": True, "status": "ready"},
        "health": {"status": "ok"},
        "slots": slots,
        "assistant_idle": idle,
        "serious_fault": False,
        "uptime_seconds": uptime,
        "boot_id": boot_id,
        "gnu_build_id": observer.EXPECTED_GNU_BUILD_ID,
        # This intentionally is not the GNU build ID or a hard-coded old release.
        "kernel_release": "5.10.260-g4e5c5ad7d950-custom",
        "boot_reset_first_record": "[ 900] / R / INFORM3(12345674) > RECOVERY >",
        "loaded_modules": ["wlan", "cfg80211"],
        # Bluetooth/HCI UART may be built in, so they need not be in /proc/modules.
        "kernel_components": ["btpower", "exynos_tty", "bluetooth", "hci_uart"],
        "hyprland_running": True,
        "pi_process_running": True,
        "tmux_server_running": True,
        "ttyd_running": True,
        "pi_assistant_ready": True,
        "network_state": {
            "ready": True,
            "interfaces": [{"name": "wlan0", "operstate": "up", "carrier": "1"}],
        },
        "power_state": {
            "battery_status": "Full",
            "battery_capacity_percent": 100,
            "battery_temperature_celsius": 27.4,
            "thermal_zone_count": 3,
            "thermal_all_readable": True,
            "thermal_max_temperature_celsius": 39.0,
        },
        "device_tree_model": "Samsung R0S board based on S5E9925",
        "device_tree_compatible": ["samsung,armv8", "samsung,s5e9925"],
        "boot_model": "SM-S901B",
        "boot_hardware": "s5e9925",
        "bootloader_model_match": True,
    }
    value.update(changes)
    return value


def completed_observer(**changes):
    value = {
        "schema": "s22-hci-recovery-observer/v1",
        "status": "completed",
        "target": "recovery",
        "candidate_sha256": observer.EXPECTED_FLASH_SHA256,
        "gnu_build_id": observer.EXPECTED_GNU_BUILD_ID,
        "kernel_release": "5.10.260-g4e5c5ad7d950-custom",
        "actual_mode": "RECOVERY",
        "boot_id": "candidate-boot-id",
        "baseline_boot_id": "baseline-boot-id",
        "reboot_requests": 1,
        "reboot_request_outcome": "UNKNOWN",
        "observed_seconds": observer.OBSERVATION_SECONDS,
        "continuous_uptime_seconds": observer.MIN_UPTIME_SECONDS,
        "readiness": True,
        "assistant_idle": True,
        "no_serious_fault": True,
        "required_modules_ready": True,
        "hci_components_ready": True,
        "hyprland_running": True,
        "pi_assistant_ready": True,
        "network_ready": True,
        "device_target_valid": True,
        "power_ready": True,
        "baseline_power_ready": True,
        "baseline_readiness": True,
        "baseline_assistant_idle": True,
        "baseline_no_serious_fault": True,
        "baseline_modules_ready": True,
        "baseline_hci_components_ready": True,
        "baseline_desktop_ready": True,
        "baseline_pi_process_running": True,
        "baseline_tmux_server_running": True,
        "baseline_ttyd_running": True,
        "postboot_pi_process_running": True,
        "postboot_tmux_server_running": True,
        "postboot_ttyd_running": True,
        "baseline_network_ready": True,
        "baseline_device_target_valid": True,
        "recovery_sha256": observer.EXPECTED_FLASH_SHA256,
        "recovery_sha256_after_boot": observer.EXPECTED_FLASH_SHA256,
    }
    value.update(changes)
    return value


class ObserverPolicyTests(unittest.TestCase):
    def test_default_observer_output_is_private_local_state_not_repo_rootfs(self):
        self.assertEqual(observer.OUT, Path.home() / ".local/state/s22-hci-trial-20260924/observer")
        self.assertNotIn("rootfs", observer.OUT.parts)

    def test_flash_receipt_pins_target_mode_byte_count_and_no_reboot(self):
        observer.validate_flash_receipt(flash_receipt())
        for receipt in (
            flash_receipt(partition_written="boot"),
            flash_receipt(mode="stage"),
            flash_receipt(readback_sha256="0" * 64),
            flash_receipt(bytes=100663295),
            flash_receipt(reboot_performed=True),
        ):
            with self.subTest(receipt=receipt), self.assertRaises(observer.ObserverError):
                observer.validate_flash_receipt(receipt)

    def test_build_id_is_parsed_separately_from_kernel_release(self):
        descriptor = bytes.fromhex(observer.EXPECTED_GNU_BUILD_ID)
        note = (struct.pack("<III", 4, len(descriptor), 3) + b"GNU\0" +
                descriptor + b"\0" * ((-len(descriptor)) % 4))
        self.assertEqual(observer.parse_gnu_build_id(note), observer.EXPECTED_GNU_BUILD_ID)
        state = snapshot()
        observer.validate_snapshot(state, post_reboot=True)
        self.assertNotEqual(state["kernel_release"], state["gnu_build_id"])
        for changes in (
            {"gnu_build_id": "0" * 40},
            {"kernel_release": ""},
            {"boot_reset_first_record": "[ 900] / R / INFORM3(12345674) > NORMAL >"},
        ):
            with self.subTest(changes=changes), self.assertRaises(observer.ObserverError):
                observer.validate_snapshot(snapshot(**changes), post_reboot=True)

    def test_slots_require_explicit_nonempty_idle_booleans_and_redact_payload(self):
        summary, idle = observer.summarize_slots([
            {"is_processing": False, "prompt": "private prompt", "config": {"secret": 1}},
        ])
        self.assertTrue(idle)
        self.assertEqual(summary, {"count": 1, "processing": [False]})
        for payload in ([], None, [{"is_processing": True}], [{"is_processing": "false"}],
                        [{"prompt": "unknown busy state"}]):
            with self.subTest(payload=payload):
                self.assertFalse(observer.summarize_slots(payload)[1])
        self.assertIn("/slots", observer.SNAPSHOT)
        self.assertIn("/health", observer.SNAPSHOT)

    def test_baseline_and_postboot_modules_desktop_network_target_and_power_gates(self):
        state = snapshot()
        observer.validate_snapshot(state, post_reboot=True)
        # Built-in components are taken from /sys/module, not required in /proc/modules.
        for component in ("btpower", "exynos_tty", "bluetooth", "hci_uart"):
            self.assertNotIn(component, state["loaded_modules"])
        cases = (
            {"loaded_modules": ["btpower", "exynos_tty", "cfg80211"]},
            {"kernel_components": ["btpower", "exynos_tty", "bluetooth"]},
            {"hyprland_running": False},
            {"pi_process_running": False},
            {"tmux_server_running": False},
            {"ttyd_running": False},
            {"pi_assistant_ready": False},
            {"network_state": {"ready": False, "interfaces": []}},
            {"network_state": {"ready": True, "interfaces": [
                {"name": "ecm0", "operstate": "up", "carrier": "1"}]}},
            {"device_tree_compatible": ["samsung,armv8", "samsung,other"]},
            {"device_tree_model": "Samsung other device"},
            {"bootloader_model_match": None},
            {"power_state": dict(state["power_state"], battery_status="Discharging")},
            {"power_state": dict(state["power_state"], battery_capacity_percent=59)},
            {"power_state": dict(state["power_state"], battery_temperature_celsius=42)},
            {"power_state": dict(state["power_state"], thermal_all_readable=False)},
            {"power_state": dict(state["power_state"], thermal_max_temperature_celsius=65)},
        )
        for changes in cases:
            with self.subTest(changes=changes), self.assertRaises(observer.ObserverError):
                observer.validate_snapshot(snapshot(**changes), post_reboot=True)

    def test_hci_mode_rejects_wrong_target_mode_hash_or_incomplete_receipts(self):
        self.assertTrue(observer.observer_receipt_valid(completed_observer()))
        for receipt in (
            completed_observer(target="boot"),
            completed_observer(status="running"),
            completed_observer(actual_mode="NORMAL"),
            completed_observer(candidate_sha256="0" * 64),
            completed_observer(recovery_sha256_after_boot="0" * 64),
            completed_observer(observed_seconds=observer.OBSERVATION_SECONDS - 1),
            completed_observer(baseline_power_ready=False),
            completed_observer(baseline_assistant_idle=False),
            completed_observer(reboot_request_outcome="RETRY"),
        ):
            with self.subTest(receipt=receipt):
                self.assertFalse(observer.observer_receipt_valid(receipt))

    def test_candidate_hash_rejects_full_partition_mismatch(self):
        def wrong_hash(command, **kwargs):
            return subprocess.CompletedProcess(command, 0, "0" * 64 + "  /dev/block/by-name/recovery\n", "")

        with self.assertRaisesRegex(observer.ObserverError, "RECOVERY hash"):
            observer.candidate_hash(runner=wrong_hash)

    def test_reboot_disconnect_is_unknown_durable_and_never_retried(self):
        with tempfile.TemporaryDirectory(prefix="s22-observer-once-") as temporary:
            root = Path(temporary)
            marker = root / "reboot-attempted.json"
            result_path = root / "request-result.json"
            calls = []

            def disconnect(command, **kwargs):
                calls.append(command)
                raise subprocess.TimeoutExpired(command, kwargs["timeout"])

            self.assertEqual(observer.request_reboot_once(marker, result_path, runner=disconnect),
                             "UNKNOWN")
            self.assertEqual(len(calls), 1)
            receipt = observer.read_json(result_path)
            self.assertEqual(receipt["outcome"], "UNKNOWN")
            self.assertFalse(receipt["retry_allowed"])
            with self.assertRaises(FileExistsError):
                observer.request_reboot_once(marker, result_path, runner=disconnect)
            self.assertEqual(len(calls), 1)

    def test_reboot_nonzero_transport_result_is_unknown_and_never_retried(self):
        with tempfile.TemporaryDirectory(prefix="s22-observer-reboot-nonzero-") as temporary:
            root = Path(temporary)
            calls = []

            def nonzero(command, **kwargs):
                calls.append(command)
                return subprocess.CompletedProcess(command, 255, "", "")

            marker = root / "reboot-attempted.json"
            result_path = root / "request-result.json"
            self.assertEqual(observer.request_reboot_once(marker, result_path, runner=nonzero),
                             "UNKNOWN")
            result = observer.read_json(result_path)
            self.assertEqual(result["outcome"], "UNKNOWN")
            self.assertFalse(result["retry_allowed"])
            with self.assertRaises(FileExistsError):
                observer.request_reboot_once(marker, result_path, runner=nonzero)
            self.assertEqual(len(calls), 1)

    def test_timeout_continues_reconnect_observation_and_accepts_once(self):
        with tempfile.TemporaryDirectory(prefix="s22-observer-integration-") as temporary:
            root = Path(temporary)
            calls = []
            reboot_calls = []
            reboot_timeouts = []
            usb_snapshots = 0
            tick = [0.0]

            def fake_run(command, **kwargs):
                nonlocal usb_snapshots
                calls.append(command)
                remote = command[-1]
                if remote == "s22-reboot recovery":
                    reboot_calls.append(command)
                    reboot_timeouts.append(kwargs["timeout"])
                    tick[0] += 0.25
                    raise subprocess.TimeoutExpired(command, kwargs["timeout"])
                if remote == "sha256sum /dev/block/by-name/recovery":
                    return subprocess.CompletedProcess(
                        command, 0,
                        observer.EXPECTED_FLASH_SHA256 + "  /dev/block/by-name/recovery\n", "")
                if remote.startswith("python3 -c ") and "addresses=" in remote:
                    return subprocess.CompletedProcess(command, 0, "10.23.4.5\n", "")
                if remote.startswith("python3 -c ") and "device-tree/model" in remote:
                    if command[0].endswith("tools/s22-ssh"):
                        usb_snapshots += 1
                        if usb_snapshots == 1:
                            payload = snapshot("baseline-boot-id", 5000)
                        else:
                            # The device is still rebooting on USB; fallback Wi-Fi then reconnects.
                            return subprocess.CompletedProcess(command, 1, "", "disconnected")
                    else:
                        payload = snapshot("candidate-boot-id", 240 + usb_snapshots)
                    return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")
                self.fail("unexpected mocked command")

            def fake_clock():
                return tick[0]

            def fake_sleep(seconds):
                tick[0] += seconds

            redacted = {"usb_device_count": 1, "interfaces": [], "wifi_devices": [],
                        "tailscale": None}
            with mock.patch.object(observer, "OBSERVATION_SECONDS", 2), \
                    mock.patch.object(observer, "pinned_usb_host_key_alias", return_value="fixture-alias"):
                result = observer.run_reboot_observer(
                    root, "trial", flash_receipt(),
                    observation_seconds=2, sample_interval=1, runner=fake_run,
                    clock=fake_clock, sleep=fake_sleep,
                    enumerate_host=lambda **kwargs: redacted)

            self.assertEqual(len(reboot_calls), 1)
            self.assertLessEqual(reboot_timeouts[0], 2)
            self.assertEqual(result["reboot_request_outcome"], "UNKNOWN")
            self.assertEqual(result["reboot_requests"], 1)
            self.assertEqual(result["status"], "completed")
            self.assertLessEqual(result["observed_seconds"], 2)
            self.assertEqual(result["baseline_recovery_sha256"], observer.EXPECTED_FLASH_SHA256)
            self.assertEqual(result["recovery_sha256_after_boot"], observer.EXPECTED_FLASH_SHA256)
            with mock.patch.object(observer, "OBSERVATION_SECONDS", 2):
                self.assertTrue(observer.observer_receipt_valid(result),
                                json.dumps(result, sort_keys=True))
            self.assertGreaterEqual(result["samples"], 2)
            self.assertEqual(len(list((root / "trial").glob("host-window-*.json"))), result["samples"])
            rendered = json.dumps(result) + "\n".join(
                path.read_text() for path in (root / "trial").glob("*.json"))
            self.assertNotIn("10.23.4.5", rendered)
            self.assertNotIn("fixture-alias", rendered)
            self.assertTrue(any(command[0] == "ssh" for command in calls))

    def test_hci_once_refuses_stale_boot_before_hash_or_socket(self):
        with tempfile.TemporaryDirectory(prefix="s22-observer-hci-stale-") as temporary:
            root = Path(temporary)
            calls = []

            def snapshotter(*args, **kwargs):
                return snapshot("different-boot-id"), "usb"

            def hasher(*args, **kwargs):
                calls.append("hash")
                return observer.EXPECTED_FLASH_SHA256

            with self.assertRaisesRegex(observer.ObserverError, "boot ID changed"):
                observer.run_hci_once(root, "trial", completed_observer(),
                                       route_selector=snapshotter, hasher=hasher)
            self.assertEqual(calls, [])
            self.assertFalse((root / "hci-candidate-socket-attempted.json").exists())

    def test_hci_socket_disconnect_is_unknown_and_cannot_be_retried(self):
        with tempfile.TemporaryDirectory(prefix="s22-observer-hci-once-") as temporary:
            root = Path(temporary)
            socket_calls = []

            def route_selector(*args, **kwargs):
                return snapshot("candidate-boot-id"), "usb"

            def hasher(*args, **kwargs):
                return observer.EXPECTED_FLASH_SHA256

            def disconnect(command, **kwargs):
                socket_calls.append(command)
                raise subprocess.TimeoutExpired(command, kwargs["timeout"])

            with self.assertRaisesRegex(observer.ObserverError, "UNKNOWN"):
                observer.run_hci_once(root, "trial", completed_observer(), runner=disconnect,
                                       route_selector=route_selector, hasher=hasher)
            result = observer.read_json(root / "trial-hci" / "result.json")
            self.assertEqual(result["outcome"], "UNKNOWN")
            self.assertFalse(result["retry_allowed"])
            with self.assertRaises(FileExistsError):
                observer.run_hci_once(root, "trial", completed_observer(), runner=disconnect,
                                       route_selector=route_selector, hasher=hasher)
            self.assertEqual(len(socket_calls), 1)

    def test_hci_nonzero_transport_result_is_unknown_and_cannot_be_retried(self):
        with tempfile.TemporaryDirectory(prefix="s22-observer-hci-nonzero-") as temporary:
            root = Path(temporary)
            socket_calls = []

            def route_selector(*args, **kwargs):
                return snapshot("candidate-boot-id"), "usb"

            def hasher(*args, **kwargs):
                return observer.EXPECTED_FLASH_SHA256

            def nonzero(command, **kwargs):
                socket_calls.append(command)
                return subprocess.CompletedProcess(command, 255, "", "")

            with self.assertRaises(observer.ObserverError):
                observer.run_hci_once(root, "trial", completed_observer(), runner=nonzero,
                                       route_selector=route_selector, hasher=hasher)
            result = observer.read_json(root / "trial-hci" / "result.json")
            self.assertEqual(result["outcome"], "UNKNOWN")
            self.assertFalse(result["retry_allowed"])
            with self.assertRaises(FileExistsError):
                observer.run_hci_once(root, "trial", completed_observer(), runner=nonzero,
                                       route_selector=route_selector, hasher=hasher)
            self.assertEqual(len(socket_calls), 1)

    def test_hci_preflight_carries_selected_network_route_through_hash_and_request(self):
        with tempfile.TemporaryDirectory(prefix="s22-observer-hci-route-") as temporary:
            root = Path(temporary)
            calls = []
            hash_routes = []

            def route_selector(wifi_host, tailscale_host, **kwargs):
                self.assertEqual(wifi_host, "10.23.4.5")
                return snapshot("candidate-boot-id"), "wifi"

            def hasher(transport, host, runner, project_root):
                hash_routes.append((transport, host))
                return observer.EXPECTED_FLASH_SHA256

            def socket_request(command, **kwargs):
                calls.append(command)
                return subprocess.CompletedProcess(command, 0, "", "")

            with mock.patch.object(observer, "pinned_usb_host_key_alias", return_value="ephemeral-alias"):
                result = observer.run_hci_once(
                    root, "trial", completed_observer(), runner=socket_request,
                    route_selector=route_selector, hasher=hasher,
                    wifi_host="10.23.4.5", project_root=observer.ROOT)
            self.assertEqual(result["status"], "completed")
            self.assertEqual(hash_routes, [("wifi", "10.23.4.5")])
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][0], "ssh")
            self.assertIn("root@10.23.4.5", calls[0])
            preflight = observer.read_json(root / "trial-hci" / "preflight.json")
            self.assertEqual(preflight["transport"], "wifi")
            self.assertNotIn("10.23.4.5", json.dumps(preflight))

    def test_hci_mode_requires_observer_receipt_before_any_device_operation(self):
        with tempfile.TemporaryDirectory(prefix="s22-observer-hci-gate-") as temporary:
            root = Path(temporary)
            calls = []

            def never_called(*args, **kwargs):
                calls.append(args)
                return subprocess.CompletedProcess(args, 0, "{}", "")

            with self.assertRaisesRegex(observer.ObserverError, "completed observer"):
                observer.run_hci_once(root, "trial", completed_observer(target="boot"),
                                       runner=never_called)
            self.assertEqual(calls, [])
            self.assertFalse((root / "hci-candidate-socket-attempted.json").exists())

    def test_hci_probe_contract_never_attaches_scans_or_pairs(self):
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("socket.AF_BLUETOOTH", source)
        self.assertIn("socket.SOCK_RAW", source)
        self.assertNotIn("HCI_CHANNEL_USER", source)
        self.assertNotIn("hcitool scan", source)
        self.assertNotIn("bluetoothctl pair", source)

    def test_trusted_root_checks_wrapper_sha_and_uses_pinned_alias(self):
        with tempfile.TemporaryDirectory(prefix="s22-observer-trusted-root-") as temporary:
            root = Path(temporary)
            wrapper = root / "tools" / "s22-ssh"
            deployer = root / "tools" / "hardware" / "deploy-audio-recovery.py"
            known_hosts = root / "evidence" / "native-linux-20260919" / "native-v2-known-hosts"
            wrapper.parent.mkdir(parents=True)
            deployer.parent.mkdir(parents=True)
            known_hosts.parent.mkdir(parents=True)
            wrapper_bytes = (observer.ROOT / "tools" / "s22-ssh").read_bytes()
            digest = hashlib.sha256(wrapper_bytes).hexdigest()
            wrapper.write_bytes(wrapper_bytes)
            deployer.write_text("APPROVED_SSH_WRAPPER_SHA256 = '" + digest + "'\n")
            known_hosts.write_text("10.55.0.2 ssh-ed25519 AAAATESTKEY\n")
            self.assertEqual(hashlib.sha256(observer.validated_ssh_wrapper(root).read_bytes()).hexdigest(), digest)
            command = observer._ssh_transport("wifi", "10.23.4.5", "true", root)
            self.assertIn("HostKeyAlias=10.55.0.2", command)
            self.assertTrue(any(item.startswith("UserKnownHostsFile=") for item in command))

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
