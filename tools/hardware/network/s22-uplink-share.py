#!/usr/bin/env python3
"""s22-uplink-share: lend the phone's LTE to the rig while the home internet is down (stdlib only).

Hub U-fe741e10, rules from corpunum-82 (2026-11-12, docs/P4_UPLINK_FAILOVER_PLAN.md):
* only the rig: forwarding and NAT for one source address (the rig's LAN IP, from the private
  /srv/s22/state/uplink-share.json {"rig": "<ip>"}), everything else forwarded from wlan0 is dropped;
* guards: battery above 40 %, at most 2 GB a day (rmnet0 bytes while sharing), off after 6 h;
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
import subprocess
import sys
import time
from pathlib import Path

CONF = Path(os.environ.get('S22_SHARE_CONF', '/srv/s22/state/uplink-share.json'))
STATE = Path(os.environ.get('S22_SHARE_STATE', '/srv/s22/state/uplink-share/state.json'))
USAGE = Path(os.environ.get('S22_SHARE_USAGE', '/srv/s22/state/uplink-share/usage.json'))
AUDIT = Path(os.environ.get('S22_SHARE_AUDIT', '/srv/s22/state/audit/uplink-share.jsonl'))
NETPOLICY = Path(os.environ.get('S22_NETPOLICY_CONF', '/srv/s22/state/netpolicy/config.json'))
NP_STATUS = Path(os.environ.get('S22_NETPOLICY_STATUS', '/run/s22-netpolicy/status.json'))
BATTERY = Path(os.environ.get('S22_BATTERY', '/sys/class/power_supply/battery/capacity'))
NET = Path(os.environ.get('S22_SYS_NET', '/sys/class/net'))
FORWARD = Path(os.environ.get('S22_IP_FORWARD', '/proc/sys/net/ipv4/ip_forward'))
IPT = ['chroot', '/mnt/omarchy-trial', 'iptables-legacy']
DEV, TABLE, PRIO = 'rmnet0', '177', '1177'
MIN_BATTERY = 40                  # stop at <= 40 %
CAP_BYTES = 2 * 1000 ** 3         # 2 GB a day
MAX_S = 6 * 3600                  # off after 6 h
UP_WAIT_S = 60


def now() -> float:
    return time.time()


def run(argv: list[str], check: bool = True) -> subprocess.CompletedProcess:
    r = subprocess.run(argv, capture_output=True, text=True, timeout=30)
    if check and r.returncode != 0:
        raise RuntimeError(f'{" ".join(argv[-6:])}: rc={r.returncode} {r.stderr.strip()[:200]}')
    return r


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
        ['-t', 'filter', '-A', 'S22SHARE', '-i', 'wlan0', '-j', 'DROP'],
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
        raise RuntimeError('the 2 GB daily cap is used up')
    conf = read_json(NETPOLICY)
    st = {'on': False, 'prev_mode': conf.get('mobile_mode'), 'since': now(), 'rig_set': True}
    write_json(STATE, st)                     # from here on, `off`/`sweep` know what to restore
    try:
        conf['mobile_mode'] = 'always'
        write_json(NETPOLICY, conf)
        deadline = now() + UP_WAIT_S
        while not mobile_up():
            if now() > deadline:
                raise RuntimeError('mobile data did not come up within 60 s')
            time.sleep(2)
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
    audit('enable', battery=b, bytes_today=used_today(st))
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


def status() -> dict:
    st = read_json(STATE)
    try:
        rig_ok = bool(rig())
    except RuntimeError:
        rig_ok = False
    fwd = FORWARD.read_text().strip() == '1' if FORWARD.exists() else None
    return {'ok': True, 'on': bool(st.get('on')), 'since': st.get('since'), 'forwarding': fwd,
            'rig_configured': rig_ok, 'battery': battery(), 'bytes_today': used_today(st),
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
