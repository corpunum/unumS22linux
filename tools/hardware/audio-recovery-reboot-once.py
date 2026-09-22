#!/usr/bin/env python3
"""One explicitly selected recovery reboot, followed by bounded observation.

Requires the separate, successful audio RECOVERY flash receipt. No retries of
reboot, firmware writes, Android targets or hardware-control probes.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shlex
import subprocess
import time

ROOT = Path(__file__).resolve().parents[2]
SSH = ROOT / 'tools/s22-ssh'
OUT = ROOT / 'rootfs/main-driver-loop-20260921/audio-reboot'
SNAPSHOT = r'''
import json,pathlib,urllib.request
p=pathlib.Path
def read(name,limit=16384):
 try:
  with open(name,'rb') as f:return f.read(limit).decode('utf-8','replace').rstrip('\0\n')
 except OSError:return None
ready=read('/run/s22-persistent-ready.json')
state=dict(boot_id=read('/proc/sys/kernel/random/boot_id'),pid1=read('/proc/1/comm'),
 uptime_seconds=float(read('/proc/uptime').split()[0]),
 boot_reset=read('/proc/boot_reset',8192),temperature=int(read('/sys/class/power_supply/battery/temp'))/10,
 native_ready=p('/run/native-ready').exists(),persistent_ready=json.loads(ready) if ready else None,
 cp_status=read('/sys/devices/platform/cpif/modem_ctrl/status'),
 hci_count=len(list(p('/sys/class/bluetooth').glob('hci*'))),pending_kill_tasks=[])
for proc in p('/proc').glob('[0-9]*'):
 status=read(proc/'status',8192)
 if not status:continue
 fields=dict(line.split(':',1) for line in status.splitlines() if ':' in line)
 if (int(fields.get('SigPnd','0'),16)|int(fields.get('ShdPnd','0'),16)) & 0x100:
  state['pending_kill_tasks'].append(dict(pid=int(proc.name),name=fields.get('Name','').strip()))
try:
 op=urllib.request.build_opener(urllib.request.ProxyHandler({}))
 with op.open('http://127.0.0.1:8089/health',timeout=3) as f:state['model']=json.load(f)
except Exception as e:state['model_error']=type(e).__name__
print(json.dumps(state))
'''


def snapshot():
    result = subprocess.run([str(SSH), 'python3 -c '+shlex.quote(SNAPSHOT)],
                            capture_output=True, text=True, timeout=12)
    if result.returncode:
        raise RuntimeError('SSH snapshot unavailable')
    return json.loads(result.stdout)


def save(name, value):
    with (OUT/name).open('x') as stream:
        json.dump(value, stream, indent=2); stream.write('\n')


def main():
    global OUT
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute',action='store_true')
    parser.add_argument('--name',default='audio-reboot')
    parser.add_argument('--capture-early-kernel',action='store_true')
    parser.add_argument('--image-profile',choices=('core','extras'),default='core')
    args=parser.parse_args()
    if not re.fullmatch('[a-z0-9-]+',args.name):parser.error('use a unique lowercase receipt name')
    OUT=OUT.parent/args.name
    if not args.execute:
        print('Plan only: one s22-reboot recovery, observe up to 240s; no automatic retry.');return
    profiles={'core':('audio-recovery-flash.json','1c1b77a5e532e50b8274cfc68921aa9b1bfe6d4ae9a3459281be0cc033c5c3d5'),
              'extras':('audio-extra-recovery-flash.json','758fc9d30491e17b7c829a89d338ba69476efa15a1280deb8a1b9b8009687f4b')}
    flash_name,expected=profiles[args.image_profile]
    flash=json.loads((OUT.parent/flash_name).read_text())
    assert flash['partition_written']=='recovery'
    assert flash['readback_sha256']==expected
    live_hash=subprocess.run([str(SSH),'sha256sum /dev/block/by-name/recovery'],capture_output=True,text=True,timeout=30)
    assert live_hash.returncode==0 and live_hash.stdout.split()[0]==expected
    ack=subprocess.run(['systemctl','--user','is-active','s22-native-auto-ack.service'],capture_output=True,text=True)
    assert ack.returncode==0 and ack.stdout.strip()=='active'
    before=snapshot()
    assert before['pid1']=='native-guardian' and before['temperature']<42
    assert before['model'].get('status')=='ok' and before['native_ready']
    OUT.mkdir(exist_ok=False)
    save('before.json',before)
    save('request.json',{'utc':datetime.now(timezone.utc).isoformat(),'target':'recovery','reboot_count':1})
    started=time.monotonic()
    try:
        requested=subprocess.run([str(SSH),'s22-reboot recovery'],capture_output=True,text=True,timeout=15)
        save('request-result.json',{'returncode':requested.returncode,'stderr':requested.stderr})
    except subprocess.TimeoutExpired:
        save('request-result.json',{'ssh_timeout_seconds':15,'reboot_retried':False})
    count=0
    kernel_captured=False
    while time.monotonic()-started<240:
        try:
            current=snapshot()
            current['host_elapsed_seconds']=round(time.monotonic()-started,2)
            save('sample-%03d.json'%count,current)
            print(json.dumps({k:current[k] for k in ['host_elapsed_seconds','uptime_seconds','pid1','temperature','native_ready','pending_kill_tasks']}),flush=True)
            changed=current['boot_id']!=before['boot_id']
            if changed and args.capture_early_kernel and not kernel_captured:
                kernel=subprocess.run([str(SSH),'dmesg'],capture_output=True,text=True,timeout=12)
                with (OUT/'early-kernel.txt').open('x') as stream:stream.write(kernel.stdout)
                save('early-kernel-receipt.json',{'returncode':kernel.returncode,
                    'uptime_seconds':current['uptime_seconds'],'bytes':len(kernel.stdout.encode())})
                kernel_captured=True
            if changed and current['uptime_seconds']>=90 and current.get('model',{}).get('status')=='ok' and current['persistent_ready']:
                assert current['pid1']=='native-guardian' and current['native_ready']
                first_record=next(line for line in current['boot_reset'].splitlines() if re.match(r'^\[\s*\d+\]',line))
                assert ' / R / ' in first_record and 'INFORM3(12345674)' in first_record and '> RECOVERY >' in first_record
                save('result.json',{'new_boot':True,'actual_mode':'recovery','continuous_uptime_seconds':current['uptime_seconds'],
                     'host_elapsed_seconds':current['host_elapsed_seconds'],'model_healthy':True,
                     'pending_kill_tasks':current['pending_kill_tasks'],'reboot_requests':1})
                print('Recovery boot and >=90s uninterrupted uptime verified.',flush=True);return
        except (OSError,subprocess.TimeoutExpired,RuntimeError,ValueError) as error:
            print(json.dumps({'elapsed_seconds':round(time.monotonic()-started,2),'observation_error':type(error).__name__}),flush=True)
        count+=1
        time.sleep(5)
    save('result.json',{'acceptance':False,'reason':'240s observation ended without full readiness','reboot_requests':1})
    raise SystemExit(1)


if __name__=='__main__':main()
