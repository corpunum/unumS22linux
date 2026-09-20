#!/usr/bin/env python3
"""Read one active DRM CRTC framebuffer into a PNG without modesetting.

This helper intentionally performs only these DRM operations:

* ``drmModeGetCrtc`` and ``drmModeGetFB2`` (with legacy ``drmModeGetFB`` as
  a compatibility fallback) to inspect an already active scanout;
* ``DRM_IOCTL_MODE_MAP_DUMB`` to obtain a read-only mmap offset; and
* a read-only ``mmap`` followed by the matching libdrm free calls.

It never becomes DRM master, sets a mode/plane, page-flips, removes a
framebuffer, or writes to a mapped buffer.  Imported/non-dumb GEM buffers may
not support MAP_DUMB; that case is reported as unsupported instead of being
treated as a successful capture.  The ABI definitions below are taken from
the local primary headers ``/usr/include/xf86drmMode.h``,
``/usr/include/libdrm/drm_mode.h``, and ``/usr/include/libdrm/drm.h``.
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.util
import errno
import fcntl
import mmap
import os
import struct
import sys
import zlib
from pathlib import Path


class ModeInfo(ctypes.Structure):
    _fields_ = [
        ("clock", ctypes.c_uint32),
        ("hdisplay", ctypes.c_uint16),
        ("hsync_start", ctypes.c_uint16),
        ("hsync_end", ctypes.c_uint16),
        ("htotal", ctypes.c_uint16),
        ("hskew", ctypes.c_uint16),
        ("vdisplay", ctypes.c_uint16),
        ("vsync_start", ctypes.c_uint16),
        ("vsync_end", ctypes.c_uint16),
        ("vtotal", ctypes.c_uint16),
        ("vscan", ctypes.c_uint16),
        ("vrefresh", ctypes.c_uint32),
        ("flags", ctypes.c_uint32),
        ("type", ctypes.c_uint32),
        ("name", ctypes.c_char * 32),
    ]


class DrmModeCrtc(ctypes.Structure):
    _fields_ = [
        ("crtc_id", ctypes.c_uint32),
        ("buffer_id", ctypes.c_uint32),
        ("x", ctypes.c_uint32),
        ("y", ctypes.c_uint32),
        ("width", ctypes.c_uint32),
        ("height", ctypes.c_uint32),
        ("mode_valid", ctypes.c_int),
        ("mode", ModeInfo),
        ("gamma_size", ctypes.c_int),
    ]


class DrmModeFB(ctypes.Structure):
    _fields_ = [
        ("fb_id", ctypes.c_uint32),
        ("width", ctypes.c_uint32),
        ("height", ctypes.c_uint32),
        ("pitch", ctypes.c_uint32),
        ("bpp", ctypes.c_uint32),
        ("depth", ctypes.c_uint32),
        ("handle", ctypes.c_uint32),
    ]


class DrmModeFB2(ctypes.Structure):
    _fields_ = [
        ("fb_id", ctypes.c_uint32),
        ("width", ctypes.c_uint32),
        ("height", ctypes.c_uint32),
        ("pixel_format", ctypes.c_uint32),
        ("modifier", ctypes.c_uint64),
        ("flags", ctypes.c_uint32),
        ("handles", ctypes.c_uint32 * 4),
        ("pitches", ctypes.c_uint32 * 4),
        ("offsets", ctypes.c_uint32 * 4),
    ]


class DrmModeMapDumb(ctypes.Structure):
    _fields_ = [
        ("handle", ctypes.c_uint32),
        ("pad", ctypes.c_uint32),
        ("offset", ctypes.c_uint64),
    ]


def ioc(direction: int, type_byte: int, number: int, size: int) -> int:
    # Linux _IOC layout from libdrm/drm.h.  DRM_IOCTL_MODE_MAP_DUMB is
    # DRM_IOWR('d', 0xb3, struct drm_mode_map_dumb).
    return (
        (direction << 30)
        | (size << 16)
        | (type_byte << 8)
        | number
    )


DRM_IOCTL_MODE_MAP_DUMB = ioc(3, ord("d"), 0xB3, ctypes.sizeof(DrmModeMapDumb))
DRM_FORMAT_XRGB8888 = struct.unpack("<I", b"XR24")[0]
DRM_FORMAT_ARGB8888 = struct.unpack("<I", b"AR24")[0]
DRM_FORMAT_XBGR8888 = struct.unpack("<I", b"XB24")[0]
DRM_FORMAT_ABGR8888 = struct.unpack("<I", b"AB24")[0]


def fourcc(value: int) -> str:
    return struct.pack("<I", value).decode("ascii", "replace")


def load_libdrm() -> ctypes.CDLL:
    path = ctypes.util.find_library("drm") or "libdrm.so.2"
    lib = ctypes.CDLL(path, use_errno=True)
    lib.drmModeGetCrtc.argtypes = [ctypes.c_int, ctypes.c_uint32]
    lib.drmModeGetCrtc.restype = ctypes.POINTER(DrmModeCrtc)
    lib.drmModeFreeCrtc.argtypes = [ctypes.POINTER(DrmModeCrtc)]
    lib.drmModeFreeCrtc.restype = None
    lib.drmModeGetFB2.argtypes = [ctypes.c_int, ctypes.c_uint32]
    lib.drmModeGetFB2.restype = ctypes.POINTER(DrmModeFB2)
    lib.drmModeFreeFB2.argtypes = [ctypes.POINTER(DrmModeFB2)]
    lib.drmModeFreeFB2.restype = None
    lib.drmModeGetFB.argtypes = [ctypes.c_int, ctypes.c_uint32]
    lib.drmModeGetFB.restype = ctypes.POINTER(DrmModeFB)
    lib.drmModeFreeFB.argtypes = [ctypes.POINTER(DrmModeFB)]
    lib.drmModeFreeFB.restype = None
    return lib


def png_chunk(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(
        ">I", zlib.crc32(kind + payload) & 0xFFFFFFFF
    )


def write_png(path: Path, width: int, height: int, rows: list[bytes]) -> None:
    raw = b"".join(b"\0" + row for row in rows)
    png = bytearray(b"\x89PNG\r\n\x1a\n")
    png += png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    png += png_chunk(b"IDAT", zlib.compress(raw, 6))
    png += png_chunk(b"IEND", b"")
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(png)
    os.replace(tmp, path)


def convert_row_fast(src: memoryview, width: int, fmt: int) -> bytes:
    if fmt in (DRM_FORMAT_XRGB8888, DRM_FORMAT_ARGB8888):
        data = src[: width * 4].tobytes()
        out = bytearray(width * 3)
        out[0::3] = data[2::4]
        out[1::3] = data[1::4]
        out[2::3] = data[0::4]
        return bytes(out)
    if fmt in (DRM_FORMAT_XBGR8888, DRM_FORMAT_ABGR8888):
        data = src[: width * 4].tobytes()
        out = bytearray(width * 3)
        out[0::3] = data[0::4]
        out[1::3] = data[1::4]
        out[2::3] = data[2::4]
        return bytes(out)
    raise ValueError(f"unsupported DRM format {fourcc(fmt)} (0x{fmt:08x})")


def capture(card: str, crtc_id: int, output: Path) -> dict[str, object]:
    if sys.byteorder != "little":
        raise RuntimeError("only little-endian DRM framebuffer layouts are supported")
    lib = load_libdrm()
    fd = os.open(card, os.O_RDWR | os.O_CLOEXEC)
    crtc = lib.drmModeGetCrtc(fd, crtc_id)
    if not crtc:
        saved = ctypes.get_errno()
        os.close(fd)
        raise OSError(saved, f"drmModeGetCrtc({crtc_id}) failed")
    try:
        c = crtc.contents
        if c.buffer_id == 0 or c.width == 0 or c.height == 0 or not c.mode_valid:
            raise RuntimeError(
                f"CRTC {crtc_id} is not active (fb={c.buffer_id}, "
                f"size={c.width}x{c.height}, mode_valid={c.mode_valid})"
            )
        fb2 = lib.drmModeGetFB2(fd, c.buffer_id)
        fb_legacy = None
        if fb2:
            fb = fb2.contents
            width, height = fb.width, fb.height
            fmt, modifier = fb.pixel_format, fb.modifier
            handle, pitch, offset = fb.handles[0], fb.pitches[0], fb.offsets[0]
            free_fb = ("fb2", fb2)
        else:
            fb_legacy = lib.drmModeGetFB(fd, c.buffer_id)
            if not fb_legacy:
                saved = ctypes.get_errno()
                raise OSError(saved, f"drmModeGetFB2/GetFB({c.buffer_id}) failed")
            old = fb_legacy.contents
            width, height = old.width, old.height
            fmt = DRM_FORMAT_XRGB8888 if old.bpp == 32 else 0
            modifier = 0
            handle, pitch, offset = old.handle, old.pitch, 0
            free_fb = ("legacy", fb_legacy)
        try:
            if not handle:
                raise RuntimeError("active framebuffer has no GEM handle")
            if modifier != 0:
                raise RuntimeError(f"non-linear framebuffer modifier 0x{modifier:x} is unsupported")
            if fmt not in {
                DRM_FORMAT_XRGB8888,
                DRM_FORMAT_ARGB8888,
                DRM_FORMAT_XBGR8888,
                DRM_FORMAT_ABGR8888,
            }:
                raise RuntimeError(f"unsupported framebuffer format {fourcc(fmt)} (0x{fmt:08x})")
            if width == 0 or height == 0 or width > 16384 or height > 16384:
                raise RuntimeError(f"unreasonable framebuffer size {width}x{height}")
            if c.x + c.width > width or c.y + c.height > height:
                raise RuntimeError(
                    f"CRTC window {c.x},{c.y} {c.width}x{c.height} exceeds FB {width}x{height}"
                )
            min_pitch = width * 4
            if pitch < min_pitch or pitch > 256 * 1024 * 1024:
                raise RuntimeError(f"invalid framebuffer pitch {pitch} for width {width}")
            read_len = offset + pitch * height
            if read_len > 1024 * 1024 * 1024:
                raise RuntimeError(f"mapping length {read_len} is unreasonable")
            mapped = DrmModeMapDumb(handle=handle)
            try:
                fcntl.ioctl(fd, DRM_IOCTL_MODE_MAP_DUMB, mapped, True)
            except OSError as exc:
                if exc.errno in (errno.EINVAL, errno.ENOSYS, errno.ENOTTY, errno.EPERM):
                    raise RuntimeError(
                        "active GEM buffer does not support DRM_IOCTL_MODE_MAP_DUMB; "
                        "an imported Weston buffer needs a Weston screencopy client"
                    ) from exc
                raise
            map_offset = int(mapped.offset)
            if map_offset % mmap.PAGESIZE:
                raise RuntimeError(f"kernel returned unaligned mmap offset 0x{map_offset:x}")
            with mmap.mmap(
                fd,
                read_len,
                flags=mmap.MAP_SHARED,
                prot=mmap.PROT_READ,
                offset=map_offset,
            ) as mapped_mem:
                view = memoryview(mapped_mem)[offset:]
                rows = []
                for row in range(c.height):
                    start = (c.y + row) * pitch + c.x * 4
                    end = start + c.width * 4
                    if end > len(view):
                        raise RuntimeError(f"row {row} exceeds mapped framebuffer bounds")
                    rows.append(convert_row_fast(view[start:end], c.width, fmt))
                del view
            output.parent.mkdir(parents=True, exist_ok=True)
            write_png(output, c.width, c.height, rows)
            return {
                "card": card,
                "crtc_id": crtc_id,
                "fb_id": int(c.buffer_id),
                "fb_size": [int(width), int(height)],
                "window": [int(c.x), int(c.y), int(c.width), int(c.height)],
                "format": fourcc(fmt),
                "modifier": int(modifier),
                "handle": int(handle),
                "pitch": int(pitch),
                "offset": int(offset),
                "mapped_bytes": int(read_len),
                "output": str(output),
            }
        finally:
            if free_fb[0] == "fb2":
                lib.drmModeFreeFB2(free_fb[1])
            else:
                lib.drmModeFreeFB(free_fb[1])
    finally:
        lib.drmModeFreeCrtc(crtc)
        os.close(fd)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="PNG output path")
    parser.add_argument("--card", default="/dev/dri/card1", help="DRM card (default: card1)")
    parser.add_argument("--crtc-id", type=int, default=184, help="active CRTC id (default: 184)")
    args = parser.parse_args()
    try:
        result = capture(args.card, args.crtc_id, args.output)
    except Exception as exc:  # concise failure for phone-side shell use
        print(f"drm-capture: unsupported/failed: {exc}", file=sys.stderr)
        return 2
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
