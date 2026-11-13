#!/usr/bin/env python3
"""s22-uplink-failover: rig-side failover to the phone's LTE share (stdlib only, runs as root).

Plan: docs/P4_UPLINK_FAILOVER_PLAN.md ("Rig failover"). Every interval the MAIN uplink is probed with
packets carrying fwmark 0x178; a policy rule sends marked packets to table 178, whose only route is the
DHCP gateway on dev, so the probe never rides the phone, even while failover is active (no host routes).
`down_after` failures: ask the phone to share (uplink.share on) and, only if it says ok, add a low-metric
default route via the phone and put failover_dns on dev. Back to normal only after `up_after` successes AND
`min_failover_s` of failover; the phone is polled (uplink.status) while failing over. No gateway on dev is
"unknown", not "down". Repeated failovers within an hour double the retry gap (60 .. 900 s).

Before and during failover a second marked path (fwmark 0x179 -> table 179 -> the phone) is checked with a
real content check: GET /cdn-cgi/trace over certificate-verified HTTPS must answer 200 with a colo= line (a
carrier portal cannot pass the TLS check; a redirect or a dead path does not count either). In normal mode the main line's public address is learned over the main marked path
(0x178); a trace through the phone that shows the SAME address went the wrong way and counts as failed
(via_path_wrong_way). A phone whose path is dead is refused (phone_path_dead) or left again. Just before the
default route is replaced the main uplink is probed once more (failover_refused reason main_back). While the
verification runs STATE carries verifying: true. SIGTERM interrupts the verification (reason stopping).

  s22-uplink-failover [--config FILE] run | once | status | sweep | selftest [--seconds N]
                                       (once/status/sweep/selftest print JSON; selftest: main uplink fine,
                                        shares briefly, probes the phone path on its own mark 0x17a / table 180 /
                                        rule 1180, never touches route or DNS)

once, sweep and selftest serialise with the daemon through /run/unum-uplink-failover.lock (wait up to 120 s).

Config JSON: phone_gw (optional override/fallback: the phone's wlan0 address is asked from the phone's
uplink.status "wlan0_ipv4" at every failover/probe decision and used when it is a private IPv4 on dev's
subnet that answers the neighbour table; any failure falls back to phone_gw with a warning in the audit
log), api, token_file (required; token default location /etc/unum/s22-device.token,
root:root 600); dev, targets, interval_s, down_after, up_after, min_failover_s, metric, failover_dns,
keepalive_s (normal mode: ask the phone's uplink.status this often to keep the path warm; 0 = off),
verify_targets (IPv4 only, probed through the phone over verified HTTPS, default 1.1.1.1 and 1.0.0.1),
verify_tries 1..10 (default 3, attempts for the FIRST success), verify_hold 1..10 (default 3, consecutive
successes required, the path must keep answering) and verify_gap_s 0..30 (default 8, pause between probes)
before failover, path_check_s 5..300 (default 10) and path_fail_limit 1..10 (default 3) during failover.
"""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import ipaddress
import json
import os
import re
import signal
import socket
import ssl
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
LOCK = Path('/run/unum-uplink-failover.lock')
LOCK_WAIT_S = 120
DEFAULTS = {'dev': 'eno1', 'targets': ['1.1.1.1', '9.9.9.9'], 'interval_s': 10, 'down_after': 3,
            'up_after': 6, 'min_failover_s': 300, 'metric': 50, 'failover_dns': ['1.0.0.1', '149.112.112.112'],
            'keepalive_s': 300, 'verify_targets': ['1.1.1.1', '1.0.0.1'], 'verify_tries': 3, 'verify_hold': 3,
            'verify_gap_s': 8, 'path_check_s': 10, 'path_fail_limit': 3}
