#!/usr/bin/env python3
"""Synthetic decoder fixtures; retained device dumps add optional hash checks."""
from pathlib import Path
import hashlib
import runpy
import unittest

decode = runpy.run_path(str(Path(__file__).with_name("recover-f2fs-file.py")))["inline_data_from_dump"]
HEADER = b"i_inline [0x2b : 43]\ni_extra_isize [0x24 : 36]\n"
WORDS = b"i_addr[0xa] [0x64636261 : 0]\ni_addr[0xb] [0x68676665 : 0]\n"


class InlineTests(unittest.TestCase):
    def test_exact_and_partial_words(self):
        self.assertEqual(decode(HEADER + WORDS, 8), b"abcdefgh")
        self.assertEqual(decode(HEADER + WORDS, 5), b"abcde")

    def test_explicit_zero_preserves_position(self):
        self.assertEqual(decode(HEADER + WORDS.replace(b"64636261", b"00000000"), 8), b"\0\0\0\0efgh")

    def test_gap_is_not_collapsed(self):
        with self.assertRaises(RuntimeError):
            decode(HEADER + WORDS.replace(b"[0xa]", b"[0x9]"), 8)

    def test_duplicate_is_rejected(self):
        with self.assertRaises(RuntimeError):
            decode(HEADER + WORDS + WORDS, 8)

    def test_unaligned_extra_attributes(self):
        with self.assertRaises(RuntimeError):
            decode(HEADER.replace(b": 36", b": 35") + WORDS, 8)

    def test_oversize_is_rejected(self):
        with self.assertRaises(RuntimeError):
            decode(HEADER + WORDS, 4096)

    def test_non_inline_is_not_decoded(self):
        self.assertIsNone(decode(HEADER.replace(b"0x2b", b"0x29") + WORDS, 8))

    def test_retained_rc_dumps_when_available(self):
        base = Path(__file__).parents[2] / "rootfs/wifi-init-reference"
        fixtures = [
            ("dump-0x1b7.log", 2438, "8ffaaed32bc3e1453abd838d1af2abf1a99eb9b93109e43db20a5ed5f74dfead"),
            ("dump-0x1b8.log", 3294, "160e501d7e95bf362d4ce3f73c36ac7ef6a719d8cf811f4dfd06822b7531712f"),
        ]
        if not all((base / name).exists() for name, _, _ in fixtures):
            self.skipTest("local-only RC metadata dumps not retained")
        for name, size, digest in fixtures:
            data = decode((base / name).read_bytes(), size)
            self.assertEqual(hashlib.sha256(data).hexdigest(), digest)


if __name__ == "__main__":
    unittest.main()
