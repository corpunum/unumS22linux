#!/usr/bin/env python3
"""Host-only regressions for the S22 HCI candidate preflight gate."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import struct
import tempfile
import unittest

SOURCE = Path(__file__).with_name("s22-hci-candidate-preflight-20260924.py")
SPEC = importlib.util.spec_from_file_location("s22_hci_preflight", SOURCE)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot import preflight module at {SOURCE}")
GATE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GATE)

CLEAN_RELEASE = "5.10.260-g4e5c5ad7d950"
DIRTY_RELEASE = CLEAN_RELEASE + "-dirty"
MODULE_SUFFIX = " SMP preempt mod_unload modversions aarch64"
LAYOUT_CRC = "0x12345678"


class PinnedSameMagicTests(unittest.TestCase):
    def test_release_only_difference_is_accepted_with_versions(self):
        result = GATE.module_vermagic_check(
            CLEAN_RELEASE + MODULE_SUFFIX,
            DIRTY_RELEASE + MODULE_SUFFIX,
            versions_section_present=True,
            config_modversions=True,
        )
        self.assertTrue(result["loader_compatible"])
        self.assertEqual(result["comparison_mode"], "flags_after_release")
        self.assertTrue(result["release_prefix_differs"])

    def test_release_only_difference_is_rejected_without_versions(self):
        result = GATE.module_vermagic_check(
            CLEAN_RELEASE + MODULE_SUFFIX,
            DIRTY_RELEASE + MODULE_SUFFIX,
            versions_section_present=False,
            config_modversions=True,
        )
        self.assertFalse(result["loader_compatible"])
        self.assertEqual(result["comparison_mode"], "full_vermagic")

    def test_no_modversions_configuration_keeps_full_string_comparison(self):
        result = GATE.module_vermagic_check(
            CLEAN_RELEASE + MODULE_SUFFIX,
            DIRTY_RELEASE + MODULE_SUFFIX,
            versions_section_present=True,
            config_modversions=False,
        )
        self.assertFalse(result["loader_compatible"])
        self.assertEqual(result["comparison_mode"], "full_vermagic")

    def test_different_flags_are_rejected_even_with_versions(self):
        result = GATE.module_vermagic_check(
            CLEAN_RELEASE + MODULE_SUFFIX,
            DIRTY_RELEASE + " SMP preempt modversions aarch64",
            versions_section_present=True,
            config_modversions=True,
        )
        self.assertFalse(result["loader_compatible"])
        self.assertEqual(result["comparison_mode"], "flags_after_release")
        self.assertNotEqual(result["module_flags"], result["kernel_flags"])

    def test_unknown_version_section_is_not_reported_as_compatible(self):
        result = GATE.module_vermagic_check(
            CLEAN_RELEASE + MODULE_SUFFIX,
            DIRTY_RELEASE + MODULE_SUFFIX,
            versions_section_present=None,
            config_modversions=True,
        )
        self.assertIsNone(result["loader_compatible"])

    def test_model_names_the_pinned_source_and_traced_functions(self):
        reference = GATE.PINNED_LOADER_REFERENCE
        self.assertEqual(
            reference["commit"], "4e5c5ad7d950e4de0688b5663965f2075654b2ad"
        )
        self.assertEqual(reference["file"], "kernel/module.c")
        self.assertIn("same_magic", reference["functions"])
        self.assertIn("check_modstruct_version", reference["functions"])
        self.assertIn("check_version", reference["functions"])
        self.assertIn("source-traced host model", reference["model_note"].lower())


class ModuleVersionEvidenceTests(unittest.TestCase):
    def test_missing_versions_section_is_separate_and_rejects_without_force_load(self):
        result = GATE.module_symbol_version_verdict(
            [], {"module_layout": {LAYOUT_CRC}},
            versions_section_present=False,
            config_modversions=True,
        )
        self.assertFalse(result["loader_compatible"])
        self.assertFalse(result["evidence_complete"])
        self.assertEqual(result["mode"], "versions_section_missing")
        self.assertTrue(result["missing_module_layout_version_record"])

    def test_all_symbol_crcs_including_module_layout_match(self):
        result = GATE.module_symbol_version_verdict(
            [(LAYOUT_CRC, "module_layout"), ("0x87654321", "one")],
            {"module_layout": {LAYOUT_CRC}, "one": {"0x87654321"}},
            versions_section_present=True,
        )
        self.assertTrue(result["loader_compatible"])
        self.assertTrue(result["evidence_complete"])
        self.assertTrue(result["module_layout_record_present"])
        self.assertTrue(result["module_layout_candidate_crc_present"])
        self.assertFalse(result["module_layout_crc_mismatch"])
        self.assertEqual(result["module_layout_module_crcs"], [LAYOUT_CRC])
        self.assertEqual(result["module_layout_candidate_crcs"], [LAYOUT_CRC])
        self.assertEqual(result["crc_mismatch_count"], 0)

    def test_mismatched_import_crc_is_loader_incompatible(self):
        result = GATE.module_symbol_version_verdict(
            [(LAYOUT_CRC, "module_layout"), ("0x87654321", "one")],
            {"module_layout": {LAYOUT_CRC}, "one": {"0x11111111"}},
            versions_section_present=True,
        )
        self.assertFalse(result["loader_compatible"])
        self.assertFalse(result["evidence_complete"])
        self.assertEqual(result["crc_mismatch_count"], 1)

    def test_mismatched_module_layout_crc_is_loader_incompatible(self):
        result = GATE.module_symbol_version_verdict(
            [("0x87654321", "module_layout")],
            {"module_layout": {LAYOUT_CRC}},
            versions_section_present=True,
        )
        self.assertFalse(result["loader_compatible"])
        self.assertTrue(result["module_layout_crc_mismatch"])
        self.assertEqual(result["crc_mismatch_count"], 1)

    def test_missing_module_layout_record_matches_pinned_warning_but_is_incomplete(self):
        result = GATE.module_symbol_version_verdict(
            [("0x87654321", "one")],
            {"module_layout": {LAYOUT_CRC}, "one": {"0x87654321"}},
            versions_section_present=True,
        )
        self.assertTrue(result["loader_compatible"])
        self.assertFalse(result["evidence_complete"])
        self.assertTrue(result["missing_module_layout_version_record"])

    def test_missing_export_is_rejected_as_unresolved_symbol(self):
        result = GATE.module_symbol_version_verdict(
            [(LAYOUT_CRC, "module_layout"), ("0x87654321", "not_exported")],
            {"module_layout": {LAYOUT_CRC}},
            versions_section_present=True,
        )
        self.assertFalse(result["loader_compatible"])
        self.assertEqual(result["missing_symbol_count"], 1)

    def test_zero_or_multiple_candidate_crcs_remain_unverified(self):
        no_crc = GATE.compare_symbol_versions(
            [("0x12345678", "one")], {"one": {"0x00000000"}},
        )
        self.assertIsNone(no_crc["loader_compatible"])
        self.assertFalse(no_crc["evidence_complete"])
        self.assertEqual(no_crc["unknown_candidate_crc_count"], 1)
        ambiguous = GATE.compare_symbol_versions(
            [("0x12345678", "one")],
            {"one": {"0x12345678", "0x87654321"}},
        )
        self.assertIsNone(ambiguous["loader_compatible"])
        self.assertEqual(ambiguous["ambiguous_candidate_crc_count"], 1)


class ElfSectionInventoryTests(unittest.TestCase):
    @staticmethod
    def make_elf64_module(include_versions: bool) -> bytes:
        names = b"\0.shstrtab\0"
        version_name_offset = 0
        if include_versions:
            version_name_offset = len(names)
            names += b"__versions\0"
        section_table_offset = 64
        section_table_size = 3 * 64
        names_offset = section_table_offset + section_table_size
        versions_offset = names_offset + len(names)
        ident = b"\x7fELF" + bytes((2, 1, 1, 0)) + bytes(8)
        header = struct.pack(
            "<16sHHIQQQIHHHHHH", ident, 1, 183, 1, 0, 0,
            section_table_offset, 0, 64, 0, 0, 64, 3, 1,
        )
        null_section = bytes(64)
        string_section = struct.pack(
            "<IIQQQQIIQQ", 1, 3, 0, 0, names_offset, len(names), 0, 0, 1, 0,
        )
        version_section = struct.pack(
            "<IIQQQQIIQQ", version_name_offset, 1, 2, 0, versions_offset,
            (64 if include_versions else 0), 0, 0, 8, 0,
        )
        return header + null_section + string_section + version_section + names + bytes(64 if include_versions else 0)

    def test_elf_section_reader_detects_present_and_absent_versions(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with_versions = root / "with-versions.ko"
            without_versions = root / "without-versions.ko"
            with_versions.write_bytes(self.make_elf64_module(True))
            without_versions.write_bytes(self.make_elf64_module(False))
            self.assertIn("__versions", GATE.elf_section_names(with_versions))
            self.assertNotIn("__versions", GATE.elf_section_names(without_versions))

    def test_malformed_module_is_not_silently_treated_as_versionless(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "not-an-elf.ko"
            path.write_bytes(b"not an ELF module")
            with self.assertRaises(GATE.GateError):
                GATE.elf_section_names(path)


class CandidateVermagicTests(unittest.TestCase):
    @staticmethod
    def module_records(vermagic):
        return [
            {"name": "btpower.ko", "vermagic": vermagic,
             "versions_section_present": True},
            {"name": "exynos_tty.ko", "vermagic": vermagic,
             "versions_section_present": True},
        ]

    def test_required_modules_use_loader_rule_not_full_release_name(self):
        expected = DIRTY_RELEASE + MODULE_SUFFIX
        actual = CLEAN_RELEASE + MODULE_SUFFIX
        result = GATE.module_vermagic_verdict(
            expected, self.module_records(actual), expected_count=2,
        )
        self.assertTrue(result["compatible"])
        self.assertEqual(result["vermagic_mismatch_count"], 0)
        self.assertTrue(result["required_modules"]["btpower.ko"]["release_prefix_differs"])

    def test_missing_required_module_or_wrong_count_still_fails(self):
        expected = CLEAN_RELEASE + MODULE_SUFFIX
        result = GATE.module_vermagic_verdict(
            expected,
            [{"name": "btpower.ko", "vermagic": expected,
              "versions_section_present": True}],
            expected_count=2,
        )
        self.assertFalse(result["compatible"])
        self.assertEqual(result["module_count"], 1)
        self.assertEqual(result["missing_required_modules"], ["exynos_tty.ko"])

    def test_crc_section_absence_uses_full_vermagic(self):
        result = GATE.module_vermagic_verdict(
            DIRTY_RELEASE + MODULE_SUFFIX,
            [{"name": "btpower.ko", "vermagic": CLEAN_RELEASE + MODULE_SUFFIX,
              "versions_section_present": False}],
            expected_count=1,
        )
        self.assertFalse(result["compatible"])
        self.assertEqual(result["vermagic_mismatch_count"], 1)


class CandidateModuleSourcePlanTests(unittest.TestCase):
    def test_lineage_wlan_is_required_and_stock_fyi3_is_only_an_alternate(self):
        selected, alternate = GATE.WLAN_SOURCE_PLAN
        self.assertTrue(selected["candidate_required"])
        self.assertIn("lineage-23.2-20260915", selected["path"])
        self.assertEqual(
            selected["expected_sha256"],
            "cbf8932d079e97006a5b7aae0e1b5acfe65b3e8b5113ab8fa095daa36796738d",
        )
        self.assertIn("wifi-stage.sh", selected["activation_evidence"])
        self.assertIn("wifi-bringup-once.py", selected["activation_evidence"])
        self.assertFalse(alternate["candidate_required"])
        self.assertIn("vendor_dlkm/lib/modules/wlan.ko", alternate["path"])
        self.assertIn("must not be loaded", alternate["activation_evidence"])
        self.assertEqual(GATE.EXPECTED_RAMDISK_MODULE_COUNT + 1, 325)


class ManifestSourceProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.config = root / ".config"
        self.symvers = root / "Module.symvers"
        self.image = root / "Image"
        self.config.write_text("CONFIG_MODVERSIONS=y\n")
        self.symvers.write_text("0x12345678\tmodule_layout\tvmlinux\tEXPORT_SYMBOL\n")
        self.image.write_bytes(b"kernel image fixture")
        self.compiler_metadata = {
            "LINUX_COMPILER": "Ubuntu clang version fixture, Ubuntu LLD fixture",
        }

    def complete_manifest(self):
        return {"kernel_build_provenance": {
            "complete": True,
            "source_commit": "abc123",
            "source_dirty": False,
            "kernel_release": CLEAN_RELEASE,
            "config_sha256": GATE.sha256_file(self.config),
            "module_symvers_sha256": GATE.sha256_file(self.symvers),
            "kernel_image_sha256": GATE.sha256_file(self.image),
            "toolchain_tools": [{
                "name": "clang", "version_first_line": "Ubuntu clang version fixture",
                "sha256": "a" * 64,
            }, {
                "name": "ld.lld", "version_first_line": "Ubuntu LLD fixture (compatible)",
                "sha256": "b" * 64,
            }],
            "build_command": "make ARCH=arm64 Image modules",
        }}

    def test_complete_matching_build_provenance_is_accepted(self):
        self.assertEqual([], GATE.manifest_build_provenance_issues(
            self.complete_manifest(), "abc123", CLEAN_RELEASE,
            self.config, self.symvers, self.image, self.compiler_metadata,
        ))

    def test_missing_or_incomplete_build_provenance_is_rejected(self):
        self.assertIn(
            "candidate manifest lacks complete kernel build provenance",
            GATE.manifest_build_provenance_issues(
                {}, "abc123", CLEAN_RELEASE, self.config, self.symvers, self.image,
                self.compiler_metadata,
            ),
        )
        incomplete = self.complete_manifest()
        incomplete["kernel_build_provenance"]["complete"] = False
        self.assertIn(
            "candidate manifest lacks complete kernel build provenance",
            GATE.manifest_build_provenance_issues(
                incomplete, "abc123", CLEAN_RELEASE, self.config, self.symvers, self.image,
                self.compiler_metadata,
            ),
        )

    def test_source_config_symvers_image_and_toolchain_mismatches_are_rejected(self):
        manifest = self.complete_manifest()
        provenance = manifest["kernel_build_provenance"]
        provenance["source_commit"] = "def456"
        provenance["source_dirty"] = True
        provenance["config_sha256"] = "0" * 64
        provenance["module_symvers_sha256"] = "0" * 64
        provenance["kernel_image_sha256"] = "0" * 64
        provenance["toolchain_tools"] = []
        issues = GATE.manifest_build_provenance_issues(
            manifest, "abc123", CLEAN_RELEASE + "-wrong",
            self.config, self.symvers, self.image, self.compiler_metadata,
        )
        self.assertTrue(any("source commit" in issue for issue in issues))
        self.assertTrue(any("clean kernel source" in issue for issue in issues))
        self.assertTrue(any("kernel release" in issue for issue in issues))
        self.assertTrue(any("kernel config hash" in issue for issue in issues))
        self.assertTrue(any("Module.symvers hash" in issue for issue in issues))
        self.assertTrue(any("kernel Image hash" in issue for issue in issues))
        self.assertTrue(any("toolchain identities" in issue for issue in issues))

    def test_toolchain_names_and_versions_must_match_kernel_compiler_metadata(self):
        manifest = self.complete_manifest()
        manifest["kernel_build_provenance"]["toolchain_tools"] = [{
            "name": "not-clang-or-linker", "version_first_line": "unrelated tool version",
            "sha256": "f" * 64,
        }]
        issues = GATE.manifest_build_provenance_issues(
            manifest, "abc123", CLEAN_RELEASE,
            self.config, self.symvers, self.image, self.compiler_metadata,
        )
        self.assertTrue(any("recorded clang version" in issue for issue in issues))
        self.assertTrue(any("recorded ld.lld identity" in issue for issue in issues))

        manifest = self.complete_manifest()
        manifest["kernel_build_provenance"]["toolchain_tools"][0]["version_first_line"] = (
            "Ubuntu clang version unrelated"
        )
        manifest["kernel_build_provenance"]["toolchain_tools"][1]["version_first_line"] = (
            "Ubuntu LLD unrelated (compatible)"
        )
        issues = GATE.manifest_build_provenance_issues(
            manifest, "abc123", CLEAN_RELEASE,
            self.config, self.symvers, self.image, self.compiler_metadata,
        )
        self.assertTrue(any("recorded clang version" in issue for issue in issues))
        self.assertTrue(any("recorded ld.lld version" in issue for issue in issues))

    def test_matching_manifest_source_commit_is_accepted(self):
        manifest = {"kernel_build_provenance": {
            "complete": True, "source_commit": "abc123",
        }}
        self.assertIsNone(GATE.manifest_source_commit_issue(manifest, "abc123"))

    def test_mismatched_manifest_source_commit_is_reported(self):
        manifest = {"kernel_build_provenance": {
            "complete": True, "source_commit": "abc123",
        }}
        issue = GATE.manifest_source_commit_issue(manifest, "def456")
        self.assertIn("abc123", issue)
        self.assertIn("def456", issue)


class ModuleSymbolVersionComparisonTests(unittest.TestCase):
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
