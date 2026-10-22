#!/usr/bin/env python3
"""s22-haptic: vibration motor (Cirrus CS40L26 via the Samsung inputff driver).

Device facts (r0s, kernel 5.10.260, verified live 2026-10-22):
  * i2c 17-0040, driver cs40l26 + sec_vibrator_inputff.  It registers an input
    device named "sec_vibrator_inputff" (event6 on this boot; found by name)
    with FF_PERIODIC / FF_CUSTOM / FF_GAIN, NOT FF_RUMBLE.
  * The haptic DSP firmware loads by itself ("loaded with 143 RAM waveforms");
    nothing needs staging.  A waveform is selected by uploading an FF_PERIODIC
    effect with waveform=FF_CUSTOM and custom_data = int16[2] {bank, index}
    (bank 0 = RAM wavetable, 1 = ROM, 2 = buzz).  Playback = write
    EV_FF/<effect id>/1, stop = value 0.  Lengths are fixed by the waveform
    (the replay length is only an upper bound).
  * Measured durations (HAPTIC -> STOPPED in the driver log): RAM 39 = 9 ms,
    RAM 3 = 41 ms, RAM 16 = 507 ms (stoppable early).  Play command to motor
    start (driver log) is about 5-7 ms.

Interface (documented in docs/S22_HAPTICS.md):
  s22-haptic tick | buzz | pattern ON,OFF,ON,... | stop | status | latency
  s22-haptic serve            # daemon on /run/s22-haptic.sock (datagram)
Client commands go to the daemon when it is running (one sendto, ~0.1 ms) and
otherwise drive the device directly.  Safety: disabled by
/srv/s22/state/haptics/config.json {"enabled": false}, per-segment and total
duration caps, minimum spacing and a rolling on-time / count budget.
"""
from __future__ import annotations

import argparse
import ctypes
import fcntl
import json
import os
import re
import socket
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path

SOCK = '/run/s22-haptic.sock'
STATE_DIR = '/srv/s22/state/haptics'
DEVICE_NAME = 'sec_vibrator_inputff'

EV_FF = 0x15
FF_PERIODIC = 0x51
FF_CUSTOM = 0x5D
EVIOCSFF = 0x40304580   # _IOW('E', 0x80, struct ff_effect[48])
EVIOCRMFF = 0x40044581  # _IOW('E', 0x81, int)
FF_EFFECT_SIZE = 48

DEFAULTS = {
    'enabled': True,
    'tick_index': 39,         # RAM wavetable, ~9 ms
    'buzz_index': 3,          # RAM wavetable, ~41 ms
    'long_index': 16,         # RAM wavetable, ~507 ms, stopped early
    'tick_ms': 9,             # nominal lengths, used for budget accounting
    'buzz_ms': 41,
    'tick_max_on_ms': 15,     # pattern segment <= this -> tick
    'buzz_max_on_ms': 60,     # <= this -> buzz, else long (stopped on time)
    'max_segment_ms': 500,    # hard cap for one vibrating segment
    'max_pattern_ms': 3000,   # hard cap for a whole pattern (on + off)
    'max_segments': 16,
    'min_interval_ms': 20,    # minimum gap between plays (rate limit)
    'window_ms': 10000,       # rolling budget window
    'window_max_plays': 150,
    'window_max_on_ms': 3000,
}


def load_config(state_dir: str = STATE_DIR) -> dict:
    """Defaults overlaid with config.json; a missing file means enabled."""
    cfg = dict(DEFAULTS)
    try:
        data = json.loads(Path(state_dir, 'config.json').read_text())
    except FileNotFoundError:
        return cfg
    except (OSError, ValueError):
        cfg['enabled'] = False  # unreadable config: fail safe, stay quiet
        return cfg
    if not isinstance(data, dict):
        cfg['enabled'] = False
        return cfg
    if 'enabled' in data:
        cfg['enabled'] = data['enabled'] is True
    for key, default in DEFAULTS.items():
        value = data.get(key)
        if key != 'enabled' and isinstance(value, int) and not isinstance(value, bool) \
                and value >= 0:
            cfg[key] = value
    return cfg


