#!/usr/bin/env python3
"""shm-reader: consume s22-campreview's shared-memory preview like a shell would.

Polls the header every --poll-ms, and on each new sequence number reads the
latest slot. Reports frames seen, reader fps, the age of each frame when read
(publish -> read, CLOCK_MONOTONIC) and a content check (mean of a sparse pixel
sample, so a black or stuck buffer shows). It never writes or keeps pixels.

Header (little endian): 0 "S22P", 4 version, 8 width, 12 height, 16 stride,
20 fourcc XR24, 24 nslots, 28 slot_bytes, 32 seq u64, 40 latest slot u32,
48 frame_start_ns u64, 56 publish_ns u64; slots start at 4096.
"""
import json
import mmap
import os
import struct
import sys
import time


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "/run/s22-campreview.shm"
    seconds = float(sys.argv[2]) if len(sys.argv) > 2 else 5.0
    poll = float(sys.argv[3]) / 1000 if len(sys.argv) > 3 else 0.004
    t_wait = time.monotonic() + 20
    while not os.path.exists(path) or os.path.getsize(path) < 4096:
        if time.monotonic() > t_wait:
            print(json.dumps({"ok": False, "error": "no shm"}))
            return 1
        time.sleep(0.05)
    f = open(path, "rb")
    m = mmap.mmap(f.fileno(), 0, prot=mmap.PROT_READ)
    magic, ver, w, h, stride, fcc, nslots, slot_bytes = struct.unpack_from("<4s7I", m, 0)
    last, seen, ages, means, t0 = None, 0, [], [], None
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        seq = struct.unpack_from("<Q", m, 32)[0]
        if seq != last and seq:
            slot, = struct.unpack_from("<I", m, 40)
            fstart, pub = struct.unpack_from("<QQ", m, 48)
            now = time.monotonic_ns()
            base = 4096 + slot * slot_bytes
            sample = [m[base + ((i * 7919) % (w * h)) * 4 + 1] for i in range(256)]   # green byte
            means.append(sum(sample) / len(sample))
            ages.append((now - pub) / 1e6)
            if t0 is None:
                t0 = now
            seen += 1
            last = seq
        time.sleep(poll)
    span = (time.monotonic_ns() - t0) / 1e9 if t0 else 0
    ages.sort()
    out = {"ok": seen > 0, "size": [w, h], "magic": magic.decode(), "frames_seen": seen,
           "reader_fps": round((seen - 1) / span, 2) if span else None,
           "age_ms_p50": round(ages[len(ages) // 2], 2) if ages else None,
           "age_ms_max": round(ages[-1], 2) if ages else None,
           "green_mean_min_max": [round(min(means), 1), round(max(means), 1)] if means else None}
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
