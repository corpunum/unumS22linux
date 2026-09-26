#!/usr/bin/env python3
"""Reversible two-control digital-route test; PREPARE-only by default.

--zero-second instead runs one second of digital zeros, never capture.
No gains, amplifier-enable or pin-switch writes.
The two selectors return to their captured zero values after the child exits.
"""
import argparse
import base64
from datetime import datetime,timezone
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import shlex
import stat
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[2]
HCI_TRIAL_ID='hci-candidate-20260924-second'
HCI_CANDIDATE_GNU_BUILD_ID='b2dda820b18d410d9bf12f1bd2584567d545991d'
HCI_CANDIDATE_RECOVERY_SHA256='42da267f3dd9f94f30f62a95fb2ac13f91d4cf98f1a2307f7cc14e45d9c49be5'
AUDIO_TRIAL_ID='audio-zero-20260927'
AUDIO_OPERATION_KIND='audio-zero'
AUDIO_TRACE_ROOT='/srv/s22'
AUDIO_TRACE_REL=('audio-trials-20260927',AUDIO_TRIAL_ID,'trace.strace')
TRACE_MAX_BYTES=2_000_000
TRACE_MIN_FREE_BYTES=16*1024*1024
TRACE_MIN_FREE_INODES=32
AUDIO_PREFLIGHT=r'''import json,pathlib,re,shutil,subprocess
p=pathlib.Path
def need(condition,reason):
 if not condition:raise RuntimeError(reason)
need(all(shutil.which(name) for name in ('python3','aplay','strace')),'diagnostic_tool_unavailable')
def control(name):
 r=subprocess.run(['amixer','-c','0','cget','name='+name],capture_output=True,text=True,timeout=5)
 values=re.findall(r'^  : values=(.*)$',r.stdout,re.M)
 need(r.returncode==0 and len(values)==1,'control_read_failed:'+name)
 return values[0],r.stdout
need(p('/proc/1/comm').read_text().strip()=='native-guardian','wrong_native_session')
need(p('/proc/asound/card0/id').read_text().strip()=='RainbowPrince','wrong_card')
node=p('/sys/class/sound/pcmC0D2p').resolve(strict=True)
need(node==p('/sys/devices/platform/sound/sound/card0/pcmC0D2p'),'wrong_pcm_node')
need((node/'dev').read_text().strip()=='116:3','wrong_pcm_device_number')
need(p('/sys/class/net/ecm0/carrier').read_text().strip()=='1','ecm_carrier_down')
amps={}
for name in ('Left AMP Enable Switch','Right AMP Enable Switch'):
 value,raw=control(name);need(value=='off','amplifier_enable_not_off:'+name);amps[name]=value
routes={}
for name in ('ABOX SPUS OUT2','ABOX UAIF1 SPK'):
 value,raw=control(name)
 need(value=='0' and "Item #0 'RESERVED'" in raw and "Item #1 'SIFS0'" in raw,
      'route_precondition_failed:'+name)
 routes[name]={'value':value,'items':['RESERVED','SIFS0']}
status=p('/proc/asound/card0/pcm2p/sub0/status').read_text().strip()
need(status=='closed','pcm_not_initially_closed')
abox=p('/sys/bus/platform/devices/18c50000.abox')
service=(abox/'service').read_text().strip();reset=(abox/'reset_count').read_text().strip()
need(service=='1','abox_service_unavailable');need(reset=='0','abox_reset_count_nonzero')
cache_only=p('/sys/kernel/debug/regmap/18c50000.abox/cache_only').read_text().strip()
print(json.dumps({'card':'RainbowPrince','pcm':'116:3','status':status,'amps':amps,
 'routes':routes,'service':service,'reset_count':reset,
 'runtime_status':(abox/'power/runtime_status').read_text().strip(),
 'cache_only':cache_only},sort_keys=True))
'''
# Both remote snippets open every ancestor with O_NOFOLLOW, then use dirfds for
# the fixed relative names.  The stage uses O_EXCL for both the trial directory
# and the trace reservation; strace is allowed to truncate only that reserved
# inode.  The readback rechecks device/inode and has a hard byte cap.
REMOTE_TRACE_STAGE=r'''import json,os,stat,sys
root_path=sys.argv[1];expected_uid=int(sys.argv[2]);require_separate_fs=sys.argv[3]=='1'
minimum_bytes=int(sys.argv[4]);minimum_inodes=int(sys.argv[5])
flags=os.O_RDONLY|os.O_DIRECTORY|os.O_CLOEXEC|getattr(os,'O_NOFOLLOW',0)
def need(ok,code):
 if not ok:raise RuntimeError(code)
def open_dir(path):
 need(path.startswith('/') and '..' not in path.split('/'),'bad_absolute_path')
 fd=os.open('/',flags)
 try:
  for part in (x for x in path.split('/') if x):
   nextfd=os.open(part,flags,dir_fd=fd);os.close(fd);fd=nextfd
  return fd
 except BaseException:
  os.close(fd);raise
def check_dir(fd,uid,mode,dev=None):
 info=os.fstat(fd)
 need(stat.S_ISDIR(info.st_mode),'not_directory')
 need(info.st_uid==uid,'unexpected_directory_owner')
 need(stat.S_IMODE(info.st_mode)==mode,'unexpected_directory_mode')
 if dev is not None:need(info.st_dev==dev,'filesystem_boundary_changed')
 return info
def child_dir(parent,name,uid,mode,dev,create=False):
 if create:
  try:os.mkdir(name,mode,dir_fd=parent);os.fsync(parent)
  except FileExistsError:pass
 fd=os.open(name,flags,dir_fd=parent)
 check_dir(fd,uid,mode,dev);return fd
rootfd=open_dir(root_path)
try:
 root=check_dir(rootfd,expected_uid,0o700)
 if require_separate_fs:
  srvfd=open_dir('/srv')
  try:
   srv=os.fstat(srvfd);need(stat.S_ISDIR(srv.st_mode),'srv_not_directory')
   need(root.st_dev!=srv.st_dev,'persistent_trace_root_not_separate_from_srv_overlay')
  finally:os.close(srvfd)
 fs=os.fstatvfs(rootfd)
 free_bytes=fs.f_bavail*fs.f_frsize;free_inodes=getattr(fs,'f_favail',fs.f_ffree)
 need(free_bytes>=minimum_bytes,'persistent_trace_space_below_floor')
 need(free_inodes>=minimum_inodes,'persistent_trace_inodes_below_floor')
 namespace=child_dir(rootfd,'audio-trials-20260927',expected_uid,0o700,root.st_dev,True)
 try:
  os.mkdir('audio-zero-20260927',0o700,dir_fd=namespace);os.fsync(namespace)
  trial=child_dir(namespace,'audio-zero-20260927',expected_uid,0o700,root.st_dev)
  try:
   tfd=os.open('trace.strace',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_CLOEXEC|
               getattr(os,'O_NOFOLLOW',0),0o600,dir_fd=trial)
   try:
    trace=os.fstat(tfd)
    need(stat.S_ISREG(trace.st_mode) and trace.st_nlink==1,'trace_reservation_not_private_regular_file')
    need(trace.st_uid==expected_uid and stat.S_IMODE(trace.st_mode)==0o600,'trace_reservation_owner_or_mode_changed')
    need(trace.st_dev==root.st_dev and trace.st_size==0,'trace_reservation_filesystem_or_size_changed')
    os.fsync(tfd);os.fsync(trial);os.fsync(namespace)
    print(json.dumps({'path':root_path+'/audio-trials-20260927/audio-zero-20260927/trace.strace',
     'root_dev':root.st_dev,'namespace_dev':os.fstat(namespace).st_dev,
     'trial_dev':os.fstat(trial).st_dev,'trace_dev':trace.st_dev,'trace_ino':trace.st_ino,
     'trace_size':trace.st_size,'free_bytes':free_bytes,'free_inodes':free_inodes},sort_keys=True))
   finally:os.close(tfd)
  finally:os.close(trial)
 finally:os.close(namespace)
finally:os.close(rootfd)
'''
REMOTE_TRACE_READ=r'''import base64,json,os,stat,sys
root_path=sys.argv[1];expected_uid=int(sys.argv[2]);expected_dev=int(sys.argv[3]);expected_ino=int(sys.argv[4]);limit=int(sys.argv[5])
flags=os.O_RDONLY|os.O_DIRECTORY|os.O_CLOEXEC|getattr(os,'O_NOFOLLOW',0)
def need(ok,code):
 if not ok:raise RuntimeError(code)
def open_dir(path):
 need(path.startswith('/') and '..' not in path.split('/'),'bad_absolute_path')
 fd=os.open('/',flags)
 try:
  for part in (x for x in path.split('/') if x):
   nextfd=os.open(part,flags,dir_fd=fd);os.close(fd);fd=nextfd
  return fd
 except BaseException:
  os.close(fd);raise
def child(parent,name,uid,mode,dev):
 fd=os.open(name,flags,dir_fd=parent);info=os.fstat(fd)
 need(stat.S_ISDIR(info.st_mode) and info.st_uid==uid and stat.S_IMODE(info.st_mode)==mode and info.st_dev==dev,'trace_directory_changed')
 return fd
root=open_dir(root_path)
try:
 info=os.fstat(root);need(stat.S_ISDIR(info.st_mode) and info.st_uid==expected_uid and stat.S_IMODE(info.st_mode)==0o700 and info.st_dev==expected_dev,'trace_root_changed')
 namespace=child(root,'audio-trials-20260927',expected_uid,0o700,expected_dev)
 try:
  trial=child(namespace,'audio-zero-20260927',expected_uid,0o700,expected_dev)
  try:
   fd=os.open('trace.strace',os.O_RDONLY|os.O_NONBLOCK|os.O_CLOEXEC|getattr(os,'O_NOFOLLOW',0),dir_fd=trial)
   try:
    trace=os.fstat(fd)
    need(stat.S_ISREG(trace.st_mode) and trace.st_nlink==1 and trace.st_uid==expected_uid and
         stat.S_IMODE(trace.st_mode)==0o600 and trace.st_dev==expected_dev and trace.st_ino==expected_ino,
         'trace_reservation_identity_changed')
    need(0<trace.st_size<=limit,'trace_size_out_of_bounds')
    data=bytearray()
    while len(data)<trace.st_size:
     chunk=os.read(fd,min(65536,trace.st_size-len(data)))
     if not chunk:break
     data.extend(chunk)
    need(len(data)==trace.st_size,'trace_short_read')
    need(os.fstat(fd).st_size==len(data),'trace_size_changed_during_read')
    print(json.dumps({'path':root_path+'/audio-trials-20260927/audio-zero-20260927/trace.strace',
     'device':trace.st_dev,'inode':trace.st_ino,'size':len(data),
     'data_base64':base64.b64encode(bytes(data)).decode('ascii')},sort_keys=True))
   finally:os.close(fd)
  finally:os.close(trial)
 finally:os.close(namespace)
finally:os.close(root)
'''
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
def require(condition,code):
 if not condition:raise RuntimeError(code)