INT_RANGES = {'verify_tries': (1, 10), 'verify_hold': (1, 10), 'verify_gap_s': (0, 30), 'path_check_s': (5, 300), 'path_fail_limit': (1, 10)}
MARK, TABLE, RULE_PRIO = 0x178, 178, 1178   # probe packets: fwmark -> table -> DHCP gateway on dev
VIA_MARK, VIA_TABLE, VIA_RULE_PRIO = 0x179, 179, 1179   # phone-path probes: fwmark -> table -> the phone
ST_MARK, ST_TABLE, ST_RULE_PRIO = 0x17a, 180, 1180     # selftest only: never shares state with the daemon's path
WRONG_WAY_S = 600     # via_path_wrong_way is audited at most this often
TRACE_REQ = 'GET /cdn-cgi/trace HTTP/1.0\r\nHost: {}\r\n\r\n'
RETRY_S = 60          # minimum gap between refused/failed failover attempts (doubles on flapping, max GAP_MAX)
GAP_MAX = 900
FLAP_WINDOW_S = 3600  # a failover this soon after the previous one doubles the gap; this long in normal resets it
DNS_FAIL_S = 600      # a failing DNS restore is audited at most this often
STATUS_S = 30         # poll the phone's uplink.status at most this often while failing over
STOP = threading.Event()


class ConfigError(Exception):
    pass


class LockBusy(Exception):
    pass


@contextlib.contextmanager
def hold_lock(wait_s: float):
    """Exclusive flock on LOCK for the duration; LockBusy when it cannot be had within wait_s (or on STOP)."""
    try:
        fd = os.open(LOCK, os.O_RDWR | os.O_CREAT, 0o600)
    except OSError as e:
        raise LockBusy(f'cannot open lock: {e}')
    try:
        deadline = time.monotonic() + wait_s
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if STOP.is_set() or time.monotonic() >= deadline:
                    raise LockBusy('another uplink-failover run holds the lock')
                time.sleep(0.2)
        yield
    finally:
        os.close(fd)


def now() -> float:
    return time.time()


def pause(seconds: float) -> None:
    STOP.wait(seconds)


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
    for k in ('targets', 'failover_dns', 'verify_targets'):
        v = cfg[k]
        try:
            if not isinstance(v, list) or not v:
                raise ValueError
            for a in v:
                ip = ipaddress.ip_address(a)
                if k == 'verify_targets' and (not isinstance(a, str) or ip.version != 4):
                    raise ValueError
        except (ValueError, TypeError):
            raise ConfigError(f'{k} must be a non-empty list of {"IPv4 " if k == "verify_targets" else ""}IP addresses')
    for k, (lo, hi) in INT_RANGES.items():
        if not isinstance(cfg[k], int) or isinstance(cfg[k], bool) or not lo <= cfg[k] <= hi:
            raise ConfigError(f'{k} must be an integer from {lo} to {hi}')
    cfg['phone_gw'] = str(gw)
    return cfg


def _json_cmd(argv: list[str]) -> list:
    try:
        v = json.loads(run(argv, check=False).stdout or '[]')
    except (OSError, ValueError, subprocess.SubprocessError):
        return []
    return v if isinstance(v, list) else []


def validate_phone_ip(cfg: dict, raw) -> tuple[str | None, str]:
    """(address, '') when raw is a private IPv4 on dev's subnet, not the rig's own and REACHABLE; else (None, why)."""
    try:
        ip = ipaddress.ip_address(str(raw))
    except ValueError:
        return None, 'not_an_ip'
    if ip.version != 4 or not ip.is_private:
        return None, 'not_private_ipv4'
    nets, own = [], set()
    for iface in _json_cmd(['ip', '-j', '-4', 'addr', 'show', 'dev', cfg['dev']]):
        for a in iface.get('addr_info') or []:
            try:
                nets.append(ipaddress.ip_interface(f"{a['local']}/{a['prefixlen']}").network)
                own.add(ipaddress.ip_address(a['local']))
            except (KeyError, ValueError, TypeError):
                continue
    if not any(ip in n for n in nets):
        return None, 'off_subnet'
    if ip in own:
        return None, 'own_address'

    def reachable() -> bool:
        return any(isinstance(n, dict) and 'REACHABLE' in (n.get('state') or [])
                   for n in _json_cmd(['ip', '-j', 'neigh', 'show', str(ip), 'dev', cfg['dev']]))
    if reachable():
        return str(ip), ''
    run_ok(['ping', '-c1', '-W1', '-I', cfg['dev'], str(ip)])   # make the kernel (re)resolve the neighbour
    return (str(ip), '') if reachable() else (None, 'neighbour_not_reachable')


