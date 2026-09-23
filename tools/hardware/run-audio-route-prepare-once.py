#!/usr/bin/env python3
"""Reversible two-control digital-route test; PREPARE-only by default.

--zero-second instead runs one second of digital zeros, never capture.
No gains, amplifier-enable or pin-switch writes.
The two selectors return to their captured zero values after the child exits.
"""
import argparse
from datetime import datetime,timezone
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import shlex
import subprocess
import time

ROOT=Path(__file__).resolve().parents[2]
WRAPPER=r'''import json,pathlib,re,subprocess,sys,time
request=json.loads(sys.stdin.read());helper=request['helper'];samples=[]
observer=None
if request.get('observer'):
 namespace={'__name__':'progress_observer'};exec(request['observer'],namespace)
 observer=namespace['snapshot']
def sample():
 if observer:
  try:samples.append(observer(True))
  except (OSError,ValueError) as error:samples.append({'error':str(error)})
routes=('ABOX SPUS OUT2','ABOX UAIF1 SPK')
def get(name):
 r=subprocess.run(['amixer','-c','0','cget','name='+name],capture_output=True,text=True,timeout=5)
 assert r.returncode==0,r.stderr
 values=re.findall(r'^  : values=(.*)$',r.stdout,re.M); assert len(values)==1
 return values[0],r.stdout
def muted():
 for name in ('Left AMP Enable Switch','Right AMP Enable Switch'):
  assert get(name)[0]=='off'
assert pathlib.Path('/proc/1/comm').read_text().strip()=='native-guardian'
assert pathlib.Path('/proc/asound/card0/pcm2p/sub0/status').read_text().strip()=='closed'
muted()
before={n:get(n) for n in routes}
for value,raw in before.values():
 assert value=='0' and "Item #0 'RESERVED'" in raw and "Item #1 'SIFS0'" in raw
changed=[]; events=[]; child=None; child_dead=True; deadline_exceeded=False; result={}
try:
 for name in routes:
  changed.append(name)
  r=subprocess.run(['amixer','-q','-c','0','cset','name='+name,'1'],capture_output=True,text=True,timeout=5)
  events.append({'control':name,'value':1,'returncode':r.returncode})
  assert r.returncode==0,r.stderr
  assert get(name)[0]=='1'; muted()
 child=subprocess.Popen(['python3','-c',helper],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
 child_dead=False
 end=time.monotonic()+10
 while True:
  try:
   out,err=child.communicate(timeout=max(0.01,min(1 if observer else 10,end-time.monotonic())))
   break
  except subprocess.TimeoutExpired:
   if time.monotonic()<end:
    sample();continue
   deadline_exceeded=True;child.terminate()
   try:out,err=child.communicate(timeout=3)
   except subprocess.TimeoutExpired:
    child.kill();out,err=child.communicate(timeout=3)
   break
 child_dead=True
 result={'child_returncode':child.returncode,'child_stdout':out,'child_stderr':err,
         'child_deadline_exceeded':deadline_exceeded,'progress_samples':samples}
finally:
 result.update({'events':events,'child_exited':child_dead,'before':before,'restored':{}})
 if child_dead:
  for name in reversed(changed):
   r=subprocess.run(['amixer','-q','-c','0','cset','name='+name,before[name][0]],capture_output=True,text=True,timeout=5)
   result['restored'][name]={'returncode':r.returncode,'value':get(name)[0]}
  muted()
 result['after_status']=pathlib.Path('/proc/asound/card0/pcm2p/sub0/status').read_text().strip()
 print(json.dumps(result),flush=True)
 if not child_dead:raise RuntimeError('child not reaped: no route cleanup while kernel state is unknown')
 assert all(v['returncode']==0 and v['value']==before[n][0] for n,v in result['restored'].items())
 assert result['after_status']=='closed'
'''

def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

