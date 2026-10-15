#!/usr/bin/env python3
"""Chunked camera recorder for the Galaxy S22: bounded capture sessions + deferred MJPEG mux.

Why this exists: `s22-camera record`'s long stream wedges the phone camera
driver at stream stop (client stuck in D state at kthread_stop). A
capture-style session (~7 frames, ~0.7 s) always tears down cleanly, so
recording here is a chain of independent capture-style sessions: each session
opens the sensor-group leader and the CSIS VC0 DMA through the capture
path's own code (s22-camera.py's run_stream/Session, imported, not
reimplemented), streams at most --max-frames-per-session frames, and runs the
capture path's full teardown before the next session starts. Raw frames are
written to --out only after that session's teardown. Only after the LAST
teardown does the encoder develop the raws and mux video.avi, so no encoder
ever runs while a stream is open.

Encoder: gst-launch-1.0 (the reviewed make_mjpeg path, including the Arch
chroot fallback) when available; otherwise a pure-Python baseline-JPEG
encoder + AVI muxer in this file. `auto` (default) tries gst first and falls
back to the pure-Python writer.

Stdlib only (Python 3.12 host tests, 3.14 on the phone).
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import os
import struct
import sys
import time

DEFAULT_MAX_FRAMES = 7     # the capture path's proven-clean session length
DEFAULT_FPS = 5
DEFAULT_DEADLINE = 120.0
DEFAULT_SCALE = 4          # develop downscale for the MJPEG frames
JPEG_QUALITY = 80
SESSION_PIXFMT = "sbggr16"


def load_client(path: str):
    """Import the reviewed s22-camera client module (the capture path)."""
    spec = importlib.util.spec_from_file_location("s22_camera_client", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--client", default=os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "s22-camera.py"),
        help="s22-camera.py to import (default: sibling of this script)")
    p.add_argument("--sensor", choices=("rear", "front"), default="rear")
    p.add_argument("--seconds", type=float, required=True)
    p.add_argument("--fps", type=float, default=DEFAULT_FPS,
                   help="target frames per second (default %d)" % DEFAULT_FPS)
    p.add_argument("--out", required=True)
    p.add_argument("--max-frames-per-session", type=int, default=DEFAULT_MAX_FRAMES,
                   help="stream budget per session; the camera fully tears down "
                        "between sessions (default %d)" % DEFAULT_MAX_FRAMES)
    p.add_argument("--exposure-us", type=int, default=0,
                   help="sensor integration time in us, written once per session "
                        "after stream-on (0 = leave the sensor default)")
    p.add_argument("--gain", type=float, default=0.0,
                   help="sensor analog gain multiplier, written once per session "
                        "(0 = leave the sensor default)")
    p.add_argument("--deadline", type=float, default=DEFAULT_DEADLINE,
                   help="hard per-session deadline (s, default %d)" % DEFAULT_DEADLINE)
    p.add_argument("--scale", type=int, default=DEFAULT_SCALE,
                   help="even develop downscale for the MJPEG frames (default %d)"
                        % DEFAULT_SCALE)
    p.add_argument("--encoder", choices=("auto", "gst", "python"), default="auto",
                   help="auto = gst-launch-1.0 when available, else the built-in "
                        "pure-Python MJPEG writer")
    p.add_argument("--keep-raw", action="store_true",
                   help="keep frame_*.raw after encoding (deleted by default)")
    return p


def capture_args(cam, sensor: str, deadline: float, exposure_us: int, gain: float):
    """The capture path's own argument set for one capture-style session."""
    argv = ["capture", "--sensor", sensor, "--out", os.devnull,
            "--deadline", str(deadline), "--allow-fw-stall"]
    if exposure_us:
        argv += ["--cis-exposure-us", str(exposure_us)]
    if gain:
        argv += ["--cis-again", str(gain)]
    return cam.build_parser().parse_args(argv)