def resolve_phone_gw(cfg: dict) -> str:
    """Set cfg['phone_gw'] to the phone's current wlan0 address; fall back to the configured one. Returns it."""
    fallback = cfg.setdefault('phone_gw_cfg', cfg['phone_gw'])
    ps = phone_status(cfg)
    got, why = None, 'phone_unreachable'
    if ps is not None:
        got, why = (validate_phone_ip(cfg, ps['wlan0_ipv4']) if ps.get('wlan0_ipv4')
                    else (None, 'no_wlan0_ipv4'))
    gw = got or fallback
    note = (gw, why)
    if cfg.get('_gw_noted') != note:   # log on change only, not at every probe
        cfg['_gw_noted'] = note
        if got is None:
            audit('phone_gw_fallback', reason=why, using=gw)
        elif got != fallback:
            audit('phone_gw_changed', configured=fallback, resolved=got)
    cfg['phone_gw'] = gw
    return gw


def adopt_live_gw(cfg: dict, st: dict) -> None:
    """After a restart the installed phone route is at the address the state remembers, not the configured one."""
    try:
        ip = ipaddress.ip_address(str(st.get('phone_gw_live')))
    except ValueError:
        return
    if ip.version == 4 and ip.is_private:
        cfg.setdefault('phone_gw_cfg', cfg['phone_gw'])
        cfg['phone_gw'] = str(ip)


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
    _ensure_path(cfg, gw, MARK, TABLE, RULE_PRIO)


def _ensure_path(cfg: dict, gw: str, mark: int, table: int, prio: int) -> None:
    run(['ip', 'route', 'replace', 'default', 'via', gw, 'dev', cfg['dev'], 'table', str(table)])
    r = run(['ip', '-j', 'rule', 'show', 'priority', str(prio)], check=False)
    try:
        have = json.loads(r.stdout or '[]')
    except ValueError:
        have = []
    if not (isinstance(have, list) and have):
        run(['ip', 'rule', 'add', 'fwmark', hex(mark), 'lookup', str(table), 'priority', str(prio)])


def _cleanup_path(mark: int, table: int, prio: int) -> None:
    run_ok(['ip', 'rule', 'del', 'fwmark', hex(mark), 'lookup', str(table), 'priority', str(prio)])
    run_ok(['ip', 'route', 'flush', 'table', str(table)])


def cleanup_probe_path() -> None:
    _cleanup_path(MARK, TABLE, RULE_PRIO)


def ensure_via_path(cfg: dict, mark: int = VIA_MARK, table: int = VIA_TABLE, prio: int = VIA_RULE_PRIO) -> None:
    """The table holds only the phone gateway; packets with the mark use it, so a probe rides the phone.
    Defaults are the daemon's 0x179/179/1179; the selftest passes its own ST_* so the two never collide."""
    _ensure_path(cfg, cfg['phone_gw'], mark, table, prio)


def cleanup_via_path(mark: int = VIA_MARK, table: int = VIA_TABLE, prio: int = VIA_RULE_PRIO) -> None:
    _cleanup_path(mark, table, prio)


def nm_recheck() -> None:
    """Let NetworkManager re-check the wired link at once instead of keeping its route penalty for minutes."""
    run_ok(['nmcli', 'networking', 'connectivity', 'check'])


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


