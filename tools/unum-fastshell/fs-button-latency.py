#!/usr/bin/env python3
"""Button -> effect latency on the phone (native root, as root).

Injects key presses into the real key event nodes (gpio_keys: volume up;
sec-pmic-key: volume down and Power), found by their key capabilities, so every
reader (s22-buttons, unum-fastshell) sees them exactly like a real press. Then
polls the effects every ~1 ms and reports the first change of each, in ms
after the injected key-down and after the key-up:

  amp      CS35L41 'Left Digital PCM Volume' (ALSA control, read by ioctl)
  level    /run/s22-say.level (speech level the shell keeps across the mute guard)
  saved    /srv/s22/buttons/volume
  pcm      a playback stream RUNNING (pcm100p graph or pcm6p direct): audible start
  display  /run/s22-display-state
  crtc     the panel CRTC 'active' flag in DRM debugfs (panel really on/off)
  fb       plane-0's framebuffer id in DRM debugfs (something new drawn: OSD, lock)

usage:
  fs-button-latency.py vol up|down [--hold MS] [--watch S]
  fs-button-latency.py power [--hold MS] [--watch S]
  fs-button-latency.py combo power+voldown [--hold MS]     (screenshot)
Prints one JSON line. Keep test sounds short and quiet: the caller should set
/run/s22-say.level <= 2 before volume tests.
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import struct
import sys
import time

EV = struct.Struct('<qqHHi')
KEY = {'volup': 115, 'voldown': 114, 'power': 116}
STATE = '/sys/kernel/debug/dri/1/state'
PLANE = re.compile(r'plane-0\n\tcrtc=\S+\n\tfb=(\d+)')
CRTC = re.compile(r'crtc\[\d+\]: exynos-crtc\n\tenable=1\n\tactive=(\d)')
ELEM_READ = 0xC4C85512          # _IOWR('U', 0x12, struct snd_ctl_elem_value) on LP64 (1224 bytes)


def node_for(code: int) -> str:
    for n in sorted(os.listdir('/sys/class/input')):
        if not n.startswith('event'):
            continue
        try:
            words = open(f'/sys/class/input/{n}/device/capabilities/key').read().split()
        except OSError:
            continue
        bits = 0
        for w in words:
            bits = (bits << 64) | int(w, 16)
        if bits >> code & 1:
            return '/dev/input/' + n
    raise SystemExit(f'no node with key {code}')


def amp() -> int | None:
    try:
        fd = os.open('/dev/snd/controlC0', os.O_RDONLY)
    except OSError:
        return None
    try:
        buf = bytearray(1224)
        struct.pack_into('<IiII44sI', buf, 0, 0, 2, 0, 0, b'Left Digital PCM Volume', 0)
        fcntl.ioctl(fd, ELEM_READ, buf)
        return struct.unpack_from('<q', buf, 72)[0]
    except OSError:
        return None
    finally:
        os.close(fd)


def rd(p: str) -> str | None:
    try:
        with open(p) as f:
            return f.read().strip()
    except OSError:
        return None


def pcm() -> bool:
    for p in ('pcm100p', 'pcm6p', 'pcm102p'):
        s = rd(f'/proc/asound/card0/{p}/sub0/status') or ''
        if 'RUNNING' in s:
            return True
    return False


def drm() -> tuple[str | None, str | None]:
    s = rd(STATE) or ''
    m, c = PLANE.search(s), CRTC.search(s)
    return (m.group(1) if m else None, c.group(1) if c else None)


def snapshot() -> dict:
    fb, crtc = drm()
    return {'amp': amp(), 'level': rd('/run/s22-say.level'), 'saved': rd('/srv/s22/buttons/volume'),
            'pcm': pcm(), 'display': rd('/run/s22-display-state'), 'crtc': crtc, 'fb': fb}


def run(keys: list[str], hold_ms: float, watch_s: float) -> dict:
    fds = {k: os.open(node_for(KEY[k]), os.O_WRONLY) for k in keys}

    def ev(k, v):
        now = time.time()
        os.write(fds[k], EV.pack(int(now), int((now % 1) * 1e6), 1, KEY[k], v) + EV.pack(int(now), int((now % 1) * 1e6), 0, 0, 0))

    base = snapshot()
    first: dict[str, float] = {}
    t_down = time.monotonic()
    for k in keys:
        ev(k, 1)
    t_up = None
    last = dict(base)
    while time.monotonic() - t_down < watch_s:
        now = time.monotonic()
        if t_up is None and (now - t_down) * 1000 >= hold_ms:
            for k in reversed(keys):
                ev(k, 0)
            t_up = time.monotonic()
        cur = snapshot()
        t = time.monotonic()
        for k, v in cur.items():
            if v != last[k]:
                first.setdefault(k, (t - t_down) * 1000)
                first.setdefault(k + '_values', [])
                first[k + '_values'].append([round((t - t_down) * 1000, 1), v])
        last = cur
        time.sleep(0.0005)
    if t_up is None:
        for k in reversed(keys):
            ev(k, 0)
        t_up = time.monotonic()
    for fd in fds.values():
        os.close(fd)
    up_ms = (t_up - t_down) * 1000
    out = {'keys': keys, 'hold_ms': hold_ms, 'up_at_ms': round(up_ms, 1), 'before': base, 'after': last}
    for k in ('amp', 'level', 'saved', 'pcm', 'display', 'crtc', 'fb'):
        if k in first:
            out[k] = {'from_down_ms': round(first[k], 1), 'from_up_ms': round(first[k] - up_ms, 1),
                      'changes': first[k + '_values'][:6]}
    return out


def main() -> int:
    a = sys.argv[1:]
    hold = float(a[a.index('--hold') + 1]) if '--hold' in a else 80
    watch = float(a[a.index('--watch') + 1]) if '--watch' in a else 4.0
    if a[0] == 'vol':
        keys = ['volup' if a[1] == 'up' else 'voldown']
    elif a[0] == 'power':
        keys = ['power']
    elif a[0] == 'combo':
        keys = a[1].split('+')
    else:
        raise SystemExit(__doc__)
    print(json.dumps(run(keys, hold, watch)))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
