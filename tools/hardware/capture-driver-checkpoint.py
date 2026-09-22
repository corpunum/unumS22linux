#!/usr/bin/env python3
"""Capture read-only post-reboot control/WLAN/model checks and private logs."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import time

ROOT=Path(__file__).resolve().parents[2]
SSH=ROOT/'tools/s22-ssh'
STATE=r'''import hashlib,json,os,pathlib,stat,urllib.request
p=pathlib.Path
def read(name):
 try:return p(name).read_text().strip()
 except OSError:return None
result={'boot_id':read('/proc/sys/kernel/random/boot_id'),'boot_reset':read('/proc/boot_reset'),
 'uptime_seconds':float(read('/proc/uptime').split()[0]),'pid1':read('/proc/1/comm'),
 'temperature':int(read('/sys/class/power_supply/battery/temp'))/10,
 'startup_sha256':hashlib.sha256(p('/usr/local/bin/start-persistent-desktop').read_bytes()).hexdigest(),
 'ready':json.loads(read('/run/s22-persistent-ready.json') or 'null'),
 'card_id':read('/sys/class/sound/card0/id'),'native_sound_nodes':[], 'arch_sound_nodes':[]}
for key,directory in [('native_sound_nodes','/dev/snd'),('arch_sound_nodes','/mnt/omarchy-trial/dev/snd')]:
 for node in sorted(p(directory).glob('*')):
  s=node.lstat();result[key].append({'name':node.name,'character':stat.S_ISCHR(s.st_mode),
   'major':os.major(s.st_rdev),'minor':os.minor(s.st_rdev),'uid':s.st_uid,'gid':s.st_gid,'mode':oct(stat.S_IMODE(s.st_mode))})
result['control_mount_lines']=[x for x in p('/proc/self/mountinfo').read_text().splitlines() if ' /mnt/omarchy-trial/dev/snd/controlC0 ' in x]
result['startup_log']=read('/run/weston-native.log')
print(json.dumps(result))
'''

def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('name');args=ap.parse_args()
    if not re.fullmatch('[a-z0-9-]+',args.name):ap.error('unique safe name required')
    out=ROOT/'rootfs/main-driver-loop-20260921'/args.name
    out.mkdir(mode=0o700,exist_ok=False)
    summary={'utc':datetime.now(timezone.utc).isoformat(),'checks':{}}
    for name,command,source in [
        ('state','python3 -',STATE.encode()),
        ('arch-control','chroot /mnt/omarchy-trial /usr/local/libexec/s22-close-range-compat -- /usr/bin/python3 -',
         (ROOT/'tools/hardware/audio-arch-control-read.py').read_bytes()),
        ('wifi','python3 -',(ROOT/'tools/hardware/wifi-acceptance.py').read_bytes())]:
        start=time.monotonic()
        r=subprocess.run([str(SSH),command],input=source,capture_output=True,timeout=95)
        (out/(name+'.stdout')).write_bytes(r.stdout);(out/(name+'.stderr')).write_bytes(r.stderr)
        item={'returncode':r.returncode,'elapsed_seconds':round(time.monotonic()-start,3),
              'source_sha256':hashlib.sha256(source).hexdigest()}
        try:item['result']=json.loads(r.stdout)
        except ValueError:item['result']=None
        summary['checks'][name]=item
    (out/'receipt.json').write_text(json.dumps(summary,indent=2)+'\n')
    state=summary['checks']['state']['result'] or {}
    control=summary['checks']['arch-control']['result'] or {}
    wifi=summary['checks']['wifi']['result'] or {}
    print(json.dumps({'uptime_seconds':state.get('uptime_seconds'),'pid1':state.get('pid1'),
                     'startup_sha256':state.get('startup_sha256'),'card_id':state.get('card_id'),
                     'arch_sound_nodes':state.get('arch_sound_nodes'),'control_count':control.get('control_count'),
                     'control_mount_present':bool(state.get('control_mount_lines')),
                     'wifi_checks':wifi.get('checks'),'wifi_all_passed':wifi.get('all_checks_passed')},indent=2))
    return int(any(x['returncode'] for x in summary['checks'].values()))

if __name__=='__main__':raise SystemExit(main())
