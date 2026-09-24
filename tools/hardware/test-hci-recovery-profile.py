#!/usr/bin/env python3
"""Hardware-free checks for the HCI RECOVERY forward/reverse profiles."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
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


DEPLOY = load("s22_hci_profile_deployer", ROOT / "tools/hardware/deploy-audio-recovery.py")
REMOTE_TESTS = load(
    "s22_hci_remote_test_helpers",
    ROOT / "tools/hardware/test-recovery-deployment-hardening.py",
)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class HCIProfileManifestTests(unittest.TestCase):
    def test_forward_and_reverse_pin_the_only_allowed_image_pair(self):
        manifest = DEPLOY.HCI_FORWARD_MANIFEST
        self.assertEqual(
            manifest["candidate_sha256"],
            "42da267f3dd9f94f30f62a95fb2ac13f91d4cf98f1a2307f7cc14e45d9c49be5",
        )
        self.assertEqual(
            manifest["baseline_sha256"],
            "758fc9d30491e17b7c829a89d338ba69476efa15a1280deb8a1b9b8009687f4b",
        )
        forward = DEPLOY.resolve_hci_profile("hci-forward")
        reverse = DEPLOY.resolve_hci_profile("hci-reverse")
        self.assertEqual(forward["before_sha256"], manifest["baseline_sha256"])
        self.assertEqual(forward["new_sha256"], manifest["candidate_sha256"])
        self.assertEqual(reverse["before_sha256"], manifest["candidate_sha256"])
        self.assertEqual(reverse["new_sha256"], manifest["baseline_sha256"])
        self.assertEqual(reverse["image"], manifest["baseline_image"])
        self.assertNotEqual(forward["staging_directory"], reverse["staging_directory"])
        self.assertTrue(forward["staging_directory"].endswith("-second"))
        self.assertTrue(reverse["staging_directory"].endswith("-second"))

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="s22-hci-profile-")
        self.root = Path(self.temp.name)
        self.candidate = b"C" * 128
        self.baseline = b"B" * 128
        self.lineage = b"lineage recovery fixture"
        self.manifest = {
            "candidate_image": "candidate/recovery.img",
            "candidate_sha256": digest(self.candidate),
            "candidate_build_manifest": "candidate/manifest.json",
            "baseline_image": "baseline/recovery.img",
            "baseline_sha256": digest(self.baseline),
            "lineage_image": "lineage/recovery.img",
            "lineage_sha256": digest(self.lineage),
            "partition_size_bytes": len(self.candidate),
        }
        candidate_path = self.root / self.manifest["candidate_image"]
        baseline_path = self.root / self.manifest["baseline_image"]
        lineage_path = self.root / self.manifest["lineage_image"]
        for path in (candidate_path, baseline_path, lineage_path):
            path.parent.mkdir(parents=True, exist_ok=True)
        candidate_path.write_bytes(self.candidate)
        baseline_path.write_bytes(self.baseline)
        lineage_path.write_bytes(self.lineage)
        builder_manifest = {
            "image": self.manifest["candidate_image"],
            "image_sha256": self.manifest["candidate_sha256"],
            "base_image": self.manifest["baseline_image"],
            "base_image_sha256": self.manifest["baseline_sha256"],
            "partition_size_bytes": self.manifest["partition_size_bytes"],
            "phone_access": False,
        }
        (self.root / self.manifest["candidate_build_manifest"]).write_text(
            json.dumps(builder_manifest)
        )

    def tearDown(self):
        self.temp.cleanup()

    def validate(self, profile="hci-forward", manifest=None):
        selected = self.manifest if manifest is None else manifest
        with mock.patch.object(DEPLOY, "SIZE", len(self.candidate)):
            return DEPLOY.validate_hci_profile_artifacts(
                profile, root=self.root, manifest=selected
            )

    def test_candidate_and_rollback_artifacts_validate_in_both_directions(self):
        self.assertEqual(self.validate("hci-forward"), self.candidate)
        self.assertEqual(self.validate("hci-reverse"), self.baseline)

    def test_wrong_candidate_image_is_rejected(self):
        path = self.root / self.manifest["candidate_image"]
        path.write_bytes(b"X" + self.candidate[1:])
        with self.assertRaisesRegex(ValueError, "candidate image has an incorrect size or SHA-256"):
            self.validate()

    def test_wrong_baseline_image_is_rejected(self):
        path = self.root / self.manifest["baseline_image"]
        path.write_bytes(b"X" + self.baseline[1:])
        with self.assertRaisesRegex(ValueError, "rollback/base image has an incorrect size or SHA-256"):
            self.validate()

    def test_candidate_builder_manifest_must_pin_image_and_baseline(self):
        path = self.root / self.manifest["candidate_build_manifest"]
        builder_manifest = json.loads(path.read_text())
        builder_manifest["base_image_sha256"] = "0" * 64
        path.write_text(json.dumps(builder_manifest))
        with self.assertRaisesRegex(ValueError, "manifest mismatch for base_image_sha256"):
            self.validate()

    def test_hci_profile_rejects_wrong_device_identity(self):
        namespace = {"hashlib": hashlib, "os": os, "stat": __import__("stat")}
        exec(DEPLOY.REMOTE_GUARDS, namespace)
        with self.assertRaisesRegex(RuntimeError, "alias does not resolve"):
            namespace["validate_recovery_target"](
                "native-guardian", "5.10.260-g4e5c5ad7d950", 278,
                "/dev/sda17", True, 196608, True, os.makedev(259, 0),
                False, os.makedev(259, 0), 196608,
            )

    def test_wrong_stage_receipt_is_rejected(self):
        profile = DEPLOY.resolve_hci_profile("hci-reverse")
        receipt = {
            "mode": "stage", "partition_written": False,
            "backup_sha256": "0" * 64,
            "candidate_sha256": profile["new_sha256"],
        }
        with self.assertRaisesRegex(ValueError, "receipt mismatch for backup_sha256"):
            DEPLOY.validate_remote_receipt(
                receipt, mode="stage", before_sha=profile["before_sha256"],
                candidate_sha=profile["new_sha256"], size=DEPLOY.SIZE,
            )

    def test_wrong_capacity_manifest_is_rejected(self):
        manifest = dict(self.manifest, partition_size_bytes=len(self.candidate) + 1)
        with mock.patch.object(DEPLOY, "SIZE", len(self.candidate)):
            with self.assertRaisesRegex(ValueError, "incorrect RECOVERY capacity"):
                DEPLOY.validate_hci_profile_artifacts(
                    "hci-forward", root=self.root, manifest=manifest
                )

    def test_profile_device_guard_survives_optimized_python_modes(self):
        probe = r'''import importlib.util,os,stat,sys
spec=importlib.util.spec_from_file_location("deployer",sys.argv[1])
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
namespace={"hashlib":module.hashlib,"os":os,"stat":stat};exec(module.REMOTE_GUARDS,namespace)
try:
 namespace["validate_recovery_target"]("native-guardian","5.10.260-g4e5c5ad7d950",278,
  "/dev/sda17",True,196608,True,os.makedev(259,0),False,os.makedev(259,0),196608)
except RuntimeError as error:
 print("REFUSED:"+str(error));sys.exit(23)
print("UNSAFE_ACCEPT");sys.exit(0)
'''
        for mode in ("normal", "-O", "PYTHONOPTIMIZE=1"):
            env = os.environ.copy()
            env.pop("PYTHONOPTIMIZE", None)
            argv = [sys.executable]
            if mode == "-O":
                argv.append("-O")
            elif mode == "PYTHONOPTIMIZE=1":
                env["PYTHONOPTIMIZE"] = "1"
            argv.extend(["-c", probe, str(ROOT / "tools/hardware/deploy-audio-recovery.py")])
            with self.subTest(mode=mode):
                result = subprocess.run(argv, capture_output=True, text=True, env=env, check=False)
                self.assertEqual(result.returncode, 23, result.stdout + result.stderr)
                self.assertIn("alias does not resolve", result.stdout)

    def test_transport_timeout_is_not_retried_or_recorded_as_success(self):
        receipts = self.root / DEPLOY.HCI_TRIAL_ID
        transport = mock.Mock(side_effect=subprocess.TimeoutExpired("ssh", 100))
        with mock.patch.object(DEPLOY, "ROOT", self.root), \
             mock.patch.object(DEPLOY, "validate_hci_profile_artifacts", return_value=b"candidate"), \
             mock.patch.object(DEPLOY, "run_approved_ssh_wrapper", transport):
            with self.assertRaisesRegex(RuntimeError, "remote outcome may be unknown"):
                DEPLOY.main_hci_profile("hci-forward", "stage", receipt_dir=receipts,
                                         trial_identity=DEPLOY.HCI_TRIAL_ID)
        transport.assert_called_once()
        self.assertFalse((receipts / "hci-recovery-forward-stage.json").exists())

    def test_hci_execution_requires_the_one_authorized_trial_identity(self):
        transport = mock.Mock()
        with mock.patch.object(DEPLOY, "ROOT", self.root), \
             mock.patch.object(DEPLOY, "validate_hci_profile_artifacts", return_value=b"candidate"), \
             mock.patch.object(DEPLOY, "run_approved_ssh_wrapper", transport):
            with self.assertRaisesRegex(SystemExit, "explicitly authorized trial identity"):
                DEPLOY.main_hci_profile("hci-forward", "stage", trial_identity=None)
        transport.assert_not_called()

    def test_invalid_flash_receipt_is_not_recorded_as_success(self):
        receipts = self.root / DEPLOY.HCI_TRIAL_ID
        result = SimpleNamespace(
            returncode=0,
            stdout=json.dumps({
                "mode": "flash", "partition_written": "recovery", "bytes": DEPLOY.SIZE,
                "before_sha256": "0" * 64, "readback_sha256": "0" * 64,
                "reboot_performed": False,
            }).encode(),
            stderr=b"",
        )
        transport = mock.Mock(return_value=result)
        with mock.patch.object(DEPLOY, "ROOT", self.root), \
             mock.patch.object(DEPLOY, "validate_hci_profile_artifacts", return_value=b"candidate"), \
             mock.patch.object(DEPLOY, "run_approved_ssh_wrapper", transport):
            with self.assertRaisesRegex(RuntimeError, "invalid receipt"):
                DEPLOY.main_hci_profile("hci-forward", "flash", receipt_dir=receipts,
                                         trial_identity=DEPLOY.HCI_TRIAL_ID)
        transport.assert_called_once()
        self.assertFalse((receipts / "hci-recovery-forward-flash.json").exists())

    def test_failed_readback_is_not_recorded_as_success_or_retried(self):
        receipts = self.root / DEPLOY.HCI_TRIAL_ID
        result = SimpleNamespace(
            returncode=1,
            stdout=b"",
            stderr=b"RECOVERY readback; do not reboot SHA-256 mismatch",
        )
        transport = mock.Mock(return_value=result)
        with mock.patch.object(DEPLOY, "ROOT", self.root), \
             mock.patch.object(DEPLOY, "validate_hci_profile_artifacts", return_value=b"candidate"), \
             mock.patch.object(DEPLOY, "run_approved_ssh_wrapper", transport):
            with self.assertRaisesRegex(RuntimeError, "flash failed; no reboot was requested") as failure:
                DEPLOY.main_hci_profile("hci-forward", "flash", receipt_dir=receipts,
                                         trial_identity=DEPLOY.HCI_TRIAL_ID)
        self.assertIn("RECOVERY readback", str(failure.exception))
        transport.assert_called_once()
        self.assertFalse((receipts / "hci-recovery-forward-flash.json").exists())

    def test_forward_stage_passes_pinned_manifest_to_shared_remote_renderer(self):
        receipts = self.root / DEPLOY.HCI_TRIAL_ID
        profile = DEPLOY.resolve_hci_profile("hci-forward")
        result = SimpleNamespace(
            returncode=0,
            stdout=json.dumps({
                "mode": "stage", "partition_written": False,
                "backup_sha256": profile["before_sha256"],
                "candidate_sha256": profile["new_sha256"],
            }).encode(),
            stderr=b"",
        )
        transport = mock.Mock(return_value=result)
        with mock.patch.object(DEPLOY, "ROOT", self.root), \
             mock.patch.object(DEPLOY, "validate_hci_profile_artifacts", return_value=b"candidate-image"), \
             mock.patch.object(DEPLOY, "run_approved_ssh_wrapper", transport):
            DEPLOY.main_hci_profile("hci-forward", "stage", receipt_dir=receipts,
                                         trial_identity=DEPLOY.HCI_TRIAL_ID)
        transport.assert_called_once()
        args, kwargs = transport.call_args
        remote_command = args[1]
        self.assertIn(profile["before_sha256"], remote_command)
        self.assertIn(profile["new_sha256"], remote_command)
        self.assertIn("bt-hci-forward-20260924", remote_command)
        self.assertEqual(kwargs["input_data"], b"candidate-image")
        self.assertEqual(kwargs["timeout"], 100)
        self.assertTrue((receipts / "hci-recovery-forward-stage.json").is_file())
        stage_receipt = json.loads((receipts / "hci-recovery-forward-stage.json").read_text())
        self.assertEqual(stage_receipt["trial_identity"], DEPLOY.HCI_TRIAL_ID)
        self.assertEqual(stage_receipt["profile"], "hci-forward")

    def test_forward_flash_has_no_client_deadline_during_partition_write(self):
        receipts = self.root / DEPLOY.HCI_TRIAL_ID
        profile = DEPLOY.resolve_hci_profile("hci-forward")
        result = SimpleNamespace(
            returncode=0,
            stdout=json.dumps({
                "mode": "flash", "partition_written": "recovery",
                "bytes": DEPLOY.SIZE,
                "before_sha256": profile["before_sha256"],
                "readback_sha256": profile["new_sha256"],
                "reboot_performed": False,
            }).encode(),
            stderr=b"",
        )
        transport = mock.Mock(return_value=result)
        with mock.patch.object(DEPLOY, "ROOT", self.root), \
             mock.patch.object(DEPLOY, "validate_hci_profile_artifacts", return_value=b"candidate-image"), \
             mock.patch.object(DEPLOY, "run_approved_ssh_wrapper", transport):
                DEPLOY.main_hci_profile("hci-forward", "flash", receipt_dir=receipts,
                                         trial_identity=DEPLOY.HCI_TRIAL_ID)
        transport.assert_called_once()
        _, kwargs = transport.call_args
        self.assertIsNone(kwargs["timeout"])
        self.assertEqual(kwargs["input_data"], b"")
        self.assertTrue((receipts / "hci-recovery-forward-flash.json").is_file())
        flash_receipt = json.loads((receipts / "hci-recovery-forward-flash.json").read_text())
        self.assertEqual(flash_receipt["trial_identity"], DEPLOY.HCI_TRIAL_ID)
        self.assertEqual(flash_receipt["profile"], "hci-forward")

    def test_reverse_stage_sends_only_the_exact_rollback_to_separate_stage(self):
        receipts = self.root / DEPLOY.HCI_TRIAL_ID
        profile = DEPLOY.resolve_hci_profile("hci-reverse")
        result = SimpleNamespace(
            returncode=0,
            stdout=json.dumps({
                "mode": "stage", "partition_written": False,
                "backup_sha256": profile["before_sha256"],
                "candidate_sha256": profile["new_sha256"],
            }).encode(),
            stderr=b"",
        )
        transport = mock.Mock(return_value=result)
        with mock.patch.object(DEPLOY, "ROOT", self.root), \
             mock.patch.object(DEPLOY, "validate_hci_profile_artifacts", return_value=b"exact-rollback-image"), \
             mock.patch.object(DEPLOY, "run_approved_ssh_wrapper", transport):
                DEPLOY.main_hci_profile("hci-reverse", "stage", receipt_dir=receipts,
                                         trial_identity=DEPLOY.HCI_TRIAL_ID)
        transport.assert_called_once()
        args, kwargs = transport.call_args
        remote_command = args[1]
        self.assertIn(profile["before_sha256"], remote_command)
        self.assertIn(profile["new_sha256"], remote_command)
        self.assertIn("bt-hci-reverse-20260924", remote_command)
        self.assertIn("hci-candidate-rollback.img", remote_command)
        self.assertEqual(kwargs["input_data"], b"exact-rollback-image")
        self.assertTrue((receipts / "hci-recovery-reverse-stage.json").is_file())

    def test_reverse_flash_requires_candidate_before_and_writes_only_rollback(self):
        receipts = self.root / DEPLOY.HCI_TRIAL_ID
        profile = DEPLOY.resolve_hci_profile("hci-reverse")
        result = SimpleNamespace(
            returncode=0,
            stdout=json.dumps({
                "mode": "flash", "partition_written": "recovery",
                "bytes": DEPLOY.SIZE,
                "before_sha256": profile["before_sha256"],
                "readback_sha256": profile["new_sha256"],
                "reboot_performed": False,
            }).encode(),
            stderr=b"",
        )
        transport = mock.Mock(return_value=result)
        with mock.patch.object(DEPLOY, "ROOT", self.root), \
             mock.patch.object(DEPLOY, "validate_hci_profile_artifacts", return_value=b"rollback-image"), \
             mock.patch.object(DEPLOY, "run_approved_ssh_wrapper", transport):
                DEPLOY.main_hci_profile("hci-reverse", "flash", receipt_dir=receipts,
                                         trial_identity=DEPLOY.HCI_TRIAL_ID)
        transport.assert_called_once()
        args, kwargs = transport.call_args
        remote_command = args[1]
        self.assertIn(profile["before_sha256"], remote_command)
        self.assertIn(profile["new_sha256"], remote_command)
        self.assertIn("bt-hci-reverse-20260924", remote_command)
        self.assertEqual(kwargs["input_data"], b"")
        self.assertIsNone(kwargs["timeout"])
        self.assertTrue((receipts / "hci-recovery-reverse-flash.json").is_file())

    def test_receipt_directory_rejects_symlinks_and_existing_receipt(self):
        target=self.root/"private"
        target.mkdir(mode=0o700)
        link=self.root/"receipt-link"
        link.symlink_to(target,target_is_directory=True)
        with self.assertRaisesRegex(ValueError,"symlink or non-directory"):
            DEPLOY.prepare_private_receipt_directory(link)
        receipt=target/"hci-recovery-forward-stage.json"
        receipt.write_text("already exists")
        with self.assertRaisesRegex(ValueError,"refusing to overwrite"):
            DEPLOY.ensure_new_receipt(receipt)

    def test_receipt_is_private_durable_and_never_overwritten(self):
        receipts=DEPLOY.prepare_private_receipt_directory(self.root/"receipts")
        output=receipts/"receipt.json"
        value={"phase":"staged","partition_written":False}
        DEPLOY.persist_receipt(output,value)
        self.assertEqual(json.loads(output.read_text()),value)
        self.assertEqual(output.stat().st_mode&0o777,0o600)
        with self.assertRaises(FileExistsError):
            DEPLOY.persist_receipt(output,{"phase":"unexpected"})

    def test_receipt_uses_verified_directory_even_if_path_is_replaced(self):
        receipts=DEPLOY.prepare_private_receipt_directory(self.root/"receipts")
        moved=self.root/"receipts-original"
        directory_fd=DEPLOY.open_private_receipt_directory(receipts)
        try:
            receipts.rename(moved)
            receipts.mkdir(mode=0o700)
            output=receipts/"receipt.json"
            DEPLOY.ensure_new_receipt(output,directory_fd=directory_fd)
            DEPLOY.persist_receipt(
                output,{"phase":"remote-success"},directory_fd=directory_fd)
        finally:
            DEPLOY.os.close(directory_fd)
        self.assertTrue((moved/"receipt.json").is_file())
        self.assertFalse((receipts/"receipt.json").exists())

    def test_receipt_directory_sync_failure_is_not_retried(self):
        receipts=DEPLOY.prepare_private_receipt_directory(self.root/"receipts")
        output=receipts/"receipt.json"
        sync=mock.Mock(side_effect=[None,OSError("injected directory fsync failure")])
        with mock.patch.object(DEPLOY.os,"fsync",sync):
            with self.assertRaisesRegex(OSError,"directory fsync failure"):
                DEPLOY.persist_receipt(output,{"phase":"remote-success"})
        self.assertEqual(sync.call_count,2)
        self.assertTrue(output.is_file())
        with self.assertRaises(FileExistsError):
            DEPLOY.persist_receipt(output,{"phase":"do-not-repeat"})

    def test_stage_has_zero_partition_writes_and_failed_readback_has_no_success_receipt(self):
        sandbox = REMOTE_TESTS.RenderedRemoteSandbox()
        profile = DEPLOY.resolve_hci_profile("hci-forward")
        sandbox.rendered = DEPLOY.render_remote(
            base_sha=sandbox.base_sha, new_sha=sandbox.candidate_sha,
            staging_directory=profile["staging_directory"],
            rollback_filename=profile["rollback_filename"],
        )
        try:
            staged = sandbox.execute("stage", candidate_input=True)
            self.assertIsNone(staged.error, staged.stdout)
            self.assertEqual(sandbox.device_write_bytes, 0)
            self.assertEqual(json.loads(staged.stdout)["partition_written"], False)

            real_read = sandbox.fake_os.read
            corrupted = {"done": False}

            def corrupt_first_postwrite_partition_read(fd, amount):
                data = real_read(fd, amount)
                if (sandbox.device_write_bytes >= sandbox.SIZE
                        and sandbox.fake_os.fd_kinds.get(fd) == "partition"
                        and data and not corrupted["done"]):
                    corrupted["done"] = True
                    return bytes([data[0] ^ 1]) + data[1:]
                return data

            sandbox.fake_os.read = corrupt_first_postwrite_partition_read
            failed_flash = sandbox.execute("flash")
            self.assertIsNotNone(failed_flash.error)
            self.assertIn("RECOVERY readback; do not reboot SHA-256 mismatch", str(failed_flash.error))
            self.assertTrue(corrupted["done"])
            self.assertEqual(failed_flash.stdout, "")
            self.assertNotIn("s22-reboot", sandbox.rendered)
        finally:
            sandbox.close()


if __name__ == "__main__":
    unittest.main()
