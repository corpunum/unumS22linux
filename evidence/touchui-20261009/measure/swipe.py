#!/usr/bin/env python3
"""Inject a synthetic single-finger swipe into the S22 touchscreen evdev node (for testing gestures).
usage: swipe.py X0 Y0 X1 Y1 [ms]   (normalised 0..1 coordinates)"""
import os, struct, sys, time
EV = struct.Struct('<qqHHi')
fd = os.open('/dev/input/event7', os.O_WRONLY)
def ev(t, c, v):
    now = time.time(); os.write(fd, EV.pack(int(now), int((now % 1) * 1e6), t, c, v))
def syn(): ev(0, 0, 0)
x0, y0, x1, y1 = map(float, sys.argv[1:5]); ms = float(sys.argv[5]) if len(sys.argv) > 5 else 250
M = 4095; steps = 12; tid = int(time.time()) % 30000
ev(3, 0x2f, 0); ev(3, 0x39, tid); ev(3, 0x35, int(x0 * M)); ev(3, 0x36, int(y0 * M)); ev(1, 0x14a, 1); syn()
for i in range(1, steps + 1):
    time.sleep(ms / 1000 / steps)
    f = i / steps
    ev(3, 0x35, int((x0 + (x1 - x0) * f) * M)); ev(3, 0x36, int((y0 + (y1 - y0) * f) * M)); syn()
ev(3, 0x39, -1); ev(1, 0x14a, 0); syn()
os.close(fd)
