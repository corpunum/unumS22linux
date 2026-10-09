#!/usr/bin/env python3
"""Host tests for tools/hardware/camera/s22-camera.py (no device needed).

Run: python3 -I -B tools/hardware/camera/test_s22_camera.py
"""
import types
import ctypes
import importlib.util
import io
import json
import os
import struct
import sys
import tempfile
import unittest
import zlib
from contextlib import redirect_stdout
from pathlib import Path

SRC = Path(__file__).resolve().parent / "s22-camera.py"
spec = importlib.util.spec_from_file_location("s22_camera", SRC)
cam = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cam)


# Values produced by compiling a probe with the exact fimc-is build flags
# (aarch64, clang 18, kernel UAPI of the running S22 kernel).
KERNEL = {
    "VIDIOC_QUERYCAP": 2154321408, "VIDIOC_ENUM_FMT": 3225441794,
    "VIDIOC_G_FMT": 3234878980, "VIDIOC_S_FMT": 3234878981,
    "VIDIOC_REQBUFS": 3222558216, "VIDIOC_QUERYBUF": 3227014665,
    "VIDIOC_QBUF": 3227014671, "VIDIOC_DQBUF": 3227014673,
    "VIDIOC_STREAMON": 1074026002, "VIDIOC_STREAMOFF": 1074026003,
    "VIDIOC_S_INPUT": 3221509671, "VIDIOC_S_CTRL": 3221771804,
    "VIDIOC_S_PARM": 3234616854, "DMA_HEAP_IOCTL_ALLOC": 3222816768,
    "DMA_BUF_IOCTL_SYNC": 1074291200,
}


# Not in the 2026-10-08 probe: derived from include/uapi/linux/videodev2.h of the
# fimc_is source (3fca5094) with struct sizes read from fimc-is.ko DWARF
# (v4l2_ext_controls 0x20, v4l2_ext_control 0x14, packed).
UAPI_DERIVED = {"VIDIOC_G_CTRL": 3221771803, "VIDIOC_S_EXT_CTRLS": 3223344712}
IOCTL_NAMES = {v: k for k, v in {**KERNEL, **UAPI_DERIVED}.items()}


class AbiTest(unittest.TestCase):
    def test_ioctl_numbers_match_kernel(self):
        for name, value in KERNEL.items():
            self.assertEqual(getattr(cam, name), value, name)

    def test_i2c_abi_and_read_guard(self):
        self.assertEqual(ctypes.sizeof(cam.I2cMsg), 16)
        self.assertEqual(ctypes.sizeof(cam.I2cRdwr), 16)
        self.assertEqual(cam.I2C_RDWR, 0x0707)
        self.assertEqual((cam.CID_IS_G_DTPSTATUS, cam.CID_IS_G_MIPI_ERR,
                          cam.CID_SENSOR_GET_DIGITAL_GAIN), (0x9A1039, 0x9A103E, 0x9A306D))
        real = cam.RealSys()
        for reg, length in ((0x6000, 2), (0xFCFC, 2), (0x6F12, 2), (0x0100, 3), (-1, 2)):
            with self.assertRaises(ValueError):
                real.i2c_read(-1, 0x10, reg, length)
        for reg, length, _ in cam.GN3_DUMP_REGS:
            self.assertTrue(0 <= reg < 0x6000 and length in (1, 2), hex(reg))
        self.assertEqual(cam.GN3_DUMP_REGS[1][0], cam.GN3_REV_REG)   # page check comes early

    def test_ext_ctrl_abi(self):
        for name, value in UAPI_DERIVED.items():
            self.assertEqual(getattr(cam, name), value, name)
        self.assertEqual((cam.VIDIOC_S_EXT_CTRLS >> 16) & 0x3FFF, 32)
        self.assertEqual((cam.VIDIOC_G_CTRL >> 16) & 0x3FFF, 8)
        self.assertEqual(struct.calcsize(cam.EXT_CTRL_FMT), 20)
        self.assertEqual(struct.calcsize(cam.EXT_CTRLS_FMT), 32)
        # videodev2_exynos_camera.h: SENSOR_BASE = CLASS_CAMERA | 0x3000
        self.assertEqual(cam.CID_SENSOR_SET_EXTENDED_MODE, cam.CID_SENSOR_BASE + 21)
        self.assertEqual(cam.CID_IS_G_STREAM, cam.CID_IS_S_STREAM + 1)
        self.assertEqual((cam.CID_SENSOR_SET_AE_TARGET, cam.CID_SENSOR_SET_ANALOG_GAIN,
                          cam.CID_SENSOR_SET_DIGITAL_GAIN, cam.CID_SENSOR_GET_ANALOG_GAIN),
                         (0x9A3001, 0x9A3012, 0x9A3013, 0x9A306C))
        ext = cam.ExtControls([(cam.CID_SENSOR_SET_AE_TARGET, 20000),
                               (cam.CID_SENSOR_SET_ANALOG_GAIN, 4000)])
        which, count, err, req_fd, _rsvd, ptr = struct.unpack(cam.EXT_CTRLS_FMT, ext.buf)
        self.assertEqual((which, count, req_fd), (0x009A0000, 2, 0))
        raw = ctypes.string_at(ptr, 40)
        self.assertEqual(struct.unpack_from(cam.EXT_CTRL_FMT, raw, 0),
                         (0x9A3001, 0, 0, 20000))
        self.assertEqual(struct.unpack_from(cam.EXT_CTRL_FMT, raw, 20), (0x9A3012, 0, 0, 4000))
        with self.assertRaises(ValueError):
            cam.ExtControls([])

    def test_cis_plan_clamps_and_gain_sequence(self):
        self.assertEqual(cam.cis_exposure_plan(0, 0), ([], {}))
        controls, summary = cam.cis_exposure_plan(20000, 4.0)
        self.assertEqual(controls, [(cam.CID_SENSOR_SET_AE_TARGET, 20000),
                                    (cam.CID_SENSOR_SET_ANALOG_GAIN, 3999),
                                    (cam.CID_SENSOR_SET_ANALOG_GAIN, 4000),
                                    (cam.CID_SENSOR_SET_DIGITAL_GAIN, 1000)])
        self.assertEqual(summary, {"exposure_us": 20000, "exposure_clamped": False,
                                   "again_permille": 4000, "again_clamped": False})
        controls, summary = cam.cis_exposure_plan(10_000_000, 100.0)
        self.assertEqual(controls[0], (cam.CID_SENSOR_SET_AE_TARGET, 30000))
        self.assertEqual(controls[2], (cam.CID_SENSOR_SET_ANALOG_GAIN, 16000))
        self.assertTrue(summary["exposure_clamped"] and summary["again_clamped"])
        controls, summary = cam.cis_exposure_plan(5, 0.5)
        self.assertEqual([v for _, v in controls], [100, 999, 1000, 1000])
        # exposure only: gain registers are not touched; negative values mean "off"
        self.assertEqual(cam.cis_exposure_plan(8000, 0)[0],
                         [(cam.CID_SENSOR_SET_AE_TARGET, 8000)])
        self.assertEqual(cam.cis_exposure_plan(-1, -2.0), ([], {}))
        for bad in (float("nan"), float("inf"), float("-inf")):   # non-finite = not requested
            self.assertEqual(cam.cis_exposure_plan(0, bad), ([], {}))
        self.assertEqual(cam.cis_exposure_plan(0, 1e300)[1]["again_permille"], 16000)

    def test_struct_sizes_match_kernel(self):
        self.assertEqual(struct.calcsize(cam.BUFFER_FMT), 88)   # v4l2_buffer w/ 16-byte timeval
        self.assertEqual(struct.calcsize(cam.PLANE_FMT), 64)
        self.assertEqual(struct.calcsize(cam.REQBUFS_FMT), 20)
        self.assertEqual(struct.calcsize(cam.HEAP_ALLOC_FMT), 24)
        self.assertEqual(len(cam.make_format_mplane(9, 1, 1, 0, 2)), 208)
        self.assertEqual(len(cam.make_streamparm_capture(30)), 204)
        self.assertEqual(len(cam.make_control(1, 2)), 8)
        self.assertEqual(len(cam.make_reqbufs(2, 9)), 20)
        self.assertEqual(len(cam.make_buffer(0, 9, 0, 2)), 88)
        # every ioctl's encoded size equals the struct we hand it
        for req, size in ((cam.VIDIOC_QUERYCAP, 104), (cam.VIDIOC_S_FMT, 208),
                          (cam.VIDIOC_REQBUFS, 20), (cam.VIDIOC_QBUF, 88),
                          (cam.VIDIOC_S_PARM, 204), (cam.VIDIOC_S_CTRL, 8),
                          (cam.VIDIOC_ENUM_FMT, 64)):
            self.assertEqual((req >> 16) & 0x3FFF, size)

    def test_buffer_field_offsets(self):
        buf = cam.make_buffer(3, 10, 0x1122334455667788, 2)
        self.assertEqual(struct.unpack_from("<I", buf, 0)[0], 3)
        self.assertEqual(struct.unpack_from("<I", buf, 4)[0], 10)
        self.assertEqual(struct.unpack_from("<I", buf, 60)[0], cam.V4L2_MEMORY_DMABUF)
        self.assertEqual(struct.unpack_from("<Q", buf, 64)[0], 0x1122334455667788)  # m.planes
        self.assertEqual(struct.unpack_from("<I", buf, 72)[0], 2)                   # length

    def test_format_layout(self):
        fmt = cam.make_format_mplane(9, 2040, 1532, cam.PIX_SBGGR16, 2, 4096)
        self.assertEqual(struct.unpack_from("<IIII", fmt, 8)[:3], (2040, 1532, cam.PIX_SBGGR16))
        self.assertEqual(struct.unpack_from("<I", fmt, 8 + 24)[0], 4096)  # plane_fmt[0].bytesperline
        self.assertEqual(fmt[8 + 180], 2)                                  # num_planes

    def test_capability_parse(self):
        raw = struct.pack("<16s32s32sIII12x", b"exynos-is-ss0", b"exynos-is-ss0", b"", 1,
                          0x04000000 | 0x2000, 0x2000)
        self.assertEqual(len(raw), 104)
        cap = cam.parse_capability(raw)
        self.assertEqual(cap["driver"], "exynos-is-ss0")
        self.assertEqual(cap["device_caps"], 0x2000)

    def test_pablo_encodings(self):
        # Same input the self-test passes to is_sensor_s_input(): VISION, rear, vid 1, leader
        self.assertEqual(cam.s_input_value(1, 0, 1, 1), 0x04000101)
        self.assertEqual(cam.sensor_size_value(2040, 1532), (2040 << 16) | 1532)
        self.assertEqual(cam.fourcc_str(cam.PIX_SBGGR16), "BYR2")
        self.assertEqual(cam.fourcc_str(cam.PIX_SBGGR10P), "pBAA")
        self.assertEqual(cam.fourcc_str(cam.PIX_SRGB36P_SP), "SRP6")
        self.assertEqual(cam.vc0_stride(2040, cam.PIX_SBGGR16), 4096)
        self.assertEqual(cam.vc0_stride(2040, cam.PIX_SBGGR10P), 2560)
        self.assertEqual(cam.leader_image_size(2040, 1532), 14063760)

    def test_shot_layout(self):
        meta = cam.build_shot(2040, 1532, cam.PIX_SBGGR16)
        self.assertEqual(len(meta), 57344)
        self.assertEqual(struct.unpack_from("<I", meta, 37936)[0], 0x56789234)
        cap0 = 88 + 200
        self.assertEqual(struct.unpack_from("<II", meta, cap0), (110, 1))
        self.assertEqual(struct.unpack_from("<II", meta, cap0 + 56), (2040, 1532))
        self.assertEqual(struct.unpack_from("<I", meta, 88 + 4)[0], 0)        # leader.request
        # capture[0].buf (v4l2_buffer) length must stay 0: avoids the kernel BUG() path
        self.assertEqual(struct.unpack_from("<I", meta, cap0 + 112 + 72)[0], 0)
        self.assertEqual(struct.unpack_from("<Q", meta, 6184)[0], 0)          # AE untouched
        manual = cam.build_shot(2040, 1532, cam.PIX_SBGGR16, exposure_us=10000, iso=100)
        self.assertEqual(struct.unpack_from("<Q", manual, 6184)[0], 10_000_000)
        self.assertEqual(struct.unpack_from("<I", manual, 5560)[0], 1)