changed=[];events=[];child=None;child_dead=True;deadline_exceeded=False
result={'progress_samples':samples,'events':events,'before':{},'restored':{},
        'child_deadline_exceeded':False,'wrapper_interrupted':False,
        'child_started':False,'preconditions_verified':False,
        'cleanup_attempted':False,'cleanup_errors':[]}
try:
 require(pathlib.Path('/proc/1/comm').read_text().strip()=='native-guardian','wrong_native_session')
 require(status_text()=='closed','pcm_not_initially_closed')
 require(amps_muted(),'amplifier_enable_not_off')
 before={n:get(n) for n in routes};result['before']=before
 for value,raw in before.values():
  require(value=='0' and "Item #0 'RESERVED'" in raw and "Item #1 'SIFS0'" in raw,'route_precondition_failed')
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
 if observer:sample()
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
   out,err=child.communicate(timeout=max(0.01,min(0.2 if observer else 10,end-time.monotonic())))
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
    """Accept only advancing pointers across a contiguous captured RUNNING window.

    Pointer movement proves DMA progress for this bounded stream, not audible
    output. Missing/error/non-RUNNING observations inside the window, sequence
    gaps, malformed pointers, time reversal, or pointer regression fail
    closed. Physical playback is never inferred here.
    """
    if not isinstance(samples, list) or len(samples) > 256:
        return {'verified':False,'running_samples':0,'reason':'invalid sample collection'}
    points=[]
    interrupted_window=False
    for sample in samples:
        if not isinstance(sample, dict):
            if points:
                interrupted_window=True
            continue
        status=sample.get('alsa_status')
        if (not isinstance(status,str)
                or not re.search(r'^state\s*:\s*RUNNING\s*$',status,re.M)):
            if points:
                interrupted_window=True
            continue
        matches=re.findall(r'^hw_ptr\s*:\s*(\d+)$',status,re.M)
        timestamp=sample.get('monotonic')
        sequence=sample.get('sequence')
        if (len(matches)!=1 or isinstance(sequence,bool) or not isinstance(sequence,int)
                or sequence<0 or isinstance(timestamp,bool)
                or not isinstance(timestamp,(int,float))):
            return {'verified':False,'running_samples':len(points),'reason':'invalid RUNNING sample'}
        try:
            timestamp=float(timestamp)
        except (OverflowError,ValueError):
            return {'verified':False,'running_samples':len(points),'reason':'invalid RUNNING sample'}
        if not math.isfinite(timestamp):
            return {'verified':False,'running_samples':len(points),'reason':'invalid RUNNING sample'}
        if points and interrupted_window:
            return {'verified':False,'running_samples':len(points),
                    'reason':'RUNNING observations separated by a missing or non-RUNNING sample'}
        point=(sequence,timestamp,int(matches[0]))
        if points and point[0] != points[-1][0]+1:
            return {'verified':False,'running_samples':len(points),
                    'reason':'capture sequence gap inside RUNNING observation window'}
        if points and point[1] <= points[-1][1]:
            return {'verified':False,'running_samples':len(points),'reason':'non-monotonic observation'}
        if points and point[2] < points[-1][2]:
            return {'verified':False,'running_samples':len(points),'reason':'hw_ptr regression'}
        points.append(point)
        interrupted_window=False
    advance=points[-1][2]-points[0][2] if len(points)>=2 else 0
    return {
        'verified':len(points)>=2 and advance>0,
        'running_samples':len(points),
        'first_hw_ptr':points[0][2] if points else None,
        'last_hw_ptr':points[-1][2] if points else None,
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
    candidate=receipt.get('candidate_identity')
    before=receipt.get('before')
    candidate_identity_ok=(receipt.get('candidate_identity_verified') is True
        and isinstance(candidate,dict)
        and candidate.get('trial_identity')==HCI_TRIAL_ID
        and candidate.get('candidate_sha256')==HCI_CANDIDATE_RECOVERY_SHA256
        and candidate.get('gnu_build_id')==HCI_CANDIDATE_GNU_BUILD_ID
        and candidate.get('actual_mode')=='RECOVERY'
        and candidate.get('observer_receipt_verified') is True
        and candidate.get('live_recovery_hash_verified') is True
        and isinstance(candidate.get('boot_id'),str) and bool(candidate.get('boot_id'))
        and isinstance(before,dict) and candidate.get('boot_id')==before.get('boot_id'))
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
    accepted=bool(cleanup and capture_ok and receipt.get('same_boot') is True
                  and candidate_identity_ok
                  and child_ok and evidence_ok)
    dma=assess_dma_progress(progress_samples)
    period=audio_snapshot.period_progress(progress_samples)
    source_path=audio_snapshot.source_path_assessment(progress_samples)
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
            'source_path_assessment':source_path,
            'capture_correlation':correlation,
            'candidate_identity_verified':candidate_identity_ok,
            'physical_playback_verified':False,
            'outcome':'interrupted' if interrupted else ('completed' if accepted else 'failed-or-unproven')}


