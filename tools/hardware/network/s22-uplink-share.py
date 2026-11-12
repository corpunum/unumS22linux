#!/usr/bin/env python3
"""s22-uplink-share: lend the phone's LTE to the rig while the home internet is down (stdlib only).

Hub U-fe741e10, rules from corpunum-82 (2026-11-12, docs/P4_UPLINK_FAILOVER_PLAN.md):
* only the rig: forwarding and NAT for one source address (the rig's LAN IP, from the private
  /srv/s22/state/uplink-share.json {"rig": "<ip>"}), everything else that would be forwarded is dropped;
* guards: battery above 40 %, at most 10 GB a day (rmnet0 bytes while sharing), off after 6 h;
* an audit line per enable and disable (/srv/s22/state/audit/uplink-share.jsonl);
* forwarding off again afterwards whatever happens: a failed `on` runs `off`; a watchdog enforces the
  guards; `sweep` (run at s22d start) undoes what a reboot or a dead watchdog left behind.

  s22-uplink-share on | off | status | watch | sweep        (native root; prints one JSON line)

Mechanics: netpolicy mobile_mode "always" (so rmnet0 is up while Wi-Fi works; the previous value is put
back), ip_forward=1, `ip rule from <rig> lookup 177` -> default dev rmnet0, iptables-legacy chains
S22SHARE (filter FORWARD) and S22SHARE_NAT (nat POSTROUTING, MASQUERADE) via the Arch chroot, which shares
the network namespace. The kernel has legacy iptables only (no nftables).
"""
from __future__ import annotations

import ipaddress
import json
import os
import socket
import ssl
import subprocess
import sys
import time
from pathlib import Path

CONF = Path(os.environ.get('S22_SHARE_CONF', '/srv/s22/state/uplink-share.json'))
STATE = Path(os.environ.get('S22_SHARE_STATE', '/srv/s22/state/uplink-share/state.json'))
USAGE = Path(os.environ.get('S22_SHARE_USAGE', '/srv/s22/state/uplink-share/usage.json'))
AUDIT = Path(os.environ.get('S22_SHARE_AUDIT', '/srv/s22/state/audit/uplink-share.jsonl'))
NETPOLICY = Path(os.environ.get('S22_NETPOLICY_CONF', '/srv/s22/state/netpolicy/config.json'))
SMS_PAUSED = Path(os.environ.get('S22_SMS_PAUSED', '/srv/s22/state/phoned/sms_paused'))
SWITCHES = Path(os.environ.get('S22_DEVICE_SWITCHES', '/srv/s22/state/device-switches.json'))
NP_STATUS = Path(os.environ.get('S22_NETPOLICY_STATUS', '/run/s22-netpolicy/status.json'))
BATTERY = Path(os.environ.get('S22_BATTERY', '/sys/class/power_supply/battery/capacity'))
NET = Path(os.environ.get('S22_SYS_NET', '/sys/class/net'))
FORWARD = Path(os.environ.get('S22_IP_FORWARD', '/proc/sys/net/ipv4/ip_forward'))
IPT = ['chroot', '/mnt/omarchy-trial', 'iptables-legacy']
DEV, TABLE, PRIO = 'rmnet0', '177', '1177'
MIN_BATTERY = 40                  # stop at <= 40 %
CAP_BYTES = 10 * 1000 ** 3        # 10 GB a day (owner 2026-11-12: unlimited data bundle until 2026-12-12)
MAX_S = 6 * 3600                  # off after 6 h
UP_WAIT_S = 60
PROBE_TARGETS = ('1.1.1.1', '1.0.0.1')   # data-path check: an HTTP answer through the mobile interface
VERIFY_N = 4                      # consecutive good rounds `on` needs
VERIFY_GAP_S = 8
VERIFY_FIRST_TRIES = 3            # rounds allowed to get the first good one
WATCH_FAILS = 3                   # consecutive bad watch rounds before sharing stops


def now() -> float:
    return time.time()


def run(argv: list[str], check: bool = True) -> subprocess.CompletedProcess:
    r = subprocess.run(argv, capture_output=True, text=True, timeout=30)
    if check and r.returncode != 0:
        raise RuntimeError(f'{" ".join(argv[-6:])}: rc={r.returncode} {r.stderr.strip()[:200]}')
    return r


