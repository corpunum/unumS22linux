#!/usr/bin/env python3
"""One supervised 1-second zero-sample RDMA2 diagnostic, not acoustic proof.

Default captures read-only preflight. --execute creates only the sysfs-matched
native pcmC0D2p node and writes digital zeros via ALSA. No mixer controls,
gain, routing, capture, Arch PCM binding, persistent enablement or retry.
"""
import argparse
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import time

ROOT=Path(__file__).resolve().parents[2]
PREFLIGHT=r'''import json,os,pathlib,stat,subprocess
p=pathlib.Path
assert p('/proc/1/comm').read_text().strip()=='native-guardian'
assert p('/proc/asound/card0/id').read_text().strip()=='RainbowPrince'
node=p('/sys/class/sound/pcmC0D2p').resolve(strict=True)
assert node==p('/sys/devices/platform/sound/sound/card0/pcmC0D2p')
assert (node/'dev').read_text().strip()=='116:3'
assert p('/sys/class/net/ecm0/carrier').read_text().strip()=='1'
amp={}
for name in ('Left AMP Enable Switch','Right AMP Enable Switch'):
 r=subprocess.run(['amixer','-c','0','cget','name='+name],capture_output=True,text=True,timeout=5)
 assert r.returncode==0 and ': values=off\n' in r.stdout
 amp[name]=r.stdout
status=p('/proc/asound/card0/pcm2p/sub0/status').read_text().strip()
assert status=='closed',status
base=p('/sys/bus/platform/devices/18c50000.abox')
service=(base/'service').read_text().strip(); reset=(base/'reset_count').read_text().strip()
assert service=='1' and reset=='0'
print(json.dumps({'amps':amp,'status':status,'service':service,'reset_count':reset,
                  'runtime_status':(base/'power/runtime_status').read_text().strip()}))
'''


def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('name');ap.add_argument('--execute',action='store_true');args=ap.parse_args()
    if not re.fullmatch('[a-z0-9-]+',args.name):ap.error('unique lowercase trial name required')
    spec=importlib.util.spec_from_file_location('hardware_trial',ROOT/'tools/gpu-compat/run-trial.py')
    trial=importlib.util.module_from_spec(spec);spec.loader.exec_module(trial)
    before=trial.phone_health()
    pre=trial.remote('python3 -c '+shlex.quote(PREFLIGHT))
    if pre.returncode:raise RuntimeError(pre.stderr)
    metadata=json.loads(pre.stdout)
    if not args.execute:
        print(json.dumps({'preflight':metadata,'model':before['model'],'execute_required':True},indent=2));return
    os.umask(0o077)
    raw=ROOT/'rootfs/main-driver-loop-20260921'/args.name;raw.mkdir(exist_ok=False)
    (raw/'before.json').write_text(json.dumps({'health':before,'audio':metadata},indent=2)+'\n')
    kernel=trial.remote('dmesg')
    if kernel.returncode:raise RuntimeError('kernel capture failed')
    (raw/'before-kernel.txt').write_text(kernel.stdout)
    boundary=max(map(float,re.findall(r'^\[\s*([0-9.]+)\]',kernel.stdout,re.M)),default=0)
    sync=ROOT/'tools/hardware/audio-sync-sound-nodes.py'
    node=subprocess.run([str(ROOT/'tools/s22-ssh'),'python3 - --apply --only pcmC0D2p'],
                        input=sync.read_bytes(),capture_output=True,timeout=15)
    (raw/'node.json').write_bytes(node.stdout)
    if node.returncode:raise RuntimeError(node.stderr.decode())
    trace='/srv/s22/audio-early-20260922/'+args.name+'.strace'
    command=shlex.join(['timeout','-s','TERM','-k','3','10','strace','-f','-qq','-tt','-T','-s','160',
        '-o',trace,'-e','trace=openat,close,ioctl,read,write,poll,ppoll',
        'aplay','--nonblock','-v','-D','hw:0,2','-t','raw','-f','S16_LE','-r','48000','-c','2',
        '--buffer-size=8192','--period-size=1024','-d','1','/dev/zero'])
    start=datetime.now(timezone.utc).isoformat(); t=time.monotonic()
    try:result=trial.remote(command,timeout=20)
    except subprocess.TimeoutExpired:
        (raw/'unknown.txt').write_text('Host timeout; no retry. Inspect phone and kernel before recovery.\n');raise
    elapsed=time.monotonic()-t
    (raw/'stdout.txt').write_text(result.stdout);(raw/'stderr.txt').write_text(result.stderr)
    traced=trial.remote('head -c 2000000 '+shlex.quote(trace));(raw/'strace.txt').write_text(traced.stdout)
    kernel=trial.remote('dmesg');(raw/'after-kernel.txt').write_text(kernel.stdout)
    delta=[s for s in kernel.stdout.splitlines() if (m:=re.match(r'^\[\s*([0-9.]+)\]',s)) and float(m[1])>boundary]
    (raw/'kernel-delta.txt').write_text('\n'.join(delta)+'\n')
    post=trial.remote('python3 -c '+shlex.quote(PREFLIGHT));(raw/'after-audio.txt').write_text(post.stdout+post.stderr)
    after=trial.phone_health()
    r={'started_at':start,'elapsed_seconds':round(elapsed,3),'returncode':result.returncode,
       'before':before,'after':after,'same_boot':before['boot_id']==after['boot_id'],
       'post_audio_preflight_exit':post.returncode,'trace_capture_exit':traced.returncode,
       'kernel_capture_exit':kernel.returncode,'samples':'digital zero only','duration_seconds':1,
       'mixer_writes':[],'microphone_capture':False,'acoustic_acceptance':False,'automatic_retry':False}
    (raw/'receipt.json').write_text(json.dumps(r,indent=2)+'\n')
    print(json.dumps({k:v for k,v in r.items() if k not in ('before','after')},indent=2))
    if result.returncode or post.returncode or not r['same_boot']:raise SystemExit(1)


if __name__=='__main__':main()