def assess_dma_progress(samples):
    """Accept only advancing ALSA hardware pointers from timed RUNNING samples.

    Pointer movement proves DMA progress for this bounded stream, not audible
    output. Missing/malformed pointers, time reversal, or pointer regression
    fail closed. Physical playback is never inferred here.
    """
    if not isinstance(samples, list) or len(samples) > 256:
        return {'verified':False,'running_samples':0,'reason':'invalid sample collection'}
    points=[]
    for sample in samples:
        if not isinstance(sample, dict):
            continue
        status=sample.get('alsa_status')
        if not isinstance(status,str) or 'state: RUNNING' not in status:
            continue
        matches=re.findall(r'^hw_ptr\s*:\s*(\d+)$',status,re.M)
        timestamp=sample.get('monotonic')
        if len(matches)!=1 or isinstance(timestamp,bool) or not isinstance(timestamp,(int,float)):
            return {'verified':False,'running_samples':len(points),'reason':'invalid RUNNING sample'}
        try:
            timestamp=float(timestamp)
        except (OverflowError,ValueError):
            return {'verified':False,'running_samples':len(points),'reason':'invalid RUNNING sample'}
        if not math.isfinite(timestamp):
            return {'verified':False,'running_samples':len(points),'reason':'invalid RUNNING sample'}
        point=(timestamp,int(matches[0]))
        if points and (point[0] <= points[-1][0] or point[1] < points[-1][1]):
            return {'verified':False,'running_samples':len(points),'reason':'non-monotonic observation'}
        points.append(point)
    advance=points[-1][1]-points[0][1] if len(points)>=2 else 0
    return {
        'verified':len(points)>=2 and advance>0,
        'running_samples':len(points),
        'first_hw_ptr':points[0][1] if points else None,
        'last_hw_ptr':points[-1][1] if points else None,
        'hw_ptr_advance':advance,
        'reason':'hw_ptr advanced while RUNNING' if advance>0 else 'no observed hw_ptr advance',
    }

def classify(receipt, trace):
    """Separate cleanup, child completion and hardware evidence.

    aplay can return zero after SIGTERM. Older receipts lack the explicit
    deadline flag, so the signal and abort message must also be checked.
    Never rewrite those original receipts to manufacture a timeout flag.
    """
    result=receipt.get('result') or {}
    routes=('ABOX SPUS OUT2','ABOX UAIF1 SPK')
    restored=result.get('restored',{})
    cleanup=(result.get('child_exited') is True and result.get('after_status')=='closed'
             and all(restored.get(n)=={'returncode':0,'value':'0'} for n in routes))
    interrupted=(result.get('child_deadline_exceeded') is True
                 or 'Aborted by signal' in result.get('child_stderr','')
                 or bool(re.search(r'--- SIG(?:TERM|KILL)\b',trace)))
    capture_ok=all(receipt.get(n)==0 for n in
                   ('wrapper_returncode','after_audio_exit','trace_capture_exit','kernel_capture_exit'))
    child_ok=result.get('child_returncode')==0 and not interrupted
    prepared=bool(re.search(r'SNDRV_PCM_IOCTL_PREPARE[^\n]+= 0 ',trace))
    writes=len(re.findall(r'SNDRV_PCM_IOCTL_WRITEI_FRAMES[^\n]+= 0 ',trace))
    prepare_only=receipt.get('diagnostic_kind','prepare-only')=='prepare-only'
    evidence_ok=prepared and (writes==0 if prepare_only else writes>0)
    accepted=bool(cleanup and capture_ok and receipt.get('same_boot') is True and child_ok and evidence_ok)
    dma=assess_dma_progress(result.get('progress_samples',[]))
    return {'diagnostic_completed':accepted,'cleanup_verified':cleanup,
            'child_interrupted':interrupted,'prepare_accepted':prepared,
            'successful_write_ioctls':writes,
            'write_eagain_count':len(re.findall(r'SNDRV_PCM_IOCTL_WRITEI_FRAMES[^\n]+= -1 EAGAIN',trace)),
            'dma_progress_verified':dma['verified'],'dma_progress':dma,
            'physical_playback_verified':False,
            'outcome':'interrupted' if interrupted else ('completed' if accepted else 'failed-or-unproven')}