def validate_candidate_evidence(hci, observer, current, recovery_sha256):
    """Require the exact reviewed HCI candidate boot before audio mutation."""
    hci.require(hci.observer_receipt_valid(observer),
                'completed HCI candidate observer receipt is unavailable or stale')
    hci.validate_snapshot(current, post_reboot=True)
    hci.require(current.get('boot_id') == observer.get('boot_id'),
                'current boot does not match the completed HCI candidate observer')
    hci.require(recovery_sha256 == hci.EXPECTED_FLASH_SHA256,
                'live RECOVERY hash does not match the reviewed HCI candidate')
    return {
        'trial_identity': hci.TRIAL_ID,
        'candidate_sha256': recovery_sha256,
        'gnu_build_id': current.get('gnu_build_id'),
        'boot_id': current.get('boot_id'),
        'actual_mode': 'RECOVERY',
        'observer_receipt_verified': True,
        'live_recovery_hash_verified': True,
    }


def transport_project_root(project_root=ROOT):
    """Resolve this worktree's common checkout for private pinned SSH state."""
    result=subprocess.run(['git','-C',str(project_root),'rev-parse',
                           '--path-format=absolute','--git-common-dir'],
                          capture_output=True,text=True,timeout=5,check=False)
    if result.returncode != 0:
        raise RuntimeError('cannot resolve trusted transport project root')
    common=Path(result.stdout.strip()).resolve()
    source_root=common.parent if common.name=='.git' else common
    known_hosts=source_root/'evidence/native-linux-20260919/native-v2-known-hosts'
    if not (source_root/'tools/s22-ssh').is_file() or not known_hosts.is_file():
        raise RuntimeError('pinned USB transport wrapper/known_hosts are unavailable')
    return source_root


