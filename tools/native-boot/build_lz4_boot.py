#!/usr/bin/env python3
"""Build a host-only pure-LZ4 BOOT candidate from the pinned V2 CPIO."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "builds/native-boot-v2/ramdisk.cpio"
SOURCE_SHA = "661340d6e98c1fa4c377aac32ede21916c4f40885bbcda87a382531040aae7a2"
KERNEL = ROOT / "lineage/build-20260915/unpacked/kernel"
OUT = ROOT / "builds/native-boot-v3-lz4"
SIZE = 67108864
KERNEL_SHA = "708474da33de9afafcd1835e6f4cf6f9b6e9451ad37748e95d322678250b69b7"

def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def load_cpio():
    p = ROOT / "tools/headless-recovery/build_native_handoff.py"
    spec = importlib.util.spec_from_file_location("native_handoff_cpio", p)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod

def run(*args: object, cwd: Path = ROOT) -> None:
    subprocess.run([str(x) for x in args], cwd=cwd, check=True)

def main() -> None:
    cpio = load_cpio()
    original = SOURCE.read_bytes()
    assert sha(original) == SOURCE_SHA, "source CPIO hash mismatch"
    assert sha(KERNEL.read_bytes()) == KERNEL_SHA
    records = cpio.parse_cpio(original)
    byname = {r.name: r for r in records}
    assert len(byname) == len(records)

    with tempfile.TemporaryDirectory(prefix="native-lz4-elf-") as td:
        root = Path(td)
        for r in records:
            if r.name == "TRAILER!!!":
                continue
            p = root / r.name
            mode = r.fields[1] & 0o170000
            assert not Path(r.name).is_absolute() and '..' not in Path(r.name).parts
            # Never materialize absolute archive symlinks on the host.
            if mode == 0o100000 and r.payload.startswith(b'\x7fELF'):
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(r.payload)
        all_paths = {p.name: p for p in root.rglob("*") if p.is_file()}
        closure: set[Path] = set()
        queue = [root / x for x in (
            "system/bin/init.android", "system/bin/linker64",
            "system/bin/toybox", "system/bin/sh")]
        deps: dict[str, list[str]] = {}
        while queue:
            path = queue.pop().resolve()
            assert path.is_relative_to(root) and path.is_file(), path
            if path in closure:
                continue
            closure.add(path)
            out = subprocess.run(["readelf", "-d", str(path)], text=True,
                                  stdout=subprocess.PIPE, check=True).stdout
            needed = []
            for line in out.splitlines():
                if "Shared library:" in line:
                    name = line.split("[", 1)[1].split("]", 1)[0]
                    needed.append(name)
                    if name not in all_paths:
                        raise RuntimeError(f"unresolved DT_NEEDED {name} from {path.relative_to(root)}")
                    queue.append(all_paths[name])
            deps[str(path.relative_to(root))] = needed
        closure_names = {str(p.relative_to(root)) for p in closure}

    kept = []
    removed = []
    for r in records:
        if r.name == "TRAILER!!!":
            continue
        mode = r.fields[1] & 0o170000
        is_system_elf = mode == 0o100000 and r.name.startswith("system/") and r.payload[:4] == b"\x7fELF"
        mandatory = r.name in {"system/bin/init", "system/bin/init.android",
                               "system/bin/native-guardian", "system/bin/recovery"}
        if r.name == "system/bin/recovery":
            kept.append(cpio.replace_record(r.name, b"", r.fields))
        elif is_system_elf and r.name not in closure_names and not mandatory:
            removed.append(r.name)
        else:
            kept.append(r.raw)

    # Remove only symlinks that point at an ELF record removed above.
    removed_set = set(removed)
    final_names = {r.name for r in records if r.name not in removed_set}
    cleaned = []
    dangling = []
    for raw, r in zip(kept, [x for x in records if x.name not in removed_set]):
        mode = r.fields[1] & 0o170000
        if mode == 0o120000:
            target = r.payload.decode("utf-8", "replace")
            resolved = (Path(r.name).parent / target).as_posix() if not target.startswith("/") else target[1:]
            if resolved not in final_names and r.name.startswith("system/"):
                dangling.append(r.name)
                continue
        cleaned.append(raw)
    trailer = next(r.raw for r in records if r.name == "TRAILER!!!")
    packed = b"".join(cleaned) + trailer
    final = {r.name:r for r in cpio.parse_cpio(packed)}
    module_names = [n for n in byname if n.startswith('lib/modules/')]
    assert sum(n.endswith('.ko') for n in module_names) == 324
    assert all(final[n].raw == byname[n].raw for n in module_names)
    assert final['init'].raw == byname['init'].raw
    assert final['system/bin/recovery'].payload == b''
    assert final['system/bin/recovery'].fields[1] & 0o170000 == 0o100000
    outdir = OUT
    outdir.mkdir(parents=True, exist_ok=False)
    cpio_path = outdir / "ramdisk.cpio"
    cpio_path.write_bytes(packed)
    lz4_path = outdir / "ramdisk.lz4"
    run("lz4", "-l", "-12", "-f", cpio_path, lz4_path)
    assert lz4_path.read_bytes()[:4] == bytes.fromhex('02214c18')
    decoded = outdir / "ramdisk.decoded.cpio"
    run("lz4", "-d", "-f", lz4_path, decoded)
    assert decoded.read_bytes() == packed

    image = outdir / "boot.img"
    run(sys.executable, ROOT / "tools/mkbootimg/mkbootimg.py", "--kernel", KERNEL,
        "--ramdisk", lz4_path, "--header_version", "4", "--os_version", "16.0.0",
        "--os_patch_level", "2026-09", "--cmdline", " bootconfig", "--output", image)
    raw_bytes = image.stat().st_size
    assert raw_bytes < SIZE - 69632
    raw = image.read_bytes()
    assert raw[:8] == b'ANDROID!'
    assert struct.unpack_from('<I',raw,40)[0] == 4
    assert struct.unpack_from('<I',raw,20)[0] == 1584
    assert struct.unpack_from('<I',raw,1580)[0] == 0
    kernel_bytes = KERNEL.read_bytes()
    assert raw[4096:4096+len(kernel_bytes)] == kernel_bytes
    rstart=4096+((len(kernel_bytes)+4095)//4096)*4096
    assert raw[rstart:rstart+lz4_path.stat().st_size] == lz4_path.read_bytes()
    run(sys.executable, ROOT / "tools/avb/avbtool.py", "add_hash_footer", "--image", image,
        "--partition_size", SIZE, "--partition_name", "boot", "--algorithm", "NONE",
        "--rollback_index", "0", "--salt", sha(KERNEL.read_bytes()),
        "--prop", "com.android.build.boot.os_version:16",
        "--prop", "com.android.build.boot.fingerprint:samsung/lineage_r0s/r0s:16/BP4A.251205.006/4a67c928b4:userdebug/release-keys")
    run(sys.executable, ROOT / "tools/avb/avbtool.py", "verify_image", "--image", image)
    assert image.stat().st_size == SIZE

    assert sha(KERNEL.read_bytes()) == KERNEL_SHA
    stock_parts = [ROOT / "builds/boot-candidate-analysis/backup_vendor_boot/vendor_ramdisk00",
                   ROOT / "builds/boot-candidate-analysis/backup_vendor_boot/vendor_ramdisk01"]
    for part in stock_parts:
        assert part.is_file()
    assert [sha(p.read_bytes()) for p in stock_parts] == [
        '2ee3688c2c98274af35b9c9cc4ffa0bc9ca1878370049502f73af14eab3d41d5',
        '3faacc0eec09b38d0ad0532028dcf472363d2551daa58d9afd2b2e2ccc55d083']
    merged = outdir / "stock-plus-new.lz4"
    merged.write_bytes(b"".join(p.read_bytes() for p in stock_parts) + lz4_path.read_bytes())
    merged_decoded = outdir / "stock-plus-new.decoded"
    merged_data = subprocess.run(["lz4", "-d", "-m", "-c", merged],
                                 stdout=subprocess.PIPE, check=True).stdout
    merged_decoded.write_bytes(merged_data)
    stock_data = b"".join(subprocess.run(["lz4", "-d", "-c", p],
                                         stdout=subprocess.PIPE, check=True).stdout
                            for p in stock_parts)
    assert merged_data == stock_data + packed
    manifest = {
        "source_cpio_sha256": SOURCE_SHA, "source_cpio_bytes": len(original),
        "output_cpio_sha256": sha(packed), "output_cpio_bytes": len(packed),
        "lz4_sha256": sha(lz4_path.read_bytes()), "lz4_bytes": lz4_path.stat().st_size,
        "image_sha256": sha(image.read_bytes()), "image_bytes": image.stat().st_size,
        "raw_image_bytes": raw_bytes, "kernel_sha256": sha(KERNEL.read_bytes()),
        "elf_closure_count": len(closure_names), "elf_closure": sorted(closure_names),
        "removed_system_elf_count": len(removed), "removed_system_elf": sorted(removed),
        "removed_dangling_symlinks": sorted(dangling),
        "recovery_marker": "system/bin/recovery zero-byte regular record",
        "modules_preserved": sum(r.name.startswith("lib/modules/") for r in records),
        "stock_lz4_sha256": [sha(p.read_bytes()) for p in stock_parts],
        "full_concatenated_decode_sha256":sha(merged_data),
        "dependencies":deps,
        "kernel_sha256_asserted": KERNEL_SHA, "hardware_acceptance": "UNTESTED - HOST ONLY",
    }
    (outdir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest, indent=2))

if __name__ == "__main__":
    main()
