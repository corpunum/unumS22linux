#!/usr/bin/env python3
"""Hardware-free tests for the passive camera inventory."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock


SCRIPT = Path(__file__).with_name("camera-readiness-once.py")
SPEC = importlib.util.spec_from_file_location("camera_readiness_once", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise ImportError(f"could not load camera inventory at {SCRIPT}")
camera = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(camera)


class CameraReadinessInventoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="camera-inventory-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.sys_root = self.root / "sys"
        self.proc_root = self.root / "proc"
        self.dev_root = self.root / "dev"
        self.dt_root = self.proc_root / "device-tree"
        self.firmware_root = self.root / "vendor/firmware"

    def put(self, base: Path, relative: str, value: bytes | str) -> Path:
        path = base / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(value, bytes):
            path.write_bytes(value)
        else:
            path.write_text(value, encoding="utf-8")
        return path

    def run_inventory(self) -> dict[str, object]:
        return camera.inventory(
            sys_root=self.sys_root, proc_root=self.proc_root,
            dev_root=self.dev_root, dt_root=self.dt_root,
            firmware_roots=(self.firmware_root, self.root / "system/vendor/firmware"),
        )

    def test_inventory_is_bounded_and_never_opens_device_nodes(self) -> None:
        video = self.dev_root / "video0"
        video.parent.mkdir(parents=True, exist_ok=True)
        video.touch()
        self.put(self.sys_root, "class/video4linux/video0/name", "Exynos ISP\n")
        self.put(self.sys_root, "class/video4linux/video0/dev", "81:0\n")
        video_late = self.dev_root / "video110"
        video_late.touch()
        self.put(self.sys_root, "class/video4linux/video110/name", "Exynos sensor leader\n")
        media = self.dev_root / "media0"
        media.touch()
        self.put(self.sys_root, "class/media/media0/model", "Exynos IS\n")
        self.put(self.sys_root, "class/media/media0/dev", "241:0\n")
        self.put(self.proc_root, "modules", "fimc_is 222 0 - Live 0x9876\n"
                 "exynos_is 123 0 - Live 0x1234\n"
                 "s5k_gn3 321 0 - Live 0x5678\nordinary 1 0 - Live 0x9abc\n")
        self.put(self.dt_root, "model", b"Samsung r0s\x00")
        self.put(self.dt_root, "compatible", b"samsung,r0s\x00samsung,s5e9925\x00")
        self.put(self.firmware_root, "setfile_gn3.bin", b"fixture firmware bytes")
        self.put(self.firmware_root, "is_lib.bin", b"fixture library bytes")
        self.put(self.firmware_root, "actual.bin", b"not inventoried")
        (self.firmware_root / "setfile_2la.bin").symlink_to("actual.bin")

        real_lstat = os.lstat
        opened: list[Path] = []

        def guarded_lstat(path: str | os.PathLike[str]) -> os.stat_result:
            if Path(path).parent == self.dev_root:
                opened.append(Path(path))
            return real_lstat(path)

        real_read = camera._read_bounded
        real_path_open = Path.open

        def guarded_read(path: Path, limit: int) -> tuple[bytes | None, str]:
            self.assertFalse(path == self.dev_root or self.dev_root in path.parents,
                             "device-node path was opened for reading")
            return real_read(path, limit)

        def guarded_path_open(path: Path, *args: object, **kwargs: object):
            self.assertFalse(path == self.dev_root or self.dev_root in path.parents,
                             "Path.open targeted a device node")
            return real_path_open(path, *args, **kwargs)

        with mock.patch.object(camera.os, "open",
                               side_effect=AssertionError("os.open called")), \
                mock.patch.object(camera.os, "lstat", side_effect=guarded_lstat), \
                mock.patch.object(Path, "open", new=guarded_path_open), \
                mock.patch.object(camera, "_read_bounded", side_effect=guarded_read):
            result = self.run_inventory()

        self.assertEqual(len(opened), camera.VIDEO_LIMIT + camera.MEDIA_LIMIT)
        self.assertIn(video, opened)
        self.assertIn(video_late, opened)
        self.assertIn(media, opened)
        self.assertEqual(result["assessment"], "inventory_only")
        self.assertEqual(result["readiness"], "not_assessed")
        self.assertFalse(result["capture_attempted"])
        self.assertFalse(result["device_nodes_opened"])
        self.assertFalse(result["ioctl_attempted"])
        self.assertFalse(result["camera_power_requested"])
        self.assertEqual(result["kernel_modules"]["names"], ["exynos_is", "fimc_is", "s5k_gn3"])
        self.assertEqual(result["device_tree"]["model"],
                         {"status": "ok", "values": ["Samsung r0s"]})
        self.assertEqual(result["video4linux"][0]["sysfs"]["name"]["value"], "Exynos ISP")
        self.assertEqual(result["video4linux"][1]["index"], 110)
        self.assertEqual(result["video4linux"][1]["sysfs"]["name"]["value"],
                         "Exynos sensor leader")
        self.assertEqual(result["media"][0]["sysfs"]["model"]["value"], "Exynos IS")
        firmware = result["firmware_paths"][str(self.firmware_root)]
        self.assertEqual(firmware["setfile_gn3.bin"]["status"], "present")
        self.assertEqual(firmware["setfile_2la.bin"]["kind"], "symlink_to_file")
        self.assertEqual(firmware["setfile_2la.bin"]["size_bytes"], len(b"not inventoried"))
        self.assertEqual(firmware["setfile_3k1.bin"]["status"], "missing")
        self.assertEqual(result["kernel_logs"]["status"], "not_collected")
        serialized = json.dumps(result).lower()
        for forbidden in ("sensor_id", "otp", "calibration", "actual.bin", "1234", "5678"):
            self.assertNotIn(forbidden, serialized)

    def test_missing_roots_are_reported_without_claiming_readiness(self) -> None:
        result = self.run_inventory()
        self.assertEqual(result["readiness"], "not_assessed")
        self.assertEqual(result["kernel_modules"], {"status": "missing", "names": []})
        self.assertEqual(result["device_tree"]["model"], {"status": "missing"})
        self.assertEqual(result["video4linux"], [])
        self.assertEqual(result["media"], [])

    def test_malformed_and_truncated_inputs_fail_closed_per_field(self) -> None:
        self.put(self.dt_root, "model", b"bad-utf8-\xff\x00")
        self.put(self.dt_root, "compatible", b"A" * (camera.DEVICE_TREE_BYTES + 1))
        self.put(self.sys_root, "class/video4linux/video1/name", "N" * 513)
        self.put(self.proc_root, "modules", b"exynos_is 1 0 - Live 0x1\n" +
                 b"x" * camera.MODULE_BYTES)

        result = self.run_inventory()
        self.assertEqual(result["device_tree"]["model"], {"status": "malformed"})
        self.assertEqual(result["device_tree"]["compatible"], {"status": "truncated"})
        self.assertEqual(result["kernel_modules"]["status"], "truncated")
        video1 = next(item for item in result["video4linux"] if item["index"] == 1)
        self.assertEqual(video1["sysfs"]["name"], {"status": "truncated"})

    def test_symlink_device_node_is_only_lstat_reported_and_index_range_is_fixed(self) -> None:
        self.dev_root.mkdir(parents=True, exist_ok=True)
        (self.dev_root / "video0").symlink_to("/some/other/device")
        self.put(self.sys_root, "class/video4linux/video0/name", "node link\n")
        self.put(self.sys_root, "class/video4linux/video256/name", "outside range\n")
        self.put(self.sys_root, "class/media/media256/model", "outside range\n")

        result = self.run_inventory()
        self.assertEqual(result["video4linux"], [{
            "index": 0,
            "device_node": {"status": "symlink"},
            "sysfs": {"name": {"status": "ok", "value": "node link"},
                      "dev": {"status": "missing"}},
        }])
        self.assertEqual(result["media"], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
