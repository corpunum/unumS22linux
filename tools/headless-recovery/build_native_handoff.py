#!/usr/bin/env python3
"""Build the native-handoff derivative of the verified recovery image.

The input is the already verified, host-key-authorized recovery CPIO.  Its
records are copied byte-for-byte except for the top-level ``init`` record,
which becomes a regular, byte-identical copy of the shipping init binary,
and the ``system/bin/init`` record, which becomes the static wrapper.  The
original first-stage init is retained as ``system/bin/init.android`` and the
additional native handoff files are appended before the CPIO trailer.

The Alpine root is kept as an xz archive.  It is not unpacked into the CPIO:
the recovery ramdisk has only about 96 MiB of partition space and the xz
archive is the only form that fits alongside the known-good recovery payload.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
REF = ROOT / "lineage/build-20260915"
TOOLS = ROOT / "tools"
PARTITION_SIZE = 100663296
REF_SALT = "708474da33de9afafcd1835e6f4cf6f9b6e9451ad37748e95d322678250b69b7"
RECOVERY_FINGERPRINT = (
    "samsung/lineage_r0s/r0s:16/BP4A.251205.006/4a67c928b4:"
    "userdebug/release-keys"
)
BASE_CPIO_SHA256 = "dfbedf50dad75a4bda8b8627d4a6e9ac4e8e1bb1c7c3bc83902931aceef13d0b"


def align4(n: int) -> int:
    return (n + 3) & ~3


@dataclass(frozen=True)
class Record:
    name: str
    fields: tuple[int, ...]
    payload: bytes
    raw: bytes


def parse_cpio(data: bytes) -> list[Record]:
    records: list[Record] = []
    pos = 0
    while pos + 110 <= len(data):
        start = pos
        if data[pos : pos + 6] != b"070701":
            raise ValueError(f"invalid newc magic at offset {pos}")
        fields = tuple(int(data[pos + 6 + i * 8 : pos + 14 + i * 8], 16) for i in range(13))
        namesize = fields[11]
        filesize = fields[6]
        name_start = pos + 110
        name_end = name_start + namesize
        data_start = align4(name_end)
        data_end = data_start + filesize
        next_pos = align4(data_end)
        if name_end > len(data) or data_end > len(data):
            raise ValueError(f"truncated CPIO record at offset {pos}")
        raw_name = data[name_start:name_end]
        if not raw_name.endswith(b"\0"):
            raise ValueError(f"unterminated CPIO name at offset {pos}")
        name = raw_name[:-1].decode("utf-8", "surrogateescape")
        records.append(Record(name, fields, data[data_start:data_end], data[start:next_pos]))
        pos = next_pos
        if name == "TRAILER!!!":
            if pos != len(data):
                raise ValueError("bytes follow CPIO trailer")
            break
    else:
        raise ValueError("CPIO has no trailer")
    if not records or records[-1].name != "TRAILER!!!":
        raise ValueError("CPIO has no trailer")
    return records


def make_record(
    name: str,
    payload: bytes,
    *,
    mode: int,
    ino: int,
    uid: int = 0,
    gid: int = 0,
    nlink: int = 1,
    mtime: int = 0,
) -> bytes:
    name_bytes = name.encode("utf-8") + b"\0"
    fields = (ino, mode, uid, gid, nlink, mtime, len(payload), 0, 0, 0, 0, len(name_bytes), 0)
    header = b"070701" + b"".join(f"{value:08x}".encode("ascii") for value in fields)
    out = bytearray(header)
    out += name_bytes
    out += b"\0" * (align4(len(out)) - len(out))
    out += payload
    out += b"\0" * (align4(len(out)) - len(out))
    return bytes(out)


def replace_record(name: str, payload: bytes, fields: tuple[int, ...]) -> bytes:
    """Replace payload while retaining all identity fields except size/check."""
    values = list(fields)
    values[6] = len(payload)
    values[12] = 0
    return make_record(
        name,
        payload,
        mode=values[1],
        ino=values[0],
        uid=values[2],
        gid=values[3],
        nlink=values[4],
        mtime=values[5],
    )


def clone_with_name(name: str, record: Record) -> bytes:
    return make_record(
        name,
        record.payload,
        mode=record.fields[1],
        ino=record.fields[0],
        uid=record.fields[2],
        gid=record.fields[3],
        nlink=record.fields[4],
        mtime=record.fields[5],
    )


def transform_cpio(
    original: bytes,
    *,
    wrapper: bytes,
    guardian: bytes,
    native_start: bytes,
    busybox: bytes,
    musl: bytes,
    rootfs_xz: bytes,
) -> tuple[bytes, dict[str, object]]:
    records = parse_cpio(original)
    by_name: dict[str, Record] = {}
    for record in records:
        if record.name in by_name:
            raise ValueError(f"duplicate CPIO record: {record.name}")
        by_name[record.name] = record

    init_link = by_name.get("init")
    init_binary = by_name.get("system/bin/init")
    if init_link is None or init_binary is None:
        raise ValueError("base CPIO is missing init records")
    if init_link.fields[1] & 0o170000 != 0o120000 or init_link.payload != b"/system/bin/init":
        raise ValueError("base top-level init is not the shipping /system/bin/init symlink")
    if init_binary.fields[1] & 0o170000 != 0o100000 or not init_binary.payload.startswith(b"\x7fELF"):
        raise ValueError("base system/bin/init is not a regular ELF")
    if any(name.startswith("native") or name in {"run", "system/bin/init.android"} for name in by_name):
        raise ValueError("base CPIO already contains native handoff paths")

    max_ino = max(record.fields[0] for record in records if record.name != "TRAILER!!!")
    next_ino = max_ino + 1
    added: dict[str, bytes] = {
        "native/native-start": native_start,
        "native/bin/busybox": busybox,
        "native/lib/ld-musl-aarch64.so.1": musl,
        "native/rootfs.tar.xz": rootfs_xz,
        "system/bin/native-guardian": guardian,
    }
    added_modes = {
        "native/native-start": 0o100755,
        "native/bin/busybox": 0o100755,
        "native/lib/ld-musl-aarch64.so.1": 0o100755,
        "native/rootfs.tar.xz": 0o100644,
        "system/bin/native-guardian": 0o100755,
    }
    dir_names = ["native", "native/bin", "native/lib", "run"]
    collisions = set(added) | set(dir_names) | {"native-enable", "system/bin/init.android"}
    if collisions & set(by_name):
        raise ValueError(f"native path collision: {sorted(collisions & set(by_name))}")

    out = bytearray()
    changed: list[str] = []
    for record in records:
        if record.name == "TRAILER!!!":
            # New records are appended immediately before the original trailer.
            # This keeps every original record in order and byte-identical unless
            # it is one of the two deliberate init records below.
            out += clone_with_name("system/bin/init.android", init_binary)
            changed.append("+system/bin/init.android")
            out += make_record(
                "native-enable", b"", mode=0o100644, ino=next_ino
            )
            next_ino += 1
            changed.append("+native-enable")
            for name in dir_names:
                out += make_record(name, b"", mode=0o040755, ino=next_ino)
                next_ino += 1
                changed.append("+" + name)
            for name, payload in added.items():
                out += make_record(name, payload, mode=added_modes[name], ino=next_ino)
                next_ino += 1
                changed.append("+" + name)
            out += record.raw
            continue
        if record.name == "init":
            # Recovery's first-stage entry must be a regular copy of the
            # known-good init binary.  The existing record is a symlink, so
            # retain its inode/owner/timestamp but take the regular-file mode
            # and payload identity from system/bin/init.
            fields = list(record.fields)
            fields[1] = init_binary.fields[1]
            out += make_record(
                "init",
                init_binary.payload,
                mode=fields[1],
                ino=fields[0],
                uid=fields[2],
                gid=fields[3],
                nlink=fields[4],
                mtime=fields[5],
            )
            changed.append("init")
        elif record.name == "system/bin/init":
            out += replace_record(record.name, wrapper, record.fields)
            changed.append("system/bin/init")
        else:
            out += record.raw
    if not changed or {"init", "system/bin/init"} - set(changed):
        raise ValueError("failed to replace both first-stage init records")
    return bytes(out), {
        "base_entries": len(records),
        "new_entries": len(parse_cpio(bytes(out))),
        "changed_records": changed,
        "base_cpio_sha256": hashlib.sha256(original).hexdigest(),
        "new_cpio_sha256": hashlib.sha256(out).hexdigest(),
    }


def run(*args: str) -> None:
    subprocess.run(list(args), check=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", type=Path, default=ROOT / "builds/native_handoff_embedded.img")
    ap.add_argument("--base-cpio", type=Path, default=ROOT / "builds/headless_recovery_key_ramdisk.cpio")
    ap.add_argument("--rootfs-archive", type=Path, default=ROOT / "rootfs/alpine-arm64.tar.xz")
    ap.add_argument("--init-wrapper", type=Path, default=ROOT / "builds/native_handoff_init_wrapper_aarch64")
    ap.add_argument("--guardian", type=Path, default=ROOT / "builds/native_handoff_guardian_aarch64")
    ap.add_argument("--native-start", type=Path, default=ROOT / "tools/native-handoff/native-start")
    ap.add_argument("--busybox", type=Path, default=ROOT / "rootfs/alpine-arm64/bin/busybox")
    ap.add_argument("--musl", type=Path, default=ROOT / "rootfs/alpine-arm64/lib/ld-musl-aarch64.so.1")
    args = ap.parse_args()

    paths = [args.base_cpio, args.rootfs_archive, args.init_wrapper, args.guardian,
             args.native_start, args.busybox, args.musl]
    for path in paths:
        if not path.is_file():
            raise SystemExit(f"required input is not a regular file: {path}")
    original = args.base_cpio.read_bytes()
    base_sha = hashlib.sha256(original).hexdigest()
    if args.base_cpio == ROOT / "builds/headless_recovery_key_ramdisk.cpio" and base_sha != BASE_CPIO_SHA256:
        raise SystemExit(f"unexpected host-key base CPIO hash: {base_sha}")

    new_cpio, audit = transform_cpio(
        original,
        wrapper=args.init_wrapper.read_bytes(),
        guardian=args.guardian.read_bytes(),
        native_start=args.native_start.read_bytes(),
        busybox=args.busybox.read_bytes(),
        musl=args.musl.read_bytes(),
        rootfs_xz=args.rootfs_archive.read_bytes(),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="native-handoff-") as td:
        work = Path(td)
        cpio = work / "ramdisk.cpio"
        lz4 = work / "ramdisk.lz4"
        raw = work / "recovery.raw.img"
        cpio.write_bytes(new_cpio)
        run("lz4", "-l", "-12", str(cpio), str(lz4))
        run(
            "python3", str(TOOLS / "mkbootimg/mkbootimg.py"),
            "--kernel", str(REF / "unpacked/kernel"), "--ramdisk", str(lz4),
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
        shutil.copyfile(cpio, args.output.with_name(args.output.stem + "_ramdisk.cpio"))
        shutil.copyfile(lz4, args.output.with_name(args.output.stem + "_ramdisk.lz4"))

    manifest = {
        "image": str(args.output),
        "image_sha256": hashlib.sha256(args.output.read_bytes()).hexdigest(),
        "ramdisk_cpio_sha256": hashlib.sha256(new_cpio).hexdigest(),
        "ramdisk_lz4_sha256": hashlib.sha256(args.output.with_name(args.output.stem + "_ramdisk.lz4").read_bytes()).hexdigest(),
        "rootfs_archive_sha256": hashlib.sha256(args.rootfs_archive.read_bytes()).hexdigest(),
        "rootfs_archive_bytes": args.rootfs_archive.stat().st_size,
        "inputs": {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths},
        "audit": audit,
        "header_source": "lineage/build-20260915/recovery.img",
        "avb_algorithm": "NONE",
        "partition_size": PARTITION_SIZE,
    }
    manifest_path = args.output.with_name(args.output.stem + "_manifest.json")
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