def record(cam, args, sysif=None, alarm=True, encode=None) -> dict:
    """Run the session chain, then encode. Returns the final result dict.

    `encode` (for tests) replaces the encoder callable
    (cam, out_dir, fps, scale, mode, keep_raw) -> dict; the real encoder is
    only ever invoked after every session's teardown has completed.
    """
    sysif = sysif or cam.RealSys()
    cap_args = capture_args(cam, args.sensor, args.deadline, args.exposure_us, args.gain)
    cam.apply_sensor_preset(cap_args)
    target = max(1, int(round(args.seconds * args.fps)))
    os.makedirs(args.out, exist_ok=True)
    stride = cam.vc0_stride(cap_args.width, cam.PIX_SBGGR16)
    n_sessions = (target + args.max_frames_per_session - 1) // args.max_frames_per_session
    written = sessions = 0
    started = time.monotonic()
    for session in range(n_sessions):
        budget = min(args.max_frames_per_session, target - written)
        held = []

        def consume(raw, frame, _held=held, _budget=budget):
            if raw is not None:
                _held.append(raw)
            return len(_held) >= _budget

        stream = cam.run_stream(sysif, cap_args, consume, alarm=alarm)
        for err in stream.get("teardown_errors") or []:
            print(f"# session {session} teardown: {err}", file=sys.stderr)
        # run_stream's finally block has stopped the stream and released the
        # DMA buffers; only now may the frames touch the filesystem (the same
        # rule `s22-camera record` follows).
        for raw in held:
            with open(os.path.join(args.out, "frame_%05d.raw" % written), "wb") as f:
                f.write(raw)
            written += 1
        sessions += 1
        print(f"# session {session + 1}/{n_sessions}: {written}/{target} frames",
              file=sys.stderr)
    meta = {"sensor": args.sensor, "frames": written, "fps": args.fps,
            "seconds": args.seconds, "sessions": sessions,
            "max_frames_per_session": args.max_frames_per_session,
            "width": cap_args.width, "height": cap_args.height, "stride": stride,
            "pixfmt": "BYR2", "bayer": cam.SENSOR_PRESETS[args.sensor]["bayer"],
            "scale": args.scale}
    with open(os.path.join(args.out, "frames.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=1)
    # The encoder runs only here: every session's teardown has completed.
    enc = encode or encode_video
    video = enc(cam, args.out, args.fps, args.scale, args.encoder, args.keep_raw)
    return {"ok": bool(video.get("ok")), "path": video.get("path"),
            "frames": written, "sessions": sessions, "seconds": args.seconds,
            "effective_fps": written / (time.monotonic() - started)}


# --------------------------------------------------------------------------
# Pure-Python baseline JPEG encoder (ITU T.81 Annex K tables, 4:4:4) and
# MJPEG AVI muxer. Used only when gst-launch-1.0 is unavailable; never while
# a camera stream is open.
# --------------------------------------------------------------------------
_Q_LUMA = (16, 11, 10, 16, 24, 40, 51, 61, 12, 12, 14, 19, 26, 58, 60, 55,
           14, 13, 16, 24, 40, 57, 69, 56, 14, 17, 22, 29, 51, 87, 80, 62,
           18, 22, 37, 56, 68, 109, 103, 77, 24, 35, 55, 64, 81, 104, 113, 92,
           49, 64, 78, 87, 103, 121, 120, 101, 72, 92, 95, 98, 112, 100, 103, 99)
_Q_CHROMA = (17, 18, 24, 47, 99, 99, 99, 99, 18, 21, 26, 66, 99, 99, 99, 99,
             24, 26, 56, 99, 99, 99, 99, 99, 47, 66, 99, 99, 99, 99, 99, 99,
             99, 99, 99, 99, 99, 99, 99, 99, 99, 99, 99, 99, 99, 99, 99, 99,
             99, 99, 99, 99, 99, 99, 99, 99, 99, 99, 99, 99, 99, 99, 99, 99)
_ZIGZAG = (0, 1, 8, 16, 9, 2, 3, 10, 17, 24, 32, 25, 18, 11, 4, 5,
           12, 19, 26, 33, 40, 48, 41, 34, 27, 20, 13, 6, 7, 14, 21,
           28, 35, 42, 49, 56, 57, 50, 43, 36, 29, 22, 15, 23, 30,
           37, 44, 51, 58, 59, 52, 45, 38, 31, 39, 46, 53, 60, 61, 54,
           47, 55, 62, 63)
_DC_LUMA_BITS = (0, 1, 5, 1, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0, 0, 0)
_DC_CHROMA_BITS = (0, 3, 1, 1, 1, 1, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0)
_AC_LUMA_BITS = (0, 2, 1, 3, 3, 2, 4, 3, 5, 5, 4, 4, 0, 0, 1, 0x7D)
_AC_CHROMA_BITS = (0, 2, 1, 2, 4, 4, 3, 4, 7, 5, 4, 4, 0, 1, 2, 0x77)
_AC_LUMA_VALS = (
    0x01, 0x02, 0x03, 0x00, 0x04, 0x11, 0x05, 0x12,
    0x21, 0x31, 0x41, 0x06, 0x13, 0x51, 0x61, 0x07,
    0x22, 0x71, 0x14, 0x32, 0x81, 0x91, 0xa1, 0x08,
    0x23, 0x42, 0xb1, 0xc1, 0x15, 0x52, 0xd1, 0xf0,
    0x24, 0x33, 0x62, 0x72, 0x82, 0x09, 0x0a, 0x16,
    0x17, 0x18, 0x19, 0x1a, 0x25, 0x26, 0x27, 0x28,
    0x29, 0x2a, 0x34, 0x35, 0x36, 0x37, 0x38, 0x39,
    0x3a, 0x43, 0x44, 0x45, 0x46, 0x47, 0x48, 0x49,
    0x4a, 0x53, 0x54, 0x55, 0x56, 0x57, 0x58, 0x59,
    0x5a, 0x63, 0x64, 0x65, 0x66, 0x67, 0x68, 0x69,
    0x6a, 0x73, 0x74, 0x75, 0x76, 0x77, 0x78, 0x79,
    0x7a, 0x83, 0x84, 0x85, 0x86, 0x87, 0x88, 0x89,
    0x8a, 0x92, 0x93, 0x94, 0x95, 0x96, 0x97, 0x98,
    0x99, 0x9a, 0xa2, 0xa3, 0xa4, 0xa5, 0xa6, 0xa7,
    0xa8, 0xa9, 0xaa, 0xb2, 0xb3, 0xb4, 0xb5, 0xb6,
    0xb7, 0xb8, 0xb9, 0xba, 0xc2, 0xc3, 0xc4, 0xc5,
    0xc6, 0xc7, 0xc8, 0xc9, 0xca, 0xd2, 0xd3, 0xd4,
    0xd5, 0xd6, 0xd7, 0xd8, 0xd9, 0xda, 0xe1, 0xe2,
    0xe3, 0xe4, 0xe5, 0xe6, 0xe7, 0xe8, 0xe9, 0xea,
    0xf1, 0xf2, 0xf3, 0xf4, 0xf5, 0xf6, 0xf7, 0xf8,
    0xf9, 0xfa)
_AC_CHROMA_VALS = (
    0x00, 0x01, 0x02, 0x03, 0x11, 0x04, 0x05, 0x21,
    0x31, 0x06, 0x12, 0x41, 0x51, 0x07, 0x61, 0x71,
    0x13, 0x22, 0x32, 0x81, 0x08, 0x14, 0x42, 0x91,
    0xa1, 0xb1, 0xc1, 0x09, 0x23, 0x33, 0x52, 0xf0,
    0x15, 0x62, 0x72, 0xd1, 0x0a, 0x16, 0x24, 0x34,
    0xe1, 0x25, 0xf1, 0x17, 0x18, 0x19, 0x1a, 0x26,
    0x27, 0x28, 0x29, 0x2a, 0x35, 0x36, 0x37, 0x38,
    0x39, 0x3a, 0x43, 0x44, 0x45, 0x46, 0x47, 0x48,
    0x49, 0x4a, 0x53, 0x54, 0x55, 0x56, 0x57, 0x58,
    0x59, 0x5a, 0x63, 0x64, 0x65, 0x66, 0x67, 0x68,
    0x69, 0x6a, 0x73, 0x74, 0x75, 0x76, 0x77, 0x78,
    0x79, 0x7a, 0x82, 0x83, 0x84, 0x85, 0x86, 0x87,
    0x88, 0x89, 0x8a, 0x92, 0x93, 0x94, 0x95, 0x96,
    0x97, 0x98, 0x99, 0x9a, 0xa2, 0xa3, 0xa4, 0xa5,
    0xa6, 0xa7, 0xa8, 0xa9, 0xaa, 0xb2, 0xb3, 0xb4,
    0xb5, 0xb6, 0xb7, 0xb8, 0xb9, 0xba, 0xc2, 0xc3,
    0xc4, 0xc5, 0xc6, 0xc7, 0xc8, 0xc9, 0xca, 0xd2,
    0xd3, 0xd4, 0xd5, 0xd6, 0xd7, 0xd8, 0xd9, 0xda,
    0xe2, 0xe3, 0xe4, 0xe5, 0xe6, 0xe7, 0xe8, 0xe9,
    0xea, 0xf2, 0xf3, 0xf4, 0xf5, 0xf6, 0xf7, 0xf8,
    0xf9, 0xfa)


def _huff_codes(bits, vals) -> dict:
    """Canonical Huffman codes (T.81 B.2.2) from code lengths + symbols."""
    codes, code, k = {}, 0, 0
    for length in range(1, 17):
        for _ in range(bits[length - 1]):
            codes[vals[k]] = (code, length)
            k += 1
            code += 1
        code <<= 1
    return codes


class _BitWriter:
    def __init__(self):
        self.out = bytearray()
        self.acc = 0
        self.nbits = 0

    def write(self, code: int, length: int) -> None:
        self.acc = (self.acc << length) | code
        self.nbits += length
        out = self.out
        while self.nbits >= 8:
            self.nbits -= 8
            byte = (self.acc >> self.nbits) & 0xFF
            out.append(byte)
            if byte == 0xFF:      # byte stuffing (B.1.1.5)
                out.append(0x00)
        self.acc &= (1 << self.nbits) - 1

    def flush(self) -> None:
        if self.nbits:
            self.write((1 << self.nbits) - 1, self.nbits)   # pad with 1s


def _round(v: float) -> int:
    return int(v + 0.5) if v >= 0 else -int(-v + 0.5)


def _fdct(blk, cos, scale):
    """Separable 8x8 DCT; blk (64 floats, row-major) -> 64 coefficients."""
    tmp = [0.0] * 64
    for y in range(8):
        base = y * 8
        row = blk[base:base + 8]
        for u in range(8):
            cu = cos[u]
            tmp[base + u] = (row[0] * cu[0] + row[1] * cu[1] + row[2] * cu[2]
                              + row[3] * cu[3] + row[4] * cu[4] + row[5] * cu[5]
                              + row[6] * cu[6] + row[7] * cu[7])
    out = [0.0] * 64
    for u in range(8):
        cu = cos[u]
        for v in range(8):
            s = (tmp[0 * 8 + u] * cu[0] + tmp[1 * 8 + u] * cu[1]
                 + tmp[2 * 8 + u] * cu[2] + tmp[3 * 8 + u] * cu[3]
                 + tmp[4 * 8 + u] * cu[4] + tmp[5 * 8 + u] * cu[5]
                 + tmp[6 * 8 + u] * cu[6] + tmp[7 * 8 + u] * cu[7])
            out[v * 8 + u] = scale[u] * scale[v] * s
    return out


def jpeg_encode(rows, width: int, height: int, quality: int = JPEG_QUALITY) -> bytes:
    """RGB888 rows -> baseline JPEG bytes (4:4:4, Annex K Huffman tables)."""
    q = max(1, min(100, quality))
    qscale = 5000 // q if q < 50 else 200 - 2 * q
    zz = _ZIGZAG
    ql_nat = [min(255, max(1, (v * qscale + 50) // 100)) for v in _Q_LUMA]
    qc_nat = [min(255, max(1, (v * qscale + 50) // 100)) for v in _Q_CHROMA]
    ql = [ql_nat[zz[i]] for i in range(64)]      # DQT stores zigzag order
    qc = [qc_nat[zz[i]] for i in range(64)]
    dc_l = _huff_codes(_DC_LUMA_BITS, tuple(range(12)))
    dc_c = _huff_codes(_DC_CHROMA_BITS, tuple(range(12)))
    ac_l = _huff_codes(_AC_LUMA_BITS, _AC_LUMA_VALS)
    ac_c = _huff_codes(_AC_CHROMA_BITS, _AC_CHROMA_VALS)
    cos = [[math.cos((2 * x + 1) * u * math.pi / 16) for x in range(8)] for u in range(8)]
    scale = [0.25 * (0.7071067811865476 if u == 0 else 1.0) for u in range(8)]

    padw, padh = (width + 7) // 8 * 8, (height + 7) // 8 * 8
    y_plane = [0.0] * (padw * padh)
    cb_plane = [0.0] * (padw * padh)
    cr_plane = [0.0] * (padw * padh)
    for y in range(padh):
        row = rows[min(y, height - 1)]
        ybase = y * padw
        for x in range(padw):
            sx = min(x, width - 1)
            r, g, b = row[3 * sx], row[3 * sx + 1], row[3 * sx + 2]
            i = ybase + x
            y_plane[i] = 0.299 * r + 0.587 * g + 0.114 * b - 128.0
            cb_plane[i] = -0.168736 * r - 0.331264 * g + 0.5 * b
            cr_plane[i] = 0.5 * r - 0.418688 * g - 0.081312 * b

    def encode_block(plane, bx, by, q_nat, dc_code, ac_code, state):
        blk = [plane[(by * 8 + yy) * padw + bx * 8 + xx]
               for yy in range(8) for xx in range(8)]
        coefs = _fdct(blk, cos, scale)
        qz = [0] * 64
        for j in range(64):                    # natural order, matching ql_nat
            qz[j] = _round(coefs[j] / q_nat[j])
        qzz = [qz[zz[i]] for i in range(64)]
        dc = qzz[0]
        diff = dc - state[0]
        cat = abs(diff).bit_length()
        code, nbits = dc_code[cat]
        bw.write(code, nbits)
        if cat:
            amp = diff if diff > 0 else diff + (1 << cat) - 1
            bw.write(amp & ((1 << cat) - 1), cat)
        run = 0
        for i in range(1, 64):
            v = qzz[i]
            if v == 0:
                run += 1
                continue
            while run >= 16:
                code, nbits = ac_code[0xF0]
                bw.write(code, nbits)
                run -= 16
            cat = abs(v).bit_length()
            code, nbits = ac_code[(run << 4) | cat]
            bw.write(code, nbits)
            amp = v if v > 0 else v + (1 << cat) - 1
            bw.write(amp & ((1 << cat) - 1), cat)
            run = 0
        if run:
            code, nbits = ac_code[0x00]
            bw.write(code, nbits)
        state[0] = dc

    bw = _BitWriter()
    state = [0]
    for by in range(padh // 8):
        for bx in range(padw // 8):
            encode_block(y_plane, bx, by, ql_nat, dc_l, ac_l, state)
            encode_block(cb_plane, bx, by, qc_nat, dc_c, ac_c, state)
            encode_block(cr_plane, bx, by, qc_nat, dc_c, ac_c, state)
    bw.flush()

    out = bytearray(b"\xFF\xD8")
    for tq, table in ((0x00, ql), (0x01, qc)):
        out += b"\xFF\xDB" + struct.pack(">H", 67) + bytes([tq]) + bytes(table)
    out += (b"\xFF\xC0" + struct.pack(">HBHHB", 17, 8, padh, padw, 3)
            + b"\x01\x11\x00\x02\x11\x01\x03\x11\x01")
    for tc_th, bits, vals in ((b"\x00", _DC_LUMA_BITS, tuple(range(12))),
                              (b"\x10", _AC_LUMA_BITS, _AC_LUMA_VALS),
                              (b"\x01", _DC_CHROMA_BITS, tuple(range(12))),
                              (b"\x11", _AC_CHROMA_BITS, _AC_CHROMA_VALS)):
        out += (b"\xFF\xC4" + struct.pack(">H", 19 + len(vals)) + tc_th
                + bytes(bits) + bytes(vals))
    out += (b"\xFF\xDA" + struct.pack(">HB", 12, 3)
            + b"\x01\x00\x02\x11\x03\x11" + bytes((0, 63, 0)))
    out += bw.out
    out += b"\xFF\xD9"
    return bytes(out)


def write_mjpeg_avi(path: str, jpegs, fps: float, width: int, height: int) -> str:
    """Mux JPEG frames into an MJPEG AVI 1.0 (RIFF, avih/strl/movi/idx1)."""
    rate = max(1, int(round(fps)))
    usec = int(round(1_000_000 / fps))
    biggest = max((len(j) for j in jpegs), default=0)
    movi = bytearray()
    idx = bytearray()
    offset = 4                      # first chunk sits right after 'movi'
    for jpg in jpegs:
        data = bytes(jpg)
        movi += b"00dc" + struct.pack("<I", len(data)) + data
        if len(data) & 1:
            movi += b"\x00"
        idx += struct.pack("<4sIII", b"00dc", 0x10, offset, len(data))
        offset += 8 + len(data) + (len(data) & 1)
    movi_list = b"LIST" + struct.pack("<I", 4 + len(movi)) + b"movi" + bytes(movi)
    idx1 = b"idx1" + struct.pack("<I", len(idx)) + bytes(idx)

    avih = struct.pack("<14I", usec, biggest * rate, 0, 0x10, len(jpegs), 0, 1,
                       biggest, width, height, 0, 0, 0, 0)
    strh = struct.pack("<4s4sIHH8I4H", b"vids", b"MJPG", 0, 0, 0, 0, 1, rate, 0,
                       len(jpegs), biggest, 0xFFFFFFFF, 0, 0, 0, width, height)
    strf = struct.pack("<IiiHHIIiiII", 40, width, height, 1, 24,
                       struct.unpack("<I", b"MJPG")[0], biggest, 0, 0, 0, 0)
    strl = (b"LIST" + struct.pack("<I", 4 + (8 + 56) + (8 + 40)) + b"strl"
            + b"strh" + struct.pack("<I", 56) + strh
            + b"strf" + struct.pack("<I", 40) + strf)
    hdrl = (b"LIST" + struct.pack("<I", 4 + (8 + 56) + len(strl)) + b"hdrl"
            + b"avih" + struct.pack("<I", 56) + avih + strl)
    body = b"AVI " + hdrl + movi_list + idx1
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(b"RIFF" + struct.pack("<I", len(body)) + body)
    os.replace(tmp, path)
    return path


def encode_video(cam, out_dir: str, fps: float, scale: int, mode: str,
                 keep_raw: bool = False) -> dict:
    """Develop the saved raws and mux video.avi. ONLY called after the last teardown.

    gst: PNG sequence -> gst-launch-1.0 multifilesrc/pngdec/jpegenc/avimux (the
    reviewed make_mjpeg path, including the Arch chroot fallback). python: the
    built-in encoder above. auto tries gst, then falls back to python.
    """
    names = sorted(n for n in os.listdir(out_dir)
                   if n.startswith("frame_") and n.endswith(".raw"))
    if not names:
        return {"ok": False, "error": "no frames to encode"}
    with open(os.path.join(out_dir, "frames.json"), encoding="utf-8") as f:
        meta = json.load(f)
    width, height, stride = meta["width"], meta["height"], meta["stride"]
    pattern = meta["bayer"]

    if mode in ("auto", "gst"):
        pngs = []
        for name in names:
            with open(os.path.join(out_dir, name), "rb") as f:
                raw = f.read()
            ow, oh, rows, _ = cam.develop(raw, width, height, stride, SESSION_PIXFMT,
                                          pattern=pattern, scale=scale)
            png = os.path.join(out_dir, name[:-4] + ".png")
            cam.write_image(png, ow, oh, rows)
            pngs.append(png)
        res = cam.make_mjpeg(out_dir, fps)
        if res.get("ok"):
            if not keep_raw:
                for name in names:
                    os.unlink(os.path.join(out_dir, name))
            return res
        for png in pngs:
            try:
                os.unlink(png)
            except OSError:
                pass
        if mode == "gst":
            return res
        # auto: gst unavailable or failed -> fall back to the built-in encoder

    jpegs = []
    ow = oh = None
    for name in names:
        with open(os.path.join(out_dir, name), "rb") as f:
            raw = f.read()
        ow, oh, rows, _ = cam.develop(raw, width, height, stride, SESSION_PIXFMT,
                                      pattern=pattern, scale=scale)
        jpegs.append(jpeg_encode(rows, ow, oh, JPEG_QUALITY))
        if not keep_raw:
            os.unlink(os.path.join(out_dir, name))
    video = os.path.join(out_dir, "video.avi")
    write_mjpeg_avi(video, jpegs, fps, ow, oh)
    return {"ok": True, "path": video, "encoder": "python", "width": ow, "height": oh}


def main(argv=None) -> int:
    p = build_parser()
    a = p.parse_args(argv)
    if a.seconds <= 0 or a.fps <= 0 or a.max_frames_per_session <= 0:
        p.error("--seconds, --fps and --max-frames-per-session must be positive")
    if a.scale < 2 or a.scale % 2:
        p.error("--scale must be an even integer >= 2")
    try:
        cam = load_client(a.client)
        result = record(cam, a)
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc) or repr(exc)}))
        return 1
    print(json.dumps(result))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
