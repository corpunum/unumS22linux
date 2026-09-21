#!/usr/bin/env python3
"""Create only explicit, verified device nodes inside one test root.

Original device nodes and their permissions are never changed. No mounts or
driver calls happen here. SGPU access enables real hardware execution in the
subsequent separately supervised process.
"""
import argparse
import json
from pathlib import Path
import shlex
import subprocess

ROOT=Path(__file__).resolve().parents[2]
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('variant',choices=['opencl-headless','vulkan-headless','llama-vulkan'])
p.add_argument('set',choices=['basic','sgpu-render','sgpu-primary','dma-system'])
args=p.parse_args()
phone='/srv/s22/gpu-compat-20260921/'+args.variant
script='''import json,os,pathlib,stat
root=pathlib.Path(DEST)
assert root.is_dir()
names=NAMES
result=[]
for name in names:
 src=pathlib.Path('/dev')/name
 st=src.stat()
 assert stat.S_ISCHR(st.st_mode),name
 if name.startswith('dri/'):
  driver=(pathlib.Path('/sys/class/drm')/src.name/'device/driver').resolve()
  assert str(driver)=='/sys/bus/platform/drivers/sgpu',str(driver)
 dst=root/'dev'/name
 dst.parent.mkdir(parents=True,exist_ok=True)
 if dst.exists():
  assert stat.S_ISCHR(dst.stat().st_mode) and dst.stat().st_rdev==st.st_rdev
 else:
  os.mknod(dst,stat.S_IFCHR|0o600,st.st_rdev)
 os.chown(dst,1000,1000)
 os.chmod(dst,0o600)
 result.append(dict(path=str(dst),major=os.major(st.st_rdev),minor=os.minor(st.st_rdev)))
print(json.dumps(result))
'''.replace('DEST',repr(phone)).replace('NAMES',repr({
 'basic':['null','urandom','random'],
 'sgpu-render':['dri/renderD128'],
 'sgpu-primary':['dri/card0'],
 'dma-system':['dma_heap/system','dma_heap/system-uncached'],
}[args.set]))
r=subprocess.run([str(ROOT/'tools/s22-ssh'),'python3 -c '+shlex.quote(script)],
                 capture_output=True,text=True,timeout=20)
if r.returncode:
    raise RuntimeError(r.stderr)
record=json.loads(r.stdout)
(ROOT/'rootfs/gpu-compat-20260921'/('devices-'+args.variant+'-'+args.set+'.json')).write_text(json.dumps(record,indent=2)+'\n')
print(json.dumps(record,indent=2))
