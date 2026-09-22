#!/usr/bin/env python3
"""Capture private evidence after the interrupted HCI/audio trial, read-only."""
import hashlib,json,os,re,shlex,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'rootfs/main-driver-loop-20260921/hci-audio-panic-766'
os.umask(0o077);OUT.mkdir(exist_ok=False)
files={'last-kmsg.bin':'/proc/last_kmsg','boot-reset.bin':'/proc/boot_reset',
       'reset-reason.txt':'/proc/reset_reason','extra.txt':'/proc/extra',
       'bt-trace.txt':'/srv/s22/bt-board-20260922/hci-bridge-first.strace'}
meta={}
for name,path in files.items():
    r=subprocess.run([str(ROOT/'tools/s22-ssh'),'head -c 16777216 '+shlex.quote(path)],capture_output=True,timeout=30)
    (OUT/name).write_bytes(r.stdout)
    (OUT/(name+'.stderr')).write_bytes(r.stderr)
    meta[name]={'returncode':r.returncode,'bytes':len(r.stdout),'sha256':hashlib.sha256(r.stdout).hexdigest()}
    if name=='last-kmsg.bin':
        lines=r.stdout.replace(b'\x00',b'').decode(errors='replace').splitlines()
        markers=[i for i,line in enumerate(lines) if re.search(r'Kernel panic|BUG:|Unable to handle|Call trace:|pc :|lr :|Internal error',line)]
        indices=set()
        for i in markers:
            indices.update(range(max(0,i-3),min(len(lines),i+40)))
        excerpt='\n'.join(lines[i] for i in sorted(indices))+'\n'
        (OUT/'panic-excerpt.txt').write_text(excerpt)
        print(excerpt[:16000])
(OUT/'capture.json').write_text(json.dumps(meta,indent=2)+'\n')
print(json.dumps(meta,indent=2))