def parse_trace(raw: bytes) -> dict | None:
    """A 200 answer whose body has a non-empty colo= line (what /cdn-cgi/trace returns); anything else is None."""
    head, sep, body = raw.partition(b'\r\n\r\n')
    if not sep:
        head, sep, body = raw.partition(b'\n\n')
    if not sep:
        return None
    lines = head.decode('latin-1').splitlines()
    if not lines or not re.match(r'HTTP/1\.\d 200(\s|$)', lines[0]):
        return None
    kv: dict[str, str] = {}
    for line in body.decode('latin-1').splitlines():
        k, eq, v = line.partition('=')
        if eq:
            kv.setdefault(k.strip(), v.strip())
    if not kv.get('colo'):
        return None
    return {'ip': kv.get('ip', '')[:64], 'colo': kv['colo'][:16]}


def trace_probe(dev: str, target: str, mark: int) -> dict | None:
    """GET /cdn-cgi/trace over IPv4 HTTPS (port 443, certificate verified against the IP) pinned to dev; mark 0
    means no SO_MARK. {'ip','colo'} or None. The certificate check is what keeps a carrier portal from passing."""
    s = raw = None
    try:
        raw = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        raw.setsockopt(socket.SOL_SOCKET, getattr(socket, 'SO_BINDTODEVICE', 25), dev.encode())
        if mark:
            raw.setsockopt(socket.SOL_SOCKET, getattr(socket, 'SO_MARK', 36), mark)
        raw.settimeout(4)
        raw.connect((target, 443))
        s = ssl.create_default_context().wrap_socket(raw, server_hostname=target)
        s.sendall(TRACE_REQ.format(target).encode())
        data, deadline = b'', time.monotonic() + 8
        while len(data) < 4096 and time.monotonic() < deadline:
            chunk = s.recv(4096 - len(data))
            if not chunk:
                break
            data += chunk
        return parse_trace(data)
    except Exception:
        return None
    finally:
        for sock in (s, raw):
            if sock is not None:
                sock.close()


def first_trace(cfg: dict, mark: int) -> dict | None:
    """The trace of the first verify_target that answers over the given mark."""
    for t in cfg['verify_targets']:
        r = trace_probe(cfg['dev'], t, mark)
        if r is not None:
            return r
    return None


def probe_via_phone(cfg: dict, st: dict) -> bool:
    """True only if a trace answers through the phone path AND does not show the main line's own address."""
    r = first_trace(cfg, VIA_MARK)
    if r is None:
        return False
    if st.get('main_ip') and r['ip'] == st['main_ip']:
        if now() - st.get('wrong_way_at', 0) >= WRONG_WAY_S:
            st['wrong_way_at'] = now()
            audit('via_path_wrong_way')
        return False
    return True


def learn_main_ip(cfg: dict, st: dict, t: float) -> None:
    """Normal mode, main line up, at the keepalive cadence: remember the main line's public address."""
    if t - st['mainip_at'] < (cfg['keepalive_s'] or 300):
        return
    st['mainip_at'] = t
    r = first_trace(cfg, MARK)
    if r is not None and r['ip']:
        st['main_ip'] = r['ip']


def probe_main(cfg: dict, gw: str) -> bool:
    """UP if any target answers by ICMP or TCP over the main gateway."""
    try:
        ensure_probe_path(cfg, gw)
    except (RuntimeError, OSError, subprocess.SubprocessError):
        return False
    return any(run(['ping', '-c1', '-W2', '-I', cfg['dev'], '-m', hex(MARK), t], check=False).returncode == 0
               or tcp_probe(cfg['dev'], t) for t in cfg['targets'])


