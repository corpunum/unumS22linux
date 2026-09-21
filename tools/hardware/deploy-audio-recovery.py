#!/usr/bin/env python3
"""Stage, or explicitly write, the reviewed audio-only RECOVERY candidate.

Never reboots. The only block-device write is the separately selected --flash
operation on exact recovery/sda16 (259:0), after old/new full-image hash checks.
No BOOT, MISC, EFS, identity, PIT, bootloader or TrustZone access.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shlex
import subprocess

ROOT = Path(__file__).resolve().parents[2]
SIZE = 100663296
BASE_SHA = '1a827b43d29141efb47f530902dd4e4ee2b6d780515893c9ecd676ad27efd7d1'
NEW_SHA = '1c1b77a5e532e50b8274cfc68921aa9b1bfe6d4ae9a3459281be0cc033c5c3d5'
IMAGE = ROOT/'builds/audio-early-20260922/recovery.img'
REMOTE = r'''
import fcntl,hashlib,json,os,pathlib,stat,struct,sys
p=pathlib.Path
size=100663296
base_sha='1a827b43d29141efb47f530902dd4e4ee2b6d780515893c9ecd676ad27efd7d1'
new_sha='1c1b77a5e532e50b8274cfc68921aa9b1bfe6d4ae9a3459281be0cc033c5c3d5'
assert p('/proc/1/comm').read_text().strip()=='native-guardian'
assert p('/proc/sys/kernel/osrelease').read_text().strip()=='5.10.260-g4e5c5ad7d950'
assert int(p('/sys/class/power_supply/battery/temp').read_text())<420
node=p('/dev/block/by-name/recovery')
assert node.resolve()==p('/dev/sda16')
assert 'PARTNAME=recovery' in p('/sys/class/block/sda16/uevent').read_text().splitlines()
assert int(p('/sys/class/block/sda16/size').read_text())==196608
info=node.stat()
assert stat.S_ISBLK(info.st_mode) and info.st_rdev==os.makedev(259,0)
assert not any(l.split()[2]=='259:0' for l in p('/proc/self/mountinfo').read_text().splitlines())
def read_recovery():
 fd=os.open(node,os.O_RDONLY|os.O_CLOEXEC)
 try:
  assert os.fstat(fd).st_rdev==info.st_rdev
  assert struct.unpack('Q',fcntl.ioctl(fd,0x80081272,b'\0'*8))[0]==size
  data=bytearray()
  while len(data)<size:
   chunk=os.read(fd,min(size-len(data),1048576)); assert chunk; data.extend(chunk)
  return bytes(data)
 finally: os.close(fd)
old=read_recovery()
assert hashlib.sha256(old).hexdigest()==base_sha, 'recovery baseline changed; no write'
directory=p('/srv/s22/audio-early-20260922')
candidate=directory/'recovery.img'
backup=directory/'native-v3-rollback.img'
os.umask(0o077)
mode=sys.argv[1]
if mode=='stage':
 data=sys.stdin.buffer.read(size+1)
 assert len(data)==size and hashlib.sha256(data).hexdigest()==new_sha
 directory.mkdir(mode=0o700,exist_ok=False)
 for target,content in [(backup,old),(candidate,data)]:
  with target.open('xb') as f: f.write(content); f.flush(); os.fsync(f.fileno())
 os.sync()
 assert hashlib.sha256(backup.read_bytes()).hexdigest()==base_sha
 assert hashlib.sha256(candidate.read_bytes()).hexdigest()==new_sha
 print(json.dumps({'mode':mode,'partition_written':False,'backup_sha256':base_sha,'candidate_sha256':new_sha}))
elif mode=='flash':
 assert directory.stat().st_uid==0 and not directory.is_symlink()
 assert candidate.is_file() and not candidate.is_symlink() and candidate.stat().st_uid==0
 assert backup.is_file() and not backup.is_symlink() and backup.stat().st_uid==0
 assert hashlib.sha256(backup.read_bytes()).hexdigest()==base_sha
 data=candidate.read_bytes()
 assert len(data)==size and hashlib.sha256(data).hexdigest()==new_sha
 fd=os.open(node,os.O_RDWR|os.O_CLOEXEC)
 try:
  assert os.fstat(fd).st_rdev==info.st_rdev
  assert struct.unpack('Q',fcntl.ioctl(fd,0x80081272,b'\0'*8))[0]==size
  offset=0
  while offset<size:
   written=os.pwrite(fd,data[offset:offset+1048576],offset)
   assert written>0; offset+=written
  os.fsync(fd)
 finally: os.close(fd)
 actual=hashlib.sha256(read_recovery()).hexdigest()
 assert actual==new_sha, 'RECOVERY READBACK MISMATCH; DO NOT REBOOT'
 print(json.dumps({'mode':mode,'partition_written':'recovery','bytes':size,
  'before_sha256':base_sha,'readback_sha256':actual,'reboot_performed':False}))
else: raise RuntimeError('unknown operation')
'''


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    choice=parser.add_mutually_exclusive_group()
    choice.add_argument('--stage',action='store_true')
    choice.add_argument('--flash',action='store_true')
    args=parser.parse_args()
    image=IMAGE.read_bytes()
    assert len(image)==SIZE and hashlib.sha256(image).hexdigest()==NEW_SHA
    assert hashlib.sha256((ROOT/'builds/native_handoff_v3.img').read_bytes()).hexdigest()==BASE_SHA
    assert hashlib.sha256((ROOT/'lineage/build-20260915/recovery.img').read_bytes()).hexdigest()=='b5bf01c4a47091eb95078fc69b133b44c2b453b31c23433594c5b605e3747b55'
    if not(args.stage or args.flash):
        print(json.dumps({'partition':'recovery','bytes':SIZE,'before':BASE_SHA,'candidate':NEW_SHA,'execution':False},indent=2));return
    mode='stage' if args.stage else 'flash'
    command='python3 -c '+shlex.quote(REMOTE)+' '+mode
    result=subprocess.run([str(ROOT/'tools/s22-ssh'),command],input=image if args.stage else b'',
                          capture_output=True,timeout=100)
    out=ROOT/'rootfs/main-driver-loop-20260921'/('audio-recovery-'+mode+'.json')
    if result.returncode:
        raise RuntimeError(mode+' failed; do not reboot: '+result.stderr.decode(errors='replace'))
    receipt=json.loads(result.stdout)
    with out.open('x') as f:json.dump(receipt,f,indent=2);f.write('\n')
    print(json.dumps(receipt,indent=2))


if __name__=='__main__':
    main()
