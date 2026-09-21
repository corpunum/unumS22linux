#!/usr/bin/env python3
"""Emit a hash-only manifest for the isolated ENN loader staging root."""

from __future__ import annotations

import hashlib
import json
import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--root', type=Path, default=ROOT / "rootfs/npu-compat-20260921")
STAGE = parser.parse_args().root.resolve()
OUTPUT = STAGE / "manifests/files.json"

records = []
for path in sorted(p for p in STAGE.rglob("*") if p.is_file() and p != OUTPUT):
    records.append({
        "path": str(path.relative_to(STAGE)),
        "bytes": path.stat().st_size,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    })
manifest = {
    "format": "s22-enn-compat-stage-v1",
    "stage": str(STAGE),
    "source_policy": "read-only recovered vendor files plus copies of accepted GPU Bionic baseline files",
    "checked_exact_sources": [
        "lineage/build-20260915/unpacked/ramdisk_extracted/system/lib64",
        "rootfs/gpu-compat-20260921/runtime/system/lib64",
        "rootfs/vendor-pristine-20260920.img via tools/gpu-compat/recover-vendor-closure.py",
        "/home/corpunum/s22-private-backups/20260920T062514Z/super.img: verified FYI3 system logical extents via tools/npu-probe/read-lp-metadata.py and read-only dm-linear view",
    ],
    "vendor_elf_execution": False,
    "phone_access": False,
    "npu_ioctl": False,
    "missing_unresolved_runtime_names": [],
    "missing_policy": "the three previously missing AArch64 system libraries and the transitive HIDL token library were recovered from the verified FYI3 system logical partition; no stubs or host libraries",
    "files": records,
}
OUTPUT.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
print(json.dumps({"files": len(records), "missing": manifest["missing_unresolved_runtime_names"]}))