def verify_live_hci_candidate(project_root=ROOT, transport_root=None):
    """Read the existing HCI observer, live boot identity and full RECOVERY hash.

    This intentionally does not inspect, remove, or reuse the consumed HCI
    one-shot marker. The audio trial has its own unique, create-exclusive
    receipt directory.
    """
    hci = load('audio_recovery_observer',
               project_root/'tools/hardware/audio-recovery-reboot-once.py')
    observer_path = hci.observer_receipt_path(hci.OUT, hci.TRIAL_ID)
    observer = hci.read_json(observer_path)
    hci.require(hci.observer_receipt_valid(observer),
                'completed HCI candidate observer receipt is unavailable or stale')
    transport_root=transport_root or transport_project_root(project_root)
    current = hci.snapshot_over('usb', project_root=transport_root)
    hci.validate_snapshot(current, post_reboot=True)
    hci.require(current.get('boot_id') == observer.get('boot_id'),
                'current boot does not match the completed HCI candidate observer')
    recovery_sha256 = hci.candidate_hash('usb', project_root=transport_root)
    return validate_candidate_evidence(hci, observer, current, recovery_sha256)

def _directory_flags():
    return os.O_RDONLY|os.O_DIRECTORY|os.O_CLOEXEC|getattr(os,'O_NOFOLLOW',0)


