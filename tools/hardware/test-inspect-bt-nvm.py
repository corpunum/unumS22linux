#!/usr/bin/env python3
import importlib.util
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("inspect_bt_nvm", HERE / "inspect-bt-nvm.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def record(tag, payload):
    return tag.to_bytes(2, "little") + len(payload).to_bytes(2, "little") + b"\0" * 8 + payload


class NvmInspectorTests(unittest.TestCase):
    def test_real_asset_redacts_tag2(self):
        body = record(1, b"meta") + record(2, b"123456")
        out = mod.parse_bab(bytes([2]) + len(body).to_bytes(3, "little") + body)
        self.assertEqual(out["container_type"], 2)
        self.assertEqual(next(x for x in out["tags"] if x["id"] == 2)["payload"], "REDACTED")

    def test_truncated_record(self):
        with self.assertRaises(ValueError):
            mod.parse_bab(bytes([2, 4, 0, 0]) + record(2, b"123")[:-1])

    def test_duplicate_tag(self):
        body = record(2, b"123456") + record(2, b"abcdef")
        with self.assertRaises(ValueError):
            mod.parse_bab(bytes([2]) + len(body).to_bytes(3, "little") + body)

    def test_xml_offset_bounds(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "x.xml"
            p.write_text('<Tag count="1"><Tag2><Length len="2"/><OffsetCount count="1"/><Offset><Offset0 value="2"/></Offset><ChangesCount count="1"/><Changes><Changes0 value="1"/></Changes></Tag2></Tag>')
            with self.assertRaises(ValueError):
                mod.parse_xml(p)

    def test_xml_duplicate_and_byte_count(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "x.xml"
            p.write_text('<Tag count="2"><Tag2><Length len="2"/><OffsetCount count="1"/><Offset><Offset0 value="0"/></Offset><ChangesCount count="1"/><Changes><Changes0 value="1"/></Changes></Tag2><Tag2><Length len="2"/><OffsetCount count="1"/><Offset><Offset0 value="1"/></Offset><ChangesCount count="1"/><Changes><Changes0 value="2"/></Changes></Tag2></Tag>')
            with self.assertRaises(ValueError):
                mod.parse_xml(p)


if __name__ == "__main__":
    unittest.main()
