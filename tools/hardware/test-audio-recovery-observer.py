#!/usr/bin/env python3
"""Focused host-only policy tests for the RECOVERY observer and HCI smoke."""
from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import struct
import subprocess
import sys
import tempfile
import urllib.request
import unittest
from unittest import mock


SCRIPT = Path(__file__).with_name("audio-recovery-reboot-once.py")
SPEC = importlib.util.spec_from_file_location("audio_recovery_observer", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise ImportError(f"cannot import observer at {SCRIPT}")
observer = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = observer
SPEC.loader.exec_module(observer)
TRIAL = observer.TRIAL_ID


def flash_receipt(**changes):
    value = {
        "trial_identity": observer.TRIAL_ID,
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
        "persistent_ready": {"ready": True, "mount_ready": True},
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
        "schema": observer.OBSERVER_SCHEMA,
        "trial_identity": observer.TRIAL_ID,
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


def write_snapshot_fixture(root, *, persistent_uuid, mountinfo):
    def write(relative, value):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(value, bytes):
            path.write_bytes(value)
        else:
            path.write_text(value, encoding="utf-8")

    descriptor = bytes.fromhex(observer.EXPECTED_GNU_BUILD_ID)
    notes = (struct.pack("<III", 4, len(descriptor), 3) + b"GNU\0" + descriptor +
             b"\0" * ((-len(descriptor)) % 4))
    write("run/s22-persistent-ready.json", json.dumps({"uuid": persistent_uuid}))
    write("proc/self/mountinfo", mountinfo)
    write("proc/boot_reset", "[ 900] / R / INFORM3(12345674) > RECOVERY >\n")
    write("proc/modules", "wlan 1 0 - Live 0x0\ncfg80211 1 0 - Live 0x0\n")
    write("proc/1/comm", "native-guardian\n")
    for pid, comm in ((2, "Hyprland"), (3, "pi"), (4, "tmux: server"), (5, "ttyd")):
        write(f"proc/{pid}/comm", comm + "\n")
    write("proc/sys/kernel/random/boot_id", "candidate-boot-id\n")
    write("proc/sys/kernel/osrelease", "5.10.260-gfixture-custom\n")
    write("proc/uptime", "5000.00 0.00\n")
    write("proc/device-tree/model", "Samsung R0S board based on S5E9925\0")
    write("proc/device-tree/compatible", b"samsung,armv8\0samsung,s5e9925\0")
    write("proc/cmdline", "androidboot.em.model=SM-S901B androidboot.hardware=s5e9925 "
          "androidboot.bootloader=S901BXXUfixture\n")
    write("sys/kernel/notes", notes)
    for component in observer.REQUIRED_HCI_COMPONENTS:
        (root / "sys/module" / component).mkdir(parents=True, exist_ok=True)
    write("sys/class/net/wlan0/operstate", "up\n")
    write("sys/class/net/wlan0/carrier", "1\n")
    write("sys/class/power_supply/battery/status", "Full\n")
    write("sys/class/power_supply/battery/capacity", "100\n")
    write("sys/class/power_supply/battery/temp", "274\n")
    write("sys/class/thermal/thermal_zone0/temp", "39000\n")
    write("sys/class/thermal/thermal_zone1/temp", "38000\n")
    (root / "run/native-ready").parent.mkdir(parents=True, exist_ok=True)
    (root / "run/native-ready").touch()


def execute_snapshot_fixture(root):
    redirect_paths = f"""
_fixture_root = pathlib.Path({str(root)!r})
_fixture_open = open
def open(name, mode='r', *args, **kwargs):
 path = os.fspath(name)
 fixture_prefix = os.fspath(_fixture_root) + os.sep
 if path.startswith('/') and path != os.fspath(_fixture_root) and not path.startswith(fixture_prefix):
  name = _fixture_root / path.lstrip('/')
 return _fixture_open(name, mode, *args, **kwargs)
def p(name):
 path = os.fspath(name)
 fixture_prefix = os.fspath(_fixture_root) + os.sep
 if path == os.fspath(_fixture_root) or path.startswith(fixture_prefix):
  return pathlib.Path(path)
 if path.startswith('/'):
  return _fixture_root / path.lstrip('/')
 return pathlib.Path(name)
"""
    script = observer.render_snapshot_script().replace(
        "ready=read(", redirect_paths + "\nready=read(", 1)

    class FakeResponse(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *_):
            self.close()

    class FakeOpener:
        def open(self, url, timeout):
            payload = (b'{"status":"ok"}' if url.endswith("/health") else
                       b'[{"is_processing":false}]')
            return FakeResponse(payload)

    def fake_run(command, **kwargs):
        if command == ["dmesg"]:
            return subprocess.CompletedProcess(command, 0, "", "")
        raise AssertionError(f"unexpected snapshot command: {command!r}")

    output = io.StringIO()
    with mock.patch.object(observer.subprocess, "run", side_effect=fake_run), \
            mock.patch.object(urllib.request, "build_opener", return_value=FakeOpener()), \
            contextlib.redirect_stdout(output):
        exec(compile(script, "<observer snapshot fixture>", "exec"), {})
    return json.loads(output.getvalue())


class ObserverPolicyTests(unittest.TestCase):
    def test_default_observer_output_is_private_local_state_not_repo_rootfs(self):
        self.assertEqual(observer.OUT, observer.RIG_HOME /
                         ".local/state/s22-hci-trial-20260924/observer")
        self.assertNotIn("rootfs", observer.OUT.parts)

    def test_trial_marker_root_ignores_caller_overridden_home(self):
        environment = os.environ.copy()
        environment["HOME"] = "/tmp/s22-untrusted-home"
        program = ("import runpy; ns=runpy.run_path("+repr(str(SCRIPT))+
                   ", run_name='s22_observer_fixture'); print(ns['TRIAL_STATE_ROOT'])")
        result = subprocess.run([sys.executable, "-c", program], capture_output=True,
                                text=True, env=environment, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(Path(result.stdout.strip()), observer.TRIAL_STATE_ROOT)

    def test_second_trial_markers_are_namespaced_and_old_markers_are_preserved(self):
        with tempfile.TemporaryDirectory(prefix="s22-observer-marker-isolation-") as temporary:
            root = Path(temporary)
            old_marker = root / observer.REBOOT_MARKER
            old_contents = '{"status":"consumed"}\n'
            old_marker.write_text(old_contents, encoding="utf-8")
            with mock.patch.object(observer, "TRIAL_STATE_ROOT", root):
                marker = observer.trial_marker_path(TRIAL, observer.REBOOT_MARKER)
                self.assertEqual(marker, root / TRIAL / observer.REBOOT_MARKER)
                observer.require_unused_trial_marker(marker)
                marker.parent.mkdir(mode=0o700)
                observer.durable_json(marker, {"trial_identity": TRIAL, "retry_allowed": False})
                self.assertTrue(old_marker.is_file())
                self.assertEqual(old_marker.read_text(encoding="utf-8"), old_contents)
                self.assertEqual(observer.read_json(marker)["trial_identity"], TRIAL)

    def test_flash_and_observer_receipt_paths_are_second_trial_specific(self):
        self.assertEqual(observer.TRIAL_FLASH_RECEIPT.parent.name, TRIAL)
        self.assertEqual(observer.TRIAL_FLASH_RECEIPT.name, "hci-recovery-forward-flash.json")
        self.assertEqual(observer.TRIAL_FLASH_RECEIPT.parent.parent,
                         observer.TRIAL_STATE_ROOT / "receipts")
        self.assertEqual(observer.observer_receipt_path(observer.OUT, TRIAL),
                         observer.OUT / TRIAL / "result.json")
        self.assertEqual(observer.hci_receipt_path(observer.OUT, TRIAL),
                         observer.OUT / (TRIAL + "-hci") / "result.json")

    def test_only_the_explicit_second_trial_name_is_authorized(self):
        self.assertEqual(observer.validate_trial_identity(TRIAL), TRIAL)
        for name in ("hci-candidate-20260924", "another-trial", "../second"):
            with self.subTest(name=name), self.assertRaises(observer.ObserverError):
                observer.validate_trial_identity(name)

        with tempfile.TemporaryDirectory(prefix="s22-observer-unauthorized-name-") as temporary:
            route_calls = []

            def route_selector(*args, **kwargs):
                route_calls.append(args)
                return snapshot(), "usb"

            with mock.patch.object(observer, "TRIAL_STATE_ROOT", Path(temporary) / "state"), \
                    self.assertRaises(observer.ObserverError):
                observer.run_hci_once(Path(temporary), "hci-candidate-20260924",
                                       completed_observer(), route_selector=route_selector)
            self.assertEqual(route_calls, [])

    def test_flash_receipt_pins_target_mode_byte_count_and_no_reboot(self):
        observer.validate_flash_receipt(flash_receipt())
        for receipt in (
            flash_receipt(partition_written="boot"),
            flash_receipt(mode="stage"),
            flash_receipt(readback_sha256="0" * 64),
            flash_receipt(readback_sha256=observer.EXPECTED_BASE_SHA256),
            flash_receipt(before_sha256=observer.EXPECTED_FLASH_SHA256),
            flash_receipt(bytes=100663295),
            flash_receipt(reboot_performed=True),
            flash_receipt(trial_identity="hci-candidate-20260924"),
            {key: value for key, value in flash_receipt().items()
             if key != "trial_identity"},
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
            {"persistent_ready": {"ready": True, "mount_ready": False}},
        )
        for changes in cases:
            with self.subTest(changes=changes), self.assertRaises(observer.ObserverError):
                observer.validate_snapshot(snapshot(**changes), post_reboot=True)

    def test_executed_snapshot_requires_expected_uuid_and_exact_rw_ext4_mount(self):
        def mountinfo(device="259:20", mountpoint="/srv/s22", options="rw,relatime",
                      filesystem="ext4"):
            return (f"36 25 {device} / {mountpoint} {options} shared:1 - "
                    f"{filesystem} /dev/mmcblk0p1 rw,relatime\n")

        cases = (
            ("valid", observer.EXPECTED_PERSISTENT_UUID, mountinfo(), True, True),
            ("wrong UUID", "0" * 36, mountinfo(), False, True),
            ("wrong major:minor", observer.EXPECTED_PERSISTENT_UUID,
             mountinfo(device="259:21"), False, False),
            ("wrong filesystem", observer.EXPECTED_PERSISTENT_UUID,
             mountinfo(filesystem="f2fs"), False, False),
            ("read only", observer.EXPECTED_PERSISTENT_UUID,
             mountinfo(options="ro,relatime"), False, False),
            ("wrong mountpoint", observer.EXPECTED_PERSISTENT_UUID,
             mountinfo(mountpoint="/srv/other"), False, False),
        )
        for label, persistent_uuid, mount_record, expected_ready, expected_mount in cases:
            with self.subTest(mount=label), tempfile.TemporaryDirectory(
                    prefix="s22-observer-snapshot-") as temporary:
                root = Path(temporary)
                write_snapshot_fixture(root, persistent_uuid=persistent_uuid,
                                       mountinfo=mount_record)
                state = execute_snapshot_fixture(root)
                self.assertEqual(state["persistent_ready"], {
                    "ready": expected_ready,
                    "mount_ready": expected_mount,
                })
                if expected_ready:
                    observer.validate_snapshot(state, post_reboot=False)
                    observer.validate_snapshot(state, post_reboot=True)
                    self.assertEqual(state["gnu_build_id"], observer.EXPECTED_GNU_BUILD_ID)
                    self.assertEqual(state["kernel_release"], "5.10.260-gfixture-custom")
                    self.assertEqual(state["loaded_modules"], ["cfg80211", "wlan"])
                    self.assertTrue(set(observer.REQUIRED_HCI_COMPONENTS).issubset(
                        state["kernel_components"]))
                else:
                    with self.assertRaisesRegex(observer.ObserverError,
                                                "native readiness is not established"):
                        observer.validate_snapshot(state, post_reboot=False)

    def test_hci_mode_rejects_wrong_target_mode_hash_or_incomplete_receipts(self):
        self.assertTrue(observer.observer_receipt_valid(completed_observer()))
        for receipt in (
            completed_observer(schema="s22-hci-recovery-observer/v1"),
            completed_observer(trial_identity="hci-candidate-20260924"),
            completed_observer(target="boot"),
            completed_observer(status="running"),
            completed_observer(actual_mode="NORMAL"),
            completed_observer(candidate_sha256="0" * 64),
            completed_observer(candidate_sha256=observer.EXPECTED_BASE_SHA256),
            completed_observer(recovery_sha256=observer.EXPECTED_BASE_SHA256),
            completed_observer(recovery_sha256_after_boot="0" * 64),
            completed_observer(observed_seconds=observer.OBSERVATION_SECONDS - 1),
            completed_observer(baseline_power_ready=False),
            completed_observer(baseline_assistant_idle=False),
            completed_observer(reboot_request_outcome="RETRY"),
        ):
            with self.subTest(receipt=receipt):
                self.assertFalse(observer.observer_receipt_valid(receipt))
        previous_receipt = completed_observer(schema="s22-hci-recovery-observer/v1")
        previous_receipt.pop("trial_identity")
        self.assertFalse(observer.observer_receipt_valid(previous_receipt))

    def test_candidate_hash_rejects_full_partition_mismatch(self):
        def wrong_hash(command, **kwargs):
            return subprocess.CompletedProcess(command, 0, "0" * 64 + "  /dev/block/by-name/recovery\n", "")

        with self.assertRaisesRegex(observer.ObserverError, "RECOVERY hash"):
            observer.candidate_hash(runner=wrong_hash)

    def test_hci_once_rejects_live_rollback_image_before_socket_or_marker(self):
        with tempfile.TemporaryDirectory(prefix="s22-observer-hci-rollback-image-") as temporary:
            root = Path(temporary)
            device_calls = []

            def route_selector(*args, **kwargs):
                return snapshot("candidate-boot-id"), "usb"

            def rollback_hasher(*args, **kwargs):
                return observer.EXPECTED_BASE_SHA256

            def unexpected_socket(command, **kwargs):
                device_calls.append(command)
                return subprocess.CompletedProcess(command, 0, "{}", "")

            with mock.patch.object(observer, "TRIAL_STATE_ROOT", root / "trial-state"):
                with self.assertRaisesRegex(observer.ObserverError,
                                            "RECOVERY hash changed since observation"):
                    observer.run_hci_once(root, TRIAL, completed_observer(),
                                           runner=unexpected_socket,
                                           route_selector=route_selector,
                                           hasher=rollback_hasher)
            self.assertEqual(device_calls, [])
            marker = root / "trial-state" / TRIAL / observer.HCI_MARKER
            self.assertFalse(marker.exists())

    def test_reboot_disconnect_is_unknown_durable_and_never_retried(self):
        with tempfile.TemporaryDirectory(prefix="s22-observer-once-") as temporary:
            root = Path(temporary)
            marker = root / TRIAL / observer.REBOOT_MARKER
            result_path = root / TRIAL / "request-result.json"
            marker.parent.mkdir(parents=True)
            calls = []

            def disconnect(command, **kwargs):
                calls.append(command)
                raise subprocess.TimeoutExpired(command, kwargs["timeout"])

            with mock.patch.object(observer, "TRIAL_STATE_ROOT", root):
                self.assertEqual(observer.request_reboot_once(marker, result_path, runner=disconnect),
                                 "UNKNOWN")
            self.assertEqual(len(calls), 1)
            receipt = observer.read_json(result_path)
            self.assertEqual(receipt["trial_identity"], TRIAL)
            self.assertEqual(receipt["outcome"], "UNKNOWN")
            self.assertFalse(receipt["retry_allowed"])
            with mock.patch.object(observer, "TRIAL_STATE_ROOT", root), \
                    self.assertRaises(FileExistsError):
                observer.request_reboot_once(marker, result_path, runner=disconnect)
            self.assertEqual(len(calls), 1)

    def test_reboot_nonzero_transport_result_is_unknown_and_never_retried(self):
        with tempfile.TemporaryDirectory(prefix="s22-observer-reboot-nonzero-") as temporary:
            root = Path(temporary)
            calls = []

            def nonzero(command, **kwargs):
                calls.append(command)
                return subprocess.CompletedProcess(command, 255, "", "")

            marker = root / TRIAL / observer.REBOOT_MARKER
            result_path = root / TRIAL / "request-result.json"
            marker.parent.mkdir(parents=True)
            with mock.patch.object(observer, "TRIAL_STATE_ROOT", root):
                self.assertEqual(observer.request_reboot_once(marker, result_path, runner=nonzero),
                                 "UNKNOWN")
            result = observer.read_json(result_path)
            self.assertEqual(result["outcome"], "UNKNOWN")
            self.assertFalse(result["retry_allowed"])
            with mock.patch.object(observer, "TRIAL_STATE_ROOT", root), \
                    self.assertRaises(FileExistsError):
                observer.request_reboot_once(marker, result_path, runner=nonzero)
            self.assertEqual(len(calls), 1)

    def test_usb_reboot_executes_only_the_sealed_wrapper_with_trusted_path(self):
        with tempfile.TemporaryDirectory(prefix="s22-observer-sealed-ssh-") as temporary:
            root = Path(temporary)
            wrapper = root / "tools" / "s22-ssh"
            wrapper.parent.mkdir()
            original = (observer.ROOT / "tools/s22-ssh").read_bytes()
            wrapper.write_bytes(original)
            wrapper.chmod(0o700)
            observed = {}
            inherited_path = "/tmp/fake-ssh-bin"

            def inspect_invocation(command, **kwargs):
                wrapper_fd = kwargs["pass_fds"][0]
                observed.update(command=command, kwargs=kwargs,
                                sealed_wrapper=Path(f"/proc/self/fd/{wrapper_fd}").read_bytes())
                # Simulate replacement after the wrapper was opened and sealed.
                wrapper.write_text("#!/bin/bash\necho shim\n", encoding="utf-8")
                return subprocess.CompletedProcess(command, 0, "", "")

            with mock.patch.dict(observer.os.environ, {"PATH": inherited_path}):
                result = observer.run_trusted_remote("usb", None, "s22-reboot recovery",
                                                     runner=inspect_invocation,
                                                     project_root=root)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(observed["command"][0], "/bin/bash")
            self.assertIn("source \"$S22_APPROVED_SSH_FD_PATH\"", observed["command"][2])
            self.assertNotEqual(observed["kwargs"]["env"]["PATH"], inherited_path)
            self.assertEqual(observed["kwargs"]["env"]["PATH"], "/usr/bin")
            self.assertEqual(observed["sealed_wrapper"], original)

    def test_hci_remote_zero_exit_without_exact_device_receipt_is_not_success(self):
        with tempfile.TemporaryDirectory(prefix="s22-observer-hci-bad-receipt-") as temporary:
            root = Path(temporary)
            calls = []

            def route_selector(*args, **kwargs):
                return snapshot("candidate-boot-id"), "usb"

            def hasher(*args, **kwargs):
                return observer.EXPECTED_FLASH_SHA256

            def successful_but_empty(command, **kwargs):
                calls.append(command)
                return subprocess.CompletedProcess(command, 0, "", "")

            with mock.patch.object(observer, "TRIAL_STATE_ROOT", root / "trial-state"):
                with self.assertRaisesRegex(observer.ObserverError, "result was not verified"):
                    observer.run_hci_once(root / "first-state", TRIAL, completed_observer(),
                                           runner=successful_but_empty,
                                           route_selector=route_selector, hasher=hasher)
                receipt = observer.read_json(root / "first-state" / (TRIAL + "-hci") / "result.json")
                self.assertEqual(receipt["outcome"], "UNKNOWN")
                with self.assertRaises(FileExistsError):
                    observer.run_hci_once(root / "second-state", TRIAL,
                                           completed_observer(), runner=successful_but_empty,
                                           route_selector=route_selector, hasher=hasher)
            self.assertEqual(len(calls), 1)

    def test_hci_remote_receipt_requires_unique_boolean_fields(self):
        malformed = (
            '{"socket_created_and_closed":1,"attached":false,'
            '"scan_sent":false,"pairing_started":false}',
            '{"socket_created_and_closed":true,"socket_created_and_closed":false,'
            '"attached":false,"scan_sent":false,"pairing_started":false}',
        )
        for payload in malformed:
            with self.subTest(payload=payload), \
                    tempfile.TemporaryDirectory(prefix="s22-observer-hci-strict-json-") as temporary:
                root = Path(temporary)

                def route_selector(*args, **kwargs):
                    return snapshot("candidate-boot-id"), "usb"

                def hasher(*args, **kwargs):
                    return observer.EXPECTED_FLASH_SHA256

                def malformed_success(command, **kwargs):
                    return subprocess.CompletedProcess(command, 0, payload, "")

                with mock.patch.object(observer, "TRIAL_STATE_ROOT", root / "trial-state"):
                    with self.assertRaisesRegex(observer.ObserverError, "result was not verified"):
                        observer.run_hci_once(root / "state", TRIAL, completed_observer(),
                                               runner=malformed_success,
                                               route_selector=route_selector, hasher=hasher)
                    receipt = observer.read_json(root / "state" / (TRIAL + "-hci") / "result.json")
                self.assertEqual(receipt["outcome"], "UNKNOWN")
                self.assertFalse(receipt["remote_receipt_valid"])

    def test_timeout_continues_reconnect_observation_and_accepts_once(self):
        with tempfile.TemporaryDirectory(prefix="s22-observer-integration-") as temporary:
            root = Path(temporary)
            calls = []
            reboot_calls = []
            reboot_timeouts = []
            hash_requests = []
            snapshot_requests = []
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
                    hash_requests.append(command)
                    return subprocess.CompletedProcess(
                        command, 0,
                        observer.EXPECTED_FLASH_SHA256 + "  /dev/block/by-name/recovery\n", "")
                if remote.startswith("python3 -c ") and "addresses=" in remote:
                            return subprocess.CompletedProcess(command, 0, "192.0.2.10\n", "")
                if remote.startswith("python3 -c ") and "device-tree/model" in remote:
                    snapshot_requests.append(command)
                    if command[0] == "/bin/bash":
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
            old_output = root / "hci-candidate-20260924"
            old_output.mkdir(mode=0o700)
            old_receipt_path = old_output / "result.json"
            old_receipt_contents = '{"schema":"s22-hci-recovery-observer/v1"}\n'
            old_receipt_path.write_text(old_receipt_contents, encoding="utf-8")
            with mock.patch.object(observer, "OBSERVATION_SECONDS", 2), \
                    mock.patch.object(observer, "TRIAL_STATE_ROOT", root / "fixed-trial-state"), \
                    mock.patch.object(observer, "pinned_usb_host_key_alias", return_value="fixture-alias"):
                result = observer.run_reboot_observer(
                    root, TRIAL, flash_receipt(),
                    observation_seconds=2, sample_interval=1, runner=fake_run,
                    clock=fake_clock, sleep=fake_sleep,
                    enumerate_host=lambda **kwargs: redacted)

            self.assertEqual(len(reboot_calls), 1)
            self.assertLessEqual(reboot_timeouts[0], 2)
            self.assertEqual(result["reboot_request_outcome"], "UNKNOWN")
            self.assertEqual(result["reboot_requests"], 1)
            self.assertEqual(result["status"], "completed")
            self.assertEqual(old_receipt_path.read_text(encoding="utf-8"),
                             old_receipt_contents)
            self.assertLessEqual(result["observed_seconds"], 2)
            self.assertEqual(result["baseline_recovery_sha256"], observer.EXPECTED_FLASH_SHA256)
            self.assertEqual(result["recovery_sha256_after_boot"], observer.EXPECTED_FLASH_SHA256)
            wrapper = str(observer.ROOT / "tools/s22-ssh")
            self.assertEqual(reboot_calls[0][0], "/bin/bash")
            self.assertEqual(reboot_calls[0][-2], wrapper)
            self.assertEqual(hash_requests[0][0], "/bin/bash")
            self.assertEqual(hash_requests[0][-2], wrapper)
            self.assertTrue(any(command[0] == "/usr/bin/ssh" and
                                "root@192.0.2.10" in command
                                for command in snapshot_requests))
            self.assertTrue(any(command[0] == "/usr/bin/ssh" and
                                "root@192.0.2.10" in command
                                for command in hash_requests[1:]))
            with mock.patch.object(observer, "OBSERVATION_SECONDS", 2):
                self.assertTrue(observer.observer_receipt_valid(result),
                                json.dumps(result, sort_keys=True))
            self.assertGreaterEqual(result["samples"], 2)
            self.assertEqual(len(list((root / TRIAL).glob("host-window-*.json"))), result["samples"])
            rendered = json.dumps(result) + "\n".join(
                path.read_text() for path in (root / TRIAL).glob("*.json"))
            self.assertNotIn("192.0.2.10", rendered)
            self.assertNotIn("fixture-alias", rendered)
            self.assertTrue(any(command[0] == "/usr/bin/ssh" for command in calls))
            reboot_count = len(reboot_calls)
            with mock.patch.object(observer, "OBSERVATION_SECONDS", 2), \
                    mock.patch.object(observer, "TRIAL_STATE_ROOT", root / "fixed-trial-state"), \
                    mock.patch.object(observer, "pinned_usb_host_key_alias", return_value="fixture-alias"):
                with self.assertRaises(FileExistsError):
                    observer.run_reboot_observer(
                        root / "alternate-state-root", TRIAL, flash_receipt(),
                        wifi_host="phone-wifi.invalid", observation_seconds=2,
                        sample_interval=1, runner=fake_run, clock=fake_clock,
                        sleep=fake_sleep, enumerate_host=lambda **kwargs: redacted)
            self.assertEqual(len(reboot_calls), reboot_count)

    def test_hci_once_refuses_stale_boot_before_hash_or_socket(self):
        with tempfile.TemporaryDirectory(prefix="s22-observer-hci-stale-") as temporary:
            root = Path(temporary)
            calls = []

            def snapshotter(*args, **kwargs):
                return snapshot("different-boot-id"), "usb"

            def hasher(*args, **kwargs):
                calls.append("hash")
                return observer.EXPECTED_FLASH_SHA256

            with mock.patch.object(observer, "TRIAL_STATE_ROOT", root / "trial-state"):
                with self.assertRaisesRegex(observer.ObserverError, "boot ID changed"):
                    observer.run_hci_once(root, TRIAL, completed_observer(),
                                           route_selector=snapshotter, hasher=hasher)
            self.assertEqual(calls, [])
            marker = root / "trial-state" / TRIAL / observer.HCI_MARKER
            self.assertFalse(marker.exists())

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

            with mock.patch.object(observer, "TRIAL_STATE_ROOT", root / "trial-state"):
                with self.assertRaisesRegex(observer.ObserverError, "UNKNOWN"):
                    observer.run_hci_once(root, TRIAL, completed_observer(), runner=disconnect,
                                           route_selector=route_selector, hasher=hasher)
                result = observer.read_json(root / (TRIAL + "-hci") / "result.json")
                marker = observer.read_json(root / "trial-state" / TRIAL / observer.HCI_MARKER)
                self.assertEqual(result["outcome"], "UNKNOWN")
                self.assertEqual(result["trial_identity"], TRIAL)
                self.assertEqual(marker["trial_identity"], TRIAL)
                self.assertFalse(result["retry_allowed"])
                with self.assertRaises(FileExistsError):
                    observer.run_hci_once(root, TRIAL, completed_observer(), runner=disconnect,
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

            with mock.patch.object(observer, "TRIAL_STATE_ROOT", root / "trial-state"):
                with self.assertRaises(observer.ObserverError):
                    observer.run_hci_once(root, TRIAL, completed_observer(), runner=nonzero,
                                           route_selector=route_selector, hasher=hasher)
                result = observer.read_json(root / (TRIAL + "-hci") / "result.json")
                self.assertEqual(result["outcome"], "UNKNOWN")
                self.assertFalse(result["retry_allowed"])
                with self.assertRaises(FileExistsError):
                    observer.run_hci_once(root, TRIAL, completed_observer(), runner=nonzero,
                                           route_selector=route_selector, hasher=hasher)
            self.assertEqual(len(socket_calls), 1)

    def test_hci_preflight_carries_selected_network_route_through_hash_and_request(self):
        with tempfile.TemporaryDirectory(prefix="s22-observer-hci-route-") as temporary:
            root = Path(temporary)
            calls = []
            hash_routes = []
            wifi_host = "phone-wifi.invalid"
            wifi_state = snapshot("candidate-boot-id")

            def route_selector(wifi_host, tailscale_host, **kwargs):
                self.assertEqual(wifi_host, "phone-wifi.invalid")
                return wifi_state, "wifi"

            def hasher(transport, host, runner, project_root):
                hash_routes.append((transport, host))
                return observer.EXPECTED_FLASH_SHA256

            probe_result = json.dumps({"socket_created_and_closed": True, "attached": False,
                                       "scan_sent": False, "pairing_started": False})

            def socket_request(command, **kwargs):
                calls.append(command)
                return subprocess.CompletedProcess(command, 0, probe_result, "")

            with mock.patch.object(observer, "TRIAL_STATE_ROOT", root / "trial-state"), \
                    mock.patch.object(observer, "pinned_usb_host_key_alias", return_value="ephemeral-alias"):
                result = observer.run_hci_once(
                    root, TRIAL, completed_observer(), runner=socket_request,
                    route_selector=route_selector, hasher=hasher,
                    wifi_host=wifi_host, project_root=observer.ROOT)
            self.assertEqual(result["status"], "completed")
            self.assertTrue(result["remote_receipt_valid"])
            self.assertEqual(hash_routes, [("wifi", wifi_host)])
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][0], "/usr/bin/ssh")
            self.assertIn("root@" + wifi_host, calls[0])
            preflight = observer.read_json(root / (TRIAL + "-hci") / "preflight.json")
            self.assertEqual(preflight["trial_identity"], TRIAL)
            self.assertEqual(preflight["transport"], "wifi")
            self.assertEqual(preflight["network_state"], wifi_state["network_state"])
            self.assertTrue(observer.network_state_valid(preflight["network_state"]))
            self.assertNotIn(wifi_host, json.dumps(preflight))

    def test_hci_mode_requires_observer_receipt_before_any_device_operation(self):
        with tempfile.TemporaryDirectory(prefix="s22-observer-hci-gate-") as temporary:
            root = Path(temporary)
            calls = []

            def never_called(*args, **kwargs):
                calls.append(args)
                return subprocess.CompletedProcess(args, 0, "{}", "")

            with mock.patch.object(observer, "TRIAL_STATE_ROOT", root / "trial-state"):
                with self.assertRaisesRegex(observer.ObserverError, "completed observer"):
                    observer.run_hci_once(root, TRIAL, completed_observer(target="boot"),
                                           runner=never_called)
            self.assertEqual(calls, [])
            marker = root / "trial-state" / TRIAL / observer.HCI_MARKER
            self.assertFalse(marker.exists())

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
            wrapper_alias = re.search(
                r'\broot@([A-Za-z0-9._:-]+)\s+"\$@"',
                wrapper_bytes.decode("utf-8"),
            ).group(1)
            wrapper.write_bytes(wrapper_bytes)
            # The private project root may be an older dirty checkout whose
            # deployer predates this policy constant. Trust comes from the
            # reviewed observer's deployer, while this root supplies only the
            # exact pinned wrapper and its private known-hosts file.
            deployer.write_text("# older local deployer without wrapper policy\n")
            known_hosts.write_text(wrapper_alias + " ssh-ed25519 AAAATESTKEY\n")
            self.assertEqual(hashlib.sha256(observer.validated_ssh_wrapper(root).read_bytes()).hexdigest(), digest)
            checked = subprocess.CompletedProcess(
                ["/usr/bin/ssh-keygen"], 0, "# Host match\n", ""
            )
            with mock.patch.object(observer.subprocess, "run", return_value=checked) as key_lookup:
                command = observer._ssh_transport("wifi", "phone-wifi.invalid", "true", root)
            key_lookup.assert_called_once()
            self.assertEqual(key_lookup.call_args.args[0][0], "/usr/bin/ssh-keygen")
            self.assertIn("HostKeyAlias=" + wrapper_alias, command)
            self.assertTrue(any(item.startswith("UserKnownHostsFile=") for item in command))

            wrapper.write_text(wrapper.read_text() + "# modified\n")
            with self.assertRaisesRegex(observer.ObserverError, "approved deployer SHA-256"):
                observer.validated_ssh_wrapper(root)

    @unittest.skipUnless(Path("/usr/bin/ssh-keygen").is_file(), "OpenSSH ssh-keygen unavailable")
    def test_hashed_usb_pinned_known_host_entry_is_resolved(self):
        with tempfile.TemporaryDirectory(prefix="s22-observer-hashed-hostkey-") as temporary:
            root = Path(temporary)
            wrapper = root / "tools" / "s22-ssh"
            deployer = root / "tools" / "hardware" / "deploy-audio-recovery.py"
            known_hosts = root / "evidence" / "native-linux-20260919" / "native-v2-known-hosts"
            key_path = root / "fixture-host-key"
            wrapper.parent.mkdir(parents=True)
            deployer.parent.mkdir(parents=True)
            known_hosts.parent.mkdir(parents=True)
            wrapper.write_bytes((observer.ROOT / "tools" / "s22-ssh").read_bytes())
            deployer.write_text("# source-only alternate project root\n")
            wrapper_alias = re.search(
                r'\broot@([A-Za-z0-9._:-]+)\s+"\$@"',
                wrapper.read_text(encoding="utf-8"),
            ).group(1)
            generated = subprocess.run(
                ["/usr/bin/ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key_path)],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(generated.returncode, 0, generated.stderr)
            key_type, public_key = Path(str(key_path) + ".pub").read_text().split()[:2]
            known_hosts.write_text(f"{wrapper_alias} {key_type} {public_key}\n")
            hashed = subprocess.run(
                ["/usr/bin/ssh-keygen", "-H", "-f", str(known_hosts)],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(hashed.returncode, 0, hashed.stderr)
            self.assertTrue(known_hosts.read_text().split()[0].startswith("|1|"))
            self.assertNotIn(wrapper_alias, known_hosts.read_text())
            self.assertEqual(observer.pinned_usb_host_key_alias(known_hosts, root), wrapper_alias)

    def test_host_enumeration_redacts_addresses_ssids_and_tailscale_names(self):
        outputs = {
            "lsusb": "Bus 001 Device 002: ID 0000:0000 Test Device\n",
            "ip": "eth0 UP 02:00:00:00:00:01\n",
            "nmcli": "wlan0:wifi:connected\n",
            "tailscale": (
                '{"BackendState":"Running","Self":{"DNSName":"phone.invalid"},'
                '"Peer":{"peer-1":{"Online":true,"DNSName":"peer.invalid",'
                '"TailscaleIPs":["192.0.2.11"]}}}'
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
        for private_value in ("02:00:00:00:00:01", "phone.invalid",
                              "peer.invalid", "192.0.2.11"):
            self.assertNotIn(private_value, rendered)


if __name__ == "__main__":
    unittest.main(verbosity=2)
