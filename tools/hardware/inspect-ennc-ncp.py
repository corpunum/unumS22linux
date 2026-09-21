#!/usr/bin/env python3
"""Offline ENNC/NCP-v25 inventory; never opens an NPU device or executes a model.

The NCP field offsets and range checks intentionally mirror the pinned
S5E9925 ncp_header_v25.h and npu-util-common.c.  This is a structural report,
not an ENNC unpacker and not proof that any candidate can execute.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
from pathlib import Path
from typing import BinaryIO

NCP_MAGIC1 = 0x0C0FFEE0
NCP_MAGIC2 = 0xC0DEC0DE
NCP_VERSION = 25
NCP_HEADER_SIZE = 172
NCP_MAGIC_BYTES = struct.pack("<I", NCP_MAGIC1)

# Field offsets in struct ncp_header.  All fields are u32 on the pinned ABI.
FIELDS = {
    "magic1": 0,
    "version": 4,
    "header_size": 8,
    "address_offset": 60,
    "address_count": 64,
    "memory_offset": 68,
    "memory_count": 72,
    "power_offset": 76,
    "power_count": 80,
    "interrupt_offset": 84,
    "interrupt_count": 88,
    "group_offset": 92,
    "group_count": 96,
    "thread_offset": 100,
    "thread_count": 104,
    "body_offset": 112,
    "body_size": 116,
    "io_offset": 120,
    "io_count": 124,
    "rq_offset": 128,
    "rq_size": 132,
    "llc_offset": 140,
    "llc_count": 144,
    "magic2": 168,
}

# sizeof() values from the exact pinned header, checked by
# tools/hardware/ncp-v25-layout-probe.c.  Do not change these from a model
# observation: the C header is authoritative.
VECTOR_SIZES = {
    "address": 16,
    "memory": 36,
    "power": 16,
    "interrupt": 28,
    "group": 44,
    "thread": 28,
    "llc": 12,
}

VECTOR_FIELD_OFFSETS = {
    "memory_address_index": 32,
    "group_intrinsic_offset": 28,
    "group_intrinsic_size": 32,
    "group_isa_offset": 36,
    "group_isa_size": 40,
    "thread_group_start": 12,
    "thread_group_end": 16,
    "thread_interrupt_start": 20,
    "thread_interrupt_end": 24,
}


def u32(blob: bytes, offset: int) -> int:
    return struct.unpack_from("<I", blob, offset)[0]


def read_fields(blob: bytes) -> dict[str, int]:
    return {name: u32(blob, offset) for name, offset in FIELDS.items()}


def range_check(offset: int, size: int, count: int, file_size: int) -> str:
    # This follows validate_ncp_offset_range(): empty entries are ignored.
    if not offset or not count or not size:
        return "ignored_zero_entry"
    end = offset + size * count
    return "pass" if end <= file_size else "fail_out_of_bounds"


def candidate(blob: bytes, offset: int) -> dict[str, object]:
    result: dict[str, object] = {"offset": offset}
    if len(blob) - offset < NCP_HEADER_SIZE:
        result["status"] = "truncated_header"
        return result
    header = blob[offset : offset + NCP_HEADER_SIZE]
    fields = read_fields(header)
    result["magic1"] = f"0x{fields['magic1']:08x}"
    result["magic2"] = f"0x{fields['magic2']:08x}"
    result["version"] = fields["version"]
    result["header_size"] = fields["header_size"]
    result["body_offset"] = fields["body_offset"]
    result["body_size"] = fields["body_size"]
    result["counts"] = {
        name: fields[f"{name}_count"]
        for name in ("address", "memory", "power", "interrupt", "group", "thread", "llc")
    }
    result["ranges"] = {
        name: range_check(
            fields[f"{name}_offset"],
            VECTOR_SIZES[name],
            fields[f"{name}_count"],
            len(blob) - offset,
        )
        for name in VECTOR_SIZES
    }
    result["body_range"] = range_check(fields["body_offset"], fields["body_size"], 1, len(blob) - offset)
    result["magic_version"] = (
        fields["magic1"] == NCP_MAGIC1
        and fields["magic2"] == NCP_MAGIC2
        and fields["version"] == NCP_VERSION
    )
    failures = [name for name, status in result["ranges"].items() if status == "fail_out_of_bounds"]
    if result["body_range"] == "fail_out_of_bounds":
        failures.append("body")

    # These are the additional checks in the pinned validator.  Only inspect
    # vector entries when their exact source range check passed.
    cross_failures: list[str] = []
    if result["ranges"]["memory"] == "pass" and fields["memory_count"]:
        memory_start = offset + fields["memory_offset"]
        address_start = offset + fields["address_offset"]
        if result["ranges"]["address"] == "pass":
            for index in range(fields["memory_count"]):
                item = struct.unpack_from("<9I", blob, memory_start + index * VECTOR_SIZES["memory"])
                address_index = item[8]
                if address_index >= fields["address_count"]:
                    cross_failures.append(f"memory[{index}].address_vector_index")
                    continue
                address = struct.unpack_from("<4I", blob, address_start + address_index * VECTOR_SIZES["address"])
                # The kernel checks m_addr bounds only for CUCODE/WEIGHT/WMASK
                # (enum values 12, 7, 8 in the pinned header).
                if item[0] in (7, 8, 12):
                    if range_check(address[1], address[3], 1, len(blob) - offset) == "fail_out_of_bounds":
                        cross_failures.append(f"memory[{index}].m_addr")
        else:
            # Every memory vector must reference an address vector.  Do not
            # dereference an absent/invalid address table.
            for index in range(fields["memory_count"]):
                cross_failures.append(f"memory[{index}].address_vector_index")
    if result["ranges"]["interrupt"] == "pass":
        start = offset + fields["interrupt_offset"]
        for index in range(fields["interrupt_count"]):
            item = struct.unpack_from("<7I", blob, start + index * VECTOR_SIZES["interrupt"])
            if range_check(fields["body_offset"] + item[1], item[2], 1, len(blob) - offset) == "fail_out_of_bounds":
                cross_failures.append(f"interrupt[{index}].isa")
    # The pinned validator also checks group ISA/intrinsic ranges and thread
    # group/interruption indices.  Keep these checks separate from the range
    # table so a zero offset with a nonzero count cannot lead to unsafe unpack.
    if fields["group_count"] and result["ranges"]["group"] == "pass":
        start = offset + fields["group_offset"]
        for index in range(fields["group_count"]):
            item = struct.unpack_from("<11I", blob, start + index * VECTOR_SIZES["group"])
            if range_check(fields["body_offset"] + item[9], item[10], 1, len(blob) - offset) == "fail_out_of_bounds":
                cross_failures.append(f"group[{index}].isa")
            if range_check(fields["body_offset"] + item[7], item[8], 1, len(blob) - offset) == "fail_out_of_bounds":
                cross_failures.append(f"group[{index}].intrinsic")
    if fields["thread_count"] and result["ranges"]["thread"] == "pass":
        start = offset + fields["thread_offset"]
        for index in range(fields["thread_count"]):
            item = struct.unpack_from("<7I", blob, start + index * VECTOR_SIZES["thread"])
            if fields["group_count"] and (item[3] >= fields["group_count"] or item[4] >= fields["group_count"]):
                cross_failures.append(f"thread[{index}].group_index")
            if fields["interrupt_count"] and (item[5] >= fields["interrupt_count"] or item[6] >= fields["interrupt_count"]):
                cross_failures.append(f"thread[{index}].interrupt_index")

    result["cross_checks"] = "pass" if not cross_failures else cross_failures
    zero_count_ranges = [
        name for name, status in result["ranges"].items()
        if status == "ignored_zero_entry" and fields[f"{name}_count"]
    ]
    if result["body_range"] == "ignored_zero_entry" and fields["body_size"]:
        zero_count_ranges.append("body")
    result["indeterminate_zero_ranges"] = zero_count_ranges
    if not result["magic_version"]:
        result["status"] = "magic_or_version_rejected"
    elif zero_count_ranges:
        result["status"] = "indeterminate_zero_range"
    elif failures or cross_failures:
        result["status"] = "validator_rejected"
    else:
        result["status"] = "structural_candidate_only"
    return result


def scan(path: Path) -> dict[str, object]:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        chunks: list[bytes] = []
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            chunks.append(chunk)
    blob = b"".join(chunks)
    offsets: list[int] = []
    cursor = 0
    while True:
        found = blob.find(NCP_MAGIC_BYTES, cursor)
        if found < 0:
            break
        offsets.append(found)
        cursor = found + 1
    ennc = []
    for pos in range(0, min(len(blob), 64) - 8 + 1, 4):
        if blob[pos + 4 : pos + 8] == b"ENNC":
            ennc.append({"offset": pos, "header_size_word": u32(blob, pos)})
    return {
        "name": path.name,
        "size": len(blob),
        "sha256": digest.hexdigest(),
        "ennc_headers_in_first_64_bytes": ennc,
        "ncp_magic_offsets": offsets,
        "candidates": [candidate(blob, pos) for pos in offsets],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--models-root", type=Path, default=Path("rootfs/npu-vendor-assets-decompressed"))
    parser.add_argument("--output", type=Path, default=Path("rootfs/hardware-reuse-20260921/ennc-ncp-inventory-v2.json"))
    args = parser.parse_args()
    paths = sorted(p for p in args.models_root.glob("*.nnc*") if p.is_file())
    records = [scan(path) for path in paths]
    summary = {
        "format": "offline-ennc-ncp-v25-inventory-v2",
        "source_header": "lineage/android_kernel_samsung_s5e9925/drivers/vision/npu/core/include/ncp_header_v25.h",
        "source_validator": "lineage/android_kernel_samsung_s5e9925/drivers/vision/npu/core/npu-util-common.c",
        "ncp_magic": "0x0c0ffee0",
        "ncp_version": NCP_VERSION,
        "files_scanned": len(records),
        "files_with_ennc_header": sum(bool(r["ennc_headers_in_first_64_bytes"]) for r in records),
        "files_with_ncp_magic": sum(bool(r["ncp_magic_offsets"]) for r in records),
        "structural_candidates": sum(sum(c["status"] == "structural_candidate_only" for c in r["candidates"]) for r in records),
        "records": records,
        "limitations": [
            "ENNC is not unpacked; NCP magic occurrences are only candidate offsets.",
            "No model contents are emitted and no candidate is certified executable.",
            "No NPU device, ioctl, firmware, ENN library, or phone path is used.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({k: summary[k] for k in ("files_scanned", "files_with_ennc_header", "files_with_ncp_magic", "structural_candidates")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
