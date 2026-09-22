#!/usr/bin/env python3
import importlib.util
import json
import os
import tempfile
from pathlib import Path
import unittest

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("runtime_nvm", HERE / "build-bt-runtime-nvm.py")
mod = importlib.util.module_from_spec(spec); assert spec.loader; spec.loader.exec_module(mod)


def rec(tag, payload):
    return tag.to_bytes(2, "little") + len(payload).to_bytes(2, "little") + b"\0" * 8 + payload


class RuntimeNvmTests(unittest.TestCase):
    def test_address_validation_and_reverse(self):
        controller, source = mod.parse_address({"display_address": "22:22:01:02:03:04", "source": "linux-generated"})
        self.assertEqual(controller, bytes.fromhex("04 03 02 01 22 22"))
        self.assertEqual(source, "linux-generated")

    def test_rejects_factory_or_wrong_prefix(self):
        for doc in ({"display_address": "22:22:01:02:03:04", "source": "factory"},
                    {"display_address": "11:22:01:02:03:04", "source": "linux-generated"},
                    {"display_address": "22:22:01:02:03:+1", "source": "linux-generated"},
                    {"display_address": "22:22:1:02:03:04", "source": "linux-generated"}):
            with self.subTest(doc=doc), self.assertRaises(ValueError): mod.parse_address(doc)

    def test_fixture_replacement_does_not_need_private_asset(self):
        body = rec(2, b"\0" * 6) + rec(17, b"abcdef")
        raw = bytes([2]) + len(body).to_bytes(3, "little") + body
        transformed, count = mod.replace_tag2(raw, bytes.fromhex("04 03 02 01 22 22"))
        self.assertEqual(count, 6)
        self.assertEqual(transformed[16:22], bytes.fromhex("04 03 02 01 22 22"))

    def test_build_metadata_redacts_address(self):
        body = rec(2, b"\0" * 6) + rec(17, b"abcdef")
        raw = bytes([2]) + len(body).to_bytes(3, "little") + body
        original_transform = mod.base.transform
        try:
            mod.base.transform = lambda value: (value, [])
            transformed, metadata = mod.build(
                raw, {"display_address": "22:22:01:02:03:04", "source": "linux-generated"})
        finally:
            mod.base.transform = original_transform
        changed = [i for i, (before, after) in enumerate(zip(raw, transformed)) if before != after]
        self.assertEqual(changed, list(range(16, 22)))
        self.assertNotIn("display_address", json.dumps(metadata))
        self.assertEqual(metadata["tag2_change_count"], 6)

    def test_private_input_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "address.json"
            path.write_text("{}")
            os.chmod(path, 0o600)
            mod.check_private_input(path)
            os.chmod(path, 0o644)
            with self.assertRaises(ValueError):
                mod.check_private_input(path)

    def test_header_is_private_payload_only(self):
        header = mod.c_header(bytes([0, 1, 255]))
        self.assertIn(b"s22_nvm_payload[]", header)
        self.assertIn(b"0x00, 0x01, 0xff", header)


if __name__ == "__main__": unittest.main()
