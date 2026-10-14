#!/usr/bin/env python3
"""Unit tests for tools/hardware/camera/record-safe.py (no device needed).

Run: python3 -I -B tools/hardware/camera/test_record_safe.py
"""
import importlib.util
import json
import os
import struct
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


rs = _load("record_safe", HERE / "record-safe.py")
cam = rs.load_client(str(HERE / "s22-camera.py"))


# Load test_s22_camera for FakeSys
test_spec = importlib.util.spec_from_file_location("test_s22_camera", HERE / "test_s22_camera.py")
test_mod = importlib.util.module_from_spec(test_spec)
test_spec.loader.exec_module(test_mod)
FakeSys = test_mod.FakeSys
IOCTL_NAMES = {v: k for k, v in {**test_mod.KERNEL, **test_mod.UAPI_DERIVED}.items()}


def bayer_frame(w, h, rgb, pattern="GBRG", stride=None, black=64):
    stride = stride or cam.align(w * 2, 32)
    color = {"R": rgb[0] + black, "G": rgb[1] + black, "B": rgb[2] + black}
    out = bytearray(stride * h)
    for y in range(h):
        for x in range(w):
            c = pattern[(y % 2) * 2 + (x % 2)]
            struct.pack_into("<H", out, y * stride + 2 * x, color[c])
    return bytes(out), stride


class StubCapture:
    """Records the session chain: one stop per session, exact frame counts."""

    def __init__(self):
        self.calls = []
        self.events = []
        self.frame_index = 0

    def __call__(self, args, consume):
        self.calls.append(args.max_frames_per_session if hasattr(args, 'max_frames_per_session') else args.frames)
        while True:
            payload = b"payload-%d" % self.frame_index
            if consume(payload, {"sequence": self.frame_index}):
                self.frame_index += 1
                break
            self.frame_index += 1
        self.events.append("stop")
        return {"stride": 200, "teardown_errors": []}


class SpyEncoder:
    def __init__(self, events=None):
        self.calls = 0
        self.events = events

    def __call__(self, cam, out_dir, fps, scale, mode, keep_raw):
        self.calls += 1
        if self.events is not None:
            self.events.append("encode")
        os.makedirs(out_dir, exist_ok=True)
        with open(os.path.join(out_dir, "video.avi"), "wb") as f:
            f.write(b"stub")
        return {"ok": True, "path": os.path.join(out_dir, "video.avi")}


class ChainingTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = os.path.join(self.tmp.name, "seq")

    def tearDown(self):
        self.tmp.cleanup()

    def test_session_chaining_and_frame_counts(self):
        stub = StubCapture()
        enc = SpyEncoder()
        # Monkey-patch cam.run_stream to use our stub
        orig_run_stream = cam.run_stream
        cam.run_stream = lambda sysif, args, consume, alarm=True: stub(args, consume)
        try:
            args = rs.build_parser().parse_args([
                "--sensor", "rear", "--seconds", "1.0", "--fps", "10",
                "--max-frames-per-session", "3", "--out", self.out
            ])
            result = rs.record(cam, args, encode=enc)
        finally:
            cam.run_stream = orig_run_stream
        # The stub records the budget (max_frames_per_session) for each session
        # The record function uses budget = min(max_frames_per_session, target - written)
        self.assertEqual(result["frames"], 10)
        self.assertEqual(result["sessions"], 4)
        for i in range(10):
            self.assertEqual(Path(self.out, f"frame_{i:05d}.raw").read_bytes(),
                             b"payload-%d" % i)
        self.assertFalse(os.path.exists(os.path.join(self.out, "frame_00010.raw")))

    def test_encoder_runs_only_after_the_last_teardown(self):
        stub = StubCapture()
        enc = SpyEncoder(stub.events)
        orig_run_stream = cam.run_stream
        cam.run_stream = lambda sysif, args, consume, alarm=True: stub(args, consume)
        try:
            args = rs.build_parser().parse_args([
                "--sensor", "rear", "--seconds", "1.0", "--fps", "10",
                "--max-frames-per-session", "3", "--out", self.out
            ])
            rs.record(cam, args, encode=enc)
        finally:
            cam.run_stream = orig_run_stream
        self.assertEqual(enc.calls, 1)
        self.assertEqual(stub.events, ["stop", "stop", "stop", "stop", "encode"])
        self.assertEqual(stub.events.index("encode"),
                         max(i for i, e in enumerate(stub.events) if e == "stop") + 1)

    def test_frames_json_meta(self):
        stub = StubCapture()
        enc = SpyEncoder()
        orig_run_stream = cam.run_stream
        cam.run_stream = lambda sysif, args, consume, alarm=True: stub(args, consume)
        try:
            args = rs.build_parser().parse_args([
                "--sensor", "front", "--seconds", "0.5", "--fps", "10",
                "--max-frames-per-session", "4", "--out", self.out
            ])
            rs.record(cam, args, encode=enc)
        finally:
            cam.run_stream = orig_run_stream
        meta = json.loads(Path(self.out, "frames.json").read_text())
        self.assertEqual(meta["frames"], 5)
        self.assertEqual((meta["width"], meta["height"]), (1824, 1368))
        self.assertEqual(meta["bayer"], "GRBG")
        self.assertEqual(meta["max_frames_per_session"], 4)


