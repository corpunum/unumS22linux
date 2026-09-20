#!/usr/bin/env python3
"""Non-grabbing physical power-key -> Hyprland DPMS toggle helper.

It observes KEY_POWER (code 116) from the matching event node and sends only
Hyprland's DPMS toggle. It does not grab the device, suspend, reboot, or write
any input node, so kernel long-press/boot behavior remains outside this helper.
"""
from __future__ import annotations

import argparse
import glob
import os
import select
import struct
import subprocess
import time
from pathlib import Path

EVENT = struct.Struct("<qqHHi")
KEY_POWER = 116


def find_power_events() -> list[str]:
    result = []
    for sysdev in sorted(glob.glob("/sys/class/input/event*")):
        try:
            name = (Path(sysdev) / "device/name").read_text().strip().lower()
        except OSError:
            continue
        if any(word in name for word in ("power", "gpio", "sec_key", "keyboard")):
            result.append("/dev/input/" + Path(sysdev).name)
    return [x for x in result if os.path.exists(x)]


def toggle(hyprctl: str) -> None:
    subprocess.run([hyprctl, "dispatch", 'hl.dsp.dpms({ action = "toggle" })'],
                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL, check=False)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--event", action="append", help="explicit event node; repeatable")
    ap.add_argument("--hyprctl", default="hyprctl")
    ap.add_argument("--seconds", type=float, default=0,
                    help="bounded observation duration; 0 means daemon mode")
    args = ap.parse_args()
    if args.seconds < 0 or args.seconds > 86400:
        ap.error("--seconds must be between 0 and 86400")
    paths = args.event or find_power_events()
    if not paths:
        raise SystemExit("no candidate power-key event node found")
    fds = []
    try:
        for path in paths:
            fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
            fds.append(fd)
        end = time.monotonic() + args.seconds if args.seconds else None
        last = 0.0
        while end is None or time.monotonic() < end:
            ready, _, _ = select.select(fds, [], [], .25)
            for fd in ready:
                while True:
                    try:
                        data = os.read(fd, EVENT.size)
                    except BlockingIOError:
                        break
                    if len(data) != EVENT.size:
                        break
                    _, _, typ, code, value = EVENT.unpack(data)
                    if typ == 1 and code == KEY_POWER and value == 1 and time.monotonic() - last >= 1:
                        toggle(args.hyprctl)
                        last = time.monotonic()
        return 0
    finally:
        for fd in fds:
            os.close(fd)


if __name__ == "__main__":
    raise SystemExit(main())
