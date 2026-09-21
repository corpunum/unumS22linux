#!/usr/bin/env python3
"""Host fake-filesystem tests for audio-sync-sound-nodes.py."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import tempfile
import sys
import unittest

SOURCE = Path(__file__).with_name("audio-sync-sound-nodes.py")
SPEC = importlib.util.spec_from_file_location("audio_sync", SOURCE)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)

class AudioSyncTests(unittest.TestCase):
    def test_plan_sees_missing_control_and_pcm_without_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            devices = root / "devices"; devices.mkdir()
            target = devices / "soc" / "sound" / "controlC0"; target.mkdir(parents=True)
            (target / "dev").write_text("116:114")
            (target / "uevent").write_text("MAJOR=116\nMINOR=114\nDEVNAME=snd/controlC0\n")
            sysfs = root / "sys"; sysfs.mkdir()
            (sysfs / "controlC0").symlink_to(target)
            got = MODULE.entries(sysfs, devices, {"controlC0"})
            self.assertEqual(got, [("controlC0", 116, 114)])

    def test_wrong_existing_target_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sysfs = root / "sys"; dev = root / "dev"
            node = sysfs / "controlC0"; node.mkdir(parents=True)
            (node / "dev").write_text("116:114")
            (node / "uevent").write_text("MAJOR=116\nMINOR=114\n")
            devices = root / "devices"; devices.mkdir()
            self.assertRaises(SystemExit, MODULE.entries, sysfs, devices, {"controlC0"})

    def test_wrong_major_and_escaped_symlink_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); devices = root / "devices"; devices.mkdir()
            target = devices / "sound"; target.mkdir()
            (target / "dev").write_text("115:114")
            (target / "uevent").write_text("MAJOR=115\nMINOR=114\n")
            sysfs = root / "sys"; sysfs.mkdir(); (sysfs / "controlC0").symlink_to(target)
            self.assertRaises(SystemExit, MODULE.entries, sysfs, devices, {"controlC0"})
            outside = root / "outside"; outside.mkdir(); (outside / "dev").write_text("116:114")
            (outside / "uevent").write_text("MAJOR=116\nMINOR=114\n")
            (sysfs / "controlC0").unlink(); (sysfs / "controlC0").symlink_to(outside)
            other_root = root / "other"; other_root.mkdir()
            self.assertRaises(SystemExit, MODULE.entries, sysfs, other_root, {"controlC0"})

if __name__ == "__main__":
    unittest.main()
