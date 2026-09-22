#!/usr/bin/env python3
import hashlib, importlib.util, json, tempfile, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("build_nvm", HERE / "build-bt-nvm-diagnostic.py")
mod = importlib.util.module_from_spec(spec); assert spec.loader; spec.loader.exec_module(mod)

def rec(tag, payload):
    return tag.to_bytes(2, 'little') + len(payload).to_bytes(2, 'little') + b'\0'*8 + payload

class DiagnosticTests(unittest.TestCase):
    def test_hash_gate(self):
        with self.assertRaises(ValueError): mod.transform(b'not-an-asset')

    def test_synthetic_bounds_helper(self):
        body = rec(2, b'123456') + rec(17, b'abcdef') + rec(27, b'12345678901')
        raw = bytes([2]) + len(body).to_bytes(3, 'little') + body
        self.assertEqual([x[0] for x in mod.records(raw)], [2, 17, 27])

    @unittest.skipUnless(mod.NVM.is_file() and mod.XML.is_file(), 'private recovered assets required')
    def test_private_asset_transform_is_allowlisted(self):
        raw = mod.NVM.read_bytes()
        transformed, changes = mod.transform(raw)
        self.assertEqual(hashlib.sha256(raw).hexdigest(), mod.NVM_SHA)
        self.assertNotEqual(hashlib.sha256(transformed).hexdigest(), mod.NVM_SHA)
        self.assertEqual(hashlib.sha256(transformed).hexdigest(),
                         '7f9564170b3cfae8293b432248406827215319d0b88f8a9417877d2539dc3c5d')
        allowed = {2, 17, 27, 36, 38, 83, 87, 204}
        self.assertTrue(changes)
        self.assertTrue(all(item["tag"] in allowed for item in changes))
        self.assertTrue(all(set(item) == {"tag", "offset", "old", "new", "reason"}
                            for item in changes))
        self.assertEqual({item["tag"] for item in changes}, allowed)

if __name__ == '__main__': unittest.main()