def main_is_up(cfg: dict) -> bool:
    gw = main_gateway(cfg)
    return gw is not None and probe_main(cfg, gw)


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
    if code != 200 or not body:
        return None
    # s22d answers {ok, cap, effect, state: {on, rig_configured, ...}}; hand back the state fields.
    inner = body.get('state')
    return {**body, **inner} if isinstance(inner, dict) else body


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
            'phone_ok': None, 'path_at': 0, 'path_fails': 0, 'verifying': False, 'main_ip': None,
            'mainip_at': 0, 'wrong_way_at': 0}


def sweep(cfg: dict) -> dict:
    """Tear everything down and tell the phone to stop sharing (idempotent, whatever STATE says)."""
    old = read_json(STATE)  # dns_prev lives here: read it before the state is reset
    adopt_live_gw(cfg, old)
    run_ok(route_argv(cfg, 'del'))
    if cfg['phone_gw'] != cfg.get('phone_gw_cfg', cfg['phone_gw']):
        run_ok(['ip', 'route', 'del', 'default', 'via', cfg['phone_gw_cfg'], 'dev', cfg['dev'], 'metric', str(cfg['metric'])])
    cleanup_probe_path()
    cleanup_via_path()
    restore_dns(cfg, old)
    ok, why = share(cfg, False)
    nm_recheck()
    audit('failover_off', reason='sweep', ok=ok, detail=why)
    keep = ('gap', 'last_failover', 'normal_since', 'phone_seen', 'phone_ok', 'main_ip', 'wrong_way_at')
    st = {**fresh(), **{k: old[k] for k in keep if k in old}}
    write_json(STATE, st)
    return st


def leave_failover(cfg: dict, st: dict, reason: str) -> None:
    t = now()
    run_ok(route_argv(cfg, 'del'))
    cleanup_via_path()
    restore_dns(cfg, st)
    nm_recheck()
    ok, why = share(cfg, False)
    st.pop('phone_gw_live', None)
    st.update(mode='normal', sharing=False, fails=0, oks=0, last_try=t,
              normal_since=t, status_fails=0, path_fails=0)
    audit('failover_off', reason=reason, ok=ok, detail=why)


def verify_phone_path(cfg: dict, st: dict) -> tuple[int, str]:
    """Via path up, then a first success within verify_tries, then verify_hold-1 more after verify_gap_s each.
    Returns (probes_used, stage): 'ok' when it holds, else where it failed: 'setup' (via path), 'first',
    'hold', or 'stopping' (STOP is checked after every pause and before every probe)."""
    if STOP.is_set():
        return 0, 'stopping'
    try:
        ensure_via_path(cfg)
    except (RuntimeError, OSError, subprocess.SubprocessError):
        return 0, 'setup'
    used = 0
    for i in range(cfg['verify_tries']):
        if i:
            pause(cfg['verify_gap_s'])
        if STOP.is_set():
            return used, 'stopping'
        used += 1
        if probe_via_phone(cfg, st):
            break
    else:
        return used, 'first'
    for _ in range(cfg['verify_hold'] - 1):
        pause(cfg['verify_gap_s'])
        if STOP.is_set():
            return used, 'stopping'
        used += 1
        if not probe_via_phone(cfg, st):
            return used, 'hold'
    return used, 'ok'


def enter_failover(cfg: dict, st: dict, t: float) -> None:
    st['last_try'] = t
    resolve_phone_gw(cfg)
    if not run_ok(['ip', '-j', 'route', 'get', cfg['phone_gw'], 'oif', cfg['dev']]):
        audit('failover_refused', reason='no_path')
        return
    st['verifying'] = True
    write_json(STATE, st)   # visible to `status` for the whole verification
    try:
        _enter_failover(cfg, st, t)
    finally:
        st['verifying'] = False
        if st['mode'] != 'failover':
            st['sharing'] = False
        write_json(STATE, st)


def _abort_share(cfg: dict) -> None:
    share(cfg, False)
    cleanup_via_path()


