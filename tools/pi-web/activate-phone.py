#!/usr/bin/env python3
"""Back up and activate the reviewed web startup delta, without a reboot.

Only the existing native desktop supervisor, Pi web launcher/readiness helper,
session wrapper and userdata enable marker are changed. Exact previous hashes
prevent clobbering an independently edited deployment. Default is a host-only
manifest.
"""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import shlex
import subprocess

ROOT=Path(__file__).resolve().parents[2]
SPECS={
    # Install the imported module before atomically replacing its launcher.
    '/srv/s22/agent-web/pi_readiness.py': (
        Path(__file__).with_name('pi_readiness.py'), None, 0o644),
    '/usr/local/bin/start-persistent-desktop': (
        ROOT/'tools/persistence/start-persistent-desktop.py',
        '081d97e3afcb80da84b3d27ff0cc7901be8d53bc980af02345c6b19ff0b9d851', 0o755),
    '/srv/s22/agent-web/start-agent-web.py': (
        Path(__file__).with_name('start-agent-web.py'),
        '649b9f93bfe23f6f01e48ab26e303c4c8a5cc7c2eebdc40fdb96f0d78a9887c6', 0o755),
    '/srv/s22/arch/usr/local/bin/pi-web-session': (
        Path(__file__).with_name('pi-web-session'),
        'c58e7ce052e73fe13e09e5e2c1a6f00b06a244279af84693fdab44e973d86751', 0o755),
}
REMOTE=r'''import base64,hashlib,json,os,pathlib,sys
p=pathlib.Path
assert os.geteuid()==0 and p('/proc/1/comm').read_text().strip()=='native-guardian'
assert json.loads(p('/run/s22-persistent-ready.json').read_text())['model_profile']=='qwen4b'
incoming=json.load(sys.stdin)
assert set(incoming)=={'/srv/s22/agent-web/pi_readiness.py','/usr/local/bin/start-persistent-desktop','/srv/s22/agent-web/start-agent-web.py','/srv/s22/arch/usr/local/bin/pi-web-session'}
backup=p('/srv/s22/agent-web/private/before-autostart')
enabled=p('/srv/s22/agent-web/enabled')
assert not backup.exists() and not enabled.exists()
for name,item in incoming.items():
 target=p(name)
 if item['before'] is None:
  if target.exists() or target.is_symlink():
   st=target.stat()
   assert not target.is_symlink() and st.st_uid==0 and not st.st_mode & 0o022
   assert hashlib.sha256(target.read_bytes()).hexdigest()==item['after'], name
   item['already_current']=True
  else:
   item['already_current']=False
 else:
  st=target.stat()
  assert not target.is_symlink() and st.st_uid==0 and not st.st_mode & 0o022
  current=hashlib.sha256(target.read_bytes()).hexdigest()
  assert current in (item['before'],item['after']), name
  item['already_current']=current==item['after']
 data=base64.b64decode(item['data'],validate=True)
 assert hashlib.sha256(data).hexdigest()==item['after']
 assert not target.with_name(target.name+'.pi-web-next').exists()
os.umask(0o077)
backup.mkdir(mode=0o700)
receipt={}
for name,item in incoming.items():
 target=p(name)
 if item.get('already_current'):
  current=hashlib.sha256(target.read_bytes()).hexdigest()
  receipt[name]={'before':current,'after':current}
  continue
 if item['before'] is not None:
  (backup/target.name).write_bytes(target.read_bytes())
 staged=target.with_name(target.name+'.pi-web-next')
 with staged.open('xb') as f:
  f.write(base64.b64decode(item['data'])); f.flush(); os.fchmod(f.fileno(),item['mode']); os.fsync(f.fileno())
 if item['before'] is None:
  os.link(staged,target)
  staged.unlink()
 else:
  os.replace(staged,target)
 assert hashlib.sha256(target.read_bytes()).hexdigest()==item['after']
 receipt[name]={'before':item['before'],'after':item['after']}
with enabled.open('x') as f:
 f.write('Enabled: private tailnet Pi terminal; optional desktop-ready startup.\n'); f.flush(); os.fsync(f.fileno())
print(json.dumps({'files':receipt,'enabled':True,'desktop_restarted':False,'rebooted':False,'cold_boot_tested':False}))
'''

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--activate',action='store_true')
    args=parser.parse_args()
    payload={name:{'before':old,'after':hashlib.sha256(local.read_bytes()).hexdigest(),
                   'mode':mode,'data':base64.b64encode(local.read_bytes()).decode()}
             for name,(local,old,mode) in SPECS.items()}
    if not args.activate:
        print(json.dumps({name:{k:v for k,v in item.items() if k!='data'} for name,item in payload.items()},indent=2))
        return
    result=subprocess.run([str(ROOT/'tools/s22-ssh'),'python3 -c '+shlex.quote(REMOTE)],
                          input=json.dumps(payload),capture_output=True,text=True,timeout=25)
    if result.returncode: raise RuntimeError(result.stderr)
    receipt=json.loads(result.stdout)
    (ROOT/'rootfs/pi-web-20260921/activation.json').write_text(json.dumps(receipt,indent=2)+'\n')
    print(json.dumps(receipt,indent=2))

if __name__=='__main__': main()
