#!/usr/bin/env python3
"""Back up only the verified 80 MiB radio firmware partition, read-only.

Never accesses EFS/cpefs/cp_debug or writes any phone block device. Captured
bytes remain private. An incomplete capture is retained and never reused.
"""
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT/'rootfs/hardware-reuse-20260921/radio-readonly.img'
SIZE = 80*1024*1024
REMOTE = r'''import hashlib,json,os,pathlib,stat,sys
p=pathlib.Path
node=p('/proc/1/root/dev/block/by-name/radio')
assert os.readlink(node)=='../sda17'
assert 'PARTNAME=radio' in p('/sys/dev/block/259:1/uevent').read_text().splitlines()
assert int(p('/sys/dev/block/259:1/size').read_text())==163840
assert p('/sys/devices/platform/cpif/modem_state').read_text().strip()=='INIT'
info=node.stat()
assert stat.S_ISBLK(info.st_mode) and info.st_rdev==os.makedev(259,1)
digest=hashlib.sha256()
left=80*1024*1024
fd=os.open(node,os.O_RDONLY|os.O_CLOEXEC)
try:
 assert os.fstat(fd).st_rdev==info.st_rdev
 while left:
  chunk=os.read(fd,min(left,1024*1024))
  if not chunk: raise RuntimeError('Short firmware read')
  digest.update(chunk)
  sys.stdout.buffer.write(chunk)
  left-=len(chunk)
 sys.stdout.buffer.flush()
finally:
 os.close(fd)
print(json.dumps(dict(bytes=80*1024*1024,sha256=digest.hexdigest(),device='radio',
                     phone_partition_writes=False,
                     modem_state=p('/sys/devices/platform/cpif/modem_state').read_text().strip())),file=sys.stderr)
'''


def main():
    os.umask(0o077)
    OUT.parent.mkdir(parents=True,exist_ok=True)
    with OUT.open('xb') as output:
        result=subprocess.run([str(ROOT/'tools/s22-ssh'),
                               'timeout -k 3 45 python3 -c '+shlex.quote(REMOTE)],
                              stdout=output,stderr=subprocess.PIPE,timeout=55)
        output.flush()
        os.fsync(output.fileno())
    if result.returncode:
        raise RuntimeError('Retained incomplete private capture; do not reuse: '+result.stderr.decode(errors='replace'))
    remote=json.loads(result.stderr)
    digest=hashlib.sha256(OUT.read_bytes()).hexdigest()
    if OUT.stat().st_size!=SIZE or remote['bytes']!=SIZE or digest!=remote['sha256']:
        raise RuntimeError('Capture size/hash mismatch; preserve for inspection')
    os.chmod(OUT,0o400)
    receipt=dict(remote,host_sha256=digest,complete=True,
                 limitations='Firmware identity/header audit pending; no modem boot or SIM operation.')
    OUT.with_suffix('.json').write_text(json.dumps(receipt,indent=2)+'\n')
    print(json.dumps(receipt,indent=2))


if __name__=='__main__':
    main()
