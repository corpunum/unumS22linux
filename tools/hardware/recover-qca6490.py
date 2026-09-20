#!/usr/bin/env python3
"""Recover only manifest QCA6490 files from the reconstructed compressed F2FS image."""
from pathlib import Path
import hashlib, os, re, struct, sys
import lz4.block

ROOT = Path(__file__).resolve().parents[2]
IMG = Path(os.environ.get("WIFI_SOURCE_IMAGE", ROOT / "rootfs/vendor-pristine-20260920.img"))
MAP = Path("/tmp/blockmap.txt")
OUT = ROOT / "rootfs/wifi-vendor-assets/vendor/firmware/qca6490"
FILES = {
    "amss20.bin": 0x461, "bdwlan.elf": 0x462, "bdwlan.elf1": 0x463,
    "bdwlan.elf10": 0x464, "bdwlan.elf2": 0x465, "bdwlang.elf": 0x466,
    "bdwlang.elf1": 0x467, "bdwlang.elf10": 0x468, "bdwlang.elf2": 0x469,
    "m3.bin": 0x46A, "regdb.bin": 0x46B,
}

def slots_for(name):
    for line in MAP.read_text().splitlines():
        if line.startswith("/firmware/qca6490/" + name + " "):
            slots = []
            for tok in line.split()[1:]:
                if tok == "0": slots.append(0)
                elif re.fullmatch(r"\d+(?:-\d+)?", tok):
                    a = tok.split("-"); slots.extend(range(int(a[0]), int(a[-1]) + 1))
            return slots
    raise RuntimeError(f"no block map entry for {name}")

def inode_size(ino):
    # dump.f2fs is metadata-only; its output is not trusted for file bytes.
    import subprocess, tempfile
    with tempfile.TemporaryDirectory() as td:
        d = Path(td); (d / "dump.f2fs").symlink_to("/tmp/f2fs-tools.pTclc2/root/sbin/fsck.f2fs")
        p = subprocess.run([str(d / "dump.f2fs"), "-i", hex(ino), str(IMG)], cwd=d,
                           input=b"N\n", stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=True)
    m = re.search(rb"i_size\s+\[0x\s*[0-9a-f]+\s*:\s*(\d+)\]", p.stdout)
    if not m: raise RuntimeError(f"no inode size for {ino:x}")
    return int(m.group(1))

def recover(slots, size):
    out = bytearray(); block = 4096
    with IMG.open("rb") as f:
        for i in range(0, len(slots), 4):
            group = slots[i:i+4]; nz = [x for x in group if x]
            if not nz:
                out.extend(b"\0" * (4 * block)); continue
            if group[0] == 0:
                payload = bytearray()
                for j, addr in enumerate(nz):
                    f.seek(addr * block); raw = f.read(block)
                    payload.extend(raw[24:] if j == 0 else raw)
                f.seek(nz[0] * block); compressed_len = struct.unpack("<I", f.read(4))[0]
                out.extend(lz4.block.decompress(bytes(payload[:compressed_len]), uncompressed_size=4 * block))
            else:
                for addr in nz:
                    f.seek(addr * block); out.extend(f.read(block))
    return bytes(out[:size])

def main():
    if os.geteuid() == 0 or IMG.stat().st_mode & 0o222:
        raise SystemExit("use an unprivileged account and a read-only image copy")
    if any((OUT / name).exists() for name in FILES) or (ROOT / "rootfs/wifi-vendor-assets/manifest.sha256").exists():
        raise SystemExit("refusing to overwrite existing recovered artifacts or manifest")
    h = hashlib.sha256()
    with IMG.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""): h.update(chunk)
    source_hash = h.hexdigest()
    expected = os.environ.get("WIFI_EXPECT_IMAGE_SHA256", "6dfe677119792e95a37b016092c327ad62bc0dc7759851424ad05e6bcb31b206")
    if source_hash != expected:
        raise SystemExit(f"source image hash mismatch: {source_hash}")
    print(f"source_image_sha256={source_hash}")
    OUT.mkdir(parents=True, exist_ok=True); manifest = []
    for name, ino in FILES.items():
        slots = slots_for(name); size = inode_size(ino)
        data = recover(slots, size)
        if len(data) != size or not any(data): raise RuntimeError(f"invalid recovered {name}")
        dst = OUT / name; dst.write_bytes(data)
        digest = hashlib.sha256(data).hexdigest(); manifest.append(f"{digest}  vendor/firmware/qca6490/{name}  inode=0x{ino:x} size={size} blocks={len(slots)}")
        print(manifest[-1])
    (ROOT / "rootfs/wifi-vendor-assets/manifest.sha256").write_text("\n".join(manifest) + "\n")

if __name__ == "__main__": main()
