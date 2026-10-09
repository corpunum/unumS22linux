#!/usr/bin/env python3
"""s22-camera: raw-Bayer still/sequence capture for the Galaxy S22 (Exynos 2200)
rear wide sensor (S5KGN3) on native Linux, without the Pablo ISP.

RISK / SAFETY NOTES (read before running on the phone)
------------------------------------------------------
* `list` (default) only reads sysfs. `list --probe` and `capture`/`record`
  OPEN Pablo video nodes. Opening /dev/video101 runs is_resource_open() +
  is_sensor_open() (powers the CSIS/sensor resource); closing it runs sensor
  deinit (GN3 retention deinit may toggle stream on/off internally).
* `capture`/`record` power the GN3 sensor, stream MIPI CSI and, for the FIRST
  time on this device, let the CSIS write DMA (VC0) write pixels into memory
  (the 2026-10-06 self-test streamed 83 frames but VC0 had no buffer, so no
  DMA write happened). Buffers come from /dev/dma_heap/system and are mapped
  through the camera SysMMU. A wrong buffer size could make the DMA overrun;
  the image plane is sized from the kernel's own stride formula
  (is_hw_dma_get_img_stride, align 32) and passed as bytesperline.
* No kernel module is loaded/unloaded, no sysfs is written, no firmware is
  staged. The OIS MCU firmware (is_mcu_fw.bin) is only CHECKED: if absent the
  sensor start stalls ~60 s in the firmware sysfs fallback (seen 2026-10-06);
  the client refuses unless --allow-fw-stall. Staging the firmware is a
  separate reviewed action.
* Every run ends with: S_CTRL(IS_S_STREAM=off), STREAMOFF(leader), STREAMOFF(VC0),
  REQBUFS(0) on both, close(VC0), close(leader), unmap + close dma-bufs - in a
  finally block, each step independent. A SIGALRM hard deadline backs this up.
* Kernel BUG() trap avoided: node_group.capture[0].buf.length stays 0 so
  _is_queue_subbuf_prepare() never maps user plane pointers.
* --cis-exposure-us / --cis-again (opt-in, off by default) write GN3 CIS
  registers 0x0202/0x0704 (integration) and 0x0204/0x020E (gains) over I2C:
  ONE VIDIOC_S_EXT_CTRLS on the leader, only after the blocking IS_S_STREAM
  on and an IS_G_STREAM == 1 check, values clamped here and again in the CIS
  driver. On any failure the client sends nothing more (no retry). The older
  --exposure-us/--iso only fill shot.ctl, which nothing reads without the DDK.

PIPELINE (derived from kernel source, fimc_is GNU id 59e54c03; see CAMERA.md)
-----------------------------------------------------------------------------
  /dev/video101  "exynos-is-ss0"     sensor group leader, OUTPUT_MPLANE (M2M dir)
  /dev/video210  "exynos-is-ss0vc0"  CSIS VC0 write DMA,  CAPTURE_MPLANE
  (video node = 100 + IS_VIDEO_*_NUM; SS0=1, SS0VC0=110. video110-113 are the
   CSTAT 3AA nodes, which belong to the ISP chain, not CSI.)
  Same settings as the 83-frame self-test (test_sensor_run "1 0 2040 1532 30"):
  scenario SENSOR_SCENARIO_VISION (stand-alone, no ischain / no DDK / no TZ),
  position 0 (rear), leader video id 1, stream leader 1, ex_mode 0,
  leader format SRGB36P_SP, 30 fps, front start non-blocking.
  Memory: V4L2_MEMORY_DMABUF (the Pablo vb2 meta-plane kmap does
  dma_buf_get(m.fd), so MMAP buffers cannot work on this driver).
  Default VC0 format SBGGR16 = CSIS U10BIT_UNPACK_MSB_ZERO (10-bit values in
  little-endian u16). GN3 Bayer order is GBRG (OTF_INPUT_ORDER_BAYER_GB_RG).

Stdlib only (Python 3.12 host tests, 3.14 on the phone).
"""
from __future__ import annotations

import argparse
import ctypes
import errno
import fcntl
import json
import math
import mmap
import os
import select
import shutil
import signal
import struct
import subprocess
import sys
import threading
import time
import zlib
from array import array
from operator import add

# --------------------------------------------------------------------------
# Kernel ABI (aarch64). Sizes/offsets were produced by compiling a probe with
# the exact fimc-is build flags (clang 18, out dir npu-native-eight-out-...).
# --------------------------------------------------------------------------
_IOC_WRITE, _IOC_READ = 1, 2


def _ioc(direction: int, typ: str, nr: int, size: int) -> int:
    return (direction << 30) | (size << 16) | (ord(typ) << 8) | nr


def _iowr(typ, nr, size):
    return _ioc(_IOC_READ | _IOC_WRITE, typ, nr, size)


SIZEOF = {
    "v4l2_capability": 104, "v4l2_fmtdesc": 64, "v4l2_format": 208,
    "v4l2_requestbuffers": 20, "v4l2_buffer": 88, "v4l2_plane": 64,
    "v4l2_control": 8, "v4l2_streamparm": 204, "dma_heap_allocation_data": 24,
    "dma_buf_sync": 8, "v4l2_ext_control": 20, "v4l2_ext_controls": 32,
}
VIDIOC_QUERYCAP = _ioc(_IOC_READ, "V", 0, 104)
VIDIOC_ENUM_FMT = _iowr("V", 2, 64)
VIDIOC_G_FMT = _iowr("V", 4, 208)
VIDIOC_S_FMT = _iowr("V", 5, 208)
VIDIOC_REQBUFS = _iowr("V", 8, 20)
VIDIOC_QUERYBUF = _iowr("V", 9, 88)
VIDIOC_QBUF = _iowr("V", 15, 88)
VIDIOC_DQBUF = _iowr("V", 17, 88)
VIDIOC_STREAMON = _ioc(_IOC_WRITE, "V", 18, 4)
VIDIOC_STREAMOFF = _ioc(_IOC_WRITE, "V", 19, 4)
VIDIOC_S_PARM = _iowr("V", 22, 204)
VIDIOC_S_CTRL = _iowr("V", 28, 8)
VIDIOC_G_CTRL = _iowr("V", 27, 8)
VIDIOC_S_EXT_CTRLS = _iowr("V", 72, 32)   # struct v4l2_ext_controls: 32 bytes (DWARF)
VIDIOC_S_INPUT = _iowr("V", 39, 4)
DMA_HEAP_IOCTL_ALLOC = _iowr("H", 0, 24)
DMA_BUF_IOCTL_SYNC = _ioc(_IOC_WRITE, "b", 0, 8)
DMA_BUF_SYNC_READ, DMA_BUF_SYNC_WRITE, DMA_BUF_SYNC_END = 1, 2, 4

V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE = 9
V4L2_BUF_TYPE_VIDEO_OUTPUT_MPLANE = 10
V4L2_MEMORY_DMABUF = 4
V4L2_BUF_FLAG_ERROR = 0x40


def fourcc(code: str) -> int:
    return struct.unpack("<I", code.encode("ascii"))[0]


def fourcc_str(value: int) -> str:
    return struct.pack("<I", value & 0xFFFFFFFF).decode("latin-1")


PIX_SRGB36P_SP = fourcc("SRP6")   # leader format used by pablo-test-sensor-self.c
PIX_SBGGR16 = fourcc("BYR2")
PIX_SBGGR10P = fourcc("pBAA")

