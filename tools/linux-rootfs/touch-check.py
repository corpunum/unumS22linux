#!/usr/bin/env python3
"""Inspect the real digitizer; optional synthetic tap tests the downstream path.

No grab, calibration changes, or partition writes. A synthetic tap is not proof
of physical digitizer sensing. Run without --tap for read-only metadata.
"""
import argparse
import array
import fcntl
import json
import os
import stat
import struct
import time


def ior(number, size):
    return (2 << 30) | (size << 16) | (ord('E') << 8) | number


def absolute(fd, axis):
    values = array.array('i', [0] * 6)
    fcntl.ioctl(fd, ior(0x40 + axis, 24), values, True)
    return dict(zip(('value', 'min', 'max', 'fuzz', 'flat', 'resolution'), values))


def slots(fd, count):
    values = array.array('i', [0x39] + [0] * count)
    fcntl.ioctl(fd, ior(0x0a, len(values) * 4), values, True)
    return list(values[1:])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--device', default='/dev/input/event7')
    parser.add_argument('--tap', nargs=2, type=float, metavar=('X_FRACTION', 'Y_FRACTION'))
    args = parser.parse_args()
    if args.tap and any(not 0 < value < 1 for value in args.tap):
        parser.error('tap coordinates must be fractions strictly between 0 and 1')
    fd = os.open(args.device, (os.O_RDWR if args.tap else os.O_RDONLY) | os.O_NONBLOCK)
    try:
        if not stat.S_ISCHR(os.fstat(fd).st_mode):
            raise RuntimeError('not a character device')
        name = bytearray(256)
        fcntl.ioctl(fd, ior(0x06, len(name)), name, True)
        name = bytes(name).split(b'\0')[0].decode()
        if name != 'sec_touchscreen':
            raise RuntimeError(f'wrong input device: {name}')
        axes = {hex(axis): absolute(fd, axis) for axis in (0x2f, 0x30, 0x31, 0x35, 0x36, 0x39)}
        count = axes['0x2f']['max'] + 1
        if not 1 <= count <= 32 or axes['0x2f']['min'] != 0:
            raise RuntimeError('unexpected slot geometry')
        active = slots(fd, count)
        print(json.dumps({'name': name, 'axes': axes, 'tracking_ids': active}), flush=True)
        if not args.tap:
            return
        # Wait briefly to verify idle, never grab or override an active finger.
        time.sleep(.25)
        if any(value >= 0 for value in active + slots(fd, count)):
            raise RuntimeError('active touch present; refusing synthetic tap')
        x = round(axes['0x35']['min'] + args.tap[0] * (axes['0x35']['max'] - axes['0x35']['min']))
        y = round(axes['0x36']['min'] + args.tap[1] * (axes['0x36']['max'] - axes['0x36']['min']))

        def events(items):
            payload = b''.join(struct.pack('llHHi', 0, 0, typ, code, val) for typ, code, val in items)
            if os.write(fd, payload) != len(payload):
                raise RuntimeError('short evdev write')

        release = [(3, 0x2f, 0), (3, 0x39, -1), (1, 0x14a, 0), (1, 0x145, 0), (0, 0, 0)]
        try:
            events([(3, 0x2f, 0), (3, 0x39, 1234), (3, 0x35, x), (3, 0x36, y),
                    (3, 0x30, 8), (3, 0x31, 8), (1, 0x14a, 1), (1, 0x145, 1), (0, 0, 0)])
            time.sleep(.12)
        finally:
            events(release)
        time.sleep(.2)
        print(json.dumps({'synthetic_tap': [x, y], 'tracking_ids_after': slots(fd, count),
                          'physical_touch_verified': False}), flush=True)
    finally:
        os.close(fd)


if __name__ == '__main__':
    main()
