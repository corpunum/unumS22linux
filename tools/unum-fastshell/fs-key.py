#!/usr/bin/env python3
"""Inject a Power key press into the PMIC key node (native root), so s22-buttons
runs its hook exactly as for a real press. usage: fs-key.py HOLD_MS [KEYCODE]"""
import os
import struct
import sys
import time

EV = struct.Struct('<qqHHi')
hold = float(sys.argv[1]) if len(sys.argv) > 1 else 100
code = int(sys.argv[2]) if len(sys.argv) > 2 else 116
node = None
for n in sorted(os.listdir('/sys/class/input')):
    try:
        if open(f'/sys/class/input/{n}/device/name').read().strip() == 'sec-pmic-key':
            node = '/dev/input/' + n
    except OSError:
        pass
fd = os.open(node, os.O_WRONLY)


def ev(t, c, v):
    now = time.time()
    os.write(fd, EV.pack(int(now), int((now % 1) * 1e6), t, c, v))


ev(1, code, 1); ev(0, 0, 0)
time.sleep(hold / 1000)
ev(1, code, 0); ev(0, 0, 0)
os.close(fd)