# Pablo private controls (V4L2_CID_FIMC_IS_BASE based, values from the build)
CID_IS_S_STREAM = 10096654
CID_IS_S_SENSOR_SIZE = 10096709
CID_SENSOR_SET_EXTENDED_MODE = 10104853
CID_IS_G_STREAM = 10096655                  # V4L2_CID_FIMC_IS_BASE + 15
IS_ENABLE_STREAM = 1
# Sensor (CIS) controls, V4L2_CID_SENSOR_BASE = V4L2_CTRL_CLASS_CAMERA | 0x3000
# (include/videodev2_exynos_camera.h). SET_EXTENDED_MODE above is base + 21.
V4L2_CTRL_CLASS_CAMERA = 0x009A0000
CID_SENSOR_BASE = V4L2_CTRL_CLASS_CAMERA | 0x3000
CID_SENSOR_SET_AE_TARGET = CID_SENSOR_BASE + 1        # exposure, microseconds
CID_SENSOR_SET_ANALOG_GAIN = CID_SENSOR_BASE + 18     # permille (1000 = 1x)
CID_SENSOR_SET_DIGITAL_GAIN = CID_SENSOR_BASE + 19    # permille
CID_SENSOR_GET_ANALOG_GAIN = CID_SENSOR_BASE + 108    # reads GN3 reg 0x0204 back
# Manual CIS exposure without the DDK (exposure review 2026-10-09, see CAMERA.md).
# Only VIDIOC_S_EXT_CTRLS on the leader reaches sensor_module_s_ctrl():
# VIDIOC_S_CTRL with AE_TARGET/SHUTTER/GAIN is caught by is_ssx_video_s_ctrl()
# and ends in CALL_MOPS on module->ops == NULL, a silent no-op. The values are
# applied once, after the blocking IS_S_STREAM on, and clamped here first.
CIS_EXPOSURE_US_RANGE = (100, 30000)        # 30 fps frame is 33.3 ms; u16 CIT stays safe
CIS_AGAIN_PERMILLE_RANGE = (1000, 16000)    # GN3 mode 18 allows 64x; first runs stay <=16x
CIS_DGAIN_UNITY = 1000
CID_IS_G_DTPSTATUS = 10096697              # V4L2_CID_FIMC_IS_BASE + 57 (state bits, no I2C)
CID_IS_G_MIPI_ERR = 10096702               # V4L2_CID_FIMC_IS_BASE + 62 (csi->error_id_last, no I2C)
CID_SENSOR_GET_DIGITAL_GAIN = CID_SENSOR_BASE + 109   # reads GN3 reg 0x020E
# Read-only GN3 register dump through i2c-dev (review v2 2026-10-09). Every
# access is ONE I2C_RDWR of [write 2-byte register address, read n bytes]
# to 7-bit address 0x10 - the same framing as is_sensor_read16(). No register
# is ever written. All addresses are page-0x4000 CCI registers below 0x6000
# (0x6000+ are page/indirect-access controls and are never touched). The page
# is checked first: 0x0002 must read the GN3 revision 0xC000 seen at open.
I2C_RDWR = 0x0707
I2C_M_RD = 0x0001
GN3_I2C_ADDR = 0x10
GN3_COMPATIBLE = b"samsung,exynos-is-cis-gn3"
GN3_REV_REG, GN3_REV = 0x0002, 0xC000
GN3_DUMP_REGS = (   # (address, bytes, name) - sensor_gn3 log table subset, page 0x4000
    (0x0000, 2, "model_id"), (0x0002, 2, "revision"), (0x0005, 1, "frame_count"),
    (0x0100, 2, "mode_select|orientation"), (0x010E, 2, "retention_crc_en"),
    (0x0112, 2, "csi_data_format"), (0x0202, 2, "coarse_integration"),
    (0x0204, 2, "analog_gain"), (0x020E, 2, "digital_gain"),
    (0x0340, 2, "frame_length_lines"), (0x0342, 2, "line_length_pck"),
    (0x0344, 2, "x_addr_start"), (0x0346, 2, "y_addr_start"),
    (0x0348, 2, "x_addr_end"), (0x034A, 2, "y_addr_end"),
    (0x034C, 2, "x_output_size"), (0x034E, 2, "y_output_size"),
    (0x0600, 2, "test_pattern_mode"), (0x0620, 2, "test_pattern_0620"),
    (0x0702, 2, "fll_shifter"), (0x0704, 2, "cit_shifter"), (0x0900, 2, "binning"),
    (0x0B30, 2, "fast_change_idx"), (0x0E00, 1, "aeb"), (0x19C2, 2, "retention_crc_ok"),
)
# Review v3: page-0x4000 CCI registers (< 0x6000) that the driver's own setfiles
# write, with the value written (sensor_gn3_setfile_A_19p2_Global, then
# sensor_gn3_setfile_A_2040x1532_30fps), plus SMIA id registers. Read-only echo
# check: a mismatch is a hint (firmware may legitimately rewrite some), not an error.
GN3_SETFILE_ECHO = (
    (0x0006, 1, None), (0x0008, 2, None),          # pixel order, data pedestal (SMIA)
    (0x011C, 2, 0x0101), (0x0136, 2, 0x1300), (0x013E, 2, 0x00C8), (0x0228, 2, 0x0100),
    (0x0260, 2, 0x0001), (0x0262, 2, 0x0200), (0x0264, 2, 0x0203), (0x0266, 2, 0x0300),
    (0x0304, 2, 0x0002), (0x0306, 2, 0x00C8), (0x030E, 2, 0x0003), (0x0310, 2, 0x00CA),
    (0x0312, 2, 0x0000), (0x031A, 2, 0x0003), (0x031C, 2, 0x0031), (0x031E, 2, 0x0001),
    (0x0400, 2, 0x1010), (0x0408, 2, 0x0100), (0x040A, 2, 0x0100), (0x040C, 2, 0x0000),
    (0x0724, 2, 0x0000), (0x0A52, 2, 0x0001), (0x0B04, 2, 0x0001), (0x0B32, 2, 0x0000),
    (0x0BC2, 2, 0x0000), (0x0BC4, 2, 0x0000), (0x0BC6, 2, 0x0000), (0x0BE2, 2, 0x0000),
    (0x0BE4, 2, 0x0001), (0x0FE0, 2, 0x0000),
    (0x0118, 2, 0x0000), (0x020C, 2, 0x0000), (0x021E, 2, 0x0000), (0x0270, 2, 0x2B2B),
    (0x0272, 2, 0x2B10), (0x0350, 2, 0x0000), (0x0352, 2, 0x000A), (0x0380, 2, 0x0002),
    (0x0382, 2, 0x0006), (0x0384, 2, 0x0002), (0x0386, 2, 0x0006), (0x0720, 2, 0x0001),
    (0x0722, 2, 0x0000), (0x0728, 2, 0x03F8), (0x072A, 2, 0x017E), (0x0B02, 2, 0x0103),
    (0x0B08, 2, 0x0001),
)
# Review v3: camera rails from the GN3 power table (all S2MPB02 regulators,
# DT s2mpb02_pmic@59). Their sysfs state/microvolts are read from the PMIC
# (s2m_is_enabled_regmap / get_voltage_sel read the chip, no cache).
CAMERA_RAILS = ("VDDA_2.2V_CAM", "VDDD_0.92V_CAM", "VDDPHY_0.92V_CAM", "VDDIO_1.8V_CAM",
                "VDDIO_1.8V_SUB", "S2MPB02_BUCK2", "S2MPB02_BB", "VDDAF_3.2V_CAM",
                "VDDAF_2.8V_SUB", "VDDD_1.8V_OIS", "VDDD_3.2V_OIS", "VDDOIS_2.8V_SUB")
REGULATOR_CLASS = "/sys/class/regulator"
EXT_CTRL_FMT = "<IIIq"                      # struct v4l2_ext_control (packed), 20 bytes
EXT_CTRLS_FMT = "<IIIiI4xQ"                 # which,count,error_idx,request_fd,rsvd,controls
SENSOR_SCENARIO_VISION = 1
SENSOR_NOBLOCK_SHIFT = 28

# camera2_shot_ext (v10_1_0 metadata)
SIZE_OF_META_PLANE = 57344
SHOT_MAGIC_NUMBER = 0x56789234
OFF_SHOT_MAGIC = 37936
OFF_NODE_GROUP = 88
NODE_SIZE = 200
CAPTURE_NODE_MAX = 12
NODE_VID, NODE_REQUEST, NODE_INPUT_CROP, NODE_OUTPUT_CROP = 0, 4, 8, 24
NODE_PIXELFORMAT, NODE_FLAGS, NODE_WIDTH, NODE_HEIGHT, NODE_BUF = 44, 52, 56, 60, 112
OFF_CTL_AA_AEMODE, OFF_CTL_AA_MODE = 5460, 5560
OFF_CTL_EXPOSURE_NS, OFF_CTL_DURATION_NS, OFF_CTL_SENSITIVITY = 6184, 6192, 6200
AA_CONTROL_OFF = AA_AEMODE_OFF = 1

VIDEO_NODE_BASE = 100            # EXYNOS_VIDEONODE_FIMC_IS
IS_VIDEO_SS0_NUM = 1
IS_VIDEO_SS0VC0_NUM = 110
LEADER_NODE = "/dev/video101"
VC0_NODE = "/dev/video210"
LEADER_NAME = "exynos-is-ss0"
VC0_NAME = "exynos-is-ss0vc0"
# This kernel's IS_VIDEO_SSXVC0_NAME(id) stringifies the parameter name ("ss"#id"vc0"),
# so every sensor's VC0 node is called this. It is accepted only on /dev/video210,
# whose number (IS_VIDEO_SS0VC0_NUM + 0*4, is-video-sensor-subdev.c:61-62) is SS0 VC0.
VC0_NAME_KERNEL_BUG = "exynos-is-ssdevice_idvc0"
SELFTEST_PARAM = "/sys/module/fimc_is/parameters/test_sensor_run"
MCU_FW_NAME = "is_mcu_fw.bin"
# Only where the kernel's firmware loader looks (firmware_loader/main.c): it never
# searches /vendor/firmware on this kernel.
MCU_FW_DIRS = ("/system/vendor/firmware", "/lib/firmware/updates", "/lib/firmware")
# Review 2026-10-08: one SysMMU fault on the camera block panics the kernel, and
# the CSIS DMA has no visible vertical clamp, so every VC0 image buffer gets
# guard pages (vb2 accepts a larger plane; the whole dma-buf is mapped).
VC0_GUARD_BYTES = 1 << 20
# Until a capture has been proven once, only the geometry the 2026-10-06
# self-test used is allowed (DMA geometry comes from the DT mode anyway).
LOCKED_GEOMETRY = (2040, 1532, 30)
DMA_HEAP = "/dev/dma_heap/system"
GN3_BAYER = "GBRG"


def align(value: int, to: int) -> int:
    return (value + to - 1) // to * to


def page_round(value: int) -> int:
    return align(value, 4096)


# --------------------------------------------------------------------------
# struct packing
# --------------------------------------------------------------------------
BUFFER_FMT = "<IIIII4xqq16sIIQIIi4x"     # struct v4l2_buffer, 88 bytes on arm64
PLANE_FMT = "<IIQI44x"                   # struct v4l2_plane, 64 bytes
REQBUFS_FMT = "<IIII4x"                  # count,type,memory,capabilities,flags+rsvd
HEAP_ALLOC_FMT = "<QIIQ"                 # len, fd, fd_flags, heap_flags


def parse_capability(buf: bytes) -> dict:
    driver, card, bus, version, caps, dcaps = struct.unpack_from("<16s32s32sIII", buf)
    clean = lambda b: b.split(b"\0", 1)[0].decode("utf-8", "replace")
    return {"driver": clean(driver), "card": clean(card), "bus_info": clean(bus),
            "version": version, "capabilities": caps, "device_caps": dcaps}


def make_format_mplane(buf_type: int, width: int, height: int, pixfmt: int,
                       num_planes: int, bytesperline0: int = 0, flags: int = 0) -> bytearray:
    out = bytearray(SIZEOF["v4l2_format"])
    struct.pack_into("<I", out, 0, buf_type)
    struct.pack_into("<IIIII", out, 8, width, height, pixfmt, 0, 0)
    struct.pack_into("<II", out, 8 + 20, 0, bytesperline0)   # plane_fmt[0]
    struct.pack_into("<BB", out, 8 + 180, num_planes, flags)
    return out


