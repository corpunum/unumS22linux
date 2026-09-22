#!/usr/bin/env python3
"""Back up and install the optional ALSA control-only startup delta.

No compositor stop or phone reboot; the existing session bind was tested
separately. Default is a host-only manifest, --install is explicit.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shlex
import subprocess

ROOT=Path(__file__).resolve().parents[2]
SOURCE=ROOT/'tools/persistence/start-persistent-desktop.py'
BEFORE='5d5ce794f683ece35eabf432261f7a7f0ccc1db815763bcc65bd7870ffb524a0'
REMOTE=r'''import hashlib,json,os,pathlib,sys
p=pathlib.Path
assert os.geteuid()==0 and p('/proc/1/comm').read_text().strip()=='native-guardian'
assert json.loads(p('/run/s22-persistent-ready.json').read_text())['model_profile']=='qwen4b'
incoming=sys.stdin.buffer.read(); assert hashlib.sha256(incoming).hexdigest()==AFTER
compile(incoming,'start-persistent-desktop','exec')
target=p('/usr/local/bin/start-persistent-desktop'); info=target.lstat()
assert target.is_file() and not target.is_symlink() and info.st_uid==0 and not info.st_mode & 0o022
assert hashlib.sha256(target.read_bytes()).hexdigest()==BEFORE
backup=p(BACKUP); assert not backup.exists()
os.umask(0o077); backup.mkdir(mode=0o700)
with (backup/'start-persistent-desktop.before').open('xb') as f:
 f.write(target.read_bytes()); f.flush(); os.fsync(f.fileno())
nextfile=target.with_name(target.name+'.audio-next'); assert not nextfile.exists() and not nextfile.is_symlink()
with nextfile.open('xb') as f:
 f.write(incoming); f.flush(); os.fchmod(f.fileno(),0o755); os.fsync(f.fileno())
os.replace(nextfile,target)
fd=os.open(target.parent,os.O_DIRECTORY); os.fsync(fd); os.close(fd)
assert hashlib.sha256(target.read_bytes()).hexdigest()==AFTER
print(json.dumps({'before_sha256':BEFORE,'after_sha256':AFTER,'backup':str(backup),
                  'installed':True,'rebooted':False,'startup_reboot_tested':False}))
'''

def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--install',action='store_true')
    ap.add_argument('--before-sha256',default=BEFORE)
    ap.add_argument('--name',default='audio-control-startup-20260922');args=ap.parse_args()
    if not re.fullmatch('[0-9a-f]{64}',args.before_sha256):ap.error('expected SHA256 required')
    if not re.fullmatch('[a-z0-9-]+',args.name):ap.error('safe unique name required')
    data=SOURCE.read_bytes(); after=hashlib.sha256(data).hexdigest()
    if not args.install:
        print(json.dumps({'before_sha256':args.before_sha256,'after_sha256':after,'bytes':len(data)}));return
    receipt_path=ROOT/'rootfs/main-driver-loop-20260921'/(args.name+'-install.json')
    assert not receipt_path.exists()
    code=REMOTE.replace('BEFORE',repr(args.before_sha256)).replace('AFTER',repr(after)).replace('BACKUP',repr('/srv/s22/'+args.name))
    result=subprocess.run([str(ROOT/'tools/s22-ssh'),'python3 -c '+shlex.quote(code)],
                          input=data,capture_output=True,timeout=25)
    if result.returncode:raise RuntimeError(result.stderr.decode())
    receipt=json.loads(result.stdout)
    with receipt_path.open('x') as stream:stream.write(json.dumps(receipt,indent=2)+'\n')
    print(json.dumps(receipt,indent=2))

if __name__=='__main__':main()
