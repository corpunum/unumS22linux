#!/usr/bin/env python3
"""Host-only tests for the optional, controlC0-only desktop exposure."""
from __future__ import annotations

import importlib.util
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("persistent_desktop", HERE / "start-persistent-desktop.py")
assert SPEC and SPEC.loader
desktop = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(desktop)


class OptionalAudioControlTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.devices = root / "sys/devices"
        card = self.devices / "platform/sound/sound/card0"
        control = card / "controlC0"
        control.mkdir(parents=True)
        (card / "id").write_text("RainbowPrince\n")
        (control / "dev").write_text("116:114\n")
        (control / "uevent").write_text("MAJOR=116\nMINOR=114\nDEVNAME=snd/controlC0\n")
        classes = root / "sys/class/sound"
        classes.mkdir(parents=True)
        (classes / "card0").symlink_to(card)
        (classes / "controlC0").symlink_to(control)
        self.card = classes / "card0"
        self.control = classes / "controlC0"
        self.sound_dir = root / "dev/snd"
        self.sound_dir.mkdir(parents=True)
        os.chmod(self.sound_dir, 0o777)
        self.node = self.sound_dir / "controlC0"
        self.node.touch()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _validate(self, node_info=None):
        real_lstat = Path.lstat
        sound_dir = self.sound_dir
        node = self.node

        def fake_lstat(path, *args, **kwargs):
            path = os.fspath(path)
            if path == os.fspath(sound_dir):
                return SimpleNamespace(st_mode=stat.S_IFDIR | 0o777, st_uid=0)
            if path == os.fspath(node) and node_info is not None:
                return node_info
            return real_lstat(Path(path), *args, **kwargs)

        with mock.patch.object(Path, "lstat", autospec=True, side_effect=fake_lstat):
            return desktop.validate_optional_audio_control(
                card_sysfs=self.card, control_sysfs=self.control,
                sysfs_devices=self.devices, sound_dir=self.sound_dir,
                control_node=self.node)

    def test_correct_class_symlinks_and_control_node(self):
        info = SimpleNamespace(st_mode=stat.S_IFCHR | 0o660, st_uid=0,
                               st_rdev=os.makedev(116, 114))
        self.assertEqual(self._validate(info), self.node)
        self.assertEqual(stat.S_IMODE(self.sound_dir.stat().st_mode), 0o755)

    def test_absent_control_node_fails_closed(self):
        self.node.unlink()
        with self.assertRaises(desktop.Failure):
            self._validate()

    def test_wrong_device_identity_fails_closed(self):
        info = SimpleNamespace(st_mode=stat.S_IFCHR | 0o660, st_uid=0,
                               st_rdev=os.makedev(116, 115))
        with self.assertRaises(desktop.Failure):
            self._validate(info)

    def test_symlink_control_node_fails_closed(self):
        self.node.unlink()
        self.node.symlink_to(self.sound_dir / "not-control")
        with self.assertRaises(desktop.Failure):
            self._validate()

    def test_wrong_sysfs_ancestry_fails_closed(self):
        wrong = self.devices / "platform/other/sound/sound/card0"
        wrong.mkdir(parents=True)
        (wrong / "id").write_text("RainbowPrince\n")
        with self.assertRaises(desktop.Failure):
            desktop.validate_optional_audio_control(
                card_sysfs=wrong, control_sysfs=self.control,
                sysfs_devices=self.devices, sound_dir=self.sound_dir,
                control_node=self.node, require_node=False)

    def test_world_writable_control_fails_closed(self):
        info = SimpleNamespace(st_mode=stat.S_IFCHR | 0o666, st_uid=0,
                               st_rdev=os.makedev(116, 114))
        with self.assertRaises(desktop.Failure):
            self._validate(info)

    def test_trigger_timeout_is_optional_and_narrow(self):
        calls = []
        with mock.patch.object(desktop, "validate_optional_audio_control",
                               side_effect=[None, self.node]), \
             mock.patch.object(desktop, "command",
                               side_effect=lambda *args, **kwargs: calls.append(args) or
                               (_ for _ in ()).throw(subprocess.TimeoutExpired(args[0], 30))):
            self.assertIsNone(desktop.prepare_optional_audio_control())
        self.assertEqual(calls, [
            ('udevadm', 'trigger', '--action=add', '--subsystem-match=sound',
             '--sysname-match=controlC0')])

    def test_late_card_polls_then_attempts_once(self):
        card = mock.Mock()
        control = mock.Mock()
        card.exists.side_effect = [False, True]
        control.exists.return_value = True
        node = self.node
        with mock.patch.object(desktop.time, "monotonic", side_effect=[0.0, 1.0]), \
             mock.patch.object(desktop.time, "sleep"), \
             mock.patch.object(desktop, "validate_optional_audio_control"), \
             mock.patch.object(desktop, "materialize_optional_audio_control", return_value=node) as materialize, \
             mock.patch.object(desktop, "command") as command:
            self.assertIsNone(desktop.late_prepare_optional_audio_control(
                deadline=10.0, card_sysfs=card, control_sysfs=control,
                sysfs_devices=self.devices, sound_dir=self.sound_dir,
                control_node=node))
            self.assertEqual(desktop.late_prepare_optional_audio_control(
                deadline=10.0, card_sysfs=card, control_sysfs=control,
                sysfs_devices=self.devices, sound_dir=self.sound_dir,
                control_node=node), node)
        self.assertEqual(command.call_args_list, [
            mock.call('udevadm', 'trigger', '--action=add', '--subsystem-match=sound',
                      '--sysname-match=controlC0'),
            mock.call('udevadm', 'settle', '--timeout=10')])
        materialize.assert_called_once()

    def test_late_card_timeout_does_not_attempt(self):
        card = mock.Mock(); control = mock.Mock()
        card.exists.return_value = False; control.exists.return_value = False
        with mock.patch.object(desktop.time, "monotonic", return_value=10.0), \
             mock.patch.object(desktop, "command") as command, \
             mock.patch.object(desktop, "materialize_optional_audio_control") as materialize:
            self.assertIsNone(desktop.late_prepare_optional_audio_control(
                deadline=10.0, card_sysfs=card, control_sysfs=control))
        command.assert_not_called(); materialize.assert_not_called()

    def test_late_unsafe_or_trigger_failure_stops_without_materialize(self):
        card = mock.Mock(); control = mock.Mock()
        card.exists.return_value = True; control.exists.return_value = True
        with mock.patch.object(desktop, "validate_optional_audio_control",
                               side_effect=desktop.Failure("unsafe")), \
             mock.patch.object(desktop, "materialize_optional_audio_control") as materialize, \
             mock.patch.object(desktop, "command") as command:
            self.assertIsNone(desktop.late_prepare_optional_audio_control(
                deadline=10.0, card_sysfs=card, control_sysfs=control))
        command.assert_not_called(); materialize.assert_not_called()

    def test_late_trigger_failure_stops_without_materialize(self):
        card = mock.Mock(); control = mock.Mock()
        card.exists.return_value = True; control.exists.return_value = True
        with mock.patch.object(desktop, "validate_optional_audio_control"), \
             mock.patch.object(desktop, "materialize_optional_audio_control") as materialize, \
             mock.patch.object(desktop, "command", side_effect=subprocess.CalledProcessError(1, "udevadm")):
            self.assertIsNone(desktop.late_prepare_optional_audio_control(
                deadline=10.0, card_sysfs=card, control_sysfs=control))
        materialize.assert_not_called()

    def test_materialize_missing_uses_only_control_identity(self):
        calls = []
        fake_stat = SimpleNamespace(st_dev=1, st_ino=2, st_uid=0,
                                    st_mode=stat.S_IFDIR | 0o777)
        dirfd_stat = SimpleNamespace(st_dev=1, st_ino=2, st_uid=0,
                                     st_mode=stat.S_IFDIR | 0o777)
        char_stat = SimpleNamespace(st_uid=0, st_mode=stat.S_IFCHR | 0o660,
                                    st_rdev=os.makedev(116, 114))
        with mock.patch.object(desktop, "validate_optional_audio_control",
                               side_effect=[None, self.node]), \
             mock.patch.object(Path, "lstat", return_value=fake_stat), \
             mock.patch.object(desktop.os, "open", return_value=9), \
             mock.patch.object(desktop.os, "fstat", return_value=dirfd_stat), \
             mock.patch.object(desktop.os, "stat", side_effect=[FileNotFoundError(), char_stat]), \
             mock.patch.object(desktop.os, "mknod", side_effect=lambda *a, **k: calls.append((a, k))), \
             mock.patch.object(desktop.os, "chown") as chown, \
             mock.patch.object(desktop.os, "fchmod") as fchmod, \
             mock.patch.object(desktop.os, "chmod") as chmod, \
             mock.patch.object(desktop.os, "close"):
            self.assertEqual(desktop.materialize_optional_audio_control(
                card_sysfs=self.card, control_sysfs=self.control,
                sysfs_devices=self.devices, sound_dir=self.sound_dir,
                control_node=self.node), self.node)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0][0], "controlC0")
        self.assertEqual(calls[0][0][2], os.makedev(116, 114))
        chown.assert_called_once()
        fchmod.assert_called_once_with(9, 0o755)
        self.assertTrue(any(call.args[0] == "controlC0" for call in chmod.call_args_list))

    def test_materialize_rejects_existing_regular_node(self):
        with mock.patch.object(desktop, "validate_optional_audio_control"), \
             self.assertRaises(desktop.Failure):
            desktop.materialize_optional_audio_control(
                card_sysfs=self.card, control_sysfs=self.control,
                sysfs_devices=self.devices, sound_dir=self.sound_dir,
                control_node=self.node)


if __name__ == "__main__":
    unittest.main()