def make_reqbufs(count: int, buf_type: int, memory: int = V4L2_MEMORY_DMABUF) -> bytearray:
    return bytearray(struct.pack(REQBUFS_FMT, count, buf_type, memory, 0))


def make_streamparm_capture(fps: int) -> bytearray:
    out = bytearray(SIZEOF["v4l2_streamparm"])
    # type, capability, capturemode, timeperframe{numerator, denominator}
    struct.pack_into("<IIIII", out, 0, V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE, 0, 0, 1, fps)
    return out


def make_control(cid: int, value: int) -> bytearray:
    return bytearray(struct.pack("<Ii", cid, value))


def _clamp(value: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, value))


def cis_exposure_plan(exposure_us: int = 0, again: float = 0.0) -> tuple[list, dict]:
    """(controls, summary) for one S_EXT_CTRLS on the leader; ([], {}) = leave the sensor alone.

    exposure_us -> V4L2_CID_SENSOR_SET_AE_TARGET -> GN3 coarse integration (0x0202).
    again (x, e.g. 4.0) -> V4L2_CID_SENSOR_SET_ANALOG_GAIN -> GN3 0x0204.

    sensor_module_s_ctrl() skips the analog write and FALLS THROUGH to the
    digital gain when the value equals the cached cis_data->analog_gain[1]. With
    CONFIG_CAMERA_VENDER_MCD that cache survives close/open while the sensor
    register does not. So the gain goes out as (g-1, g, dgain=1x): the second
    write always differs from the cache, and a fall-through on the first is
    undone by the explicit unity digital gain.
    """
    controls, summary = [], {}
    if exposure_us and exposure_us > 0:
        exp = _clamp(int(exposure_us), *CIS_EXPOSURE_US_RANGE)
        controls.append((CID_SENSOR_SET_AE_TARGET, exp))
        summary["exposure_us"] = exp
        summary["exposure_clamped"] = exp != int(exposure_us)
    if again and math.isfinite(again) and again > 0:
        want = int(round(min(float(again), 1000.0) * 1000))
        gain = _clamp(want, *CIS_AGAIN_PERMILLE_RANGE)
        controls += [(CID_SENSOR_SET_ANALOG_GAIN, gain - 1), (CID_SENSOR_SET_ANALOG_GAIN, gain),
                     (CID_SENSOR_SET_DIGITAL_GAIN, CIS_DGAIN_UNITY)]
        summary["again_permille"] = gain
        summary["again_clamped"] = gain != want
    return controls, summary


def make_buffer(index: int, buf_type: int, planes_addr: int, num_planes: int,
                memory: int = V4L2_MEMORY_DMABUF) -> bytearray:
    return bytearray(struct.pack(BUFFER_FMT, index, buf_type, 0, 0, 0, 0, 0, b"\0" * 16,
                                 0, memory, planes_addr, num_planes, 0, 0))


def parse_buffer(buf: bytes) -> dict:
    (index, typ, bytesused, flags, field, sec, usec, _tc, sequence, memory, m, length,
     _r2, _rq) = struct.unpack(BUFFER_FMT, bytes(buf))
    return {"index": index, "type": typ, "flags": flags, "sequence": sequence,
            "timestamp": sec + usec / 1e6, "memory": memory, "m": m, "length": length}


def s_input_value(scenario: int, position: int, vindex: int, leader: int, stream: int = 0) -> int:
    """CONFIG_USE_SENSOR_GROUP encoding from include/is-video.h."""
    return ((scenario & 0x3F) << 26) | ((stream & 0x3) << 24) | ((position & 0xFF) << 16) \
        | ((vindex & 0xFF) << 8) | (leader & 0xF)


def sensor_size_value(width: int, height: int) -> int:
    value = ((width & 0xFFFF) << 16) | (height & 0xFFFF)
    return value - (1 << 32) if value >= 1 << 31 else value


