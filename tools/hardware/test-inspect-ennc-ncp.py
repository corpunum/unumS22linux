#!/usr/bin/env python3
"""Host-only regression tests for inspect-ennc-ncp.py."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import struct
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("inspect_ennc_ncp", HERE / "inspect-ennc-ncp.py")
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def header() -> bytearray:
    blob = bytearray(172)
    struct.pack_into("<I", blob, 0, MODULE.NCP_MAGIC1)
    struct.pack_into("<I", blob, 4, MODULE.NCP_VERSION)
    struct.pack_into("<I", blob, 168, MODULE.NCP_MAGIC2)
    return blob


def set_u32(blob: bytearray, offset: int, value: int) -> None:
    struct.pack_into("<I", blob, offset, value)


def test_truncated_header() -> None:
    assert MODULE.candidate(bytes(header()[:32]), 0)["status"] == "truncated_header"


def test_out_of_bounds_offset_count() -> None:
    blob = header()
    set_u32(blob, 60, 160)
    set_u32(blob, 64, 2)
    assert MODULE.candidate(bytes(blob), 0)["status"] == "validator_rejected"


def test_zero_offset_nonzero_count_is_indeterminate_without_unpack() -> None:
    blob = header()
    set_u32(blob, 72, 1)
    result = MODULE.candidate(bytes(blob), 0)
    assert result["status"] == "indeterminate_zero_range"
    assert "memory" in result["indeterminate_zero_ranges"]


def test_zero_body_offset_is_indeterminate() -> None:
    blob = header()
    set_u32(blob, 116, 4)
    result = MODULE.candidate(bytes(blob), 0)
    assert result["status"] == "indeterminate_zero_range"
    assert "body" in result["indeterminate_zero_ranges"]


def test_huge_memory_count_zero_offset_does_not_unpack() -> None:
    blob = header()
    set_u32(blob, 72, 0xFFFFFFFF)
    result = MODULE.candidate(bytes(blob), 0)
    assert result["status"] == "indeterminate_zero_range"


def test_memory_vector_with_zero_address_count_is_rejected() -> None:
    blob = header() + bytearray(52)
    set_u32(blob, 68, 172)
    set_u32(blob, 72, 1)
    set_u32(blob, 112, 208)
    set_u32(blob, 116, 4)
    result = MODULE.candidate(bytes(blob), 0)
    assert result["status"] == "validator_rejected"
    assert "memory[0].address_vector_index" in result["cross_checks"]


def test_invalid_memory_address_index() -> None:
    blob = header() + bytearray(52)
    set_u32(blob, 60, 172)  # address vector, one 16-byte element
    set_u32(blob, 64, 1)
    set_u32(blob, 68, 188)  # memory vector, one 36-byte element
    set_u32(blob, 72, 1)
    set_u32(blob, 112, 224)  # body
    set_u32(blob, 116, 4)
    set_u32(blob, 188 + 32, 1)  # only address index 0 exists
    result = MODULE.candidate(bytes(blob), 0)
    assert result["status"] == "validator_rejected"
    assert "memory[0].address_vector_index" in result["cross_checks"]


def test_group_and_thread_indices() -> None:
    blob = header() + bytearray(100)
    set_u32(blob, 92, 172)  # one group vector
    set_u32(blob, 96, 1)
    set_u32(blob, 100, 216)  # one thread vector
    set_u32(blob, 104, 1)
    set_u32(blob, 112, 244)  # body
    set_u32(blob, 116, 4)
    # group: all zero ranges are safe, thread group end refers out of range
    set_u32(blob, 216 + 16, 2)
    result = MODULE.candidate(bytes(blob), 0)
    assert result["status"] == "validator_rejected"
    assert "thread[0].group_index" in result["cross_checks"]


def test_structural_candidate() -> None:
    blob = header() + bytearray(56)
    set_u32(blob, 60, 172)
    set_u32(blob, 64, 1)
    set_u32(blob, 68, 188)
    set_u32(blob, 72, 1)
    set_u32(blob, 112, 224)
    set_u32(blob, 116, 4)
    set_u32(blob, 188 + 32, 0)
    assert MODULE.candidate(bytes(blob), 0)["status"] == "structural_candidate_only"


def test_compiled_c_layout_matches_python_constants() -> None:
    with tempfile.TemporaryDirectory() as directory:
        binary = Path(directory) / "ncp-layout"
        subprocess.run(
            ["cc", "-std=c11", "-Wall", "-Wextra", "-Werror", str(HERE / "ncp-v25-layout-probe.c"), "-o", str(binary)],
            check=True,
            capture_output=True,
            text=True,
        )
        lines = subprocess.check_output([str(binary)], text=True).splitlines()
    sizes = json.loads(lines[0])
    offsets = json.loads(lines[1])
    vector_offsets = json.loads(lines[2])
    assert sizes["address_vector"] == MODULE.VECTOR_SIZES["address"] == 16
    assert sizes["memory_vector"] == MODULE.VECTOR_SIZES["memory"] == 36
    assert sizes["pwr_est_vector"] == MODULE.VECTOR_SIZES["power"] == 16
    assert sizes["interruption_vector"] == MODULE.VECTOR_SIZES["interrupt"] == 28
    assert sizes["group_vector"] == MODULE.VECTOR_SIZES["group"] == 44
    assert sizes["thread_vector"] == MODULE.VECTOR_SIZES["thread"] == 28
    assert sizes["llc_vector"] == MODULE.VECTOR_SIZES["llc"] == 12
    assert sizes["memory_address_index"] == 32
    assert sizes["memory_type_cucode"] == 12
    assert sizes["memory_type_weight"] == 7
    assert sizes["memory_type_wmask"] == 8
    assert offsets == MODULE.FIELDS
    assert vector_offsets == MODULE.VECTOR_FIELD_OFFSETS


if __name__ == "__main__":
    for name, value in sorted(globals().items()):
        if name.startswith("test_"):
            value()
    print("inspect-ennc-ncp: 10 host regression tests passed")