def pause(seconds: float) -> None:
    time.sleep(seconds)


def parse_trace(raw: bytes) -> dict | None:
    """Parse a /cdn-cgi/trace answer. None unless the status is HTTP/1.x 200 and the body has a colo= line
    (a carrier top-up portal answering port 80 with a redirect or a page has neither)."""
    head, _, body = raw.partition(b'\r\n\r\n')
    if not _:
        head, _, body = raw.partition(b'\n\n')
    lines = head.decode('latin-1').splitlines()
    parts = lines[0].split() if lines else []
    if len(parts) < 2 or parts[0] not in ('HTTP/1.0', 'HTTP/1.1') or parts[1] != '200':
        return None
    kv = {}
    for line in body.decode('latin-1').splitlines():
        k, eq, v = line.strip().partition('=')
        if eq:
            kv.setdefault(k, v)
    if not kv.get('colo'):
        return None
    return {'ip': kv.get('ip', ''), 'colo': kv['colo']}


def data_probe(iface: str, target: str) -> bool:
    """True only if a genuine /cdn-cgi/trace answer comes back through iface over verified TLS. A bare TCP
    connect proves nothing (a SIM without allowance still completes handshakes), and plain HTTP could be
    answered by the carrier's own portal; the certificate check (IP SAN of the target) rules that out."""
    try:
        ctx = ssl.create_default_context()
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, iface.encode())
            s.settimeout(4)
            s.connect((target, 443))
            with ctx.wrap_socket(s, server_hostname=target) as tls:
                tls.sendall(f'GET /cdn-cgi/trace HTTP/1.0\r\nHost: {target}\r\n\r\n'.encode())
                raw = b''
                while len(raw) < 4096:
                    chunk = tls.recv(4096 - len(raw))
                    if not chunk:
                        break
                    raw += chunk
            return parse_trace(raw) is not None
    except Exception:  # noqa: BLE001 - any failure means no data
        return False


def data_round(iface: str) -> bool:
    return any(data_probe(iface, t) for t in PROBE_TARGETS)


def data_ok(iface: str) -> tuple[bool, str]:
    for i in range(VERIFY_FIRST_TRIES):
        if i:
            pause(VERIFY_GAP_S)
        if data_round(iface):
            break
    else:
        return False, 'no data'
    for k in range(1, VERIFY_N):
        pause(VERIFY_GAP_S)
        if not data_round(iface):
            return False, f'data stopped after {k * VERIFY_GAP_S} s'
    return True, 'ok'


def datacheck_skipped() -> bool:
    return bool(os.environ.get('S22_SHARE_NO_DATACHECK'))


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
    AUDIT.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(AUDIT, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, 'a') as f:
        f.write(json.dumps({'ts': round(now()), 'event': event, **kw}) + '\n')


def rig() -> str:
    ip = str(read_json(CONF).get('rig') or '')
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        raise RuntimeError(f'no valid rig address in {CONF}') from None
    if a.version != 4 or not a.is_private:
        raise RuntimeError('the rig address must be a private IPv4 LAN address')
    return ip


def battery() -> int:
    try:
        return int(BATTERY.read_text().strip())
    except (OSError, ValueError):
        return -1


def counters() -> int:
    total = 0
    for k in ('rx_bytes', 'tx_bytes'):
        try:
            total += int((NET / DEV / 'statistics' / k).read_text().strip())
        except (OSError, ValueError):
            pass
    return total


def today() -> str:
    return time.strftime('%Y-%m-%d', time.localtime(now()))


def used_today(state: dict | None = None) -> int:
    u = read_json(USAGE)
    base = int(u.get('bytes', 0)) if u.get('date') == today() else 0
    if state and state.get('on'):
        base += max(0, counters() - int(state.get('counter0', 0)))
    return base


def account(state: dict) -> None:
    """Add this session's rmnet bytes to today's ledger (called once, when sharing stops)."""
    if not state.get('on'):
        return
    write_json(USAGE, {'date': today(), 'bytes': used_today(state)})


