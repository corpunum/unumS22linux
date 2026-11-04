#!/usr/bin/env python3
"""s22-charge-limit: keep the S22's battery below a set level while it lives on USB power (stdlib only).

The phone is plugged into the rig or the Ally almost all the time, so the battery sits at 100 % and ages
fast. Samsung's own "Protect battery" lives in the stock kernel we run: the battery power supply has
`batt_full_capacity` (drivers/battery/common/sb_full_soc.c). Writing N (50..99) makes the driver stop
charging at N % (charging off, the phone keeps running from USB) and charge again at N-2 %; 0 (the boot
default) or 100 is the normal behaviour (is_full_capacity() is 1..99 only). Read on the phone 2026-11-04:
the attribute exists, value 0, battery Full at 100 %. The value is kernel memory only, so it is gone after a
reboot: `run` (a keepalive
service) re-applies the configured limit at boot and whenever it drifts (checked every 5 minutes).

  s22-charge-limit status            JSON: configured limit, kernel value, capacity, charging status
  s22-charge-limit set N             configure N (50..99; 0 or 100 = no limit), apply now, read back
  s22-charge-limit off               configure 0 (no limit, the kernel default) and apply
  s22-charge-limit run [--every S]   service: apply the configured limit when the kernel value differs

Only that one attribute is ever written, with a validated integer, and every write is read back. Without
a configuration file (/srv/s22/state/charge-limit.json) `run` writes nothing.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

PSY = Path(os.environ.get('S22_BATTERY_PSY', '/sys/class/power_supply/battery'))
CONFIG = Path(os.environ.get('S22_CHARGE_LIMIT_CONFIG', '/srv/s22/state/charge-limit.json'))
ATTR = 'batt_full_capacity'
MIN_LIMIT, MAX_LIMIT = 50, 100
NO_LIMIT = 0                      # the kernel's boot default; 100 means the same


def valid(n) -> int:
    n = int(n)
    if n != NO_LIMIT and not MIN_LIMIT <= n <= MAX_LIMIT:
        raise ValueError(f'limit must be {MIN_LIMIT}..{MAX_LIMIT} or {NO_LIMIT}, got {n}')
    return n


def read_attr(name: str, psy: Path = PSY) -> str | None:
    try:
        return (psy / name).read_text().strip()
    except OSError:
        return None


def kernel_limit(psy: Path = PSY) -> int | None:
    v = read_attr(ATTR, psy)
    try:
        return int(v.split()[0]) if v else None
    except ValueError:
        return None


def configured(config: Path = CONFIG) -> int | None:
    try:
        return valid(json.loads(config.read_text())['limit'])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def apply(limit: int, psy: Path = PSY) -> dict:
    """Write the limit if it differs, read it back. -> {'ok', 'changed', 'kernel'}."""
    limit = valid(limit)
    before = kernel_limit(psy)
    if before is None:
        return {'ok': False, 'changed': False, 'kernel': None, 'error': f'{psy / ATTR} not available'}
    if before == limit:
        return {'ok': True, 'changed': False, 'kernel': before}
    with open(psy / ATTR, 'w') as f:
        f.write(f'{limit}\n')
    after = kernel_limit(psy)
    return {'ok': after == limit, 'changed': True, 'kernel': after, 'before': before}


def status(psy: Path = PSY, config: Path = CONFIG) -> dict:
    k = kernel_limit(psy)
    return {'configured': configured(config), 'kernel': k, 'active': k is not None and 0 < k < 100,
            'capacity': read_attr('capacity', psy), 'status': read_attr('status', psy),
            'supported': (psy / ATTR).exists()}


def save(limit: int, config: Path = CONFIG) -> None:
    config.parent.mkdir(parents=True, exist_ok=True)
    tmp = config.with_suffix('.tmp')
    tmp.write_text(json.dumps({'limit': valid(limit), 'set_at': int(time.time())}) + '\n')
    os.replace(tmp, config)


def run(every_s: float = 300.0, psy: Path = PSY, config: Path = CONFIG, once: bool = False, log=print) -> None:
    last = None
    while True:
        want = configured(config)
        if want is not None:
            try:
                r = apply(want, psy)
            except (OSError, ValueError) as e:
                r = {'ok': False, 'error': f'{type(e).__name__}: {e}'}
            if r.get('changed') or (not r.get('ok') and r != last):
                log(json.dumps({'t': int(time.time()), 'limit': want, **r}), flush=True)
            last = r
        if once:
            return
        time.sleep(every_s)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('cmd', choices=['status', 'set', 'off', 'run'])
    ap.add_argument('limit', nargs='?')
    ap.add_argument('--every', type=float, default=300.0)
    a = ap.parse_args(argv)
    if a.cmd == 'status':
        print(json.dumps(status()))
        return 0
    if a.cmd == 'run':
        run(a.every)
        return 0
    try:
        limit = NO_LIMIT if a.cmd == 'off' else valid(a.limit)
    except (TypeError, ValueError) as e:
        ap.error(str(e))
    save(limit)
    r = apply(limit)
    print(json.dumps({'limit': limit, **r}))
    return 0 if r['ok'] else 1


if __name__ == '__main__':
    sys.exit(main())
