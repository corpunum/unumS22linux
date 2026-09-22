#!/usr/bin/env python3
"""Build and verify a host-only ABOX-extra recovery candidate.

This deliberately creates a new output directory and adds only the 20
recovered ABOX extra-firmware files listed by the generated closure manifest.
It never reads a phone or a stock directory and never overwrites an output.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
BASE_CPIO = ROOT / "builds/audio-early-20260922/ramdisk.cpio"
BASE_IMAGE = ROOT / "builds/audio-early-20260922/recovery.img"
MANIFEST = ROOT / "rootfs/main-driver-loop-20260921/audio-extra-firmware-manifest.json"
ASSET_ROOT = ROOT / "rootfs/abox-audio-vendor-assets/vendor/firmware"
REF = ROOT / "lineage/build-20260915/unpacked"
OUT = ROOT / "builds/audio-extra-v2-20260922"
PARTITION_SIZE = 100663296
BASE_CPIO_SHA256 = "229263c61029b0ca8e3a9ff7b942649f180e5fbac5222409284e9056743cd87c"
BASE_IMAGE_SHA256 = "1c1b77a5e532e50b8274cfc68921aa9b1bfe6d4ae9a3459281be0cc033c5c3d5"
REF_HASHES = {
    "kernel": "708474da33de9afafcd1835e6f4cf6f9b6e9451ad37748e95d322678250b69b7",
    "dtb": "f5a00d80dd1800c934c6baed4ff4f5b7ac0bdc19acbc52a8e6092f03e5f7b2b8",
    "recovery_dtbo": "dd2acb7f8e02a58bba6b9ee9da09790286ab19c887ef7feabc8a8be10e0e8f98",
}
CORE_HASHES = {
    "vendor/firmware/calliope_sram.bin": (165120, "786ae058d4c439c01edf856abb52184a92e8d25e90b1b748e380186bed84796d"),
    "vendor/firmware/calliope_dram.bin": (2204800, "d2460b85f1e8ceabe6a23e103688932c03ca5aca6da0de5ed95ff78dfd8feccd"),
    "vendor/firmware/abox_tplg.bin": (1066664, "3690d21b6a7e91242ee423ea655604842969607c318769fe73fbc6db826eb9da"),
    "vendor/firmware/abox_tplg.conf": (109315, "ddc3874ec797a085c8497a240d47b995fedbcc5c876483c94e2f236ecb79f782"),
}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_cpio():
    source = ROOT / "tools/headless-recovery/build_native_handoff.py"
    spec = importlib.util.spec_from_file_location("audio_extra_cpio", source)
    if not spec or not spec.loader:
        raise RuntimeError("cannot load approved CPIO parser")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_unpacker():
    source = ROOT / "tools/mkbootimg/unpack_bootimg.py"
    spec = importlib.util.spec_from_file_location("audio_extra_unpacker", source)
    if not spec or not spec.loader:
        raise RuntimeError("cannot load boot unpacker")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def run(*args: str) -> None:
    subprocess.run(list(args), check=True)


def verify_inputs() -> tuple[object, list[tuple[str, bytes, str]]]:
    if OUT.exists():
        raise SystemExit(f"refusing existing output directory: {OUT}")
    base = BASE_CPIO.read_bytes()
    if sha(base) != BASE_CPIO_SHA256:
        raise SystemExit("base CPIO hash mismatch")
    if sha(BASE_IMAGE.read_bytes()) != BASE_IMAGE_SHA256:
        raise SystemExit("baseline image hash mismatch")
    manifest = json.loads(MANIFEST.read_text())
    assets = [x for x in manifest["assets"] if x["status"] == "recovered"]
    missing = [x["filename"] for x in manifest["assets"] if x["status"] != "recovered"]
    if len(assets) != 20 or set(missing) != {"APBiBF_AUDIO_SLSI.bin", "atune.bin"}:
        raise SystemExit(f"closure manifest is not exactly 20 recovered extras: {missing}")
    selected = []
    for item in assets:
        matches = [x for x in item["recovered"] if x["root"] == "abox-audio-vendor-assets"]
        if len(matches) != 1:
            raise SystemExit(f"expected one canonical recovered input: {item['filename']}")
        path = ROOT / matches[0]["location"]
        if path != ASSET_ROOT / item["filename"] or path.is_symlink() or not path.is_file():
            raise SystemExit(f"unsafe or unexpected input: {path}")
        data = path.read_bytes()
        if sha(data) != matches[0]["sha256"] or sha(data) != item["recovered"][0]["sha256"]:
            raise SystemExit(f"input hash mismatch: {path}")
        selected.append((item["filename"], data, sha(data)))
    records = load_cpio().parse_cpio(base)
    names = [record.name for record in records]
    if names[-1] != "TRAILER!!!" or len(names) != len(set(names)):
        raise SystemExit("invalid base CPIO record set")
    for name, (size, expected) in CORE_HASHES.items():
        matches = [record for record in records if record.name == name]
        if len(matches) != 1 or len(matches[0].payload) != size or sha(matches[0].payload) != expected:
            raise SystemExit(f"current audio-early core payload mismatch: {name}")
    return records, selected


def add_assets(cpio, records, selected):
    existing = {record.name for record in records}
    targets = ["vendor/firmware/" + name for name, _, _ in selected]
    if existing.intersection(targets):
        raise SystemExit("base CPIO already contains an extra-firmware target")
    next_ino = max(record.fields[0] for record in records if record.name != "TRAILER!!!") + 1
    output = bytearray()
    for record in records:
        if record.name != "TRAILER!!!":
            output += record.raw
            continue
        for target, (_, data, _) in zip(targets, selected):
            output += cpio.make_record(target, data, mode=0o100644, ino=next_ino)
            next_ino += 1
        output += record.raw
    return bytes(output), targets


def verify_image(cpio, base_records, new_cpio, targets, image, work):
    unpack = load_unpacker()
    base_dir, candidate_dir = work / "base-unpacked", work / "candidate-unpacked"
    base_info = unpack.unpack_boot_image(open(BASE_IMAGE, "rb"), str(base_dir))
    candidate_info = unpack.unpack_boot_image(open(image, "rb"), str(candidate_dir))
    fields = ("boot_magic", "header_version", "kernel_size", "kernel_load_address",
              "ramdisk_load_address", "tags_load_address", "page_size", "os_version",
              "os_patch_level", "cmdline", "recovery_dtbo_size", "dtb_size",
              "dtb_load_address", "recovery_dtbo_offset", "boot_header_size")
    # The larger ramdisk intentionally shifts recovery_dtbo_offset; all other
    # decoded header fields must remain identical. The offset is checked by
    # the unpacked recovery_dtbo byte identity below.
    required_fields = {field for field in fields}
    for field in required_fields:
        if field == "recovery_dtbo_offset":
            continue
        if not hasattr(base_info, field) or not hasattr(candidate_info, field):
            raise SystemExit(f"boot unpacker omitted required header field: {field}")
        if getattr(base_info, field, None) != getattr(candidate_info, field, None):
            raise SystemExit(f"boot header mismatch: {field}")
    if candidate_info.ramdisk_size == base_info.ramdisk_size:
        raise SystemExit("candidate ramdisk size did not change")
    for name in ("kernel", "dtb", "recovery_dtbo"):
        if sha((candidate_dir / name).read_bytes()) != sha((base_dir / name).read_bytes()):
            raise SystemExit(f"candidate changed {name}")
        if sha((candidate_dir / name).read_bytes()) != REF_HASHES[name]:
            raise SystemExit(f"candidate {name} differs from pinned reference")
    run("lz4", "-d", str(candidate_dir / "ramdisk"), str(work / "candidate.cpio"))
    extracted = (work / "candidate.cpio").read_bytes()
    if extracted != new_cpio:
        raise SystemExit("unpacked candidate CPIO differs from built CPIO")
    final_records = cpio.parse_cpio(extracted)
    filtered = [record.raw for record in final_records if record.name not in targets]
    original = [record.raw for record in base_records]
    if filtered != original:
        raise SystemExit("original CPIO records are not byte-identical")
    if {record.name for record in final_records if record.name in targets} != set(targets):
        raise SystemExit("candidate additions do not match exactly")
    core_payloads = {}
    for name, (size, expected) in CORE_HASHES.items():
        matches = [record for record in final_records if record.name == name]
        if len(matches) != 1 or len(matches[0].payload) != size or sha(matches[0].payload) != expected:
            raise SystemExit(f"candidate core payload mismatch: {name}")
        core_payloads[name] = {"bytes": size, "sha256": expected}
    return {
        "base_header": {field: getattr(base_info, field, None) for field in fields},
        "candidate_header": {field: getattr(candidate_info, field, None) for field in fields},
        "kernel_sha256": sha((candidate_dir / "kernel").read_bytes()),
        "dtb_sha256": sha((candidate_dir / "dtb").read_bytes()),
        "recovery_dtbo_sha256": sha((candidate_dir / "recovery_dtbo").read_bytes()),
        "candidate_ramdisk_bytes": candidate_info.ramdisk_size,
        "core_payloads": core_payloads,
    }


def main() -> int:
    cpio = load_cpio()
    base_records, selected = verify_inputs()
    base = BASE_CPIO.read_bytes()
    new_cpio, targets = add_assets(cpio, base_records, selected)
    if len(new_cpio) <= len(base):
        raise SystemExit("candidate CPIO did not grow")
    total = sum(len(data) for _, data, _ in selected)
    OUT.mkdir()
    try:
        with tempfile.TemporaryDirectory(prefix="audio-extra-build-") as td:
            work = Path(td)
            cpio_path, lz4_path, image = work / "ramdisk.cpio", work / "ramdisk.lz4", work / "recovery.img"
            cpio_path.write_bytes(new_cpio)
            run("lz4", "-l", "-12", str(cpio_path), str(lz4_path))
            run(sys.executable, str(ROOT / "tools/mkbootimg/mkbootimg.py"),
                "--kernel", str(REF / "kernel"), "--ramdisk", str(lz4_path),
                "--dtb", str(REF / "dtb"), "--recovery_dtbo", str(REF / "recovery_dtbo"),
                "--output", str(image), "--base", "0x0", "--kernel_offset", "0x10008000",
                "--ramdisk_offset", "0x11000000", "--tags_offset", "0x10000100",
                "--dtb_offset", "0x11f00000", "--pagesize", "2048", "--header_version", "2",
                "--os_version", "16.0.0", "--os_patch_level", "2026-09", "--cmdline", " bootconfig")
            run(sys.executable, str(ROOT / "tools/avb/avbtool.py"), "add_hash_footer",
                "--image", str(image), "--partition_size", str(PARTITION_SIZE),
                "--partition_name", "recovery", "--algorithm", "NONE", "--rollback_index", "0",
                "--salt", "708474da33de9afafcd1835e6f4cf6f9b6e9451ad37748e95d322678250b69b7",
                "--prop", "com.android.build.recovery.fingerprint:samsung/lineage_r0s/r0s:16/BP4A.251205.006/4a67c928b4:userdebug/release-keys")
            if image.stat().st_size != PARTITION_SIZE:
                raise SystemExit("candidate exceeds or differs from 100663296-byte partition")
            validation = verify_image(cpio, base_records, new_cpio, targets, image, work)
            shutil.copyfile(image, OUT / "recovery.img")
            shutil.copyfile(cpio_path, OUT / "ramdisk.cpio")
            shutil.copyfile(lz4_path, OUT / "ramdisk.lz4")
        report = {
            "image": str((OUT / "recovery.img").relative_to(ROOT)),
            "image_sha256": sha((OUT / "recovery.img").read_bytes()),
            "base_cpio_sha256": sha(base),
            "base_image_sha256": sha(BASE_IMAGE.read_bytes()),
            "new_cpio_sha256": sha(new_cpio),
            "extra_count": len(selected),
            "extra_bytes_total": total,
            "extras": [{"filename": n, "bytes": len(d), "sha256": h, "cpio_path": t}
                       for (n, d, h), t in zip(selected, targets)],
            "validation": validation,
            "phone_access": False,
        }
        (OUT / "manifest.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        print(json.dumps(report, indent=2, sort_keys=True))
    except Exception:
        # Preserve failed artifacts/receipts for diagnosis; never erase output.
        raise
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
