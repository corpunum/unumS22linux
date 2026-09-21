#!/usr/bin/env python3
"""Bounded LSM6DSO IIO sample trial; restore sensor mask and buffer state.

Execute via native Alpine Python on the phone, not Arch Python. This uses
the exact drivers/staging/nanohub ABI: a bitmask enable control, six-byte
accelerometer or twelve-byte gyro payload followed by a u64 timestamp.
No synthetic input, calibration sysfs/file writes, self-test or reset.
The kernel's first accel enable DOES request saved calibration through its
file-manager channel and send runtime calibration to the hub. With no
Android file-manager client this times out and replays zeros. This is not
factory-calibrated sampling; the sensor-enable/buffer restore does not undo
that internal runtime calibration command. No EFS file is opened here.
"""
import argparse
import json
import os
from pathlib import Path
import select
import signal
import stat
import struct
import time


SENSORS = {
    'accel': ('accelerometer_sensor', 0, '<hhhQ'),
    'gyro': ('gyro_sensor', 1, '<iiiQ'),
}


def decode(payload, layout):
    size = struct.calcsize(layout)
    if len(payload) % size:
        raise ValueError('Incomplete IIO frame')
    return [struct.unpack_from(layout, payload, offset)
            for offset in range(0, len(payload), size)]


def terminate(signum, frame):
    raise RuntimeError('Interrupted sample trial; restoring sensor state')


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('sensor', choices=SENSORS)
    args = ap.parse_args()
    name, bit, layout = SENSORS[args.sensor]
    root = Path('/sys/class/sensors/ssp_sensor')
    if (root / 'fs_ready').read_text().strip() != '1':
        raise RuntimeError('Hub has not started')
    if (root / 'data_injection_enable').read_text().strip() != '0':
        raise RuntimeError('Data injection enabled; not physical evidence')
    enable = root / 'enable'
    original = int(enable.read_text())
    if original != 0:
        raise RuntimeError('Another sensor client is active; do not change its state')
    matches = [p for p in Path('/sys/bus/iio/devices').glob('iio:device*')
               if (p/'name').read_text().strip() == name]
    if len(matches) != 1:
        raise RuntimeError('Ambiguous IIO target')
    device = matches[0]
    node = Path('/dev') / device.name
    major, minor = map(int, (device/'dev').read_text().split(':'))
    info = node.stat()
    if not stat.S_ISCHR(info.st_mode) or info.st_rdev != os.makedev(major, minor):
        raise RuntimeError('IIO node identity mismatch')
    buffer = device/'buffer/enable'
    original_buffer = buffer.read_text().strip()
    if original_buffer != '0':
        raise RuntimeError('IIO buffer already active')
    signal.signal(signal.SIGTERM, terminate)
    fd = os.open(node, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
    samples = []
    pending = bytearray()
    size = struct.calcsize(layout)
    try:
        buffer.write_text('1\n')
        # No driver sample queue is accepted as fresh evidence before enable.
        while True:
            try:
                if not os.read(fd, size * 64):
                    break
            except BlockingIOError:
                break
        enable.write_text(str(1 << bit)+'\n')
        until = time.monotonic()+5
        while time.monotonic() < until and len(samples) < 25:
            readable, _, _ = select.select([fd], [], [], min(0.25, max(0, until-time.monotonic())))
            if not readable:
                continue
            try:
                pending.extend(os.read(fd, size * 64))
            except BlockingIOError:
                continue
            complete = len(pending)//size*size
            samples.extend(decode(pending[:complete], layout))
            del pending[:complete]
    finally:
        try:
            enable.write_text(str(original)+'\n')
        finally:
            try:
                buffer.write_text(original_buffer+'\n')
            finally:
                os.close(fd)
    restored = int(enable.read_text()) == original and buffer.read_text().strip() == original_buffer
    timestamps = [row[-1] for row in samples]
    monotonic = bool(timestamps) and all(t > 0 for t in timestamps) and all(a < b for a,b in zip(timestamps, timestamps[1:]))
    result = dict(sensor=args.sensor, iio_name=name, frame_bytes=size,
                  samples=len(samples), timestamps_strictly_increasing=monotonic,
                  distinct_vectors=len({tuple(row[:-1]) for row in samples}),
                  axes_min=[min(row[i] for row in samples) for i in range(3)] if samples else [],
                  axes_max=[max(row[i] for row in samples) for i in range(3)] if samples else [],
                  sample_frames=samples, partial_bytes=len(pending),
                  state_restored=restored, data_injection=False,
                  units='raw driver units, calibration/orientation not accepted')
    print(json.dumps(result, indent=2))
    if not restored or len(samples)<5 or not monotonic or pending:
        raise RuntimeError('Sampling acceptance failed; no automatic retry')


if __name__ == '__main__':
    main()
