#!/usr/bin/env python3
"""Acknowledge one absent optional QDSS file; no global firmware changes.

Default is inspection only. --acknowledge sends the normal firmware fallback
loading=-1 response for this exact pending file, after verifying its identity
and absence. Never handles arbitrary names, sends bytes, or touches ABOX.
"""
import argparse
import fcntl
import json
import os
from pathlib import Path
import signal
import time

NAME = 'qca6490/qdss_trace_config_v2.cfg'
REQUEST = Path('/sys/class/firmware/qca6490!qdss_trace_config_v2.cfg')
PID1_ROOT = Path('/proc/1/root')
FIRMWARE_SEARCH_PATH = Path('/sys/module/firmware_class/parameters/path')
LOCK = Path('/run/wifi-optional-firmware.lock')
META = Path('/run/wifi-optional-firmware.json')


def handle(acknowledge=False):
    if os.uname().release != '5.10.260-g4e5c5ad7d950':
        raise RuntimeError('not the reviewed target kernel')
    if not REQUEST.exists():
        return {'request': NAME, 'pending': False, 'acknowledged_missing': False}
    resolved = REQUEST.resolve(strict=True)
    if not str(resolved).startswith('/sys/devices/platform/qcom,cnss-qca6490/'):
        raise RuntimeError('unexpected firmware request owner')
    values = dict(line.split('=', 1) for line in (REQUEST / 'uevent').read_text().splitlines() if '=' in line)
    if values.get('FIRMWARE') != NAME or values.get('ASYNC') != '0':
        raise RuntimeError('unexpected firmware request identity')
    # request_firmware already tried kernel lookup. Also verify the mounted
    # custom firmware path and standard initial-root locations explicitly.
    if FIRMWARE_SEARCH_PATH.read_text().strip() != '/vendor/firmware':
        raise RuntimeError('firmware search path changed')
    for root in ('vendor/firmware', 'lib/firmware', 'lib/firmware/updates',
                 'lib/firmware/5.10.260-g4e5c5ad7d950',
                 'lib/firmware/updates/5.10.260-g4e5c5ad7d950'):
        if (PID1_ROOT / root / NAME).exists():
            raise RuntimeError('optional firmware actually present; refusing missing response')
    if acknowledge:
        (REQUEST / 'loading').write_text('-1\n')
    return {'request': NAME, 'pending': True, 'acknowledged_missing': acknowledge}


def serve(acknowledge, interval=0.25, lock_path=LOCK, meta_path=META):
    """Hold one owner lock and service this exact request until SIGTERM."""
    if not acknowledge:
        raise ValueError('--serve requires --acknowledge')
    lock_path = Path(lock_path); meta_path = Path(meta_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    owner = f'{os.getpid()}:{time.monotonic_ns()}'
    with lock_path.open('a+') as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError('serve already owned') from exc
        metadata = {'owner': owner, 'pid': os.getpid(), 'request': NAME,
                    'acknowledge': True, 'ready': True}
        temp = meta_path.with_name(f'.{meta_path.name}.{os.getpid()}.tmp')
        temp.write_text(json.dumps(metadata, sort_keys=True) + '\n')
        os.replace(temp, meta_path)
        stopping = False
        def stop(_signum, _frame):
            nonlocal stopping
            stopping = True
        previous = signal.signal(signal.SIGTERM, stop)
        try:
            first = True
            while not stopping:
                try:
                    result = handle(True)
                except FileNotFoundError:
                    result = {'request': NAME, 'pending': False,
                              'acknowledged_missing': False}
                if first or result['pending']:
                    print(json.dumps(result), flush=True)
                first = False
                time.sleep(interval)
        finally:
            signal.signal(signal.SIGTERM, previous)
            try:
                if meta_path.read_text() == json.dumps(metadata, sort_keys=True) + '\n':
                    meta_path.unlink()
            except FileNotFoundError:
                pass


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--acknowledge', action='store_true')
    parser.add_argument('--serve', action='store_true',
                        help='hold a serialized owner and serve until SIGTERM')
    parser.add_argument('--watch-seconds', type=int, default=0)
    args = parser.parse_args()
    if not 0 <= args.watch_seconds <= 60:
        parser.error('watch duration must be 0..60 seconds')
    if args.serve:
        if args.watch_seconds:
            parser.error('--serve cannot be combined with --watch-seconds')
        try:
            serve(args.acknowledge)
        except (RuntimeError, ValueError) as exc:
            parser.error(str(exc))
        raise SystemExit(0)
    end = time.monotonic() + args.watch_seconds
    first = True
    while True:
        try:
            result = handle(args.acknowledge)
        except FileNotFoundError:
            # Request may disappear naturally between presence and uevent.
            result = {'request': NAME, 'pending': False, 'acknowledged_missing': False}
        if first or result['pending']:
            print(json.dumps(result), flush=True)
        first = False
        if time.monotonic() >= end:
            break
        time.sleep(0.25)
