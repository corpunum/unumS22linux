#!/usr/bin/env python3
"""Host-only tests for audio-firmware-stage.py; no phone or subprocess calls."""
import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "audio_firmware_stage", ROOT / "tools/hardware/audio-firmware-stage.py")
MOD = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MOD)


class AudioFirmwareStageTests(unittest.TestCase):
    def test_real_manifest_matches_pinned_four(self):
        result = MOD.manifest()
        self.assertEqual(tuple(result), (
            "calliope_sram.bin", "calliope_dram.bin",
            "abox_tplg.bin", "abox_tplg.conf",
        ))
        self.assertEqual(result["calliope_sram.bin"]["bytes"], 165120)
        self.assertEqual(result["calliope_dram.bin"]["bytes"], 2204800)

    def test_manifest_rejects_mutated_artifact(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "calliope_sram.bin"
            path.write_bytes(b"wrong")
            files = {"calliope_sram.bin": (path, 165120,
                     MOD.FILES["calliope_sram.bin"][2])}
            with self.assertRaises(RuntimeError):
                MOD.manifest(files)

    def test_stage_script_is_exactly_firmware_only(self):
        data = {
            "a.bin": {"bytes": 3, "sha256": hashlib.sha256(b"abc").hexdigest()},
            "b.bin": {"bytes": 2, "sha256": hashlib.sha256(b"de").hexdigest()},
        }
        script = MOD.stage_script(data, tuple(data))
        self.assertIn("/proc/1/root/vendor/firmware", script)
        self.assertIn("native-guardian", script)
        self.assertNotIn("power/control", script)
        self.assertNotIn("unbind", script)
        self.assertNotIn("bind", script)
        self.assertNotIn("reboot", script)
        self.assertNotIn("/dev/block", script)
        self.assertNotIn("ioctl", script)
        self.assertIn("a.bin", script)
        self.assertIn("b.bin", script)

    def test_payload_order_follows_manifest_order(self):
        data = {
            "first": {"bytes": 1, "sha256": hashlib.sha256(b"x").hexdigest()},
            "second": {"bytes": 2, "sha256": hashlib.sha256(b"yz").hexdigest()},
        }
        script = MOD.stage_script(data, tuple(data))
        self.assertLess(script.index("'first'"), script.index("'second'"))
        self.assertIn("offset==len(blob)", script)


if __name__ == "__main__":
    unittest.main()
