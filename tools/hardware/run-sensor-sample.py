#!/usr/bin/env python3
"""Host supervisor for a single source-matched sensor sample trial."""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shlex

ROOT = Path(__file__).resolve().parents[2]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('sensor', choices=['accel', 'gyro', 'mag', 'light'])
    ap.add_argument('name', help='unique lower-case trial name')
    args = ap.parse_args()
    if not re.fullmatch(r'[a-z0-9-]+', args.name):
        ap.error('Unsafe trial name')
    spec = importlib.util.spec_from_file_location('gpu_trial', ROOT/'tools/gpu-compat/run-trial.py')
    trial = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(trial)
    source = (ROOT/'tools/hardware/sensorhub-sample-once.py').read_text()
    raw = ROOT/'rootfs/hardware-reuse-20260921/sensor-samples'/args.name
    raw.mkdir(parents=True, mode=0o700, exist_ok=False)
    before = trial.phone_health()
    kernel = trial.remote('dmesg')
    if kernel.returncode:
        raise RuntimeError('Cannot capture kernel; do not sample')
    (raw/'before-kernel.txt').write_text(kernel.stdout)
    (raw/'sample-tested.py').write_text(source)
    boundary = max(map(float,re.findall(r'^\[\s*([0-9.]+)\]',kernel.stdout,re.M)),default=0)
    command = shlex.join(['timeout','-k','3','15','python3','-c',source,args.sensor])
    began = datetime.now(timezone.utc).isoformat()
    result = trial.remote(command, timeout=22)
    (raw/'stdout.txt').write_text(result.stdout)
    (raw/'stderr.txt').write_text(result.stderr)
    after = trial.phone_health()
    kernel = trial.remote('dmesg')
    (raw/'after-kernel.txt').write_text(kernel.stdout)
    delta = [line for line in kernel.stdout.splitlines()
             if (m:=re.match(r'^\[\s*([0-9.]+)\]',line)) and float(m[1])>boundary]
    (raw/'kernel-delta.txt').write_text('\n'.join(delta)+'\n')
    try:
        samples=json.loads(result.stdout)
    except json.JSONDecodeError:
        samples=None
    receipt=dict(started_at=began,sensor=args.sensor,returncode=result.returncode,
                 source_sha256=hashlib.sha256(source.encode()).hexdigest(),
                 before=before,after=after,same_boot=before['boot_id']==after['boot_id'],
                 kernel_capture_exit=kernel.returncode,sampling=samples)
    (raw/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
    public={k:v for k,v in receipt.items() if k not in ['before','after','sampling']}
    public['sampling']={k:v for k,v in (samples or {}).items() if k!='sample_frames'}
    public['model_healthy']=after['model']=='ok'
    public['temperature']=after['temperature']
    print(json.dumps(public,indent=2))
    if result.returncode:
        print(result.stderr)
    if result.returncode or kernel.returncode or not receipt['same_boot']:
        raise RuntimeError('Trial failed; inspect receipt, do not automatically retry')


if __name__=='__main__':
    main()
