#!/usr/bin/env python3
"""Persist the tested close_range workaround for fresh canonical Pi launches.

No service/model restart or partition write. Saves exact originals first,
requires known current launcher/system hashes, and leaves model config alone.
"""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import shlex
import subprocess

ROOT = Path(__file__).resolve().parents[2]
OLD = {
    'usr/local/bin/pi': '9e47d90efa7aa6165935f800abee4ad33a1ebdf5c315bbc4b68150f182210e26',
    'usr/local/bin/pi-research': '4313efedc543a976b9ee3ac1c4ad28a03e0919acdbf419f4b531c7f4c2b7f274',
    'home/alarm/.pi/agent/SYSTEM.md': 'c32f2d864559e856f0cb46d21abe74e3f6b96d54a105e4fd262529dc2c98196a',
    'home/alarm/.pi/research/SYSTEM.md': 'c32f2d864559e856f0cb46d21abe74e3f6b96d54a105e4fd262529dc2c98196a',
}
SOURCES = {
    'usr/local/bin/pi': 'tools/pi-agent/pi',
    'usr/local/bin/pi-research': 'tools/pi-agent/pi-research',
    'home/alarm/.pi/agent/SYSTEM.md': 'tools/pi-agent/SYSTEM.md',
    'home/alarm/.pi/research/SYSTEM.md': 'tools/pi-agent/SYSTEM.md',
    'usr/local/libexec/s22-close-range-compat': 'builds/runtime-compat-20260921/close-range-compat',
    'usr/local/libexec/s22-runtime-spawn-check.py': 'tools/hardware/runtime-spawn-check.py',
}
REMOTE = r'''
import base64,hashlib,json,os,pathlib,sys
p=pathlib.Path
assert p('/proc/1/comm').read_text().strip()=='native-guardian'
root=p('/mnt/omarchy-trial')
payload=json.load(sys.stdin)
old=payload['old']; files=payload['files']; stats={}
config={n:hashlib.sha256((root/n).read_bytes()).hexdigest() for n in
 ['home/alarm/.pi/agent/settings.json','home/alarm/.pi/agent/models.json',
  'home/alarm/.pi/research/settings.json','home/alarm/.pi/research/models.json']}
for name, item in files.items():
 target=root/name
 assert not target.is_symlink()
 data=base64.b64decode(item['content'])
 assert hashlib.sha256(data).hexdigest()==item['sha256']
 if name in old:
  assert target.is_file() and hashlib.sha256(target.read_bytes()).hexdigest()==old[name],name
  st=target.stat(); stats[name]=(st.st_uid,st.st_gid,st.st_mode&0o777)
 else: assert not target.exists(),name
backup=p('/srv/s22/runtime-compat-20260921/pi-originals')
backup.mkdir(mode=0o700,exist_ok=False)
for name in old:
 dest=backup/name; dest.parent.mkdir(parents=True,exist_ok=True)
 with dest.open('xb') as f: f.write((root/name).read_bytes()); f.flush(); os.fsync(f.fileno())
 os.chmod(dest,0o600)
for name,item in files.items():
 target=root/name; target.parent.mkdir(parents=True,exist_ok=True)
 temp=target.with_name(target.name+'.close-range-new')
 with temp.open('xb') as f: f.write(base64.b64decode(item['content'])); f.flush(); os.fsync(f.fileno())
 uid,gid,mode=stats.get(name,(0,0,0o755))
 os.chown(temp,uid,gid); os.chmod(temp,mode); os.replace(temp,target)
 assert hashlib.sha256(target.read_bytes()).hexdigest()==item['sha256']
for name,digest in config.items(): assert hashlib.sha256((root/name).read_bytes()).hexdigest()==digest
os.sync()
print(json.dumps({'files':{n:i['sha256'] for n,i in files.items()},'model_configs_unchanged':True,
 'backup':str(backup),'existing_processes_changed':False}))
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    files = {}
    for name, source in SOURCES.items():
        data = (ROOT/source).read_bytes()
        files[name] = dict(sha256=hashlib.sha256(data).hexdigest(),
                           content=base64.b64encode(data).decode())
    assert files['usr/local/libexec/s22-close-range-compat']['sha256'] == '313aa94848bdc719ce6ad44c649e74736968da033aa18d38f2de5bfd9d245b36'
    for name in ('runtime-close-range-subprocess-first', 'runtime-close-range-repeat', 'runtime-close-range-tmux'):
        receipt = json.loads((ROOT/'rootfs/main-driver-loop-20260921'/name/'receipt.json').read_text())
        assert receipt['returncode'] == 0 and receipt['same_boot'] and not receipt['new_pending_kill']
    if not args.execute:
        print(json.dumps({n:v['sha256'] for n,v in files.items()},indent=2)); return
    result = subprocess.run([str(ROOT/'tools/s22-ssh'),'python3 -c '+shlex.quote(REMOTE)],
        input=json.dumps({'old':OLD,'files':files}),capture_output=True,text=True,timeout=45)
    if result.returncode: raise RuntimeError(result.stderr)
    receipt = json.loads(result.stdout)
    out=ROOT/'rootfs/main-driver-loop-20260921/pi-compat-installed.json'
    with out.open('x') as f: json.dump(receipt,f,indent=2); f.write('\n')
    print(json.dumps(receipt,indent=2))


if __name__ == '__main__':
    main()
