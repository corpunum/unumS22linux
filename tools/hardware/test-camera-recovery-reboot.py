#!/usr/bin/env python3
"""Host-only regressions for the camera one-shot reboot observer."""
from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


OBS = load("s22_camera_recovery_reboot_observer_test",
           ROOT / "tools/hardware/camera-recovery-reboot-once.py")
DEPLOY = OBS.DEPLOY


def write_private_json(path: Path, value: dict) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    data = (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o600)
    try:
        view = memoryview(data)
        while view:
            count = os.write(fd, view)
            view = view[count:]
        os.fsync(fd)
    finally:
        os.close(fd)
    return data


def replace_private_json(path: Path, value: dict) -> bytes:
    path.unlink()
    return write_private_json(path, value)


class FakeClock:
    def __init__(self, start: float = 1_000_000.0):
        self.value = start

    def now(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.value += seconds


class CameraRebootFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="s22-camera-reboot-")
        self.root = Path(self.temp.name)
        os.chmod(self.root, 0o700)
        self.receipts = self.root / "receipts"
        self.receipts.mkdir(mode=0o700)
        self.guard_root = self.root / "guard"
        self.guard_root.mkdir(mode=0o700)
        self.local = self.root / "observer"
        self.local.mkdir(mode=0o700)
        self.observation_scenario = 0
        self.bundle_paths = {}
        self.guard_markers = {}
        for name in ("camera-forward", "camera-reverse"):
            self._write_flash_bundle(name)
        self.addCleanup(self.temp.cleanup)

    @staticmethod
    def _target():
        return {
            "alias_target": "/dev/sda16", "rdev": "259:0",
            "capacity_bytes": DEPLOY.SIZE, "sysfs_sectors": DEPLOY.SIZE // 512,
            "partition_name": "recovery", "mounted": False,
        }

    def identity(self, profile_name: str, boot_id: str, recovery_sha: str,
                 module_id: str | None, *, loaded: bool = True) -> dict:
        return {
            "schema": "camera-recovery-identity/v1", "boot_id": boot_id,
            "recovery_sha256": recovery_sha, "recovery_target": self._target(),
            "pid1": "native-guardian", "native_ready": True,
            "kernel_gnu_build_id": OBS.RUNNING_KERNEL_BUILD_ID,
            "camera_module_loaded": loaded,
            "camera_module_gnu_build_id": module_id if loaded else None,
        }

    def _write_flash_bundle(self, profile_name: str):
        profile = DEPLOY.resolve_profile(profile_name)
        trial = DEPLOY.TRIAL_IDENTITIES[(profile_name, "flash")]
        directory = self.receipts / trial
        directory.mkdir(mode=0o700, exist_ok=True)
        prefix = profile["receipt_prefix"]
        for suffix in ("flash-prewrite", "flash-write-readback", "flash"):
            old = directory / f"{prefix}-{suffix}.json"
            if old.exists():
                old.unlink()
        marker_path = self.guard_root / f"{trial}.json"
        if marker_path.exists():
            marker_path.unlink()
        before_module = profile["before"]["module_build_id"]
        pre_id = self.identity(profile_name, "11111111-2222-4333-8444-555555555555",
                                profile["before_sha256"], before_module)
        pre = {
            "schema": "camera-recovery-prewrite/v1", "profile": profile_name,
            "trial_identity": trial, "partition": "recovery",
            "expected_write_sha256": profile["new_sha256"], "identity": pre_id,
        }
        pre_path = directory / f"{prefix}-flash-prewrite.json"
        pre_bytes = write_private_json(pre_path, pre)
        raw = {
            "schema": "camera-recovery-write-readback/v1",
            "mode": "flash", "partition_written": "recovery",
            "before_sha256": profile["before_sha256"],
            "readback_sha256": profile["new_sha256"],
            "written_image_sha256": profile["new_sha256"], "bytes": DEPLOY.SIZE,
            "reboot_performed": False, "explicit_execute_flag_present": True,
            "profile": profile_name, "trial_identity": trial, "partition": "recovery",
            "expected_before_module_build_id": before_module,
            "expected_write_module_build_id": profile["write"]["module_build_id"],
            "prewrite_identity_receipt_sha256": hashlib.sha256(pre_bytes).hexdigest(),
            "prewrite_boot_id": pre_id["boot_id"],
            "prewrite_kernel_gnu_build_id": pre_id["kernel_gnu_build_id"],
            "prewrite_camera_module_loaded": pre_id["camera_module_loaded"],
            "prewrite_camera_module_gnu_build_id": pre_id["camera_module_gnu_build_id"],
        }
        raw_path = directory / f"{prefix}-flash-write-readback.json"
        raw_bytes = write_private_json(raw_path, raw)
        post_id = self.identity(profile_name, pre_id["boot_id"], profile["new_sha256"],
                                pre_id["camera_module_gnu_build_id"],
                                loaded=pre_id["camera_module_loaded"])
        final = dict(raw)
        final.update({
            "schema": "camera-recovery-flash-bound/v1",
            "write_readback_receipt_sha256": hashlib.sha256(raw_bytes).hexdigest(),
            "postwrite_identity": post_id,
            "prewrite_postwrite_same_boot": True,
        })
        final_path = directory / f"{prefix}-flash.json"
        final_bytes = write_private_json(final_path, final)
        marker = {
            "trial_id": trial,
            "operation_kind": f"camera-recovery-{profile_name.removeprefix('camera-')}-flash",
            "status": "complete", "outcome": "success",
            "receipt_path": str(final_path),
            "receipt_sha256": hashlib.sha256(final_bytes).hexdigest(),
        }
        write_private_json(marker_path, marker)
        self.bundle_paths[profile_name] = {
            "directory": directory, "pre": pre_path, "raw": raw_path,
            "final": final_path, "flash_marker": marker_path,
            "profile": profile, "pre_identity": pre_id,
            "flash_sha256": hashlib.sha256(final_bytes).hexdigest(),
        }

    @staticmethod
    def helper_snapshot():
        return {
            "schema": "camera-reboot-helpers/v1",
            "helpers": {
                "reboot": {"sha256": OBS.S22_REBOOT_SHA256, "uid": 0,
                           "mode": 0o755, "regular": True, "symlink": False},
                "restart2": {"sha256": OBS.S22_RESTART2_SHA256, "uid": 0,
                             "mode": 0o755, "regular": True, "symlink": False},
            },
        }

    @staticmethod
    def healthy_state(boot_id: str, bore: str, *, desktop: bool = True,
                      serious_fault: bool = False, liveness_unresolved: bool = False) -> dict:
        logs = {
            "fatal_indicators": [], "hung_task_warning_count": 0,
            "call_trace_count": 0, "liveness_unresolved": liveness_unresolved,
            "capture_complete": True, "coverage_complete": True,
            "full_boot_log_coverage": False,
            "liveness_review_status": "no_hung_task_warning_in_available_ring",
            "source_wait_stacks": {
                "source_commit": OBS.AUDIO.EXPECTED_TZ_SOURCE_COMMIT,
                "warning_count": 0, "matched_count": 0, "unmatched_count": 0,
                "progress_measured": False,
            },
        }
        return {
            "boot_id": boot_id, "pid1": "native-guardian", "native_ready": True,
            "persistent_ready": {"ready": True, "mount_ready": True},
            "health": {"status": "ok"},
            "slots": {"count": 1, "processing": [False]}, "assistant_idle": True,
            "serious_fault": serious_fault, "kernel_log_classification": logs,
            "loaded_modules": ["wlan", "cfg80211"],
            "kernel_components": ["btpower", "exynos_tty", "bluetooth", "hci_uart"],
            "readiness": {
                "kernel_remote_control": True, "model_api_health": True,
                "model_idle": True, "desktop_environment": desktop,
                "desktop_pi_status": "ready" if desktop else "failed",
                "browser_terminal_status": "ready" if desktop else "failed",
                "dedicated_pi_session_status": "absent",
            },
            "hyprland_running": desktop, "pi_process_running": desktop,
            "ttyd_running": desktop, "pi_assistant_ready": desktop,
            "network_state": {"ready": True, "interfaces": [
                {"name": "wlan0", "operstate": "up", "carrier": "1"}]},
            "power_state": {
                "battery_status": "Charging", "battery_capacity_percent": 90,
                "battery_temperature_celsius": 32, "thermal_all_readable": True,
                "thermal_zone_count": 1, "thermal_max_temperature_celsius": 40,
            },
            "device_tree_model": "Samsung R0S",
            "device_tree_compatible": ["samsung,s5e9925"],
            "boot_model": "SM-S901B", "boot_hardware": "s5e9925",
            "bootloader_model_match": True,
            "uptime_seconds": 500, "gnu_build_id": OBS.RUNNING_KERNEL_BUILD_ID,
            "kernel_release": "5.10.260-g4e5c5ad7d950",
            "boot_reset_first_record": bore,
        }

    def prepare_request_context(self, profile_name: str, outcome="UNKNOWN", *, local_root=None):
        info = self.bundle_paths[profile_name]
        trial = OBS.S22_REBOOT_TRIAL_IDS[profile_name]
        root = self.local if local_root is None else Path(local_root)
        request_dir = root / trial
        for name in ("request-started.json", "request-result.json"):
            path = request_dir / name
            if path.exists():
                path.unlink()
        old_marker = self.guard_root / f"{trial}.json"
        if old_marker.exists():
            old_marker.unlink()
        baseline_bore = "[  1.000] / R / INFORM3(12345674) > RECOVERY > baseline\n"
        attempt = {
            "schema": "camera-reboot-request/v1", "profile": profile_name,
            "trial_identity": trial, "flash_trial_identity": DEPLOY.TRIAL_IDENTITIES[
                (profile_name, "flash")],
            "flash_receipt_sha256": info["flash_sha256"],
            "prewrite_boot_id": info["pre_identity"]["boot_id"],
            "request_command": OBS.REQUEST_COMMAND_LABEL,
            "baseline_boot_reset_record": baseline_bore, "retry_allowed": False,
        }
        result = {
            "schema": "camera-reboot-request-result/v1", "profile": profile_name,
            "trial_identity": trial, "outcome": outcome, "retry_allowed": False,
        }
        write_private_json(request_dir / "request-started.json", attempt)
        write_private_json(request_dir / "request-result.json", result)
        global_marker = {
            "trial_id": trial,
            "operation_kind": f"camera-recovery-{profile_name.removeprefix('camera-')}-reboot",
            "status": "unknown",
        }
        marker_path = self.guard_root / f"{trial}.json"
        write_private_json(marker_path, global_marker)
        return baseline_bore, marker_path


