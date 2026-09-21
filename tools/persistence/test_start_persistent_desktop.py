#!/usr/bin/env python3
"""Host-only unit checks for start-persistent-desktop.py.

These tests deliberately replace every mount/device/process boundary.  They
must remain runnable on a development host and must never invoke --check or
--foreground against the real phone/root filesystem.
"""
import importlib.util
import json
import os
import signal
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SOURCE = Path(__file__).with_name("start-persistent-desktop.py")
SPEC = importlib.util.spec_from_file_location("persistent_desktop_under_test", SOURCE)
MOD = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MOD)


class SupervisorUnitTests(unittest.TestCase):
    def test_web_disabled_without_userdata_marker(self):
        with tempfile.TemporaryDirectory() as td, \
             mock.patch.object(MOD, 'MOUNT', Path(td)), \
             mock.patch.object(MOD, 'command') as command:
            MOD.optional_agent_web('--start')
            MOD.optional_agent_web('--stop')
            command.assert_not_called()

    def test_web_missing_helper_is_nonfatal(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root/'agent-web').mkdir()
            (root/'agent-web/enabled').touch()
            with mock.patch.object(MOD, 'MOUNT', root), \
                 mock.patch.object(MOD, 'command') as command:
                MOD.optional_agent_web('--start')
                command.assert_not_called()

    def test_wifi_disabled_by_default_and_explicit_marker(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            enabled, disabled = root / 'enabled', root / 'disabled'
            with mock.patch.object(MOD, 'WIFI_ENABLED', enabled), \
                 mock.patch.object(MOD, 'WIFI_DISABLED', disabled), \
                 mock.patch.object(MOD.subprocess, 'Popen') as popen:
                self.assertIsNone(MOD.start_optional_wifi())
                enabled.touch(); disabled.touch()
                self.assertIsNone(MOD.start_optional_wifi())
                popen.assert_not_called()

    def test_wifi_spawn_is_detached_and_failure_is_nonfatal(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            enabled = root / 'enabled'; enabled.touch()
            script = root / 'hardware/wifi-tools/wifi-autostart.py'
            script.parent.mkdir(parents=True); script.touch()
            private = root / 'hardware/wifi-private'; private.mkdir(mode=0o700)
            with mock.patch.object(MOD, 'MOUNT', root), \
                 mock.patch.object(MOD, 'WIFI_ENABLED', enabled), \
                 mock.patch.object(MOD, 'WIFI_DISABLED', root / 'disabled'), \
                 mock.patch.object(MOD.subprocess, 'Popen') as popen:
                self.assertIs(MOD.start_optional_wifi(), popen.return_value)
                self.assertEqual(popen.call_args.args[0], ['/usr/bin/python3', str(script), '--start'])
                self.assertTrue(popen.call_args.kwargs['start_new_session'])
                self.assertEqual(popen.call_args.kwargs['stdin'], subprocess.DEVNULL)
                popen.side_effect = OSError('fixture failure')
                self.assertIsNone(MOD.start_optional_wifi())
            self.assertEqual((private / 'autostart.log').stat().st_mode & 0o777, 0o600)

    def test_stride_trial_missing_library_fails_before_process_start(self):
        with tempfile.TemporaryDirectory() as td, \
             mock.patch.object(MOD, 'CHROOT', Path(td)), \
             mock.patch.dict(os.environ, {'S22_LINEAR_STRIDE_TRIAL': '1'}, clear=True), \
             mock.patch.object(MOD.subprocess, 'Popen') as popen:
            with self.assertRaises(MOD.Failure):
                MOD.start_desktop(Path(td) / 'desktop.log')
            popen.assert_not_called()

    def test_stride_trial_preload_is_opt_in_and_desktop_only(self):
        for marker, override, enabled in ((False, None, False), (False, '1', True),
                                          (True, None, True), (True, '0', False)):
            with self.subTest(marker=marker, override=override), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                (root / 'run').mkdir()
                (root / 'run/seatd.sock').touch()
                library = root / 'opt/s22-aquamarine/libs22-linear-stride.so'
                library.parent.mkdir(parents=True)
                library.touch()
                marker_path = root / 'stride-enabled'
                if marker:
                    marker_path.touch()
                env = {'S22_LINEAR_STRIDE_TRIAL': override} if override is not None else {}
                with mock.patch.object(MOD, 'CHROOT', root), \
                     mock.patch.object(MOD, 'STRIDE_ENABLED', marker_path), \
                     mock.patch.dict(os.environ, env, clear=True), \
                     mock.patch.object(MOD.subprocess, 'Popen') as popen:
                    MOD.start_desktop(root / 'desktop.log')
                seat_args = popen.call_args_list[0].args[0]
                desktop_args = popen.call_args_list[1].args[0]
                preload = 'LD_PRELOAD=/opt/s22-aquamarine/libs22-linear-stride.so'
                self.assertNotIn(preload, seat_args)
                self.assertEqual(preload in desktop_args, enabled)
                self.assertEqual('S22_LINEAR_STRIDE=1' in desktop_args, enabled)

    def test_bind_file_creates_regular_placeholder_and_mounts(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src = root / "null"
            dst = root / "candidate" / "dev" / "null"
            src.write_bytes(b"")
            calls = []
            with mock.patch.object(MOD, "is_mounted", return_value=False), \
                 mock.patch.object(MOD, "command", side_effect=lambda *a, **k: calls.append(a)):
                MOD.bind_file(src, dst)
            self.assertTrue(dst.is_file())
            self.assertEqual(calls, [("mount", "-o", "bind", str(src), str(dst))])

    def test_health_rejects_non_ok_json(self):
        class Response:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self): return json.dumps({"status": "loading"}).encode()
        opener = mock.Mock()
        opener.open.return_value = Response()
        with mock.patch.object(MOD.urllib.request, "build_opener", return_value=opener):
            self.assertFalse(MOD.healthy("http://127.0.0.1:8089/health"))

    def test_health_accepts_only_status_ok(self):
        class Response:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self): return b'{"status":"ok"}'
        opener = mock.Mock()
        opener.open.return_value = Response()
        with mock.patch.object(MOD.urllib.request, "build_opener", return_value=opener):
            self.assertTrue(MOD.healthy("http://127.0.0.1:8089/health"))

    def test_terminate_targets_only_owned_process_group(self):
        class Proc:
            pid = 4242
            def wait(self, timeout): return 0
        with mock.patch.object(MOD.os, "killpg") as killpg:
            MOD.terminate(Proc())
        self.assertEqual(killpg.call_args_list,
                         [mock.call(4242, signal.SIGTERM),
                          mock.call(4242, signal.SIGKILL)])

    def test_preflight_rejects_wrong_uuid_before_mount(self):
        mount = Path(tempfile.mkdtemp())
        with mock.patch.object(MOD, "MOUNT", mount), \
             mock.patch.object(MOD, "DEVICE", "/dev/block/by-name/userdata"), \
             mock.patch.object(MOD.Path, "read_text", return_value="native-guardian\n"), \
             mock.patch.object(MOD.os.path, "exists", return_value=True), \
             mock.patch.object(MOD.os.path, "islink", return_value=True), \
             mock.patch.object(MOD.os.path, "realpath", return_value="/dev/sda36"), \
             mock.patch.object(MOD, "stat_device", return_value=(259, 20)), \
             mock.patch.object(MOD, "partition_size_sectors", return_value=MOD.EXPECTED_SECTORS), \
             mock.patch.object(MOD, "filesystem_uuid", return_value="00000000-0000-0000-0000-000000000000"), \
             self.assertRaises(MOD.Failure):
            MOD.preflight()


if __name__ == "__main__":
    unittest.main()
