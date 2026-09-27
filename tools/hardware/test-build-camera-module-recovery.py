#!/usr/bin/env python3
"""Host-only tests for exact single-record camera CPIO replacement."""
from __future__ import annotations

import hashlib
import importlib.util
import os
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
IMAGE_HELPER = BUILDER._load_module(
    ROOT / "tools/hardware/build-bt-hci-recovery.py",
    "camera_recovery_test_image_helper", BUILDER.IMAGE_HELPER_SHA256,
)
PUBLIC_AVBTOOL = Path(os.environ.get(
    "S22_AVBTOOL",
    ROOT.parents[1] / "s22-linux/tools/avb/avbtool.py",
))


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

    def test_manifest_write_failure_removes_only_new_receipt(self) -> None:
        with tempfile.TemporaryDirectory(prefix="camera-manifest-write-failure-") as temporary:
            root = Path(temporary)
            output = root / "out"
            output.mkdir()
            candidate = output / "recovery.img"
            candidate.write_bytes(b"verified candidate image")
            keep = output / "other-output.json"
            keep.write_bytes(b"preserve me")
            manifest = output / "manifest.json"
            success_shaped = b'{"complete":true,"success":true}\n'

            def write_then_fail(fd: int, data: bytes) -> None:
                BUILDER._write_all_fd(fd, data)
                raise OSError("injected manifest write failure")

            with self.assertRaisesRegex(OSError, "injected manifest write failure"):
                BUILDER._publish_success_manifest(
                    manifest, success_shaped, write_file=write_then_fail,
                )
            self.assertFalse(manifest.exists())
            self.assertEqual(candidate.read_bytes(), b"verified candidate image")
            self.assertEqual(keep.read_bytes(), b"preserve me")

    def test_manifest_file_fsync_failure_removes_new_receipt(self) -> None:
        with tempfile.TemporaryDirectory(prefix="camera-manifest-file-fsync-") as temporary:
            output = Path(temporary) / "out"
            output.mkdir()
            manifest = output / "manifest.json"

            def fail_fsync(_fd: int) -> None:
                raise OSError("injected manifest file fsync failure")

            with self.assertRaisesRegex(OSError, "injected manifest file fsync failure"):
                BUILDER._publish_success_manifest(
                    manifest, b'{"complete":true}\n', fsync_file=fail_fsync,
                )
            self.assertFalse(manifest.exists())

    def test_final_directory_fsync_failure_removes_new_receipt(self) -> None:
        with tempfile.TemporaryDirectory(prefix="camera-manifest-dir-fsync-") as temporary:
            output = Path(temporary) / "out"
            output.mkdir()
            candidate = output / "recovery.img"
            candidate.write_bytes(b"keep verified payload")
            manifest = output / "manifest.json"
            sync_calls = []

            def fail_first_directory_sync(path: Path) -> None:
                sync_calls.append(path)
                if len(sync_calls) == 1:
                    raise OSError("injected final directory fsync failure")
                BUILDER._fsync_directory(path)

            with self.assertRaisesRegex(OSError, "injected final directory fsync failure"):
                BUILDER._publish_success_manifest(
                    manifest, b'{"complete":true}\n',
                    fsync_directory=fail_first_directory_sync,
                )
            self.assertFalse(manifest.exists())
            self.assertEqual(candidate.read_bytes(), b"keep verified payload")
            self.assertEqual(sync_calls, [output, output])

    def test_manifest_publisher_never_removes_preexisting_receipt(self) -> None:
        with tempfile.TemporaryDirectory(prefix="camera-manifest-existing-") as temporary:
            output = Path(temporary) / "out"
            output.mkdir()
            manifest = output / "manifest.json"
            manifest.write_bytes(b'{"prior":true}\n')
            with self.assertRaises(FileExistsError):
                BUILDER._publish_success_manifest(manifest, b'{"complete":true}\n')
            self.assertEqual(manifest.read_bytes(), b'{"prior":true}\n')

    def test_manifest_failure_preserves_replacement_inode(self) -> None:
        with tempfile.TemporaryDirectory(prefix="camera-manifest-inode-") as temporary:
            output = Path(temporary) / "out"
            output.mkdir()
            manifest = output / "manifest.json"
            replacement = b'{"owner":"other"}\n'

            def replace_path_then_fail(fd: int, data: bytes) -> None:
                BUILDER._write_all_fd(fd, data)
                manifest.unlink()
                manifest.write_bytes(replacement)
                raise OSError("injected manifest writer failure")

            with self.assertRaisesRegex(OSError, "injected manifest writer failure"):
                BUILDER._publish_success_manifest(
                    manifest, b'{"complete":true}\n', write_file=replace_path_then_fail,
                )
            self.assertEqual(manifest.read_bytes(), replacement)

    def test_public_avbtool_partition_name_requires_recovery_image_basename(self) -> None:
        if not PUBLIC_AVBTOOL.is_file():
            self.skipTest(f"pinned public avbtool unavailable: {PUBLIC_AVBTOOL}")
        with tempfile.TemporaryDirectory(prefix="camera-avb-name-contract-") as temporary:
            root = Path(temporary)
            bad_dir = root / "bad-name"
            bad_dir.mkdir()
            bad_path = bad_dir / "candidate.recovery.img"
            payload = bytes(index % 251 for index in range(512 * 1024))
            bad_path.write_bytes(payload)
            IMAGE_HELPER.run_trusted_avbtool(
                PUBLIC_AVBTOOL, "add_hash_footer", "--image", bad_path,
                "--partition_size", str(2 * 1024 * 1024),
                "--partition_name", "recovery", "--algorithm", "NONE",
                "--rollback_index", "0", "--salt", "11" * 32,
            )
            with self.assertRaisesRegex(RuntimeError, "trusted avbtool verify_image failed"):
                IMAGE_HELPER.verify_image(PUBLIC_AVBTOOL, bad_path)

            good_dir = root / "correct-name"
            good_dir.mkdir()
            good_path = BUILDER.candidate_recovery_image_path(good_dir)
            self.assertEqual(good_path.name, "recovery.img")
            good_path.write_bytes(payload)
            IMAGE_HELPER.run_trusted_avbtool(
                PUBLIC_AVBTOOL, "add_hash_footer", "--image", good_path,
                "--partition_size", str(2 * 1024 * 1024),
                "--partition_name", "recovery", "--algorithm", "NONE",
                "--rollback_index", "0", "--salt", "11" * 32,
            )
            self.assertTrue(IMAGE_HELPER.verify_image(PUBLIC_AVBTOOL, good_path))

            tampered = bytearray(good_path.read_bytes())
            tampered[123] ^= 1
            good_path.write_bytes(tampered)
            with self.assertRaisesRegex(RuntimeError, "trusted avbtool verify_image failed"):
                IMAGE_HELPER.verify_image(PUBLIC_AVBTOOL, good_path)


if __name__ == "__main__":
    unittest.main()
