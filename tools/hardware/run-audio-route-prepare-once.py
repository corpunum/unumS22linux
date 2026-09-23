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
WRAPPER=r'''import json,pathlib,re,signal,subprocess,sys,time
request=json.loads(sys.stdin.read());helper=request['helper'];samples=[]
observer=None;signal_seen=None;routes=('ABOX SPUS OUT2','ABOX UAIF1 SPK')
if request.get('observer'):
 namespace={'__name__':'progress_observer'};exec(request['observer'],namespace)
 observer=namespace['snapshot']
def on_signal(signum,frame):
 global signal_seen
 signal_seen=signum
for signum in (signal.SIGINT,signal.SIGTERM):signal.signal(signum,on_signal)
def sample():
 if observer:
  try:samples.append(observer(True,sequence=len(samples),trial_id=request.get('trial_id')))
  except Exception as error:
   now=time.monotonic_ns()
   samples.append({'error_type':type(error).__name__,'monotonic':now/1e9,
                   'sample_monotonic_ns':now,'sequence':len(samples),
                   'trial_id':request.get('trial_id')})
def get(name):
 r=subprocess.run(['amixer','-c','0','cget','name='+name],capture_output=True,text=True,timeout=5)
 values=re.findall(r'^  : values=(.*)$',r.stdout,re.M)
 if r.returncode!=0 or len(values)!=1:raise RuntimeError('control_read_failed')
 return values[0],r.stdout
def amps_muted():
 return all(get(name)[0]=='off' for name in ('Left AMP Enable Switch','Right AMP Enable Switch'))
def status_text():
 return pathlib.Path('/proc/asound/card0/pcm2p/sub0/status').read_text().strip()
changed=[];events=[];child=None;child_dead=True;deadline_exceeded=False
result={'progress_samples':samples,'events':events,'before':{},'restored':{},
        'child_deadline_exceeded':False,'wrapper_interrupted':False,
        'child_started':False,'preconditions_verified':False,
        'cleanup_attempted':False,'cleanup_errors':[]}
try:
 assert pathlib.Path('/proc/1/comm').read_text().strip()=='native-guardian','wrong_native_session'
 assert status_text()=='closed','pcm_not_initially_closed'
 assert amps_muted(),'amplifier_enable_not_off'
 before={n:get(n) for n in routes};result['before']=before
 for value,raw in before.values():
  assert value=='0' and "Item #0 'RESERVED'" in raw and "Item #1 'SIFS0'" in raw,'route_precondition_failed'
 result['preconditions_verified']=True
 for name in routes:
  if signal_seen is not None:raise InterruptedError('wrapper_interrupted_before_stream')
  changed.append(name)
  r=subprocess.run(['amixer','-q','-c','0','cset','name='+name,'1'],capture_output=True,text=True,timeout=5)
  events.append({'control':name,'value':1,'returncode':r.returncode})
  if r.returncode!=0:raise RuntimeError('route_write_failed')
  if get(name)[0]!='1' or not amps_muted():raise RuntimeError('route_postcondition_failed')
 if signal_seen is not None:raise InterruptedError('wrapper_interrupted_before_stream')
 child=subprocess.Popen(['python3','-c',helper],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
 result['child_started']=True
 child_dead=False;end=time.monotonic()+10;out='';err=''
 while True:
  if signal_seen is not None:
   result['wrapper_interrupted']=True
   child.terminate()
   try:out,err=child.communicate(timeout=3)
   except subprocess.TimeoutExpired:
    child.kill()
    try:out,err=child.communicate(timeout=3)
    except subprocess.TimeoutExpired:break
   break
  try:
   out,err=child.communicate(timeout=max(0.01,min(1 if observer else 10,end-time.monotonic())))
   break
  except subprocess.TimeoutExpired:
   if time.monotonic()<end:
    sample();continue
   deadline_exceeded=True;result['child_deadline_exceeded']=True;child.terminate()
   try:out,err=child.communicate(timeout=3)
   except subprocess.TimeoutExpired:
    child.kill()
    try:out,err=child.communicate(timeout=3)
    except subprocess.TimeoutExpired:break
   break
 child_dead=child.poll() is not None
 result.update({'child_returncode':child.returncode if child_dead else None,
                'child_stdout':out,'child_stderr':err,
                'child_deadline_exceeded':deadline_exceeded,
                'child_interrupted':bool(result['wrapper_interrupted'] or deadline_exceeded),
                'progress_samples':samples})
except BaseException as error:
 result['wrapper_error_type']=type(error).__name__
 result['wrapper_error_code']=str(error)[:80] if isinstance(error,(AssertionError,RuntimeError,InterruptedError)) else type(error).__name__
 result['wrapper_interrupted']=bool(signal_seen is not None or isinstance(error,InterruptedError))
finally:
 result['child_exited']=child_dead
 result['cleanup_attempted']=child_dead
 if child_dead:
  try:result['pcm_status_before_restore']=status_text()
  except Exception as error:result['cleanup_errors'].append('pcm_status_before_restore:'+type(error).__name__)
  result['route_restore_safe']=result.get('pcm_status_before_restore')=='closed'
  if not result['route_restore_safe'] and changed:
   result['cleanup_errors'].append('pcm_not_closed_routes_not_restored')
  for name in reversed(changed) if result['route_restore_safe'] else ():
   try:
    prior=before[name][0]
    r=subprocess.run(['amixer','-q','-c','0','cset','name='+name,prior],capture_output=True,text=True,timeout=5)
    try:observed=get(name)[0]
    except Exception as error:
     observed=None;result['cleanup_errors'].append('route_verify:'+type(error).__name__)
    result['restored'][name]={'returncode':r.returncode,'value':observed,
                              'expected_value':prior,'verified':r.returncode==0 and observed==prior}
    if r.returncode!=0:result['cleanup_errors'].append('route_write:'+name)
   except Exception as error:
    result['restored'][name]={'returncode':None,'value':None,'expected_value':before.get(name,('',))[0],
                              'verified':False,'error_type':type(error).__name__}
    result['cleanup_errors'].append('route_restore:'+name+':'+type(error).__name__)
  try:result['amps_still_off']=amps_muted()
  except Exception as error:
   result['amps_still_off']=False;result['cleanup_errors'].append('amp_mute_verify:'+type(error).__name__)
  try:result['after_status']=status_text()
  except Exception as error:
   result['after_status']=None;result['cleanup_errors'].append('pcm_status_after_restore:'+type(error).__name__)
 else:
  result['cleanup_errors'].append('child_not_reaped_routes_not_restored')
 result['cleanup_verified']=bool(child_dead and result.get('after_status')=='closed'
  and result.get('preconditions_verified') is True
  and result.get('amps_still_off') is True
  and all(result['restored'].get(n,{}).get('verified') is True for n in changed)
  and not result['cleanup_errors'])
 print(json.dumps(result,sort_keys=True),flush=True)
if not child_dead:raise RuntimeError('child not reaped: selectors intentionally left unchanged')
'''

