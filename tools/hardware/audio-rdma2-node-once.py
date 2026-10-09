#!/usr/bin/env python3
"""Create only the reviewed RDMA2 playback node after exact sysfs checks.

This is a current-boot, one-node repair. It never scans /sys/dev, starts
ueventd/mdev, opens the PCM, or changes mixer state. The controller runs it
only after its candidate and durable-operation preflight.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import stat
from typing import Any, Callable


NODE_NAME = "pcmC0D2p"
NODE_PATH = "/dev/snd/pcmC0D2p"
MAJOR = 116
MINOR = 3
CARD_ID = "RainbowPrince"
KERNEL_RELEASE = "5.10.260-g4e5c5ad7d950"
EXPECTED_TARGET = "devices/platform/sound/sound/card0/pcmC0D2p"


def _need(condition: bool, reason: str) -> None:
    if not condition:
        raise RuntimeError(reason)


def _read_text(path: Path, limit: int = 1024) -> str:
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    _need(len(raw) <= limit, "bounded metadata file is too large")
    return raw.decode("ascii", "strict").strip()


def _sysfs_identity(sys_root: Path, proc_root: Path) -> dict[str, str]:
    _need(_read_text(proc_root / "1/comm", 64) == "native-guardian",
          "wrong native session")
    _need(_read_text(proc_root / "sys/kernel/osrelease", 128) == KERNEL_RELEASE,
          "unexpected native kernel")
    _need(_read_text(proc_root / "asound/card0/id", 64) == CARD_ID,
          "wrong ALSA card")
    _need(_read_text(proc_root / "asound/card0/pcm2p/sub0/status", 256) == "closed",
          "PCM is not closed")

    expected = sys_root / EXPECTED_TARGET
    class_node = sys_root / "class/sound" / NODE_NAME
    devchar = sys_root / "dev/char/116:3"
    _need(expected.is_dir(), "expected PCM sysfs target is missing")
    _need(class_node.resolve(strict=True) == expected,
          "sound-class link does not resolve to the reviewed PCM")
    _need(devchar.resolve(strict=True) == expected,
          "116:3 device link does not resolve to the reviewed PCM")
    _need(_read_text(expected / "dev", 32) == "116:3",
          "PCM sysfs dev number is not 116:3")

    raw = _read_text(expected / "uevent", 1024)
    values: dict[str, str] = {}
    for line in raw.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key in {"MAJOR", "MINOR", "DEVNAME", "DEVTYPE"}:
            _need(key not in values, "duplicate identity field in PCM uevent")
            values[key] = value
    wanted = {"MAJOR": "116", "MINOR": "3", "DEVNAME": "snd/pcmC0D2p",
              "DEVTYPE": "pcm"}
    _need(all(values.get(key) == value for key, value in wanted.items()),
          "PCM uevent identity mismatch")
    return {"card_id": CARD_ID, "pcm_status": "closed",
            "sysfs_class_target": str(class_node.resolve(strict=True)),
            "sysfs_devchar_target": str(devchar.resolve(strict=True)),
            "dev": "116:3", "devname": values["DEVNAME"],
            "devtype": values["DEVTYPE"]}


def _validate_node(info: Any, *, expected_uid: int, expected_gid: int) -> None:
    _need(stat.S_ISCHR(info.st_mode), "existing PCM path is not a character device")
    _need(info.st_rdev == os.makedev(MAJOR, MINOR),
          "existing PCM character device has the wrong identity")
    _need(info.st_uid == expected_uid and info.st_gid == expected_gid,
          "PCM node ownership is not root:root")
    _need(stat.S_IMODE(info.st_mode) == 0o600,
          "PCM node mode is not the diagnostic-only 0600")


def provision_node(*, sys_root: Path = Path("/sys"),
                   proc_root: Path = Path("/proc"),
                   dev_root: Path = Path("/dev"), apply: bool,
                   expected_uid: int = 0, expected_gid: int = 0,
                   mknod_fn: Callable[..., Any] = os.mknod,
                   chown_fn: Callable[..., Any] = os.chown,
                   chmod_fn: Callable[..., Any] = os.chmod,
                   stat_fn: Callable[..., Any] = os.stat) -> dict[str, Any]:
    """Validate the exact ALSA source and optionally create one missing node.

    The syscall parameters are injectable solely for fake-filesystem tests.
    Production invocation uses the defaults and fixed /proc, /sys, and /dev.
    """
    identity = _sysfs_identity(sys_root, proc_root)
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
    dev_fd = os.open(dev_root, flags)
    try:
        dev_info = os.fstat(dev_fd)
        _need(stat.S_ISDIR(dev_info.st_mode) and dev_info.st_uid == expected_uid
              and not (stat.S_IMODE(dev_info.st_mode) & 0o022),
              "/dev is not a protected root-owned directory")
        snd_fd = os.open("snd", flags, dir_fd=dev_fd)
        try:
            snd_info = os.fstat(snd_fd)
            _need(stat.S_ISDIR(snd_info.st_mode)
                  and snd_info.st_uid == expected_uid
                  and snd_info.st_gid == expected_gid
                  and stat.S_IMODE(snd_info.st_mode) == 0o755
                  and snd_info.st_dev == dev_info.st_dev,
                  "/dev/snd is not the reviewed root-owned directory")
            try:
                existing = stat_fn(NODE_NAME, dir_fd=snd_fd,
                                   follow_symlinks=False)
            except FileNotFoundError:
                existing = None
            if existing is not None:
                _validate_node(existing, expected_uid=expected_uid,
                               expected_gid=expected_gid)
                return {**identity, "path": NODE_PATH, "major": MAJOR,
                        "minor": MINOR, "uid": expected_uid,
                        "gid": expected_gid, "mode": "0600",
                        "created": False, "state": "already_present_exact"}
            if not apply:
                return {**identity, "path": NODE_PATH, "major": MAJOR,
                        "minor": MINOR, "uid": expected_uid,
                        "gid": expected_gid, "mode": "0600",
                        "created": False, "state": "missing"}

            # mknod is atomic with respect to the basename: if a path appears
            # after the absent check, EEXIST is an error and nothing is replaced.
            device = os.makedev(MAJOR, MINOR)
            mknod_fn(NODE_NAME, stat.S_IFCHR | 0o600, device, dir_fd=snd_fd)
            chown_fn(NODE_NAME, expected_uid, expected_gid, dir_fd=snd_fd,
                     follow_symlinks=False)
            chmod_fn(NODE_NAME, 0o600, dir_fd=snd_fd)
            created = stat_fn(NODE_NAME, dir_fd=snd_fd, follow_symlinks=False)
            _validate_node(created, expected_uid=expected_uid,
                           expected_gid=expected_gid)
            return {**identity, "path": NODE_PATH, "major": MAJOR,
                    "minor": MINOR, "uid": expected_uid,
                    "gid": expected_gid, "mode": "0600",
                    "created": True, "state": "created"}
        finally:
            os.close(snd_fd)
    finally:
        os.close(dev_fd)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="create only missing /dev/snd/pcmC0D2p")
    args = parser.parse_args()
    if not args.apply:
        parser.error("explicit --apply is required; the controller owns the preflight")
    if os.geteuid() != 0:
        raise SystemExit("node provision requires native root")
    result = provision_node(apply=True)
    print(json.dumps({"schema": "audio-rdma2-node/v1", **result}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
