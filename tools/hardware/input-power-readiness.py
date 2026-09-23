#!/usr/bin/env python3
"""Read-only native input, power, and camera checks.

The event mode observes physical events but never writes/injects input.  The
It does not inject input, change display power, suspend, reboot, or alter USB.
"""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import select
import struct
import time

EVENT = struct.Struct("<qqHHi")


def read(path: Path) -> str | None:
    try:
        return path.read_text().strip()
    except OSError:
        return None


def inputs(sys_root: Path = Path("/sys"),
           dev_root: Path = Path("/dev")) -> list[dict[str, object]]:
    result = []
    for sysdev in sorted((sys_root / "class/input").glob("event*")):
        name = read(sysdev / "device/name")
        event = dev_root / "input" / sysdev.name
        result.append({"event": str(event), "name": name, "present": event.exists()})
    return result


def power(sys_root: Path = Path("/sys")) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for base in sorted((sys_root / "class/power_supply").glob("*")):
        if not base.is_dir():
            continue
        values = {}
        for key in ("capacity", "status", "health", "online", "voltage_now",
                    "current_now", "temp", "type", "scope"):
            value = read(base / key)
            if value is not None:
                values[key] = value
        result[base.name] = values
    return result


def thermal(sys_root: Path = Path("/sys")) -> dict[str, dict[str, str]]:
    result = {}
    for base in sorted((sys_root / "class/thermal").glob("thermal_zone*")):
        typ = read(base / "type")
        temp = read(base / "temp")
        if typ is not None or temp is not None:
            result[base.name] = {"type": typ or "", "temp_millidegrees": temp or ""}
    return result


def cameras(sys_root: Path = Path("/sys"),
            dev_root: Path = Path("/dev")) -> list[dict[str, str | bool]]:
    result = []
    for dev in sorted(dev_root.glob("video*")):
        name = read(sys_root / "class/video4linux" / dev.name / "name")
        result.append({"device": str(dev), "name": name or "", "present": True})
    return result


def observe(seconds: float, sys_root: Path = Path("/sys"),
            dev_root: Path = Path("/dev")) -> list[dict[str, int | str]]:
    if not math.isfinite(seconds) or seconds < 0 or seconds > 300:
        raise ValueError("seconds must be between 0 and 300")
    handles = []
    try:
        for item in inputs(sys_root, dev_root):
            if not item["present"]:
                continue
            try:
                fd = os.open(str(item["event"]), os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
            except OSError:
                continue
            handles.append((fd, str(item["event"]), str(item["name"])))
        end = time.monotonic() + seconds
        events = []
        while True:
            remaining = end - time.monotonic()
            if remaining <= 0:
                break
            ready, _, _ = select.select([x[0] for x in handles], [], [], min(.25, remaining))
            for fd, path, name in handles:
                if fd not in ready:
                    continue
                while time.monotonic() < end:
                    try:
                        data = os.read(fd, EVENT.size)
                    except BlockingIOError:
                        break
                    if len(data) != EVENT.size:
                        break
                    sec, usec, typ, code, value = EVENT.unpack(data)
                    events.append({"device": path, "name": name, "type": typ,
                                   "code": code, "value": value, "sec": sec, "usec": usec})
        return events
    finally:
        for fd, _, _ in handles:
            os.close(fd)


def main(argv: list[str] | None = None, *, sys_root: Path = Path("/sys"),
         dev_root: Path = Path("/dev")) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", type=float, metavar="SECONDS", help="observe physical input events")
    ap.add_argument("--camera", action="store_true", help="include read-only V4L2 node inventory")
    args = ap.parse_args(argv)
    result: dict[str, object] = {
        "inputs": inputs(sys_root, dev_root),
        "power": power(sys_root),
        "thermal": thermal(sys_root),
    }
    if args.camera:
        result["cameras"] = cameras(sys_root, dev_root)
    if args.events is not None:
        if not math.isfinite(args.events) or args.events < 0 or args.events > 300:
            ap.error("--events must be between 0 and 300 seconds")
        result["events"] = observe(args.events, sys_root, dev_root)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
