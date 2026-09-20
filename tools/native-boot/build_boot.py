#!/usr/bin/env python3
"""Reproduce the experimental gzip BOOT v2; never communicates with the phone.

WARNING: v2 did not restore USB after a normal reboot. Its mixed vendor-LZ4 /
generic-gzip compression boundary is under investigation. Do not reflash it.

Keep the complete V3 first-stage/module payload, externalize only the Alpine
lower archive to CACHE, and use the BOOT-specific recovery fallback helpers.
"""
import argparse
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
SIZE = 67108864
CPIO_SHA = 'abe6b1f47f9cb6ad8df2d82eda93cc0985fb8058ee67c29cc2e4d8aae8a8b84b'
KERNEL_SHA = '708474da33de9afafcd1835e6f4cf6f9b6e9451ad37748e95d322678250b69b7'
LOWER_SHA = '1139f04222e577f18dc2d540721c0be037700952780d54cfb919e39c5d15ac1b'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def run(*args):
    subprocess.run([str(a) for a in args], check=True, cwd=ROOT)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output-dir', type=Path, required=True)
    args = ap.parse_args()
    out = args.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=False)
    spec = importlib.util.spec_from_file_location('handoff', ROOT/'tools/headless-recovery/build_native_handoff.py')
    cpio = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = cpio
    spec.loader.exec_module(cpio)
    original = (ROOT/'builds/native_handoff_v3_ramdisk.cpio').read_bytes()
    assert sha(original) == CPIO_SHA
    records = cpio.parse_cpio(original)
    byname = {r.name: r for r in records}
    assert len(byname) == len(records)
    assert sha(byname['native/rootfs.tar.xz'].payload) == LOWER_SHA
    kernel = (ROOT/'lineage/build-20260915/unpacked/kernel').read_bytes()
    assert sha(kernel) == KERNEL_SHA
    run(sys.executable, ROOT/'tools/native-boot/build-native-start-boot-variant.py')
    replacements = {
        'system/bin/init': (ROOT/'builds/native_boot_recovery/init-wrapper').read_bytes(),
        'system/bin/native-guardian': (ROOT/'builds/native_boot_recovery/native-guardian').read_bytes(),
        'native/native-start': (ROOT/'builds/native_boot_start_persistent').read_bytes(),
    }
    assert all(b'BOOT' in replacements[n] for n in replacements)
    assert b'BOOT recovery restart unavailable' in replacements['system/bin/init']
    assert b'BOOT recovery restart unavailable' in replacements['system/bin/native-guardian']
    packed = b''.join(
        cpio.replace_record(r.name, replacements[r.name], r.fields)
        if r.name in replacements else r.raw
        for r in records if r.name != 'native/rootfs.tar.xz'
    )
    final = {r.name:r for r in cpio.parse_cpio(packed)}
    assert set(byname)-set(final) == {'native/rootfs.tar.xz'}
    assert not set(final)-set(byname)
    for name, record in final.items():
        if name not in replacements:
            assert record.raw == byname[name].raw, name
    assert final['init'].payload == final['system/bin/init.android'].payload
    assert final['init'].fields[1] & 0o170000 == 0o100000
    assert final['system/bin/recovery'].payload.startswith(b'\x7fELF')
    assert 'native-enable' in final
    modules = {n:sha(r.payload) for n,r in final.items() if n.startswith('lib/modules/')}
    assert sum(n.endswith('.ko') for n in modules) == 324
    (out/'ramdisk.cpio').write_bytes(packed)
    compressed = gzip.compress(packed, compresslevel=9, mtime=0)
    assert gzip.decompress(compressed) == packed
    (out/'ramdisk.gz').write_bytes(compressed)
    # avbtool resolves the hash descriptor's partition name to boot.img.
    image = out/'boot.img'
    run(sys.executable, ROOT/'tools/mkbootimg/mkbootimg.py',
        '--kernel', ROOT/'lineage/build-20260915/unpacked/kernel',
        '--ramdisk', out/'ramdisk.gz', '--header_version', '4',
        '--os_version', '16.0.0', '--os_patch_level', '2026-09',
        '--cmdline', ' bootconfig', '--output', image)
    raw_size = image.stat().st_size
    assert raw_size < SIZE-69632, raw_size
    raw = image.read_bytes()
    assert raw[:8] == b'ANDROID!'
    ksize, rsize, os_version, hsize = struct.unpack_from('<4I', raw, 8)
    assert (ksize, rsize, hsize) == (len(kernel), len(compressed), 1584)
    assert struct.unpack_from('<I', raw, 40)[0] == 4
    assert struct.unpack_from('<I', raw, 1580)[0] == 0
    assert os_version == ((16 << 14) << 11) | ((2026-2000) << 4) | 9
    assert raw[44:1580].rstrip(b'\0') == b' bootconfig'
    assert raw[4096:4096+ksize] == kernel
    rstart = 4096+((ksize+4095)//4096)*4096
    assert raw[rstart:rstart+rsize] == compressed
    run(sys.executable, ROOT/'tools/avb/avbtool.py', 'add_hash_footer',
        '--image', image, '--partition_size', SIZE, '--partition_name', 'boot',
        '--algorithm', 'NONE', '--rollback_index', '0', '--salt', KERNEL_SHA,
        '--prop', 'com.android.build.boot.os_version:16',
        '--prop', 'com.android.build.boot.fingerprint:samsung/lineage_r0s/r0s:16/BP4A.251205.006/4a67c928b4:userdebug/release-keys')
    assert image.stat().st_size == SIZE
    run(sys.executable, ROOT/'tools/avb/avbtool.py', 'verify_image', '--image', image)
    manifest = {
        'image_sha256':sha(image.read_bytes()), 'image_bytes':SIZE,
        'raw_image_bytes':raw_size, 'header_version':4, 'kernel_sha256':KERNEL_SHA,
        'ramdisk_compression':'gzip-9', 'ramdisk_bytes':len(compressed),
        'ramdisk_cpio_sha256':sha(packed), 'source_v3_cpio_sha256':CPIO_SHA,
        'external_cache_lower_sha256':LOWER_SHA,
        'external_cache_lower_bytes':len(byname['native/rootfs.tar.xz'].payload),
        'changed_records':{n:sha(v) for n,v in replacements.items()},
        'removed_records':['native/rootfs.tar.xz'], 'module_files':modules,
        'preserves_complete_v3_module_tree':True,
        'preserves_recovery_partition':True, 'vendor_boot_changes':False,
        'hardware_acceptance':'UNTESTED',
    }
    (out/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print(json.dumps({k:v for k,v in manifest.items() if k!='module_files'},indent=2))


if __name__ == '__main__':
    main()
