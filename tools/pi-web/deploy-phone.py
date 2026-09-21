#!/usr/bin/env python3
"""Install only new Pi web files into the already mounted phone userdata."""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import shlex
import subprocess

ROOT=Path(__file__).resolve().parents[2]
REMOTE=r'''import base64,hashlib,json,os,pathlib,sys
p=pathlib.Path
assert os.geteuid()==0 and p('/proc/1/comm').read_text().strip()=='native-guardian'
assert json.loads(p('/run/s22-persistent-ready.json').read_text())['model_profile']=='qwen4b'
arch=p('/srv/s22/arch')
assert arch.stat().st_dev==p('/mnt/omarchy-trial').stat().st_dev
assert arch.stat().st_ino==p('/mnt/omarchy-trial').stat().st_ino
spec={
 'ttyd':(arch/'opt/s22-pi-web/ttyd',0o755),
 'pi-web-session':(arch/'usr/local/bin/pi-web-session',0o755),
 'start-agent-web.py':(p('/srv/s22/agent-web/start-agent-web.py'),0o755),
}
incoming=json.load(sys.stdin)
assert set(incoming)==set(spec)
for name,(target,mode) in spec.items():
 assert not target.exists() and not target.is_symlink(), 'Existing target: '+str(target)
 data=base64.b64decode(incoming[name]['data'],validate=True)
 assert hashlib.sha256(data).hexdigest()==incoming[name]['sha256']
 if name=='ttyd': assert incoming[name]['sha256']=='b38acadd89d1d396a0f5649aa52c539edbad07f4bc7348b27b4f4b7219dd4165'
for name,(target,mode) in spec.items():
 target.parent.mkdir(parents=True,exist_ok=True)
 with target.open('xb') as f:
  os.fchmod(f.fileno(),mode);os.fchown(f.fileno(),0,0)
  f.write(base64.b64decode(incoming[name]['data']));f.flush();os.fsync(f.fileno())
 assert hashlib.sha256(target.read_bytes()).hexdigest()==incoming[name]['sha256']
sessions=arch/'home/alarm/.pi/agent/web-sessions'
assert not sessions.exists() and not sessions.is_symlink()
sessions.mkdir(mode=0o700);os.chown(sessions,1000,1000)
private=p('/srv/s22/agent-web/private')
private.mkdir(mode=0o700)
print(json.dumps({'installed':{name:{'sha256':incoming[name]['sha256'],'mode':oct(mode)} for name,(_,mode) in spec.items()},'sessions_uid':1000,'service_started':False,'autostart_changed':False}))
'''


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install',action='store_true')
    args=parser.parse_args()
    paths={'ttyd':ROOT/'rootfs/pi-web-20260921/ttyd.aarch64',
           'pi-web-session':Path(__file__).with_name('pi-web-session'),
           'start-agent-web.py':Path(__file__).with_name('start-agent-web.py')}
    files={name:{'data':base64.b64encode(path.read_bytes()).decode(),
                 'sha256':hashlib.sha256(path.read_bytes()).hexdigest()} for name,path in paths.items()}
    if not args.install:
        print(json.dumps({name:record['sha256'] for name,record in files.items()},indent=2))
        return
    result=subprocess.run([str(ROOT/'tools/s22-ssh'),'python3 -c '+shlex.quote(REMOTE)],
                          input=json.dumps(files),capture_output=True,text=True,timeout=35)
    if result.returncode: raise RuntimeError(result.stderr)
    receipt=json.loads(result.stdout)
    (ROOT/'rootfs/pi-web-20260921/deployment.json').write_text(json.dumps(receipt,indent=2)+'\n')
    print(json.dumps(receipt,indent=2))


if __name__=='__main__': main()
