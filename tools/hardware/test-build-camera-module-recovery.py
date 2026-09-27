#!/usr/bin/env python3
"""Host-only tests for exact single-record camera CPIO replacement."""
from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools/hardware/build-camera-module-recovery.py"
SPEC = importlib.util.spec_from_file_location("camera_recovery_builder", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("could not load camera recovery builder")
BUILDER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = BUILDER
SPEC.loader.exec_module(BUILDER)

CPIO_PATH = ROOT / "tools/headless-recovery/build_native_handoff.py"
CPIO_API = BUILDER._load_module(
    CPIO_PATH, "camera_recovery_test_cpio", BUILDER.CPIO_PARSER_SHA256,
)


def _fields(*, mode: int, ino: int, uid: int = 0, gid: int = 0,
            nlink: int = 1, mtime: int = 0, devmajor: int = 0,
            devminor: int = 0, rdevmajor: int = 0, rdevminor: int = 0,
            check: int = 0) -> tuple[int, ...]:
    return (
        ino, mode, uid, gid, nlink, mtime, 0, devmajor, devminor,
        rdevmajor, rdevminor, 0, check,
    )


def _fixture_cpio(target_payload: bytes = b"old module", *, target_count: int = 1,
                  extra_payload: bytes = b"metadata") -> bytes:
    records = [
        ("init", b"bootstrap", _fields(mode=0o100755, ino=1, uid=0, gid=0)),
    ]
    for index in range(target_count):
        records.append((
            BUILDER.TARGET_RECORD, target_payload,
            _fields(mode=0o100644, ino=10 + index, uid=0, gid=0,
                    nlink=1, mtime=1700000000, devmajor=3, devminor=4,
                    rdevmajor=5, rdevminor=6),
        ))
    records.extend([
        ("lib/modules/modules.dep", extra_payload,
         _fields(mode=0o100644, ino=20, uid=0, gid=0)),
        ("lib/modules/modules.alias", b"alias fixture", _fields(mode=0o100644, ino=21)),
        ("lib/modules/modules.softdep", b"softdep fixture", _fields(mode=0o100644, ino=22)),
        ("TRAILER!!!", b"", _fields(mode=0, ino=0, nlink=1)),
    ])
    return b"".join(BUILDER._newc_record(name, fields, payload)
                    for name, payload, fields in records)


class CameraRecoveryPackageTests(unittest.TestCase):
    def test_replace_preserves_exactly_one_record_and_all_target_metadata(self) -> None:
        old_module = b"old module"
        replacement = b"replacement module contents"
        base = _fixture_cpio(old_module)
        candidate, summary = BUILDER.replace_module_cpio(
            CPIO_API, base, replacement,
            expected_base_sha256=hashlib.sha256(old_module).hexdigest(),
            expected_module_sha256=hashlib.sha256(replacement).hexdigest(),
            expected_base_bytes=len(old_module),
        )

        before = CPIO_API.parse_cpio(base)
        after = CPIO_API.parse_cpio(candidate)
        self.assertEqual(summary["changed_records"], [BUILDER.TARGET_RECORD])
        self.assertEqual(summary["base_target_bytes"], len(old_module))
        self.assertEqual(summary["candidate_target_bytes"], len(replacement))
        self.assertEqual(summary["target_mode"], "0100644")
        self.assertEqual(summary["target_uid_gid"], "0:0")
        for old, new in zip(before, after, strict=True):
            if old.name == BUILDER.TARGET_RECORD:
                expected_fields = list(old.fields)
                expected_fields[6] = len(replacement)
                self.assertEqual(new.fields, tuple(expected_fields))
                self.assertEqual(new.payload, replacement)
            else:
                self.assertEqual(new.raw, old.raw)

    def test_rejects_wrong_base_or_replacement_identity(self) -> None:
        base = _fixture_cpio()
        replacement = b"replacement"
        with self.assertRaisesRegex(BUILDER.BuildError, "baseline CPIO fimc-is.ko"):
            BUILDER.replace_module_cpio(
                CPIO_API, base, replacement,
                expected_base_sha256="0" * 64,
                expected_module_sha256=hashlib.sha256(replacement).hexdigest(),
                expected_base_bytes=len(b"old module"),
            )
        with self.assertRaisesRegex(BUILDER.BuildError, "replacement fimc-is.ko SHA-256"):
            BUILDER.replace_module_cpio(
                CPIO_API, base, replacement,
                expected_base_sha256=hashlib.sha256(b"old module").hexdigest(),
                expected_module_sha256="0" * 64,
                expected_base_bytes=len(b"old module"),
            )

    def test_rejects_missing_duplicate_and_corrupt_cpio_targets(self) -> None:
        replacement = b"replacement"
        replacement_sha = hashlib.sha256(replacement).hexdigest()
        empty_target_base = _fixture_cpio(target_count=0)
        duplicate_target_base = _fixture_cpio(target_count=2)
        with self.assertRaisesRegex(BUILDER.BuildError, "exactly one fimc-is.ko"):
            BUILDER.replace_module_cpio(
                CPIO_API, empty_target_base, replacement,
                expected_module_sha256=replacement_sha,
            )
        with self.assertRaisesRegex(BUILDER.BuildError, "duplicate record names"):
            BUILDER.replace_module_cpio(
                CPIO_API, duplicate_target_base, replacement,
                expected_module_sha256=replacement_sha,
            )
        with self.assertRaisesRegex(ValueError, "CPIO has no trailer"):
            BUILDER.replace_module_cpio(
                CPIO_API, b"not a cpio archive", replacement,
                expected_module_sha256=replacement_sha,
            )

    def test_detects_corruption_to_a_non_target_record(self) -> None:
        base = _fixture_cpio()
        replacement = b"replacement"
        expected_module_sha = hashlib.sha256(replacement).hexdigest()
        candidate, _summary = BUILDER.replace_module_cpio(
            CPIO_API, base, replacement,
            expected_base_sha256=hashlib.sha256(b"old module").hexdigest(),
            expected_module_sha256=expected_module_sha,
            expected_base_bytes=len(b"old module"),
        )
        records = CPIO_API.parse_cpio(candidate)
        corrupted = bytearray()
        for record in records:
            if record.name == "lib/modules/modules.dep":
                corrupted += BUILDER._newc_record(
                    record.name, record.fields, record.payload + b"!",
                )
            else:
                corrupted += record.raw
        with self.assertRaisesRegex(BUILDER.BuildError, "unexpected CPIO record changed"):
            BUILDER.verify_only_target_record_changed(
                CPIO_API, base, bytes(corrupted), replacement,
            )

    def test_canonical_import_hash_sorts_by_symbol_field(self) -> None:
        rows = ["0xffffeeee symbol_z", "0x00000001 symbol_a"]
        canonical = "0x00000001 symbol_a\n0xffffeeee symbol_z\n".encode()
        self.assertEqual(BUILDER._canonical_import_hash(rows),
                         hashlib.sha256(canonical).hexdigest())

    def test_new_output_path_rejects_existing_and_symlink_destinations(self) -> None:
        with tempfile.TemporaryDirectory(prefix="camera-output-path-") as temporary:
            root = Path(temporary)
            output = root / "candidate"
            self.assertEqual(BUILDER.validate_new_output_directory(output), output)
            output.mkdir()
            with self.assertRaisesRegex(BUILDER.BuildError, "refusing existing"):
                BUILDER.validate_new_output_directory(output)
            link = root / "candidate-link"
            link.symlink_to(output, target_is_directory=True)
            with self.assertRaisesRegex(BUILDER.BuildError, "refusing existing"):
                BUILDER.validate_new_output_directory(link)

    def test_new_output_path_rejects_symlink_parent(self) -> None:
        with tempfile.TemporaryDirectory(prefix="camera-output-parent-") as temporary:
            root = Path(temporary)
            real_parent = root / "real"
            real_parent.mkdir()
            linked_parent = root / "linked"
            linked_parent.symlink_to(real_parent, target_is_directory=True)
            with self.assertRaisesRegex(BUILDER.BuildError, "non-symlink directory"):
                BUILDER.validate_new_output_directory(linked_parent / "candidate")

    def test_durable_copy_is_hash_checked_and_refuses_overwrite(self) -> None:
        with tempfile.TemporaryDirectory(prefix="camera-copy-verify-") as temporary:
            root = Path(temporary)
            source = root / "source.bin"
            source.write_bytes(b"candidate payload")
            expected = hashlib.sha256(source.read_bytes()).hexdigest()
            destination = root / "out" / "candidate.bin"
            (root / "out").mkdir()
            copied_hash = BUILDER._copy_new(source, destination, expected)
            self.assertEqual(copied_hash, expected)
            self.assertEqual(destination.read_bytes(), source.read_bytes())
            with self.assertRaises(FileExistsError):
                BUILDER._copy_new(source, destination, expected)

            wrong_hash_destination = root / "out" / "wrong-hash.bin"
            with self.assertRaisesRegex(BUILDER.BuildError, "copy source bytes changed"):
                BUILDER._copy_new(source, wrong_hash_destination, "0" * 64)
            self.assertEqual(wrong_hash_destination.read_bytes(), source.read_bytes())

    def test_header_values_require_the_pinned_unpacker_fields(self) -> None:
        complete = type("Header", (), {
            field: f"value-{field}" for field in BUILDER.UNMODIFIED_HEADER_FIELDS
        })()
        self.assertEqual(
            set(BUILDER._header_values(complete)),
            set(BUILDER.UNMODIFIED_HEADER_FIELDS),
        )
        incomplete = type("Header", (), {})()
        with self.assertRaisesRegex(BUILDER.BuildError, "omitted required header fields"):
            BUILDER._header_values(incomplete)


if __name__ == "__main__":
    unittest.main()