def _ensure_private_child(parent_fd,name,*,create,exclusive=False):
    if '/' in name or name in ('','.','..'):
        raise RuntimeError('unsafe private artifact path component')
    if create:
        if exclusive:
            os.mkdir(name,0o700,dir_fd=parent_fd);os.fsync(parent_fd)
        else:
            try:os.mkdir(name,0o700,dir_fd=parent_fd);os.fsync(parent_fd)
            except FileExistsError:pass
    fd=os.open(name,_directory_flags(),dir_fd=parent_fd)
    info=os.fstat(fd)
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.getuid()
            or stat.S_IMODE(info.st_mode)!=0o700):
        os.close(fd);raise RuntimeError('private artifact directory ownership or mode mismatch')
    return fd


def create_private_trial_dir(name,root=ROOT):
    """Create one owner-only output directory without following path links."""
    root_fd=os.open(root,_directory_flags())
    try:
        root_info=os.fstat(root_fd)
        if not stat.S_ISDIR(root_info.st_mode):raise RuntimeError('worktree root is not a directory')
        current=root_fd
        opened=[]
        try:
            for part in ('rootfs','audio-trials-20260927'):
                child=_ensure_private_child(current,part,create=True)
                opened.append(child);current=child
            trial_fd=_ensure_private_child(current,name,create=True,exclusive=True)
            opened.append(trial_fd)
            path=root/'rootfs'/'audio-trials-20260927'/name
            return path,trial_fd
        except BaseException:
            for fd in reversed(opened):os.close(fd)
            raise
    finally:os.close(root_fd)


