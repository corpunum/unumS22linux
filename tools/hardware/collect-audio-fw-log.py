#!/usr/bin/env python3
"""Copy only the existing ABOX DRAM log, not SRAM/SFR or a dump trigger.

The pinned sysfs reader flushes firmware only at offset zero while active.
Skip byte zero and require suspended PM before/after. This is a stale
post-stream log snapshot, not live progress evidence. Output stays private.
"""
import hashlib
import json
from pathlib import Path
import shlex
import subprocess

ROOT=Path(__file__).resolve().parents[2]
CODE=r'''import os,pathlib,sys
assert pathlib.Path('/proc/1/comm').read_text().strip()=='native-guardian'
p=pathlib.Path('/sys/devices/platform/18c50000.abox/power/runtime_status')
assert p.read_text().strip()=='suspended'
fd=os.open('/sys/devices/platform/18c50000.abox/0.abox-debug/calliope_log',os.O_RDONLY|os.O_CLOEXEC|os.O_NOFOLLOW)
try:
 chunks=[];offset=1
 while offset<1048576:
  part=os.pread(fd,min(4096,1048576-offset),offset)
  if not part:raise RuntimeError('short log read')
  chunks.append(part);offset+=len(part)
 data=b''.join(chunks)
finally:os.close(fd)
assert p.read_text().strip()=='suspended' and len(data)==1048575
sys.stdout.buffer.write(data)
'''

def main():
    out=ROOT/'rootfs/main-driver-loop-20260921/audio-fw-log-after-progress-v2'
    out.mkdir(exist_ok=False)
    result=subprocess.run([str(ROOT/'tools/s22-ssh'),'python3 -c '+shlex.quote(CODE)],
                          capture_output=True,timeout=20)
    (out/'stderr.txt').write_bytes(result.stderr)
    if result.returncode:raise RuntimeError(result.stderr.decode())
    (out/'calliope-log-offset1.bin').write_bytes(result.stdout)
    receipt={'offset':1,'bytes':len(result.stdout),'sha256':hashlib.sha256(result.stdout).hexdigest(),
             'runtime_pm_before_after':'suspended','firmware_flush_requested':False,
             'register_or_sram_dump':False,'log_cursor_consumed':False}
    (out/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
    print(json.dumps(receipt,indent=2))

if __name__=='__main__':main()