class ReceiptAndRequestTests(CameraRebootFixture):
    def test_installed_reboot_helper_hash_is_exact_source_hash_and_64_hex(self):
        digest = hashlib.sha256((ROOT / "tools/linux-rootfs/s22-reboot").read_bytes()).hexdigest()
        self.assertEqual(len(OBS.S22_REBOOT_SHA256), 64)
        self.assertEqual(OBS.S22_REBOOT_SHA256, digest)
        self.assertEqual(OBS.S22_REBOOT_SHA256,
                         "25df3dafd81a0fb8ff0b89055977918e386bd83959f23e5723018feda82f2c42")

    def test_flash_bundle_validates_exact_private_receipt_chain(self):
        for profile in ("camera-forward", "camera-reverse"):
            bundle = OBS.validate_flash_bundle(profile, receipts_root=self.receipts)
            self.assertEqual(bundle["flash_receipt_sha256"],
                             self.bundle_paths[profile]["flash_sha256"])
            OBS.validate_flash_guard_marker(profile, bundle,
                                            guard_state_root=self.guard_root)

    def test_flash_receipt_replay_or_changed_boot_is_rejected(self):
        info = self.bundle_paths["camera-forward"]
        final = json.loads(info["final"].read_text())
        final["trial_identity"] = "old-camera-recovery-trial"
        replace_private_json(info["final"], final)
        with self.assertRaises(OBS.CameraObserverError):
            OBS.validate_flash_bundle("camera-forward", receipts_root=self.receipts)

        # Restore exact fixture, then break only the same-boot binding.
        self._write_flash_bundle("camera-forward")
        info = self.bundle_paths["camera-forward"]
        final = json.loads(info["final"].read_text())
        final["prewrite_postwrite_same_boot"] = False
        replace_private_json(info["final"], final)
        with self.assertRaisesRegex(OBS.CameraObserverError, "same-boot"):
            OBS.validate_flash_bundle("camera-forward", receipts_root=self.receipts)

        self._write_flash_bundle("camera-forward")
        info = self.bundle_paths["camera-forward"]
        final = json.loads(info["final"].read_text())
        final["postwrite_identity"]["boot_id"] = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
        replace_private_json(info["final"], final)
        with self.assertRaises(OBS.CameraObserverError):
            OBS.validate_flash_bundle("camera-forward", receipts_root=self.receipts)

        self._write_flash_bundle("camera-forward")
        info = self.bundle_paths["camera-forward"]
        final = json.loads(info["final"].read_text())
        final["postwrite_identity"]["recovery_sha256"] = "0" * 64
        replace_private_json(info["final"], final)
        with self.assertRaisesRegex(OBS.CameraObserverError, "RECOVERY hash mismatch"):
            OBS.validate_flash_bundle("camera-forward", receipts_root=self.receipts)

    def test_missing_or_mismatched_flash_guard_marker_blocks_before_remote(self):
        for mode in ("missing", "mismatched"):
            with self.subTest(mode=mode):
                self._write_flash_bundle("camera-forward")
                marker = self.bundle_paths["camera-forward"]["flash_marker"]
                if mode == "missing":
                    marker.unlink()
                else:
                    value = json.loads(marker.read_text())
                    value["receipt_sha256"] = "0" * 64
                    replace_private_json(marker, value)
                commands = []
                with mock.patch.object(DEPLOY, "validate_artifacts",
                                       return_value=(DEPLOY.resolve_profile("camera-forward"), b"", b"")):
                    with self.assertRaises(OBS.CameraObserverError):
                        OBS.request_recovery_once(
                            "camera-forward", OBS.OWNER_ACK["camera-forward"],
                            receipts_root=self.receipts, local_root=self.local,
                            guard_state_root=self.guard_root, snapshotter=lambda: {},
                            remote=lambda command, timeout: commands.append(command))
                self.assertEqual(commands, [])

    def test_authorization_and_host_artifact_gates_precede_any_request(self):
        profile = "camera-forward"
        commands = []
        with mock.patch.object(DEPLOY, "validate_artifacts",
                               side_effect=ValueError("wrong host image")):
            with self.assertRaisesRegex(OBS.CameraObserverError, "host candidate/rollback"):
                OBS.request_recovery_once(
                    profile, OBS.OWNER_ACK[profile], receipts_root=self.receipts, local_root=self.local,
                    guard_state_root=self.guard_root, snapshotter=lambda: {},
                    remote=lambda command, timeout: commands.append(command))
        self.assertEqual(commands, [])

        with mock.patch.object(DEPLOY, "validate_artifacts",
                               return_value=(DEPLOY.resolve_profile(profile), b"", b"")):
            with self.assertRaisesRegex(OBS.CameraObserverError, "acknowledgement"):
                OBS.request_recovery_once(
                    profile, "wrong-owner-token", receipts_root=self.receipts,
                    local_root=self.local, guard_state_root=self.guard_root,
                    remote=lambda command, timeout: commands.append(command))
        self.assertEqual(commands, [])

    def test_wrong_current_boot_or_image_is_refused_before_remote(self):
        profile = "camera-forward"
        info = self.bundle_paths[profile]
        old_boot = info["pre_identity"]["boot_id"]
        bore = "[  1.000] / R / INFORM3(12345674) > RECOVERY > baseline\n"
        live = self.healthy_state(old_boot, bore)
        current = self.identity(profile, "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
                                info["profile"]["new_sha256"],
                                info["profile"]["before"]["module_build_id"])
        commands = []
        with mock.patch.object(DEPLOY, "validate_artifacts",
                               return_value=(info["profile"], b"before", b"write")):
            with self.assertRaisesRegex(OBS.CameraObserverError, "current boot ID"):
                OBS.request_recovery_once(
                    profile, OBS.OWNER_ACK[profile], receipts_root=self.receipts,
                    local_root=self.local, guard_state_root=self.guard_root,
                    snapshotter=lambda: live, remote=lambda command, timeout:
                    commands.append(command), identity_reader=lambda: current,
                    helper_reader=self.helper_snapshot)
        self.assertEqual(commands, [])

    def _run_request(self, *, raise_timeout=False):
        profile = "camera-forward"
        info = self.bundle_paths[profile]
        old_boot = info["pre_identity"]["boot_id"]
        bore = "[  1.000] / R / INFORM3(12345674) > RECOVERY > baseline\n"
        live = self.healthy_state(old_boot, bore)
        current = self.identity(profile, old_boot, info["profile"]["new_sha256"],
                                info["profile"]["before"]["module_build_id"])
        commands = []

        def remote(command, timeout):
            commands.append(command)
            request_dir, attempt, _ = OBS._request_local_paths(profile, self.local)
            self.assertTrue(attempt.exists())
            pending = json.loads((self.guard_root /
                                  f"{OBS.S22_REBOOT_TRIAL_IDS[profile]}.json").read_text())
            self.assertEqual(pending["status"], "pending")
            if raise_timeout:
                raise subprocess.TimeoutExpired(command, timeout)
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        with mock.patch.object(DEPLOY, "validate_artifacts",
                               return_value=(info["profile"], b"before", b"write")):
            result = OBS.request_recovery_once(
                profile, OBS.OWNER_ACK[profile], receipts_root=self.receipts,
                local_root=self.local, guard_state_root=self.guard_root,
                snapshotter=lambda: live, remote=remote,
                identity_reader=lambda: current,
                helper_reader=self.helper_snapshot)
        self.assertEqual(commands, [OBS.reboot_exec_command(old_boot)])
        command_argv = shlex.split(commands[0])
        self.assertEqual(command_argv[:3], ["python3", "-I", "-B"])
        self.assertIn("os.execve('/usr/local/sbin/s22-reboot'", command_argv[4])
        marker = json.loads((self.guard_root /
                             f"{OBS.S22_REBOOT_TRIAL_IDS[profile]}.json").read_text())
        self.assertEqual(marker["status"], "unknown")
        self.assertFalse(result["retry_allowed"])
        return result

    def test_one_shot_request_uses_absolute_helper_and_ack_is_not_completion(self):
        result = self._run_request()
        self.assertEqual(result["outcome"], "ACKNOWLEDGED")

    def test_disconnect_is_unknown_durable_and_never_retried(self):
        result = self._run_request(raise_timeout=True)
        self.assertEqual(result["outcome"], "UNKNOWN")
        self.assertEqual(result["error_type"], "TimeoutExpired")

    def test_immediate_boot_id_prelude_exits_before_helper_on_mismatch(self):
        expected = "11111111-2222-4333-8444-555555555555"
        script = OBS.render_reboot_prelude(expected)
        compile(script, "camera-reboot-boot-prelude", "exec")
        with mock.patch("builtins.open", mock.mock_open(read_data="aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee\n")), \
             mock.patch.object(OBS.os, "lstat") as lstat, \
             mock.patch.object(OBS.os, "execve") as execve:
            with self.assertRaises(SystemExit) as raised:
                exec(compile(script, "camera-reboot-boot-prelude", "exec"), {})
        self.assertEqual(raised.exception.code, 42)
        lstat.assert_not_called()
        execve.assert_not_called()