def write_private_artifact(directory_fd,name,data):
    if '/' in name or name in ('','.','..'):
        raise RuntimeError('unsafe artifact filename')
    if isinstance(data,str):data=data.encode()
    fd=os.open(name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_CLOEXEC|
               getattr(os,'O_NOFOLLOW',0),0o600,dir_fd=directory_fd)
    try:
        info=os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink!=1
                or info.st_uid!=os.getuid() or stat.S_IMODE(info.st_mode)!=0o600):
            raise RuntimeError('private artifact file ownership or mode mismatch')
        view=memoryview(data)
        while view:
            written=os.write(fd,view)
            if written<=0:raise OSError('short private artifact write')
            view=view[written:]
        os.fsync(fd)
    finally:os.close(fd)
    os.fsync(directory_fd)


def load_trial_guard(project_root=ROOT):
    path=project_root/'tools/hardware/device-trial-guard.py'
    if not path.is_file():
        raise RuntimeError('shared durable device-trial guard is unavailable; execute is disabled')
    return load('s22_device_trial_guard',path)


def remote_trace_stage(trial):
    command=shlex.join(['python3','-c',REMOTE_TRACE_STAGE,AUDIO_TRACE_ROOT,'0','1',
                        str(TRACE_MIN_FREE_BYTES),str(TRACE_MIN_FREE_INODES)])
    result=trial.remote(command)
    if result.returncode:
        raise RuntimeError('exclusive persistent trace staging failed: '+
                           (result.stderr.decode('utf-8','replace') if isinstance(result.stderr,bytes) else str(result.stderr))[:400])
    try:metadata=json.loads(result.stdout)
    except (TypeError,ValueError) as error:raise RuntimeError('trace stage returned malformed identity receipt') from error
    expected=AUDIO_TRACE_ROOT+'/'+('/'.join(AUDIO_TRACE_REL))
    if (not isinstance(metadata,dict) or metadata.get('path')!=expected
            or metadata.get('root_dev')!=metadata.get('trace_dev')
            or metadata.get('namespace_dev')!=metadata.get('root_dev')
            or metadata.get('trial_dev')!=metadata.get('root_dev')
            or not isinstance(metadata.get('trace_ino'),int)
            or isinstance(metadata.get('trace_ino'),bool)
            or metadata.get('trace_size')!=0
            or metadata.get('free_bytes',0)<TRACE_MIN_FREE_BYTES
            or metadata.get('free_inodes',0)<TRACE_MIN_FREE_INODES):
        raise RuntimeError('trace stage identity/space receipt failed validation')
    return metadata


def remote_trace_read(trial,metadata):
    command=shlex.join(['python3','-c',REMOTE_TRACE_READ,AUDIO_TRACE_ROOT,'0',
                        str(metadata['root_dev']),str(metadata['trace_ino']),str(TRACE_MAX_BYTES)])
    result=trial.remote(command)
    if result.returncode:
        raise RuntimeError('reserved trace readback failed: '+
                           (result.stderr.decode('utf-8','replace') if isinstance(result.stderr,bytes) else str(result.stderr))[:400])
    try:record=json.loads(result.stdout)
    except (TypeError,ValueError) as error:raise RuntimeError('trace readback returned malformed receipt') from error
    if (not isinstance(record,dict) or record.get('path')!=metadata['path']
            or record.get('device')!=metadata['trace_dev']
            or record.get('inode')!=metadata['trace_ino']
            or isinstance(record.get('size'),bool)
            or not isinstance(record.get('size'),int)
            or not 0<record['size']<=TRACE_MAX_BYTES):
        raise RuntimeError('trace readback identity or size failed validation')
    try:data=base64.b64decode(record['data_base64'],validate=True)
    except (KeyError,ValueError) as error:raise RuntimeError('trace readback payload is invalid') from error
    if len(data)!=record['size'] or len(data)>TRACE_MAX_BYTES:
        raise RuntimeError('trace readback byte count mismatch')
    return data.decode('utf-8','replace'),record


