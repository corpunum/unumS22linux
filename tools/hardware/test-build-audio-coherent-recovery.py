#!/usr/bin/env python3
"""Hardware-free tests for the pinned three-module recovery packager."""
from __future__ import annotations

import hashlib
import importlib.util
import os
from pathlib import Path
import subprocess
import struct
import sys
import tempfile
import unittest
import copy
import shutil
from unittest import mock


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
    def _compile_synthetic_elf(self, directory: Path) -> tuple[Path, Path]:
        compiler = shutil.which("cc", path="/usr/bin:/bin")
        if compiler is None:
            self.skipTest("local C compiler unavailable for ELF transform fixture")
        source = directory / "strip-fixture.c"
        output = directory / "strip-fixture.o"
        source.write_text(
            "extern int external_value;\n"
            "int fixture_function(void) { return external_value + 7; }\n",
            encoding="utf-8",
        )
        environment = {"PATH": "/usr/bin:/bin", "LC_ALL": "C", "TMPDIR": str(directory)}
        result = subprocess.run(
            [compiler, "-g", "-c", str(source), "-o", str(output)],
            capture_output=True, text=True, check=False, env=environment,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return source, output

    def _strip_synthetic_elf(self, directory: Path) -> tuple[bytes, bytes, list[str]]:
        _source, object_path = self._compile_synthetic_elf(directory)
        original = object_path.read_bytes()
        BUILDER._verify_tool_alias(
            BUILDER.OBJCOPY, BUILDER.OBJCOPY_REALPATH, BUILDER.OBJCOPY_SHA256,
            "pinned llvm-objcopy-18",
        )
        result = subprocess.run(
            [str(BUILDER.OBJCOPY), "--strip-debug", str(object_path)],
            capture_output=True, text=True, check=False,
            env={"PATH": "/usr/bin:/bin", "LC_ALL": "C", "TMPDIR": str(directory)},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        stripped = object_path.read_bytes()
        old_names = {section["name"] for section in BUILDER._parse_elf64(original, "fixture source")["sections"]}
        new_names = {section["name"] for section in BUILDER._parse_elf64(stripped, "fixture stripped")["sections"]}
        removed = sorted(old_names - new_names)
        self.assertTrue(removed)
        self.assertTrue(all(BUILDER._is_debug_section(name) for name in removed))
        return original, stripped, removed

    def test_real_objcopy_strip_preserves_runtime_elf_semantics(self) -> None:
        with tempfile.TemporaryDirectory(prefix="audio-strip-synthetic-") as temporary:
            original, stripped, removed = self._strip_synthetic_elf(Path(temporary))
            summary = BUILDER.validate_debug_strip_transform(original, stripped, removed)
            self.assertEqual(summary["removed_debug_sections"], removed)
            self.assertGreater(summary["allocated_section_count"], 0)
            self.assertGreater(summary["runtime_relocation_entry_count"], 0)
            self.assertEqual(summary["source_sha256"], hashlib.sha256(original).hexdigest())
            self.assertEqual(summary["stripped_sha256"], hashlib.sha256(stripped).hexdigest())

    def test_transform_validator_rejects_alloc_relocation_and_symbol_table_damage(self) -> None:
        with tempfile.TemporaryDirectory(prefix="audio-strip-negative-") as temporary:
            original, stripped, removed = self._strip_synthetic_elf(Path(temporary))
            parsed = BUILDER._parse_elf64(stripped, "synthetic stripped output")
            alloc_section = next(
                section for section in parsed["sections"]
                if section["flags"] & BUILDER.ELF_SHF_ALLOC and section["size"] > 0
                and section["type"] != BUILDER.ELF_SHT_NOBITS
            )
            damaged_alloc = bytearray(stripped)
            damaged_alloc[alloc_section["offset"]] ^= 1
            with self.assertRaisesRegex(BUILDER.BuildError, "SHF_ALLOC section contents changed"):
                BUILDER.validate_debug_strip_transform(original, bytes(damaged_alloc), removed)

            rela = next(
                section for section in parsed["sections"]
                if section["type"] in (BUILDER.ELF_SHT_REL, BUILDER.ELF_SHT_RELA)
                and not BUILDER._is_debug_section(section["name"]) and section["size"]
            )
            damaged_relocation = bytearray(stripped)
            if rela["type"] == BUILDER.ELF_SHT_RELA:
                field_offset = rela["offset"] + 16
                value = struct.unpack_from("<q", damaged_relocation, field_offset)[0]
                struct.pack_into("<q", damaged_relocation, field_offset, value + 1)
            else:
                value = struct.unpack_from("<Q", damaged_relocation, rela["offset"])[0]
                struct.pack_into("<Q", damaged_relocation, rela["offset"], value + 1)
            with self.assertRaisesRegex(BUILDER.BuildError, "runtime relocation semantics changed"):
                BUILDER.validate_debug_strip_transform(original, bytes(damaged_relocation), removed)

            source_elf = BUILDER._parse_elf64(original, "synthetic source")
            symtab = next(
                section for section in source_elf["sections"]
                if section["type"] == BUILDER.ELF_SHT_SYMTAB
            )
            symbol_count = symtab["size"] // BUILDER.ELF64_SYMBOL.size
            bad_source = bytearray(original)
            shoff, shentsize = source_elf["header"][6], source_elf["header"][11]
            struct.pack_into(
                "<I", bad_source, shoff + symtab["index"] * shentsize + 44,
                symbol_count + 1,
            )
            with self.assertRaisesRegex(BUILDER.BuildError, "local boundary is out of range"):
                BUILDER.validate_debug_strip_transform(bytes(bad_source), stripped, removed)

    def test_transform_refuses_appended_kernel_module_signature(self) -> None:
        with tempfile.TemporaryDirectory(prefix="audio-strip-signature-") as temporary:
            original, stripped, removed = self._strip_synthetic_elf(Path(temporary))
            with self.assertRaisesRegex(BUILDER.BuildError, "signature trailer is unsupported"):
                BUILDER.validate_debug_strip_transform(
                    original + b"signature~Module signature appended~\n",
                    stripped, removed,
                )

    def test_actual_pinned_modules_have_exact_debug_only_derivatives(self) -> None:
        missing = []
        for identity in BUILDER.TARGET_MODULES.values():
            source = identity["source"]
            if source.is_symlink():
                self.fail(f"pinned module input unexpectedly became a symlink: {source}")
            if not source.exists():
                missing.append(source.name)
            else:
                BUILDER._verify_file(
                    source, f"unstripped source fixture {source.name}",
                    identity["source_sha256"], identity["source_bytes"],
                )
        if missing:
            self.skipTest(f"private pinned module fixture(s) unavailable: {', '.join(missing)}")
        with tempfile.TemporaryDirectory(prefix="audio-strip-actual-modules-") as temporary:
            root = Path(temporary)
            for relative, identity in BUILDER.TARGET_MODULES.items():
                source = identity["source"]
                BUILDER._verify_file(
                    source, f"unstripped source fixture {relative}",
                    identity["source_sha256"], identity["source_bytes"],
                )
                output = root / Path(relative).name
                data, summary = BUILDER.strip_debug_candidate(source, output, identity)
                self.assertEqual(len(data), identity["candidate_bytes"], relative)
                self.assertEqual(summary["stripped_sha256"], identity["candidate_sha256"], relative)
                self.assertEqual(summary["source_sha256"], identity["source_sha256"], relative)
                self.assertEqual(summary["source_build_id_sha1"], identity["source_build_id"], relative)
                self.assertEqual(summary["stripped_build_id_sha1"], identity["candidate_build_id"], relative)
                self.assertEqual(summary["removed_debug_sections"], identity["stripped_debug_sections"], relative)
                self.assertGreater(summary["runtime_symbol_table_count"], 0, relative)
                self.assertGreater(summary["runtime_symbol_count"], 0, relative)
                BUILDER._verify_file(
                    source, f"unchanged source fixture {relative}",
                    identity["source_sha256"], identity["source_bytes"],
                )

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

    def test_external_wlan_internal_name_comes_from_modinfo_not_report_label(self) -> None:
        label = "selected external Lineage WLAN"
        module_path = Path("/tmp/wlan.ko")
        with mock.patch.object(BUILDER, "_modinfo_value", return_value="wlan") as read_name:
            actual = BUILDER.validate_internal_module_name(
                BUILDER.MODINFO, module_path, BUILDER.WLAN_MODULE_NAME, label,
            )
        read_name.assert_called_once_with(BUILDER.MODINFO, module_path, "name")
        self.assertEqual(actual, "wlan")
        self.assertNotEqual(actual, label)

        for wrong_name in ("not_wlan", label):
            with mock.patch.object(BUILDER, "_modinfo_value", return_value=wrong_name):
                with self.assertRaisesRegex(BUILDER.BuildError, "internal module name differs"):
                    BUILDER.validate_internal_module_name(
                        BUILDER.MODINFO, module_path, BUILDER.WLAN_MODULE_NAME, label,
                    )

    def test_mkbootimg_wrong_gki_helper_hash_fails_before_child(self) -> None:
        with tempfile.TemporaryDirectory(prefix="audio-mkbootimg-wrong-gki-") as temporary:
            directory = Path(temporary)
            script = directory / "mkbootimg.py"
            helper = directory / "gki" / "generate_gki_certificate.py"
            helper.parent.mkdir()
            script.write_bytes(b"pinned fixture script\n")
            helper.write_bytes(b"untrusted helper fixture\n")
            with (
                mock.patch.object(BUILDER, "MKBOOTIMG_SHA256",
                                  hashlib.sha256(script.read_bytes()).hexdigest()),
                mock.patch.object(BUILDER, "GKI_CERT_HELPER", helper),
                mock.patch.object(BUILDER, "GKI_CERT_HELPER_SHA256", "0" * 64),
                mock.patch.object(BUILDER.subprocess, "run") as child,
            ):
                with self.assertRaisesRegex(BUILDER.BuildError, "SHA-256 mismatch"):
                    BUILDER._run_mkbootimg(Path(sys.executable), script, ["--help"])
                child.assert_not_called()

    def test_mkbootimg_missing_gki_helper_fails_without_success(self) -> None:
        with tempfile.TemporaryDirectory(prefix="audio-mkbootimg-missing-gki-") as temporary:
            directory = Path(temporary)
            script = directory / "mkbootimg.py"
            script.write_bytes(b"pinned fixture script\n")
            missing_helper = directory / "gki" / "generate_gki_certificate.py"
            success_output = directory / "should-not-exist.img"
            with (
                mock.patch.object(BUILDER, "MKBOOTIMG_SHA256",
                                  hashlib.sha256(script.read_bytes()).hexdigest()),
                mock.patch.object(BUILDER, "GKI_CERT_HELPER", missing_helper),
                mock.patch.object(BUILDER.subprocess, "run") as child,
            ):
                with self.assertRaisesRegex(BUILDER.BuildError, "unavailable"):
                    BUILDER._run_mkbootimg(
                        Path(sys.executable), script, ["--output", str(success_output)],
                    )
                child.assert_not_called()
            self.assertFalse(success_output.exists())

    def test_isolated_mkbootimg_bootstrap_imports_only_explicit_fixture_root(self) -> None:
        with tempfile.TemporaryDirectory(prefix="audio-mkbootimg-bootstrap-") as temporary:
            package_root = Path(temporary) / "mkbootimg-package"
            gki_dir = package_root / "gki"
            gki_dir.mkdir(parents=True)
            helper = gki_dir / "generate_gki_certificate.py"
            helper.write_text('marker = "pinned-fixture-helper"\n', encoding="utf-8")
            script = package_root / "mkbootimg.py"
            script.write_text(
                "from pathlib import Path\n"
                "import sys\n"
                "from gki.generate_gki_certificate import marker\n"
                "Path(sys.argv[1]).write_text('\\t'.join((marker, "
                "str(sys.flags.isolated), str(sys.flags.no_site), sys.path[0])), "
                "encoding='utf-8')\n",
                encoding="utf-8",
            )
            output = Path(temporary) / "import-proof.txt"
            with (
                mock.patch.object(BUILDER, "MKBOOTIMG_SHA256",
                                  hashlib.sha256(script.read_bytes()).hexdigest()),
                mock.patch.object(BUILDER, "GKI_CERT_HELPER", helper),
                mock.patch.object(BUILDER, "GKI_CERT_HELPER_SHA256",
                                  hashlib.sha256(helper.read_bytes()).hexdigest()),
                mock.patch.object(BUILDER, "MKBOOTIMG_IMPORT_ROOT", package_root),
            ):
                command = BUILDER._mkbootimg_command(
                    Path(sys.executable), script, [str(output)], package_root,
                )
                self.assertEqual(command[1:4], ["-I", "-S", "-B"])
                BUILDER._run_mkbootimg(Path(sys.executable), script, [str(output)])
            self.assertEqual(
                output.read_text(encoding="utf-8"),
                f"pinned-fixture-helper\t1\t1\t{package_root}",
            )

    def _require_local_pinned_mkbootimg_fixture(self) -> None:
        for path, expected_sha, label in (
            (BUILDER.MKBOOTIMG, BUILDER.MKBOOTIMG_SHA256, "pinned mkbootimg.py"),
            (BUILDER.GKI_CERT_HELPER, BUILDER.GKI_CERT_HELPER_SHA256,
             "pinned mkbootimg GKI helper"),
        ):
            self.assertFalse(path.is_symlink(), f"{label} must not be a symlink")
            if not path.exists():
                self.skipTest(f"{label} fixture unavailable: {path}")
            self.assertEqual(
                hashlib.sha256(path.read_bytes()).hexdigest(), expected_sha,
                f"{label} fixture hash mismatch",
            )

    def test_pinned_mkbootimg_help_runs_with_pinned_gki_import(self) -> None:
        self._require_local_pinned_mkbootimg_fixture()
        result = BUILDER._run_mkbootimg(
            Path(sys.executable), BUILDER.MKBOOTIMG, ["--help"],
        )
        self.assertIn("usage:", result.stdout.lower())

    def test_pinned_mkbootimg_writes_tiny_header_v2_fixture(self) -> None:
        self._require_local_pinned_mkbootimg_fixture()
        with tempfile.TemporaryDirectory(prefix="audio-mkbootimg-v2-smoke-") as temporary:
            directory = Path(temporary)
            files = {}
            for name, content in (
                ("kernel", b"tiny synthetic kernel"),
                ("ramdisk", b"tiny synthetic ramdisk"),
                ("second", b"tiny synthetic second"),
                ("dtb", b"tiny synthetic dtb"),
            ):
                path = directory / name
                path.write_bytes(content)
                files[name] = path
            output = directory / "tiny-header-v2.img"
            BUILDER._run_mkbootimg(
                Path(sys.executable), BUILDER.MKBOOTIMG,
                [
                    "--base", "0x0", "--pagesize", "2048", "--header_version", "2",
                    "--os_version", "16.0.0", "--os_patch_level", "2026-09",
                    "--kernel", str(files["kernel"]), "--ramdisk", str(files["ramdisk"]),
                    "--second", str(files["second"]), "--dtb", str(files["dtb"]),
                    "--output", str(output),
                ],
            )
            image = output.read_bytes()
            self.assertEqual(image[:8], b"ANDROID!")
            self.assertEqual(struct.unpack_from("<I", image, 40)[0], 2)
            self.assertGreater(len(image), 2048)

    def test_effective_pythonoptimize_environment_is_not_ignored_by_test_runner(self) -> None:
        if os.environ.get("PYTHONOPTIMIZE") == "1":
            self.assertEqual(sys.flags.optimize, 1)

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
