#!/usr/bin/env python3
"""Copy one already-downloaded, verified small model into the test root only."""
import hashlib
import json
from pathlib import Path
import shlex
import subprocess

ROOT=Path(__file__).resolve().parents[2]
BASE=ROOT/'rootfs/gpu-compat-20260921'
source=ROOT/'rootfs/model-bench/models/Qwen3.5-0.8B-Q4_0.gguf'
digest='57d1997790d1744fba5b40a7317df71ea5e2acee28c47e78f0cce39c0703f8cf'
assert source.stat().st_size==563036064
assert hashlib.file_digest(source.open('rb'),'sha256').hexdigest()==digest
dest='/srv/s22/gpu-compat-20260921/llama-vulkan/models'
target=dest+'/'+source.name
archive=subprocess.Popen(['tar','-cf','-','-C',str(source.parent),source.name],stdout=subprocess.PIPE)
command='test ! -e '+shlex.quote(target)+' && mkdir -p '+shlex.quote(dest)+' && tar -xf - -C '+shlex.quote(dest)+' && chown 0:0 '+shlex.quote(target)+' && chmod 644 '+shlex.quote(target)
result=subprocess.run([str(ROOT/'tools/s22-ssh'),command],stdin=archive.stdout,capture_output=True,text=True,timeout=120)
archive.stdout.close()
if result.returncode or archive.wait(timeout=10):
    raise RuntimeError('Preserve partial file for inspection: '+result.stderr)
verified=subprocess.run([str(ROOT/'tools/s22-ssh'),'sha256sum '+shlex.quote(target)],capture_output=True,text=True,timeout=30)
assert verified.returncode==0 and verified.stdout.split()[0]==digest
manifest=BASE/'manifest-llama-vulkan.json'
expected=json.loads(manifest.read_text())
expected['models/'+source.name]=digest
manifest.write_text(json.dumps(expected,indent=2)+'\n')
print(json.dumps(dict(path=target,sha256=digest,bytes=source.stat().st_size)))
