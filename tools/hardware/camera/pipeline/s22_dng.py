"""Minimal DNG 1.4 writer for S22 raw Bayer frames.

Wraps a 10-bit Bayer plane (stored in u16, little-endian) as an uncompressed
single-strip CFA DNG so that LibRaw / rawpy can demosaic it with the standard
DNG code path. Only the tags LibRaw needs are written: CFA pattern, black and
white level, colour matrix and the active area. The data is left as-is.

Pure Python + numpy. No third-party DNG library is used.
"""
from __future__ import annotations

import struct

import numpy as np

# TIFF field types
BYTE, ASCII, SHORT, LONG, RATIONAL, SRATIONAL = 1, 2, 3, 4, 5, 10
_SIZE = {BYTE: 1, ASCII: 1, SHORT: 2, LONG: 4, RATIONAL: 8, SRATIONAL: 8}

# DNG CFA colour codes: 0=R, 1=G, 2=B
CFA_CODE = {"R": 0, "G": 1, "B": 2}

# XYZ (D65) -> linear sRGB. Placeholder camera matrix: the GN3 has no
# ColorChecker calibration yet. See the brief for the calibration plan.
XYZ_TO_SRGB_D65 = [
    3.2406, -1.5372, -0.4986,
    -0.9689, 1.8758, 0.0415,
    0.0557, -0.2040, 1.0570,
]


def cfa_bytes(pattern: str) -> bytes:
    pattern = pattern.upper()
    if sorted(pattern) != sorted("RGGB"):
        raise ValueError("CFA pattern must be a permutation of RGGB")
    return bytes(CFA_CODE[c] for c in pattern)


def _entry_value(typ: int, values) -> bytes:
    if typ == ASCII:
        return values.encode("ascii") + b"\x00"
    if typ == BYTE:
        return bytes(values)
    if typ == SHORT:
        return struct.pack("<%dH" % len(values), *values)
    if typ == LONG:
        return struct.pack("<%dI" % len(values), *values)
    if typ == RATIONAL:
        return b"".join(struct.pack("<II", n, d) for n, d in values)
    if typ == SRATIONAL:
        return b"".join(struct.pack("<ii", n, d) for n, d in values)
    raise ValueError(typ)


def build_dng(bayer: np.ndarray, *, pattern: str = "GBRG", black: int = 64,
              white: int = 1023, make: str = "Samsung",
              model: str = "GN3 (S22 Linux)", as_shot_neutral=(1.0, 1.0, 1.0),
              color_matrix=XYZ_TO_SRGB_D65) -> bytes:
    """Return the bytes of a DNG for a (H, W) uint16 Bayer plane."""
    if bayer.ndim != 2 or bayer.dtype != np.uint16:
        raise ValueError("bayer must be a 2-D uint16 array")
    h, w = bayer.shape
    data = np.ascontiguousarray(bayer, dtype="<u2").tobytes()

    cm = [(round(v * 10000), 10000) for v in color_matrix]
    neutral = [(round(v * 1000000), 1000000) for v in as_shot_neutral]
    entries = [
        (254, LONG, [0]),
        (256, LONG, [w]),
        (257, LONG, [h]),
        (258, SHORT, [16]),
        (259, SHORT, [1]),
        (262, SHORT, [32803]),
        (271, ASCII, make),
        (272, ASCII, model),
        (273, LONG, [0]),          # StripOffsets, patched below
        (277, SHORT, [1]),
        (278, LONG, [h]),
        (279, LONG, [len(data)]),
        (33421, SHORT, [2, 2]),
        (33422, BYTE, cfa_bytes(pattern)),
        (50706, BYTE, [1, 4, 0, 0]),
        (50707, BYTE, [1, 2, 0, 0]),
        (50708, ASCII, model),
        (50714, SHORT, [black]),
        (50717, SHORT, [white]),
        (50721, SRATIONAL, cm),
        (50728, RATIONAL, neutral),
        (50778, SHORT, [21]),      # D65
        (50829, LONG, [0, 0, h, w]),
    ]
    entries.sort(key=lambda e: e[0])

    n = len(entries)
    ifd_off = 8
    ifd_size = 2 + 12 * n + 4
    cursor = ifd_off + ifd_size
    blobs = []
    records = []
    data_off = None
    for tag, typ, vals in entries:
        payload = _entry_value(typ, vals)
        count = len(payload) // _SIZE[typ]
        if len(payload) <= 4:
            field = payload.ljust(4, b"\x00")
        else:
            field = struct.pack("<I", cursor)
            if cursor % 2:
                blobs.append(b"\x00")
                cursor += 1
                field = struct.pack("<I", cursor)
            blobs.append(payload)
            cursor += len(payload)
        records.append((tag, typ, count, field))

    # Pad, then place the image data and point StripOffsets at it.
    if cursor % 2:
        blobs.append(b"\x00")
        cursor += 1
    data_off = cursor
    records = [
        (t, ty, c, struct.pack("<I", data_off) if t == 273 else f)
        for (t, ty, c, f) in records
    ]

    out = bytearray()
    out += b"II" + struct.pack("<HI", 42, ifd_off)
    out += struct.pack("<H", n)
    for tag, typ, count, field in records:
        out += struct.pack("<HHI", tag, typ, count) + field
    out += b"\x00\x00\x00\x00"  # next IFD
    for b in blobs:
        out += b
    out += data
    return bytes(out)


def write_dng(path: str, bayer: np.ndarray, **kwargs) -> int:
    blob = build_dng(bayer, **kwargs)
    with open(path, "wb") as f:
        f.write(blob)
    return len(blob)
