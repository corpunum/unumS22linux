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


if __name__ == "__main__":
    unittest.main()
