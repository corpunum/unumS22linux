#!/usr/bin/env python3
"""Supervise one explicit BT UART version trial with independent USB SSH.

No firmware download, HCI attach, pairing, Wi-Fi restart or retry. Native
Alpine runs the helper; Arch Python subprocess is deliberately not involved.
Raw kernel/UART receipts stay in ignored rootfs, never public by default.
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
import time

ROOT = Path(__file__).resolve().parents[2]
BINARY = ROOT / 'builds/bt-live-20260921/bt-version-probe'
DEST = '/srv/s22/bt-live-20260921/bt-version-probe'

METADATA = r'''
import json, os, pathlib, stat, struct
p=pathlib.Path
assert p('/proc/1/comm').read_text().strip() == 'native-guardian'
assert p('/proc/sys/kernel/osrelease').read_text().strip() == '5.10.260-g4e5c5ad7d950'
assert os.environ['SSH_CONNECTION'].split()[2] == '10.55.0.2', 'USB SSH required'
assert p('/sys/class/net/ecm0/carrier').read_text().strip() == '1'
devices={'/dev/btpower':(503,0),'/dev/ttySAC1':(204,65)}
for name, ids in devices.items():
 st=p(name).lstat()
 assert stat.S_ISCHR(st.st_mode) and (os.major(st.st_rdev),os.minor(st.st_rdev))==ids, name
fds=[]
for proc in p('/proc').iterdir():
 if not proc.name.isdigit(): continue
 try: entries=list((proc/'fd').iterdir())
 except FileNotFoundError: continue
 for fd in entries:
  try:
   st=fd.stat()
   if stat.S_ISCHR(st.st_mode) and (os.major(st.st_rdev),os.minor(st.st_rdev)) in devices.values():
    fds.append(str(fd))
  except FileNotFoundError: pass
assert not fds, fds
assert not list(p('/sys/class/bluetooth').glob('hci*'))
assert p('/sys/class/rfkill/rfkill0/name').read_text().strip()=='bt_power'
assert p('/sys/class/rfkill/rfkill0/soft').read_text().strip()=='1'
assert p('/sys/class/rfkill/rfkill0/hard').read_text().strip()=='0'
dt=p('/sys/firmware/devicetree/base/bt_qca6490')
assert (dt/'compatible').read_bytes()==b'qcom,qca6490\0'
print(json.dumps(dict(boot_id=p('/proc/sys/kernel/random/boot_id').read_text().strip(),
 device_fds=fds, independent_usb=True, cnss=p('/sys/kernel/debug/cnss/stats').read_text().strip(),
 regulator_summary=p('/sys/kernel/debug/regulator/regulator_summary').read_text(),
 gpio=p('/sys/kernel/debug/gpio').read_text(),
 bt_dt_gpios=list(struct.unpack('>9I',(dt/'gpios').read_bytes())))))
'''


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('name')
    ap.add_argument('--execute', action='store_true')
    args = ap.parse_args()
    if not re.fullmatch('[a-z0-9-]+', args.name):
        ap.error('use unique lowercase trial name')
    spec = importlib.util.spec_from_file_location('trial', ROOT/'tools/gpu-compat/run-trial.py')
    trial = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(trial)
    before = trial.phone_health()
    result = trial.remote('python3 -c ' + shlex.quote(METADATA))
    if result.returncode:
        raise RuntimeError(result.stderr)
    metadata = json.loads(result.stdout)
    if not args.execute:
        print(json.dumps({'metadata_only': True, 'device_fds': metadata['device_fds'],
                          'usb': metadata['independent_usb'], 'health': before}, indent=2))
        return
    os.umask(0o077)
    raw = ROOT/'rootfs/hardware-reuse-20260921/bt-trials'/args.name
    raw.mkdir(parents=True, exist_ok=False)
    (raw/'before.json').write_text(json.dumps({'health':before,'metadata':metadata},indent=2)+'\n')
    kernel = trial.remote('dmesg')
    if kernel.returncode:
        raise RuntimeError('Cannot capture kernel; refusing trial')
    (raw/'before-kernel.txt').write_text(kernel.stdout)
    boundary = max(map(float, re.findall(r'^\[\s*([0-9.]+)\]', kernel.stdout,re.M)),default=0)
    data = BINARY.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    stage = '''import hashlib,os,pathlib,sys
p=pathlib.Path(DEST)
data=sys.stdin.buffer.read()
assert hashlib.sha256(data).hexdigest()==DIGEST
p.parent.mkdir(exist_ok=True)
if p.exists() or p.is_symlink():
 assert not p.is_symlink() and hashlib.sha256(p.read_bytes()).hexdigest()==DIGEST
else:
 with p.open('xb') as f: f.write(data); f.flush(); os.fsync(f.fileno())
 os.chmod(p,0o700)
assert p.stat().st_uid==0 and (p.stat().st_mode & 0o777)==0o700
print('binary_hash_verified')
'''.replace('DEST',repr(DEST)).replace('DIGEST',repr(digest))
    staged = subprocess.run([str(ROOT/'tools/s22-ssh'),'python3 -c '+shlex.quote(stage)],
                            input=data,capture_output=True,timeout=30)
    if staged.returncode:
        raise RuntimeError(staged.stderr.decode())
    check = trial.remote(DEST+' --check-live-wlan')
    (raw/'metadata-check.txt').write_text(check.stdout+check.stderr)
    if check.returncode:
        raise RuntimeError('Live C preflight refused without opening devices')
    trace = '/srv/s22/bt-live-20260921/'+args.name+'.strace'
    command = shlex.join(['timeout','-s','TERM','-k','5','12','strace','-f','-qq','-tt','-T',
                         '-s','256','-o',trace,'-e','trace=openat,close,ioctl,read,write',
                         DEST,'--execute','--allow-shared-wlan-rail','--live-wlan-vote',
                         '--uart','/dev/ttySAC1','--btpower','/dev/btpower',
                         '--btpower-rdev','503:0','--timeout-ms','2000'])
    start = datetime.now(timezone.utc).isoformat()
    now = time.monotonic()
    try:
        result = trial.remote(command, timeout=25)
    except subprocess.TimeoutExpired:
        (raw/'unknown.txt').write_text('Host timeout. No automatic retry/reset.\n')
        raise
    elapsed = time.monotonic()-now
    (raw/'stdout.txt').write_text(result.stdout)
    (raw/'stderr.txt').write_text(result.stderr)
    captured = trial.remote('head -c 8388608 '+shlex.quote(trace))
    (raw/'strace.txt').write_text(captured.stdout)
    kernel = trial.remote('dmesg')
    (raw/'after-kernel.txt').write_text(kernel.stdout)
    delta = [line for line in kernel.stdout.splitlines()
             if (m:=re.match(r'^\[\s*([0-9.]+)\]',line)) and float(m[1])>boundary]
    (raw/'kernel-delta.txt').write_text('\n'.join(delta)+'\n')
    after = trial.phone_health()
    state = trial.remote('python3 -c '+shlex.quote(METADATA))
    (raw/'after-metadata.txt').write_text(state.stdout+state.stderr)
    check_after = trial.remote(DEST+' --check-live-wlan')
    (raw/'after-check.txt').write_text(check_after.stdout+check_after.stderr)
    receipt = dict(started_at=start, returncode=result.returncode, elapsed=round(elapsed,3),
                   source_sha256=hashlib.sha256((ROOT/'tools/hardware/bt-version-transport-probe.c').read_bytes()).hexdigest(),
                   binary_sha256=digest, before=before, after=after,
                   same_boot=before['boot_id']==after['boot_id'], kernel_capture_exit=kernel.returncode,
                   after_metadata_exit=state.returncode, after_vote_check=check_after.returncode,
                   strace_capture_exit=captured.returncode, uart_output=result.stdout,
                   note='Raw BT transport only; not HCI, firmware, pairing or data acceptance.')
    (raw/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
    print(json.dumps({k:v for k,v in receipt.items() if k not in ('before','after','uart_output')},indent=2))
    print(result.stdout)
    print(result.stderr)
    if not receipt['same_boot'] or state.returncode or check_after.returncode or kernel.returncode:
        raise RuntimeError('Post-trial state changed; no retry')
    if result.returncode:
        raise SystemExit(result.returncode)


if __name__ == '__main__':
    main()
