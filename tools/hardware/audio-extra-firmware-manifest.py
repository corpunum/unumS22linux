#!/usr/bin/env python3
"""Emit a filename/hash-only ABOX extra-firmware closure manifest.

This is a host-only parser. It reads the pinned r0s DT source, the bounded
early-kernel receipt, and two explicitly named recovered firmware roots. It
never emits firmware bytes or scans outside those paths.
"""

import argparse
import hashlib
import json
import re
from pathlib import Path


DEFAULT_DTS = Path("builds/close-range-kernel-20260922/arch/arm64/boot/dts/samsung/r0s/r0s_eur_openx_w01_r25.dts")
DEFAULT_EARLY = Path("rootfs/main-driver-loop-20260921/audio-control-hook-reboot/early-kernel.txt")
ROOTS = (
    ("abox-audio-vendor-assets", Path("rootfs/abox-audio-vendor-assets/vendor/firmware")),
    ("audio-fw-recovery-20260922", Path("rootfs/audio-fw-recovery-20260922/vendor/firmware")),
)

# Pinned ABOX 4.2 Kconfig defaults. These are capacities, not staged bytes.
AREA_CAPACITY = {0: 0x85000, 1: 0x2800000, 2: None}


def number(value):
    return int(value, 0)


def parse_dt(path):
    text = path.read_text()
    rows = []
    for match in re.finditer(r"ext-bin@(\d+)\s*\{(.*?)\n\s*\};", text, re.S):
        body = match.group(2)
        compatible = re.search(r'compatible\s*=\s*"([^"]+)"', body)
        name = re.search(r'samsung,name\s*=\s*"([^"]+)"', body)
        area = re.search(r'samsung,area\s*=\s*<([^>]+)>', body)
        offset = re.search(r'samsung,offset\s*=\s*<([^>]+)>', body)
        if not compatible or compatible.group(1) != "samsung,abox-ext-bin" or not name or not area:
            continue
        area_value = number(area.group(1).split()[0])
        offset_value = number(offset.group(1).split()[0]) if offset else None
        capacity = AREA_CAPACITY.get(area_value)
        max_size = capacity - offset_value if capacity is not None and offset_value is not None else None
        rows.append({
            "index": int(match.group(1)),
            "filename": name.group(1),
            "area": area_value,
            "offset": offset_value,
            "max_size": max_size,
            "changeable": bool(re.search(r"samsung,changable\s*;", body)),
            "mixer_control": bool(re.search(r"samsung,mixer-control\s*;", body)),
        })
    if len(rows) != 22:
        raise SystemExit(f"expected 22 ABOX ext-bin nodes, found {len(rows)}")
    return rows


def hashes(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def recovered(filename, repo_root):
    entries = []
    for label, relative in ROOTS:
        path = repo_root / relative / filename
        if path.is_file() and not path.is_symlink():
            entries.append({
                "root": label,
                "location": str(relative / filename),
                "sha256": hashes(path),
            })
    return entries


def early_missing(path):
    if not path.is_file():
        return []
    pattern = re.compile(r"abox [^:]+: ([^ ]+) doesn't exist$")
    return sorted({m.group(1) for line in path.read_text(errors="replace").splitlines()
                   if (m := pattern.search(line))})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--dts", type=Path, default=DEFAULT_DTS)
    parser.add_argument("--early-log", type=Path, default=DEFAULT_EARLY)
    args = parser.parse_args()
    repo = args.repo_root.resolve()
    dts = args.dts if args.dts.is_absolute() else repo / args.dts
    early = args.early_log if args.early_log.is_absolute() else repo / args.early_log
    assets = []
    for row in parse_dt(dts):
        found = recovered(row["filename"], repo)
        hashes_seen = sorted({item["sha256"] for item in found})
        assets.append({
            **row,
            "recovered": found,
            "hash_consistent": len(hashes_seen) <= 1,
            "status": "recovered" if found else "missing",
        })
    print(json.dumps({
        "source_dts": str(dts.relative_to(repo)),
        "source_early_log": str(early.relative_to(repo)),
        "abox_ext_bin_count": len(assets),
        "early_log_missing_names": early_missing(early),
        "assets": assets,
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
