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

    def test_openunum_autostart_is_opt_in(self):
        with tempfile.TemporaryDirectory() as td, \
             mock.patch.object(MOD, 'OPENUNUM_AUTOSTART', Path(td) / 'autostart'), \
             mock.patch.object(MOD.subprocess, 'Popen') as popen:
            self.assertIsNone(MOD.start_optional_openunum())
            popen.assert_not_called()

    def test_openunum_autostart_detached_and_checks_ownership(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); marker = root / 'autostart'; marker.write_text('')
            control = root / 's22-openunum'; control.write_text('#!/bin/sh\n'); control.chmod(0o755)
            with mock.patch.object(MOD, 'OPENUNUM_AUTOSTART', marker), \
                 mock.patch.object(MOD, 'OPENUNUM_CONTROL', control), \
                 mock.patch.object(MOD.subprocess, 'Popen') as popen:
                if os.getuid() == 0:
                    self.assertIs(MOD.start_optional_openunum(), popen.return_value)
                    self.assertEqual(popen.call_args.args[0], ['/bin/sh', str(control), 'start'])
                    self.assertTrue(popen.call_args.kwargs['start_new_session'])
                else:   # not root-owned: refused, and the refusal is non-fatal
                    self.assertIsNone(MOD.start_optional_openunum())
                    popen.assert_not_called()
                control.chmod(0o777)
                popen.reset_mock()
                self.assertIsNone(MOD.start_optional_openunum())
                popen.assert_not_called()

    def test_keepalive_is_opt_in_and_checks_ownership(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); marker = root / 'enabled'
            daemon = root / 's22-keepalive'; daemon.write_text('#!/usr/bin/python3\n'); daemon.chmod(0o755)
            with mock.patch.object(MOD, 'KEEPALIVE_ENABLED', marker), \
                 mock.patch.object(MOD, 'KEEPALIVE_DAEMON', daemon), \
                 mock.patch.object(MOD.subprocess, 'Popen') as popen:
                self.assertIsNone(MOD.start_optional_keepalive())
                popen.assert_not_called()
                marker.write_text('')
                if os.getuid() == 0:
                    self.assertIs(MOD.start_optional_keepalive(), popen.return_value)
                    self.assertEqual(popen.call_args.args[0], ['/usr/bin/python3', str(daemon)])
                    self.assertTrue(popen.call_args.kwargs['start_new_session'])
                else:
                    self.assertIsNone(MOD.start_optional_keepalive())
                    popen.assert_not_called()
                daemon.chmod(0o777)
                popen.reset_mock()
                self.assertIsNone(MOD.start_optional_keepalive())
                popen.assert_not_called()

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

    def test_desktop_profile_default_valid_and_unknown(self):
        with tempfile.TemporaryDirectory() as td:
            sel = Path(td) / 's22-desktop'
            self.assertEqual(MOD.selected_desktop(sel), 'hyprland')
            sel.write_text('sway-pixman\n')
            self.assertEqual(MOD.selected_desktop(sel), 'sway-pixman')
            sel.write_text('phosh\n')
            with mock.patch.object(MOD, 'say'):
                self.assertEqual(MOD.selected_desktop(sel), 'hyprland')

    def test_sway_pixman_env_has_no_gl_or_stride_preload(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / 'run').mkdir()
            (root / 'run/seatd.sock').touch()
            (root / 'usr/bin').mkdir(parents=True)
            (root / 'usr/bin/sway').touch()
            library = root / 'opt/s22-aquamarine/libs22-linear-stride.so'
            library.parent.mkdir(parents=True)
            library.touch()
            with mock.patch.object(MOD, 'CHROOT', root), \
                 mock.patch.dict(os.environ, {'S22_LINEAR_STRIDE_TRIAL': '1'}, clear=True), \
                 mock.patch.object(MOD.subprocess, 'Popen') as popen:
                MOD.start_desktop(root / 'desktop.log', 'sway-pixman')
            args = popen.call_args_list[1].args[0]
            self.assertIn('WLR_RENDERER=pixman', args)
            self.assertIn('WLR_DRM_DEVICES=/dev/dri/card1', args)
            self.assertIn('LIBSEAT_BACKEND=seatd', args)
            self.assertIn('/usr/bin/sway', args)
            self.assertIn('LD_LIBRARY_PATH=/usr/lib', args)
            self.assertNotIn('/usr/bin/Hyprland', args)
            for banned in ('LD_PRELOAD', 'GALLIUM_DRIVER', 'LP_NUM_THREADS', 'AQ_DRM_DEVICES'):
                self.assertFalse(any(a.startswith(banned + '=') for a in args), banned)

    def test_sway_pixman_uses_wlroots_fix_when_installed(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / 'run').mkdir()
            (root / 'run/seatd.sock').touch()
            (root / 'usr/bin').mkdir(parents=True)
            (root / 'usr/bin/sway').touch()
            (root / 'opt/s22-wlroots').mkdir(parents=True)
            (root / 'opt/s22-wlroots/libwlroots-0.20.so').touch()
            with mock.patch.object(MOD, 'CHROOT', root), \
                 mock.patch.dict(os.environ, {}, clear=True), \
                 mock.patch.object(MOD.subprocess, 'Popen') as popen:
                MOD.start_desktop(root / 'desktop.log', 'sway-pixman')
            self.assertIn('LD_LIBRARY_PATH=/opt/s22-wlroots:/usr/lib', popen.call_args_list[1].args[0])

    def test_sway_pixman_missing_binary_fails_before_process_start(self):
        with tempfile.TemporaryDirectory() as td, \
             mock.patch.object(MOD, 'CHROOT', Path(td)), \
             mock.patch.object(MOD.subprocess, 'Popen') as popen:
            with self.assertRaises(MOD.Failure):
                MOD.start_desktop(Path(td) / 'desktop.log', 'sway-pixman')
            popen.assert_not_called()

    def test_sway_failure_falls_back_to_hyprland(self):
        calls = []

        def fake_start(log, desktop='hyprland'):
            calls.append(desktop)
            return mock.Mock(name='seat'), mock.Mock(name=desktop)

        def fake_ready(proc, desktop):
            if desktop == 'sway-pixman':
                raise MOD.Failure('sway exited before readiness')

        with tempfile.TemporaryDirectory() as td, \
             mock.patch.object(MOD, 'CHROOT', Path(td)), \
             mock.patch.object(MOD, 'start_desktop', side_effect=fake_start), \
             mock.patch.object(MOD, 'wait_desktop_ready', side_effect=fake_ready), \
             mock.patch.object(MOD, 'terminate') as term, \
             mock.patch.object(MOD, 'say'):
            (Path(td) / 'run/user/0').mkdir(parents=True)
            seat, proc, used = MOD.start_desktop_with_fallback(Path(td) / 'd.log', 'sway-pixman')
        self.assertEqual(calls, ['sway-pixman', 'hyprland'])
        self.assertEqual(used, 'hyprland')
        self.assertEqual(term.call_count, 2)

    def test_hyprland_failure_is_not_retried(self):
        with tempfile.TemporaryDirectory() as td, \
             mock.patch.object(MOD, 'start_desktop', return_value=(mock.Mock(), mock.Mock())) as start, \
             mock.patch.object(MOD, 'wait_desktop_ready', side_effect=MOD.Failure('x')), \
             mock.patch.object(MOD, 'terminate'):
            with self.assertRaises(MOD.Failure):
                MOD.start_desktop_with_fallback(Path(td) / 'd.log', 'hyprland')
        self.assertEqual(start.call_count, 1)

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

    def test_audio_firmware_staging_copies_only_missing_files(self):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / 'src'; dst = Path(td) / 'dst'
            src.mkdir(); dst.mkdir()
            (src / 'sectiongraph_tplg.bin').write_bytes(b'graph')
            (src / 'abox_tplg.bin').write_bytes(b'new')
            (dst / 'abox_tplg.bin').write_bytes(b'ramdisk')
            (src / 'link.bin').symlink_to(src / 'abox_tplg.bin')
            with mock.patch.object(MOD, 'say'):
                self.assertEqual(MOD.stage_optional_audio_firmware(src, dst), 1)
                self.assertEqual(MOD.stage_optional_audio_firmware(src, dst), 0)
                self.assertEqual(MOD.stage_optional_audio_firmware(src / 'absent', dst), 0)
            self.assertEqual((dst / 'sectiongraph_tplg.bin').read_bytes(), b'graph')
            self.assertEqual((dst / 'abox_tplg.bin').read_bytes(), b'ramdisk')
            self.assertFalse((dst / 'link.bin').exists())
            self.assertEqual(sorted(x.name for x in dst.iterdir()),
                             ['abox_tplg.bin', 'sectiongraph_tplg.bin'])

    def test_firmware_fallback_answers_only_known_audio_names(self):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / 'src'; req = Path(td) / 'firmware'
            src.mkdir(); req.mkdir()
            (src / 'sectiongraph_tplg.bin').write_bytes(b'graph-bytes')
            for name in ('sectiongraph_tplg.bin', 'mfc!mfc_fw_flash.bin', 'unknown.bin'):
                (req / name).mkdir()
                (req / name / 'loading').write_text('')
                (req / name / 'data').write_bytes(b'')
            (req / 'timeout').write_text('60')
            with mock.patch.object(MOD, 'say'):
                served = MOD.answer_audio_firmware_requests(src, req, seconds=0.05, interval=0.01)
            self.assertEqual(served, ['sectiongraph_tplg.bin'])
            self.assertEqual((req / 'sectiongraph_tplg.bin/data').read_bytes(), b'graph-bytes')
            self.assertEqual((req / 'sectiongraph_tplg.bin/loading').read_text(), '0\n')
            self.assertEqual((req / 'mfc!mfc_fw_flash.bin/loading').read_text(), '')
            self.assertEqual((req / 'unknown.bin/loading').read_text(), '')

    def test_llvmpipe_threads_default_and_override(self):
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / 'threads'
            self.assertEqual(MOD.llvmpipe_threads(f), '0')
            f.write_text('4\n'); self.assertEqual(MOD.llvmpipe_threads(f), '4')
            f.write_text('lots'); self.assertEqual(MOD.llvmpipe_threads(f), '0')
            f.write_text('99'); self.assertEqual(MOD.llvmpipe_threads(f), '0')

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