def parse_pattern(text: str) -> list[int]:
    try:
        values = [int(p) for p in text.replace(' ', '').split(',') if p != '']
    except ValueError as exc:
        raise ValueError('pattern must be comma separated integers (ms)') from exc
    if not values or any(v < 0 for v in values):
        raise ValueError('pattern needs non-negative durations')
    return values


def plan_pattern(values: list[int], cfg: dict) -> list[tuple]:
    """ON,OFF,ON,... in ms -> [('tick'|'buzz'|'long', on_ms, off_ms_after)].

    Segment and total caps are applied here; excess is dropped, never an error.
    """
    steps: list[tuple] = []
    total = 0
    for i in range(0, min(len(values), 2 * cfg['max_segments']), 2):
        on = min(values[i], cfg['max_segment_ms'])
        off = values[i + 1] if i + 1 < len(values) else 0
        if on <= 0:
            total += off
            if steps:
                steps[-1] = (steps[-1][0], steps[-1][1], steps[-1][2] + off)
            continue
        if total + on > cfg['max_pattern_ms']:
            break
        off = min(off, max(0, cfg['max_pattern_ms'] - total - on))
        kind = ('tick' if on <= cfg['tick_max_on_ms']
                else 'buzz' if on <= cfg['buzz_max_on_ms'] else 'long')
        steps.append((kind, on, off))
        total += on + off
    return steps


class Limiter:
    """Minimum spacing plus a rolling plays / on-time budget (monotonic ms)."""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.history: list[tuple[float, int]] = []

    def allow(self, now_ms: float, on_ms: int) -> bool:
        cfg = self.cfg
        self.history = [(t, d) for t, d in self.history if now_ms - t < cfg['window_ms']]
        if self.history and now_ms - self.history[-1][0] < cfg['min_interval_ms']:
            return False
        if len(self.history) >= cfg['window_max_plays']:
            return False
        if sum(d for _, d in self.history) + on_ms > cfg['window_max_on_ms']:
            return False
        self.history.append((now_ms, on_ms))
        return True


def pack_effect(bank: int, index: int, data_ptr: int, length_ms: int = 1000,
                effect_id: int = -1) -> bytearray:
    """struct ff_effect (aarch64, 48 bytes) for an FF_CUSTOM {bank,index} play."""
    b = bytearray(FF_EFFECT_SIZE)
    struct.pack_into('<HhHHHHH', b, 0, FF_PERIODIC, effect_id, 0, 0, 0, length_ms, 0)
    struct.pack_into('<HHhhHHHHH', b, 16, FF_CUSTOM, 0, 0x7FFF, 0, 0, 0, 0, 0, 0)
    struct.pack_into('<I', b, 36, 2)
    struct.pack_into('<Q', b, 40, data_ptr)
    return b


def find_event_node(root: str = '/sys/class/input') -> str:
    for ev in sorted(Path(root).glob('event*')):
        try:
            if (ev / 'device/name').read_text().strip() == DEVICE_NAME:
                return '/dev/input/' + ev.name
        except OSError:
            continue
    raise RuntimeError(f'input device {DEVICE_NAME} not found')


class Motor:
    """Uploads the three waveforms once and plays them by effect id."""

    def __init__(self, cfg: dict, node: str | None = None):
        self.cfg = cfg
        self.fd = os.open(node or find_event_node(), os.O_RDWR)
        self._keep = []
        self.ids = {
            'tick': self._upload(0, cfg['tick_index']),
            'buzz': self._upload(0, cfg['buzz_index']),
            'long': self._upload(0, cfg['long_index']),
        }

    def _upload(self, bank: int, index: int) -> int:
        data = (ctypes.c_int16 * 2)(bank, index)
        self._keep.append(data)
        eff = pack_effect(bank, index, ctypes.addressof(data))
        fcntl.ioctl(self.fd, EVIOCSFF, eff)
        return struct.unpack_from('<h', eff, 2)[0]

    def _write(self, effect_id: int, value: int) -> None:
        os.write(self.fd, struct.pack('llHHi', 0, 0, EV_FF, effect_id, value))

    def start(self, kind: str) -> None:
        self._write(self.ids[kind], 1)

    def stop(self, kind: str) -> None:
        self._write(self.ids[kind], 0)

    def close(self) -> None:
        for eid in self.ids.values():
            try:
                fcntl.ioctl(self.fd, EVIOCRMFF, eid)
            except OSError:
                pass
        os.close(self.fd)


