#!/usr/bin/env python3
"""s22-uplink-failover: rig-side failover to the phone's LTE share (stdlib only, runs as root).

Plan: docs/P4_UPLINK_FAILOVER_PLAN.md ("Rig failover"). Every interval the MAIN uplink is probed with
packets carrying fwmark 0x178; a policy rule sends marked packets to table 178, whose only route is the
DHCP gateway on dev, so the probe never rides the phone, even while failover is active (no host routes).
`down_after` failures: ask the phone to share (uplink.share on) and, only if it says ok, add a low-metric
default route via the phone and put failover_dns on dev. Back to normal only after `up_after` successes AND
`min_failover_s` of failover; the phone is polled (uplink.status) while failing over. No gateway on dev is
"unknown", not "down". Repeated failovers within an hour double the retry gap (60 .. 900 s).

  s22-uplink-failover [--config FILE] run | once | status | sweep      (once/status/sweep print JSON)

Config JSON: phone_gw, api, token_file (required; token default location /etc/unum/s22-device.token,
root:root 600); dev, targets, interval_s, down_after, up_after, min_failover_s, metric, failover_dns,
keepalive_s (normal mode: ask the phone's uplink.status this often to keep the path warm; 0 = off).
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

CONF = Path('/etc/unum/uplink-failover.json')
STATE = Path('/run/unum-uplink-failover.json')
AUDIT = Path('/var/log/unum-uplink-failover.jsonl')
DEFAULTS = {'dev': 'eno1', 'targets': ['1.1.1.1', '9.9.9.9'], 'interval_s': 10, 'down_after': 3,
            'up_after': 6, 'min_failover_s': 300, 'metric': 50, 'failover_dns': ['1.0.0.1', '149.112.112.112'],
            'keepalive_s': 300}
MARK, TABLE, RULE_PRIO = 0x178, 178, 1178   # probe packets: fwmark -> table -> DHCP gateway on dev
RETRY_S = 60          # minimum gap between refused/failed failover attempts (doubles on flapping, max GAP_MAX)
GAP_MAX = 900
FLAP_WINDOW_S = 3600  # a failover this soon after the previous one doubles the gap; this long in normal resets it
DNS_FAIL_S = 600      # a failing DNS restore is audited at most this often
STATUS_S = 30         # poll the phone's uplink.status at most this often while failing over
STOP = threading.Event()


class ConfigError(Exception):
    pass


def now() -> float:
    return time.time()


def run(argv: list[str], check: bool = True) -> subprocess.CompletedProcess:
    r = subprocess.run(argv, capture_output=True, text=True, timeout=30)
    if check and r.returncode != 0:
        raise RuntimeError(f'{" ".join(argv[:5])}: rc={r.returncode} {r.stderr.strip()[:200]}')
    return r


def run_ok(argv: list[str]) -> bool:
    """Best-effort command: True when it ran and returned 0, never raises."""
    try:
        return run(argv, check=False).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _http(req: urllib.request.Request, timeout: float) -> tuple[int, dict]:
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            code, raw = r.status, r.read()
    except urllib.error.HTTPError as e:
        code, raw = e.code, b''
    try:
        body = json.loads(raw or b'{}')
    except ValueError:
        body = {}
    return code, body if isinstance(body, dict) else {}


def http_post(url: str, token: str, payload: dict, timeout: float) -> tuple[int, dict]:
    return _http(urllib.request.Request(url, data=json.dumps(payload).encode(), method='POST', headers={
        'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}), timeout)


def http_get(url: str, token: str, timeout: float) -> tuple[int, dict]:
    return _http(urllib.request.Request(url, method='GET', headers={'Authorization': f'Bearer {token}'}), timeout)


def read_json(p: Path) -> dict:
    try:
        v = json.loads(p.read_text())
        return v if isinstance(v, dict) else {}
    except (OSError, ValueError):
        return {}


def write_json(p: Path, v: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + '.tmp')
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as f:
        json.dump(v, f)
    os.replace(tmp, p)


def audit(event: str, **kw) -> None:
    """Best-effort: a full disk or a bad log path must never break the failover logic."""
    try:
        AUDIT.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(AUDIT, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        ts = datetime.fromtimestamp(now(), timezone.utc).isoformat(timespec='seconds')
        with os.fdopen(fd, 'a') as f:
            f.write(json.dumps({'ts': ts, 'event': event, **kw}) + '\n')
    except OSError:
        pass


def load_config(path: Path) -> dict:
    try:
        raw = json.loads(Path(path).read_text())
    except (OSError, ValueError) as e:
        raise ConfigError(f'cannot read config {path}: {e}')
    if not isinstance(raw, dict):
        raise ConfigError('config must be a JSON object')
    cfg = {**DEFAULTS, **raw}
    try:
        gw = ipaddress.ip_address(str(cfg.get('phone_gw')))
    except ValueError:
        raise ConfigError('phone_gw is not an IP address')
    if gw.version != 4 or not gw.is_private:
        raise ConfigError('phone_gw must be a private IPv4 address')
    if not str(cfg.get('api') or '').startswith(('http://', 'https://')):
        raise ConfigError('api must be an http(s) URL')
    tf = Path(str(cfg.get('token_file') or ''))
    try:
        st = tf.stat()
        if not tf.is_file() or st.st_mode & 0o077:
            raise ConfigError('token_file must be a file readable by its owner only (chmod 600)')
        cfg['token'] = tf.read_text().strip()
    except OSError:
        raise ConfigError('token_file is missing or unreadable')
    if not cfg['token']:
        raise ConfigError('token_file is empty')
    for k in ('interval_s', 'down_after', 'up_after', 'metric'):
        if not isinstance(cfg[k], int) or cfg[k] < 1:
            raise ConfigError(f'{k} must be a positive integer')
    for k in ('min_failover_s', 'keepalive_s'):
        if not isinstance(cfg[k], int) or cfg[k] < 0:
            raise ConfigError(f'{k} must be a non-negative integer')
    if not isinstance(cfg['dev'], str) or not cfg['dev'] or cfg['dev'].startswith('-'):
        raise ConfigError('dev must be an interface name')
    for k in ('targets', 'failover_dns'):
        v = cfg[k]
        try:
            if not isinstance(v, list) or not v:
                raise ValueError
            for a in v:
                ipaddress.ip_address(a)
        except (ValueError, TypeError):
            raise ConfigError(f'{k} must be a non-empty list of IP addresses')
    cfg['phone_gw'] = str(gw)
    return cfg


def main_gateway(cfg: dict) -> str | None:
    """The DHCP gateway: the default route on dev whose gateway is not the phone."""
    r = run(['ip', '-j', 'route', 'show', 'default', 'dev', cfg['dev']], check=False)
    try:
        routes = json.loads(r.stdout or '[]')
    except ValueError:
        return None
    cands = [x for x in routes if isinstance(x, dict) and x.get('gateway') and x['gateway'] != cfg['phone_gw']]
    cands.sort(key=lambda x: x.get('metric', 0))
    return cands[0]['gateway'] if cands else None


def ensure_probe_path(cfg: dict, gw: str) -> None:
    """Table 178 holds only the DHCP gateway; one fwmark rule (never duplicated) sends marked packets to it."""
    run(['ip', 'route', 'replace', 'default', 'via', gw, 'dev', cfg['dev'], 'table', str(TABLE)])
    r = run(['ip', '-j', 'rule', 'show', 'priority', str(RULE_PRIO)], check=False)
    try:
        have = json.loads(r.stdout or '[]')
    except ValueError:
        have = []
    if not (isinstance(have, list) and have):
        run(['ip', 'rule', 'add', 'fwmark', hex(MARK), 'lookup', str(TABLE), 'priority', str(RULE_PRIO)])


def cleanup_probe_path() -> None:
    run_ok(['ip', 'rule', 'del', 'fwmark', hex(MARK), 'lookup', str(TABLE), 'priority', str(RULE_PRIO)])
    run_ok(['ip', 'route', 'flush', 'table', str(TABLE)])


def tcp_probe(dev: str, target: str) -> bool:
    """TCP connect to target:443 pinned to dev and marked like the ping probe."""
    s = socket.socket(socket.AF_INET6 if ':' in target else socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.setsockopt(socket.SOL_SOCKET, getattr(socket, 'SO_BINDTODEVICE', 25), dev.encode())
        s.setsockopt(socket.SOL_SOCKET, getattr(socket, 'SO_MARK', 36), MARK)
        s.settimeout(3)
        s.connect((target, 443))
        return True
    except OSError:
        return False
    finally:
        s.close()


def probe_main(cfg: dict, gw: str) -> bool:
    """UP if any target answers by ICMP or TCP over the main gateway."""
    try:
        ensure_probe_path(cfg, gw)
    except (RuntimeError, OSError, subprocess.SubprocessError):
        return False
    return any(run(['ping', '-c1', '-W2', '-I', cfg['dev'], '-m', hex(MARK), t], check=False).returncode == 0
               or tcp_probe(cfg['dev'], t) for t in cfg['targets'])


def share(cfg: dict, on: bool) -> tuple[bool, str]:
    try:
        code, body = http_post(cfg['api'].rstrip('/') + '/v1/device/uplink.share', cfg['token'], {'on': on},
                               100 if on else 20)
    except Exception as e:
        return False, f'{type(e).__name__}: {str(e)[:120]}'
    return (code == 200 and body.get('ok') is True), f'http {code}'


def phone_status(cfg: dict) -> dict | None:
    try:
        code, body = http_get(cfg['api'].rstrip('/') + '/v1/device/uplink.status', cfg['token'], 10)
    except Exception:
        return None
    return body if code == 200 and body else None


def route_argv(cfg: dict, verb: str) -> list[str]:
    return ['ip', 'route', verb, 'default', 'via', cfg['phone_gw'], 'dev', cfg['dev'], 'metric', str(cfg['metric'])]


def read_dns(cfg: dict) -> list[str]:
    """Resolvers now on dev (`Link 2 (eno1): a b`), verbatim, minus our own failover_dns."""
    try:
        r = run(['resolvectl', 'dns', cfg['dev']], check=False)
    except (OSError, subprocess.SubprocessError):
        return []
    lines = (r.stdout or '').splitlines()
    if r.returncode != 0 or not lines or '):' not in lines[0]:
        return []
    return [a for a in lines[0].partition('):')[2].split() if a.split('%')[0] not in cfg['failover_dns']]


def set_dns(cfg: dict, st: dict) -> None:
    if not st.get('dns_prev'):  # a re-entry after a crash must not save the failover resolvers
        st['dns_prev'] = read_dns(cfg)
    st['dns_set'] = True
    write_json(STATE, st)  # the previous resolvers are on disk before we replace them
    if not run_ok(['resolvectl', 'dns', cfg['dev'], *cfg['failover_dns']]):
        audit('dns_failed', action='set')


def restore_dns(cfg: dict, st: dict) -> bool:
    """Put back the saved resolvers; with none saved, revert and let NetworkManager re-apply its own."""
    prev = st.get('dns_prev') or []
    if prev:
        ok = run_ok(['resolvectl', 'dns', cfg['dev'], *prev])
    else:
        ok = run_ok(['resolvectl', 'revert', cfg['dev']])
        run_ok(['nmcli', 'device', 'reapply', cfg['dev']])
    st['dns_set'] = not ok
    if ok:
        st['dns_prev'] = []
    elif now() - st.get('dns_fail_at', 0) >= DNS_FAIL_S:
        st['dns_fail_at'] = now()
        audit('dns_failed', action='restore')
    return ok


def fresh() -> dict:
    return {'mode': 'normal', 'fails': 0, 'oks': 0, 'sharing': False, 'last_try': 0, 'failover_since': 0,
            'last_failover': 0, 'gap': RETRY_S, 'normal_since': 0, 'dns_set': False, 'status_at': 0,
            'status_fails': 0, 'dns_prev': [], 'dns_fail_at': 0, 'keepalive_at': 0, 'phone_seen': None,
            'phone_ok': None}


def sweep(cfg: dict) -> dict:
    """Tear everything down and tell the phone to stop sharing (idempotent, whatever STATE says)."""
    run_ok(route_argv(cfg, 'del'))
    cleanup_probe_path()
    old = read_json(STATE)  # dns_prev lives here: read it before the state is reset
    restore_dns(cfg, old)
    ok, why = share(cfg, False)
    audit('failover_off', reason='sweep', ok=ok, detail=why)
    keep = ('gap', 'last_failover', 'normal_since', 'phone_seen', 'phone_ok')
    st = {**fresh(), **{k: old[k] for k in keep if k in old}}
    write_json(STATE, st)
    return st


def leave_failover(cfg: dict, st: dict, reason: str) -> None:
    t = now()
    run_ok(route_argv(cfg, 'del'))
    restore_dns(cfg, st)
    ok, why = share(cfg, False)
    st.update(mode='normal', sharing=False, fails=0, oks=0, last_try=t,
              normal_since=t, status_fails=0)
    audit('failover_off', reason=reason, ok=ok, detail=why)


def enter_failover(cfg: dict, st: dict, t: float) -> None:
    st['last_try'] = t
    if not run_ok(['ip', '-j', 'route', 'get', cfg['phone_gw'], 'oif', cfg['dev']]):
        audit('failover_refused', reason='no_path')
        return
    ok, why = share(cfg, True)
    if not ok:
        share(cfg, False)  # the phone may have switched on although we did not hear it
        audit('failover_refused', reason='phone', detail=why)
        return
    try:
        run(route_argv(cfg, 'replace'))
    except RuntimeError as e:
        share(cfg, False)
        audit('failover_refused', reason='route', detail=str(e)[:200])
        return
    if st['last_failover'] and t - st['last_failover'] < FLAP_WINDOW_S:
        st['gap'] = min(st['gap'] * 2, GAP_MAX)
    st.update(mode='failover', sharing=True, oks=0, failover_since=t, last_failover=t, status_at=t, status_fails=0)
    write_json(STATE, st)  # state first: the route exists now, a crash must not orphan it
    set_dns(cfg, st)
    audit('failover_on', metric=cfg['metric'], gap=st['gap'])


def keepalive(cfg: dict, st: dict, t: float) -> None:
    """Normal mode: ask the phone now and then so the path stays warm. Never touches mode/fails/oks, never POSTs."""
    if cfg['keepalive_s'] <= 0 or t - st['keepalive_at'] < cfg['keepalive_s']:
        return
    st['keepalive_at'] = t
    ps = phone_status(cfg)
    was_up, ever = st['phone_ok'] is not None, st['phone_seen'] is not None
    if ps is None:
        if was_up:
            audit('phone_unreachable', source='keepalive')
        st['phone_ok'] = None
        return
    if ever and not was_up:
        audit('phone_back', source='keepalive')
    st['phone_seen'], st['phone_ok'] = t, ps.get('rig_configured') is True


def once(cfg: dict) -> dict:
    st = {**fresh(), **read_json(STATE)}
    st.pop('note', None)
    t = now()
    if st['mode'] == 'failover':
        run_ok(route_argv(cfg, 'replace'))  # re-assert
        if t - st['status_at'] >= STATUS_S:
            st['status_at'] = t
            ps = phone_status(cfg)
            st['status_fails'] = st['status_fails'] + 1 if ps is None else 0
            reason = ('phone_stopped' if ps is not None and ps.get('on') is False
                      else 'phone_unreachable' if st['status_fails'] >= 3 else None)
            if reason:
                leave_failover(cfg, st, reason)
                write_json(STATE, st)
                return st
    else:
        run_ok(route_argv(cfg, 'del'))  # the phone route must never exist in normal mode
        if st['dns_set']:
            restore_dns(cfg, st)
        if st['gap'] > RETRY_S and t - st['normal_since'] >= FLAP_WINDOW_S:
            st['gap'] = RETRY_S
        keepalive(cfg, st, t)
    gw = main_gateway(cfg)
    if gw is None:  # no DHCP gateway on dev is unknown, not down
        st['note'] = 'no_gateway'
        if st['mode'] == 'normal':
            st.update(fails=0, oks=0)
            write_json(STATE, st)
            return st
    up = gw is not None and probe_main(cfg, gw)
    st['fails'], st['oks'] = (0, st['oks'] + 1) if up else (st['fails'] + 1, 0)
    if st['mode'] == 'normal':
        if st['fails'] == cfg['down_after']:
            audit('down', fails=st['fails'])
        if st['fails'] >= cfg['down_after'] and t - st['last_try'] >= st['gap']:
            enter_failover(cfg, st, t)
    elif st['oks'] >= cfg['up_after'] and t - st['failover_since'] >= cfg['min_failover_s']:
        audit('up', oks=st['oks'])
        leave_failover(cfg, st, 'main_up')
    write_json(STATE, st)
    return st


def startup(cfg: dict) -> str:
    """Adopt a failover that is still live (phone says on); otherwise sweep. Returns 'adopt' or 'sweep'."""
    st = read_json(STATE)
    if st.get('mode') == 'failover':
        ps = phone_status(cfg)
        if ps is not None and ps.get('on') is True:
            audit('adopt', since=st.get('failover_since'))
            return 'adopt'
    sweep(cfg)
    return 'sweep'


def loop(cfg: dict) -> None:
    audit('start', dev=cfg['dev'], metric=cfg['metric'], interval_s=cfg['interval_s'])
    startup(cfg)
    try:
        while not STOP.is_set():
            try:
                once(cfg)
            except Exception as e:  # never die on one bad iteration
                audit('error', detail=f'{type(e).__name__}: {str(e)[:150]}')
            STOP.wait(cfg['interval_s'])
    finally:
        sweep(cfg)
        audit('stop')


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog='s22-uplink-failover')
    ap.add_argument('--config', default=str(CONF))
    ap.add_argument('cmd', choices=['run', 'once', 'status', 'sweep'])
    a = ap.parse_args(argv)
    if a.cmd == 'status':
        print(json.dumps(read_json(STATE)))
        return 0
    try:
        cfg = load_config(Path(a.config))
    except ConfigError as e:
        print(f'refusing to start: {e}', file=sys.stderr)
        return 2
    if a.cmd == 'run':
        signal.signal(signal.SIGTERM, lambda *_: STOP.set())
        signal.signal(signal.SIGINT, lambda *_: STOP.set())
        loop(cfg)
        return 0
    print(json.dumps(once(cfg) if a.cmd == 'once' else sweep(cfg)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
