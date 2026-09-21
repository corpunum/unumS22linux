#!/usr/bin/env python3
"""Supervise one reviewed QCA6490 version->board-ID trial.

This is a separate runner from run-bt-version-once.py.  It reuses that
runner's complete metadata and health contract, but stages the board-query
candidate under a distinct path and records both source hashes.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
BINARY = ROOT / 'builds/bt-board-20260922/bt-qca6490-board-probe'
DEST = '/srv/s22/bt-board-20260922/bt-qca6490-board-probe'
SOURCE = ROOT / 'tools/hardware/bt-qca6490-board-probe.c'
ACCEPTED_SOURCE = ROOT / 'tools/hardware/bt-version-transport-probe.c'
NOTE = 'Raw version+board transport only; no baud, firmware, HCI, pairing or data acceptance.'


def accepted_runner():
    spec = importlib.util.spec_from_file_location('accepted_bt_trial', ROOT / 'tools/hardware/run-bt-version-once.py')
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def host_self_test():
    with tempfile.TemporaryDirectory(prefix='bt-board-selftest-') as directory:
        binary=str(Path(directory)/'probe')
        subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror', '-O2',
                        str(SOURCE), '-o', binary], check=True)
        subprocess.run([binary, '--self-test'], check=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('name')
    ap.add_argument('--execute', action='store_true')
    ap.add_argument('--host-self-test', action='store_true')
    args = ap.parse_args()
    if not re.fullmatch('[a-z0-9-]+', args.name):
        ap.error('use unique lowercase trial name')
    if args.host_self_test:
        host_self_test()
        if not args.execute:
            return
    if not BINARY.is_file():
        raise RuntimeError(f'missing built aarch64 binary: {BINARY}')
    accepted = accepted_runner()
    spec = importlib.util.spec_from_file_location('hardware_trial', ROOT/'tools/gpu-compat/run-trial.py')
    trial = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(trial)
    before = trial.phone_health()
    state = trial.remote('python3 -c ' + shlex.quote(accepted.METADATA))
    if state.returncode:
        raise RuntimeError('Read-only metadata preflight failed: '+state.stderr)
    metadata = json.loads(state.stdout)
    if not args.execute:
        print(json.dumps({'metadata_only': True, 'device_fds': metadata['device_fds'],
                          'usb': metadata['independent_usb'], 'health': before}, indent=2))
        return
    os.umask(0o077)
    raw = ROOT / 'rootfs/hardware-reuse-20260921/bt-trials' / args.name
    raw.mkdir(parents=True, exist_ok=False)
    (raw / 'before.json').write_text(json.dumps({'health': before, 'metadata': metadata}, indent=2) + '\n')
    kernel = trial.remote('dmesg')
    if kernel.returncode:
        raise RuntimeError('Cannot capture kernel; refusing trial')
    (raw / 'before-kernel.txt').write_text(kernel.stdout)
    boundary = max(map(float, re.findall(r'^\[\s*([0-9.]+)\]', kernel.stdout, re.M)), default=0)
    data = BINARY.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    source_hash = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    accepted_hash = hashlib.sha256(ACCEPTED_SOURCE.read_bytes()).hexdigest()
    stage = '''import hashlib,os,pathlib
p=pathlib.Path(DEST); data=__import__('sys').stdin.buffer.read()
assert hashlib.sha256(data).hexdigest()==DIGEST
p.parent.mkdir(exist_ok=True)
if p.exists() or p.is_symlink():
 assert not p.is_symlink() and hashlib.sha256(p.read_bytes()).hexdigest()==DIGEST
else:
 with p.open('xb') as f: f.write(data); f.flush(); os.fsync(f.fileno())
 os.chmod(p,0o700)
assert p.stat().st_uid==0 and (p.stat().st_mode & 0o777)==0o700
print('binary_hash_verified')
'''.replace('DEST', repr(DEST)).replace('DIGEST', repr(digest))
    staged = subprocess.run([str(ROOT / 'tools/s22-ssh'), 'python3 -c ' + shlex.quote(stage)],
                            input=data, capture_output=True, timeout=30)
    if staged.returncode:
        raise RuntimeError(staged.stderr.decode())
    check = trial.remote(DEST + ' --check-live-wlan')
    (raw / 'metadata-check.txt').write_text(check.stdout + check.stderr)
    if check.returncode:
        raise RuntimeError('Live C preflight refused without opening devices')
    trace = '/srv/s22/bt-board-20260922/' + args.name + '.strace'
    command = shlex.join(['timeout', '-s', 'TERM', '-k', '5', '12', 'strace', '-f', '-qq', '-tt', '-T',
                          '-s', '256', '-o', trace, '-e', 'trace=openat,close,ioctl,read,write', DEST,
                          '--execute', '--allow-shared-wlan-rail', '--live-wlan-vote', '--uart', '/dev/ttySAC1',
                          '--btpower', '/dev/btpower', '--btpower-rdev', '503:0'])
    start = datetime.now(timezone.utc).isoformat()
    began = time.monotonic()
    try:
        result = trial.remote(command, timeout=25)
    except subprocess.TimeoutExpired:
        (raw / 'unknown.txt').write_text('Host timeout. No automatic retry/reset.\n')
        raise
    elapsed = time.monotonic() - began
    (raw / 'stdout.txt').write_text(result.stdout)
    (raw / 'stderr.txt').write_text(result.stderr)
    captured = trial.remote('head -c 8388608 ' + shlex.quote(trace))
    (raw / 'strace.txt').write_text(captured.stdout)
    kernel = trial.remote('dmesg')
    (raw / 'after-kernel.txt').write_text(kernel.stdout)
    delta = [line for line in kernel.stdout.splitlines()
             if (m := re.match(r'^\[\s*([0-9.]+)\]', line)) and float(m[1]) > boundary]
    (raw / 'kernel-delta.txt').write_text('\n'.join(delta) + '\n')
    after = trial.phone_health()
    state = trial.remote('python3 -c ' + shlex.quote(accepted.METADATA))
    (raw / 'after-metadata.txt').write_text(state.stdout + state.stderr)
    check_after = trial.remote(DEST + ' --check-live-wlan')
    (raw / 'after-check.txt').write_text(check_after.stdout + check_after.stderr)
    receipt = dict(started_at=start, returncode=result.returncode, elapsed=round(elapsed, 3),
                   source_sha256=source_hash, accepted_source_sha256=accepted_hash,
                   binary_sha256=digest, before=before, after=after,
                   same_boot=before['boot_id'] == after['boot_id'], kernel_capture_exit=kernel.returncode,
                   after_metadata_exit=state.returncode, after_vote_check=check_after.returncode,
                   strace_capture_exit=captured.returncode, uart_output=result.stdout,
                   note=NOTE)
    (raw / 'receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
    print(json.dumps({k: v for k, v in receipt.items() if k not in ('before', 'after', 'uart_output')}, indent=2))
    if not receipt['same_boot'] or state.returncode or check_after.returncode or kernel.returncode:
        raise RuntimeError('Post-trial state changed; no retry')
    if result.returncode:
        raise SystemExit(result.returncode)


if __name__ == '__main__':
    main()
