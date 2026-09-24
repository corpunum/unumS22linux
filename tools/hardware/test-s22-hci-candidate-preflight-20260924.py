#!/usr/bin/env python3
"""Host-only regressions for the S22 HCI candidate preflight gate."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest

SOURCE = Path(__file__).with_name("s22-hci-candidate-preflight-20260924.py")
SPEC = importlib.util.spec_from_file_location("s22_hci_preflight", SOURCE)
GATE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(GATE)

CLEAN_RELEASE = "5.10.260-g4e5c5ad7d950"
DIRTY_RELEASE = CLEAN_RELEASE + "-dirty"
MODULE_SUFFIX = "SMP preempt mod_unload modversions aarch64"


class CandidateVermagicTests(unittest.TestCase):
    def module_records(self, vermagic):
        return [
            {"name": "btpower.ko", "vermagic": vermagic},
            {"name": "exynos_tty.ko", "vermagic": vermagic},
        ]

    def test_dirty_kernel_release_rejects_the_unchanged_external_modules(self):
        expected = DIRTY_RELEASE + " " + MODULE_SUFFIX
        actual = CLEAN_RELEASE + " " + MODULE_SUFFIX
        result = GATE.module_vermagic_verdict(
            expected, self.module_records(actual), expected_count=2,
        )
        self.assertFalse(result["compatible"])
        self.assertEqual(result["vermagic_mismatch_count"], 2)
        self.assertFalse(result["required_modules"]["btpower.ko"]["matches_candidate_kernel"])
        self.assertFalse(result["required_modules"]["exynos_tty.ko"]["matches_candidate_kernel"])

    def test_exact_full_vermagic_and_required_modules_pass(self):
        expected = CLEAN_RELEASE + " " + MODULE_SUFFIX
        result = GATE.module_vermagic_verdict(
            expected, self.module_records(expected), expected_count=2,
        )
        self.assertTrue(result["compatible"])
        self.assertEqual(result["vermagic_mismatch_count"], 0)
        self.assertEqual(result["missing_required_modules"], [])

    def test_missing_required_module_or_wrong_count_fails_closed(self):
        expected = CLEAN_RELEASE + " " + MODULE_SUFFIX
        result = GATE.module_vermagic_verdict(
            expected, [{"name": "btpower.ko", "vermagic": expected}], expected_count=2,
        )
        self.assertFalse(result["compatible"])
        self.assertEqual(result["module_count"], 1)
        self.assertEqual(result["missing_required_modules"], ["exynos_tty.ko"])


class ModuleSymbolVersionTests(unittest.TestCase):
    def test_candidate_symvers_accepts_every_imported_crc(self):
        result = GATE.compare_symbol_versions(
            [("0x12345678", "one"), ("0x87654321", "two")],
            {"one": {"0x12345678"}, "two": {"0x87654321"}},
        )
        self.assertTrue(result["compatible"])
        self.assertEqual(result["import_count"], 2)
        self.assertEqual(result["missing_symbol_count"], 0)
        self.assertEqual(result["crc_mismatch_count"], 0)

    def test_missing_or_changed_crc_fails_closed(self):
        result = GATE.compare_symbol_versions(
            [("0x12345678", "present"), ("0x87654321", "missing")],
            {"present": {"0x11111111"}},
        )
        self.assertFalse(result["compatible"])
        self.assertEqual(result["missing_symbol_count"], 1)
        self.assertEqual(result["crc_mismatch_count"], 1)


if __name__ == "__main__":
    unittest.main()
