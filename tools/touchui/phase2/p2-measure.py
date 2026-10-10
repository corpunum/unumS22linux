#!/usr/bin/env python3
"""Phase 2 compositor measurement for the S22 panel (native root, read-only).

Counts frames that really reach the panel (scanout fb changes on plane-0 of
exynos-drmdpu, read from DRM debugfs), and samples CPU %, RSS and SoC
temperatures for the named processes over the same window.

usage: p2-measure.py SECONDS COMM[,COMM...] [--label L] [--out FILE]
  COMMs are matched exactly against /proc/<pid>/comm (no pattern kills, no
  signals: this tool only reads /proc, /sys and debugfs).
Needs debugfs mounted at /sys/kernel/debug (mount -t debugfs none /sys/kernel/debug).
"""
from __future__ import annotations

import json
import os
import re
import statistics
import sys
import time

STATE = '/sys/kernel/debug/dri/1/state'
PLANE = re.compile(r'plane-0\n\tcrtc=\S+\n\tfb=(\d+)')
HZ = os.sysconf('SC_CLK_TCK')


def pids_for(comms: set[str]) -> dict[int, str]:
    out = {}
    for d in os.listdir('/proc'):
        if not d.isdigit():
            continue
        try:
            with open(f'/proc/{d}/comm') as f:
                c = f.read().strip()
        except OSError:
            continue
        if c in comms:
            out[int(d)] = c
    return out


def cpu_ticks(pid: int) -> int | None:
    try:
        with open(f'/proc/{pid}/stat') as f:
            parts = f.read().rsplit(')', 1)[1].split()
        return int(parts[11]) + int(parts[12])
    except (OSError, IndexError, ValueError):
        return None


def rss_kb(pid: int) -> int | None:
    try:
        with open(f'/proc/{pid}/status') as f:
            for line in f:
                if line.startswith('VmRSS:'):
                    return int(line.split()[1])
    except OSError:
        return None
    return None


def system_ticks() -> tuple[int, int]:
    with open('/proc/stat') as f:
        v = [int(x) for x in f.readline().split()[1:]]
    idle = v[3] + v[4]
    return sum(v), idle


def temps() -> dict[str, float]:
    out = {}
    base = '/sys/class/thermal'
    for z in sorted(os.listdir(base)):
        if not z.startswith('thermal_zone'):
            continue
        try:
            t = open(f'{base}/{z}/type').read().strip()
            if t in ('BIG', 'MID', 'LITTLE', 'G3D'):
                out[t] = int(open(f'{base}/{z}/temp').read()) / 1000
        except (OSError, ValueError):
            pass
    return out


def scanout_fb() -> str | None:
    with open(STATE) as f:
        m = PLANE.search(f.read())
    return m.group(1) if m else None


def main() -> int:
    args = sys.argv[1:]
    if len(args) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    seconds = float(args[0])
    comms = set(args[1].split(','))
    label = args[args.index('--label') + 1] if '--label' in args else ''
    out_file = args[args.index('--out') + 1] if '--out' in args else None
    pids = pids_for(comms)
    t_start = temps()
    c0 = {p: cpu_ticks(p) for p in pids}
    s0 = system_ticks()
    flips: list[float] = []
    last = scanout_fb()
    samples = 0
    start = time.monotonic()
    end = start + seconds
    while True:
        now = time.monotonic()
        if now >= end:
            break
        fb = scanout_fb()
        samples += 1
        if fb != last:
            flips.append(now)
            last = fb
        time.sleep(0.004)
    wall = time.monotonic() - start
    s1 = system_ticks()
    procs = []
    for p, c in pids.items():
        t1 = cpu_ticks(p)
        if t1 is None or c0[p] is None:
            continue
        procs.append({'pid': p, 'comm': c, 'cpu_pct': round((t1 - c0[p]) * 100 / HZ / wall, 1),
                      'rss_mb': round((rss_kb(p) or 0) / 1024, 1)})
    gaps = [(b - a) * 1000 for a, b in zip(flips, flips[1:])]
    tot, idle = s1[0] - s0[0], s1[1] - s0[1]
    res = {
        'label': label, 'seconds': round(wall, 2), 'samples': samples,
        'panel_flips': len(flips), 'panel_fps': round(len(flips) / wall, 2),
        'frame_ms_mean': round(statistics.mean(gaps), 1) if gaps else None,
        'frame_ms_p50': round(statistics.median(gaps), 1) if gaps else None,
        'frame_ms_p95': round(sorted(gaps)[int(len(gaps) * .95) - 1], 1) if len(gaps) >= 20 else None,
        'system_cpu_pct_of_8': round((tot - idle) * 100 / tot, 1) if tot else None,
        'procs': sorted(procs, key=lambda r: -r['cpu_pct']),
        'temp_start': t_start, 'temp_end': temps(), 'ts': int(time.time()),
    }
    text = json.dumps(res, sort_keys=True)
    print(text)
    if out_file:
        with open(out_file, 'a') as f:
            f.write(text + '\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
