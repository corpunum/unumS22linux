#!/usr/bin/env python3
"""Inject a tap or a swipe into sec_touchscreen (native root), for shell tests.

usage: fs-inject.py tap X Y [HOLD_MS]
       fs-inject.py swipe X0 Y0 X1 Y1 [DURATION_MS]
Coordinates are normalised 0..1 (portrait). Slot 0 only, like a single finger.
"""
import os
import struct
import sys
import time

EV = struct.Struct('<qqHHi')
M = 4095


def dev():
    for n in sorted(os.listdir('/sys/class/input')):
        try:
            if open(f'/sys/class/input/{n}/device/name').read().strip() == 'sec_touchscreen':
                return '/dev/input/' + n
        except OSError:
            pass
    return '/dev/input/event7'


def main():
    a = sys.argv[1:]
    fd = os.open(dev(), os.O_WRONLY)

    def ev(t, c, v):
        now = time.time()
        os.write(fd, EV.pack(int(now), int((now % 1) * 1e6), t, c, v))

    def down(x, y):
        ev(3, 0x2f, 0); ev(3, 0x39, int(time.time()) % 30000); ev(3, 0x35, int(x * M)); ev(3, 0x36, int(y * M))
        ev(1, 0x14a, 1); ev(0, 0, 0)

    def move(x, y):
        ev(3, 0x2f, 0); ev(3, 0x35, int(x * M)); ev(3, 0x36, int(y * M)); ev(0, 0, 0)

    def up():
        ev(3, 0x2f, 0); ev(3, 0x39, -1); ev(1, 0x14a, 0); ev(0, 0, 0)

    if a[0] == 'tap':
        x, y = float(a[1]), float(a[2])
        hold = float(a[3]) if len(a) > 3 else 60
        down(x, y)
        time.sleep(hold / 1000)
        up()
    elif a[0] == 'swipe':
        x0, y0, x1, y1 = map(float, a[1:5])
        dur = float(a[5]) if len(a) > 5 else 250
        steps = max(4, int(dur / 8))
        down(x0, y0)
        for i in range(1, steps + 1):
            time.sleep(dur / 1000 / steps)
            move(x0 + (x1 - x0) * i / steps, y0 + (y1 - y0) * i / steps)
        up()
    os.close(fd)


if __name__ == '__main__':
    main()
