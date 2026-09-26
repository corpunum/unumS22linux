#!/usr/bin/env python3
"""Bounded, passive camera inventory for the native S5E9925 system.

This script only reads fixed procfs/sysfs/device-tree metadata and stats fixed
device/firmware paths. It never opens a /dev/video* or /dev/media* node, issues
an ioctl, requests camera power, or captures frames. Presence is not readiness.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import stat
import sys
from typing import Any


VIDEO_LIMIT = 256
MEDIA_LIMIT = 256
MODULE_BYTES = 64 * 1024
ATTRIBUTE_BYTES = 512
DEVICE_TREE_BYTES = 4096
MODULE_NAME = re.compile(r"^[A-Za-z0-9_.+-]{1,96}$")
MODULE_PREFIXES = (
    "exynos_is", "exynos-is", "is_", "is-", "fimc-is", "s5k", "imx",
    "exynos_mfc", "exynos-mfc", "exynos_jpeg", "exynos-jpeg",
    "exynos_scaler", "exynos-scaler", "exynos_gdc", "exynos-gdc",
)
FIRMWARE_NAMES = (
    "setfile_gn3.bin", "setfile_2la.bin", "setfile_3k1.bin",
    "setfile_imx374.bin", "is_lib.bin", "is_rta.bin", "is_mcu_fw.bin",
)


def _read_bounded(path: Path, limit: int) -> tuple[bytes | None, str]:
    try:
        with path.open("rb") as stream:
            data = stream.read(limit + 1)
    except FileNotFoundError:
        return None, "missing"
    except OSError:
        return None, "unreadable"
    if len(data) > limit:
        return data[:limit], "truncated"
    return data, "ok"


def _text_attribute(path: Path, limit: int = ATTRIBUTE_BYTES) -> dict[str, str]:
    data, status = _read_bounded(path, limit)
    if data is None:
        return {"status": status}
    if status == "truncated":
        return {"status": status}
    try:
        value = data.decode("utf-8", "strict").strip("\x00\r\n \t")
    except UnicodeDecodeError:
        return {"status": "malformed"}
    if not value or any(ord(char) < 32 or ord(char) == 127 for char in value):
        return {"status": "malformed"}
    return {"status": "ok", "value": value[:160]}


def _device_tree_strings(path: Path) -> dict[str, Any]:
    data, status = _read_bounded(path, DEVICE_TREE_BYTES)
    if data is None:
        return {"status": status}
    if status == "truncated":
        return {"status": status}
    pieces = data.rstrip(b"\x00").split(b"\x00")
    if not pieces or any(not item for item in pieces):
        return {"status": "malformed"}
    if len(pieces) > 16:
        return {"status": "truncated"}
    try:
        values = [item.decode("utf-8", "strict") for item in pieces[:16]]
    except UnicodeDecodeError:
        return {"status": "malformed"}
    if any(len(item) > 160 or any(ord(char) < 32 or ord(char) == 127 for char in item)
           for item in values):
        return {"status": "malformed"}
    return {"status": "ok", "values": values}


def _node_stat(path: Path) -> dict[str, Any]:
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return {"status": "missing"}
    except OSError:
        return {"status": "unreadable"}
    if stat.S_ISCHR(info.st_mode):
        return {"status": "character_device", "major": os.major(info.st_rdev),
                "minor": os.minor(info.st_rdev)}
    if stat.S_ISLNK(info.st_mode):
        return {"status": "symlink"}
    if stat.S_ISDIR(info.st_mode):
        return {"status": "directory"}
    if stat.S_ISREG(info.st_mode):
        return {"status": "regular_file"}
    return {"status": "other"}


def _firmware_stat(path: Path) -> dict[str, Any]:
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return {"status": "missing"}
    except OSError:
        return {"status": "unreadable"}
    is_link = stat.S_ISLNK(info.st_mode)
    try:
        target = os.stat(path) if is_link else info
    except OSError:
        return {"status": "symlink_target_unavailable"}
    if stat.S_ISREG(target.st_mode):
        return {"status": "present", "kind": "symlink_to_file" if is_link else "file",
                "size_bytes": target.st_size}
    return {"status": "present_nonfile", "kind": "symlink" if is_link else "other"}


def _camera_modules(proc_root: Path) -> dict[str, Any]:
    data, status = _read_bounded(proc_root / "modules", MODULE_BYTES)
    if data is None:
        return {"status": status, "names": []}
    if status == "truncated":
        return {"status": status, "names": []}
    names: list[str] = []
    malformed_lines = 0
    for line in data.splitlines():
        fields = line.split()
        if not fields:
            continue
        try:
            name = fields[0].decode("ascii", "strict")
        except UnicodeDecodeError:
            malformed_lines += 1
            continue
        if MODULE_NAME.fullmatch(name) and name.lower().startswith(MODULE_PREFIXES):
            names.append(name)
    return {"status": status, "names": sorted(set(names))[:128],
            "malformed_lines": malformed_lines}


def _node_inventory(kind: str, sys_root: Path, dev_root: Path,
                    limit: int) -> list[dict[str, Any]]:
    if kind == "video":
        class_root = sys_root / "class/video4linux"
        names = ("name", "dev")
    else:
        class_root = sys_root / "class/media"
        names = ("model", "dev")
    found: list[dict[str, Any]] = []
    for index in range(limit):
        node_name = f"{kind}{index}"
        attributes = {name: _text_attribute(class_root / node_name / name)
                      for name in names}
        node = _node_stat(dev_root / node_name)
        if node["status"] == "missing" and all(
                value["status"] == "missing" for value in attributes.values()):
            continue
        found.append({"index": index, "device_node": node, "sysfs": attributes})
    return found


def inventory(*, sys_root: Path = Path("/sys"), proc_root: Path = Path("/proc"),
              dev_root: Path = Path("/dev"), dt_root: Path = Path("/proc/device-tree"),
              firmware_roots: tuple[Path, ...] =
              (Path("/vendor/firmware"), Path("/system/vendor/firmware"),
               Path("/proc/1/root/vendor/firmware"),
               Path("/proc/1/root/system/vendor/firmware"))) -> dict[str, Any]:
    return {
        "schema": "camera-readiness-inventory/v1",
        "assessment": "inventory_only",
        "readiness": "not_assessed",
        "capture_attempted": False,
        "device_nodes_opened": False,
        "ioctl_attempted": False,
        "camera_power_requested": False,
        "kernel_logs": {"status": "not_collected",
                        "reason": "private kernel traces are outside this inventory"},
        "limits": {"video_indices": f"0..{VIDEO_LIMIT - 1}",
                   "media_indices": f"0..{MEDIA_LIMIT - 1}",
                   "module_file_bytes": MODULE_BYTES,
                   "device_tree_property_bytes": DEVICE_TREE_BYTES},
        "kernel_modules": _camera_modules(proc_root),
        "device_tree": {
            "model": _device_tree_strings(dt_root / "model"),
            "compatible": _device_tree_strings(dt_root / "compatible"),
        },
        "video4linux": _node_inventory("video", sys_root, dev_root, VIDEO_LIMIT),
        "media": _node_inventory("media", sys_root, dev_root, MEDIA_LIMIT),
        "firmware_paths": {
            str(root): {name: _firmware_stat(root / name) for name in FIRMWARE_NAMES}
            for root in firmware_roots
        },
    }


def main() -> int:
    print(json.dumps(inventory(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