class Raw10Test(unittest.TestCase):
    PIX = [0x3FF, 0x000, 0x155, 0x2AA, 1, 2, 3, 1020]

    def test_mipi_vector(self):
        packed = cam.pack_raw10_mipi(self.PIX[:4])
        self.assertEqual(packed, bytes([0xFF, 0x00, 0x55, 0xAA, 0x93]))
        self.assertEqual(cam.unpack_raw10_mipi(packed, 4), self.PIX[:4])

    def test_lsb_vector(self):
        packed = cam.pack_raw10_lsb([1, 2, 3, 4])
        self.assertEqual(packed, (1 | 2 << 10 | 3 << 20 | 4 << 30).to_bytes(5, "little"))
        self.assertEqual(cam.unpack_raw10_lsb(packed, 4), [1, 2, 3, 4])

    def test_roundtrips(self):
        self.assertEqual(cam.unpack_raw10_mipi(cam.pack_raw10_mipi(self.PIX), 8), self.PIX)
        self.assertEqual(cam.unpack_raw10_lsb(cam.pack_raw10_lsb(self.PIX), 8), self.PIX)

    def test_detect_packing(self):
        w, h = 32, 12
        rows = [[(x * 7 + y * 3) % 900 + 50 for x in range(w)] for y in range(h)]
        stride = cam.align(w * 10 // 8, 32)
        for packer, name in ((cam.pack_raw10_mipi, "raw10-mipi"), (cam.pack_raw10_lsb, "raw10-lsb")):
            raw = b"".join(packer(r).ljust(stride, b"\0") for r in rows)
            self.assertEqual(cam.detect_packing(raw, w, h, stride), name)


def bayer_frame(w, h, rgb, pattern="GBRG", stride=None, black=64):
    stride = stride or cam.align(w * 2, 32)
    color = {"R": rgb[0] + black, "G": rgb[1] + black, "B": rgb[2] + black}
    out = bytearray(stride * h)
    for y in range(h):
        for x in range(w):
            c = pattern[(y % 2) * 2 + (x % 2)]
            struct.pack_into("<H", out, y * stride + 2 * x, color[c])
    return bytes(out), stride


class DevelopTest(unittest.TestCase):
    def test_binned_demosaic_known_colour(self):
        raw, stride = bayer_frame(16, 8, (536, 236, 86))
        ow, oh, rows, stats = cam.develop(raw, 16, 8, stride, "sbggr16", "GBRG", scale=4,
                                          black=64, wb="none", auto_exposure=False,
                                          white=959, gamma=1.0)
        self.assertEqual((ow, oh), (4, 2))
        self.assertEqual(rows[0][:3], bytes([143, 63, 23]))
        self.assertEqual(len(set(rows)), 1)

    def test_wrong_pattern_swaps_channels(self):
        raw, stride = bayer_frame(16, 8, (536, 236, 86))
        _, _, rows, _ = cam.develop(raw, 16, 8, stride, "sbggr16", "GRBG", scale=2, black=64,
                                    wb="none", auto_exposure=False, white=959, gamma=1.0)
        self.assertEqual(rows[0][:3], bytes([23, 63, 143]))

    def test_grayworld_neutralises(self):
        raw, stride = bayer_frame(32, 16, (600, 300, 120))
        _, _, rows, stats = cam.develop(raw, 32, 16, stride, "sbggr16", "GBRG", scale=4)
        r, g, b = rows[0][:3]
        self.assertLessEqual(max(r, g, b) - min(r, g, b), 2)
        self.assertGreater(g, 200)   # auto exposure stretches to near white
        self.assertAlmostEqual(stats["gains"][0], 0.5, places=3)

    def test_quadrant_image(self):
        # left half red, right half blue: binned output must keep them apart
        w, h = 16, 8
        stride = cam.align(w * 2, 32)
        out = bytearray(stride * h)
        for y in range(h):
            for x in range(w):
                c = "GBRG"[(y % 2) * 2 + (x % 2)]
                val = {"R": 900 if x < 8 else 64, "G": 64, "B": 64 if x < 8 else 900}[c]
                struct.pack_into("<H", out, y * stride + 2 * x, val)
        ow, oh, rows, _ = cam.develop(bytes(out), w, h, stride, "sbggr16", scale=4,
                                      wb="none", auto_exposure=False, white=900 - 64, gamma=1.0)
        self.assertEqual(rows[0][0:3], bytes([255, 0, 0]))
        self.assertEqual(rows[0][-3:], bytes([0, 0, 255]))

    def test_raw10p_develop(self):
        w, h = 16, 8
        rows16 = []
        for y in range(h):
            rows16.append([{"G": 300, "B": 150, "R": 600}["GBRG"[(y % 2) * 2 + x % 2]] for x in range(w)])
        stride = cam.vc0_stride(w, cam.PIX_SBGGR10P)
        raw = b"".join(cam.pack_raw10_mipi(r).ljust(stride, b"\0") for r in rows16)
        _, _, rows, stats = cam.develop(raw, w, h, stride, "raw10p", scale=2, black=0,
                                        wb="none", auto_exposure=False, white=1023, gamma=1.0)
        self.assertEqual(stats["fmt"], "raw10-mipi")
        self.assertEqual(rows[0][:3], bytes([150, 75, 37]))


class PngTest(unittest.TestCase):
    def test_png_decodes(self):
        rows = [bytes([10, 20, 30] * 3), bytes([40, 50, 60] * 3)]
        data = cam.png_bytes(3, 2, rows)
        self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
        pos, chunks = 8, {}
        while pos < len(data):
            n = struct.unpack(">I", data[pos:pos + 4])[0]
            tag, body = data[pos + 4:pos + 8], data[pos + 8:pos + 8 + n]
            crc = struct.unpack(">I", data[pos + 8 + n:pos + 12 + n])[0]
            self.assertEqual(crc, zlib.crc32(tag + body))
            chunks.setdefault(tag, b"")
            chunks[tag] += body
            pos += 12 + n
        self.assertEqual(struct.unpack(">IIBBBBB", chunks[b"IHDR"]), (3, 2, 8, 2, 0, 0, 0))
        raw = zlib.decompress(chunks[b"IDAT"])
        self.assertEqual(raw, b"\x00" + rows[0] + b"\x00" + rows[1])
        self.assertIn(b"IEND", chunks)

    def test_ppm(self):
        self.assertEqual(cam.ppm_bytes(1, 1, [b"\x01\x02\x03"]), b"P6\n1 1\n255\n\x01\x02\x03")

    def test_geometry_guard(self):
        with self.assertRaises(ValueError):
            cam.png_bytes(2, 1, [b"\x00" * 3])


# --------------------------------------------------------------------------
# Fake Pablo device: enforces the ordering rules found in the kernel source.
# --------------------------------------------------------------------------
class FakeSys:
    W, H = 16, 8

    def __init__(self, deliver=True, fw=True, names=None, fail=None, again_cache=0,
                 g_stream=None, raise_on=None):
        self.deliver = deliver
        # GN3 model: registers reset at every open; the analog-gain cache in
        # cis_data persists across sessions (CONFIG_CAMERA_VENDER_MCD: no memset).
        self.regs = {"cit_us": None, "again": None, "dgain": None}
        self.again_cache = again_cache
        self.g_stream = g_stream          # force IS_G_STREAM's answer
        self.raise_on = raise_on          # ioctl name -> exception instance
        self.ext_writes = []
        self.i2c_regs = {0x0000: 0x08D3, 0x0002: 0xC000, 0x0005: 0x1700, 0x0100: 0x0103,
                         0x0112: 0x0A0A, 0x0340: 0x1B50, 0x0342: 0x23C0}
        self.i2c_reads = []      # (reg, length, front_streaming)
        self.i2c_fail = None     # (reg, errno)
        self.i2c_open_errno = None
        self.fail = fail or {}
        self.next_fd = 100
        self.fds = {}            # fd -> path
        self.bufs = {}           # dmabuf fd -> bytearray
        self.closed = []
        self.calls = []
        self.queued = {"leader": [], "vc": []}
        self.stream = {"leader": False, "vc": False, "front": False}
        self.s_input = False
        self.sysfs = {
            "/sys/class/video4linux/video101/name": "exynos-is-ss0\n",
            "/sys/class/video4linux/video210/name": "exynos-is-ss0vc0\n",
            cam.SELFTEST_PARAM: "> Set\nact 0 position 0 width 0 height 0 fps 0 ex 0\n",
        }
        if names:
            self.sysfs.update(names)
        self.files = {"/system/vendor/firmware/is_mcu_fw.bin"} if fw else set()
        self.frame, self.stride = bayer_frame(self.W, self.H, (536, 236, 86))
        self.seq = 0

    def _new_fd(self):
        self.next_fd += 1
        return self.next_fd

    def _role(self, fd):
        return "leader" if self.fds.get(fd, "").endswith(("video101", "video102")) else "vc"

    def open(self, path):
        fd = self._new_fd()
        self.fds[fd] = path
        self.calls.append(("open", path))
        return fd

    def close(self, fd):
        self.calls.append(("close", self.fds.get(fd, "dmabuf")))
        self.closed.append(fd)

    def heap_alloc(self, size):
        fd = self._new_fd()
        self.bufs[fd] = bytearray(size)
        return fd

    def mmap(self, fd, size):
        return self.bufs[fd]

    def dmabuf_sync(self, fd, flags):
        pass

    def read_text(self, path):
        return self.sysfs.get(path)

    def exists(self, path):
        return path in self.files

    def listdir(self, path):
        prefix = path.rstrip("/") + "/"
        return sorted({k[len(prefix):].split("/")[0] for k in self.sysfs if k.startswith(prefix)})

    def i2c_open(self, bus):
        self.calls.append(("i2c_open", str(bus)))
        if self.i2c_open_errno:
            raise OSError(self.i2c_open_errno, os.strerror(self.i2c_open_errno))
        fd = self._new_fd()
        self.fds[fd] = f"/dev/i2c-{bus}"
        return fd

    def i2c_write_0be4(self, fd, addr, reg, value):
        assert (addr, reg, value) == (0x10, 0x0BE4, 0x0001), (addr, reg, value)
        self.i2c_writes = getattr(self, "i2c_writes", []) + [(reg, value, self.stream["front"])]
        self.i2c_regs[reg] = value

    def i2c_read(self, fd, addr, reg, length):
        assert addr == 0x10 and 0 <= reg < 0x6000 and length in (1, 2), (addr, reg, length)
        self.i2c_reads.append((reg, length, self.stream["front"]))
        if self.i2c_fail and self.i2c_fail[0] == reg:
            raise OSError(self.i2c_fail[1], os.strerror(self.i2c_fail[1]))
        value = self.i2c_regs.get(reg, 0)
        if reg == 0x0204 and self.regs["again"] is not None:
            value = self.regs["again"]
        return value.to_bytes(2, "big")[:length] if length == 2 else bytes([value >> 8])

    def select(self, rfds, wfds, timeout):
        r = [fd for fd in rfds if self.deliver and self.stream["front"] and self.queued["vc"]]
        w = [fd for fd in wfds if self.stream["leader"] and self.queued["leader"]]
        return r, w

    def _planes(self, buf):
        info = cam.parse_buffer(buf)
        raw = ctypes.string_at(info["m"], 64 * info["length"])
        return info, [struct.unpack_from(cam.PLANE_FMT, raw, 64 * i) for i in range(info["length"])]

    def ioctl(self, fd, req, buf):
        role = self._role(fd)
        name = IOCTL_NAMES.get(req, hex(req))
        self.calls.append((name, role))
        if self.raise_on and name in self.raise_on:
            raise self.raise_on[name]
        if (req >> 16) & 0x3FFF != len(buf):
            raise AssertionError(f"{name}: size {len(buf)} != encoded {(req >> 16) & 0x3FFF}")
        if (name, role) in self.fail:
            raise OSError(self.fail[(name, role)], os.strerror(self.fail[(name, role)]))
        if name == "VIDIOC_S_INPUT":
            self.s_input_value = struct.unpack("<I", buf)[0]
            if self.s_input_value not in (0x04000101, 0x04010201):
                raise OSError(22, "bad input")
            self.s_input = True
        elif name == "VIDIOC_S_FMT":
            if role == "vc":
                bpl = struct.unpack_from("<I", buf, 8 + 24)[0]
                if bpl != self.stride:
                    raise OSError(22, "stride")
        elif name == "VIDIOC_QBUF":
            info, planes = self._planes(buf)
            if info["memory"] != cam.V4L2_MEMORY_DMABUF or len(planes) != 2:
                raise OSError(22, "memory")
            img_fd, meta_fd = planes[0][2], planes[1][2]
            if role == "leader":
                meta = self.bufs[meta_fd]
                self.__dict__.setdefault("shot_vids", []).append(struct.unpack_from(
                    "<II", meta, cam.OFF_NODE_GROUP)[0:1] + struct.unpack_from(
                    "<I", meta, cam.OFF_NODE_GROUP + cam.NODE_SIZE))
                if struct.unpack_from("<I", meta, cam.OFF_SHOT_MAGIC)[0] != cam.SHOT_MAGIC_NUMBER:
                    raise OSError(22, "Shot magic number error")
                if planes[0][0] != planes[0][1]:
                    raise OSError(22, "output bytesused")
            elif planes[0][1] < self.stride * self.H:
                raise OSError(22, "image plane too small")
            else:
                self.__dict__.setdefault("vc_plane_lengths", []).append(planes[0][1])
            self.queued[role].append((info["index"], img_fd))
        elif name == "VIDIOC_STREAMON":
            if role == "vc" and (not self.s_input or self.stream["leader"]):
                raise OSError(22, "leader already started / no s_input")
            self.stream[role] = True
        elif name == "VIDIOC_STREAMOFF":
            self.stream[role] = False
            self.queued[role] = []
        elif name == "VIDIOC_S_CTRL":
            cid, value = struct.unpack("<Ii", buf)
            if cid == cam.CID_IS_S_STREAM:
                self.__dict__.setdefault("s_stream_values", []).append(value)
                self.stream["front"] = bool(value & 0xF)
        elif name == "VIDIOC_G_CTRL":
            cid, _ = struct.unpack("<Ii", buf)
            if cid == cam.CID_IS_G_STREAM:
                value = self.g_stream if self.g_stream is not None else int(self.stream["front"])
            elif cid == cam.CID_SENSOR_GET_ANALOG_GAIN:
                code = self.regs["again"] or 0
                value = (code * 1000 + 16) // 32              # sensor_cis_calc_again_permile
            else:
                raise OSError(22, "unknown g_ctrl")
            struct.pack_into("<Ii", buf, 0, cid, value)
        elif name == "VIDIOC_S_EXT_CTRLS":
            if role != "leader" or not self.s_input:
                raise OSError(22, "subdev_module NULL")    # FIMC_BUG -> -EINVAL
            which, count, _e, _r, _rs, ptr = struct.unpack(cam.EXT_CTRLS_FMT, bytes(buf))
            raw = ctypes.string_at(ptr, 20 * count)
            for i in range(count):
                cid, _size, _r2, value = struct.unpack_from(cam.EXT_CTRL_FMT, raw, 20 * i)
                if which not in (0, cid & 0x0FFF0000):
                    struct.pack_into("<I", buf, 8, count)
                    raise OSError(22, "control class")
                self.ext_writes.append((cid, value, self.stream["front"]))
                if cid == cam.CID_SENSOR_SET_AE_TARGET:
                    self.regs["cit_us"] = value
                elif cid in (cam.CID_SENSOR_SET_ANALOG_GAIN, cam.CID_SENSOR_SET_DIGITAL_GAIN):
                    if cid == cam.CID_SENSOR_SET_ANALOG_GAIN and value != self.again_cache:
                        self.regs["again"] = min(max((value * 32 + 500) // 1000, 0x20), 0x800)
                        self.again_cache = value
                    else:   # is-device-module-base.c: case ANALOG_GAIN falls through
                        self.regs["dgain"] = value
                else:
                    struct.pack_into("<I", buf, 8, i)
                    raise OSError(22, "Unknown CID")
        elif name == "VIDIOC_DQBUF":
            if not self.queued[role]:
                raise OSError(11, "EAGAIN")
            index, img_fd = self.queued[role].pop(0)
            if role == "vc":
                self.bufs[img_fd][:len(self.frame)] = self.frame
                self.seq += 1
            struct.pack_into("<II", buf, 0, index, struct.unpack_from("<I", buf, 4)[0])
            struct.pack_into("<I", buf, 56, self.seq)


def make_args(cmd, *extra):
    base = {"capture": ["capture", "--out", "OUT", "--width", "16", "--height", "8"],
            "record": ["record", "--out", "OUT", "--width", "16", "--height", "8"]}[cmd]
    return cam.build_parser().parse_args(list(base) + list(extra))


def run_quiet(func, *a, **kw):
    out = io.StringIO()
    with redirect_stdout(out):
        rc = func(*a, **kw)
    return rc, out.getvalue()


class CaptureHelpers(unittest.TestCase):
    """setUp/teardown and run helpers shared by the fake-device capture tests."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def _capture(self, fake, *extra):
        out = os.path.join(self.tmp.name, "shot.png")
        raw = os.path.join(self.tmp.name, "shot.raw")
        args = make_args("capture", "--raw", raw, "--scale", "2", "--timeout", "0.01",
                         "--first-timeout", "0.01", *extra)
        args.out = out
        rc, text = run_quiet(cam.cmd_capture, args, fake, alarm=False)
        return rc, json.loads(text.strip().splitlines()[-1]), out, raw

    def _assert_rolled_back(self, fake):
        names = [c[0] for c in fake.calls]
        for fd in [fd for fd, p in fake.fds.items()]:
            self.assertIn(fd, fake.closed)
        for fd in fake.bufs:
            self.assertIn(fd, fake.closed)
        self.assertFalse(any(fake.stream.values()), fake.stream)
        self.assertLess(names.index("close") if "close" in names else 0, len(names))


class FakeCaptureTest(CaptureHelpers):
    def test_capture_ok_and_order(self):
        fake = FakeSys()
        rc, res, out, raw = self._capture(fake, "--skip", "2")
        self.assertEqual(rc, 0, res)
        self.assertTrue(res["ok"])
        for key in ("ok", "path", "width", "height", "format", "ms", "node"):
            self.assertIn(key, res)
        self.assertEqual((res["width"], res["height"], res["format"]), (8, 4, "png"))
        self.assertEqual(res["node"], "/dev/video210")
        self.assertEqual(res["frames"], 3)
        self.assertTrue(Path(out).read_bytes().startswith(b"\x89PNG"))
        self.assertEqual(os.path.getsize(raw), fake.stride * fake.H)
        sidecar = json.loads(Path(raw + ".json").read_text())
        self.assertEqual(sidecar["stride"], fake.stride)
        seq = [c for c in fake.calls if c[0] != "VIDIOC_DQBUF"]
        order = [c[0] + ":" + c[1] for c in seq]
        self.assertLess(order.index("VIDIOC_STREAMON:vc"), order.index("VIDIOC_STREAMON:leader"))
        self.assertLess(order.index("VIDIOC_S_INPUT:leader"), order.index("VIDIOC_S_FMT:leader"))
        stream_off_ctl = max(i for i, c in enumerate(order) if c == "VIDIOC_S_CTRL:leader")
        self.assertLess(stream_off_ctl, order.index("VIDIOC_STREAMOFF:leader"))
        self.assertEqual(fake.calls[-1][0], "close")
        self._assert_rolled_back(fake)
        # VC0 is closed before the leader
        closes = [c[1] for c in fake.calls if c[0] == "close" and c[1] != "dmabuf"]
        self.assertEqual(closes, ["/dev/video210", "/dev/video101"])

    def test_review_safety_changes(self):
        """2026-10-08 review: blocking stream start, VC0 guard pages, real-device geometry lock."""
        fake = FakeSys()
        rc, res, out, raw = self._capture(fake, "--skip", "1")
        self.assertEqual(rc, 0, res)
        self.assertEqual(fake.s_stream_values[0], 1)          # on, noblock bit clear
        self.assertNotIn(1 | (1 << cam.SENSOR_NOBLOCK_SHIFT), fake.s_stream_values)
        self.assertTrue(all(n >= fake.stride * fake.H + cam.VC0_GUARD_BYTES
                            for n in fake.vc_plane_lengths), fake.vc_plane_lengths)
        self.assertNotIn("/vendor/firmware", cam.MCU_FW_DIRS)
        args = types.SimpleNamespace(width=1920, height=1080, fps=30)
        with self.assertRaises(cam.CameraError):
            cam.run_stream(cam.RealSys.__new__(cam.RealSys), args, lambda r, i: True, alarm=False)

    def test_timeout_still_rolls_back(self):
        fake = FakeSys(deliver=False)
        rc, res, _, _ = self._capture(fake)
        self.assertEqual(rc, 1)
        self.assertIn("no VC0 frame", res["error"])
        self._assert_rolled_back(fake)
        names = [c[0] for c in fake.calls]
        self.assertIn("VIDIOC_STREAMOFF", names)

    def test_midway_failure_rolls_back(self):
        fake = FakeSys(fail={("VIDIOC_STREAMON", "leader"): 22})
        rc, res, _, _ = self._capture(fake)
        self.assertEqual(rc, 1)
        self.assertIn("STREAMON leader", res["error"])
        self._assert_rolled_back(fake)
        names = [(c[0], c[1]) for c in fake.calls]
        self.assertIn(("VIDIOC_STREAMOFF", "vc"), names)
        self.assertNotIn(("VIDIOC_STREAMOFF", "leader"), names)

    def test_teardown_errors_do_not_stop_rollback(self):
        fake = FakeSys(fail={("VIDIOC_STREAMOFF", "leader"): 5})
        rc, res, _, _ = self._capture(fake)
        self.assertEqual(rc, 0)
        self.assertTrue(any("STREAMOFF leader" in e for e in res["teardown_errors"]))
        self.assertIn(("VIDIOC_STREAMOFF", "vc"), [(c[0], c[1]) for c in fake.calls])
        self.assertIn(("close", "/dev/video101"), fake.calls)

    def test_refuses_without_firmware(self):
        fake = FakeSys(fw=False)
        rc, res, _, _ = self._capture(fake)
        self.assertEqual(rc, 1)
        self.assertIn("is_mcu_fw.bin", res["error"])
        self.assertFalse([c for c in fake.calls if c[0] == "open"])

    def test_refuses_wrong_node_name(self):
        fake = FakeSys(names={"/sys/class/video4linux/video210/name": "exynos-is-30s\n"})
        rc, res, _, _ = self._capture(fake)
        self.assertEqual(rc, 1)
        self.assertFalse([c for c in fake.calls if c[0] == "open"])

    def test_kernel_bug_vc0_name_only_on_video210(self):
        bug = cam.VC0_NAME_KERNEL_BUG + "\n"
        fake = FakeSys(names={"/sys/class/video4linux/video210/name": bug})
        rc, res, _, _ = self._capture(fake)
        self.assertEqual(rc, 0, res)
        fake = FakeSys(names={"/sys/class/video4linux/video214/name": bug})
        rc, res, _, _ = self._capture(fake, "--vc0", "/dev/video214")
        self.assertEqual(rc, 1)
        self.assertFalse([c for c in fake.calls if c[0] == "open"])

    def test_refuses_active_selftest(self):
        fake = FakeSys(names={cam.SELFTEST_PARAM: "act 1 position 0\n"})
        rc, res, _, _ = self._capture(fake)
        self.assertEqual(rc, 1)
        self.assertIn("self-test", res["error"])

    def test_record(self):
        fake = FakeSys()
        args = make_args("record", "--seconds", "0", "--fps-limit", "0", "--scale", "2",
                         "--first-timeout", "0.01", "--timeout", "0.01")
        args.out = os.path.join(self.tmp.name, "seq")
        rc, text = run_quiet(cam.cmd_record, args, fake, alarm=False)
        res = json.loads(text.strip().splitlines()[-1])
        self.assertEqual(rc, 0, res)
        self.assertGreaterEqual(res["frames"], 1)
        self.assertTrue(os.path.exists(os.path.join(args.out, "frame_00000.png")))
        self.assertFalse(os.path.exists(os.path.join(args.out, "frame_00000.raw")))
        self._assert_rolled_back_record(fake)

    def _assert_rolled_back_record(self, fake):
        self.assertFalse(any(fake.stream.values()))
        for fd in fake.fds:
            self.assertIn(fd, fake.closed)


class CisExposureTest(CaptureHelpers):
    """Exposure review 2026-10-09: manual GN3 exposure/gain via S_EXT_CTRLS."""

    CIS = ("--cis-exposure-us", "20000", "--cis-again", "4")

    def _order(self, fake):
        return [c[0] + ":" + c[1] for c in fake.calls]

    def test_applied_once_after_blocking_stream_on(self):
        fake = FakeSys()
        rc, res, _, _ = self._capture(fake, *self.CIS)
        self.assertEqual(rc, 0, res)
        order = self._order(fake)
        self.assertEqual(order.count("VIDIOC_S_EXT_CTRLS:leader"), 1)
        stream_on = [i for i, c in enumerate(order) if c == "VIDIOC_S_CTRL:leader"][2]
        self.assertEqual(fake.s_stream_values[0], 1)
        ext = order.index("VIDIOC_S_EXT_CTRLS:leader")
        self.assertLess(stream_on, ext)
        self.assertLess(order.index("VIDIOC_G_CTRL:leader"), ext)     # IS_G_STREAM gate
        self.assertLess(ext, order.index("VIDIOC_DQBUF:vc"))
        self.assertLess(ext, order.index("VIDIOC_STREAMOFF:leader"))
        self.assertTrue(all(streaming for _, _, streaming in fake.ext_writes))
        self.assertEqual(fake.regs, {"cit_us": 20000, "again": 128, "dgain": 1000})
        self.assertEqual(res["cis"]["applied"], True)
        self.assertIsNone(res["cis"]["error"])
        self.assertEqual(res["cis"]["readback_again_permille"], 4000)
        self.assertEqual(res["cis"]["requested"]["exposure_us"], 20000)
        self._assert_rolled_back(fake)

    def test_cached_gain_fallthrough_is_neutralised(self):
        for cache in (0, 3999, 4000):
            fake = FakeSys(again_cache=cache)
            rc, res, _, _ = self._capture(fake, *self.CIS)
            self.assertEqual(rc, 0, res)
            self.assertEqual((fake.regs["again"], fake.regs["dgain"]), (128, 1000), cache)

    def test_ioctl_failure_keeps_sensor_defaults(self):
        fake = FakeSys(fail={("VIDIOC_S_EXT_CTRLS", "leader"): 22})
        rc, res, _, _ = self._capture(fake, *self.CIS)
        self.assertEqual(rc, 0, res)                 # capture still succeeds
        self.assertTrue(res["ok"])
        self.assertFalse(res["cis"]["applied"])
        self.assertIn("not retried", res["cis"]["error"])
        self.assertEqual(fake.regs, {"cit_us": None, "again": None, "dgain": None})
        self.assertNotIn("VIDIOC_G_CTRL:leader",
                         self._order(fake)[self._order(fake).index("VIDIOC_S_EXT_CTRLS:leader"):])
        self._assert_rolled_back(fake)

    def test_gate_failure_is_no_change(self):
        for fake in (FakeSys(g_stream=0), FakeSys(g_stream=-22),
                     FakeSys(fail={("VIDIOC_G_CTRL", "leader"): 25})):
            rc, res, _, _ = self._capture(fake, *self.CIS)
            self.assertEqual(rc, 0, res)
            self.assertNotIn("VIDIOC_S_EXT_CTRLS:leader", self._order(fake))
            self.assertFalse(res["cis"]["applied"])
            self.assertIn("no change", res["cis"]["error"])
            self._assert_rolled_back(fake)

    def test_not_sent_when_start_fails(self):
        fake = FakeSys(fail={("VIDIOC_STREAMON", "leader"): 22})
        rc, res, _, _ = self._capture(fake, *self.CIS)
        self.assertEqual(rc, 1)
        self.assertNotIn("VIDIOC_S_EXT_CTRLS:leader", self._order(fake))
        self.assertNotIn("VIDIOC_G_CTRL:leader", self._order(fake))
        self.assertFalse(res["cis"]["applied"])
        self._assert_rolled_back(fake)

    def test_deadline_during_apply_still_rolls_back(self):
        fake = FakeSys(raise_on={"VIDIOC_S_EXT_CTRLS": cam.DeadlineExceeded("SIGALRM")})
        rc, res, _, _ = self._capture(fake, *self.CIS)
        self.assertEqual(rc, 1)
        self.assertIn("SIGALRM", res["error"])
        self.assertFalse(res["cis"]["applied"])
        self.assertIn("VIDIOC_STREAMOFF:leader", self._order(fake))
        self._assert_rolled_back(fake)

    def test_readback_failure_is_reported_not_fatal(self):
        class R(FakeSys):
            def ioctl(self, fd, req, buf):
                if req == cam.VIDIOC_G_CTRL and struct.unpack_from("<I", buf)[0] == \
                        cam.CID_SENSOR_GET_ANALOG_GAIN:
                    self.calls.append(("VIDIOC_G_CTRL", "leader"))
                    raise OSError(121, "Remote I/O error")
                return super().ioctl(fd, req, buf)
        fake = R()
        rc, res, _, _ = self._capture(fake, *self.CIS)
        self.assertEqual(rc, 0, res)
        self.assertTrue(res["cis"]["applied"])
        self.assertIn("readback_error", res["cis"])

    def test_exposure_only_leaves_gain_alone(self):
        fake = FakeSys()
        rc, res, _, _ = self._capture(fake, "--cis-exposure-us", "50000")
        self.assertEqual(rc, 0, res)
        self.assertEqual(fake.regs, {"cit_us": 30000, "again": None, "dgain": None})
        self.assertTrue(res["cis"]["requested"]["exposure_clamped"])
        self.assertNotIn("readback_again_permille", [k for k, v in res["cis"].items()
                                                      if v is not None])

    def test_default_run_sends_no_sensor_writes(self):
        fake = FakeSys()
        rc, res, _, _ = self._capture(fake)
        self.assertEqual(rc, 0, res)
        names = [c[0] for c in fake.calls]
        self.assertNotIn("VIDIOC_S_EXT_CTRLS", names)
        self.assertNotIn("VIDIOC_G_CTRL", names)
        self.assertNotIn("cis", res)

    def test_record_applies_too(self):
        fake = FakeSys()
        args = make_args("record", "--seconds", "0", "--fps-limit", "0", "--scale", "2",
                         "--first-timeout", "0.01", "--timeout", "0.01", *self.CIS)
        args.out = os.path.join(self.tmp.name, "seq")
        rc, text = run_quiet(cam.cmd_record, args, fake, alarm=False)
        res = json.loads(text.strip().splitlines()[-1])
        self.assertEqual(rc, 0, res)
        self.assertTrue(res["cis"]["applied"])
        self.assertEqual(fake.regs["cit_us"], 20000)


class CisDumpTest(CaptureHelpers):
    """Review v2 2026-10-09: read-only GN3 state dump."""

    COMPAT = {"/sys/bus/i2c/devices/7-0010/of_node/compatible": "samsung,exynos-is-cis-gn3\0"}

    def test_control_dump_after_start_and_before_stop(self):
        fake = FakeSys()
        rc, res, _, _ = self._capture(fake, "--cis-dump", "--cis-exposure-us", "10000",
                                      "--cis-again", "4")
        self.assertEqual(rc, 0, res)
        for tag in ("after_start", "before_stop"):
            ctl = res["dump"][tag]["controls"]
            self.assertEqual(ctl["is_g_stream"], 1)
            self.assertEqual(ctl["again_permille"], 4000)
            self.assertIn("csis_error_id", ctl)
        self.assertNotIn("i2c", res["dump"]["after_start"])
        self.assertNotIn("i2c_open", [c[0] for c in fake.calls])
        self._assert_rolled_back(fake)

    def test_i2c_dump_reads_only_while_streaming(self):
        fake = FakeSys(names=self.COMPAT)
        rc, res, _, _ = self._capture(fake, "--cis-i2c-bus", "7", "--cis-again", "4")
        self.assertEqual(rc, 0, res)
        i2c = res["dump"]["after_start"]["i2c"]
        self.assertNotIn("error", i2c)
        self.assertEqual(i2c["0x0002 revision"], "0xc000")
        self.assertEqual(i2c["0x0100 mode_select|orientation"], "0x0103")
        self.assertEqual(i2c["0x0204 analog_gain"], "0x0080")
        self.assertEqual(i2c["0x0005 frame_count"], "0x17")
        self.assertEqual(len(fake.i2c_reads), 2 * len(cam.GN3_DUMP_REGS))
        self.assertTrue(all(streaming for _, _, streaming in fake.i2c_reads))
        self.assertIn("before_stop", res["dump"])
        self._assert_rolled_back(fake)        # i2c fds closed too

    def test_i2c_bus_must_be_the_gn3(self):
        for names in (None, {"/sys/bus/i2c/devices/7-0010/of_node/compatible":
                             "samsung,exynos-is-cis-imx754\0"}):
            fake = FakeSys(names=names)
            rc, res, _, _ = self._capture(fake, "--cis-i2c-bus", "7")
            self.assertEqual(rc, 1)
            self.assertIn("refusing the register dump", res["error"])
            self.assertFalse([c for c in fake.calls if c[0] in ("open", "i2c_open")])

    def test_revision_mismatch_stops_dump(self):
        fake = FakeSys(names=self.COMPAT)
        fake.i2c_regs[0x0002] = 0x1234
        rc, res, _, _ = self._capture(fake, "--cis-i2c-bus", "7")
        self.assertEqual(rc, 0, res)
        i2c = res["dump"]["after_start"]["i2c"]
        self.assertIn("page is not 0x4000", i2c["error"])
        self.assertEqual(len(fake.i2c_reads), 4)        # 0x0000, 0x0002 per snapshot

    def test_i2c_errors_never_fail_the_capture(self):
        fake = FakeSys(names=self.COMPAT)
        fake.i2c_fail = (0x0202, 121)
        rc, res, _, _ = self._capture(fake, "--cis-i2c-bus", "7")
        self.assertEqual(rc, 0, res)
        self.assertIn("0x0202", res["dump"]["after_start"]["i2c"]["error"])
        fake = FakeSys(names=self.COMPAT)
        fake.i2c_open_errno = 2
        rc, res, _, _ = self._capture(fake, "--cis-i2c-bus", "7")
        self.assertEqual(rc, 0, res)
        self.assertIn("/dev/i2c-7", res["dump"]["after_start"]["i2c"]["error"])

    def test_no_dump_when_start_fails(self):
        fake = FakeSys(names=self.COMPAT, fail={("VIDIOC_STREAMON", "leader"): 22})
        rc, res, _, _ = self._capture(fake, "--cis-dump", "--cis-i2c-bus", "7")
        self.assertEqual(rc, 1)
        self.assertEqual(fake.i2c_reads, [])
        self.assertNotIn("VIDIOC_G_CTRL", [c[0] for c in fake.calls])
        self._assert_rolled_back(fake)


class RailsAndEchoTest(CaptureHelpers):
    """Review v3 2026-10-09: rail snapshot and setfile echo (both read-only)."""

    COMPAT = CisDumpTest.COMPAT

    def _rails(self, state="enabled"):
        base = "/sys/class/regulator/regulator.%d/"
        names = {}
        for i, (name, uv) in enumerate((("VDDA_2.2V_CAM", "2200000"), ("VDDIO_1.8V_CAM", "1800000"),
                                        ("BUCK_OTHER", "1000000"))):
            names.update({base % i + "name": name + "\n", base % i + "state": state + "\n",
                          base % i + "microvolts": uv + "\n", base % i + "num_users": "1\n"})
        return names

    def test_rail_snapshots_cover_the_whole_session(self):
        fake = FakeSys(names=self._rails())
        rc, res, _, _ = self._capture(fake, "--rails")
        self.assertEqual(rc, 0, res)
        self.assertEqual(set(res["dump"]), {"before_open", "after_start", "before_stop",
                                            "after_close"})
        rails = res["dump"]["after_start"]["rails"]
        self.assertEqual(rails["VDDA_2.2V_CAM"], {"state": "enabled", "microvolts": "2200000",
                                                  "num_users": "1", "node": "regulator.0"})
        self.assertEqual(rails["S2MPB02_BB"], "not found")
        self.assertNotIn("BUCK_OTHER", rails)
        self.assertFalse([c for c in fake.calls if c[0] in ("i2c_open", "VIDIOC_G_CTRL")])
        self._assert_rolled_back(fake)

    def test_rail_snapshot_errors_never_fail_capture(self):
        class Broken(FakeSys):
            def listdir(self, path):
                raise RuntimeError("sysfs gone")
        fake = Broken()
        rc, res, _, _ = self._capture(fake, "--rails")
        self.assertEqual(rc, 0, res)
        self.assertIn("sysfs gone", res["dump"]["after_start"]["rails"]["error"])
        self._assert_rolled_back(fake)

    def test_setfile_echo_reports_mismatches(self):
        fake = FakeSys(names=self.COMPAT)
        fake.i2c_regs.update({0x0136: 0x1300, 0x0B04: 0x0001, 0x0008: 0x0040})
        rc, res, _, _ = self._capture(fake, "--cis-i2c-bus", "7", "--cis-i2c-extended")
        self.assertEqual(rc, 0, res)
        i2c = res["dump"]["after_start"]["i2c"]
        self.assertEqual(i2c["setfile_echo"]["0x0008"], "0x0040")
        self.assertEqual(i2c["setfile_echo"]["0x0006"], "0x00")
        bad = " ".join(i2c["setfile_mismatch"])
        self.assertNotIn("0x0136", bad)
        self.assertNotIn("0x0b04", bad)
        self.assertIn("0x011c: 0x0000 (setfile 0x0101)", bad)
        n = len(cam.GN3_DUMP_REGS) + len(cam.GN3_SETFILE_ECHO)
        self.assertEqual(len(fake.i2c_reads), 2 * n)
        self.assertTrue(all(0 <= r < 0x6000 and s for r, _, s in fake.i2c_reads))

    def test_echo_skipped_after_page_check_failure_and_needs_bus(self):
        fake = FakeSys(names=self.COMPAT)
        fake.i2c_regs[0x0002] = 0x1234
        rc, res, _, _ = self._capture(fake, "--cis-i2c-bus", "7", "--cis-i2c-extended")
        self.assertEqual(rc, 0, res)
        self.assertNotIn("setfile_echo", res["dump"]["after_start"]["i2c"])
        self.assertEqual(len(fake.i2c_reads), 4)
        fake = FakeSys()
        rc, res, _, _ = self._capture(fake, "--cis-i2c-extended")
        self.assertEqual(rc, 1)
        self.assertIn("needs --cis-i2c-bus", res["error"])
        self.assertFalse([c for c in fake.calls if c[0] == "open"])

    def test_echo_table_is_read_only_safe(self):
        for reg, length, _ in cam.GN3_SETFILE_ECHO:
            self.assertTrue(0 <= reg < 0x6000 and length in (1, 2), hex(reg))
        regs = [r for r, _, _ in cam.GN3_SETFILE_ECHO]
        self.assertEqual(len(regs), len(set(regs)))


class Test0BE4(CaptureHelpers):
    """Review v4 2026-10-09: the single gated sensor write."""

    COMPAT = CisDumpTest.COMPAT
    FLAGS = ("--cis-i2c-bus", "7", "--cis-test-0be4", "--allow-sensor-write")

    def test_writes_once_mid_stream_and_reads_back(self):
        fake = FakeSys(names=self.COMPAT)
        rc, res, _, _ = self._capture(fake, *self.FLAGS)
        self.assertEqual(rc, 0, res)
        self.assertEqual(fake.i2c_writes, [(0x0BE4, 0x0001, True)])
        self.assertEqual(res["test_0be4"], {"done": True, "before": "0x0000", "after": "0x0001"})
        order = [c[0] for c in fake.calls]
        self.assertLess(max(i for i, c in enumerate(fake.calls) if c[0] == "VIDIOC_S_CTRL"
                            and i < order.index("VIDIOC_DQBUF")), order.index("VIDIOC_DQBUF"))
        self._assert_rolled_back(fake)

    def test_needs_bus_and_consent(self):
        for extra in (("--cis-test-0be4",), ("--cis-test-0be4", "--allow-sensor-write"),
                      ("--cis-test-0be4", "--cis-i2c-bus", "7")):
            fake = FakeSys(names=self.COMPAT)
            rc, res, _, _ = self._capture(fake, *extra)
            self.assertEqual(rc, 1)
            self.assertIn("--allow-sensor-write", res["error"])
            self.assertFalse([c for c in fake.calls if c[0] in ("open", "i2c_open")])

    def test_no_write_when_page_check_fails_or_already_set(self):
        fake = FakeSys(names=self.COMPAT)
        fake.i2c_regs[0x0002] = 0xA000
        rc, res, _, _ = self._capture(fake, *self.FLAGS)
        self.assertEqual(rc, 0, res)
        self.assertFalse(getattr(fake, "i2c_writes", []))
        self.assertIn("page check", res["test_0be4"]["error"])
        fake = FakeSys(names=self.COMPAT)
        fake.i2c_regs[0x0BE4] = 0x0001
        rc, res, _, _ = self._capture(fake, *self.FLAGS)
        self.assertFalse(getattr(fake, "i2c_writes", []))
        self.assertIn("not 0x0000", res["test_0be4"]["error"])

    def test_no_write_when_start_fails(self):
        fake = FakeSys(names=self.COMPAT, fail={("VIDIOC_STREAMON", "leader"): 22})
        rc, res, _, _ = self._capture(fake, *self.FLAGS)
        self.assertEqual(rc, 1)
        self.assertFalse(getattr(fake, "i2c_writes", []))
        self._assert_rolled_back(fake)

    def test_realsys_write_guard(self):
        real = cam.RealSys()
        for args in ((0x10, 0x0BE4, 0x0003), (0x10, 0x0BE6, 0x0001), (0x11, 0x0BE4, 0x0001),
                     (0x10, 0x6000, 0x0001)):
            with self.assertRaises(ValueError):
                real.i2c_write_0be4(-1, *args)


class SensorPresetTest(CaptureHelpers):
    """Review v5 2026-10-09: --sensor rear (default, unchanged) / front (IMX374, ss1)."""

    FRONT_NAMES = {"/sys/class/video4linux/video102/name": "exynos-is-ss1\n",
                   "/sys/class/video4linux/video214/name": cam.VC0_NAME_KERNEL_BUG + "\n"}

    def test_rear_defaults_unchanged(self):
        args = cam.build_parser().parse_args(["capture", "--out", "x"])
        preset = cam.apply_sensor_preset(args)
        self.assertEqual((args.width, args.height, args.fps, args.bayer, args.leader, args.vc0),
                         (2040, 1532, 30, "GBRG", "/dev/video101", "/dev/video210"))
        self.assertEqual((preset["position"], preset["device"]), (0, 0))
        self.assertEqual(cam.LOCKED_GEOMETRY, cam.SENSOR_PRESETS["rear"]["geometry"])
        self.assertEqual(cam.build_shot(2040, 1532, cam.PIX_SBGGR16),
                         cam.build_shot(2040, 1532, cam.PIX_SBGGR16, device=0))
        fake = FakeSys()
        rc, res, _, raw = self._capture(fake)
        self.assertEqual(rc, 0, res)
        self.assertEqual(fake.s_input_value, 0x04000101)
        self.assertEqual(set(fake.shot_vids), {(1, 110)})
        self.assertEqual([c[1] for c in fake.calls if c[0] == "open"],
                         ["/dev/video101", "/dev/video210"])
        self.assertEqual(json.loads(Path(raw + ".json").read_text())["sensor"], "S5KGN3")

    def test_front_uses_ss1_nodes_and_ids(self):
        fake = FakeSys(names=self.FRONT_NAMES)
        rc, res, _, raw = self._capture(fake, "--sensor", "front")
        self.assertEqual(rc, 0, res)
        self.assertEqual(fake.s_input_value, 0x04010201)    # VISION, position 1, vindex 2, leader
        self.assertEqual(set(fake.shot_vids), {(2, 114)})
        self.assertEqual([c[1] for c in fake.calls if c[0] == "open"],
                         ["/dev/video102", "/dev/video214"])
        self.assertEqual((res["leader"], res["node"]), ("/dev/video102", "/dev/video214"))
        side = json.loads(Path(raw + ".json").read_text())
        self.assertEqual((side["sensor"], side["bayer"]), ("IMX374", "GRBG"))
        self._assert_rolled_back(fake)

    def test_front_name_checks(self):
        # the kernel-bug VC0 name is accepted only on the preset's own VC0 node
        fake = FakeSys(names={**self.FRONT_NAMES,
                              "/sys/class/video4linux/video210/name": cam.VC0_NAME_KERNEL_BUG})
        rc, res, _, _ = self._capture(fake, "--sensor", "front", "--vc0", "/dev/video210")
        self.assertEqual(rc, 1)
        self.assertFalse([c for c in fake.calls if c[0] == "open"])
        # rear leader name on the front preset is refused
        fake = FakeSys(names={"/sys/class/video4linux/video214/name": cam.VC0_NAME_KERNEL_BUG})
        rc, res, _, _ = self._capture(fake, "--sensor", "front", "--leader", "/dev/video101")
        self.assertEqual(rc, 1)
        self.assertIn("exynos-is-ss1", res["error"])

    def test_front_refuses_rear_only_sensor_flags(self):
        for extra in (("--cis-exposure-us", "1000"), ("--cis-again", "2"),
                      ("--cis-i2c-bus", "3"), ("--cis-i2c-bus", "3", "--cis-test-0be4",
                                               "--allow-sensor-write")):
            fake = FakeSys(names=self.FRONT_NAMES)
            rc, res, _, _ = self._capture(fake, "--sensor", "front", *extra)
            self.assertEqual(rc, 1)
            self.assertIn("rear GN3 only", res["error"])
            self.assertFalse([c for c in fake.calls if c[0] in ("open", "i2c_open")])
        fake = FakeSys(names=self.FRONT_NAMES)       # read-only G_CTRL dump stays allowed
        rc, res, _, _ = self._capture(fake, "--sensor", "front", "--cis-dump")
        self.assertEqual(rc, 0, res)

    def test_front_geometry_lock(self):
        for sensor, geo, ok in (("front", (1824, 1368, 30), True), ("front", (2040, 1532, 30), False),
                                ("rear", (1824, 1368, 30), False)):
            args = types.SimpleNamespace(sensor=sensor, width=geo[0], height=geo[1], fps=geo[2])
            try:
                cam.run_stream(cam.RealSys.__new__(cam.RealSys), args, lambda r, i: True, alarm=False)
            except cam.CameraError as exc:
                self.assertEqual("geometry locked" in str(exc), not ok, (sensor, geo, exc))
            except AttributeError:
                self.assertTrue(ok)      # passed the lock, failed later on the missing args
        self.assertEqual(cam.vc0_stride(1824, cam.PIX_SBGGR16), 3648)


class ListTest(unittest.TestCase):
    def test_classify(self):
        self.assertEqual(cam.classify_node("exynos-is-ss0"), "sensor-leader")
        self.assertEqual(cam.classify_node("exynos-is-ss0vc0"), "csis-vc-dma")
        self.assertTrue(cam.classify_node("exynos-is-30s").startswith("isp-chain"))

    def test_probe_handles_missing_enum_fmt(self):
        class P(FakeSys):
            def ioctl(self, fd, req, buf):
                if req == cam.VIDIOC_QUERYCAP:
                    struct.pack_into("<16s", buf, 0, b"exynos-is-ss0")
                    struct.pack_into("<II", buf, 84, 0x04002000, 0x04002000)
                    return
                raise OSError(25, "ENOTTY")
        fake = P()
        res = cam.probe_node(fake, "/dev/video101")
        self.assertEqual(res["cap"]["driver"], "exynos-is-ss0")
        self.assertEqual(res["enum_fmt"], "ENOTTY")
        self.assertIn(("close", "/dev/video101"), fake.calls)


if __name__ == "__main__":
    unittest.main()