def forwarding_rules(r: str) -> list[list[str]]:
    return [
        ['-t', 'filter', '-A', 'S22SHARE', '-s', r, '-o', 'rmnet+', '-j', 'ACCEPT'],
        ['-t', 'filter', '-A', 'S22SHARE', '-d', r, '-i', 'rmnet+', '-m', 'state', '--state', 'ESTABLISHED,RELATED', '-j', 'ACCEPT'],
        ['-t', 'filter', '-A', 'S22SHARE', '-j', 'DROP'],      # nothing else is forwarded, whatever the interface
        ['-t', 'nat', '-A', 'S22SHARE_NAT', '-s', r, '-o', 'rmnet+', '-j', 'MASQUERADE'],
    ]


def teardown() -> list[str]:
    """Undo everything, ignoring what is not there. Returns what could not be undone."""
    left = []
    FORWARD.write_text('0\n') if FORWARD.exists() else None
    for argv in (['-t', 'filter', '-D', 'FORWARD', '-j', 'S22SHARE'], ['-t', 'filter', '-F', 'S22SHARE'],
                 ['-t', 'filter', '-X', 'S22SHARE'], ['-t', 'nat', '-D', 'POSTROUTING', '-j', 'S22SHARE_NAT'],
                 ['-t', 'nat', '-F', 'S22SHARE_NAT'], ['-t', 'nat', '-X', 'S22SHARE_NAT']):
        run(IPT + argv, check=False)
    run(['ip', 'rule', 'del', 'priority', PRIO], check=False)
    run(['ip', 'route', 'flush', 'table', TABLE], check=False)
    if FORWARD.exists() and FORWARD.read_text().strip() != '0':
        left.append('ip_forward')
    if run(IPT + ['-t', 'filter', '-S', 'S22SHARE'], check=False).returncode == 0:
        left.append('S22SHARE chain')
    return left


def restore_mode(state: dict) -> None:
    if 'prev_mode' not in state:
        return
    conf = read_json(NETPOLICY)
    if state['prev_mode'] is None:
        conf.pop('mobile_mode', None)
    else:
        conf['mobile_mode'] = state['prev_mode']
    write_json(NETPOLICY, conf)


def off(reason: str = 'request') -> dict:
    st = read_json(STATE)
    account(st)
    left = teardown()
    restore_mode(st)
    was_on = bool(st.get('on'))
    try:
        STATE.unlink()
    except FileNotFoundError:
        pass
    if was_on or reason != 'request':
        audit('disable', reason=reason, bytes_today=used_today(), left=left,
              seconds=round(now() - float(st.get('since', now()))) if was_on else 0)
    return {'ok': not left, 'on': False, 'reason': reason, 'left': left, 'bytes_today': used_today()}


def mobile_up() -> bool:
    links = read_json(NP_STATUS).get('links') or {}
    m = links.get('mobile') or {}
    return m.get('state') == 'up' and bool(m.get('ipv4'))


def on() -> dict:
    st = read_json(STATE)
    if st.get('on'):
        return status()
    r = rig()
    b = battery()
    if 0 <= b <= MIN_BATTERY:
        raise RuntimeError(f'battery {b} % (sharing needs more than {MIN_BATTERY} %)')
    if used_today() >= CAP_BYTES:
        raise RuntimeError(f'the {CAP_BYTES // 1000 ** 3} GB daily cap is used up')
    conf = read_json(NETPOLICY)
    st = {'on': False, 'prev_mode': conf.get('mobile_mode'), 'since': now(), 'rig_set': True}
    write_json(STATE, st)                     # from here on, `off`/`sweep` know what to restore
    skipped = datacheck_skipped()
    try:
        conf['mobile_mode'] = 'always'
        write_json(NETPOLICY, conf)
        deadline = now() + UP_WAIT_S
        while not mobile_up():
            if now() > deadline:
                raise RuntimeError('mobile data did not come up within 60 s')
            time.sleep(2)
        if not skipped:
            good, why = data_ok(DEV)
            if not good:
                raise RuntimeError(f'mobile data carries no traffic ({why}); check the SIM data allowance')
        teardown()                            # a clean slate, also after a crash
        run(['ip', 'route', 'replace', 'default', 'dev', DEV, 'table', TABLE])
        run(['ip', 'rule', 'add', 'from', r, 'lookup', TABLE, 'priority', PRIO])
        run(IPT + ['-t', 'filter', '-N', 'S22SHARE'])
        run(IPT + ['-t', 'nat', '-N', 'S22SHARE_NAT'])
        for argv in forwarding_rules(r):
            run(IPT + argv)
        run(IPT + ['-t', 'filter', '-I', 'FORWARD', '1', '-j', 'S22SHARE'])
        run(IPT + ['-t', 'nat', '-I', 'POSTROUTING', '1', '-j', 'S22SHARE_NAT'])
        FORWARD.write_text('1\n')
        st.update(on=True, counter0=counters())
        write_json(STATE, st)
    except Exception as e:
        off(reason=f'enable failed: {e}'[:200])
        raise
    audit('enable', battery=b, bytes_today=used_today(st), **({'datacheck': 'skipped'} if skipped else {}))
    spawn_watchdog()
    return status()


