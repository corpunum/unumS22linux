#!/usr/bin/env python3
"""Host tests for tools/hardware/modem/s22-modem.py (SIPC FMT decode + RFS NV server)."""
import hashlib
import importlib.util
import os
import struct
import tempfile
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parent / "modem" / "s22-modem.py"
spec = importlib.util.spec_from_file_location("s22_modem", SRC)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def fake_modem(tmp):
    m = object.__new__(mod.Modem)
    m.log = None
    m.replies = []
    m.rfs_buf = b""
    r, w = os.pipe()
    m.rfs, m._read_end = w, r
    nv = object.__new__(mod.NvStore)
    nv.path = os.path.join(tmp, "nv_data.bin")
    nv.md5 = nv.path + ".md5"
    with open(nv.path, "wb") as f:
        f.write(bytes(mod.NV_SIZE))
    m.nv = nv
    m.emit = lambda rec: m.replies.append(rec)
    return m


class RfsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.m = fake_modem(self.tmp.name)
        self.orig_chown = os.chown
        os.chown = lambda *a: None

    def tearDown(self):
        os.chown = self.orig_chown
        os.close(self.m.rfs)
        os.close(self.m._read_end)
        self.tmp.cleanup()

    def test_fragmented_write_updates_copy_and_md5(self):
        data = bytes(range(256)) * 20
        body = struct.pack("<II", 100, len(data)) + data
        frame = struct.pack("<IBB", 6 + len(body), mod.RFS_NV_WRITE, 7) + body
        for i in range(0, len(frame), 2040):          # kernel delivers 2040-byte chunks
            self.m.on_rfs(frame[i:i + 2040])
        reply = os.read(self.m._read_end, 64)
        self.assertEqual(reply, struct.pack("<IBBBII", 15, 2, 7, 1, 100, len(data)))
        content = open(self.m.nv.path, "rb").read()
        self.assertEqual(content[100:100 + len(data)], data)
        self.assertEqual(open(self.m.nv.md5).read(),
                         hashlib.md5(content + mod.NV_SECRET).hexdigest())
        self.assertEqual(self.m.replies[-1]["changed_bytes"], sum(1 for b in data if b))

    def test_read_and_out_of_range(self):
        frame = struct.pack("<IBBII", 14, mod.RFS_NV_READ, 1, 0, 4)
        self.m.on_rfs(frame)
        self.assertEqual(os.read(self.m._read_end, 64), struct.pack("<IBBBII", 19, 1, 1, 1, 0, 4) + bytes(4))
        frame = struct.pack("<IBBII", 14 + 4, mod.RFS_NV_WRITE, 2, mod.NV_SIZE - 2, 4) + b"abcd"
        self.m.on_rfs(frame)
        self.assertEqual(os.read(self.m._read_end, 64), struct.pack("<IBBBII", 15, 2, 2, 0, mod.NV_SIZE - 2, 4))

    def test_store_refuses_real_efs(self):
        with self.assertRaises(SystemExit):
            mod.NvStore("/mnt/vendor/efs")


class DecodeTest(unittest.TestCase):
    def test_imei_and_sim(self):
        d = mod.decode(0x0A, 0x03, 2, bytes([1, 17]) + b"35033005948301329" + bytes(15))
        self.assertEqual(d["value"], "35033005948301329")
        self.assertEqual(mod.decode(0x05, 0x01, 3, b"\x80\x00")["sim_status"], "CARD_NOT_PRESENT")

    def test_summary_masks_imei(self):
        s = mod.summarize([{"name": "MISC_ME_SN", "decoded": {"sn_type": 1, "value": "35033005948301329"}}])
        self.assertEqual(s["imei"], "35033005*****13")


if __name__ == "__main__":
    unittest.main()
