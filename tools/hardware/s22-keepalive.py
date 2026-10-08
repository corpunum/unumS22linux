#!/usr/bin/env python3
"""s22-keepalive: keep the phone's agent services running, and keep it muted.

Installed as /srv/s22/hardware/bin/s22-keepalive and started at boot by
start-persistent-desktop when /srv/s22/state/keepalive/enabled exists.

Every INTERVAL seconds it checks each service named in config "services"
and restarts it when it is down:
  * openunum   - the phone's OpenUnum (GET 127.0.0.1:18880/health), started
                 with s22-openunum start
  * unumsearch - the local search daemon (TCP 127.0.0.1:7781 answers)
  * phoned     - optional: s22-phoned serve (GET 127.0.0.1:8095/status); only
                 managed when /srv/s22/hardware/bin/s22-phoned exists
Other services are config only: an entry in "service_defs" (argv, needle,
health, optional requires/log/start_wait_s/unhealthy_s/nice) plus its name
in "services", e.g. the phone's llama.cpp GPU server:
  "service_defs": {"llama": {"argv": ["/srv/s22/llama/bin/llama-server", "..."],
                             "needle": "llama-server", "health": {"http":
                             "http://127.0.0.1:8090/health"}, "start_wait_s": 180,
                             "unhealthy_s": 600}}

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

Model pin (config "pin_model": true): OpenUnum's default model must stay
the pinned one (default openai/gpt-6-luna; or model.provider/model/routing
from the pinned copy /srv/s22/state/keepalive/openunum.pinned.json). While
OpenUnum runs, a drift is undone through its API (POST /api/model/switch,
which also saves and re-baselines config_drift); the file itself is only
patched while OpenUnum is stopped, right before keepalive starts it, because
the running server rewrites openunum.json from memory after every turn.

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
    'service_defs': {},
    'pin_model': False,
    'pin': {'provider': 'openai', 'model': 'openai/gpt-6-luna'},
    'pin_source': '/srv/s22/state/keepalive/openunum.pinned.json',
    'pin_retry_s': 600,
}
OPENUNUM_API = 'http://127.0.0.1:18880'
OPENUNUM_CONFIG = Path('/mnt/omarchy-trial/root/.openunum/openunum.json')
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


class Generic(Service):
    """A daemon described by a table entry (see SERVICE_DEFS / "service_defs").

    argv     command started detached (own session), output appended to log
    needle   substring of its `ps` line (default: the argv joined)
    health   {"http": URL} (any HTTP answer < 500) or {"tcp": [host, port]}
    requires path that must exist for the service to be managed at all
    nice, start_wait_s (default 10), unhealthy_s (overrides the global one)
    """

    def __init__(self, name: str, spec: dict):
        self.name = name
        self.spec = spec
        self.argv = [str(a) for a in spec['argv']]
        self.needle = spec.get('needle') or ' '.join(self.argv)
        self.log = Path(spec.get('log') or STATE / f'{name}.log')

    def available(self) -> bool:
        req = self.spec.get('requires')
        return not req or Path(req).exists()

    def healthy(self) -> bool:
        h = self.spec.get('health') or {}
        if 'http' in h:
            return http_ok(h['http'], timeout=float(h.get('timeout_s', 10)))
        if 'tcp' in h:
            host, port = h['tcp']
            return tcp_ok(host, int(port))
        return True                       # no probe: running is enough

    def start(self) -> tuple[bool, str]:
        nice = self.spec.get('nice')
        argv = (['nice', '-n', str(nice)] if nice is not None else []) + self.argv
        try:
            self.log.parent.mkdir(parents=True, exist_ok=True)
            out = open(self.log, 'ab')
            p = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=out, stderr=out,
                                 start_new_session=True)
            out.close()
            deadline = time.time() + float(self.spec.get('start_wait_s', 10))
            while True:
                if self.healthy():
                    return True, f'pid {p.pid}'
                if p.poll() is not None:
                    return False, f'exited rc={p.returncode}'
                if time.time() >= deadline:
                    return False, f'pid {p.pid} not healthy after {self.spec.get("start_wait_s", 10)} s'
                time.sleep(0.5)
        except OSError as e:
            return False, f'{type(e).__name__}: {e}'


SERVICE_DEFS = {
    'unumsearch': {
        'argv': ['/usr/local/bin/unumsearch', '--config', '/srv/s22/unumsearch/config.toml', 'serve'],
        'needle': 'unumsearch --config /srv/s22/unumsearch/config.toml serve',
        'health': {'tcp': ['127.0.0.1', 7781]},
        'log': '/srv/s22/unumsearch/serve.log',
        'nice': 15,
    },
    'phoned': {
        'argv': ['/srv/s22/hardware/bin/s22-phoned', 'serve'],
        'needle': 's22-phoned serve',
        'health': {'http': 'http://127.0.0.1:8095/status'},
        'requires': '/srv/s22/hardware/bin/s22-phoned',
        'log': '/srv/s22/state/phoned/serve.log',
        'start_wait_s': 15,
    },
}
SERVICES = {'openunum': OpenUnum}         # services that need code, not just a table row


def build_services(cfg: dict) -> tuple[dict, dict]:
    """(services to manage, {name: reason} for the configured ones skipped)."""
    defs = {**SERVICE_DEFS, **(cfg.get('service_defs') or {})}
    out, skipped = {}, {}
    for name in cfg['services']:
        if name in SERVICES:
            out[name] = SERVICES[name]()
        elif name in defs:
            try:
                svc = Generic(name, defs[name])
            except (KeyError, TypeError) as e:
                skipped[name] = f'bad definition: {type(e).__name__}: {e}'
                continue
            if svc.available():
                out[name] = svc
            else:
                skipped[name] = f'missing {defs[name].get("requires")}'
        else:
            skipped[name] = 'unknown service'
    return out, skipped


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


# ------------------------------------------------------------------ model pin

def load_pin(cfg: dict) -> dict:
    """{'provider', 'model', 'routing'?}: the pinned copy wins over cfg['pin']."""
    pin = dict(cfg.get('pin') or DEFAULTS['pin'])
    try:
        model = json.loads(Path(cfg['pin_source']).read_text()).get('model') or {}
        if model.get('provider') and model.get('model'):
            pin = {'provider': model['provider'], 'model': model['model']}
            if isinstance(model.get('routing'), dict):
                pin['routing'] = model['routing']
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        pass
    return pin


def pin_matches(model: dict, pin: dict) -> bool:
    return model.get('provider') == pin['provider'] and model.get('model') == pin['model']


def apply_pin(doc: dict, pin: dict) -> dict:
    """openunum.json content with the pinned default model (everything else kept)."""
    doc = dict(doc)
    model = dict(doc.get('model') or {})
    model['provider'] = pin['provider']
    model['model'] = pin['model']
    pm = dict(model.get('providerModels') or {})
    pm[pin['provider']] = pin['model']
    model['providerModels'] = pm
    if isinstance(pin.get('routing'), dict):
        model['routing'] = {**(model.get('routing') or {}), **pin['routing']}
    doc['model'] = model
    return doc


def pin_config_file(path: Path, pin: dict) -> str:
    """'ok' | 'restored' | 'error: ...'. Only call while OpenUnum is stopped."""
    try:
        doc = json.loads(path.read_text())
    except (OSError, ValueError) as e:
        return f'error: {type(e).__name__}: {e}'
    model = doc.get('model') or {}
    routing_ok = all((model.get('routing') or {}).get(k) == v for k, v in (pin.get('routing') or {}).items())
    if pin_matches(model, pin) and routing_ok:
        return 'ok'
    new = apply_pin(doc, pin)
    tmp = path.with_name(path.name + '.keepalive-tmp')
    try:
        tmp.write_text(json.dumps(new, indent=2))
        st = path.stat()
        os.chown(tmp, st.st_uid, st.st_gid)
        os.chmod(tmp, st.st_mode & 0o7777)
        os.replace(tmp, path)
    except OSError as e:
        return f'error: {type(e).__name__}: {e}'
    return 'restored'


def api_json(method: str, path: str, body: dict | None = None, timeout: float = 15) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(OPENUNUM_API + path, data=data, method=method,
                                 headers={'content-type': 'application/json'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read() or b'{}')


# ------------------------------------------------------------------ the loop

class Keepalive:
    def __init__(self, cfg: dict, state: Path = STATE, services: dict | None = None,
                 api=api_json, config_path: Path = OPENUNUM_CONFIG):
        self.cfg = cfg
        self.state = state
        self.state.mkdir(parents=True, exist_ok=True)
        self.skipped: dict = {}
        if services is None:
            services, self.skipped = build_services(cfg)
        self.services = services
        self.api = api
        self.config_path = config_path
        self.pin_next_try = 0.0
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
        svc = self.services.get(name)
        unhealthy_s = float(getattr(svc, 'spec', {}).get('unhealthy_s', self.cfg['unhealthy_s']))
        if running and now - self.bad_since[name] < unhealthy_s:
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
        if name == 'openunum' and self.cfg.get('pin_model'):
            self.pin_file_while_stopped(svc)
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

    def pin_file_while_stopped(self, svc) -> str:
        for _ in range(10):                    # graceful shutdown takes up to 5 s
            if not svc.running():
                break
            time.sleep(1)
        else:
            self.log(event='model_pin_skipped', reason='openunum still running')
            return 'skipped'
        result = pin_config_file(self.config_path, load_pin(self.cfg))
        if result != 'ok':
            self.log(event='model_pin', via='file', result=result)
        return result

    def pin_via_api(self, now: float) -> str:
        """OpenUnum is up: undo a default-model drift with its own switch API."""
        if now < self.pin_next_try:
            return 'wait'
        pin = load_pin(self.cfg)
        try:
            cur = self.api('GET', '/api/model/current')
        except Exception as e:  # noqa: BLE001 - never kill the loop
            return f'error: {type(e).__name__}'
        if pin_matches(cur, pin):
            return 'ok'
        try:
            out = self.api('POST', '/api/model/switch', {'provider': pin['provider'], 'model': pin['model']})
            ok = out.get('ok', True) is not False and pin_matches(out, pin)
        except Exception as e:  # noqa: BLE001
            out, ok = {'error': f'{type(e).__name__}: {e}'}, False
        if not ok:                             # e.g. route blocked: do not hammer the API
            self.pin_next_try = now + float(self.cfg['pin_retry_s'])
        self.log(event='model_pin', via='api', result='restored' if ok else 'failed',
                 was={'provider': cur.get('provider'), 'model': cur.get('model')}, answer=out)
        return 'restored' if ok else 'failed'

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
        ou = result.get('openunum')
        if self.cfg.get('pin_model') and fix and ou and ou['healthy']:
            result['model_pin'] = self.pin_via_api(now)
        result['mute'] = self.mute_check('after_restart' if restarted else 'periodic')
        return result

    def run(self) -> None:
        self.log(event='start', pid=os.getpid(), services=list(self.services),
                 skipped=self.skipped, cfg=self.cfg)
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
