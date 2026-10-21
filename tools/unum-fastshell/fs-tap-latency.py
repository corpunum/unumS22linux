#!/usr/bin/env python3
"""Tap -> highlight and tap-release -> page latency of the touch shell (native root).

Injects single-finger taps into sec_touchscreen (like tools/touchui/phase2/
p2-tap-latency.py) and watches plane-0's framebuffer id in DRM debugfs:

  press_ms   touch-down  -> first fb change   (pressed highlight)
  page_ms    touch-up    -> first fb change after the release (page shown)
  down_page_ms  touch-down -> page shown (includes the --hold time; this is
             the number p2-tap-latency reports as "second flip")

Between taps it sends `home` to the shell (shell.sock, or touchd for
Quickshell with --touchd) and waits, so every tap starts on the home screen.

usage: fs-tap-latency.py X Y [REPS] [--hold MS] [--gap S] [--touchd]
"""
from __future__ import annotations

import json
import os
import re
import socket
import statistics
import struct
import sys
import time

STATE = '/sys/kernel/debug/dri/1/state'
PLANE = re.compile(r'plane-0\n\tcrtc=\S+\n\tfb=(\d+)')
EV = struct.Struct('<qqHHi')
M = 4095
RUN = '/mnt/omarchy-trial/run/s22-touch'


def dev() -> str:
    for n in sorted(os.listdir('/sys/class/input')):
        try:
            if open(f'/sys/class/input/{n}/device/name').read().strip() == 'sec_touchscreen':
                return '/dev/input/' + n
        except OSError:
            pass
    return '/dev/input/event7'


def fb() -> str | None:
    with open(STATE) as f:
        m = PLANE.search(f.read())
    return m.group(1) if m else None


def call(sock: str, req: dict) -> dict:
    s = socket.socket(socket.AF_UNIX)
    s.settimeout(10)
    s.connect(sock)
    s.sendall((json.dumps(req) + '\n').encode())
    data = b''
    while not data.endswith(b'\n'):
        c = s.recv(65536)
        if not c:
            break
        data += c
    s.close()
    return json.loads(data or b'{}')


def home(touchd: bool) -> None:
    if touchd:
        call(f'{RUN}/ctl.sock', {'cmd': 'ui', 'args': ['home']})
    else:
        call(f'{RUN}/shell.sock', {'fn': 'home'})


def main() -> int:
    a = sys.argv[1:]
    x, y = float(a[0]), float(a[1])
    reps = int(a[2]) if len(a) > 2 and not a[2].startswith('--') else 10
    hold = float(a[a.index('--hold') + 1]) if '--hold' in a else 60
    gap = float(a[a.index('--gap') + 1]) if '--gap' in a else 1.0
    touchd = '--touchd' in a
    fd = os.open(dev(), os.O_WRONLY)

    def ev(t, c, v):
        now = time.time()
        os.write(fd, EV.pack(int(now), int((now % 1) * 1e6), t, c, v))

    press, page, downpage = [], [], []
    try:
        for i in range(reps):
            home(touchd)
            time.sleep(gap)
            base = fb()
            tid = (int(time.time()) + i) % 30000
            t0 = time.monotonic()
            ev(3, 0x2f, 0); ev(3, 0x39, tid); ev(3, 0x35, int(x * M)); ev(3, 0x36, int(y * M))
            ev(1, 0x14a, 1); ev(0, 0, 0)
            p = pg = None
            tup = None
            while time.monotonic() - t0 < 3.0:
                now = time.monotonic()
                if tup is None and (now - t0) * 1000 >= hold:
                    ev(3, 0x39, -1); ev(1, 0x14a, 0); ev(0, 0, 0)
                    tup = time.monotonic()
                    base = fb()
                cur = fb()
                if cur != base:
                    if tup is None:
                        if p is None:
                            p = (time.monotonic() - t0) * 1000
                        base = cur
                    else:
                        pg = (time.monotonic() - tup) * 1000
                        break
                time.sleep(0.001)
            if tup is None:
                ev(3, 0x39, -1); ev(1, 0x14a, 0); ev(0, 0, 0)
            press.append(round(p, 1) if p is not None else None)
            page.append(round(pg, 1) if pg is not None else None)
            downpage.append(round(pg + (tup - t0) * 1000, 1) if pg is not None and tup else None)
    finally:
        os.close(fd)
        home(touchd)

    def st(v):
        ok = [x for x in v if x is not None]
        return {'p50_ms': round(statistics.median(ok), 1) if ok else None, 'max_ms': max(ok) if ok else None,
                'missed': v.count(None), 'all': v}
    print(json.dumps({'x': x, 'y': y, 'hold_ms': hold, 'reps': reps, 'press': st(press), 'page': st(page),
                      'down_page': st(downpage)}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