class Player:
    """Policy layer: enabled check, limiter, pattern scheduling, cancel."""

    def __init__(self, motor, state_dir: str = STATE_DIR, clock=time.monotonic,
                 sleep=time.sleep):
        self.motor = motor
        self.state_dir = state_dir
        self.clock = clock
        self.sleep = sleep
        self.cfg = load_config(state_dir)
        self._cfg_mtime = self._mtime()
        self.limiter = Limiter(self.cfg)
        self.cancel = threading.Event()
        self.thread: threading.Thread | None = None
        self.counts = {'played': 0, 'dropped_disabled': 0, 'dropped_limited': 0}

    def _mtime(self):
        try:
            return os.stat(Path(self.state_dir, 'config.json')).st_mtime_ns
        except OSError:
            return None

    def refresh(self) -> dict:
        m = self._mtime()
        if m != self._cfg_mtime:
            self._cfg_mtime = m
            self.cfg = load_config(self.state_dir)
            self.limiter.cfg = self.cfg
        return self.cfg

    def _gate(self, on_ms: int) -> bool:
        cfg = self.refresh()
        if not cfg['enabled']:
            self.counts['dropped_disabled'] += 1
            return False
        if not self.limiter.allow(self.clock() * 1000, on_ms):
            self.counts['dropped_limited'] += 1
            return False
        return True

    def single(self, kind: str) -> bool:
        nominal = self.cfg['tick_ms'] if kind == 'tick' else self.cfg['buzz_ms']
        if not self._gate(nominal):
            return False
        self.motor.start(kind)
        self.counts['played'] += 1
        return True

    def stop(self) -> None:
        self.cancel.set()
        for kind in ('long', 'buzz', 'tick'):
            try:
                self.motor.stop(kind)
            except OSError:
                pass

    def pattern(self, values: list[int], blocking: bool = False) -> bool:
        cfg = self.refresh()
        if not cfg['enabled']:
            self.counts['dropped_disabled'] += 1
            return False
        steps = plan_pattern(values, cfg)
        if not steps:
            return False
        self.cancel.set()
        if self.thread is not None:
            self.thread.join(timeout=1.0)
        self.cancel = threading.Event()
        cancel = self.cancel
        self.thread = threading.Thread(target=self._run, args=(steps, cancel), daemon=True)
        self.thread.start()
        if blocking:
            self.thread.join()
        return True

    def _run(self, steps, cancel) -> None:
        for kind, on_ms, off_ms in steps:
            if cancel.is_set() or not self._gate(on_ms):
                return
            self.motor.start(kind)
            self.counts['played'] += 1
            if kind == 'long':
                self.sleep(on_ms / 1000)
                self.motor.stop('long')
            if cancel.wait(off_ms / 1000 + (0 if kind == 'long' else on_ms / 1000)):
                return

    def handle(self, line: str) -> str:
        parts = line.strip().split(None, 1)
        if not parts:
            return 'error empty'
        cmd = parts[0]
        try:
            if cmd in ('tick', 'buzz'):
                return 'ok' if self.single(cmd) else 'dropped'
            if cmd == 'pattern' and len(parts) == 2:
                return 'ok' if self.pattern(parse_pattern(parts[1])) else 'dropped'
            if cmd == 'stop':
                self.stop()
                return 'ok'
            if cmd == 'status':
                return json.dumps({'enabled': self.refresh()['enabled'], **self.counts})
        except ValueError as exc:
            return f'error {exc}'
        return 'error unknown command'