def begin_and_stage_trace(trial,operation,raw_fd):
    """Keep a durable operation UNKNOWN if remote stage outcome is ambiguous."""
    operation.begin(project_root=ROOT)
    try:
        return remote_trace_stage(trial)
    except BaseException as error:
        disposition={'remote_trace_stage_outcome':'unknown',
         'remote_staging_may_be_partial':True,
         'route_or_pcm_operation_invoked':False,
         'reason_type':type(error).__name__,
         'retry_permitted':False}
        write_private_artifact(raw_fd,'unknown.txt',json.dumps(disposition,sort_keys=True)+'\n')
        raise RuntimeError('remote trace stage outcome is unknown; route and PCM were not invoked; do not retry') from error


def _remote_text(result):
    value=result.stdout
    return value.decode('utf-8','replace') if isinstance(value,bytes) else str(value)


def _run(args,operation=None):
    transport_root=transport_project_root()
    # Use the original reviewed checkout for the private wrapper/known_hosts;
    # keep this worktree as the source of the audio operation and artifacts.
    trial=load('hardware_trial',transport_root/'tools/gpu-compat/run-trial.py')
    before=trial.phone_health()
    candidate=verify_live_hci_candidate(project_root=ROOT,transport_root=transport_root)
    if candidate.get('boot_id') != before.get('boot_id'):
        raise RuntimeError('HCI candidate boot changed during preflight')
    pre=trial.remote('python3 -c '+shlex.quote(AUDIO_PREFLIGHT))
    if pre.returncode:
        error=pre.stderr.decode('utf-8','replace') if isinstance(pre.stderr,bytes) else str(pre.stderr)
        raise RuntimeError(error)
    if not args.execute:
        print(json.dumps({'preflight_passed':True,
                          'candidate_identity_verified':True,
                          'candidate_trial_identity':candidate['trial_identity'],
                          'candidate_gnu_build_id':candidate['gnu_build_id'],
                          'candidate_recovery_sha256':candidate['candidate_sha256'],
                          'controls':['ABOX SPUS OUT2','ABOX UAIF1 SPK'],
                          'new_value':1,'required_previous_value':0,'prepare_only':not args.zero_second}));return
    raw,raw_fd=create_private_trial_dir(args.name)
    try:
        audio_pre=json.loads(pre.stdout)
        write_private_artifact(raw_fd,'before.json',json.dumps({'health':before,'audio':audio_pre},indent=2)+'\n')
    except BaseException:
        os.close(raw_fd);raise
    kernel=trial.remote('dmesg')
    if kernel.returncode:
        os.close(raw_fd);raise RuntimeError('pre-operation kernel capture failed')
    kernel_text=_remote_text(kernel)
    write_private_artifact(raw_fd,'before-kernel.txt',kernel_text)
    boundary=max(map(float,re.findall(r'^\[\s*([0-9.]+)\]',kernel_text,re.M)),default=0)
    if operation is None:
        os.close(raw_fd);raise RuntimeError('execute requires the shared durable operation guard')
    try:trace_meta=begin_and_stage_trace(trial,operation,raw_fd)
    except BaseException:
        os.close(raw_fd);raise
    write_private_artifact(raw_fd,'trace-stage.json',json.dumps(trace_meta,indent=2)+'\n')
    trace=trace_meta['path']
    command=shlex.join(['strace','-f','-qq','-tt','-T','-s','160','-o',trace,
                       '-e','trace=openat,close,ioctl,read,pread64,write','python3','-c',WRAPPER])
    helper_name='audio-zero-one-second.py' if args.zero_second else 'audio-pcm-prepare-only.py'
    helper=(ROOT/'tools/hardware'/helper_name).read_bytes()
    observer=(ROOT/'tools/hardware/audio-progress-snapshot.py').read_bytes() if args.sample_progress else b''
    payload=json.dumps({'helper':helper.decode(),'observer':observer.decode(),
                        'trial_id':args.name}).encode()
    start=datetime.now(timezone.utc).isoformat();t=time.monotonic()
    try:r=subprocess.run([str(transport_root/'tools/s22-ssh'),command],input=payload,capture_output=True,timeout=45)
    except subprocess.TimeoutExpired:
        write_private_artifact(raw_fd,'unknown.txt','Host timeout; no retry. Durable operation remains pending/UNKNOWN; inspect child, PCM and route cleanup before reconciliation.\n')
        os.close(raw_fd);raise
    elapsed=time.monotonic()-t
    write_private_artifact(raw_fd,'stdout.txt',r.stdout)
    write_private_artifact(raw_fd,'stderr.txt',r.stderr)
    captured_text='';captured_record=None;trace_capture_exit=1
    try:
        captured_text,readback=remote_trace_read(trial,trace_meta);trace_capture_exit=0
        write_private_artifact(raw_fd,'strace.txt',captured_text)
        captured_record={key:readback[key] for key in ('path','device','inode','size')}
        write_private_artifact(raw_fd,'trace-readback.json',json.dumps(captured_record,indent=2)+'\n')
    except BaseException as error:
        write_private_artifact(raw_fd,'trace-readback-error.txt',type(error).__name__+'\n')
    kernel=trial.remote('dmesg')
    kernel_text=_remote_text(kernel)
    write_private_artifact(raw_fd,'after-kernel.txt',kernel_text)
    delta=[s for s in kernel_text.splitlines() if (m:=re.match(r'^\[\s*([0-9.]+)\]',s)) and float(m[1])>boundary]
    write_private_artifact(raw_fd,'kernel-delta.txt','\n'.join(delta)+'\n')
    post=trial.remote('python3 -c '+shlex.quote(AUDIO_PREFLIGHT))
    post_stdout=_remote_text(post);post_stderr=post.stderr.decode('utf-8','replace') if isinstance(post.stderr,bytes) else str(post.stderr)
    write_private_artifact(raw_fd,'after-audio.txt',post_stdout+post_stderr)
    after=trial.phone_health()
    receipt={'started_at':start,'elapsed_seconds':round(elapsed,3),'wrapper_returncode':r.returncode,
             'before':before,'after':after,'same_boot':before['boot_id']==after['boot_id'],
             'candidate_identity':candidate,'candidate_identity_verified':True,
             'after_audio_exit':post.returncode,'trace_capture_exit':trace_capture_exit,
             'kernel_capture_exit':kernel.returncode,'helper_sha256':hashlib.sha256(helper).hexdigest(),
             'observer_sha256':hashlib.sha256(observer).hexdigest() if observer else None,
             'supervisor_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
             'trace_stage':{k:v for k,v in trace_meta.items() if k not in ('path',)},
             'trace_readback':captured_record,
             'diagnostic_kind':'one-second-digital-zero' if args.zero_second else 'prepare-only',
             'result':json.loads(r.stdout) if r.returncode==0 else None}
    receipt['assessment']=classify(receipt,captured_text)
    write_private_artifact(raw_fd,'receipt.json',json.dumps(receipt,indent=2)+'\n')
    print(json.dumps({k:v for k,v in receipt.items()
                      if k not in ('before','after','candidate_identity')},indent=2))
    cleanup_confirmed=(isinstance(receipt.get('result'),dict)
                       and receipt['result'].get('cleanup_verified') is True
                       and post.returncode==0 and receipt['same_boot'] is True)
    if cleanup_confirmed:
        outcome='success' if receipt['assessment']['diagnostic_completed'] else 'failed-cleanup-confirmed'
        operation.complete(str(raw/'receipt.json'),outcome=outcome,cleanup_confirmed=True)
    os.close(raw_fd)
    if not receipt['assessment']['diagnostic_completed']:raise SystemExit(1)


def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('name');ap.add_argument('--execute',action='store_true')
    ap.add_argument('--zero-second',action='store_true')
    ap.add_argument('--sample-progress',action='store_true');args=ap.parse_args()
    if args.sample_progress and not args.zero_second:ap.error('progress sampling requires --zero-second')
    if not re.fullmatch('[a-z0-9-]+',args.name):ap.error('unique lowercase trial name required')
    if args.execute and (args.name!=AUDIO_TRIAL_ID or not args.zero_second or not args.sample_progress):
        ap.error('execute is restricted to audio-zero-20260927 --zero-second --sample-progress')
    if args.execute:
        guard=load_trial_guard()
        with guard.acquire_operation_lock(ROOT,args.name,AUDIO_OPERATION_KIND) as operation:
            return _run(args,operation)
    return _run(args)

if __name__=='__main__':main()
