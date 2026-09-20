#!/usr/bin/env python3
"""Derive the BOOT cache-root native-start from the pinned persistent source."""
from __future__ import annotations

import hashlib
from pathlib import Path

SOURCE = Path("tools/native-handoff/native-start-persistent")
OUTPUT = Path("builds/native_boot_start_persistent")
SOURCE_SHA256 = "2bf666a0fdea56e23793f0acfb06cd6056008accf1c18b4440918a87b5337b01"
ROOTFS_SHA256 = "1139f04222e577f18dc2d540721c0be037700952780d54cfb919e39c5d15ac1b"

old = '''prepare_lower() {
    ensure_dir /native-lower
    if mounted /native-lower; then return 0; fi
    "$BB" mount -t tmpfs -o mode=0755,size=1G native-lower /native-lower || \\
        die "RAM lower mount failed"
    [ -f /native/rootfs.tar.xz ] || die "embedded xz rootfs not found"
    /native/lib/ld-musl-aarch64.so.1 /native/bin/busybox unxz -c \\
        /native/rootfs.tar.xz | "$BB" tar -xf - -C /native-lower || \\
        die "xz rootfs extraction failed"
}
'''

new = f'''prepare_lower() {{
    ensure_dir /native-lower
    if mounted /native-lower; then return 0; fi
    prepare_cache_workspace || die "BOOT cache workspace unavailable"
    archive=/cache/s22-linux/lower-rootfs.tar.xz
    [ ! -L "$archive" ] || die "BOOT cache lower rootfs is a symlink"
    [ -f "$archive" ] || die "BOOT cache lower rootfs is absent"
    expected={ROOTFS_SHA256}
    actual=$(${{BB}} sha256sum "$archive" | cut -d' ' -f1)
    [ "$actual" = "$expected" ] || die "BOOT lower rootfs hash mismatch: $actual"
    "$BB" mount -t tmpfs -o mode=0755,size=1G native-lower /native-lower || \\
        die "RAM lower mount failed"
    /native/lib/ld-musl-aarch64.so.1 /native/bin/busybox unxz -c \\
        "$archive" | "$BB" tar -xf - -C /native-lower || \\
        die "cached xz rootfs extraction failed"
    log "BOOT cache lower rootfs verified sha256=$actual"
}}
'''

data = SOURCE.read_bytes()
actual = hashlib.sha256(data).hexdigest()
if actual != SOURCE_SHA256:
    raise SystemExit(f"source hash mismatch: {actual} != {SOURCE_SHA256}")
text = data.decode()
if text.count(old) != 1:
    raise SystemExit(f"expected one prepare_lower source block, found {text.count(old)}")
if text.count("prepare_lower\nprepare_root") != 1:
    raise SystemExit("expected one main prepare_lower -> prepare_root call")
text = text.replace(old, new)
OUTPUT.parent.mkdir(parents=True, exist_ok=True)
OUTPUT.write_text(text)
OUTPUT.chmod(0o755)
print(f"generated {OUTPUT} source_sha256={actual} rootfs_sha256={ROOTFS_SHA256}")