def _enter_failover(cfg: dict, st: dict, t: float) -> None:
    ok, why = share(cfg, True)
    if not ok:
        share(cfg, False)  # the phone may have switched on although we did not hear it
        audit('failover_refused', reason='phone', detail=why)
        return
    st['sharing'] = True
    write_json(STATE, st)
    committed = False
    try:
        used, stage = verify_phone_path(cfg, st)
        if stage != 'ok':
            _abort_share(cfg)
            if stage == 'stopping':
                audit('failover_refused', reason='stopping', probes=used)
            else:
                audit('failover_refused', reason='phone_path_dead', tries=cfg['verify_tries'],
                      hold=cfg['verify_hold'], stage=stage, probes=used)
            return
        if main_is_up(cfg):   # last look: the main line may have come back during the verification
            _abort_share(cfg)
            st['fails'] = 0
            audit('failover_refused', reason='main_back')
            return
        try:
            run(route_argv(cfg, 'replace'))
        except RuntimeError as e:
            _abort_share(cfg)
            audit('failover_refused', reason='route', detail=str(e)[:200])
            return
        if st['last_failover'] and t - st['last_failover'] < FLAP_WINDOW_S:
            st['gap'] = min(st['gap'] * 2, GAP_MAX)
        t2 = now()   # the verification took a while: the first status/path checks are due from here
        st['phone_gw_live'] = cfg['phone_gw']
        st.update(mode='failover', sharing=True, oks=0, failover_since=t, last_failover=t, status_at=t2,
                  status_fails=0, path_at=t2, path_fails=0)
        write_json(STATE, st)  # state first: the route exists now, a crash must not orphan it
        committed = True
    except BaseException as e:
        if committed:
            raise
        _abort_share(cfg)
        if not isinstance(e, Exception):
            raise
        audit('failover_refused', reason='error', detail=f'{type(e).__name__}: {str(e)[:150]}')
        return
    set_dns(cfg, st)
    audit('failover_on', metric=cfg['metric'], gap=st['gap'], verify_probes=used, held=cfg['verify_hold'])


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
        adopt_live_gw(cfg, st)
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
        if t - st['path_at'] >= cfg['path_check_s']:
            st['path_at'] = t
            old_gw = cfg['phone_gw']
            if resolve_phone_gw(cfg) != old_gw:   # the phone's DHCP address moved: follow it
                run_ok(['ip', 'route', 'del', 'default', 'via', old_gw, 'dev', cfg['dev'], 'metric', str(cfg['metric'])])
                run_ok(route_argv(cfg, 'replace'))
                st['phone_gw_live'] = cfg['phone_gw']
            try:
                ensure_via_path(cfg)
            except (RuntimeError, OSError, subprocess.SubprocessError):
                pass
            st['path_fails'] = 0 if probe_via_phone(cfg, st) else st['path_fails'] + 1
            if st['path_fails'] >= cfg['path_fail_limit']:
                leave_failover(cfg, st, 'phone_path_dead')
                write_json(STATE, st)
                return st
    else:
        adopt_live_gw(cfg, st)
        run_ok(route_argv(cfg, 'del'))  # the phone route must never exist in normal mode
        cleanup_via_path()  # no verification runs between iterations
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
        if up:
            learn_main_ip(cfg, st, t)
        if st['fails'] == cfg['down_after']:
            audit('down', fails=st['fails'])
        if st['fails'] >= cfg['down_after'] and t - st['last_try'] >= st['gap']:
            enter_failover(cfg, st, t)
    elif st['oks'] >= cfg['up_after'] and t - st['failover_since'] >= cfg['min_failover_s']:
        audit('up', oks=st['oks'])
        leave_failover(cfg, st, 'main_up')
    write_json(STATE, st)
    return st


