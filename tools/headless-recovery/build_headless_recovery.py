#!/usr/bin/env python3
"""Build a reproducible, key-authorized derivative of the verified recovery image.

The image is intentionally changed at two CPIO records only: the shipping
``/adb_keys`` symlink is replaced by a regular file containing this host's
public key, and recovery's init adds a restorecon for that file.  Kernel, DTB,
recovery DTBO, boot header, and all other ramdisk records are copied from the
verified Lineage build.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
REF = ROOT / "lineage/build-20260915"
TOOLS = ROOT / "tools"
PARTITION_SIZE = 100663296
RECOVERY_FINGERPRINT = (
    "samsung/lineage_r0s/r0s:16/BP4A.251205.006/4a67c928b4:"
    "userdebug/release-keys"
)
# Reusing the verified image's salt makes the AVB footer deterministic.
REF_SALT = "708474da33de9afafcd1835e6f4cf6f9b6e9451ad37748e95d322678250b69b7"


def align4(n: int) -> int:
    return (n + 3) & ~3


def cpio_with_changes(original: bytes, public_key: bytes) -> tuple[bytes, dict[str, object]]:
    """Replace the key and one recovery init line, retaining stream order."""
    init_line = b"    restorecon /adb_keys\n"
    init_old = b"on early-init\n    # Set the security context of /postinstall if present.\n    restorecon /postinstall\n"
    init_new = b"on early-init\n    # Set the security context of /adb_keys if present.\n    restorecon /adb_keys\n    # Set the security context of /postinstall if present.\n    restorecon /postinstall\n"
    out = bytearray()
    pos = 0
    seen: list[str] = []
    replaced = False
    init_replaced = False
    old_target: dict[str, object] | None = None
    while pos + 110 <= len(original):
        start = pos
        if original[pos : pos + 6] != b"070701":
            raise ValueError(f"invalid newc magic at offset {pos}")
        fields = [int(original[pos + 6 + i * 8 : pos + 14 + i * 8], 16) for i in range(13)]
        (ino, mode, uid, gid, nlink, mtime, filesize, devmajor, devminor,
         rdevmajor, rdevminor, namesize, check) = fields
        name_start = pos + 110
        name_end = name_start + namesize
        data_start = align4(name_end)
        data_end = data_start + filesize
        next_pos = align4(data_end)
        if name_end > len(original) or data_end > len(original):
            raise ValueError(f"truncated newc record at offset {pos}")
        raw_name = original[name_start:name_end]
        if not raw_name.endswith(b"\0"):
            raise ValueError(f"unterminated newc name at offset {pos}")
        name = raw_name[:-1].decode("utf-8", "surrogateescape")
        seen.append(name)
        if name == "TRAILER!!!":
            out += original[start:next_pos]
            break
        replacement: bytes | None = None
        replacement_mode = mode
        replacement_uid = uid
        replacement_gid = gid
        replacement_nlink = nlink
        if name == "adb_keys":
            if replaced:
                raise ValueError("duplicate adb_keys records")
            if not (mode & 0o170000) == 0o120000:
                raise ValueError(f"expected adb_keys symlink, mode={mode:o}")
            old_target = {
                "mode": mode,
                "uid": uid,
                "gid": gid,
                "mtime": mtime,
                "size": filesize,
                "target": original[data_start:data_end].decode("utf-8", "surrogateescape"),
                "offset": start,
            }
            # newc has no meaningful checksum field for this archive.  Keep
            # all identity fields except the type, mode, and payload size.
            replacement = public_key
            replacement_mode = 0o100644
            replacement_uid = 0
            replacement_gid = 0
            replacement_nlink = 1
            replaced = True
        elif name == "system/etc/init/hw/init.rc":
            old = original[data_start:data_end]
            if init_line in old or init_old not in old:
                raise ValueError("unexpected recovery init.rc content")
            replacement = old.replace(init_old, init_new, 1)
            init_replaced = True
        if replacement is None:
            out += original[start:next_pos]
        else:
            new_fields = [ino, replacement_mode, replacement_uid, replacement_gid,
                          replacement_nlink, mtime, len(replacement), devmajor,
                          devminor, rdevmajor, rdevminor, namesize, 0]
            header = b"070701" + b"".join(f"{v:08x}".encode("ascii") for v in new_fields)
            out += header + raw_name
            out += b"\0" * (data_start - (start + 110 + namesize))
            out += replacement
            out += b"\0" * (align4(data_start + len(replacement)) - (data_start + len(replacement)))
        pos = next_pos
    else:
        raise ValueError("newc archive had no trailer")
    if not replaced or not init_replaced:
        raise ValueError(f"required records missing: adb_keys={replaced} init={init_replaced}")
    assert old_target is not None
    return bytes(out), {
        "entries": len(seen), "old_adb_keys": old_target, "new_size": len(public_key),
        "init_change": "restorecon /adb_keys",
    }


def run(*args: str, cwd: Path | None = None) -> None:
    subprocess.run(list(args), cwd=cwd, check=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", type=Path, default=ROOT / "builds/headless_recovery_key.img")
    ap.add_argument("--public-key", type=Path, default=Path("/home/corpunum/.android/adbkey.pub"))
    args = ap.parse_args()
    public_key = args.public_key.read_bytes()
    if len(public_key) < 100 or b" " not in public_key:
        raise SystemExit("public key must be the host adbkey.pub text file")
    original_cpio = (REF / "unpacked/ramdisk.cpio").read_bytes()
    new_cpio, audit = cpio_with_changes(original_cpio, public_key)
    old = audit["old_adb_keys"]
    if audit["entries"] != 928 or not isinstance(old, dict) or (
        old["mode"], old["uid"], old["gid"], old["mtime"], old["size"], old["target"]
    ) != (0o120644, 0, 0, 0, 30, "/product/etc/security/adb_keys"):
        raise SystemExit(f"unexpected original adb_keys metadata: {old!r}")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="headless-recovery-") as td:
        work = Path(td)
        cpio = work / "ramdisk.cpio"
        lz4 = work / "ramdisk.lz4"
        raw = work / "recovery.raw.img"
        cpio.write_bytes(new_cpio)
        run("lz4", "-l", "-12", str(cpio), str(lz4))
        # --base 0 preserves the already-absolute load addresses from the
        # reference header.  Leading space in cmdline is part of that header.
        run(
            "python3", str(TOOLS / "mkbootimg/mkbootimg.py"),
            "--kernel", str(REF / "unpacked/kernel"),
            "--ramdisk", str(lz4),
            "--dtb", str(REF / "unpacked/dtb"),
            "--recovery_dtbo", str(REF / "unpacked/recovery_dtbo"),
            "--output", str(raw), "--base", "0x0",
            "--kernel_offset", "0x10008000", "--ramdisk_offset", "0x11000000",
            "--tags_offset", "0x10000100", "--dtb_offset", "0x11f00000",
            "--pagesize", "2048", "--header_version", "2",
            "--os_version", "16.0.0", "--os_patch_level", "2026-09",
            "--cmdline", " bootconfig",
        )
        run(
            "python3", str(TOOLS / "avb/avbtool.py"), "add_hash_footer",
            "--image", str(raw), "--partition_size", str(PARTITION_SIZE),
            "--partition_name", "recovery", "--algorithm", "NONE",
            "--rollback_index", "0", "--salt", REF_SALT,
            "--prop", f"com.android.build.recovery.fingerprint:{RECOVERY_FINGERPRINT}",
        )
        if raw.stat().st_size != PARTITION_SIZE:
            raise SystemExit(f"unexpected output size {raw.stat().st_size}")
        tmp_output = args.output.with_suffix(args.output.suffix + ".tmp")
        shutil.copyfile(raw, tmp_output)
        os.replace(tmp_output, args.output)

        # Keep the exact transformed CPIO/LZ4 artifacts beside the image for
        # byte-level audit, under the explicitly owned builds namespace.
        shutil.copyfile(cpio, args.output.with_name(args.output.stem + "_ramdisk.cpio"))
        shutil.copyfile(lz4, args.output.with_name(args.output.stem + "_ramdisk.lz4"))
    print(f"output={args.output}")
    print(f"public_key_sha256={__import__('hashlib').sha256(public_key).hexdigest()}")
    print(f"original_cpio_sha256={__import__('hashlib').sha256(original_cpio).hexdigest()}")
    print(f"new_cpio_sha256={__import__('hashlib').sha256(new_cpio).hexdigest()}")
    print(f"entries={audit['entries']} old={audit['old_adb_keys']} new_size={audit['new_size']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