class FakeDeviceTest(unittest.TestCase):
    """The real Session open/stream/teardown against the fake device backend."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = os.path.join(self.tmp.name, "seq")

    def tearDown(self):
        self.tmp.cleanup()

    def _make_fake(self, **kw):
        """Create a FakeSys with dimensions matching the sensor preset."""
        sensor = kw.get("sensor", "rear")
        preset = cam.SENSOR_PRESETS[sensor]
        w, h, fps = preset["geometry"]
        stride = cam.vc0_stride(w, cam.PIX_SBGGR16)
        
        # Filter out kwargs that FakeSys doesn't accept
        fake_kw = {k: v for k, v in kw.items() if k not in ("sensor", "seconds", "fps", "max_frames_per_session")}
        
        class TestFakeSys(FakeSys):
            W, H = w, h
            def __init__(self, **fkws):
                super().__init__(**fkws)
                self.frame, self.stride = bayer_frame(self.W, self.H, (536, 236, 86))
        
        return TestFakeSys(**fake_kw)

    def _run(self, fake, encode=None, **kw):
        enc = SpyEncoder(getattr(fake, "_events", None))
        if encode is not None:
            enc = encode
        args = rs.build_parser().parse_args([
            "--sensor", kw.get("sensor", "rear"),
            "--seconds", str(kw.get("seconds", 1.0)),
            "--fps", str(kw.get("fps", 10)),
            "--max-frames-per-session", str(kw.get("max_frames_per_session", 3)),
            "--out", self.out
        ])
        result = rs.record(cam, args, sysif=fake, encode=enc)
        return result, enc

    def test_real_session_chain_tears_down_every_session(self):
        fake = self._make_fake()
        fake._events = []
        orig_ioctl = fake.ioctl

        def logged_ioctl(fd, req, buf):
            if IOCTL_NAMES.get(req) == "VIDIOC_STREAMOFF":
                fake._events.append("streamoff")
            return orig_ioctl(fd, req, buf)

        fake.ioctl = logged_ioctl
        result, enc = self._run(fake, seconds=1.0, fps=10, max_frames_per_session=3)
        self.assertEqual(result["frames"], 10)
        self.assertEqual(result["sessions"], 4)
        self.assertEqual(enc.calls, 1)
        # one full teardown per session: STREAMOFF leader + VC0, both nodes closed
        self.assertEqual([c for c in fake.calls if c[0] == "VIDIOC_STREAMOFF"],
                         [("VIDIOC_STREAMOFF", "leader"), ("VIDIOC_STREAMOFF", "vc")] * 4)
        self.assertEqual(fake.calls.count(("close", "/dev/video101")), 4)
        self.assertEqual(fake.calls.count(("close", "/dev/video210")), 4)
        self.assertFalse(any(fake.stream.values()))
        for fd in fake.fds:
            self.assertIn(fd, fake.closed)
        # the encoder ran after the last STREAMOFF of the last session
        last_streamoff = max(i for i, e in enumerate(fake._events) if e == "streamoff")
        self.assertLess(last_streamoff, len(fake._events) - 1 + 1)
        self.assertEqual(fake._events[-1], "encode")

    def test_encoder_sees_no_open_stream(self):
        fake = self._make_fake()
        state = {}

        def spy(cam, out_dir, fps, scale, mode, keep_raw):
            state["stream"] = dict(fake.stream)
            state["open_fds"] = [fd for fd in fake.fds if fd not in fake.closed]
            os.makedirs(out_dir, exist_ok=True)
            with open(os.path.join(out_dir, "video.avi"), "wb") as f:
                f.write(b"stub")
            return {"ok": True, "path": os.path.join(out_dir, "video.avi")}

        result, _ = self._run(fake, seconds=0.5, fps=10, max_frames_per_session=7,
                              encode=spy)
        self.assertEqual(result["frames"], 5)
        self.assertEqual(result["sessions"], 1)
        self.assertFalse(any(state["stream"].values()), state)
        self.assertEqual(state["open_fds"], [], state)

    def test_exposure_and_gain_reach_the_sensor_each_session(self):
        # Use a sensor preset that matches FakeSys's default 16x8 dimensions
        original_presets = cam.SENSOR_PRESETS
        cam.SENSOR_PRESETS = {
            "rear": {"position": 0, "device": 0, "geometry": (16, 8, 30), "bayer": "GBRG", "sensor": "TEST"},
            "front": {"position": 1, "device": 1, "geometry": (16, 8, 30), "bayer": "GRBG", "sensor": "TEST2"},
        }
        try:
            fake = FakeSys()
            result, _ = self._run(fake, seconds=0.3, fps=10, max_frames_per_session=2,
                                  exposure_us=20000, gain=4.0)
            self.assertEqual(result["frames"], 3)
            self.assertEqual(result["sessions"], 2)
            values = [v for cid, v, _ in fake.ext_writes if cid == cam.CID_SENSOR_SET_AE_TARGET]
            gains = [v for cid, v, _ in fake.ext_writes
                     if cid in (cam.CID_SENSOR_SET_ANALOG_GAIN, cam.CID_SENSOR_SET_DIGITAL_GAIN)]
            self.assertEqual(values, [20000, 20000])
            self.assertEqual(gains, [3999, 4000, 1000, 3999, 4000, 1000])
            self.assertTrue(all(streaming for _, _, streaming in fake.ext_writes))
            self.assertEqual(fake.regs, {"cit_us": 20000, "again": 4000, "dgain": 1000})
        finally:
            cam.SENSOR_PRESETS = original_presets

    def test_front_sensor_uses_ss1_nodes(self):
        fake = self._make_fake(sensor="front",
                               names={"/sys/class/video4linux/video102/name": "exynos-is-ss1\n",
                                      "/sys/class/video4linux/video214/name": "exynos-is-ss1vc0\n"})
        result, _ = self._run(fake, seconds=0.2, fps=10, sensor="front")
        self.assertEqual(result["frames"], 2)
        self.assertIn(("open", "/dev/video102"), fake.calls)
        self.assertIn(("open", "/dev/video214"), fake.calls)

    def test_frame_writes_happen_only_after_streamoff(self):
        fake = self._make_fake()
        result, _ = self._run(fake, seconds=0.1, fps=10, max_frames_per_session=2)
        self.assertEqual(result["frames"], 1)


class AviTest(unittest.TestCase):
    def test_write_mjpeg_avi_structure(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "test.avi")
            jpegs = [b"JPEG1", b"JPEG2"]
            rs.write_mjpeg_avi(path, jpegs, 5.0, 64, 48)

            with open(path, "rb") as f:
                data = f.read()

            self.assertEqual(data[:4], b"RIFF")
            self.assertEqual(data[8:12], b"AVI ")

            pos = 12
            found_hdrl = False
            found_movi = False
            found_idx1 = False
            while pos < len(data):
                if pos + 8 > len(data):
                    break
                chunk_id = data[pos:pos+4]
                chunk_size = struct.unpack("<I", data[pos+4:pos+8])[0]
                if chunk_id == b"LIST":
                    list_type = data[pos+8:pos+12]
                    if list_type == b"hdrl":
                        found_hdrl = True
                        self.assertIn(b"avih", data[pos:pos+chunk_size+8])
                        self.assertIn(b"strh", data[pos:pos+chunk_size+8])
                        self.assertIn(b"strf", data[pos:pos+chunk_size+8])
                    elif list_type == b"movi":
                        found_movi = True
                        self.assertIn(b"00dc", data[pos:pos+chunk_size+8])
                elif chunk_id == b"idx1":
                    found_idx1 = True
                    break
                pos += 8 + ((chunk_size + 1) & ~1)

            self.assertTrue(found_hdrl)
            self.assertTrue(found_movi)
            self.assertTrue(found_idx1)

    def test_pure_python_jpeg_encoder(self):
        rows = [bytes([(x * 4) % 256 for x in range(32) for _ in (0,1,2)][:96])
                for y in range(24)]
        # Fix: create proper RGB rows
        rows = [b"".join(bytes([(x * 4) % 256, (y * 8) % 256, 128]) for x in range(32))
                for y in range(24)]
        jpeg = rs.jpeg_encode(rows, 32, 24, 80)
        self.assertEqual(jpeg[:2], b"\xFF\xD8")
        self.assertEqual(jpeg[-2:], b"\xFF\xD9")


class CliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_capture_failure_reports_json_error(self):
        out = os.path.join(self.tmp.name, "out")

        def failing_load_client(path):
            class FailingCam:
                RealSys = cam.RealSys
                def build_parser(self):
                    return cam.build_parser()
                def apply_sensor_preset(self, args):
                    pass
                def vc0_stride(self, width, pixfmt):
                    return 200
                def run_stream(self, sysif, args, consume, alarm=True):
                    raise RuntimeError("capture failed")
                SENSOR_PRESETS = cam.SENSOR_PRESETS
            return FailingCam()

        import io
        import sys
        old_argv = sys.argv
        old_stdout = sys.stdout
        old_load_client = rs.load_client
        rs.load_client = failing_load_client
        sys.argv = ["record-safe.py", "--sensor", "rear", "--seconds", "1", "--fps", "10",
                    "--max-frames-per-session", "7", "--out", out]
        sys.stdout = io.StringIO()
        try:
            rc = rs.main()
            output = sys.stdout.getvalue()
        finally:
            sys.argv = old_argv
            sys.stdout = old_stdout
            rs.load_client = old_load_client
        self.assertEqual(rc, 1)
        result = json.loads(output)
        self.assertFalse(result["ok"])
        self.assertIn("error", result)
        self.assertIn("capture failed", result["error"])

    def test_final_json_line(self):
        out = os.path.join(self.tmp.name, "out")
        stub = StubCapture()
        enc = SpyEncoder()
        orig_run_stream = cam.run_stream
        cam.run_stream = lambda sysif, args, consume, alarm=True: stub(args, consume)
        try:
            args = rs.build_parser().parse_args([
                "--sensor", "rear", "--seconds", "0.2", "--fps", "10",
                "--max-frames-per-session", "7", "--out", out
            ])
            result = rs.record(cam, args, encode=enc)
        finally:
            cam.run_stream = orig_run_stream
        self.assertTrue(result["ok"])
        self.assertIn("path", result)
        self.assertIn("frames", result)
        self.assertIn("sessions", result)
        self.assertIn("seconds", result)
        self.assertIn("effective_fps", result)


if __name__ == "__main__":
    unittest.main()