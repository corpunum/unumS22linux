#!/usr/bin/env python3
"""Read-only native input, power, and camera checks.

The event mode observes physical events but never writes/injects input.  The
It does not inject input, change display power, suspend, reboot, or alter USB.
"""
from __future__ import annotations

import argparse
import glob
import json
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


def inputs() -> list[dict[str, object]]:
    result = []
    for sysdev in sorted(glob.glob("/sys/class/input/event*")):
        name = read(Path(sysdev) / "device/name")
        event = "/dev/input/" + Path(sysdev).name
        result.append({"event": event, "name": name, "present": Path(event).exists()})
    return result


def power() -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for base in sorted(Path("/sys/class/power_supply").glob("*")):
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


def thermal() -> dict[str, dict[str, str]]:
    result = {}
    for base in sorted(Path("/sys/class/thermal").glob("thermal_zone*")):
        typ = read(base / "type")
        temp = read(base / "temp")
        if typ is not None or temp is not None:
            result[base.name] = {"type": typ or "", "temp_millidegrees": temp or ""}
    return result


def cameras() -> list[dict[str, str | bool]]:
    result = []
    for dev in sorted(glob.glob("/dev/video*")):
        name = read(Path("/sys/class/video4linux") / Path(dev).name / "name")
        result.append({"device": dev, "name": name or "", "present": True})
    return result


def observe(seconds: float) -> list[dict[str, int | str]]:
    handles = []
    try:
        for item in inputs():
            if not item["present"]:
                continue
            try:
                fd = os.open(str(item["event"]), os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
            except OSError:
                continue
            handles.append((fd, str(item["event"]), str(item["name"])))
        end = time.monotonic() + seconds
        events = []
        while time.monotonic() < end:
            ready, _, _ = select.select([x[0] for x in handles], [], [], .25)
            for fd, path, name in handles:
                if fd not in ready:
                    continue
                while True:
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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", type=float, metavar="SECONDS", help="observe physical input events")
    ap.add_argument("--camera", action="store_true", help="include read-only V4L2 node inventory")
    args = ap.parse_args()
    result: dict[str, object] = {"inputs": inputs(), "power": power(), "thermal": thermal()}
    if args.camera:
        result["cameras"] = cameras()
    if args.events is not None:
        if args.events < 0 or args.events > 300:
            ap.error("--events must be between 0 and 300 seconds")
        result["events"] = observe(args.events)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