def serve(sock_path: str = SOCK, state_dir: str = STATE_DIR) -> None:
    cfg = load_config(state_dir)
    motor = Motor(cfg)
    player = Player(motor, state_dir)
    try:
        os.unlink(sock_path)
    except FileNotFoundError:
        pass
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    srv.bind(sock_path)
    os.chmod(sock_path, 0o666)
    print(f's22-haptic: serving {sock_path} on {find_event_node()}', flush=True)
    try:
        while True:
            data, addr = srv.recvfrom(512)
            reply = player.handle(data.decode(errors='replace'))
            if addr:  # only clients that bound a reply socket get an answer
                try:
                    srv.sendto(reply.encode(), addr)
                except OSError:
                    pass
    finally:
        player.stop()
        motor.close()
        try:
            os.unlink(sock_path)
        except OSError:
            pass


def client(line: str, sock_path: str = SOCK, want_reply: bool = False,
           timeout: float = 0.5) -> str | None:
    """Send one command to the daemon; None when no daemon is listening."""
    if not os.path.exists(sock_path):
        return None
    s = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    try:
        if want_reply:
            s.bind('\0s22-haptic-%d' % os.getpid())
            s.settimeout(timeout)
        s.sendto(line.encode(), sock_path)
        return s.recv(512).decode() if want_reply else 'sent'
    except OSError:
        return None
    finally:
        s.close()


def direct(line: str) -> str:
    cfg = load_config()
    motor = Motor(cfg)
    player = Player(motor)
    try:
        parts = line.split(None, 1)
        if parts[0] == 'pattern':
            ok = player.pattern(parse_pattern(parts[1]), blocking=True)
            time.sleep(0.05)
            return 'ok' if ok else 'dropped'
        reply = player.handle(line)
        time.sleep(0.12 if parts[0] != 'status' else 0)  # let the effect finish
        return reply
    finally:
        motor.close()


def latency(samples: int = 5) -> dict:
    """Driver-log latency: playback_effect line -> HAPTIC state transition."""
    # The input device only has a few effect slots, so reuse the daemon's.
    daemon = os.path.exists(SOCK)
    motor = None if daemon else Motor(load_config())
    deltas = []
    try:
        for _ in range(samples):
            if daemon:
                client('tick')
            else:
                motor.start('tick')
            time.sleep(0.25)
            log = subprocess.run(['dmesg'], capture_output=True, text=True).stdout
            lines = [l for l in log.splitlines()
                     if 'playback_effect: effect ID' in l and 'val = 1' in l
                     or 'VIBE_STATE_HAPTIC' in l]
            stamps = [(float(m.group(1)), 'H' if 'HAPTIC' in l else 'P')
                      for l in lines for m in [re.match(r'\[\s*([\d.]+)\]', l)] if m]
            if len(stamps) >= 2 and stamps[-1][1] == 'H' and stamps[-2][1] == 'P':
                deltas.append((stamps[-1][0] - stamps[-2][0]) * 1000)
            time.sleep(0.2)
    finally:
        if motor:
            motor.close()
    return {'via': 'daemon' if daemon else 'direct', 'driver_play_to_motor_ms': [round(d, 1) for d in deltas],
            'median_ms': round(sorted(deltas)[len(deltas) // 2], 1) if deltas else None}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='S22 haptics')
    ap.add_argument('command', choices=['tick', 'buzz', 'pattern', 'stop', 'status',
                                        'latency', 'serve'])
    ap.add_argument('arg', nargs='?', help='pattern: ON,OFF,ON,... in ms')
    ap.add_argument('--direct', action='store_true', help='bypass the daemon')
    args = ap.parse_args(argv)
    if args.command == 'serve':
        serve()
        return 0
    if args.command == 'latency':
        print(json.dumps(latency()))
        return 0
    if args.command == 'pattern':
        if not args.arg:
            ap.error('pattern needs ON,OFF,... ms')
        try:
            parse_pattern(args.arg)
        except ValueError as exc:
            ap.error(str(exc))
    line = args.command + (' ' + args.arg if args.arg else '')
    t0 = time.perf_counter()
    reply = None if args.direct else client(line, want_reply=args.command in ('status',))
    if reply is None:
        reply = direct(line)
    if args.command == 'status' or reply != 'sent':
        print(reply)
    if os.environ.get('S22_HAPTIC_TIMING'):
        print(f'{(time.perf_counter() - t0) * 1000:.2f} ms', file=sys.stderr)
    return 0 if not reply.startswith('error') else 2


if __name__ == '__main__':
    sys.exit(main())
