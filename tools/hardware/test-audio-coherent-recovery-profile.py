#!/usr/bin/env python3
"""Hardware-free tests for the host-only audio RECOVERY profile validator."""
from __future__ import annotations

import contextlib
import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


PROFILE = load(
    "s22_audio_coherent_profile_test",
    ROOT / "tools/hardware/audio-coherent-recovery-profile.py",
)
REMOTE_FIXTURES = load(
    "s22_audio_coherent_remote_fixtures",
    ROOT / "tools/hardware/test-recovery-deployment-hardening.py",
)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class AudioProfileFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="s22-audio-profile-")
        self.root = Path(self.temp.name)
        self.size = 256
        self.candidate = b"A" * self.size
        self.baseline = b"B" * self.size
        self.lineage = b"L" * self.size
        self.candidate_sha = digest(self.candidate)
        self.baseline_sha = digest(self.baseline)
        self.lineage_sha = digest(self.lineage)
        for relative, content in (
            (PROFILE.CANDIDATE_IMAGE, self.candidate),
            (PROFILE.BASELINE_IMAGE, self.baseline),
            (PROFILE.LINEAGE_IMAGE, self.lineage),
        ):
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)

        self.modules = {
            "lib/modules/snd-soc-samsung-abox.ko": "a" * 64,
            "lib/modules/rainbow_prince.ko": "b" * 64,
            "lib/modules/exynos-usb-audio-offloading.ko": "c" * 64,
        }
        self.package_manifest = {
            "schema": "s22-audio-coherent-recovery-package/v1",
            "candidate_image_bytes": self.size,
            "candidate_image_sha256": self.candidate_sha,
            "partition_size_bytes": self.size,
            "candidate_avb_footer_verified": True,
            "base_image_avb_footer_verified": True,
            "avb_algorithm": "NONE",
            "phone_access": False,
            "deployed": False,
            "host_only": True,
            "module_loaded": False,
            "boot_authorized": False,
            "base_image": {
                "bytes": self.size,
                "sha256": self.baseline_sha,
                "path": "/fake/artifacts/" + PROFILE.BASELINE_IMAGE,
            },
            "replacements": {
                path: {"candidate": {"sha256": module_sha}}
                for path, module_sha in self.modules.items()
            },
            "static_abi": {
                "ramdisk": {
                    "module_count": 324,
                    "import_count": 16569,
                    "all_imports_complete_and_compatible": True,
                    "missing_symbol_count": 0,
                    "crc_mismatch_count": 0,
                    "unknown_crc_count": 0,
                },
                "selected_external_wlan": {
                    "imports": 495,
                    "included_in_ramdisk": False,
                    "all_imports_complete_and_compatible": True,
                },
            },
        }
        manifest_path = self.root / PROFILE.CANDIDATE_MANIFEST
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        self.manifest_bytes = json.dumps(
            self.package_manifest, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        manifest_path.write_bytes(self.manifest_bytes)
        self.patches = mock.patch.multiple(
            PROFILE,
            PARTITION_SIZE=self.size,
            CANDIDATE_SHA256=self.candidate_sha,
            CANDIDATE_MANIFEST_SHA256=digest(self.manifest_bytes),
            BASELINE_SHA256=self.baseline_sha,
            EXPECTED_MODULES=self.modules,
            PROFILES=self._profiles(),
        )
        self.patches.start()
        self.lineage_patch = mock.patch.object(PROFILE.DEPLOY, "LINEAGE_SHA", self.lineage_sha)
        self.lineage_patch.start()
        self.addCleanup(self.lineage_patch.stop)
        self.addCleanup(self.patches.stop)
        self.addCleanup(self.temp.cleanup)

    def _profiles(self):
        profiles = copy.deepcopy(PROFILE.PROFILES)
        profiles["audio-forward"].update(
            before_sha256=self.baseline_sha,
            target_sha256=self.candidate_sha,
        )
        profiles["audio-reverse"].update(
            before_sha256=self.candidate_sha,
            target_sha256=self.baseline_sha,
        )
        return profiles

    def _valid_receipt(self, profile_name="audio-forward", mode="stage"):
        profile = PROFILE.resolve_profile(profile_name)
        if mode == "stage":
            return {
                "mode": "stage",
                "partition_written": False,
                "backup_sha256": profile["before_sha256"],
                "candidate_sha256": profile["target_sha256"],
                "profile": profile_name,
                "trial_identity": PROFILE.TRIAL_IDENTITY,
            }
        return {
            "mode": "flash",
            "partition_written": "recovery",
            "bytes": PROFILE.PARTITION_SIZE,
            "before_sha256": profile["before_sha256"],
            "readback_sha256": profile["target_sha256"],
            "reboot_performed": False,
            "profile": profile_name,
            "trial_identity": PROFILE.TRIAL_IDENTITY,
        }


class ProfileAndArtifactTests(AudioProfileFixture):
    def test_profiles_pin_opposite_before_and_target_images(self):
        forward = PROFILE.resolve_profile("audio-forward")
        reverse = PROFILE.resolve_profile("audio-reverse")
        self.assertEqual(forward["before_sha256"], self.baseline_sha)
        self.assertEqual(forward["target_sha256"], self.candidate_sha)
        self.assertEqual(reverse["before_sha256"], self.candidate_sha)
        self.assertEqual(reverse["target_sha256"], self.baseline_sha)
        self.assertEqual(forward["staging_directory"],
                         "/srv/s22/audio-coherent-forward-20261003")
        self.assertEqual(reverse["staging_directory"],
                         "/srv/s22/audio-coherent-reverse-20261003")
        self.assertNotEqual(forward["rollback_filename"], reverse["rollback_filename"])
        self.assertEqual(forward["trial_identity"], "audio-coherent-20261003-first")

    def test_exact_candidate_baseline_manifest_and_lineage_validate_in_both_directions(self):
        for profile in ("audio-forward", "audio-reverse"):
            with self.subTest(profile=profile):
                result = PROFILE.validate_profile_artifacts(profile, artifact_root=self.root)
                self.assertEqual(result["before_sha256"],
                                 PROFILE.resolve_profile(profile)["before_sha256"])
                self.assertEqual(result["target_sha256"],
                                 PROFILE.resolve_profile(profile)["target_sha256"])
                self.assertEqual(result["target_bytes_validated"], self.size)

    def test_wrong_candidate_or_baseline_bytes_are_rejected(self):
        candidate_path = self.root / PROFILE.CANDIDATE_IMAGE
        candidate_path.write_bytes(b"X" * self.size)
        with self.assertRaisesRegex(ValueError, "incorrect size or SHA-256"):
            PROFILE.validate_profile_artifacts("audio-forward", artifact_root=self.root)
        candidate_path.write_bytes(self.candidate)

        baseline_path = self.root / PROFILE.BASELINE_IMAGE
        baseline_path.write_bytes(b"Y" * self.size)
        with self.assertRaisesRegex(ValueError, "incorrect size or SHA-256"):
            PROFILE.validate_profile_artifacts("audio-forward", artifact_root=self.root)

    def test_wrong_manifest_hash_or_semantics_are_rejected(self):
        manifest_path = self.root / PROFILE.CANDIDATE_MANIFEST
        manifest_path.write_bytes(self.manifest_bytes + b" ")
        with self.assertRaisesRegex(ValueError, "manifest SHA-256 mismatch"):
            PROFILE.validate_profile_artifacts("audio-forward", artifact_root=self.root)

        bad = dict(self.package_manifest, phone_access=True)
        bad_bytes = json.dumps(bad, sort_keys=True, separators=(",", ":")).encode()
        manifest_path.write_bytes(bad_bytes)
        with mock.patch.object(PROFILE, "CANDIDATE_MANIFEST_SHA256", digest(bad_bytes)):
            with self.assertRaisesRegex(ValueError, "incorrect phone_access"):
                PROFILE.validate_profile_artifacts("audio-forward", artifact_root=self.root)

    def test_manifest_requires_exact_three_replacements_and_static_inventory(self):
        bad = copy.deepcopy(self.package_manifest)
        bad["replacements"]["lib/modules/extra.ko"] = {
            "candidate": {"sha256": "d" * 64}
        }
        bad_bytes = json.dumps(bad, sort_keys=True, separators=(",", ":")).encode()
        (self.root / PROFILE.CANDIDATE_MANIFEST).write_bytes(bad_bytes)
        with mock.patch.object(PROFILE, "CANDIDATE_MANIFEST_SHA256", digest(bad_bytes)):
            with self.assertRaisesRegex(ValueError, "replacement membership"):
                PROFILE.validate_profile_artifacts("audio-forward", artifact_root=self.root)

        bad = copy.deepcopy(self.package_manifest)
        bad["static_abi"]["ramdisk"]["crc_mismatch_count"] = 1
        bad_bytes = json.dumps(bad, sort_keys=True, separators=(",", ":")).encode()
        (self.root / PROFILE.CANDIDATE_MANIFEST).write_bytes(bad_bytes)
        with mock.patch.object(PROFILE, "CANDIDATE_MANIFEST_SHA256", digest(bad_bytes)):
            with self.assertRaisesRegex(ValueError, "static ABI evidence mismatch"):
                PROFILE.validate_profile_artifacts("audio-forward", artifact_root=self.root)

    def test_symlink_manifest_and_invalid_profile_fail_closed(self):
        manifest_path = self.root / PROFILE.CANDIDATE_MANIFEST
        real = self.root / "manifest.real"
        real.write_bytes(self.manifest_bytes)
        manifest_path.unlink()
        manifest_path.symlink_to(real)
        with self.assertRaisesRegex(ValueError, "non-symlink regular file"):
            PROFILE.validate_profile_artifacts("audio-forward", artifact_root=self.root)
        with self.assertRaisesRegex(ValueError, "unknown audio recovery profile"):
            PROFILE.resolve_profile("hci-forward")

    def test_host_only_plan_never_calls_transport_or_creates_markers(self):
        before = sorted(str(path.relative_to(self.root)) for path in self.root.rglob("*"))
        with mock.patch.object(PROFILE.DEPLOY, "run_approved_ssh_wrapper",
                               side_effect=AssertionError("transport must not run")) as transport:
            plan = PROFILE.build_plan("audio-forward", artifact_root=self.root)
        after = sorted(str(path.relative_to(self.root)) for path in self.root.rglob("*"))
        transport.assert_not_called()
        self.assertEqual(before, after)
        self.assertFalse(plan["execution"])
        self.assertTrue(plan["host_only"])
        self.assertFalse(plan["stage_flash_cli_available"])
        self.assertFalse(plan["current_deployment_authorized"])
        self.assertFalse(plan["independent_hardware_rescue_demonstrated"])
        self.assertFalse(plan["operation_marker_created"])
        self.assertFalse(plan["partition_written"])
        self.assertFalse(plan["reboot_performed"])

    def test_cli_rejects_stage_and_flash_options_without_transport(self):
        for option in ("--stage", "--flash"):
            with self.subTest(option=option), \
                 mock.patch.object(PROFILE.DEPLOY, "run_approved_ssh_wrapper",
                                   side_effect=AssertionError("transport must not run")) as transport, \
                 contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as raised:
                    PROFILE.main(["--profile", "audio-forward", "--artifact-root",
                                  str(self.root), option])
                self.assertEqual(raised.exception.code, 2)
                transport.assert_not_called()

class ReceiptValidationTests(AudioProfileFixture):
    def test_valid_stage_and_flash_receipts_are_direction_bound(self):
        for profile_name in ("audio-forward", "audio-reverse"):
            for mode in ("stage", "flash"):
                with self.subTest(profile=profile_name, mode=mode):
                    result = PROFILE.validate_receipt(
                        profile_name, self._valid_receipt(profile_name, mode))
                    profile = PROFILE.resolve_profile(profile_name)
                    self.assertEqual(result["before_sha256"], profile["before_sha256"])
                    self.assertEqual(result["target_sha256"], profile["target_sha256"])
                    self.assertFalse(result["reboot_performed"])

    def test_wrong_profile_trial_image_baseline_or_unknown_outcome_is_rejected(self):
        cases = []
        receipt = self._valid_receipt()
        receipt["profile"] = "audio-reverse"
        cases.append((receipt, "profile identity mismatch"))
        receipt = self._valid_receipt()
        receipt["trial_identity"] = "hci-candidate-20260924-second"
        cases.append((receipt, "trial identity mismatch"))
        receipt = self._valid_receipt()
        receipt["candidate_sha256"] = "0" * 64
        cases.append((receipt, "candidate_sha256"))
        receipt = self._valid_receipt()
        receipt["backup_sha256"] = "0" * 64
        cases.append((receipt, "backup_sha256"))
        receipt = self._valid_receipt()
        receipt["outcome"] = "unknown"
        cases.append((receipt, "missing or unrecognized fields"))
        receipt = self._valid_receipt()
        receipt["mode"] = "unknown"
        cases.append((receipt, "mode is unknown or ambiguous"))
        receipt = self._valid_receipt(mode="flash")
        receipt["readback_sha256"] = "0" * 64
        cases.append((receipt, "readback_sha256"))
        for receipt, error in cases:
            with self.subTest(error=error, receipt=receipt):
                with self.assertRaisesRegex(ValueError, error):
                    PROFILE.validate_receipt("audio-forward", receipt)

    def test_receipt_file_is_read_only_bounded_and_regular(self):
        path = self.root / "receipt.json"
        path.write_text(json.dumps(self._valid_receipt()), encoding="utf-8")
        self.assertEqual(PROFILE.read_receipt(path)["mode"], "stage")

        link = self.root / "receipt-link.json"
        link.symlink_to(path)
        with self.assertRaisesRegex(ValueError, "non-symlink regular file"):
            PROFILE.read_receipt(link)

        oversized = self.root / "oversized.json"
        oversized.write_bytes(b"x" * 65537)
        with self.assertRaisesRegex(ValueError, "input bound"):
            PROFILE.read_receipt(oversized)

        malformed = self.root / "malformed.json"
        malformed.write_text("{} trailing", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "invalid JSON"):
            PROFILE.read_receipt(malformed)

        duplicate = self.root / "duplicate.json"
        duplicate.write_text('{"mode":"stage","mode":"flash"}', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "duplicate JSON field"):
            PROFILE.read_receipt(duplicate)


class RenderedStageIntegrationTests(unittest.TestCase):
    def test_shared_rendered_stage_stages_both_directions_without_partition_writes(self):
        for profile_name, base_byte, target_byte in (
            ("audio-forward", 0x42, 0x43),
            ("audio-reverse", 0x43, 0x42),
        ):
            sandbox_class = type(
                "AudioDirectionSandbox",
                (REMOTE_FIXTURES.RenderedRemoteSandbox,),
                {"BASE_BYTE": base_byte, "CANDIDATE_BYTE": target_byte},
            )
            sandbox = sandbox_class()
            try:
                profile = PROFILE.resolve_profile(profile_name)
                stage_name = Path(profile["staging_directory"]).name
                sandbox.stage_dir = sandbox.root / "srv/s22" / stage_name
                synthetic_profiles = copy.deepcopy(PROFILE.PROFILES)
                synthetic_profiles[profile_name].update(
                    before_sha256=sandbox.base_sha,
                    target_sha256=sandbox.candidate_sha,
                )
                with mock.patch.object(PROFILE, "PROFILES", synthetic_profiles):
                    sandbox.rendered = PROFILE.render_remote(profile_name)
                partition_writes_before = sandbox.device_write_bytes
                stage = sandbox.execute("stage", candidate_input=True)
                self.assertIsNone(stage.error, str(stage.error))
                remote_receipt = json.loads(stage.stdout)
                self.assertEqual(remote_receipt, {
                    "mode": "stage",
                    "partition_written": False,
                    "backup_sha256": sandbox.base_sha,
                    "candidate_sha256": sandbox.candidate_sha,
                })
                self.assertEqual(sandbox.device_write_bytes, partition_writes_before)
                self.assertEqual(sandbox.hash_file(sandbox.partition), sandbox.base_sha)
                self.assertEqual(
                    sandbox.hash_file(sandbox.stage_dir / profile["rollback_filename"]),
                    sandbox.base_sha,
                )
                self.assertEqual(
                    sandbox.hash_file(sandbox.stage_dir / "recovery.img"),
                    sandbox.candidate_sha,
                )
                enriched = {
                    **remote_receipt,
                    "profile": profile_name,
                    "trial_identity": PROFILE.TRIAL_IDENTITY,
                }
                with mock.patch.object(PROFILE, "PROFILES", synthetic_profiles):
                    result = PROFILE.validate_receipt(profile_name, enriched)
                self.assertTrue(result["receipt_validated"])
                self.assertFalse(result["reboot_performed"])
            finally:
                sandbox.close()


class ActualPrivateArtifactTests(unittest.TestCase):
    def test_actual_private_artifact_fixture_when_configured(self):
        configured = os.environ.get("S22_AUDIO_PROFILE_ARTIFACT_ROOT")
        if not configured:
            self.skipTest("private 100663296-byte package inputs are not part of portable CI")
        for profile in ("audio-forward", "audio-reverse"):
            result = PROFILE.validate_profile_artifacts(profile, artifact_root=Path(configured))
            self.assertEqual(result["candidate_package_manifest_sha256"],
                             PROFILE.CANDIDATE_MANIFEST_SHA256)
            self.assertEqual(result["target_bytes_validated"], PROFILE.PARTITION_SIZE)


if __name__ == "__main__":
    unittest.main(verbosity=2)
