#!/usr/bin/env python3
"""Assemble exact recovered vendor files, never load them on the host."""
import hashlib
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / 'rootfs/gpu-compat-20260921'
manifest = json.loads((BASE / 'vendor-closure.json').read_text())
for source in [ROOT/'rootfs/gpu-vendor-reuse-20260921/lib64', BASE/'vendor/lib64']:
    for file in source.rglob('*.so'):
        target = BASE/'runtime/vendor/lib64'/file.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            assert target.read_bytes() == file.read_bytes(), str(target)
        else:
            shutil.copy2(file, target)
shutil.copy2(BASE/'opencl-probe-android', BASE/'runtime/system/bin/opencl-probe')
print(json.dumps({str(f.relative_to(BASE/'runtime')): hashlib.sha256(f.read_bytes()).hexdigest()
                  for f in (BASE/'runtime/vendor').rglob('*.so')}, indent=2))