class ObservationTests(CameraRebootFixture):
    def _observe(self, *, profile="camera-forward", duration=600,
                 initial_gap=False, delayed_module=False, final_bore=None,
                 desktop=True, serious_at=None, liveness_at=None,
                 wrong_module_at_start=False, final_snapshot_gap=False,
                 late_identity_by=0, local_root=None):
        if local_root is None:
            self.observation_scenario += 1
            local_root = self.root / f"observer-scenario-{self.observation_scenario}"
            local_root.mkdir(mode=0o700)
        else:
            local_root = Path(local_root)
            local_root.mkdir(mode=0o700, exist_ok=True)
        baseline_bore, marker_path = self.prepare_request_context(
            profile, local_root=local_root)
        info = self.bundle_paths[profile]
        old_boot = info["pre_identity"]["boot_id"]
        new_boot = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
        actual_bore = final_bore or "[  2.000] / R / INFORM3(12345674) > RECOVERY > forward\n"
        clock = FakeClock()
        calls = {"snapshots": 0, "identities": 0, "helpers": 0}
        remote_calls = []

        def snapshotter():
            calls["snapshots"] += 1
            index = calls["snapshots"] - 1
            sampling_window = duration - min(120, max(1, duration - OBS.MIN_STABLE_SECONDS))
            if initial_gap and index == 0:
                raise OBS.ReadOnlyTransportGap("USB reconnect gap")
            if final_snapshot_gap and clock.now() - 1_000_000 >= sampling_window:
                raise OBS.ReadOnlyTransportGap("final USB snapshot gap")
            observed_elapsed = clock.now() - 1_000_000
            current_bore = (final_bore if final_bore is not None and
                            observed_elapsed >= sampling_window else actual_bore)
            state = self.healthy_state(
                new_boot, current_bore, desktop=desktop,
                serious_fault=(serious_at is not None and clock.now() - 1_000_000 >= serious_at),
                liveness_unresolved=(liveness_at is not None and
                                     clock.now() - 1_000_000 >= liveness_at))
            return state

        def identity_reader():
            calls["identities"] += 1
            if late_identity_by:
                clock.value += late_identity_by
            module_id = info["profile"]["write"]["module_build_id"]
            loaded = True
            if delayed_module and clock.now() - 1_000_000 < 60:
                loaded = False
            if wrong_module_at_start and calls["identities"] == 1:
                module_id = "wrong-module-id"
            return self.identity(profile, new_boot, info["profile"]["new_sha256"],
                                 module_id, loaded=loaded)

        def helper_reader():
            calls["helpers"] += 1
            return self.helper_snapshot()

        with mock.patch.object(DEPLOY, "validate_artifacts",
                               return_value=(info["profile"], b"before", b"write")):
            result = OBS.observe_only(
                profile, receipts_root=self.receipts, local_root=local_root,
                guard_state_root=self.guard_root, snapshotter=snapshotter,
                remote=lambda command, timeout: remote_calls.append(command),
                identity_reader=identity_reader, helper_reader=helper_reader,
                observation_seconds=duration, sample_interval=5,
                clock=clock.now, sleep=clock.sleep)
        after = marker_path.read_bytes()
        return result, calls, remote_calls, after

    def test_usb_gap_then_healthy_new_boot_accepts_with_private_receipt_and_no_reboot(self):
        result, calls, remote_calls, marker_after = self._observe(initial_gap=True)
        self.assertEqual(result["status"], "accepted")
        self.assertEqual(result["transport_scope"], "usb_only")
        self.assertEqual(result["transport_gap_count"], 1)
        self.assertGreaterEqual(result["continuous_stable_seconds"], 180)
        self.assertGreaterEqual(calls["identities"], 2)
        self.assertEqual(remote_calls, [])
        self.assertEqual(result["receipt_path"] and
                         (Path(result["receipt_path"]).stat().st_mode & 0o777), 0o600)
        self.assertEqual(marker_after, (self.guard_root /
                         f"{OBS.S22_REBOOT_TRIAL_IDS['camera-forward']}.json").read_bytes())

    def test_complete_negative_or_positive_receipt_blocks_a_fresh_window(self):
        scenarios = (({"serious_at": 25}, "not_accepted"), ({}, "accepted"))
        for options, expected_status in scenarios:
            with self.subTest(expected_status=expected_status):
                completed, _, _, _ = self._observe(**options)
                self.assertEqual(completed["status"], expected_status)
                local_root = Path(completed["receipt_path"]).parents[2]
                calls = {"snapshot": 0, "remote": 0}

                def snapshotter():
                    calls["snapshot"] += 1
                    return {}

                def remote(command, timeout):
                    calls["remote"] += 1

                with self.assertRaisesRegex(
                        OBS.CameraObserverError,
                        "complete observation receipt already exists"):
                    OBS.observe_only(
                        "camera-forward", receipts_root=self.receipts,
                        local_root=local_root, guard_state_root=self.guard_root,
                        snapshotter=snapshotter, remote=remote,
                        identity_reader=lambda: {}, helper_reader=lambda: {})
                self.assertEqual(calls, {"snapshot": 0, "remote": 0})

    def test_malformed_prior_observation_receipt_fails_closed_before_transport(self):
        result, _, _, _ = self._observe(serious_at=25)
        receipt_path = Path(result["receipt_path"])
        replace_private_json(receipt_path, {"schema": "wrong-schema"})
        local_root = receipt_path.parents[2]
        calls = {"snapshot": 0, "remote": 0}

        with self.assertRaisesRegex(OBS.CameraObserverError, "malformed"):
            OBS.observe_only(
                "camera-forward", receipts_root=self.receipts, local_root=local_root,
                guard_state_root=self.guard_root,
                snapshotter=lambda: calls.__setitem__("snapshot", calls["snapshot"] + 1),
                remote=lambda command, timeout: calls.__setitem__("remote", calls["remote"] + 1),
                identity_reader=lambda: {}, helper_reader=lambda: {})
        self.assertEqual(calls, {"snapshot": 0, "remote": 0})

    def test_empty_observation_directory_without_complete_receipt_can_resume(self):
        local_root = self.root / "observer-empty-resume"
        local_root.mkdir(mode=0o700)
        self.prepare_request_context("camera-forward", local_root=local_root)
        observation_dir = (local_root / OBS.S22_REBOOT_TRIAL_IDS["camera-forward"] /
                           "observations")
        observation_dir.mkdir(mode=0o700)
        self.assertEqual(list(observation_dir.iterdir()), [])
        result, calls, remote_calls, _ = self._observe(
            local_root=local_root, profile="camera-forward", duration=20)
        self.assertEqual(result["status"], "not_accepted")
        self.assertGreater(calls["snapshots"], 0)
        self.assertEqual(remote_calls, [])
        self.assertTrue(Path(result["receipt_path"]).is_file())

    def test_delayed_camera_module_is_pending_then_stability_starts_after_exact_id(self):
        result, calls, _, _ = self._observe(delayed_module=True)
        self.assertEqual(result["status"], "accepted")
        self.assertGreaterEqual(calls["identities"], 3)
        self.assertFalse(result["camera_module_pending"])
        self.assertEqual(result["observed_camera_module_gnu_build_id"],
                         DEPLOY.CAMERA_MODULE_BUILD_ID)

    def test_final_bore_must_be_new_recovery_record_not_stale_or_other_target(self):
        stale = "[  1.000] / R / INFORM3(12345674) > RECOVERY > baseline\n"
        result, _, _, _ = self._observe(final_bore=stale)
        self.assertFalse(result["actual_bore_recovery_new_record"])
        self.assertEqual(result["status"], "not_accepted")
        wrong = "[  2.000] / R / INFORM3(12345674) > NORMAL > boot\n"
        result, _, _, _ = self._observe(final_bore=wrong)
        self.assertFalse(result["actual_bore_recovery_new_record"])
        self.assertEqual(result["status"], "not_accepted")

    def test_nonzero_origin_clock_and_short_duration_never_meet_180_second_gate(self):
        result, _, _, _ = self._observe(duration=250)
        self.assertLess(result["continuous_stable_seconds"], 180)
        self.assertEqual(result["status"], "not_accepted")
        result, _, _, _ = self._observe(profile="camera-reverse", duration=250)
        self.assertLess(result["native_restore_stable_seconds"], 180)
        self.assertFalse(result["recovery_image_restored"])
        self.assertEqual(result["status"], "restore_unverified")

    def test_overrun_is_persisted_but_never_accepted_or_queried_after_deadline(self):
        result, calls, _, _ = self._observe(duration=240, late_identity_by=241)
        self.assertGreater(result["observed_seconds"], result["observation_bound_seconds"])
        self.assertFalse(result["observation_within_bound"])
        self.assertEqual(result["status"], "not_accepted")
        self.assertEqual(calls["snapshots"], 1)
        receipt = Path(result["receipt_path"])
        self.assertTrue(receipt.is_file())
        private = json.loads(receipt.read_text())
        self.assertGreater(private["observed_seconds"], private["observation_bound_seconds"])
        self.assertFalse(private["full_health_accepted"])
        self.assertFalse(OBS.stdout_summary(private)["observation_within_bound"])

    def test_loaded_wrong_module_id_is_sticky_even_if_a_later_read_is_correct(self):
        result, _, _, _ = self._observe(wrong_module_at_start=True)
        self.assertTrue(result["camera_module_mismatch_seen"])
        self.assertEqual(result["status"], "not_accepted")

    def test_failed_final_snapshot_cannot_reuse_stale_reverse_health(self):
        result, _, _, _ = self._observe(profile="camera-reverse", final_snapshot_gap=True)
        self.assertFalse(result["recovery_image_restored"])
        self.assertFalse(result["restored_full_health"])
        self.assertEqual(result["status"], "restore_unverified")

    def test_serious_fault_or_unresolved_liveness_is_sticky_across_later_green_samples(self):
        result, _, _, _ = self._observe(serious_at=25)
        self.assertTrue(result["serious_fault_seen"])
        self.assertEqual(result["status"], "not_accepted")
        result, _, _, _ = self._observe(liveness_at=25)
        self.assertTrue(result["unresolved_liveness_seen"])
        self.assertEqual(result["status"], "not_accepted")

    def test_reverse_image_restore_is_separate_from_desktop_health(self):
        result, _, remote_calls, _ = self._observe(
            profile="camera-reverse", desktop=False)
        self.assertTrue(result["recovery_image_restored"])
        self.assertFalse(result["restored_full_health"])
        self.assertEqual(result["status"], "image_restored_health_unaccepted")
        self.assertEqual(remote_calls, [])


