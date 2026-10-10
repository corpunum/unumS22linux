#!/usr/bin/env python3
"""Input-to-panel latency on the S22 (native root).

Injects a single-finger tap into the real touchscreen evdev node and times
the first and second scanout fb changes on plane-0 after touch-down. On an
idle compositor the first flip is the response frame; on a saturated one
(continuous redraw, e.g. Hyprland/llvmpipe) the response is in the first or
second flip, so the true latency lies between the two numbers. Run it on a screen that is otherwise
idle (check p2-measure.py first: idle panel_fps should be ~0).

usage: p2-tap-latency.py X Y [REPS] [--gap S] [--hold MS]
  X, Y normalised 0..1 (portrait). Default 8 reps, 1.5 s apart, 60 ms hold.
Prints one JSON line with per-tap latencies (ms; null = no frame in 3 s).
"""
from __future__ import annotations

import json
import os
import re
import statistics
import struct
import sys
import time

DEV = '/dev/input/event7'          # sec_touchscreen
STATE = '/sys/kernel/debug/dri/1/state'
PLANE = re.compile(r'plane-0\n\tcrtc=\S+\n\tfb=(\d+)')
EV = struct.Struct('<qqHHi')
M = 4095


def fb() -> str | None:
    with open(STATE) as f:
        m = PLANE.search(f.read())
    return m.group(1) if m else None


def main() -> int:
    a = sys.argv[1:]
    if len(a) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    x, y = float(a[0]), float(a[1])
    reps = int(a[2]) if len(a) > 2 and not a[2].startswith('--') else 8
    gap = float(a[a.index('--gap') + 1]) if '--gap' in a else 1.5
    hold = float(a[a.index('--hold') + 1]) if '--hold' in a else 60
    fd = os.open(DEV, os.O_WRONLY)

    def ev(t, c, v):
        now = time.time()
        os.write(fd, EV.pack(int(now), int((now % 1) * 1e6), t, c, v))

    lat, lat2 = [], []
    try:
        for i in range(reps):
            base = fb()
            tid = (int(time.time()) + i) % 30000
            t0 = time.monotonic()
            ev(3, 0x2f, 0); ev(3, 0x39, tid); ev(3, 0x35, int(x * M)); ev(3, 0x36, int(y * M))
            ev(1, 0x14a, 1); ev(0, 0, 0)
            got = None
            second = None
            released = False
            while time.monotonic() - t0 < 3.0:
                if not released and (time.monotonic() - t0) * 1000 >= hold:
                    ev(3, 0x39, -1); ev(1, 0x14a, 0); ev(0, 0, 0)
                    released = True
                cur = fb()
                if cur != base:
                    if got is None:
                        got = (time.monotonic() - t0) * 1000
                        base = cur
                    else:
                        second = (time.monotonic() - t0) * 1000
                        break
                time.sleep(0.002)
            if not released:
                ev(3, 0x39, -1); ev(1, 0x14a, 0); ev(0, 0, 0)
            lat.append(round(got, 1) if got is not None else None)
            lat2.append(round(second, 1) if second is not None else None)
            time.sleep(gap)
    finally:
        os.close(fd)
    ok = [v for v in lat if v is not None]
    ok2 = [v for v in lat2 if v is not None]
    print(json.dumps({'x': x, 'y': y, 'latency_ms': lat, 'second_flip_ms': lat2,
                      'second_p50_ms': round(statistics.median(ok2), 1) if ok2 else None,
                      'p50_ms': round(statistics.median(ok), 1) if ok else None,
                      'max_ms': max(ok) if ok else None, 'missed': lat.count(None)}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
