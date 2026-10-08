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


class AbiTest(unittest.TestCase):
    def test_ioctl_numbers_match_kernel(self):
        for name, value in KERNEL.items():
            self.assertEqual(getattr(cam, name), value, name)

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

    def __init__(self, deliver=True, fw=True, names=None, fail=None):
        self.deliver = deliver
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
        return "leader" if self.fds.get(fd, "").endswith("video101") else "vc"

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
        name = {v: k for k, v in KERNEL.items()}.get(req, hex(req))
        self.calls.append((name, role))
        if (req >> 16) & 0x3FFF != len(buf):
            raise AssertionError(f"{name}: size {len(buf)} != encoded {(req >> 16) & 0x3FFF}")
        if (name, role) in self.fail:
            raise OSError(self.fail[(name, role)], os.strerror(self.fail[(name, role)]))
        if name == "VIDIOC_S_INPUT":
            if struct.unpack("<I", buf)[0] != 0x04000101:
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


class FakeCaptureTest(unittest.TestCase):
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