class OutputAndSourceContractTests(unittest.TestCase):
    def test_stdout_summary_drops_raw_boot_ids_records_and_private_paths(self):
        full = {
            "schema": "camera-recovery-reboot-observer/v1", "status": "not_accepted",
            "profile": "camera-forward", "baseline_boot_id": "PRIVATE-BOOT-ID",
            "observed_boot_id": "PRIVATE-NEW-BOOT-ID",
            "boot_reset_first_record": "PRIVATE-BORE-TRACE",
            "receipt_path": "/home/private/observer.json",
            "expected_recovery_sha256": "a" * 64, "observed_recovery_sha256": "a" * 64,
            "actual_bore_recovery_new_record": True,
        }
        public = OBS.stdout_summary(full)
        printed = json.dumps(public)
        for secret in ("PRIVATE-BOOT-ID", "PRIVATE-NEW-BOOT-ID",
                       "PRIVATE-BORE-TRACE", "/home/private/observer.json"):
            self.assertNotIn(secret, printed)
        self.assertIn("observed_recovery_sha256", public)
        self.assertIn("actual_bore_recovery_new_record", public)

    def test_request_cli_keeps_unknown_request_and_observation_in_one_process(self):
        order = []
        request_result = {"outcome": "UNKNOWN"}
        observer_result = {
            "schema": "camera-recovery-reboot-observer/v1", "status": "not_accepted",
            "profile": "camera-forward", "request_outcome": "UNKNOWN",
            "receipt_path": "/private/receipt", "observed_boot_id": "private-boot",
        }
        # Use redirect_stdout so the test also runs under Python isolation.
        output = io.StringIO()
        with mock.patch.object(OBS, "request_recovery_once",
                               side_effect=lambda *args: (order.append("request"), request_result)[1]), \
             mock.patch.object(OBS, "observe_only",
                               side_effect=lambda *args: (order.append("observe"), observer_result)[1]), \
             contextlib.redirect_stdout(output):
            code = OBS.main(["--profile", "camera-forward", "--request-recovery",
                             "--owner-acknowledgement", OBS.OWNER_ACK["camera-forward"]])
        self.assertEqual(code, 3)
        self.assertEqual(order, ["request", "observe"])
        self.assertNotIn("private-boot", output.getvalue())
        self.assertNotIn("/private/receipt", output.getvalue())
        self.assertEqual(json.loads(output.getvalue())["request_outcome"], "UNKNOWN")

    def test_observer_exit_code_never_promotes_inconclusive_result(self):
        for status in ("not_accepted", "restore_unverified",
                       "image_restored_health_unaccepted"):
            self.assertEqual(OBS.observer_exit_code({"status": status},
                                                    action_requested=True), 3)
        self.assertEqual(OBS.observer_exit_code({"status": "accepted"},
                                                action_requested=True), 0)
        self.assertEqual(OBS.observer_exit_code({"status": "image_restored_health_accepted"},
                                                action_requested=True), 0)
        self.assertEqual(OBS.observer_exit_code({}, action_requested=False), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