def selftest(cfg: dict, seconds: int) -> tuple[dict, int]:
    """Main uplink fine: share on, probe the phone path for `seconds` on its own mark/table/rule, tear down.
    Never touches route or DNS. Samples must not show the main line's own address (addresses are never printed)."""
    cur = read_json(STATE)
    if cur.get('mode') == 'failover' or cur.get('verifying'):
        return {'ok': False, 'error': 'failover is active'}, 1
    samples: list[dict] = []
    share_ok, error, main_ip = False, None, None
    try:
        # The main line's address first, before the phone shares: it does not depend on the share.
        r = run(['ip', '-j', 'rule', 'show', 'priority', str(RULE_PRIO)], check=False)
        try:
            marked = bool(json.loads(r.stdout or '[]'))
        except ValueError:
            marked = False
        m = first_trace(cfg, MARK if marked else 0)   # over table 178 only when its rule exists
        main_ip = m['ip'] if m and m['ip'] else None
        share_ok, _ = share(cfg, True)
        if share_ok:
            resolve_phone_gw(cfg)
            ensure_via_path(cfg, ST_MARK, ST_TABLE, ST_RULE_PRIO)
            start = now()
            for i in range(seconds // 5 + 1):
                if i:
                    pause(5)
                r = first_trace(cfg, ST_MARK)
                differs = None if r is None or main_ip is None else r['ip'] != main_ip
                samples.append({'t': round(now() - start, 1), 'ok': r is not None and differs is not False,
                                'ip_differs': differs})
    except (RuntimeError, OSError, subprocess.SubprocessError) as e:
        error = f'{type(e).__name__}: {str(e)[:120]}'
    finally:
        cleanup_via_path(ST_MARK, ST_TABLE, ST_RULE_PRIO)
        cur = read_json(STATE)   # the daemon owns the share once it is verifying or in failover
        if not (cur.get('mode') == 'failover' or cur.get('verifying')):
            share(cfg, False)
    passed = sum(x['ok'] for x in samples)
    failed = len(samples) - passed
    first = next((x['t'] for x in samples if not x['ok']), None)
    ok = share_ok and error is None and failed == 0 and len(samples) >= 3 \
         and samples[-1]['t'] - samples[0]['t'] >= 15
    audit('selftest', ok=ok, passed=passed, failed=failed)
    res = {'ok': ok, 'share_ok': share_ok, 'main_ip_known': main_ip is not None, 'samples': samples,
           'passed': passed, 'failed': failed, 'first_fail_t': first}
    if error:
        res['error'] = error
    return res, 0 if ok else 1


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
                with hold_lock(LOCK_WAIT_S):
                    once(cfg)
            except LockBusy as e:
                audit('lock_busy', detail=str(e)[:100])
            except Exception as e:  # never die on one bad iteration
                audit('error', detail=f'{type(e).__name__}: {str(e)[:150]}')
            STOP.wait(cfg['interval_s'])
    finally:
        sweep(cfg)
        audit('stop')


def _raise_term(signum, _frame):
    raise SystemExit(128 + signum)   # unwinds through selftest's finally


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog='s22-uplink-failover')
    ap.add_argument('--config', default=str(CONF))
    ap.add_argument('cmd', choices=['run', 'once', 'status', 'sweep', 'selftest'])
    ap.add_argument('--seconds', type=int, default=40, metavar='N', help='selftest duration, 10..120')
    a = ap.parse_args(argv)
    if not 10 <= a.seconds <= 120:
        ap.error('--seconds must be 10..120')
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
    try:
        with hold_lock(LOCK_WAIT_S):
            if a.cmd == 'selftest':
                old = {sg: signal.signal(sg, _raise_term) for sg in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT)}
                try:
                    res, rc = selftest(cfg, a.seconds)
                finally:
                    for sg, h in old.items():
                        signal.signal(sg, h)
                print(json.dumps(res))
                return rc
            print(json.dumps(once(cfg) if a.cmd == 'once' else sweep(cfg)))
            return 0
    except LockBusy as e:
        print(json.dumps({'ok': False, 'error': f'lock: {e}'}))
        return 1


if __name__ == '__main__':
    sys.exit(main())