def build_shot(width: int, height: int, cap_pixfmt: int, exposure_us: int = 0,
               iso: int = 0, fps: int = 30) -> bytearray:
    """camera2_shot_ext for the sensor-group leader meta plane."""
    meta = bytearray(SIZE_OF_META_PLANE)
    struct.pack_into("<I", meta, OFF_SHOT_MAGIC, SHOT_MAGIC_NUMBER)
    leader = OFF_NODE_GROUP
    struct.pack_into("<II", meta, leader + NODE_VID, IS_VIDEO_SS0_NUM, 0)  # request 0
    cap = OFF_NODE_GROUP + NODE_SIZE  # capture[0]
    struct.pack_into("<II", meta, cap + NODE_VID, IS_VIDEO_SS0VC0_NUM, 1)
    struct.pack_into("<4I", meta, cap + NODE_INPUT_CROP, 0, 0, width, height)
    struct.pack_into("<4I", meta, cap + NODE_OUTPUT_CROP, 0, 0, width, height)
    struct.pack_into("<I", meta, cap + NODE_PIXELFORMAT, cap_pixfmt)
    struct.pack_into("<II", meta, cap + NODE_WIDTH, width, height)
    # cap + NODE_BUF (struct v4l2_buffer) stays zero: length 0 => no user planes.
    if exposure_us or iso:
        struct.pack_into("<I", meta, OFF_CTL_AA_MODE, AA_CONTROL_OFF)
        struct.pack_into("<I", meta, OFF_CTL_AA_AEMODE, AA_AEMODE_OFF)
        struct.pack_into("<QQ", meta, OFF_CTL_EXPOSURE_NS, exposure_us * 1000,
                         1_000_000_000 // max(1, fps))
        struct.pack_into("<I", meta, OFF_CTL_SENSITIVITY, iso)
    return meta


def vc0_stride(width: int, pixfmt: int) -> int:
    """is_hw_dma_get_img_stride(bitwidth, bpp, ..., align=32) for CSIS v8.0."""
    if pixfmt == PIX_SBGGR16:
        return align(width * 2, 32)
    if pixfmt == PIX_SBGGR10P:
        return align((width * 10 + 7) // 8, 32)
    raise ValueError("unsupported VC0 pixel format")


def leader_image_size(width: int, height: int) -> int:
    return align(width * height * 12 // 8, 16) * 3   # srgb36p_sps()


# --------------------------------------------------------------------------
# System access layer (injectable for tests)
# --------------------------------------------------------------------------
class RealSys:
    def open(self, path: str) -> int:
        return os.open(path, os.O_RDWR | os.O_NONBLOCK | os.O_CLOEXEC)

    def close(self, fd: int) -> None:
        os.close(fd)

    def ioctl(self, fd: int, request: int, buf: bytearray) -> None:
        fcntl.ioctl(fd, request, buf, True)

    def select(self, rfds, wfds, timeout: float):
        r, w, _ = select.select(rfds, wfds, [], timeout)
        return r, w

    def heap_alloc(self, size: int) -> int:
        heap = os.open(DMA_HEAP, os.O_RDWR | os.O_CLOEXEC)
        try:
            data = bytearray(struct.pack(HEAP_ALLOC_FMT, size, 0, os.O_RDWR | os.O_CLOEXEC, 0))
            fcntl.ioctl(heap, DMA_HEAP_IOCTL_ALLOC, data, True)
            return struct.unpack(HEAP_ALLOC_FMT, data)[1]
        finally:
            os.close(heap)

    def mmap(self, fd: int, size: int):
        return mmap.mmap(fd, size, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE)

    def dmabuf_sync(self, fd: int, flags: int) -> None:
        try:
            fcntl.ioctl(fd, DMA_BUF_IOCTL_SYNC, struct.pack("<Q", flags))
        except OSError:
            pass

    def read_text(self, path: str) -> str | None:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                return f.read(4096)
        except OSError:
            return None

    def exists(self, path: str) -> bool:
        return os.path.exists(path)

    def listdir(self, path: str) -> list:
        try:
            return sorted(os.listdir(path))
        except OSError:
            return []

    def i2c_open(self, bus: int) -> int:
        return os.open(f"/dev/i2c-{int(bus)}", os.O_RDWR | os.O_CLOEXEC)

    def i2c_read(self, fd: int, addr: int, reg: int, length: int) -> bytes:
        """One I2C_RDWR: [W reg_hi reg_lo][Sr R length]. Never a data write."""
        if not (0 <= reg < 0x6000 and length in (1, 2)):
            raise ValueError("register outside the read-only allowlist")
        wbuf = (ctypes.c_uint8 * 2)(reg >> 8, reg & 0xFF)
        rbuf = (ctypes.c_uint8 * length)()
        msgs = (I2cMsg * 2)(I2cMsg(addr, 0, 2, ctypes.addressof(wbuf)),
                            I2cMsg(addr, I2C_M_RD, length, ctypes.addressof(rbuf)))
        rdwr = I2cRdwr(ctypes.addressof(msgs), 2)
        fcntl.ioctl(fd, I2C_RDWR, bytes(rdwr))
        return bytes(rbuf)


class I2cMsg(ctypes.Structure):          # struct i2c_msg, 16 bytes on arm64
    _fields_ = [("addr", ctypes.c_uint16), ("flags", ctypes.c_uint16),
                ("len", ctypes.c_uint16), ("buf", ctypes.c_void_p)]


class I2cRdwr(ctypes.Structure):         # struct i2c_rdwr_ioctl_data, 16 bytes
    _fields_ = [("msgs", ctypes.c_void_p), ("nmsgs", ctypes.c_uint32)]


class CameraError(Exception):
    pass


class DeadlineExceeded(CameraError):
    pass


class DmaBuf:
    def __init__(self, sysif, size: int):
        self.sys, self.size = sysif, size
        self.fd = sysif.heap_alloc(size)
        try:
            self.map = sysif.mmap(self.fd, size)
        except BaseException:
            sysif.close(self.fd)
            raise

    def write(self, data: bytes) -> None:
        self.sys.dmabuf_sync(self.fd, DMA_BUF_SYNC_WRITE)
        self.map[0:len(data)] = data
        self.sys.dmabuf_sync(self.fd, DMA_BUF_SYNC_WRITE | DMA_BUF_SYNC_END)

    def read(self, length: int) -> bytes:
        self.sys.dmabuf_sync(self.fd, DMA_BUF_SYNC_READ)
        data = bytes(self.map[0:length])
        self.sys.dmabuf_sync(self.fd, DMA_BUF_SYNC_READ | DMA_BUF_SYNC_END)
        return data

    def release(self) -> None:
        try:
            if hasattr(self.map, "close"):
                self.map.close()
        finally:
            self.sys.close(self.fd)


class PlaneArray:
    """A stable-address struct v4l2_plane[n] for multi-planar QBUF/DQBUF."""

    def __init__(self, count: int):
        self.count = count
        self.mem = ctypes.create_string_buffer(SIZEOF["v4l2_plane"] * count)

    @property
    def addr(self) -> int:
        return ctypes.addressof(self.mem)

    def set(self, idx: int, fd: int, length: int, bytesused: int = 0) -> None:
        struct.pack_into(PLANE_FMT, self.mem, idx * 64, bytesused, length, fd & 0xFFFFFFFF, 0)

    def clear(self) -> None:
        ctypes.memset(self.mem, 0, len(self.mem))

    def get(self, idx: int) -> tuple:
        return struct.unpack_from(PLANE_FMT, self.mem, idx * 64)


class ExtControls:
    """struct v4l2_ext_controls + a stable-address v4l2_ext_control[n] (value controls only)."""

    def __init__(self, controls, which: int = V4L2_CTRL_CLASS_CAMERA):
        if not controls:
            raise ValueError("no controls")
        self.mem = ctypes.create_string_buffer(SIZEOF["v4l2_ext_control"] * len(controls))
        for i, (cid, value) in enumerate(controls):
            struct.pack_into(EXT_CTRL_FMT, self.mem, i * SIZEOF["v4l2_ext_control"],
                             cid, 0, 0, int(value))
        self.buf = bytearray(struct.pack(EXT_CTRLS_FMT, which, len(controls), 0, 0, 0,
                                         ctypes.addressof(self.mem)))

    @property
    def error_idx(self) -> int:
        return struct.unpack_from("<I", self.buf, 8)[0]


# --------------------------------------------------------------------------
# Prechecks
# --------------------------------------------------------------------------
def node_name(sysif, node: str) -> str | None:
    text = sysif.read_text(f"/sys/class/video4linux/{os.path.basename(node)}/name")
    return text.strip() if text is not None else None


def find_mcu_firmware(sysif) -> str | None:
    dirs = list(MCU_FW_DIRS)
    custom = sysif.read_text("/sys/module/firmware_class/parameters/path")
    if custom and custom.strip():
        dirs.insert(0, custom.strip())
    for d in dirs:
        path = os.path.join(d, MCU_FW_NAME)
        if sysif.exists(path):
            return path
    return None


def selftest_active(sysif) -> bool:
    text = sysif.read_text(SELFTEST_PARAM)
    if not text:
        return False
    for line in text.splitlines():
        if line.startswith("act "):
            return line.split()[1] == "1"
    return False


def gn3_i2c_bus_ok(sysif, bus: int) -> bool:
    """/dev/i2c-BUS is only used when sysfs says the GN3 CIS client sits at BUS-0010."""
    text = sysif.read_text(f"/sys/bus/i2c/devices/{int(bus)}-{GN3_I2C_ADDR:04x}/of_node/compatible")
    return text is not None and GN3_COMPATIBLE.decode() in text.replace("\0", "\n").split("\n")


def prechecks(sysif, args) -> dict:
    info = {}
    bus = getattr(args, "cis_i2c_bus", None)
    if getattr(args, "cis_i2c_extended", False) and bus is None:
        raise CameraError("--cis-i2c-extended needs --cis-i2c-bus")
    if bus is not None and not gn3_i2c_bus_ok(sysif, bus):
        raise CameraError(f"i2c bus {bus}: /sys/bus/i2c/devices/{bus}-0010 is not "
                          f"{GN3_COMPATIBLE.decode()}; refusing the register dump")
    if not args.skip_node_check:
        for node, expected in ((args.leader, LEADER_NAME), (args.vc0, VC0_NAME)):
            name = node_name(sysif, node)
            if name != expected and not (expected == VC0_NAME and name == VC0_NAME_KERNEL_BUG
                                         and os.path.basename(node) == "video210"):
                raise CameraError(f"{node} sysfs name is {name!r}, expected {expected!r}; "
                                  "refusing (use --skip-node-check only after review)")
    if selftest_active(sysif):
        raise CameraError("Pablo self-test stream is active (test_sensor_run act 1); "
                          "stop it first with: echo 0 > " + SELFTEST_PARAM)
    fw = find_mcu_firmware(sysif)
    info["mcu_fw"] = fw
    if fw is None and not args.allow_fw_stall:
        raise CameraError(f"{MCU_FW_NAME} not found in {', '.join(MCU_FW_DIRS)}: sensor start "
                          "would stall ~60 s in the firmware fallback. Stage it (separate "
                          "reviewed action) or pass --allow-fw-stall")
    return info


# --------------------------------------------------------------------------
# Capture session
# --------------------------------------------------------------------------
class Session:
    """Sensor-group leader (video101) + CSIS VC0 DMA (video210) streaming."""

    def __init__(self, sysif, width=2040, height=1532, fps=30, pixfmt=PIX_SBGGR16,
                 leader_bufs=2, vc_bufs=3, leader=LEADER_NODE, vc0=VC0_NODE,
                 exposure_us=0, iso=0, position=0, log=None, cis_exposure_us=0, cis_again=0.0,
                 cis_dump=False, cis_i2c_bus=None, cis_i2c_extended=False, rails=False):
        self.sys = sysif
        self.width, self.height, self.fps, self.pixfmt = width, height, fps, pixfmt
        self.n_leader, self.n_vc = leader_bufs, vc_bufs
        self.leader_path, self.vc_path = leader, vc0
        self.exposure_us, self.iso, self.position = exposure_us, iso, position
        self.stride = vc0_stride(width, pixfmt)
        self.image_len = self.stride * height
        self.log = log or (lambda msg: None)
        self.lfd = self.vfd = None
        self.lbufs: list[tuple[DmaBuf, DmaBuf]] = []
        self.vbufs: list[tuple[DmaBuf, DmaBuf]] = []
        self.lplanes = PlaneArray(2)
        self.vplanes = PlaneArray(2)
        self.state = {"leader_reqbufs": False, "vc_reqbufs": False, "vc_streamon": False,
                      "leader_streamon": False, "front_start": False}
        self.teardown_errors: list[str] = []
        self.steps: list[str] = []
        self.cis_controls, summary = cis_exposure_plan(cis_exposure_us, cis_again)
        self.cis = ({"requested": summary, "applied": False, "error": None,
                     "readback_again_permille": None} if self.cis_controls else None)
        self.cis_dump_enabled, self.cis_i2c_bus = cis_dump, cis_i2c_bus
        self.cis_i2c_extended, self.rails_enabled = cis_i2c_extended, rails
        self.dump = {} if (cis_dump or cis_i2c_bus is not None or rails) else None

    def rails_snapshot(self, tag: str):
        """Read-only sysfs snapshot of the camera regulators (never raises)."""
        if not self.rails_enabled or self.dump is None:
            return
        out = {}
        try:
            for entry in self.sys.listdir(REGULATOR_CLASS):
                base = f"{REGULATOR_CLASS}/{entry}"
                name = (self.sys.read_text(base + "/name") or "").strip()
                if name not in CAMERA_RAILS:
                    continue
                out[name] = {k: (self.sys.read_text(f"{base}/{k}") or "").strip() or None
                             for k in ("state", "microvolts", "num_users")}
                out[name]["node"] = entry
            for name in CAMERA_RAILS:
                out.setdefault(name, "not found")
        except DeadlineExceeded:
            raise
        except Exception as exc:   # sysfs oddities must never fail a capture
            out["error"] = repr(exc)
        self.dump.setdefault(tag, {})["rails"] = out

    # -- helpers
    def _ioctl(self, fd, req, buf, what):
        self.steps.append(what)
        self.log(what)
        try:
            self.sys.ioctl(fd, req, buf)
        except OSError as exc:
            raise CameraError(f"{what} failed: {errno.errorcode.get(exc.errno, exc.errno)} "
                              f"({exc.strerror})") from exc

    def _qbuf(self, fd, buf_type, planes, index, img: DmaBuf, meta: DmaBuf, output: bool, what):
        planes.clear()
        planes.set(0, img.fd, img.size, img.size if output else 0)
        planes.set(1, meta.fd, meta.size, meta.size if output else 0)
        buf = make_buffer(index, buf_type, planes.addr, 2)
        self._ioctl(fd, VIDIOC_QBUF, buf, what)

    def _dqbuf(self, fd, buf_type, planes):
        planes.clear()
        buf = make_buffer(0, buf_type, planes.addr, 2)
        try:
            self.sys.ioctl(fd, VIDIOC_DQBUF, buf)
        except OSError as exc:
            if exc.errno == errno.EAGAIN:
                return None
            raise CameraError(f"DQBUF failed: {errno.errorcode.get(exc.errno, exc.errno)}") from exc
        return parse_buffer(buf)

    def _queue_leader(self, index):
        img, meta = self.lbufs[index]
        meta.write(build_shot(self.width, self.height, self.pixfmt, self.exposure_us,
                              self.iso, self.fps))
        self._qbuf(self.lfd, V4L2_BUF_TYPE_VIDEO_OUTPUT_MPLANE, self.lplanes, index, img, meta,
                   True, f"QBUF leader[{index}]")

    def _queue_vc(self, index):
        img, meta = self.vbufs[index]
        self._qbuf(self.vfd, V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE, self.vplanes, index, img, meta,
                   False, f"QBUF vc0[{index}]")

    # -- setup / teardown
    def start(self):
        out_t, cap_t = V4L2_BUF_TYPE_VIDEO_OUTPUT_MPLANE, V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE
        self.rails_snapshot("before_open")
        self.steps.append(f"open {self.leader_path}")
        self.lfd = self.sys.open(self.leader_path)
        self._ioctl(self.lfd, VIDIOC_S_INPUT, bytearray(struct.pack("<I", s_input_value(
            SENSOR_SCENARIO_VISION, self.position, IS_VIDEO_SS0_NUM, 1))), "S_INPUT leader")
        self._ioctl(self.lfd, VIDIOC_S_PARM, make_streamparm_capture(self.fps), "S_PARM leader")
        self._ioctl(self.lfd, VIDIOC_S_CTRL,
                    make_control(CID_IS_S_SENSOR_SIZE, sensor_size_value(self.width, self.height)),
                    "S_CTRL sensor_size")
        self._ioctl(self.lfd, VIDIOC_S_CTRL, make_control(CID_SENSOR_SET_EXTENDED_MODE, 0),
                    "S_CTRL ex_mode=0")
        self._ioctl(self.lfd, VIDIOC_S_FMT, make_format_mplane(
            out_t, self.width, self.height, PIX_SRGB36P_SP, 2), "S_FMT leader")
        self.steps.append(f"open {self.vc_path}")
        self.vfd = self.sys.open(self.vc_path)
        self._ioctl(self.vfd, VIDIOC_S_FMT, make_format_mplane(
            cap_t, self.width, self.height, self.pixfmt, 2, self.stride), "S_FMT vc0")
        self._ioctl(self.lfd, VIDIOC_REQBUFS, make_reqbufs(self.n_leader, out_t), "REQBUFS leader")
        self.state["leader_reqbufs"] = True
        self._ioctl(self.vfd, VIDIOC_REQBUFS, make_reqbufs(self.n_vc, cap_t), "REQBUFS vc0")
        self.state["vc_reqbufs"] = True
        limg = page_round(leader_image_size(self.width, self.height))
        for _ in range(self.n_leader):
            self.lbufs.append((DmaBuf(self.sys, limg), DmaBuf(self.sys, SIZE_OF_META_PLANE)))
        for _ in range(self.n_vc):
            self.vbufs.append((DmaBuf(self.sys, page_round(self.image_len) + VC0_GUARD_BYTES),
                               DmaBuf(self.sys, SIZE_OF_META_PLANE)))
        for i in range(self.n_vc):
            self._queue_vc(i)
        # VC0 must start before the leader: is_subdev_start() refuses once the
        # sensor group leader is started.
        self._ioctl(self.vfd, VIDIOC_STREAMON, bytearray(struct.pack("<I", cap_t)), "STREAMON vc0")
        self.state["vc_streamon"] = True
        for i in range(self.n_leader):
            self._queue_leader(i)
        self._ioctl(self.lfd, VIDIOC_STREAMON, bytearray(struct.pack("<I", out_t)),
                    "STREAMON leader")
        self.state["leader_streamon"] = True
        self.state["front_start"] = True   # set before the call: stop is harmless if it failed
        # BLOCKING start (noblock=0): is_sensor_instanton runs inside the ioctl.
        # A non-blocking start races teardown (front_stop returns early while
        # the instant work later arms CSI DMA into freed buffers -> SysMMU
        # fault -> panic). Review 2026-10-08.
        self._ioctl(self.lfd, VIDIOC_S_CTRL, make_control(CID_IS_S_STREAM, 1),
                    "S_CTRL IS_S_STREAM on (blocking)")
        self._apply_cis()
        self.dump_state("after_start")

    def dump_state(self, tag: str):
        """Read-only snapshot. Never raises for an ioctl/I2C error; never writes."""
        if self.dump is None or self.lfd is None or not self.state["front_start"]:
            return
        snap = self.dump.setdefault(tag, {})
        if self.cis_dump_enabled:
            ctl_out = snap.setdefault("controls", {})
            for cid, name in ((CID_IS_G_STREAM, "is_g_stream"), (CID_IS_G_DTPSTATUS, "dtp_status"),
                              (CID_IS_G_MIPI_ERR, "csis_error_id"),
                              (CID_SENSOR_GET_ANALOG_GAIN, "again_permille"),
                              (CID_SENSOR_GET_DIGITAL_GAIN, "dgain_permille")):
                ctl = make_control(cid, 0)
                try:
                    self._ioctl(self.lfd, VIDIOC_G_CTRL, ctl, f"G_CTRL {name}")
                    ctl_out[name] = struct.unpack("<Ii", ctl)[1]
                except DeadlineExceeded:
                    raise
                except CameraError as exc:
                    ctl_out[name] = f"error: {exc}"
        if self.cis_i2c_bus is not None:
            snap["i2c"] = self._i2c_dump()
        self.rails_snapshot(tag)

    def _i2c_dump(self) -> dict:
        out = {}
        try:
            fd = self.sys.i2c_open(self.cis_i2c_bus)
        except OSError as exc:
            return {"error": f"open /dev/i2c-{self.cis_i2c_bus}: {exc.strerror}"}
        try:
            for reg, length, name in GN3_DUMP_REGS:
                self.steps.append(f"I2C read {reg:#06x}")
                try:
                    raw = self.sys.i2c_read(fd, GN3_I2C_ADDR, reg, length)
                except OSError as exc:
                    out["error"] = f"read {reg:#06x}: {exc.strerror}; dump stopped"
                    break
                value = int.from_bytes(raw, "big")
                out[f"{reg:#06x} {name}"] = f"{value:#0{2 + 2 * length}x}"
                if reg == GN3_REV_REG and value != GN3_REV:
                    out["error"] = (f"revision {value:#06x} != {GN3_REV:#06x}: page is not "
                                    "0x4000 or wrong device; dump stopped")
                    break
            if self.cis_i2c_extended and "error" not in out:
                echo, mismatch = {}, []
                for reg, length, expected in GN3_SETFILE_ECHO:
                    self.steps.append(f"I2C read {reg:#06x}")
                    try:
                        raw = self.sys.i2c_read(fd, GN3_I2C_ADDR, reg, length)
                    except OSError as exc:
                        out["error"] = f"read {reg:#06x}: {exc.strerror}; echo stopped"
                        break
                    value = int.from_bytes(raw, "big")
                    echo[f"{reg:#06x}"] = f"{value:#0{2 + 2 * length}x}"
                    if expected is not None and value != expected:
                        mismatch.append(f"{reg:#06x}: {value:#06x} (setfile {expected:#06x})")
                out["setfile_echo"] = echo
                out["setfile_mismatch"] = mismatch
        finally:
            try:
                self.sys.close(fd)
            except OSError:
                pass
        return out

    def _apply_cis(self):
        """Write exposure/gain to the GN3 once, only while the sensor streams.

        Never raises for an ioctl failure. If the gate or the call fails,
        nothing more is sent: no retry and no second attempt, and the capture
        goes on. The hard deadline (DeadlineExceeded) still propagates to the
        teardown.
        """
        if not self.cis_controls:
            return
        rec = self.cis
        try:
            ctl = make_control(CID_IS_G_STREAM, 0)
            self._ioctl(self.lfd, VIDIOC_G_CTRL, ctl, "G_CTRL IS_G_STREAM")
            value = struct.unpack("<Ii", ctl)[1]
            if value != IS_ENABLE_STREAM:
                rec["error"] = f"sensor not streaming (IS_G_STREAM={value}); no change"
                return
            ext = ExtControls(self.cis_controls)
            try:
                self._ioctl(self.lfd, VIDIOC_S_EXT_CTRLS, ext.buf,
                            "S_EXT_CTRLS cis " + " ".join(f"{c:#x}={v}" for c, v in
                                                          self.cis_controls))
            except CameraError as exc:
                if isinstance(exc, DeadlineExceeded):
                    raise
                # The driver stops at the first failing control and leaves
                # error_idx == count, so earlier controls in the list may have
                # been written. Nothing is retried; the capture goes on.
                rec["error"] = (f"{exc}; not retried, sensor keeps its defaults or a "
                                "partial write (error_idx is not set by this driver)")
                return
            rec["applied"] = True
        except DeadlineExceeded:
            raise
        except CameraError as exc:
            rec["error"] = f"{exc}; no change"
            return
        if "again_permille" in rec["requested"]:
            try:   # best effort: one I2C read of 0x0204
                ctl = make_control(CID_SENSOR_GET_ANALOG_GAIN, 0)
                self._ioctl(self.lfd, VIDIOC_G_CTRL, ctl, "G_CTRL GET_ANALOG_GAIN")
                rec["readback_again_permille"] = struct.unpack("<Ii", ctl)[1]
            except DeadlineExceeded:
                raise
            except CameraError as exc:
                rec["readback_error"] = str(exc)

    def frames(self, timeout_first: float, timeout_next: float, deadline: float):
        """Yield (raw_bytes, info) for each VC0 frame; requeues buffers."""
        got_first = False
        last_frame = time.monotonic()
        while True:
            now = time.monotonic()
            if now >= deadline:
                raise DeadlineExceeded("hard deadline reached")
            limit = timeout_next if got_first else timeout_first
            # Leader (shot) buffers keep completing even when VC0 never
            # delivers, so the frame timeout is measured from the last VC0
            # frame, not from the last select() wake-up.
            if now - last_frame >= limit:
                raise CameraError(("no VC0 frame within %.1fs" % limit) +
                                  ("" if got_first else " (first frame)"))
            wait = min(limit - (now - last_frame), deadline - now)
            r, w = self.sys.select([self.vfd], [self.lfd], wait)
            if not r and not w:
                continue
            if self.lfd in w:
                done = self._dqbuf(self.lfd, V4L2_BUF_TYPE_VIDEO_OUTPUT_MPLANE, self.lplanes)
                if done is not None:
                    self._queue_leader(done["index"])
            if self.vfd in r:
                done = self._dqbuf(self.vfd, V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE, self.vplanes)
                if done is None:
                    continue
                idx = done["index"]
                got_first = True
                last_frame = time.monotonic()
                if done["flags"] & V4L2_BUF_FLAG_ERROR:
                    self._queue_vc(idx)
                    yield None, done
                    continue
                data = self.vbufs[idx][0].read(self.image_len)
                self._queue_vc(idx)
                yield data, done

    def stop(self):
        out_t, cap_t = V4L2_BUF_TYPE_VIDEO_OUTPUT_MPLANE, V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE

        def step(what, func):
            self.steps.append(what)
            try:
                func()
            except BaseException as exc:  # keep going: every step is independent
                self.teardown_errors.append(f"{what}: {exc!r}")

        if self.lfd is not None and self.state["front_start"]:
            step("S_CTRL IS_S_STREAM off",
                 lambda: self.sys.ioctl(self.lfd, VIDIOC_S_CTRL, make_control(CID_IS_S_STREAM, 0)))
        if self.lfd is not None and self.state["leader_streamon"]:
            step("STREAMOFF leader", lambda: self.sys.ioctl(
                self.lfd, VIDIOC_STREAMOFF, bytearray(struct.pack("<I", out_t))))
        if self.vfd is not None and self.state["vc_streamon"]:
            step("STREAMOFF vc0", lambda: self.sys.ioctl(
                self.vfd, VIDIOC_STREAMOFF, bytearray(struct.pack("<I", cap_t))))
        if self.vfd is not None and self.state["vc_reqbufs"]:
            step("REQBUFS(0) vc0", lambda: self.sys.ioctl(self.vfd, VIDIOC_REQBUFS,
                                                          make_reqbufs(0, cap_t)))
        if self.lfd is not None and self.state["leader_reqbufs"]:
            step("REQBUFS(0) leader", lambda: self.sys.ioctl(self.lfd, VIDIOC_REQBUFS,
                                                             make_reqbufs(0, out_t)))
        if self.vfd is not None:
            step("close vc0", lambda: self.sys.close(self.vfd))
            self.vfd = None
        if self.lfd is not None:
            step("close leader", lambda: self.sys.close(self.lfd))
            self.lfd = None
        for pair in self.vbufs + self.lbufs:
            for dbuf in pair:
                step("release dmabuf", dbuf.release)
        self.vbufs, self.lbufs = [], []
        try:
            self.rails_snapshot("after_close")
        except BaseException as exc:
            self.teardown_errors.append(f"rails after_close: {exc!r}")


class _Alarm:
    """SIGALRM backstop for the hard deadline (main thread, real device only)."""

    def __init__(self, seconds: float, enabled: bool):
        self.seconds = seconds
        self.enabled = enabled and threading.current_thread() is threading.main_thread()
        self.prev = None

    def _fire(self, signum, frame):
        raise DeadlineExceeded("hard deadline (SIGALRM)")

    def __enter__(self):
        if self.enabled:
            self.prev = signal.signal(signal.SIGALRM, self._fire)
            signal.setitimer(signal.ITIMER_REAL, self.seconds)
        return self

    def rearm(self, seconds: float):
        if self.enabled:
            signal.setitimer(signal.ITIMER_REAL, seconds)

    def __exit__(self, *exc):
        if self.enabled:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, self.prev)
        return False


def run_stream(sysif, args, consume, alarm=True, report=None) -> dict:
    """Common capture/record driver; `consume(raw, info) -> bool` (True = done).

    `report` (the caller's result dict) also receives "cis" when the run
    raises, so a failed run still shows whether exposure/gain were written."""
    if isinstance(sysif, RealSys) and (args.width, args.height, args.fps) != LOCKED_GEOMETRY:
        raise CameraError("geometry locked to %dx%d@%d until a capture is proven" % LOCKED_GEOMETRY)
    info = prechecks(sysif, args)
    pixfmt = PIX_SBGGR16 if args.pixfmt == "sbggr16" else PIX_SBGGR10P
    session = Session(sysif, args.width, args.height, args.fps, pixfmt,
                      vc_bufs=args.buffers, leader=args.leader, vc0=args.vc0,
                      exposure_us=args.exposure_us, iso=args.iso,
                      cis_exposure_us=args.cis_exposure_us, cis_again=args.cis_again,
                      cis_dump=args.cis_dump, cis_i2c_bus=args.cis_i2c_bus,
                      cis_i2c_extended=args.cis_i2c_extended, rails=args.rails,
                      log=(lambda m: print("# " + m, file=sys.stderr)) if args.verbose else None)
    started = time.monotonic()
    deadline = started + args.deadline
    first_timeout = args.first_timeout
    if info.get("mcu_fw") is None:
        first_timeout = max(first_timeout, 90.0)
    result = {"stride": session.stride, "pixfmt": fourcc_str(pixfmt), "errors": 0}
    with _Alarm(args.deadline + 5, alarm) as backstop:
        try:
            session.start()
            for raw, frame in session.frames(first_timeout, args.timeout, deadline):
                if raw is None:
                    result["errors"] += 1
                    continue
                if consume(raw, frame):
                    session.dump_state("before_stop")
                    break
        finally:
            backstop.rearm(30)   # teardown gets its own bounded window
            session.stop()
            if session.cis is not None:
                result["cis"] = session.cis
                if report is not None:
                    report["cis"] = session.cis
            if session.dump is not None:
                result["dump"] = session.dump
                if report is not None:
                    report["dump"] = session.dump
    result["ms"] = int((time.monotonic() - started) * 1000)
    result["teardown_errors"] = session.teardown_errors
    return result


# --------------------------------------------------------------------------
# Raw decoding and image processing (pure python)
# --------------------------------------------------------------------------
def unpack_raw10_mipi(data: bytes, count: int) -> list:
    """MIPI CSI-2 RAW10: 4 MSB bytes then one byte with the 4x2 LSBs."""
    groups = count // 4
    b = bytes(data[:groups * 5])
    ls = b[4::5]
    out = [0] * (groups * 4)
    for i in range(4):
        sh = 2 * i
        out[i::4] = [(m << 2) | ((l >> sh) & 3) for m, l in zip(b[i::5], ls)]
    return out


def unpack_raw10_lsb(data: bytes, count: int) -> list:
    """Bit-continuous little-endian 10-bit packing (4 pixels per 5 bytes)."""
    groups = count // 4
    b = bytes(data[:groups * 5])
    b0, b1, b2, b3, b4 = b[0::5], b[1::5], b[2::5], b[3::5], b[4::5]
    out = [0] * (groups * 4)
    out[0::4] = [x | ((y & 0x03) << 8) for x, y in zip(b0, b1)]
    out[1::4] = [(x >> 2) | ((y & 0x0F) << 6) for x, y in zip(b1, b2)]
    out[2::4] = [(x >> 4) | ((y & 0x3F) << 4) for x, y in zip(b2, b3)]
    out[3::4] = [(x >> 6) | (y << 2) for x, y in zip(b3, b4)]
    return out


def pack_raw10_mipi(pixels) -> bytes:
    out = bytearray()
    for i in range(0, len(pixels), 4):
        p = pixels[i:i + 4]
        out += bytes(v >> 2 for v in p)
        out.append(sum((v & 3) << (2 * j) for j, v in enumerate(p)))
    return bytes(out)


def pack_raw10_lsb(pixels) -> bytes:
    out = bytearray()
    for i in range(0, len(pixels), 4):
        v = 0
        for j, p in enumerate(pixels[i:i + 4]):
            v |= (p & 0x3FF) << (10 * j)
        out += v.to_bytes(5, "little")
    return bytes(out)


def _row_reader(raw: bytes, width: int, stride: int, fmt: str):
    view = memoryview(raw)
    if fmt == "sbggr16":
        swap = sys.byteorder != "little"

        def row(y):
            arr = array("H")
            arr.frombytes(view[y * stride:y * stride + 2 * width])
            if swap:
                arr.byteswap()
            return arr
        return row
    unpack = unpack_raw10_mipi if fmt == "raw10-mipi" else unpack_raw10_lsb

    def row10(y):
        return unpack(view[y * stride:y * stride + (width * 10 + 7) // 8], width)
    return row10


def detect_packing(raw: bytes, width: int, height: int, stride: int) -> str:
    """Pick the RAW10 packing whose decode is smoother (same-colour neighbours)."""
    scores = {}
    for fmt in ("raw10-mipi", "raw10-lsb"):
        row = _row_reader(raw, width, stride, fmt)
        total = n = 0
        for y in range(height // 3, min(height, height // 3 + 16)):
            r = row(y)
            total += sum(abs(a - b) for a, b in zip(r[0::1], r[2::1]))
            n += len(r) - 2
        scores[fmt] = total / max(1, n)
    return min(scores, key=scores.get)


def bin_bayer(raw: bytes, width: int, height: int, stride: int, fmt: str,
              pattern: str = GN3_BAYER, k: int = 2):
    """2x2 Bayer quads -> RGB sums, box-binned over k x k quads.

    Returns (ow, oh, rows_r, rows_g, rows_b, n) where each R/B value is a sum of
    n=k*k samples and each G value a sum of 2n samples.
    """
    pattern = pattern.upper()
    if sorted(pattern) != sorted("RGGB"):
        raise ValueError("bayer pattern must be a permutation of RGGB")
    pos = {(0, 0): pattern[0], (0, 1): pattern[1], (1, 0): pattern[2], (1, 1): pattern[3]}
    r_pos = next(p for p, c in pos.items() if c == "R")
    b_pos = next(p for p, c in pos.items() if c == "B")
    g_pos = [p for p, c in pos.items() if c == "G"]
    quads_w, quads_h = width // 2, height // 2
    ow, oh = quads_w // k, quads_h // k
    span = ow * k
    row = _row_reader(raw, width, stride, fmt)

    def hbin(seq):
        acc = list(seq[0:span:k])
        for j in range(1, k):
            acc = list(map(add, acc, seq[j:span:k]))
        return acc

    rows_r, rows_g, rows_b = [], [], []
    for oy in range(oh):
        acc_r = acc_g = acc_b = None
        for qy in range(k):
            y = (oy * k + qy) * 2
            ra, rb = row(y), row(y + 1)
            sub = {(0, 0): ra[0::2], (0, 1): ra[1::2], (1, 0): rb[0::2], (1, 1): rb[1::2]}
            r = hbin(sub[r_pos])
            b = hbin(sub[b_pos])
            g = hbin(list(map(add, sub[g_pos[0]], sub[g_pos[1]])))
            if acc_r is None:
                acc_r, acc_g, acc_b = r, g, b
            else:
                acc_r = list(map(add, acc_r, r))
                acc_g = list(map(add, acc_g, g))
                acc_b = list(map(add, acc_b, b))
        rows_r.append(acc_r)
        rows_g.append(acc_g)
        rows_b.append(acc_b)
    return ow, oh, rows_r, rows_g, rows_b, k * k


def develop(raw: bytes, width: int, height: int, stride: int, fmt: str = "sbggr16",
            pattern: str = GN3_BAYER, scale: int = 4, black: float | None = 64.0,
            wb: str = "grayworld", auto_exposure: bool = True, white: float | None = None,
            gamma: float = 2.2, percentile: float = 99.0):
    """Raw Bayer -> list of RGB888 rows (binned demosaic, WB, exposure, gamma)."""
    if scale < 2 or scale % 2:
        raise ValueError("scale must be an even integer >= 2")
    if fmt == "raw10p":
        fmt = detect_packing(raw, width, height, stride)
    ow, oh, rr, rg, rb, n = bin_bayer(raw, width, height, stride, fmt, pattern, scale // 2)
    peak = max(max(max(r) for r in rr) / n, max(max(g) for g in rg) / (2 * n),
               max(max(b) for b in rb) / n, 1)
    bits = max(10, int(peak).bit_length())
    if black is None:
        black = 0.0
    black = black * (1 << (bits - 10))
    full = float((1 << bits) - 1)
    tot_r = sum(map(sum, rr)) / (ow * oh * n) - black
    tot_g = sum(map(sum, rg)) / (ow * oh * 2 * n) - black
    tot_b = sum(map(sum, rb)) / (ow * oh * n) - black
    gains = [1.0, 1.0, 1.0]
    if wb == "grayworld" and min(tot_r, tot_g, tot_b) > 0:
        gains = [min(8.0, max(0.25, tot_g / tot_r)), 1.0, min(8.0, max(0.25, tot_g / tot_b))]
    if white is None:
        if auto_exposure:
            samples = []
            step = max(1, (ow * oh) // 20000)
            i = 0
            for y in range(oh):
                r_row, g_row, b_row = rr[y], rg[y], rb[y]
                for x in range(i % step, ow, step):
                    samples.append(max((r_row[x] / n - black) * gains[0],
                                       (g_row[x] / (2 * n) - black),
                                       (b_row[x] / n - black) * gains[2]))
                i += 1
            samples.sort()
            white = samples[min(len(samples) - 1, int(len(samples) * percentile / 100.0))]
            white = max(white, 16.0 * (1 << (bits - 10)))
        else:
            white = full - black
    inv = 1.0 / gamma

    def lut(count, gain, maxsum):
        table = bytearray(maxsum + 1)
        for s in range(maxsum + 1):
            x = (s / count - black) * gain / white
            if x <= 0:
                continue
            table[s] = 255 if x >= 1 else int(255 * x ** inv + 0.5)
        return bytes(table)

    lr = lut(n, gains[0], max(map(max, rr)))
    lg = lut(2 * n, gains[1], max(map(max, rg)))
    lb = lut(n, gains[2], max(map(max, rb)))
    out_rows = []
    for y in range(oh):
        line = bytearray(3 * ow)
        line[0::3] = bytes(map(lr.__getitem__, rr[y]))
        line[1::3] = bytes(map(lg.__getitem__, rg[y]))
        line[2::3] = bytes(map(lb.__getitem__, rb[y]))
        out_rows.append(bytes(line))
    stats = {"bits": bits, "black": black, "white": white, "gains": [round(g, 4) for g in gains],
             "fmt": fmt, "pattern": pattern}
    return ow, oh, out_rows, stats


def png_bytes(width: int, height: int, rows) -> bytes:
    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))
    raw = b"".join(b"\x00" + bytes(r) for r in rows)
    if len(rows) != height or any(len(r) != 3 * width for r in rows):
        raise ValueError("row geometry mismatch")
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def ppm_bytes(width: int, height: int, rows) -> bytes:
    return b"P6\n%d %d\n255\n" % (width, height) + b"".join(bytes(r) for r in rows)


def write_image(path: str, width: int, height: int, rows) -> None:
    data = ppm_bytes(width, height, rows) if path.lower().endswith((".ppm", ".pnm")) \
        else png_bytes(width, height, rows)
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


def write_raw(path: str, raw: bytes, meta: dict) -> None:
    with open(path, "wb") as f:
        f.write(raw)
    with open(path + ".json", "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=1, sort_keys=True)


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------
def classify_node(name: str) -> str:
    if name.startswith("exynos-is-ss") and "vc" in name:
        return "csis-vc-dma"
    if name.startswith("exynos-is-ss"):
        return "sensor-leader"
    if name.startswith("exynos-is-"):
        return "isp-chain (needs DDK/TrustZone)"
    return "other"


def cmd_list(args, sysif=None) -> int:
    sysif = sysif or RealSys()
    base = "/sys/class/video4linux"
    nodes = []
    try:
        entries = sorted(os.listdir(base), key=lambda s: (len(s), s))
    except OSError:
        entries = []
    for entry in entries:
        name = (sysif.read_text(f"{base}/{entry}/name") or "").strip()
        dev = (sysif.read_text(f"{base}/{entry}/dev") or "").strip()
        nodes.append({"node": f"/dev/{entry}", "name": name, "dev": dev,
                      "role": classify_node(name)})
    report = {"nodes": nodes, "leader": LEADER_NODE, "vc0": VC0_NODE,
              "mcu_fw": find_mcu_firmware(sysif), "selftest_active": selftest_active(sysif)}
    if args.probe:
        report["probe"] = probe_node(sysif, args.probe)
    print(json.dumps(report, indent=1))
    return 0


def probe_node(sysif, node: str) -> dict:
    """POWERED: open + QUERYCAP + ENUM_FMT/G_FMT attempts + close."""
    out = {"node": node}
    fd = sysif.open(node)
    try:
        cap = bytearray(SIZEOF["v4l2_capability"])
        sysif.ioctl(fd, VIDIOC_QUERYCAP, cap)
        out["cap"] = parse_capability(cap)
        mplane_out = out["cap"]["device_caps"] & 0x2000  # V4L2_CAP_VIDEO_OUTPUT_MPLANE
        buf_type = V4L2_BUF_TYPE_VIDEO_OUTPUT_MPLANE if mplane_out else V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE
        fmts = []
        for i in range(32):
            desc = bytearray(SIZEOF["v4l2_fmtdesc"])
            struct.pack_into("<II", desc, 0, i, buf_type)
            try:
                sysif.ioctl(fd, VIDIOC_ENUM_FMT, desc)
            except OSError as exc:
                if not fmts:
                    out["enum_fmt"] = errno.errorcode.get(exc.errno, str(exc.errno))
                break
            fmts.append(fourcc_str(struct.unpack_from("<I", desc, 44)[0]))
        out["formats"] = fmts
        fmt = bytearray(SIZEOF["v4l2_format"])
        struct.pack_into("<I", fmt, 0, buf_type)
        try:
            sysif.ioctl(fd, VIDIOC_G_FMT, fmt)
            w, h, pf = struct.unpack_from("<III", fmt, 8)
            out["g_fmt"] = {"width": w, "height": h, "pixelformat": fourcc_str(pf)}
        except OSError as exc:
            out["g_fmt"] = errno.errorcode.get(exc.errno, str(exc.errno))
    finally:
        sysif.close(fd)
    return out


def _develop_args(args):
    return {"pattern": args.bayer, "scale": args.scale, "black": args.black,
            "wb": args.wb, "gamma": args.gamma}


def cmd_capture(args, sysif=None, alarm=True) -> int:
    sysif = sysif or RealSys()
    t0 = time.monotonic()
    holder = {"seen": 0, "raw": None, "frame": None}

    def consume(raw, frame):
        holder["seen"] += 1
        if holder["seen"] <= args.skip:
            return False
        holder["raw"], holder["frame"] = raw, frame
        holder.setdefault("kept", 0)
        holder["kept"] += 1
        return holder["kept"] >= args.frames

    result = {"ok": False, "path": args.out, "node": args.vc0, "leader": args.leader,
              "width": None, "height": None, "format": None}
    try:
        stream = run_stream(sysif, args, consume, alarm=alarm, report=result)
        result.update(stream)
        if holder["raw"] is None:
            raise CameraError("no frame captured")
        fmt = args.pixfmt
        meta = {"width": args.width, "height": args.height, "stride": stream["stride"],
                "pixfmt": stream["pixfmt"], "bayer": args.bayer, "sequence": holder["frame"]["sequence"],
                "timestamp": holder["frame"]["timestamp"], "sensor": "S5KGN3", "mode": "2040x1532@30"}
        if args.raw:
            write_raw(args.raw, holder["raw"], meta)
            result["raw"] = args.raw
        ow, oh, rows, stats = develop(holder["raw"], args.width, args.height, stream["stride"],
                                      fmt, **_develop_args(args))
        write_image(args.out, ow, oh, rows)
        result.update({"ok": True, "width": ow, "height": oh,
                       "format": "ppm" if args.out.lower().endswith((".ppm", ".pnm")) else "png",
                       "frames": holder["seen"], "develop": stats})
    except (CameraError, OSError) as exc:
        result["error"] = str(exc)
    result["ms"] = int((time.monotonic() - t0) * 1000)
    print(json.dumps(result, sort_keys=True))
    return 0 if result["ok"] else 1


def cmd_record(args, sysif=None, alarm=True) -> int:
    sysif = sysif or RealSys()
    os.makedirs(args.out, exist_ok=True)
    t0 = time.monotonic()
    state = {"start": None, "last": None, "kept": 0, "seen": 0}
    min_dt = 1.0 / args.fps_limit if args.fps_limit > 0 else 0.0
    result = {"ok": False, "dir": args.out, "node": args.vc0}

    def consume(raw, frame):
        now = time.monotonic()
        state["seen"] += 1
        if state["start"] is None:
            state["start"] = now
        if state["last"] is None or now - state["last"] >= min_dt:
            path = os.path.join(args.out, "frame_%05d.raw" % state["kept"])
            with open(path, "wb") as f:
                f.write(raw)
            state["kept"] += 1
            state["last"] = now
        return now - state["start"] >= args.seconds

    args.deadline = max(args.deadline, args.seconds + args.first_timeout + 30)
    try:
        stream = run_stream(sysif, args, consume, alarm=alarm, report=result)
        result.update(stream)
        meta = {"width": args.width, "height": args.height, "stride": stream["stride"],
                "pixfmt": stream["pixfmt"], "bayer": args.bayer, "frames": state["kept"]}
        with open(os.path.join(args.out, "frames.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=1)
        # Develop after the sensor is stopped, keeping the stream window short.
        for i in range(state["kept"]):
            path = os.path.join(args.out, "frame_%05d.raw" % i)
            with open(path, "rb") as f:
                raw = f.read()
            ow, oh, rows, _ = develop(raw, args.width, args.height, stream["stride"],
                                      args.pixfmt, **_develop_args(args))
            write_image(os.path.join(args.out, "frame_%05d.png" % i), ow, oh, rows)
            if not args.keep_raw:
                os.unlink(path)
        result.update({"ok": state["kept"] > 0, "frames": state["kept"], "seen": state["seen"]})
        if args.mjpeg and state["kept"]:
            result["mjpeg"] = make_mjpeg(args.out, args.fps_limit or args.fps)
    except (CameraError, OSError) as exc:
        result["error"] = str(exc)
    result["ms"] = int((time.monotonic() - t0) * 1000)
    print(json.dumps(result, sort_keys=True))
    return 0 if result["ok"] else 1


GST_CHROOT = "/mnt/omarchy-trial"


def make_mjpeg(directory: str, fps: float) -> dict:
    """Optional: PNG sequence -> MJPEG AVI via gst-launch-1.0 (host PATH or Arch chroot)."""
    directory = os.path.abspath(directory)
    rate = max(1, int(round(fps)))
    pipeline = ["multifilesrc", "location=%s/frame_%%05d.png" % "{dir}", "index=0",
                "caps=image/png,framerate=%d/1" % rate, "!", "pngdec", "!", "videoconvert",
                "!", "jpegenc", "!", "avimux", "!", "filesink", "location={dir}/video.avi"]
    gst = shutil.which("gst-launch-1.0")
    if gst:
        cmd = [gst, "-q"] + [p.replace("{dir}", directory) for p in pipeline]
    elif (os.path.exists(GST_CHROOT + "/usr/bin/gst-launch-1.0")
          and directory.startswith(GST_CHROOT + "/") and shutil.which("chroot")):
        inner = directory[len(GST_CHROOT):]
        cmd = ["chroot", GST_CHROOT, "/usr/bin/gst-launch-1.0", "-q"] + \
            [p.replace("{dir}", inner) for p in pipeline]
    else:
        return {"ok": False, "skipped": "gst-launch-1.0 not available (or output dir not inside "
                + GST_CHROOT + ")"}
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=300)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": proc.returncode == 0, "path": os.path.join(directory, "video.avi"),
            "stderr": proc.stderr.decode("utf-8", "replace")[-400:]}


def cmd_develop(args) -> int:
    """Re-develop a saved .raw (+ .raw.json sidecar) on any machine."""
    with open(args.raw, "rb") as f:
        raw = f.read()
    meta = {}
    if os.path.exists(args.raw + ".json"):
        with open(args.raw + ".json", encoding="utf-8") as f:
            meta = json.load(f)
    width = args.width or meta.get("width", 2040)
    height = args.height or meta.get("height", 1532)
    fmt = args.pixfmt or ("sbggr16" if meta.get("pixfmt", "BYR2") == "BYR2" else "raw10p")
    stride = args.stride or meta.get("stride") or vc0_stride(
        width, PIX_SBGGR16 if fmt == "sbggr16" else PIX_SBGGR10P)
    ow, oh, rows, stats = develop(raw, width, height, stride, fmt, **_develop_args(args))
    write_image(args.out, ow, oh, rows)
    print(json.dumps({"ok": True, "path": args.out, "width": ow, "height": oh, "develop": stats}))
    return 0


def _add_stream_args(p):
    p.add_argument("--width", type=int, default=2040)
    p.add_argument("--height", type=int, default=1532)
    p.add_argument("--fps", type=int, default=30)
    p.add_argument("--pixfmt", choices=("sbggr16", "raw10p"), default="sbggr16")
    p.add_argument("--buffers", type=int, default=3, help="VC0 dma-buf count")
    p.add_argument("--leader", default=LEADER_NODE)
    p.add_argument("--vc0", default=VC0_NODE)
    p.add_argument("--timeout", type=float, default=5.0, help="per-frame select timeout (s)")
    p.add_argument("--first-timeout", type=float, default=20.0)
    p.add_argument("--deadline", type=float, default=120.0, help="hard total deadline (s)")
    p.add_argument("--exposure-us", type=int, default=0,
                   help="shot.ctl exposure; IGNORED without the DDK (use --cis-exposure-us)")
    p.add_argument("--iso", type=int, default=0,
                   help="shot.ctl sensitivity; IGNORED without the DDK (use --cis-again)")
    p.add_argument("--cis-exposure-us", type=int, default=0,
                   help="GN3 integration time in us, written once after stream-on "
                        "(clamped %d..%d; 0 = leave sensor default)" % CIS_EXPOSURE_US_RANGE)
    p.add_argument("--cis-again", type=float, default=0.0,
                   help="GN3 analog gain multiplier, written once after stream-on "
                        "(clamped %g..%g; 0 = leave default)" % tuple(
                            v / 1000 for v in CIS_AGAIN_PERMILLE_RANGE))
    p.add_argument("--cis-dump", action="store_true",
                   help="read-only: G_CTRL stream/DTP/CSIS-error/gain readbacks after "
                        "stream-on and before stop")
    p.add_argument("--cis-i2c-bus", type=int, default=None, metavar="N",
                   help="read-only GN3 register dump via /dev/i2c-N (i2c-dev must already "
                        "be loaded; N-0010 must be the GN3 in sysfs)")
    p.add_argument("--cis-i2c-extended", action="store_true",
                   help="with --cis-i2c-bus: also read back the page-0x4000 registers the "
                        "driver's setfiles write and report mismatches (read-only)")
    p.add_argument("--rails", action="store_true",
                   help="read-only sysfs snapshot of the GN3 S2MPB02 rails (state, microvolts, "
                        "num_users) before open, after start, before stop and after close")
    p.add_argument("--allow-fw-stall", action="store_true",
                   help="run even if is_mcu_fw.bin is not staged (~60 s stall)")
    p.add_argument("--skip-node-check", action="store_true")
    p.add_argument("-v", "--verbose", action="store_true", help="log each ioctl to stderr")
    _add_develop_args(p)


def _add_develop_args(p):
    p.add_argument("--bayer", default=GN3_BAYER, help="CFA order (GN3: GBRG)")
    p.add_argument("--scale", type=int, default=4, help="even downscale factor (2,4,8)")
    p.add_argument("--black", type=float, default=64.0, help="black level in 10-bit units")
    p.add_argument("--wb", choices=("grayworld", "none"), default="grayworld")
    p.add_argument("--gamma", type=float, default=2.2)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("list", help="enumerate video nodes from sysfs (no open)")
    p.add_argument("--probe", metavar="NODE", help="POWERED: open NODE, QUERYCAP/ENUM_FMT/G_FMT")
    p = sub.add_parser("capture", help="capture one developed still")
    p.add_argument("--out", required=True)
    p.add_argument("--raw")
    p.add_argument("--frames", type=int, default=1)
    p.add_argument("--skip", type=int, default=4, help="frames to discard first (AE/settle)")
    _add_stream_args(p)
    p = sub.add_parser("record", help="frame sequence into a directory")
    p.add_argument("--out", required=True)
    p.add_argument("--seconds", type=float, default=3.0)
    p.add_argument("--fps-limit", type=float, default=5.0)
    p.add_argument("--mjpeg", action="store_true", help="also build video.avi via gst-launch-1.0")
    p.add_argument("--keep-raw", action="store_true")
    _add_stream_args(p)
    p = sub.add_parser("develop", help="develop a saved .raw (host or phone)")
    p.add_argument("raw")
    p.add_argument("--out", required=True)
    p.add_argument("--width", type=int)
    p.add_argument("--height", type=int)
    p.add_argument("--stride", type=int)
    p.add_argument("--pixfmt", choices=("sbggr16", "raw10p"))
    _add_develop_args(p)
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd == "list":
        return cmd_list(args)
    if args.cmd == "capture":
        return cmd_capture(args)
    if args.cmd == "record":
        return cmd_record(args)
    return cmd_develop(args)


if __name__ == "__main__":
    sys.exit(main())
