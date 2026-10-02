#!/usr/bin/env python3
"""Host-only regressions for capability-based input/power readiness."""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock


SCRIPT = Path(__file__).with_name("input-power-readiness.py")
SPEC = importlib.util.spec_from_file_location("input_power_readiness", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise ImportError(f"could not load readiness probe at {SCRIPT}")
readiness = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(readiness)


class InputPowerReadinessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="input-power-readiness-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.sys_root = self.root / "sys"
        self.dev_root = self.root / "dev"

    def put(self, base: Path, relative: str, value: str) -> Path:
        path = base / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding="ascii")
        return path

    def add_input(self, number: int, name: str, *, ev: str = "3",
                  key: str | None = "0", dev: str = "13:64") -> Path:
        event = f"event{number}"
        self.put(self.sys_root, f"class/input/{event}/device/name", name + "\n")
        self.put(self.sys_root, f"class/input/{event}/device/capabilities/ev", ev + "\n")
        if key is not None:
            self.put(self.sys_root, f"class/input/{event}/device/capabilities/key", key + "\n")
        self.put(self.sys_root, f"class/input/{event}/dev", dev + "\n")
        node = self.dev_root / "input" / event
        node.parent.mkdir(parents=True, exist_ok=True)
        node.touch()
        return node

    def invoke(self, argv: list[str]) -> dict[str, object]:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = readiness.main(argv, sys_root=self.sys_root, dev_root=self.dev_root)
        self.assertEqual(result, 0)
        return json.loads(output.getvalue())

    def test_linux_input_sysfs_bitmap_word_order_and_width(self) -> None:
        # The pinned input.c prints unsigned-long words high-to-low. On the
        # live aarch64 target one word is 64 bits; bit 0 is the LSB of the
        # final word. The 32-bit form is covered independently.
        self.assertEqual(readiness.parse_sysfs_bitmap("8000000000000 0", word_bits=64),
                         {115})
        self.assertEqual(readiness.parse_sysfs_bitmap("14000000000000 0", word_bits=64),
                         {114, 116})
        self.assertEqual(readiness.parse_sysfs_bitmap("80000 0 0 0", word_bits=32),
                         {115})
        self.assertEqual(readiness.parse_sysfs_bitmap("0", word_bits=64), set())

    def test_malformed_or_noncanonical_bitmaps_fail_closed(self) -> None:
        for raw in ("", "0x1000", "1,,0", "0 1", "10000000000000000",
                    "1\n2", "1  0"):
            with self.subTest(raw=raw):
                with self.assertRaises(readiness.BitmapFormatError):
                    readiness.parse_sysfs_bitmap(raw, word_bits=64)
        with self.assertRaises(readiness.BitmapFormatError):
            readiness.parse_sysfs_bitmap("1000000000000000", word_bits=64, max_code=59)

    def test_inventory_reports_buttons_from_bits_not_names_and_remains_read_only(self) -> None:
        self.add_input(0, "gpio_keys", key="8000000000000 0")
        self.add_input(7, "sec_touchscreen", key="400 0 0 0 0 0")
        self.put(self.sys_root, "class/power_supply/BAT0/capacity", "87\n")
        self.put(self.sys_root, "class/power_supply/BAT0/status", "Discharging\n")
        self.put(self.sys_root, "class/thermal/thermal_zone3/type", "battery\n")
        self.put(self.sys_root, "class/thermal/thermal_zone3/temp", "32123\n")
        video = self.dev_root / "video0"
        video.parent.mkdir(parents=True, exist_ok=True)
        video.touch()
        self.put(self.sys_root, "class/video4linux/video0/name", "Exynos ISP\n")

        with mock.patch.object(readiness.os, "open",
                               side_effect=AssertionError("inventory opened a device")):
            result = self.invoke(["--camera"])

        inputs = {item["name"]: item for item in result["inputs"]}
        gpio = inputs["gpio_keys"]
        touch = inputs["sec_touchscreen"]
        self.assertEqual(gpio["button_capabilities"], {
            "KEY_POWER": False, "KEY_VOLUMEUP": True, "KEY_VOLUMEDOWN": False,
        })
        self.assertTrue(gpio["key_capable"])
        self.assertFalse(gpio["button_observation_candidate"])
        self.assertEqual(gpio["node_identity"]["status"], "not_character_device")
        self.assertEqual(gpio["capabilities"]["format"], readiness.BITMAP_FORMAT)
        self.assertEqual(gpio["capabilities"]["word_bits"], readiness.BITMAP_WORD_BITS)
        self.assertEqual(gpio["capabilities"]["word_order"], "most-significant-word-first")
        self.assertEqual(touch["button_capabilities"], {
            "KEY_POWER": False, "KEY_VOLUMEUP": False, "KEY_VOLUMEDOWN": False,
        })
        self.assertFalse(touch["button_observation_candidate"])
        self.assertEqual(result["power"], {
            "BAT0": {"capacity": "87", "status": "Discharging"},
        })
        self.assertEqual(result["thermal"], {
            "thermal_zone3": {"type": "battery", "temp_millidegrees": "32123"},
        })
        self.assertEqual(result["cameras"], [{
            "device": str(video), "name": "Exynos ISP", "present": True,
        }])

    def test_missing_invalid_or_name_only_capabilities_do_not_become_candidates(self) -> None:
        self.add_input(0, "sec-pmic-key", key=None)
        self.add_input(1, "power-button", key="0x1000")
        self.add_input(2, "keyboard", key="0")
        self.add_input(3, "key-without-EV_KEY", ev="1", key="10000000000000 0")
        result = readiness.inputs(self.sys_root, self.dev_root)
        by_name = {item["name"]: item for item in result}
        self.assertEqual(by_name["sec-pmic-key"]["capabilities"]["key"]["status"], "missing")
        self.assertIsNone(by_name["sec-pmic-key"]["button_capabilities"]["KEY_POWER"])
        self.assertEqual(by_name["power-button"]["capabilities"]["key"]["status"], "invalid")
        self.assertIsNone(by_name["power-button"]["button_capabilities"]["KEY_POWER"])
        self.assertFalse(by_name["key-without-EV_KEY"]["key_capable"])
        self.assertTrue(by_name["key-without-EV_KEY"]["button_capabilities"]["KEY_POWER"])
        for item in result:
            self.assertFalse(item["button_observation_candidate"])

    def test_event_node_identity_requires_sysfs_dev_and_character_device_match(self) -> None:
        node = self.root / "event-node"
        node.touch()
        self.assertEqual(readiness.node_identity(node, "13:64")["status"],
                         "not_character_device")
        self.assertEqual(readiness.node_identity(node, None)["status"],
                         "sysfs_dev_missing")
        self.assertEqual(readiness.node_identity(node, "bad")["status"],
                         "sysfs_dev_invalid")

    def test_event_window_rejects_unbounded_or_nonfinite_duration(self) -> None:
        for value in ("-0.1", "300.1", "nan", "inf"):
            with self.subTest(value=value), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    self.invoke(["--events", value])
                self.assertEqual(error.exception.code, 2)
        with self.assertRaises(ValueError):
            readiness.observe(300.1, self.sys_root, self.dev_root)


if __name__ == "__main__":
    unittest.main(verbosity=2)
