#!/usr/bin/env python3
"""s22-keepalive: keep the phone's agent services running, and keep it muted.

Installed as /srv/s22/hardware/bin/s22-keepalive and started at boot by
start-persistent-desktop when /srv/s22/state/keepalive/enabled exists.

Every INTERVAL seconds it checks each service and restarts it when it is down:
  * openunum   - the phone's OpenUnum (GET 127.0.0.1:18880/health), started
                 with s22-openunum start
  * unumsearch - the local search daemon (TCP 127.0.0.1:7781 answers)

A service whose process is missing is restarted at once. A service whose
process exists but does not answer is restarted only after it has been
unhealthy for UNHEALTHY_S (busy OpenUnum turns can stall /health briefly).
Restarts back off (30 s doubling to 10 min) and at most MAX_RESTARTS happen
per WINDOW_S per service; after that it only logs.

Mute guard (owner instruction 2026-10-08: keep the phone muted): after every
restart, and on every check, the saved level (/srv/s22/buttons/volume) and the
CS35L41 amp gain are read. With "enforce_mute" (default true) anything above
0 is set back to 0 and logged. Turn it off in config.json if the owner wants
sound again.

One instance only (flock). Everything is logged as JSON lines to
/srv/s22/state/keepalive/keepalive.jsonl (rotated at 1 MiB).

  s22-keepalive              run the loop
  s22-keepalive --once       one check pass, print the result (no restarts
                             unless --fix)
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

STATE = Path('/srv/s22/state/keepalive')
BIN = Path('/srv/s22/hardware/bin')
BUTTONS = Path('/srv/s22/buttons')
LOCK = Path('/run/s22-keepalive.lock')
CHROOT = '/mnt/omarchy-trial'
AUDIO_OPT = '/opt/s22-audio'
AMP_CONTROLS = ('Left Digital PCM Volume', 'Right Digital PCM Volume')

DEFAULTS = {
    'interval_s': 20,
    'unhealthy_s': 180,
    'backoff_start_s': 30,
    'backoff_max_s': 600,
    'max_restarts': 5,
    'window_s': 1800,
    'enforce_mute': True,
    'services': ['openunum', 'unumsearch'],
}
LOG_MAX = 1 << 20


def load_config(state: Path = STATE) -> dict:
    cfg = dict(DEFAULTS)
    try:
        cfg.update(json.loads((state / 'config.json').read_text()))
    except (OSError, ValueError):
        pass
    return cfg


def ps_lines() -> list[str]:
    try:
        return subprocess.run(['ps', '-o', 'pid,args'], capture_output=True, text=True,
                              timeout=10).stdout.splitlines()
    except (OSError, subprocess.SubprocessError):
        try:
            return subprocess.run(['ps'], capture_output=True, text=True, timeout=10).stdout.splitlines()
        except (OSError, subprocess.SubprocessError):
            return []


def pids_matching(needle: str, lines: list[str] | None = None) -> list[int]:
    out = []
    for line in (ps_lines() if lines is None else lines):
        if needle in line and 'grep' not in line and 's22-keepalive' not in line:
            try:
                out.append(int(line.split()[0]))
            except (ValueError, IndexError):
                pass
    return out


def http_ok(url: str, timeout: float = 10) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return 200 <= r.status < 500
    except urllib.error.HTTPError as e:      # an HTTP answer means the server is alive
        return e.code < 500
    except (OSError, ValueError):
        return False


def tcp_ok(host: str, port: int, timeout: float = 5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


class Service:
    name = ''
    needle = ''

    def running(self, lines=None) -> bool:
        return bool(pids_matching(self.needle, lines))

    def healthy(self) -> bool:
        raise NotImplementedError

    def start(self) -> tuple[bool, str]:
        raise NotImplementedError

    def stop(self) -> None:
        for pid in pids_matching(self.needle):
            try:
                os.kill(pid, 15)
            except OSError:
                pass


class OpenUnum(Service):
    name = 'openunum'
    needle = 'node src/server.mjs'

    def healthy(self) -> bool:
        return http_ok('http://127.0.0.1:18880/health')

    def start(self) -> tuple[bool, str]:
        try:
            r = subprocess.run(['/bin/sh', str(BIN / 's22-openunum'), 'start'], capture_output=True,
                               text=True, timeout=150, stdin=subprocess.DEVNULL)
            return r.returncode == 0, (r.stdout + r.stderr).strip()[-300:]
        except (OSError, subprocess.SubprocessError) as e:
            return False, f'{type(e).__name__}: {e}'


class UnumSearch(Service):
    name = 'unumsearch'
    needle = 'unumsearch --config /srv/s22/unumsearch/config.toml serve'
    argv = ['/usr/local/bin/unumsearch', '--config', '/srv/s22/unumsearch/config.toml', 'serve']
    log = Path('/srv/s22/unumsearch/serve.log')

    def healthy(self) -> bool:
        return tcp_ok('127.0.0.1', 7781)

    def start(self) -> tuple[bool, str]:
        try:
            out = open(self.log, 'ab')
            p = subprocess.Popen(['nice', '-n', '15'] + self.argv, stdin=subprocess.DEVNULL,
                                 stdout=out, stderr=out, start_new_session=True)
            out.close()
            for _ in range(20):
                if self.healthy():
                    return True, f'pid {p.pid}'
                if p.poll() is not None:
                    return False, f'exited rc={p.returncode}'
                time.sleep(0.5)
            return False, f'pid {p.pid} not listening after 10 s'
        except OSError as e:
            return False, f'{type(e).__name__}: {e}'


SERVICES = {'openunum': OpenUnum, 'unumsearch': UnumSearch}


# ------------------------------------------------------------------ mute guard

def amp_run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(['chroot', CHROOT, '/usr/bin/env', f'LD_LIBRARY_PATH={AUDIO_OPT}/usr/lib',
                           f'{AUDIO_OPT}/usr/bin/amixer', '-c0'] + args,
                          capture_output=True, text=True, timeout=15)


def parse_cget(out: str) -> int | None:
    for line in out.splitlines():
        line = line.strip()
        if line.startswith(': values='):
            try:
                return int(line.split('=', 1)[1].split(',')[0])
            except ValueError:
                return None
    return None


def read_mute_state(buttons: Path = BUTTONS, runner=amp_run) -> dict:
    state: dict = {}
    try:
        state['level'] = int((buttons / 'volume').read_text().strip())
    except (OSError, ValueError):
        state['level'] = None
    if not Path('/sys/class/sound/card0').exists():
        state['amp'] = None                   # no card yet: nothing can play
        return state
    amp = []
    for ctl in AMP_CONTROLS:
        try:
            amp.append(parse_cget(runner(['cget', f'name={ctl}']).stdout))
        except (OSError, subprocess.SubprocessError):
            amp.append(None)
    state['amp'] = amp
    return state


def needs_mute(state: dict) -> bool:
    if state.get('level') not in (0, None):
        return True
    amp = state.get('amp')
    return bool(amp) and any(v not in (0, None) for v in amp)


def apply_mute(buttons: Path = BUTTONS, runner=amp_run) -> None:
    (buttons / 'volume').write_text('0\n')
    if Path('/sys/class/sound/card0').exists():
        for ctl in AMP_CONTROLS:
            runner(['-q', 'cset', f'name={ctl}', '0'])


# ------------------------------------------------------------------ the loop

class Keepalive:
    def __init__(self, cfg: dict, state: Path = STATE, services: dict | None = None):
        self.cfg = cfg
        self.state = state
        self.state.mkdir(parents=True, exist_ok=True)
        names = cfg['services']
        self.services = services or {n: SERVICES[n]() for n in names if n in SERVICES}
        self.bad_since: dict[str, float | None] = {n: None for n in self.services}
        self.next_try: dict[str, float] = {n: 0.0 for n in self.services}
        self.backoff: dict[str, float] = {n: float(cfg['backoff_start_s']) for n in self.services}
        self.history: dict[str, list[float]] = {n: [] for n in self.services}
        self.limited: set[str] = set()

    def log(self, **rec) -> None:
        rec['t'] = round(time.time(), 3)
        path = self.state / 'keepalive.jsonl'
        try:
            if path.exists() and path.stat().st_size > LOG_MAX:
                os.replace(path, path.with_suffix('.jsonl.1'))
            with open(path, 'a') as f:
                f.write(json.dumps(rec, sort_keys=True) + '\n')
        except OSError:
            pass

    def decide(self, name: str, running: bool, healthy: bool, now: float) -> str:
        """'ok' | 'wait' | 'restart' | 'limited' (pure, for tests)."""
        if running and healthy:
            self.bad_since[name] = None
            self.backoff[name] = float(self.cfg['backoff_start_s'])
            return 'ok'
        if self.bad_since[name] is None:
            self.bad_since[name] = now
        if running and now - self.bad_since[name] < self.cfg['unhealthy_s']:
            return 'wait'                      # alive but slow: give it time
        if now < self.next_try[name]:
            return 'wait'
        window = [t for t in self.history[name] if now - t < self.cfg['window_s']]
        self.history[name] = window
        if len(window) >= self.cfg['max_restarts']:
            return 'limited'
        return 'restart'

    def restart(self, name: str, running: bool, now: float) -> bool:
        svc = self.services[name]
        if running:
            svc.stop()
            time.sleep(3)
        ok, detail = svc.start()
        self.history[name].append(now)
        self.next_try[name] = now + self.backoff[name]
        self.backoff[name] = min(self.backoff[name] * 2, float(self.cfg['backoff_max_s']))
        self.log(event='restart', service=name, was_running=running, ok=ok, detail=detail)
        return ok

    def mute_check(self, reason: str) -> dict:
        try:
            st = read_mute_state()
        except Exception as e:  # noqa: BLE001 - the guard must never kill the loop
            self.log(event='mute_check_error', reason=reason, error=f'{type(e).__name__}: {e}')
            return {}
        if needs_mute(st):
            if self.cfg['enforce_mute']:
                try:
                    apply_mute()
                    after = read_mute_state()
                except Exception as e:  # noqa: BLE001
                    after = {'error': f'{type(e).__name__}: {e}'}
                self.log(event='muted', reason=reason, before=st, after=after)
            else:
                self.log(event='not_muted', reason=reason, state=st)
        return st

    def check_once(self, fix: bool = True, now: float | None = None) -> dict:
        now = time.time() if now is None else now
        lines = ps_lines()
        result = {}
        restarted = False
        for name, svc in self.services.items():
            running = svc.running(lines)
            healthy = running and svc.healthy()
            action = self.decide(name, running, healthy, now)
            result[name] = {'running': running, 'healthy': healthy, 'action': action}
            if action == 'restart' and fix:
                result[name]['ok'] = self.restart(name, running, now)
                restarted = True
            elif action == 'limited' and name not in self.limited:
                self.limited.add(name)
                self.log(event='restart_limited', service=name)
            if action == 'ok':
                self.limited.discard(name)
        result['mute'] = self.mute_check('after_restart' if restarted else 'periodic')
        return result

    def run(self) -> None:
        self.log(event='start', pid=os.getpid(), services=list(self.services), cfg=self.cfg)
        while True:
            self.check_once()
            time.sleep(self.cfg['interval_s'])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--once', action='store_true')
    ap.add_argument('--fix', action='store_true', help='with --once: restart what is down')
    a = ap.parse_args(argv)
    cfg = load_config()
    if a.once:
        k = Keepalive(cfg)
        print(json.dumps(k.check_once(fix=a.fix), indent=1, sort_keys=True))
        return 0
    lock = open(LOCK, 'w')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print('s22-keepalive already running', file=sys.stderr)
        return 0
    Keepalive(cfg).run()
    return 0


if __name__ == '__main__':
    sys.exit(main())