def spawn_watchdog() -> None:
    if os.environ.get('S22_SHARE_NO_WATCHDOG'):
        return
    subprocess.Popen([sys.executable, os.path.abspath(__file__), 'watch'], stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True, cwd='/')


def check_guards(st: dict) -> str | None:
    """Why sharing must stop now, or None."""
    if not st.get('on'):
        return None
    if now() - float(st.get('since', now())) >= MAX_S:
        return 'six hours'
    b = battery()
    if 0 <= b <= MIN_BATTERY:
        return f'battery {b} %'
    if used_today(st) >= CAP_BYTES:
        return 'daily cap'
    return None


def watch(interval: float = 30.0, once: bool = False) -> dict:
    while True:
        st = read_json(STATE)
        if not st.get('on'):
            return {'ok': True, 'on': False}
        why = check_guards(st)
        if why:
            return off(reason=why)
        if not datacheck_skipped():
            fails = 0 if data_round(DEV) else int(st.get('data_fails', 0)) + 1
            if fails >= WATCH_FAILS:
                return off(reason='no data')
            st.update(data_fails=fails, data_checked=round(now()))
            write_json(STATE, st)
        if once:
            return status()
        time.sleep(interval)


def sweep() -> dict:
    """At s22d start: anything a reboot or a dead watchdog left behind goes (kernel state is gone after a
    reboot, but netpolicy's mobile_mode 'always' would persist)."""
    st = read_json(STATE)
    if st.get('on') and FORWARD.exists() and FORWARD.read_text().strip() == '1':
        spawn_watchdog()                      # still sharing (s22d restarted): keep the guards running
        return {'ok': True, 'swept': False, 'on': True}
    if st:
        return dict(off(reason='sweep'), swept=True)
    return {'ok': True, 'swept': False, 'on': False}


def sms_state() -> dict:
    """phoned's owner switch: outbound SMS refused while the file exists; its text says why."""
    try:
        why = SMS_PAUSED.read_text().strip()[:120]
    except OSError:
        return {'paused': False}
    return {'paused': True, 'reason': why or 'paused'}


def rig_switched_off() -> list[str]:
    """Device-API capabilities the owner has switched off for the rig (Settings -> Rig access)."""
    return sorted(k for k, v in read_json(SWITCHES).items() if v is False)


def status() -> dict:
    st = read_json(STATE)
    try:
        rig_ok = bool(rig())
    except RuntimeError:
        rig_ok = False
    fwd = FORWARD.read_text().strip() == '1' if FORWARD.exists() else None
    return {'ok': True, 'on': bool(st.get('on')), 'since': st.get('since'), 'forwarding': fwd,
            'rig_configured': rig_ok, 'battery': battery(), 'bytes_today': used_today(st),
            'data_fails': int(st.get('data_fails', 0)), 'data_checked': st.get('data_checked'), 'sms': sms_state(), 'rig_switched_off': rig_switched_off(),
            'cap_bytes': CAP_BYTES, 'max_s': MAX_S, 'min_battery': MIN_BATTERY}


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else 'status'
    try:
        out = {'on': on, 'off': off, 'status': status, 'watch': watch, 'sweep': sweep}[cmd]()
    except KeyError:
        out = {'ok': False, 'error': 'usage: on|off|status|watch|sweep'}
    except Exception as e:  # noqa: BLE001 - one JSON answer for s22d
        out = {'ok': False, 'error': str(e)[:300]}
    print(json.dumps(out))
    return 0 if out.get('ok') else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