def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('name');ap.add_argument('--execute',action='store_true')
    ap.add_argument('--zero-second',action='store_true')
    ap.add_argument('--sample-progress',action='store_true');args=ap.parse_args()
    if args.sample_progress and not args.zero_second:ap.error('progress sampling requires --zero-second')
    if not re.fullmatch('[a-z0-9-]+',args.name):ap.error('unique lowercase trial name required')
    trial=load('hardware_trial',ROOT/'tools/gpu-compat/run-trial.py')
    silence=load('silence_probe',ROOT/'tools/hardware/run-audio-silence-once.py')
    before=trial.phone_health(); pre=trial.remote('python3 -c '+shlex.quote(silence.PREFLIGHT))
    if pre.returncode:raise RuntimeError(pre.stderr)
    if not args.execute:
        print(json.dumps({'preflight_passed':True,'controls':['ABOX SPUS OUT2','ABOX UAIF1 SPK'],
                          'new_value':1,'required_previous_value':0,'prepare_only':not args.zero_second}));return
    os.umask(0o077);raw=ROOT/'rootfs/main-driver-loop-20260921'/args.name;raw.mkdir(exist_ok=False)
    (raw/'before.json').write_text(json.dumps({'health':before,'audio':json.loads(pre.stdout)},indent=2)+'\n')
    kernel=trial.remote('dmesg');assert kernel.returncode==0
    (raw/'before-kernel.txt').write_text(kernel.stdout)
    boundary=max(map(float,re.findall(r'^\[\s*([0-9.]+)\]',kernel.stdout,re.M)),default=0)
    trace='/srv/s22/audio-early-20260922/'+args.name+'.strace'
    command=shlex.join(['strace','-f','-qq','-tt','-T','-s','160','-o',trace,
                       '-e','trace=openat,close,ioctl,read,pread64,write','python3','-c',WRAPPER])
    helper_name='audio-zero-one-second.py' if args.zero_second else 'audio-pcm-prepare-only.py'
    helper=(ROOT/'tools/hardware'/helper_name).read_bytes()
    observer=(ROOT/'tools/hardware/audio-progress-snapshot.py').read_bytes() if args.sample_progress else b''
    payload=json.dumps({'helper':helper.decode(),'observer':observer.decode()}).encode()
    start=datetime.now(timezone.utc).isoformat();t=time.monotonic()
    try:r=subprocess.run([str(ROOT/'tools/s22-ssh'),command],input=payload,capture_output=True,timeout=45)
    except subprocess.TimeoutExpired:
        (raw/'unknown.txt').write_text('Host timeout; no automatic retry/reboot. Check child and route cleanup.\n');raise
    elapsed=time.monotonic()-t
    (raw/'stdout.txt').write_bytes(r.stdout);(raw/'stderr.txt').write_bytes(r.stderr)
    captured=trial.remote('head -c 2000000 '+shlex.quote(trace));(raw/'strace.txt').write_text(captured.stdout)
    kernel=trial.remote('dmesg');(raw/'after-kernel.txt').write_text(kernel.stdout)
    delta=[s for s in kernel.stdout.splitlines() if (m:=re.match(r'^\[\s*([0-9.]+)\]',s)) and float(m[1])>boundary]
    (raw/'kernel-delta.txt').write_text('\n'.join(delta)+'\n')
    post=trial.remote('python3 -c '+shlex.quote(silence.PREFLIGHT));(raw/'after-audio.txt').write_text(post.stdout+post.stderr)
    after=trial.phone_health()
    receipt={'started_at':start,'elapsed_seconds':round(elapsed,3),'wrapper_returncode':r.returncode,
             'before':before,'after':after,'same_boot':before['boot_id']==after['boot_id'],
             'after_audio_exit':post.returncode,'trace_capture_exit':captured.returncode,
             'kernel_capture_exit':kernel.returncode,'helper_sha256':hashlib.sha256(helper).hexdigest(),
             'observer_sha256':hashlib.sha256(observer).hexdigest() if observer else None,
             'supervisor_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
             'diagnostic_kind':'one-second-digital-zero' if args.zero_second else 'prepare-only',
             'result':json.loads(r.stdout) if r.returncode==0 else None}
    receipt['assessment']=classify(receipt,captured.stdout)
    (raw/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
    print(json.dumps({k:v for k,v in receipt.items() if k not in ('before','after')},indent=2))
    if not receipt['assessment']['diagnostic_completed']:raise SystemExit(1)

if __name__=='__main__':main()
