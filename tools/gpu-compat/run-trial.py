#!/usr/bin/env python3
"""Supervise exactly one isolated trial; retain health and kernel receipts."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import time

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT/'rootfs/gpu-compat-20260921'

def remote(cmd, timeout=25):
    return subprocess.run([str(ROOT/'tools/s22-ssh'), cmd], capture_output=True,
                          text=True, timeout=timeout)

def phone_health():
    # This compute-only test does not use the rig LLM or the Pi review loop.
    script = """import json,pathlib,urllib.request
p=pathlib.Path
r=json.loads(p('/run/s22-persistent-ready.json').read_text())
op=urllib.request.build_opener(urllib.request.ProxyHandler({}))
with op.open('http://127.0.0.1:8089/health',timeout=4) as f: h=json.load(f)
print(json.dumps(dict(boot_id=p('/proc/sys/kernel/random/boot_id').read_text().strip(),
pid1=p('/proc/1/comm').read_text().strip(),temperature=int(p('/sys/class/power_supply/battery/temp').read_text())/10,
profile=r['model_profile'],model=h.get('status'))))
"""
    result=remote('python3 -c '+shlex.quote(script))
    if result.returncode:
        raise RuntimeError('Phone status unavailable: '+result.stderr)
    state=json.loads(result.stdout)
    if state['pid1'] != 'native-guardian' or state['profile'] != 'qwen4b' or state['model'] != 'ok':
        raise RuntimeError('Phone baseline changed')
    if state['temperature'] >= 42:
        raise RuntimeError('Battery temperature >=42C; do not start another trial')
    return state

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('variant', choices=['base','opencl-real','opencl-headless','vulkan-headless','llama-vulkan'])
    p.add_argument('name')
    p.add_argument('--trace', action='store_true')
    p.add_argument('--proc', action='store_true')
    p.add_argument('--sys', action='store_true')
    bridge=p.add_mutually_exclusive_group()
    bridge.add_argument('--bridge-v2', action='store_true')
    bridge.add_argument('--bridge-v3', action='store_true')
    bridge.add_argument('--bridge-v4', action='store_true')
    p.add_argument('--deadline', type=int, default=35, choices=range(1,181), metavar='1..180')
    p.add_argument('command', nargs=argparse.REMAINDER)
    args=p.parse_args()
    if not re.fullmatch(r'[a-z0-9-]+', args.name) or not args.command:
        p.error('Safe unique trial name and /system/bin command required')
    os.umask(0o077)
    raw=BASE/'trials'/args.name
    raw.mkdir(parents=True, exist_ok=False)
    before=phone_health()
    kernel=remote('dmesg')
    if kernel.returncode:
        raise RuntimeError('No kernel capture, do not run trial')
    (raw/'before-kernel.txt').write_text(kernel.stdout)
    boundary=max(map(float,re.findall(r'^\[\s*([0-9.]+)\]',kernel.stdout,re.M)),default=0)
    expected=json.loads((BASE/('manifest-'+args.variant+'.json')).read_text())
    phone='/srv/s22/gpu-compat-20260921/'+args.variant
    verify='''import hashlib,json,pathlib
root=pathlib.Path(DEST)
expected=EXPECTED
for name,wanted in expected.items():
 assert hashlib.sha256((root/name).read_bytes()).hexdigest()==wanted,name
print('HASHES_OK',len(expected))
'''.replace('DEST',repr(phone)).replace('EXPECTED',repr(expected))
    verified=remote('python3 -c '+shlex.quote(verify))
    if verified.returncode:
        raise RuntimeError('Staged bytes changed: '+verified.stderr)
    helper=(ROOT/'tools/gpu-compat/exec-isolated.py').read_text()
    (raw/'exec-isolated-tested.py').write_text(helper)
    views=(['--proc'] if args.proc else [])+(['--sys'] if args.sys else [])
    bridge_version='v4' if args.bridge_v4 else 'v3' if args.bridge_v3 else 'v2' if args.bridge_v2 else None
    options=views+(['--bridge-'+bridge_version] if bridge_version else [])
    execution=['python3','-c',helper,phone]+options+args.command
    trace='/srv/s22/gpu-compat-20260921/'+args.name+'.strace'
    if args.trace:
        execution=['strace','-f','-qq','-tt','-T','-s','160','-o',trace,
                   '-e','trace=%file,ioctl,connect,clone,clone3']+execution
    command=shlex.join(['timeout','-k','3',str(args.deadline)]+execution)
    started=datetime.now(timezone.utc).isoformat()
    start_clock=time.monotonic()
    try:
        result=remote(command,timeout=args.deadline+10)
    except subprocess.TimeoutExpired:
        (raw/'host-timeout.txt').write_text('Remote outcome unknown. No automatic retry/reset.\n')
        raise
    elapsed=time.monotonic()-start_clock
    (raw/'stdout.txt').write_text(result.stdout)
    (raw/'stderr.txt').write_text(result.stderr)
    if args.trace:
        traced=remote('test -f '+shlex.quote(trace)+' && head -c 4194304 '+shlex.quote(trace))
        (raw/'strace.txt').write_text(traced.stdout)
    kernel=remote('dmesg')
    (raw/'after-kernel.txt').write_text(kernel.stdout)
    delta=[]
    for line in kernel.stdout.splitlines():
        match=re.match(r'^\[\s*([0-9.]+)\]',line)
        if match and float(match[1])>boundary:
            delta.append(line)
    (raw/'kernel-delta.txt').write_text('\n'.join(delta)+'\n')
    after=phone_health()
    receipt=dict(started_at=started,variant=args.variant,name=args.name,command=args.command,read_only_views=views,
                 returncode=result.returncode,before=before,after=after,
                 elapsed_seconds=round(elapsed,3),
                 deadline_seconds=args.deadline,
                 bridge=bridge_version or 'original',
                 same_boot=before['boot_id']==after['boot_id'],kernel_capture_exit=kernel.returncode,
                 gpu_messages=[x for x in delta if re.search(r'GPU|gpu|TCP|CPF|SQC|fault|timeout',x)],
                 helper_sha256=hashlib.sha256(helper.encode()).hexdigest(),
                 note='Isolated diagnostic. Numerical compute and no faults required for GPU acceptance.')
    (raw/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
    print(json.dumps(receipt,indent=2))
    print(result.stdout)
    # Preserve the full raw stream above; suppress repetitive dispatch lookups
    # only in the interactive report.
    print('\n'.join(line for line in result.stderr.splitlines()
                    if not line.startswith('VULKAN_BRIDGE_TRACE GIPA:')))
    if not receipt['same_boot'] or kernel.returncode:
        raise RuntimeError('Boot or capture changed. Stop trials.')
    return result.returncode

if __name__=='__main__':
    raise SystemExit(main())
