#!/usr/bin/env python3
"""s22-buttons: physical Power / Volume keys -> logged events + actions.

Runs on the native phone root.  Reads the key event nodes without grabbing
them (Hyprland keeps its own XF86PowerOff -> DPMS binding), logs every press
to EVENTS as JSON lines and, on release:

  volume up / down (short)   speaker volume step (amp digital gain) + spoken level
  volume up (hold >= 1 s)    hook "assistant"  (push-to-talk for an agent)
  volume down (hold >= 1 s)  hook "voldown-long"
  power (short)              hook "power", else s22-display toggle (black + backlight 0)

A hook is an executable HOOKS/<name>; it gets S22_BUTTON / S22_HELD_MS in its
environment and runs detached.  Without a hook the defaults above apply.

Press-and-hold mode (opt-in: the file BASE/ptt-hold exists and the hook
"assistant" is executable): the hook starts as soon as volume up has been
held for LONG_MS, with S22_PTT=hold and S22_PTT_STOP=<file>.  On release the
daemon writes the release time and the held time into that file, so the hook
can record exactly while the key is held.  The release then does nothing else.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import select
import struct
import subprocess
import time
from pathlib import Path

EVENT = struct.Struct('<qqHHi')
EV_KEY = 1
KEYS = {114: 'voldown', 115: 'volup', 116: 'power'}
NAMES = ('gpio_keys', 'sec-pmic-key')
BASE = Path('/srv/s22/buttons')
CHROOT = '/mnt/omarchy-trial'
OPT = '/opt/s22-audio'
SAY = '/srv/s22/hardware/bin/s22-say'
DISPLAY = '/srv/s22/hardware/bin/s22-display'
LONG_MS = 1000
LEVELS = 10            # level 10 = 0 dB amp gain, 4 dB per step, 0 = mute


def find_nodes(sys_root: str = '/sys/class/input') -> list[str]:
    nodes = []
    for ev in sorted(glob.glob(f'{sys_root}/event*')):
        try:
            name = Path(ev, 'device/name').read_text().strip()
        except OSError:
            continue
        if name in NAMES:
            nodes.append('/dev/input/' + Path(ev).name)
    return nodes


def amp_raw(level: int) -> int:
    """CS35L41 'Digital PCM Volume': 0.125 dB/step, 817 = 0 dB, 0 = -102 dB."""
    if level <= 0:
        return 0
    return 817 - (LEVELS - min(level, LEVELS)) * 32


class Buttons:
    def __init__(self, base: Path = BASE, dry_run: bool = False):
        self.base = base
        self.dry_run = dry_run
        self.down: dict[str, float] = {}
        self.base.mkdir(parents=True, exist_ok=True)
        try:
            self.level = int((base / 'volume').read_text())
        except (OSError, ValueError):
            self.level = 7
        self.actions: list[tuple] = []
        self.ptt_started = False    # hold-mode hook already started for this press

    @property
    def ptt_stop(self) -> Path:
        return self.base / 'ptt-release'

    def hold_mode(self) -> bool:
        hook = self.base / 'hooks' / 'assistant'
        return ((self.base / 'ptt-hold').is_file() and hook.is_file()
                and os.access(hook, os.X_OK))

    def tick(self, now: float | None = None) -> None:
        """Start the hold-mode hook once volume up has been held long enough."""
        start = self.down.get('volup')
        if start is None or self.ptt_started:
            return
        now = time.monotonic() if now is None else now
        held = int((now - start) * 1000)
        if held < LONG_MS or not self.hold_mode():
            return
        self.ptt_started = True
        try:
            self.ptt_stop.unlink()
        except FileNotFoundError:
            pass
        self.log(key='volup', action='ptt-start', held_ms=held)
        self.spawn([str(self.base / 'hooks' / 'assistant')],
                   {'S22_BUTTON': 'volup', 'S22_HELD_MS': str(held),
                    'S22_PTT': 'hold', 'S22_PTT_STOP': str(self.ptt_stop)})

    def ptt_release(self, held: int) -> None:
        tmp = self.ptt_stop.with_suffix('.tmp')
        tmp.write_text(json.dumps({'t': time.time(), 'held_ms': held}) + '\n')
        tmp.replace(self.ptt_stop)

    def log(self, **rec) -> None:
        rec['t'] = round(time.time(), 3)
        with open(self.base / 'events.jsonl', 'a') as f:
            f.write(json.dumps(rec, sort_keys=True) + '\n')

    def spawn(self, argv: list[str], env: dict | None = None) -> None:
        self.actions.append(tuple(argv))
        if self.dry_run:
            return
        subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True,
                         env={**os.environ, **(env or {})})

    def hook(self, name: str, key: str, held_ms: int) -> bool:
        path = self.base / 'hooks' / name
        if path.is_file() and os.access(path, os.X_OK):
            self.spawn([str(path)], {'S22_BUTTON': key, 'S22_HELD_MS': str(held_ms)})
            return True
        return False

    def set_volume(self, level: int, speak: bool = True) -> None:
        self.level = max(0, min(LEVELS, level))
        (self.base / 'volume').write_text(f'{self.level}\n')
        raw = amp_raw(self.level)
        amixer = ['chroot', CHROOT, '/usr/bin/env', f'LD_LIBRARY_PATH={OPT}/usr/lib',
                  f'{OPT}/usr/bin/amixer', '-c0', '-q', 'cset']
        for side in ('Left', 'Right'):
            self.spawn(amixer + [f'name={side} Digital PCM Volume', str(raw)])
        if speak and os.path.exists(SAY):
            self.spawn([SAY, 'mute' if self.level == 0 else f'volume {self.level}'])

    def handle(self, code: int, value: int, now: float | None = None) -> None:
        key = KEYS.get(code)
        if key is None or value not in (0, 1):
            return
        now = time.monotonic() if now is None else now
        if value == 1:
            self.down[key] = now
            if key == 'volup':
                self.ptt_started = False
            self.log(key=key, action='press')
            return
        start = self.down.pop(key, None)
        held = int((now - start) * 1000) if start is not None else 0
        long = held >= LONG_MS
        self.log(key=key, action='release', held_ms=held)
        if key == 'power':
            if long:
                self.hook('power-long', key, held)
            elif not self.hook('power', key, held) and os.path.exists(DISPLAY):
                self.spawn([DISPLAY, 'toggle'])
        elif key == 'volup':
            if self.ptt_started:
                self.ptt_started = False
                self.ptt_release(held)
            elif long:
                if not self.hook('assistant', key, held) and os.path.exists(SAY):
                    self.spawn([SAY, 'assistant hook not configured'])
            elif not self.hook('volup', key, held):
                self.set_volume(self.level + 1)
        elif key == 'voldown':
            if long:
                self.hook('voldown-long', key, held)
            elif not self.hook('voldown', key, held):
                self.set_volume(self.level - 1)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--base', type=Path, default=BASE)
    args = ap.parse_args()
    nodes = find_nodes()
    if not nodes:
        raise SystemExit('no key event nodes found')
    b = Buttons(args.base)
    b.log(action='start', nodes=nodes, volume=b.level)
    b.set_volume(b.level, speak=False)
    fds = [os.open(n, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC) for n in nodes]
    while True:
        # Poll faster while volume up is down so hold mode starts on time.
        ready, _, _ = select.select(fds, [], [], 0.05 if 'volup' in b.down else 60)
        for fd in ready:
            while True:
                try:
                    data = os.read(fd, EVENT.size * 16)
                except BlockingIOError:
                    break
                if not data:
                    break
                for off in range(0, len(data) - EVENT.size + 1, EVENT.size):
                    _, _, typ, code, value = EVENT.unpack_from(data, off)
                    if typ == EV_KEY:
                        b.handle(code, value)
        b.tick()
        # reap finished action processes
        try:
            while os.waitpid(-1, os.WNOHANG)[0]:
                pass
        except ChildProcessError:
            pass


if __name__ == '__main__':
    raise SystemExit(main())
