#!/usr/bin/env python3
"""Assemble the private llama Vulkan trial; never replace system libraries."""
import hashlib
import json
from pathlib import Path
import shutil

ROOT=Path(__file__).resolve().parents[2]
BASE=ROOT/'rootfs/gpu-compat-20260921'
runtime=BASE/'runtime'
files={
    BASE/'vulkan-bridge/libvulkan.so': runtime/'vendor/lib64/libvulkan.so',
    BASE/'llama-vulkan-build/bin/test-backend-ops': runtime/'system/bin/test-backend-ops',
    BASE/'llama-vulkan-build/bin/llama-bench': runtime/'system/bin/llama-bench',
}
manifest={}
for source,dest in files.items():
    content=source.read_bytes()
    if dest.exists() and dest.read_bytes() != content:
        raise RuntimeError('Preserve existing different trial artifact: '+str(dest))
    shutil.copy2(source,dest)
    manifest[str(dest.relative_to(runtime))]=hashlib.sha256(content).hexdigest()
(BASE/'llama-vulkan-artifacts.json').write_text(json.dumps(manifest,indent=2)+'\n')
print(json.dumps(manifest,indent=2))
