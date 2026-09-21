#!/usr/bin/env python3
"""Stage fail-fast interop sentinels only in the private runtime copy."""
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT/'rootfs/gpu-compat-20260921'
for name in ['libsync.so', 'libnativewindow.so', 'android.hardware.graphics.mapper@4.0-impl-sgr.so']:
    target=BASE/'runtime/vendor/lib64'
    if name.startswith('android.hardware.'):
        target=target/'hw'
    shutil.copy2(BASE/'shims'/name,target/name)
print('Staged three fail-fast interop libraries in host private runtime only.')
