#!/usr/bin/env python3
"""Recover a bounded AArch64 vendor ELF closure from a read-only F2FS image.

This intentionally handles only vendor-side files.  Android system/APEX
dependencies (Bionic, HIDL, nativewindow, etc.) are recorded by the caller and
must be supplied by a separate isolated runtime.  The recovery implementation
is imported from the existing compressed-F2FS recovery tool so its inline and
compressed-block checks remain canonical.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import re
import struct
import sys
from pathlib import Path


TREE_RE = re.compile(r"^(?P<prefix>(?:\|   )*)\|-- (?P<name>.*?) <ino = (?P<ino>0x[0-9a-f]+)")


def load_recovery_tool():
    path = Path(__file__).resolve().parents[1] / "hardware" / "recover-f2fs-file.py"
    spec = importlib.util.spec_from_file_location("recover_f2fs_file", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import recovery tool: {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def parse_tree(tree: Path) -> dict[str, tuple[int, str]]:
    """Map every tree path to (inode, original path spelling)."""
    stack: list[str] = []
    entries: dict[str, tuple[int, str]] = {}
    for line in tree.read_text(errors="ignore").splitlines():
        match = TREE_RE.search(line)
        if not match:
            continue
        depth = len(match.group("prefix")) // 4
        stack = stack[:depth]
        name = match.group("name")
        stack.append(name)
        path = "/".join(stack)
        entries[path] = (int(match.group("ino"), 16), path)
    return entries


def elf_identity(data: bytes) -> dict[str, int | str]:
    if len(data) < 20 or data[:4] != b"\x7fELF":
        raise ValueError("recovered file is not ELF")
    if data[4] != 2:
        raise ValueError(f"ELF class is {data[4]}, expected ELF64")
    if data[5] != 1:
        raise ValueError("ELF is not little-endian")
    machine = struct.unpack_from("<H", data, 18)[0]
    if machine != 183:
        raise ValueError(f"ELF machine is {machine}, expected AArch64 (183)")
    etype = struct.unpack_from("<H", data, 16)[0]
    if etype != 3:
        raise ValueError(f"ELF type is {etype}, expected shared object (3)")
    return {"class": "ELF64", "machine": "AArch64", "type": "DYN"}


def recover_one(image: Path, blockmap: Path, tree_entries: dict[str, tuple[int, str]],
                source: str, destination: Path, rec) -> dict[str, object]:
    source = source.lstrip("/")
    if not source.startswith("lib64/"):
        raise ValueError(f"refusing non-vendor or 32-bit source: {source}")
    if source not in tree_entries:
        raise FileNotFoundError(f"source is absent from vendor metadata: {source}")
    inode, original_path = tree_entries[source]
    dump = rec.inode_dump(image, inode)
    size = rec.inode_size_from_dump(dump)
    inline = rec.inline_data_from_dump(dump, size)
    # fsck's block map records absolute image paths while the tree parser uses
    # image-relative paths.  Keep the latter in the manifest but pass the
    # former to the canonical recovery helper.
    slots = None if inline is not None else rec.slots_for(blockmap, "/" + source)
    data = inline if inline is not None else rec.recover(image, slots, size)
    if len(data) != size or not any(data):
        raise RuntimeError(f"recovery failed for {source}: size/zero validation")
    identity = elf_identity(data)
    sha256 = hashlib.sha256(data).hexdigest()
    if destination.exists():
        raise FileExistsError(f"refusing to overwrite existing output: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
    return {
        "source_path": "/" + original_path,
        "inode": hex(inode),
        "bytes": size,
        "sha256": sha256,
        "blocks": "inline" if slots is None else len(slots),
        "destination": str(destination),
        "elf": identity,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("image", type=Path)
    ap.add_argument("blockmap", type=Path)
    ap.add_argument("tree", type=Path)
    ap.add_argument("output", type=Path, help="new compatibility root, not an existing tree")
    ap.add_argument("source", nargs="+", help="vendor image-relative lib64 paths")
    args = ap.parse_args()
    image = args.image.resolve()
    blockmap = args.blockmap.resolve()
    tree = args.tree.resolve()
    if image.stat().st_mode & 0o222:
        raise SystemExit("refusing writable image; use read-only vendor image copy")
    if args.output.exists() and any(args.output.iterdir()):
        raise SystemExit(f"refusing non-empty output namespace: {args.output}")
    entries = parse_tree(tree)
    rec = load_recovery_tool()
    results = []
    for source in args.source:
        relative = source.lstrip("/")
        destination = args.output / "vendor" / relative
        results.append(recover_one(image, blockmap, entries, relative, destination, rec))
    for result in results:
        print(
            f"source={result['source_path']} inode={result['inode']} "
            f"size={result['bytes']} blocks={result['blocks']} "
            f"sha256={result['sha256']} destination={result['destination']}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
