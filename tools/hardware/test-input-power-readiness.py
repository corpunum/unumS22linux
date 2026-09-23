#!/usr/bin/env python3
"""Host-only regression tests for input/power/camera readiness collection."""
from __future__ import annotations

import contextlib
import fcntl
import importlib.util
import io
import json
import os
from pathlib import Path
import struct
import tempfile
import time
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
        path.write_text(value, encoding="utf-8")
        return path

    def invoke(self, argv: list[str]) -> dict[str, object]:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = readiness.main(argv, sys_root=self.sys_root, dev_root=self.dev_root)
        self.assertEqual(result, 0)
        return json.loads(output.getvalue())

    def test_inventory_reads_input_power_thermal_and_camera_roots(self) -> None:
        self.put(self.sys_root, "class/input/event2/device/name", "sec_touchscreen\n")
        input_device = self.dev_root / "input/event2"
        input_device.parent.mkdir(parents=True, exist_ok=True)
        input_device.touch()

        self.put(self.sys_root, "class/power_supply/BAT0/capacity", "87\n")
        self.put(self.sys_root, "class/power_supply/BAT0/status", "Discharging\n")
        self.put(self.sys_root, "class/power_supply/BAT0/health", "Good\n")
        self.put(self.sys_root, "class/power_supply/usb/online", "0\n")
        self.put(self.sys_root, "class/power_supply/usb/type", "USB\n")
        self.put(self.sys_root, "class/thermal/thermal_zone3/type", "battery\n")
        self.put(self.sys_root, "class/thermal/thermal_zone3/temp", "32123\n")

        video_device = self.dev_root / "video0"
        video_device.parent.mkdir(parents=True, exist_ok=True)
        video_device.touch()
        self.put(self.sys_root, "class/video4linux/video0/name", "Exynos ISP\n")

        with mock.patch.object(readiness.os, "open",
                               side_effect=AssertionError("inventory opened a device")):
            result = self.invoke(["--camera"])
        self.assertEqual(result["inputs"], [{
            "event": str(input_device), "name": "sec_touchscreen", "present": True,
        }])
        self.assertEqual(result["power"], {
            "BAT0": {"capacity": "87", "health": "Good", "status": "Discharging"},
            "usb": {"online": "0", "type": "USB"},
        })
        self.assertEqual(result["thermal"], {
            "thermal_zone3": {"type": "battery", "temp_millidegrees": "32123"},
        })
        self.assertEqual(result["cameras"], [{
            "device": str(video_device), "name": "Exynos ISP", "present": True,
        }])

    def test_event_observation_is_bounded_read_only_and_does_not_write_or_grab(self) -> None:
        self.put(self.sys_root, "class/input/event0/device/name", "power-button\n")
        event_path = self.dev_root / "input/event0"
        event_path.parent.mkdir(parents=True, exist_ok=True)
        event_path.write_bytes(readiness.EVENT.pack(1700000000, 12345, 1, 116, 1))

        real_open = os.open
        opened: list[tuple[str, int, int]] = []
        descriptors: list[int] = []

        def read_only_open(path: str, flags: int, *args: object, **kwargs: object) -> int:
            self.assertEqual(flags & os.O_ACCMODE, os.O_RDONLY)
            fd = real_open(path, flags, *args, **kwargs)
            opened.append((path, flags, fd))
            descriptors.append(fd)
            return fd

        start = time.monotonic()
        with mock.patch.object(readiness.os, "open", side_effect=read_only_open), \
                mock.patch.object(readiness.os, "write", side_effect=AssertionError("event write")), \
                mock.patch.object(fcntl, "ioctl", side_effect=AssertionError("event grab")):
            result = self.invoke(["--events", "0.05"])
        elapsed = time.monotonic() - start

        self.assertLess(elapsed, 1.0)
        self.assertEqual(len(opened), 1)
        self.assertEqual(opened[0][0], str(event_path))
        self.assertEqual(result["events"], [{
            "device": str(event_path), "name": "power-button", "type": 1,
            "code": 116, "value": 1, "sec": 1700000000, "usec": 12345,
        }])
        for fd in descriptors:
            with self.assertRaises(OSError):
                os.fstat(fd)

    def test_event_duration_rejects_values_outside_finite_zero_to_300_range(self) -> None:
        for value in ("-0.1", "300.1", "nan", "inf"):
            with self.subTest(value=value), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    self.invoke(["--events", value])
                self.assertEqual(error.exception.code, 2)

        with self.assertRaises(ValueError):
            readiness.observe(300.1, self.sys_root, self.dev_root)

    def test_empty_event_inventory_waits_only_for_the_requested_duration(self) -> None:
        observed_timeouts: list[float | None] = []
        real_select = readiness.select.select

        def recording_select(readers: object, writers: object, errors: object,
                             timeout: float | None = None) -> tuple[list[object], ...]:
            observed_timeouts.append(timeout)
            return real_select(readers, writers, errors, timeout)

        start = time.monotonic()
        with mock.patch.object(readiness.select, "select", side_effect=recording_select):
            events = readiness.observe(0.05, self.sys_root, self.dev_root)
        elapsed = time.monotonic() - start

        self.assertEqual(events, [])
        self.assertTrue(observed_timeouts)
        self.assertLessEqual(observed_timeouts[0], 0.05)
        self.assertLess(elapsed, 0.5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
