#!/usr/bin/env python3
"""Pin s22-campreview's ioctl struct bytes to the reviewed Python client (s22-camera.py).

Runs `s22-campreview dump-structs` (host build) and compares every struct with
the bytes the Python builders produce. Run after `cargo build --release` in
tools/hardware/camera/s22-campreview.
"""
import ctypes
import importlib.util
import json
import os
import struct
import subprocess
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
CAM = os.path.join(HERE, "..", "s22-camera.py")
BIN = os.environ.get("CAMPREVIEW_BIN",
                     os.path.join(HERE, "..", "s22-campreview", "target", "release", "s22-campreview"))


def load():
    spec = importlib.util.spec_from_file_location("s22_camera_client", CAM)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class StructBytes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cam = load()
        cls.d = json.loads(subprocess.check_output([BIN, "dump-structs"], text=True))

    def h(self, key):
        return bytes.fromhex(self.d[key])

    def test_ioctl_numbers(self):
        c, io = self.cam, self.d["ioctls"]
        for name, val in (("S_FMT", c.VIDIOC_S_FMT), ("REQBUFS", c.VIDIOC_REQBUFS), ("QBUF", c.VIDIOC_QBUF),
                          ("DQBUF", c.VIDIOC_DQBUF), ("STREAMON", c.VIDIOC_STREAMON),
                          ("STREAMOFF", c.VIDIOC_STREAMOFF), ("S_PARM", c.VIDIOC_S_PARM),
                          ("G_CTRL", c.VIDIOC_G_CTRL), ("S_CTRL", c.VIDIOC_S_CTRL),
                          ("S_INPUT", c.VIDIOC_S_INPUT), ("S_EXT_CTRLS", c.VIDIOC_S_EXT_CTRLS),
                          ("HEAP_ALLOC", c.DMA_HEAP_IOCTL_ALLOC), ("SYNC", c.DMA_BUF_IOCTL_SYNC)):
            self.assertEqual(io[name], val, name)

    def test_values(self):
        c = self.cam
        self.assertEqual(self.d["s_input_rear"], c.s_input_value(1, 0, 1, 1))
        self.assertEqual(self.d["s_input_front"], c.s_input_value(1, 1, 2, 1))
        self.assertEqual(self.d["size_rear"], c.sensor_size_value(2040, 1532))
        self.assertEqual(self.d["size_front"], c.sensor_size_value(1824, 1368))
        self.assertEqual(self.d["stride_rear"], c.vc0_stride(2040, c.PIX_SBGGR16))
        self.assertEqual(self.d["stride_front"], c.vc0_stride(1824, c.PIX_SBGGR16))
        self.assertEqual(self.d["leader_img_rear"], c.page_round(c.leader_image_size(2040, 1532)))

    def test_structs(self):
        c = self.cam
        self.assertEqual(self.h("fmt_leader"), bytes(c.make_format_mplane(
            c.V4L2_BUF_TYPE_VIDEO_OUTPUT_MPLANE, 2040, 1532, c.PIX_SRGB36P_SP, 2)))
        self.assertEqual(self.h("fmt_vc0"), bytes(c.make_format_mplane(
            c.V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE, 2040, 1532, c.PIX_SBGGR16, 2,
            c.vc0_stride(2040, c.PIX_SBGGR16))))
        self.assertEqual(self.h("reqbufs"), bytes(c.make_reqbufs(4, c.V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE)))
        self.assertEqual(self.h("parm"), bytes(c.make_streamparm_capture(30)))
        self.assertEqual(self.h("control"), bytes(c.make_control(c.CID_IS_S_STREAM, 1)))
        self.assertEqual(self.h("buffer"), bytes(c.make_buffer(
            3, c.V4L2_BUF_TYPE_VIDEO_OUTPUT_MPLANE, 0x1122334455667788, 2)))
        planes = c.PlaneArray(2)
        planes.set(0, 7, 0x100000, 0x100000)
        planes.set(1, 9, 57344, 0)
        self.assertEqual(self.h("planes"), bytes(planes.mem.raw))
        self.assertEqual(self.h("shot_rear"), bytes(c.build_shot(2040, 1532, c.PIX_SBGGR16, device=0)))
        self.assertEqual(self.h("shot_front"), bytes(c.build_shot(1824, 1368, c.PIX_SBGGR16, device=1)))

    def test_ext_controls(self):
        c = self.cam
        ext = c.ExtControls([(c.CID_SENSOR_SET_AE_TARGET, 10000), (c.CID_SENSOR_SET_ANALOG_GAIN, 1999)])
        self.assertEqual(self.h("ext_array"), bytes(ext.mem.raw))
        hdr = bytearray(ext.buf)
        struct.pack_into("<Q", hdr, 24, 0x1122334455667788)   # the array address differs per process
        self.assertEqual(self.h("ext_hdr"), bytes(hdr))


if __name__ == "__main__":
    unittest.main()
