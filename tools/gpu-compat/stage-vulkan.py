#!/usr/bin/env python3
"""Prepare the separate Vulkan trial. Does not change accepted phone runtime."""
from pathlib import Path
import shutil

ROOT=Path(__file__).resolve().parents[2]
BASE=ROOT/'rootfs/gpu-compat-20260921'
for file in (BASE/'shims-vulkan').glob('*.so'):
    target=BASE/'runtime/vendor/lib64'
    if file.name.startswith('android.hardware.'):
        target=target/'hw'
    shutil.copy2(file,target/file.name)
shutil.copy2(BASE/'vulkan-hal-probe-android', BASE/'runtime/system/bin/vulkan-hal-probe')
shutil.copy2(ROOT/'tools/gpu-compute-probe/compute.spv',BASE/'runtime/compute.spv')
print('Prepared private host runtime for new vulkan-headless variant.')
