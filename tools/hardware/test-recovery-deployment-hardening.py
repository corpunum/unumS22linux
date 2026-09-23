#!/usr/bin/env python3
"""Host-only regressions for RECOVERY artifact and embedded target gates."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
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


DEPLOY = load("s22_deploy_audio_extra", ROOT / "tools/hardware/deploy-audio-extra-recovery.py")
BASE = load("s22_deploy_audio_base", ROOT / "tools/hardware/deploy-audio-recovery.py")
BUILDER = load("s22_build_bt_hci_recovery", ROOT / "tools/hardware/build-bt-hci-recovery.py")


class ArtifactValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="s22-recovery-gates-")
        self.root = Path(self.temp.name)
        self.candidate_path = self.root / "candidate.img"
        self.rollback_path = self.root / "rollback.img"
        self.lineage_path = self.root / "lineage.img"
        self.manifest_path = self.root / "manifest.json"
        rollback = bytearray(b"R" * 2048)
        candidate = bytearray(rollback)
        for start, end in ((16, 20), (576, 608), (1636, 1644)):
            candidate[start:end] = b"C" * (end - start)
        self.candidate = bytes(candidate)
        self.rollback = bytes(rollback)
        self.lineage = b"known-good rollback test payload"
        self.candidate_path.write_bytes(self.candidate)
        self.rollback_path.write_bytes(self.rollback)
        self.lineage_path.write_bytes(self.lineage)
        self.hashes = {
            "candidate_sha": hashlib.sha256(self.candidate).hexdigest(),
            "rollback_sha": hashlib.sha256(self.rollback).hexdigest(),
            "lineage_sha": hashlib.sha256(self.lineage).hexdigest(),
        }
        self.manifest_path.write_text(json.dumps({
            "image": "builds/audio-extra-v2-20260922/recovery.img",
            "image_sha256": self.hashes["candidate_sha"],
            "base_image_sha256": self.hashes["rollback_sha"],
            "extra_count": 20,
            "phone_access": False,
        }))

    def tearDown(self):
        self.temp.cleanup()

    def validate(self, candidate_path=None, rollback_path=None, lineage_path=None, **overrides):
        args = {
            "size": 2048,
            **self.hashes,
            "manifest_path": self.manifest_path,
            "expected_manifest_image": "builds/audio-extra-v2-20260922/recovery.img",
            "root": self.root,
        }
        args.update(overrides)
        return DEPLOY.validate_artifacts(
            candidate_path or self.candidate_path,
            rollback_path or self.rollback_path,
            lineage_path or self.lineage_path,
            **args,
        )

    def test_valid_exact_size_hashes_and_header_preservation(self):
        self.assertEqual(self.validate(), self.candidate)

    def test_wrong_candidate_hash_is_rejected(self):
        self.candidate_path.write_bytes(b"X" + self.candidate[1:])
        with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
            self.validate()

    def test_wrong_rollback_hash_is_rejected(self):
        self.rollback_path.write_bytes(b"X" + self.rollback[1:])
        with self.assertRaisesRegex(ValueError, "rollback recovery image SHA-256 mismatch"):
            self.validate()

    def test_build_manifest_must_match_image_and_rollback_provenance(self):
        manifest = json.loads(self.manifest_path.read_text())
        manifest["base_image_sha256"] = "0" * 64
        self.manifest_path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, "base-image hash does not match"):
            self.validate()
        with self.assertRaisesRegex(ValueError, "candidate build manifest is missing"):
            self.validate(manifest_path=self.root / "missing-manifest.json")

    def test_incorrect_partition_size_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "expected 2049"):
            self.validate(size=2049)

    def test_missing_rollback_is_rejected_before_operation(self):
        with self.assertRaisesRegex(ValueError, "rollback recovery image is missing"):
            self.validate(rollback_path=self.root / "missing-rollback.img")

    def test_symlink_candidate_is_rejected(self):
        link = self.root / "candidate-link.img"
        link.symlink_to(self.candidate_path)
        with self.assertRaisesRegex(ValueError, "non-symlink regular file"):
            self.validate(candidate_path=link)

    def test_immutable_header_change_is_rejected(self):
        changed = bytearray(self.candidate)
        changed[300] ^= 1
        self.candidate_path.write_bytes(changed)
        self.hashes["candidate_sha"] = hashlib.sha256(changed).hexdigest()
        manifest = json.loads(self.manifest_path.read_text())
        manifest["image_sha256"] = self.hashes["candidate_sha"]
        self.manifest_path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, "immutable boot header"):
            self.validate()

    def test_base_host_gate_refuses_wrong_hash(self):
        with self.assertRaisesRegex(ValueError, "incorrect size or SHA-256"):
            BASE.validate_host_artifacts(self.candidate_path, self.rollback_path, self.lineage_path)

    def test_candidate_hash_gate_survives_optimized_host_python(self):
        probe = r'''import importlib.util,sys
spec=importlib.util.spec_from_file_location("deploy",sys.argv[1])
mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
try:
 mod.validate_artifacts(sys.argv[2],sys.argv[3],sys.argv[4],size=2048,
  candidate_sha="0"*64,rollback_sha=sys.argv[5],lineage_sha=sys.argv[6],
  manifest_path=sys.argv[7],expected_manifest_image="builds/audio-extra-v2-20260922/recovery.img",root=sys.argv[8])
except ValueError as error:
 print("REFUSED:"+str(error));sys.exit(23)
print("UNSAFE_ACCEPT");sys.exit(0)
'''
        for mode in ("normal", "-O", "PYTHONOPTIMIZE=1"):
            env = os.environ.copy()
            argv = [sys.executable]
            if mode == "-O":
                argv.append("-O")
            elif mode == "PYTHONOPTIMIZE=1":
                env["PYTHONOPTIMIZE"] = "1"
            argv.extend([
                "-c", probe,
                str(ROOT / "tools/hardware/deploy-audio-extra-recovery.py"),
                str(self.candidate_path), str(self.rollback_path), str(self.lineage_path),
                self.hashes["rollback_sha"], self.hashes["lineage_sha"],
                str(self.manifest_path), str(self.root),
            ])
            with self.subTest(mode=mode):
                result = subprocess.run(argv, text=True, capture_output=True, env=env, check=False)
                self.assertEqual(result.returncode, 23, result.stdout + result.stderr)
                self.assertIn("candidate build manifest image hash", result.stdout)


class ExtraDeploymentEntrypointTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="s22-extra-deploy-entry-")
        self.root = Path(self.temp.name)
        builds = self.root / "builds"
        (builds / "audio-extra-v2-20260922").mkdir(parents=True)
        (builds / "audio-early-20260922").mkdir()
        (self.root / "lineage/build-20260915").mkdir(parents=True)
        candidate = bytearray(b"R" * 2048)
        for start, end in ((16, 20), (576, 608), (1636, 1644)):
            candidate[start:end] = b"C" * (end - start)
        self.candidate = bytes(candidate)
        self.rollback = b"R" * 2048
        self.lineage = b"known good lineage fixture"
        (builds / "audio-extra-v2-20260922/recovery.img").write_bytes(self.candidate)
        (builds / "audio-early-20260922/recovery.img").write_bytes(self.rollback)
        (self.root / "lineage/build-20260915/recovery.img").write_bytes(self.lineage)
        manifest = {
            "image": "builds/audio-extra-v2-20260922/recovery.img",
            "image_sha256": hashlib.sha256(self.candidate).hexdigest(),
            "base_image_sha256": hashlib.sha256(self.rollback).hexdigest(),
            "extra_count": 20,
            "phone_access": False,
        }
        (builds / "audio-extra-v2-20260922/manifest.json").write_text(json.dumps(manifest))
        (self.root / "rootfs/main-driver-loop-20260921").mkdir(parents=True)
        ssh = self.root / "tools/s22-ssh"
        ssh.parent.mkdir(parents=True)
        ssh.write_text("#!/bin/sh\nexit 99\n")
        ssh.chmod(0o755)
        self.base = SimpleNamespace(
            SIZE=2048,
            BASE_SHA=BASE.BASE_SHA,
            NEW_SHA=BASE.NEW_SHA,
            render_remote=BASE.render_remote,
            ensure_new_receipt=BASE.ensure_new_receipt,
            validate_remote_receipt=BASE.validate_remote_receipt,
        )
        self.hashes = {
            "BEFORE": hashlib.sha256(self.rollback).hexdigest(),
            "AFTER": hashlib.sha256(self.candidate).hexdigest(),
            "LINEAGE_SHA": hashlib.sha256(self.lineage).hexdigest(),
        }

    def tearDown(self):
        self.temp.cleanup()

    def call_main(self, argv, subprocess_mock):
        with mock.patch.object(DEPLOY, "ROOT", self.root), \
             mock.patch.object(DEPLOY, "BEFORE", self.hashes["BEFORE"]), \
             mock.patch.object(DEPLOY, "AFTER", self.hashes["AFTER"]), \
             mock.patch.object(DEPLOY, "LINEAGE_SHA", self.hashes["LINEAGE_SHA"]), \
             mock.patch.object(DEPLOY.subprocess, "run", subprocess_mock):
            return DEPLOY.main(argv, base_module=self.base)

    def test_stage_entrypoint_validates_then_uses_mocked_transport(self):
        transport = mock.Mock(return_value=SimpleNamespace(
            returncode=0,
            stdout=json.dumps({
                "mode": "stage", "partition_written": False,
                "backup_sha256": self.hashes["BEFORE"],
                "candidate_sha256": self.hashes["AFTER"],
            }).encode(),
            stderr=b""))
        self.call_main(["--stage"], transport)
        transport.assert_called_once()
        args, kwargs = transport.call_args
        self.assertIn(str(self.root / "tools/s22-ssh"), args[0])
        command = args[0][1]
        self.assertIn(self.hashes["BEFORE"], command)
        self.assertIn(self.hashes["AFTER"], command)
        self.assertIn("/srv/s22/audio-extra-v2-20260922", command)
        self.assertEqual(kwargs["input"], self.candidate)
        receipt = self.root / "rootfs/main-driver-loop-20260921/audio-extra-recovery-stage.json"
        self.assertTrue(receipt.is_file())

    def test_bad_hash_or_stale_receipt_refuses_before_transport(self):
        transport = mock.Mock()
        with mock.patch.object(DEPLOY, "ROOT", self.root), \
             mock.patch.object(DEPLOY, "BEFORE", self.hashes["BEFORE"]), \
             mock.patch.object(DEPLOY, "AFTER", "0" * 64), \
             mock.patch.object(DEPLOY, "LINEAGE_SHA", self.hashes["LINEAGE_SHA"]), \
             mock.patch.object(DEPLOY.subprocess, "run", transport):
            with self.assertRaisesRegex(ValueError, "candidate build manifest image hash"):
                DEPLOY.main(["--stage"], base_module=self.base)
        transport.assert_not_called()

        receipt = self.root / "rootfs/main-driver-loop-20260921/audio-extra-recovery-flash.json"
        receipt.write_text("old")
        with self.assertRaisesRegex(ValueError, "existing deployment receipt"):
            self.call_main(["--flash"], transport)
        transport.assert_not_called()

    def test_invalid_remote_receipt_is_not_recorded_as_success(self):
        transport = mock.Mock(return_value=SimpleNamespace(
            returncode=0,
            stdout=b'{"mode":"stage","partition_written":false,"backup_sha256":"wrong","candidate_sha256":"wrong"}',
            stderr=b""))
        with self.assertRaisesRegex(RuntimeError, "receipt mismatch"):
            self.call_main(["--stage"], transport)
        receipt = self.root / "rootfs/main-driver-loop-20260921/audio-extra-recovery-stage.json"
        self.assertFalse(receipt.exists())
        transport.assert_called_once()


class EmbeddedTargetGateTests(unittest.TestCase):
    def _optimized_probe(self, mode):
        probe = r'''import importlib.util,sys
path=sys.argv[1]
spec=importlib.util.spec_from_file_location("target",path)
mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
ns={};exec(mod.REMOTE_GUARDS,ns)
try:
 ns["validate_recovery_target"]("native-guardian","5.10.260-g4e5c5ad7d950",278,
  "/dev/sda17",True,196608,True,2590,False,2590,196608)
except RuntimeError as error:
 print("REFUSED:"+str(error));sys.exit(23)
print("UNSAFE_ACCEPT");sys.exit(0)
'''
        env = os.environ.copy()
        argv = [sys.executable]
        if mode == "-O":
            argv.append("-O")
        elif mode == "PYTHONOPTIMIZE=1":
            env["PYTHONOPTIMIZE"] = "1"
        argv.extend(["-c", probe, str(ROOT / "tools/hardware/deploy-audio-recovery.py")])
        return subprocess.run(argv, text=True, capture_output=True, env=env, check=False)

    def test_wrong_partition_identity_is_refused_normally(self):
        result = self._optimized_probe("normal")
        self.assertEqual(result.returncode, 23, result.stdout + result.stderr)
        self.assertIn("alias does not resolve", result.stdout)

    def test_wrong_partition_identity_is_refused_with_python_o(self):
        result = self._optimized_probe("-O")
        self.assertEqual(result.returncode, 23, result.stdout + result.stderr)
        self.assertIn("alias does not resolve", result.stdout)

    def test_wrong_partition_identity_is_refused_with_pythonoptimize(self):
        result = self._optimized_probe("PYTHONOPTIMIZE=1")
        self.assertEqual(result.returncode, 23, result.stdout + result.stderr)
        self.assertIn("alias does not resolve", result.stdout)

    def test_remote_write_gate_contains_no_assertions(self):
        compile(BASE.REMOTE, "remote-deployer", "exec", optimize=1)
        self.assertNotIn("assert ", BASE.REMOTE)
        self.assertIn("flock(lockfd", BASE.REMOTE)
        self.assertIn("RECOVERY write; do not retry or reboot", BASE.REMOTE)

    def test_manifest_renderer_rejects_unsafe_values_and_resolves_all_markers(self):
        rendered = BASE.render_remote(
            base_sha="a" * 64, new_sha="b" * 64,
            staging_directory="/srv/s22/test-candidate",
            rollback_filename="rollback.img",
        )
        self.assertIn("base_sha='" + "a" * 64 + "'", rendered)
        self.assertIn("new_sha='" + "b" * 64 + "'", rendered)
        self.assertIn("/srv/s22/test-candidate", rendered)
        self.assertNotIn("__S22_", rendered)
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            BASE.render_remote(base_sha="bad", new_sha="b" * 64,
                               staging_directory="/srv/s22/test", rollback_filename="rollback.img")
        with self.assertRaisesRegex(ValueError, "safe child"):
            BASE.render_remote(base_sha="a" * 64, new_sha="b" * 64,
                               staging_directory="/srv/s22/../outside", rollback_filename="rollback.img")

    def _guards(self):
        namespace = {"hashlib": hashlib}
        exec(BASE.REMOTE_GUARDS, namespace)
        return namespace

    def test_wrong_device_capacity_and_mounted_partition_are_refused(self):
        validate = self._guards()["validate_recovery_target"]
        good = ("native-guardian", "5.10.260-g4e5c5ad7d950", 278,
                "/dev/sda16", True, 196608, True, 2590, False, 2590, 196608)
        for values, text in (
            (good[:5] + (196607,) + good[6:], "capacity"),
            (good[:7] + (2591,) + good[8:], "exact block device"),
            (good[:8] + (True,) + good[9:], "mounted"),
        ):
            with self.subTest(text=text), self.assertRaisesRegex(RuntimeError, text):
                validate(*values)

    def test_readback_hash_mismatch_is_refused(self):
        validate_digest = self._guards()["validate_digest"]
        with self.assertRaisesRegex(RuntimeError, "RECOVERY readback SHA-256 mismatch"):
            validate_digest(b"wrong bytes", hashlib.sha256(b"expected").hexdigest(), "RECOVERY readback")

    def test_short_read_is_refused(self):
        read_all = self._guards()["read_all"]
        responses = iter((b"partial", b""))
        with self.assertRaisesRegex(RuntimeError, "short RECOVERY read"):
            read_all(1, 10, lambda _fd, _count: next(responses), "RECOVERY")

    def test_short_writes_are_completed_and_interrupted_write_fails_closed(self):
        write_all_fd = self._guards()["write_all_fd"]
        output = bytearray(6)
        def one_byte_writer(_fd, chunk, offset):
            output[offset:offset+1] = chunk[:1]
            return min(1, len(chunk))
        flushed = []
        self.assertEqual(write_all_fd(1, b"abcdef", one_byte_writer, lambda _fd: flushed.append(True), "test write"), 6)
        self.assertEqual(output, b"abcdef")
        self.assertEqual(flushed, [True])
        def interrupted(_fd, _chunk, offset):
            if offset >= 2:
                raise OSError("injected interruption")
            return 2
        with self.assertRaisesRegex(RuntimeError, "failed after 2 bytes"):
            write_all_fd(1, b"abcdef", interrupted, lambda _fd: None, "test write")

    def test_zero_write_and_fsync_failure_are_refused(self):
        write_all_fd = self._guards()["write_all_fd"]
        with self.assertRaisesRegex(RuntimeError, "made no progress"):
            write_all_fd(1, b"x", lambda _fd, _chunk, _offset: 0, lambda _fd: None, "test write")
        with self.assertRaisesRegex(RuntimeError, "fsync failed"):
            write_all_fd(1, b"x", lambda _fd, chunk, _offset: len(chunk),
                         lambda _fd: (_ for _ in ()).throw(OSError("injected fsync")), "test write")

    def test_insufficient_staging_space_is_refused(self):
        validate = self._guards()["validate_free_space"]
        with self.assertRaisesRegex(RuntimeError, "insufficient free staging bytes"):
            validate(100, 100, 100)
        with self.assertRaisesRegex(RuntimeError, "insufficient free staging inodes"):
            validate(10_000_000, 2, 100)

    def test_stale_receipt_is_refused_including_symlink(self):
        with tempfile.TemporaryDirectory(prefix="s22-receipt-test-") as directory:
            target = Path(directory) / "receipt.json"
            target.write_text("old receipt")
            with self.assertRaisesRegex(ValueError, "existing deployment receipt"):
                BASE.ensure_new_receipt(target)
            target.unlink()
            target.symlink_to(Path(directory) / "missing")
            with self.assertRaisesRegex(ValueError, "existing deployment receipt"):
                BASE.ensure_new_receipt(target)


class AvbVerificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="s22-avb-verify-")
        self.root = Path(self.temp.name)
        # Match the recovery partition name embedded in the AVB footer.
        self.image = self.root / "recovery.img"
        self.avbtool = Path(os.environ.get("S22_AVBTOOL", BUILDER.DEFAULT_AVBTOOL))

    def tearDown(self):
        self.temp.cleanup()

    def test_failed_avb_verifier_is_fatal(self):
        self.image.write_bytes(b"synthetic candidate")
        self.avbtool = self.root / "avbtool.py"
        self.avbtool.write_text("# mocked executable path\n")
        failure = subprocess.CalledProcessError(1, ["avbtool"], stderr="footer mismatch")
        with mock.patch.object(BUILDER.subprocess, "run", side_effect=failure):
            with self.assertRaisesRegex(RuntimeError, "AVB verify_image failed.*footer mismatch"):
                BUILDER.verify_image(self.avbtool, self.image)

    def test_candidate_builder_requires_verify_before_publication(self):
        source = (ROOT / "tools/hardware/build-bt-hci-recovery.py").read_text()
        footer = source.index('"add_hash_footer"')
        verify = source.index("avb_verification = verify_image(args.avbtool, candidate)")
        publish = source.rindex("out.mkdir()")
        self.assertLess(footer, verify)
        self.assertLess(verify, publish)

    def test_real_avb_footer_verifies_and_corruption_fails(self):
        if not self.avbtool.is_file():
            self.skipTest(f"avbtool.py unavailable: {self.avbtool}")
        self.image.write_bytes(bytes((index % 251 for index in range(512 * 1024))))
        subprocess.run(
            [sys.executable, str(self.avbtool), "add_hash_footer", "--image", str(self.image),
             "--partition_size", str(2 * 1024 * 1024), "--partition_name", "recovery",
             "--algorithm", "NONE", "--rollback_index", "0", "--salt", "11" * 32],
            check=True, capture_output=True, text=True,
        )
        verified = BUILDER.verify_image(self.avbtool, self.image)
        self.assertTrue(verified)
        damaged = bytearray(self.image.read_bytes())
        damaged[123] ^= 1
        self.image.write_bytes(damaged)
        with self.assertRaisesRegex(RuntimeError, "AVB verify_image failed"):
            BUILDER.verify_image(self.avbtool, self.image)


if __name__ == "__main__":
    unittest.main(verbosity=2)
