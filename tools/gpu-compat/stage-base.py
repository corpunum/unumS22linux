#!/usr/bin/env python3
"""Host-only isolated Bionic runtime from saved Lineage recovery libraries.

No Android init, services, network configuration, partitions or device files
are copied. Vendor libraries are staged separately after closure review.
"""
import hashlib
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'lineage/build-20260915/unpacked/ramdisk_extracted'
BASE = ROOT / 'rootfs/gpu-compat-20260921'
DEST = BASE / 'runtime'

def main():
    if DEST.exists():
        raise SystemExit('Refuse to overwrite an existing runtime.')
    for name in ['system/bin/linker64', 'system/bin/toybox', 'system/bin/sh',
                 'system/lib64/libc.so', 'system/lib64/libm.so', 'system/lib64/libdl.so']:
        if not (SOURCE / name).is_file():
            raise RuntimeError('Missing saved recovery artifact: ' + name)
    (DEST / 'system/bin').mkdir(parents=True)
    shutil.copytree(SOURCE / 'system/lib64', DEST / 'system/lib64', symlinks=True)
    for name in ['linker64', 'toybox', 'sh']:
        shutil.copy2(SOURCE / 'system/bin' / name, DEST / 'system/bin' / name)
    (DEST / 'system/etc').mkdir()
    shutil.copy2(SOURCE / 'system/etc/ld.config.txt', DEST / 'system/etc/ld.config.txt')
    for name in ['dev', 'proc', 'sys', 'vendor/lib64/hw', 'data/local/tmp', 'cache', 'tmp']:
        (DEST / name).mkdir(parents=True, exist_ok=True)
    files = {}
    for p in sorted(DEST.rglob('*')):
        if p.is_symlink():
            files[str(p.relative_to(DEST))] = {'symlink': str(p.readlink())}
        elif p.is_file():
            files[str(p.relative_to(DEST))] = {'bytes': p.stat().st_size,
                                             'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}
    (BASE / 'base-manifest.json').write_text(json.dumps(files, indent=2) + '\n')
    print(json.dumps({'runtime': str(DEST), 'files': len(files),
                      'bytes': sum(v.get('bytes', 0) for v in files.values())}))

if __name__ == '__main__':
    main()
