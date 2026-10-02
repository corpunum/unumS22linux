#!/usr/bin/env python3
"""Hardware-free tests for the pinned three-module recovery packager."""
from __future__ import annotations

import hashlib
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import copy


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools/hardware/build-audio-coherent-recovery.py"
SPEC = importlib.util.spec_from_file_location("audio_coherent_recovery_builder", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("could not load audio coherent package builder")
BUILDER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = BUILDER
SPEC.loader.exec_module(BUILDER)
HELPERS = BUILDER.load_helpers()
CAMERA = HELPERS["camera"]
CPIO = HELPERS["cpio"]

TARGETS = tuple(BUILDER.TARGET_MODULES)


def _fields(*, mode: int, ino: int, uid: int = 0, gid: int = 0,
            nlink: int = 1, mtime: int = 0) -> tuple[int, ...]:
    return (ino, mode, uid, gid, nlink, mtime, 0, 0, 0, 0, 0, 0, 0)


def _fixture_cpio(*, missing: str | None = None,
                  duplicate: str | None = None,
                  target_mode: int = 0o100644) -> tuple[bytes, dict[str, bytes]]:
    old = {path: ("old:" + path).encode() for path in TARGETS}
    records = [
        ("init", b"bootstrap", _fields(mode=0o100755, ino=1)),
        ("lib/modules/modules.dep", b"deps", _fields(mode=0o100644, ino=2)),
        ("lib/modules/modules.alias", b"aliases", _fields(mode=0o100644, ino=3)),
        ("lib/modules/modules.softdep", b"softdeps", _fields(mode=0o100644, ino=4)),
    ]
    for index, path in enumerate(TARGETS, 10):
        if path == missing:
            continue
        mode = target_mode if path == TARGETS[0] else 0o100644
        record = (path, old[path], _fields(mode=mode, ino=index, uid=0, gid=0, mtime=1700000000))
        records.append(record)
        if path == duplicate:
            records.append((path, b"duplicate", _fields(mode=0o100644, ino=index + 1)))
    records.append(("TRAILER!!!", b"", _fields(mode=0, ino=0)))
    return b"".join(CAMERA._newc_record(name, fields, payload) for name, payload, fields in records), old


def _test_pins(old: dict[str, bytes], replacements: dict[str, bytes]):
    return (
        {path: (hashlib.sha256(old[path]).hexdigest(), len(old[path])) for path in TARGETS},
        {path: hashlib.sha256(replacements[path]).hexdigest() for path in TARGETS},
    )


class AudioCoherentRecoveryPackageTests(unittest.TestCase):
    def test_replace_changes_exactly_three_payloads_and_preserves_raw_records(self) -> None:
        base, old = _fixture_cpio()
        replacements = {path: b"new:" + path.encode() for path in TARGETS}
        base_pins, candidate_pins = _test_pins(old, replacements)
        candidate, summary = BUILDER.replace_three_module_cpio(
            CPIO, CAMERA, base, replacements,
            expected_base=base_pins,
            expected_candidates=candidate_pins,
            expected_module_count=None,
        )
        before, after = CPIO.parse_cpio(base), CPIO.parse_cpio(candidate)
        self.assertEqual(summary["record_count"], len(before))
        self.assertEqual(set(summary["changed_records"]), set(TARGETS))
        self.assertEqual([item.name for item in before], [item.name for item in after])
        for old_record, new_record in zip(before, after, strict=True):
            if old_record.name in TARGETS:
                fields = list(old_record.fields)
                fields[6] = len(replacements[old_record.name])
                self.assertEqual(new_record.fields, tuple(fields))
                self.assertEqual(new_record.payload, replacements[old_record.name])
            else:
                self.assertEqual(new_record.raw, old_record.raw)
        self.assertEqual(
            {name: hashlib.sha256(next(r.payload for r in after if r.name == name)).hexdigest()
             for name in ("lib/modules/modules.dep", "lib/modules/modules.alias", "lib/modules/modules.softdep")},
            {name: hashlib.sha256(next(r.payload for r in before if r.name == name)).hexdigest()
             for name in ("lib/modules/modules.dep", "lib/modules/modules.alias", "lib/modules/modules.softdep")},
        )

    def test_rejects_incomplete_wrong_hash_and_duplicate_replacements(self) -> None:
        base, old = _fixture_cpio()
        replacements = {path: b"new:" + path.encode() for path in TARGETS}
        base_pins, candidate_pins = _test_pins(old, replacements)
        with self.assertRaisesRegex(BUILDER.BuildError, "exactly the three pinned"):
            BUILDER.replace_three_module_cpio(
                CPIO, CAMERA, base, {TARGETS[0]: replacements[TARGETS[0]]},
                expected_module_count=None,
            )
        wrong_base = dict(base_pins)
        wrong_base[TARGETS[0]] = ("0" * 64, len(old[TARGETS[0]]))
        with self.assertRaisesRegex(BUILDER.BuildError, "identity or ownership"):
            BUILDER.replace_three_module_cpio(
                CPIO, CAMERA, base, replacements,
                expected_base=wrong_base, expected_candidates=candidate_pins,
                expected_module_count=None,
            )
        wrong_candidates = dict(candidate_pins)
        wrong_candidates[TARGETS[1]] = "f" * 64
        with self.assertRaisesRegex(BUILDER.BuildError, "SHA-256 differs from the pin"):
            BUILDER.replace_three_module_cpio(
                CPIO, CAMERA, base, replacements,
                expected_base=base_pins, expected_candidates=wrong_candidates,
                expected_module_count=None,
            )

        missing, missing_old = _fixture_cpio(missing=TARGETS[2])
        missing_pins, _ = _test_pins(missing_old, replacements)
        with self.assertRaisesRegex(BUILDER.BuildError, "must contain exactly one"):
            BUILDER.replace_three_module_cpio(
                CPIO, CAMERA, missing, replacements,
                expected_base=missing_pins, expected_candidates=candidate_pins,
                expected_module_count=None,
            )

        duplicate, _duplicate_old = _fixture_cpio(duplicate=TARGETS[1])
        with self.assertRaisesRegex(BUILDER.BuildError, "duplicate record names"):
            BUILDER.replace_three_module_cpio(
                CPIO, CAMERA, duplicate, replacements,
                expected_module_count=None,
            )

        symlink, symlink_old = _fixture_cpio(target_mode=0o120777)
        symlink_base_pins, _ = _test_pins(symlink_old, replacements)
        with self.assertRaisesRegex(BUILDER.BuildError, "not a regular file"):
            BUILDER.replace_three_module_cpio(
                CPIO, CAMERA, symlink, replacements,
                expected_base=symlink_base_pins, expected_candidates=candidate_pins,
                expected_module_count=None,
            )

    def test_rejects_corrupt_cpio_and_any_non_target_record_change(self) -> None:
        invalid_replacements = {path: b"fixture" for path in TARGETS}
        with self.assertRaisesRegex(BUILDER.BuildError, "base CPIO is invalid"):
            BUILDER.replace_three_module_cpio(
                CPIO, CAMERA, b"not CPIO", invalid_replacements, expected_module_count=None,
            )

        base, old = _fixture_cpio()
        replacements = {path: b"new:" + path.encode() for path in TARGETS}
        base_pins, candidate_pins = _test_pins(old, replacements)
        candidate, _summary = BUILDER.replace_three_module_cpio(
            CPIO, CAMERA, base, replacements,
            expected_base=base_pins, expected_candidates=candidate_pins,
            expected_module_count=None,
        )
        records = CPIO.parse_cpio(candidate)
        corrupted = b"".join(
            CAMERA._newc_record(record.name, record.fields, record.payload + b"!")
            if record.name == "lib/modules/modules.dep" else record.raw
            for record in records
        )
        with self.assertRaisesRegex(BUILDER.BuildError, "unexpected non-target"):
            BUILDER.verify_three_targets_only_changed(CPIO, base, corrupted, replacements)
        reordered = b"".join(record.raw for record in (records[1], records[0], *records[2:]))
        with self.assertRaisesRegex(BUILDER.BuildError, "record names or ordering changed"):
            BUILDER.verify_three_targets_only_changed(CPIO, base, reordered, replacements)

    def test_dependency_membership_and_aliases_must_match_but_order_may_differ(self) -> None:
        result = BUILDER.compare_module_discovery_metadata(
            CAMERA, "first,second,gic", "gic,first,second",
            ["alias:a", "alias:b"], ["alias:b", "alias:a"], "fixture.ko",
        )
        self.assertTrue(result["dependency_set_unchanged"])
        self.assertTrue(result["dependency_order_changed"])
        self.assertTrue(result["aliases_unchanged"])
        with self.assertRaisesRegex(BUILDER.BuildError, "dependency set differs"):
            BUILDER.compare_module_discovery_metadata(
                CAMERA, "first,second,gic", "first,second", [], [], "fixture.ko",
            )
        with self.assertRaisesRegex(BUILDER.BuildError, "alias set differs"):
            BUILDER.compare_module_discovery_metadata(
                CAMERA, "first", "first", ["alias:a"], ["alias:changed"], "fixture.ko",
            )

    def test_static_abi_gate_rejects_stale_imports_and_missing_layout_evidence(self) -> None:
        good_versions = {
            "evidence_complete": True,
            "loader_compatible": True,
            "missing_module_layout_version_record": False,
            "module_layout_crc_unverified": False,
            "module_layout_crc_mismatch": False,
            "module_layout_module_crcs": [BUILDER.MODULE_LAYOUT_CRC],
            "module_layout_candidate_crcs": [BUILDER.MODULE_LAYOUT_CRC],
            "missing_symbol_count": 0,
            "crc_mismatch_count": 0,
            "unknown_candidate_crc_count": 0,
            "ambiguous_candidate_crc_count": 0,
        }
        good = {
            "vermagic": BUILDER.EXPECTED_VERMAGIC,
            "versions_section_present": True,
            "symbol_versions": good_versions,
        }
        self.assertEqual(BUILDER.validate_static_abi_report(good, "fixture.ko"), good_versions)

        stale = copy.deepcopy(good)
        stale["symbol_versions"]["crc_mismatch_count"] = 1
        stale["symbol_versions"]["loader_compatible"] = False
        with self.assertRaisesRegex(BUILDER.BuildError, "stale, incompatible"):
            BUILDER.validate_static_abi_report(stale, "stale-consumer.ko")

        missing_versions = copy.deepcopy(good)
        missing_versions["versions_section_present"] = False
        with self.assertRaisesRegex(BUILDER.BuildError, "stale, incompatible"):
            BUILDER.validate_static_abi_report(missing_versions, "unversioned.ko")

        wrong_layout = copy.deepcopy(good)
        wrong_layout["symbol_versions"]["module_layout_module_crcs"] = ["0x00000000"]
        with self.assertRaisesRegex(BUILDER.BuildError, "stale, incompatible"):
            BUILDER.validate_static_abi_report(wrong_layout, "wrong-layout.ko")

    def test_provider_map_replaces_only_the_two_pinned_owners(self) -> None:
        baseline = b"".join(line + b"\n" for line in (
            b"0x00000001 old_abox_a " + BUILDER.BASE_ABOX_OWNER + b" EXPORT_SYMBOL",
            b"0x00000002 old_abox_b " + BUILDER.BASE_ABOX_OWNER + b" EXPORT_SYMBOL",
            b"0x00000003 old_off " + BUILDER.BASE_OFFLOADER_OWNER + b" EXPORT_SYMBOL",
            b"0x00000004 module_layout vmlinux EXPORT_SYMBOL",
            b"0x00000005 unrelated vmlinux EXPORT_SYMBOL",
        ))
        abox = b"".join(line + b"\n" for line in (
            b"0x00000011 new_abox_a " + BUILDER.BASE_ABOX_OWNER + b" EXPORT_SYMBOL",
            b"0x00000012 new_abox_b " + BUILDER.BASE_ABOX_OWNER + b" EXPORT_SYMBOL",
        ))
        offloader = b"0x00000013 new_off " + BUILDER.BASE_OFFLOADER_OWNER + b" EXPORT_SYMBOL\n"
        merged, summary, exports = BUILDER.build_updated_provider_map(
            baseline, abox, offloader,
            expected_baseline_rows=5, expected_old_abox_rows=2,
            expected_old_offloader_rows=1, expected_abox_rows=2,
            expected_offloader_rows=1,
        )
        self.assertEqual(summary["final_rows"], 5)
        self.assertEqual(merged.count(b"old_abox"), 0)
        self.assertEqual(merged.count(b"old_off"), 0)
        self.assertEqual(merged.count(b"new_abox"), 2)
        self.assertEqual(exports["module_layout"], {"0x00000004"})
        with self.assertRaisesRegex(BUILDER.BuildError, "owned by another module"):
            BUILDER.build_updated_provider_map(
                baseline, b"0x00000011 wrong_owner owner EXPORT_SYMBOL\n", offloader,
                expected_baseline_rows=5, expected_old_abox_rows=2,
                expected_old_offloader_rows=1, expected_abox_rows=1,
                expected_offloader_rows=1,
            )

    def test_help_precedes_gate_and_nonisolated_startup_stops_before_preflight(self) -> None:
        help_result = subprocess.run(
            [sys.executable, str(SCRIPT), "--help"], capture_output=True, text=True, check=False,
        )
        self.assertEqual(help_result.returncode, 0, help_result.stderr)
        self.assertIn("--out-dir", help_result.stdout)

        with tempfile.TemporaryDirectory(prefix="audio-package-startup-gate-") as temporary:
            directory = Path(temporary)
            output = directory / "must-not-exist"
            missing_image = directory / "missing-base.img"
            result = subprocess.run(
                [sys.executable, "-S", str(SCRIPT), "--base-image", str(missing_image),
                 "--out-dir", str(output)],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("invoke with python3 -I -S", result.stderr)
            self.assertNotIn("unavailable", result.stderr)
            self.assertFalse(output.exists())

    def test_output_path_and_copy_helpers_refuse_overwrite(self) -> None:
        with tempfile.TemporaryDirectory(prefix="audio-package-output-") as temporary:
            parent = Path(temporary)
            output = parent / "candidate"
            self.assertEqual(CAMERA.validate_new_output_directory(output), output)
            output.mkdir()
            with self.assertRaisesRegex(RuntimeError, "refusing existing"):
                CAMERA.validate_new_output_directory(output)

            source = parent / "source.bin"
            source.write_bytes(b"pinned fixture")
            destination = output / "copy.bin"
            expected = hashlib.sha256(source.read_bytes()).hexdigest()
            self.assertEqual(CAMERA._copy_new(source, destination, expected), expected)
            with self.assertRaises(FileExistsError):
                CAMERA._copy_new(source, destination, expected)

            link = parent / "candidate-link"
            link.symlink_to(output, target_is_directory=True)
            with self.assertRaisesRegex(RuntimeError, "refusing existing"):
                CAMERA.validate_new_output_directory(link)
            with self.assertRaisesRegex(BUILDER.BuildError, "non-symlink regular file"):
                BUILDER._safe_read(link, "symlink fixture")


if __name__ == "__main__":
    unittest.main(verbosity=2)
