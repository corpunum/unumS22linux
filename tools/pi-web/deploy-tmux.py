#!/usr/bin/env python3
"""Stage the exact working Alpine tmux/musl closure, no package DB changes."""
import base64
import hashlib
import json
from pathlib import Path
import shlex
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[2]
SOURCE=ROOT/'rootfs/pi-web-20260921/tmux-musl/root'
FILES={
 'tmux':(SOURCE/'usr/bin/tmux','b676aa136782b513866867ce896cf66a1ee7edbb7dac211fda045b39487bf03b'),
 'lib/ld-musl-aarch64.so.1':(SOURCE/'lib/ld-musl-aarch64.so.1','32377e6d71725bb019e9ff6d5e9f16b4d5156d6f2c36504191c2d6a7c4d4a44d'),
 'lib/libncursesw.so.6':(SOURCE/'usr/lib/libncursesw.so.6.6','a9e06c751f47179afa780c9285e5e22ed8883d6ee1379de25a4fc9eb90054345'),
 'lib/libevent_core-2.1.so.7':(SOURCE/'usr/lib/libevent_core-2.1.so.7.0.2','8625e2b9987b48665c79d4bee9c1a1337bceb06dbd18fba6b16700a9a55c3eff'),
}
REMOTE=r'''import base64,hashlib,json,os,pathlib,sys
p=pathlib.Path
assert os.geteuid()==0 and p('/proc/1/comm').read_text().strip()=='native-guardian'
base=p('/srv/s22/arch/opt/s22-pi-web/tmux-musl')
assert not base.exists()
items=json.load(sys.stdin)
assert set(items)=={'tmux','lib/ld-musl-aarch64.so.1','lib/libncursesw.so.6','lib/libevent_core-2.1.so.7'}
for name,item in items.items():
 data=base64.b64decode(item['data'],validate=True)
 assert hashlib.sha256(data).hexdigest()==item['sha256']
base.mkdir(mode=0o755); (base/'lib').mkdir(mode=0o755)
for name,item in items.items():
 with (base/name).open('xb') as f:
  f.write(base64.b64decode(item['data'])); f.flush(); os.fchmod(f.fileno(),0o755); os.fsync(f.fileno())
 assert hashlib.sha256((base/name).read_bytes()).hexdigest()==item['sha256']
print(json.dumps({'installed':{name:item['sha256'] for name,item in items.items()},'setuid_helpers_installed':False,'package_db_changed':False}))
'''

def main():
    data={}
    for name,(path,want) in FILES.items():
        blob=path.read_bytes()
        if hashlib.sha256(blob).hexdigest()!=want: raise RuntimeError('Signature-reviewed bytes changed')
        data[name]={'data':base64.b64encode(blob).decode(),'sha256':want}
    if sys.argv[1:]!=['--install']:
        print(json.dumps({name:item['sha256'] for name,item in data.items()},indent=2)); return
    r=subprocess.run([str(ROOT/'tools/s22-ssh'),'python3 -c '+shlex.quote(REMOTE)],
                     input=json.dumps(data),capture_output=True,text=True,timeout=25)
    if r.returncode: raise RuntimeError(r.stderr)
    print(r.stdout)

if __name__=='__main__': main()