def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

audio_snapshot=load('audio_progress_snapshot',ROOT/'tools/hardware/audio-progress-snapshot.py')

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
    receipt=receipt if isinstance(receipt,dict) else {}
    raw_result=receipt.get('result')
    result=raw_result if isinstance(raw_result,dict) else {}
    trace=trace if isinstance(trace,str) else ''
    progress_samples=result.get('progress_samples',[])
    if not isinstance(progress_samples,list):progress_samples=[]
    routes=('ABOX SPUS OUT2','ABOX UAIF1 SPK')
    restored=result.get('restored',{})
    if not isinstance(restored,dict):restored={}
    cleanup_errors=result.get('cleanup_errors',[])
    cleanup_errors_valid=isinstance(cleanup_errors,list)
    cleanup_errors_empty=cleanup_errors_valid and not cleanup_errors
    child_stderr=result.get('child_stderr','')
    if not isinstance(child_stderr,str):child_stderr=''
    restored_ok=all(isinstance(restored.get(n),dict)
                    and restored[n].get('returncode')==0
                    and restored[n].get('value')=='0'
                    and restored[n].get('expected_value','0')=='0'
                    and restored[n].get('verified',True) is True for n in routes)
    # Old pre-classifier receipts can still be assessed, while new receipts
    # must explicitly attest that amp-enable controls stayed off and cleanup
    # finished without hidden errors.
    cleanup=(result.get('child_exited') is True and result.get('after_status')=='closed'
             and restored_ok and result.get('amps_still_off',True) is True
             and cleanup_errors_empty
             and result.get('preconditions_verified',True) is True
             and result.get('cleanup_verified',True) is True)
    interrupted=(result.get('child_deadline_exceeded') is True
                 or result.get('wrapper_interrupted') is True
                 or result.get('child_interrupted') is True
                 or 'Aborted by signal' in child_stderr
                 or bool(re.search(r'--- SIG(?:TERM|KILL)\b',trace)))
    capture_ok=all(receipt.get(n)==0 for n in
                   ('wrapper_returncode','after_audio_exit','trace_capture_exit','kernel_capture_exit'))
    child_ok=result.get('child_returncode')==0 and not interrupted
    prepared=bool(re.search(r'SNDRV_PCM_IOCTL_PREPARE[^\n]+= 0 ',trace))
    writes=len(re.findall(r'SNDRV_PCM_IOCTL_WRITEI_FRAMES[^\n]+= 0 ',trace))
    prepare_only=receipt.get('diagnostic_kind','prepare-only')=='prepare-only'
    evidence_ok=prepared and (writes==0 if prepare_only else writes>0)
    accepted=bool(cleanup and capture_ok and receipt.get('same_boot') is True and child_ok and evidence_ok)
    dma=assess_dma_progress(progress_samples)
    period=audio_snapshot.period_progress(progress_samples)
    timed=[]
    for sample in progress_samples:
        if not isinstance(sample,dict):continue
        start=sample.get('snapshot_start_monotonic_ns')
        end=sample.get('snapshot_end_monotonic_ns')
        if isinstance(start,int) and not isinstance(start,bool) and isinstance(end,int) and not isinstance(end,bool) and end>=start:
            timed.append((start,end))
    correlation={'timed_sample_count':len(timed),'sample_count':len(progress_samples),
                 'window_start_monotonic_ns':min((p[0] for p in timed),default=None),
                 'window_end_monotonic_ns':max((p[1] for p in timed),default=None),
                 'maximum_snapshot_duration_ns':max((e-s for s,e in timed),default=None),
                 'same_sample_source_intervals':True}
    if len(timed)>=2:
        starts=sorted(s for s,_ in timed)
        correlation['sample_start_spacing_ns']=[b-a for a,b in zip(starts,starts[1:])]
    return {'diagnostic_completed':accepted,'cleanup_verified':cleanup,
            'cleanup_error_count':len(cleanup_errors) if cleanup_errors_valid else 1,
            'child_interrupted':interrupted,'prepare_accepted':prepared,
            'successful_write_ioctls':writes,
            'write_eagain_count':len(re.findall(r'SNDRV_PCM_IOCTL_WRITEI_FRAMES[^\n]+= -1 EAGAIN',trace)),
            'dma_progress_verified':dma['verified'],'dma_progress':dma,
            'period_progress_verified':period['verified'],'period_progress':period,
            'capture_correlation':correlation,
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
    payload=json.dumps({'helper':helper.decode(),'observer':observer.decode(),
                        'trial_id':args.name}).encode()
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
