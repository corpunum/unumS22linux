#!/usr/bin/env python3
"""Build a recovery candidate with only the four ABOX files added to V3 CPIO.

Host-only.  The known-good V3 CPIO and the pinned Lineage kernel/DTB/DTBO are
inputs; no phone, partition, image mount, or stock-directory archive is used.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "builds/audio-early-20260922"
BASE = ROOT / "builds/native_handoff_v3_ramdisk.cpio"
REF = ROOT / "lineage/build-20260915/unpacked"
PARTITION_SIZE = 100663296
ASSETS = {
    "calliope_sram.bin": (165120, "786ae058d4c439c01edf856abb52184a92e8d25e90b1b748e380186bed84796d"),
    "calliope_dram.bin": (2204800, "d2460b85f1e8ceabe6a23e103688932c03ca5aca6da0de5ed95ff78dfd8feccd"),
    "abox_tplg.bin": (1066664, "3690d21b6a7e91242ee423ea655604842969607c318769fe73fbc6db826eb9da"),
    "abox_tplg.conf": (109315, "ddc3874ec797a085c8497a240d47b995fedbcc5c876483c94e2f236ecb79f782"),
}

def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def load_cpio():
    path = ROOT / "tools/headless-recovery/build_native_handoff.py"
    spec = importlib.util.spec_from_file_location("native_handoff_cpio", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module

def main() -> int:
    cpio = load_cpio()
    base = BASE.read_bytes()
    base_sha = sha(base)
    expected_base = "abe6b1f47f9cb6ad8df2d82eda93cc0985fb8058ee67c29cc2e4d8aae8a8b84b"
    if base_sha != expected_base:
        raise SystemExit(f"unexpected V3 CPIO hash: {base_sha}")
    records = cpio.parse_cpio(base)
    names = {record.name for record in records}
    if len(names) != len(records) or "TRAILER!!!" not in names:
        raise SystemExit("invalid V3 CPIO record set")
    added = []
    for name, (size, expected) in ASSETS.items():
        path = ROOT / "rootfs/bt-audio-vendor-assets/vendor/firmware" / name
        data = path.read_bytes()
        if path.is_symlink() or len(data) != size or sha(data) != expected:
            raise SystemExit(f"asset mismatch: {path}")
        target = "vendor/firmware/" + name
        if target in names:
            raise SystemExit(f"base already contains target: {target}")
        added.append((target, data, size, expected))
    max_ino = max(r.fields[0] for r in records if r.name != "TRAILER!!!")
    next_ino = max_ino + 1
    out = bytearray()
    for record in records:
        if record.name != "TRAILER!!!":
            out += record.raw
            continue
        for target, data, _, _ in added:
            out += cpio.make_record(target, data, mode=0o100644, ino=next_ino)
            next_ino += 1
        out += record.raw
    new_cpio = bytes(out)
    OUT.mkdir(parents=True, exist_ok=True)
    if any(OUT.iterdir()):
        raise SystemExit(f"refusing non-empty output directory: {OUT}")
    with tempfile.TemporaryDirectory(prefix="audio-early-") as td:
        work = Path(td)
        cpio_path, lz4_path, image = work / "ramdisk.cpio", work / "ramdisk.lz4", work / "recovery.img"
        cpio_path.write_bytes(new_cpio)
        subprocess.run(["lz4", "-l", "-12", str(cpio_path), str(lz4_path)], check=True)
        subprocess.run([sys.executable, str(ROOT / "tools/mkbootimg/mkbootimg.py"),
            "--kernel", str(REF / "kernel"), "--ramdisk", str(lz4_path), "--dtb", str(REF / "dtb"),
            "--recovery_dtbo", str(REF / "recovery_dtbo"), "--output", str(image), "--base", "0x0",
            "--kernel_offset", "0x10008000", "--ramdisk_offset", "0x11000000", "--tags_offset", "0x10000100",
            "--dtb_offset", "0x11f00000", "--pagesize", "2048", "--header_version", "2",
            "--os_version", "16.0.0", "--os_patch_level", "2026-09", "--cmdline", " bootconfig"], check=True)
        subprocess.run([sys.executable, str(ROOT / "tools/avb/avbtool.py"), "add_hash_footer",
            "--image", str(image), "--partition_size", str(PARTITION_SIZE), "--partition_name", "recovery",
            "--algorithm", "NONE", "--rollback_index", "0", "--salt", "708474da33de9afafcd1835e6f4cf6f9b6e9451ad37748e95d322678250b69b7",
            "--prop", "com.android.build.recovery.fingerprint:samsung/lineage_r0s/r0s:16/BP4A.251205.006/4a67c928b4:userdebug/release-keys"], check=True)
        if image.stat().st_size != PARTITION_SIZE:
            raise SystemExit("candidate image is not recovery partition size")
        (OUT / "recovery.img").write_bytes(image.read_bytes())
        (OUT / "ramdisk.cpio").write_bytes(new_cpio)
        (OUT / "ramdisk.lz4").write_bytes(lz4_path.read_bytes())
    manifest = {
        "image": "builds/audio-early-20260922/recovery.img",
        "image_sha256": sha((OUT / "recovery.img").read_bytes()),
        "baseline_image": "builds/native_handoff_v3.img",
        "baseline_image_sha256": sha((ROOT / "builds/native_handoff_v3.img").read_bytes()),
        "base_v3_cpio_sha256": base_sha,
        "ramdisk_cpio_sha256": sha(new_cpio),
        "ramdisk_lz4_sha256": sha((OUT / "ramdisk.lz4").read_bytes()),
        "kernel_sha256": sha((REF / "kernel").read_bytes()),
        "dtb_sha256": sha((REF / "dtb").read_bytes()),
        "recovery_dtbo_sha256": sha((REF / "recovery_dtbo").read_bytes()),
        "preserved_cpio_records": {
            n: sha(next(r.payload for r in cpio.parse_cpio(base) if r.name == n))
            for n in ("system/bin/init", "system/bin/native-guardian", "native/native-start")
        },
        "intentional_cpio_additions": {"vendor/firmware/" + n: {"bytes": s, "sha256": h} for n, (_, s, h) in ((n, (n, sz, h)) for n, (sz, h) in ASSETS.items())},
        "header": {"version": 2, "pagesize": 2048, "partition_size": PARTITION_SIZE, "avb_algorithm": "NONE"},
        "phone_access": False,
    }
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
