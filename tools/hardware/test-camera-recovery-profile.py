#!/usr/bin/env python3
"""Host-only direction, artifact, guard, and rendered RECOVERY regressions."""
from __future__ import annotations

import hashlib
import contextlib
import io
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
        raise RuntimeError(f'cannot load {path}')
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


DEPLOY = load('s22_deploy_camera_recovery_test', ROOT / 'tools/hardware/deploy-camera-recovery.py')
SHARED, GUARD = DEPLOY._load_shared(ROOT)
REMOTE_FIXTURES = load('s22_recovery_remote_fixtures', ROOT / 'tools/hardware/test-recovery-deployment-hardening.py')


class CameraFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='s22-camera-profile-')
        self.root = Path(self.temp.name)
        self.project = self.root / 'project'
        self.artifacts = self.root / 'artifacts'
        (self.project / 'evidence').mkdir(parents=True)
        (self.artifacts / 'builds/camera-module-recovery-20260927').mkdir(parents=True)
        (self.artifacts / 'builds/bt-hci-loader-compatible-20260924-repro').mkdir(parents=True)
        self.camera = b'camera-image-fixture'
        self.baseline = b'hci-baseline-fixture'
        self.camera_sha = hashlib.sha256(self.camera).hexdigest()
        self.baseline_sha = hashlib.sha256(self.baseline).hexdigest()
        (self.artifacts / DEPLOY.CAMERA_IMAGE).write_bytes(self.camera)
        (self.artifacts / DEPLOY.BASELINE_IMAGE).write_bytes(self.baseline)

        self.manifest = {
            'candidate_image_sha256': self.camera_sha,
            'candidate_image_bytes': len(self.camera),
            'base_image_sha256': self.baseline_sha,
            'partition_size_bytes': len(self.camera),
            'phone_access': False,
            'deployed': False,
            'candidate_avb_footer_verified': True,
            'avb_algorithm': 'NONE',
            'replacement_module': {
                'path': 'lib/modules/fimc-is.ko',
                'build_id_sha1': 'camera-build-id',
                'versions_section_present': True,
            },
            'baseline_module': {'build_id_sha1': 'old-camera-build-id'},
        }
        manifest_bytes = json.dumps(self.manifest, sort_keys=True).encode()
        (self.artifacts / DEPLOY.CAMERA_MANIFEST).write_bytes(manifest_bytes)
        self.manifest_sha = hashlib.sha256(manifest_bytes).hexdigest()
        self.evidence = {
            'artifact_relative_path': DEPLOY.CAMERA_IMAGE,
            'artifact_sha256': self.camera_sha,
            'artifact_bytes': len(self.camera),
            'private_manifest_sha256': self.manifest_sha,
            'base_image_sha256': self.baseline_sha,
            'partition_writes': 0,
            'reboots': 0,
            'camera_open_or_capture_attempted': False,
            'bootability_verified': False,
            'independent_hardware_rescue_demonstrated': False,
            'phone_access_by_packager': False,
        }
        evidence_bytes = json.dumps(self.evidence, sort_keys=True).encode()
        (self.project / DEPLOY.PACKAGE_EVIDENCE).write_bytes(evidence_bytes)
        self.evidence_sha = hashlib.sha256(evidence_bytes).hexdigest()
        self.patches = mock.patch.multiple(
            DEPLOY,
            SIZE=len(self.camera),
            CAMERA_IMAGE_SHA256=self.camera_sha,
            CAMERA_MANIFEST_SHA256=self.manifest_sha,
            PACKAGE_EVIDENCE_SHA256=self.evidence_sha,
            BASELINE_IMAGE_SHA256=self.baseline_sha,
            CAMERA_MODULE_BUILD_ID='camera-build-id',
            BASELINE_MODULE_BUILD_ID='old-camera-build-id',
        )
        self.patches.start()
        self.addCleanup(self.patches.stop)
        self.addCleanup(self.temp.cleanup)

    def validate(self, profile='camera-forward'):
        return DEPLOY.validate_artifacts(
            profile, project_root=self.project, artifact_root=self.artifacts,
            shared=SHARED)

    def receipt_dir(self, identity, suffix='one'):
        return self.root / 'receipts' / suffix / identity

    def state_root(self):
        return self.root / 'trial-state'


class DirectionAndArtifactTests(CameraFixture):
    def test_forward_and_reverse_select_exact_opposite_images(self):
        forward, before, target = self.validate('camera-forward')
        self.assertEqual(forward['before_sha256'], self.baseline_sha)
        self.assertEqual(forward['new_sha256'], self.camera_sha)
        self.assertEqual(before, self.baseline)
        self.assertEqual(target, self.camera)
        self.assertEqual(forward['staging_directory'],
                         '/srv/s22/camera-recovery-forward-20260927')

        reverse, before, target = self.validate('camera-reverse')
        self.assertEqual(reverse['before_sha256'], self.camera_sha)
        self.assertEqual(reverse['new_sha256'], self.baseline_sha)
        self.assertEqual(before, self.camera)
        self.assertEqual(target, self.baseline)
        self.assertEqual(reverse['staging_directory'],
                         '/srv/s22/camera-recovery-reverse-20260927')

    def test_corrupt_camera_image_is_rejected(self):
        path = self.artifacts / DEPLOY.CAMERA_IMAGE
        path.write_bytes(b'wrong-camera')
        with self.assertRaisesRegex(ValueError, 'camera recovery image has incorrect size or SHA-256'):
            self.validate()

    def test_corrupt_baseline_image_is_rejected(self):
        path = self.artifacts / DEPLOY.BASELINE_IMAGE
        path.write_bytes(b'wrong-baseline')
        with self.assertRaisesRegex(ValueError, 'HCI rollback recovery image has incorrect size or SHA-256'):
            self.validate('camera-reverse')

    def test_private_manifest_hash_and_module_identity_are_pinned(self):
        path = self.artifacts / DEPLOY.CAMERA_MANIFEST
        path.write_bytes(path.read_bytes() + b' ')
        with self.assertRaisesRegex(ValueError, 'private camera manifest SHA-256'):
            self.validate()

        manifest = dict(self.manifest)
        manifest['replacement_module'] = dict(self.manifest['replacement_module'])
        manifest['replacement_module']['build_id_sha1'] = 'wrong-build-id'
        content = json.dumps(manifest, sort_keys=True).encode()
        path.write_bytes(content)
        new_manifest_sha = hashlib.sha256(content).hexdigest()
        self.evidence['private_manifest_sha256'] = new_manifest_sha
        evidence_bytes = json.dumps(self.evidence, sort_keys=True).encode()
        evidence_path = self.project / DEPLOY.PACKAGE_EVIDENCE
        evidence_path.write_bytes(evidence_bytes)
        with mock.patch.multiple(
                DEPLOY,
                CAMERA_MANIFEST_SHA256=new_manifest_sha,
                PACKAGE_EVIDENCE_SHA256=hashlib.sha256(evidence_bytes).hexdigest()):
            with self.assertRaisesRegex(ValueError, 'camera module record build_id_sha1'):
                self.validate()

    def test_public_evidence_is_pinned_and_must_describe_host_only_package(self):
        path = self.project / DEPLOY.PACKAGE_EVIDENCE
        changed = dict(self.evidence)
        changed['phone_access_by_packager'] = True
        path.write_text(json.dumps(changed, sort_keys=True))
        with self.assertRaisesRegex(ValueError, 'camera package evidence SHA-256'):
            self.validate()

        content = json.dumps(changed, sort_keys=True).encode()
        path.write_bytes(content)
        with mock.patch.object(DEPLOY, 'PACKAGE_EVIDENCE_SHA256', hashlib.sha256(content).hexdigest()):
            with self.assertRaisesRegex(ValueError, 'package evidence phone_access_by_packager'):
                self.validate()

    def test_direction_mode_requires_its_fresh_identity(self):
        identities = set()
        for profile, mode, identity in (
            ('camera-forward', 'stage', 'camera-recovery-20260927-forward-stage'),
            ('camera-forward', 'flash', 'camera-recovery-20260927-forward-flash'),
            ('camera-reverse', 'stage', 'camera-recovery-20260927-reverse-stage'),
            ('camera-reverse', 'flash', 'camera-recovery-20260927-reverse-flash'),
        ):
            self.assertEqual(DEPLOY.validate_cli_execution(profile, mode, identity), identity)
            identities.add(identity)
        self.assertEqual(len(identities), 4)
        self.assertNotIn(SHARED.HCI_TRIAL_ID, identities)
        with self.assertRaisesRegex(ValueError, 'one-use trial identity'):
            DEPLOY.validate_cli_execution('camera-forward', 'flash',
                                          'camera-recovery-20260927-forward-stage')

    def test_default_profile_is_a_host_plan_and_operation_needs_explicit_execute(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(DEPLOY.main_camera_profile(
                'camera-forward', project_root=self.project,
                artifact_root=self.artifacts, shared=SHARED), 0)
        plan = json.loads(output.getvalue())
        self.assertFalse(plan['execution'])
        self.assertTrue(plan['host_only_plan'])
        self.assertFalse(plan['independent_rescue_demonstrated'])
        self.assertFalse(plan['camera_unattended_acceptance_recorded'])
        with self.assertRaisesRegex(ValueError, 'require explicit --execute intent'):
            DEPLOY.main_camera_profile('camera-forward', 'stage', project_root=self.project,
                                       artifact_root=self.artifacts, shared=SHARED)

    def test_cli_cannot_override_global_guard_or_trusted_source_transport_roots(self):
        for option in ('--state-root', '--artifact-root', '--project-root'):
            with self.subTest(option=option), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as raised:
                    DEPLOY.main([option, str(self.root / 'alternate')])
                self.assertEqual(raised.exception.code, 2)


class GuardedOperationTests(CameraFixture):
    def run_fake(self, profile, mode, identity, fake_transport, *, receipt_dir=None):
        with contextlib.redirect_stdout(io.StringIO()):
            return DEPLOY.main_camera_profile(
                profile, mode, project_root=self.project, artifact_root=self.artifacts,
                receipt_dir=receipt_dir or self.receipt_dir(identity),
                state_root=self.state_root(), trial_identity=identity, execute=True,
                shared=SHARED, guard=GUARD, transport=fake_transport)

    @staticmethod
    def result(receipt):
        return SimpleNamespace(returncode=0,
                               stdout=(json.dumps(receipt) + '\n').encode(),
                               stderr=b'')

    def stage_receipt(self, profile):
        resolved = DEPLOY.resolve_profile(profile)
        return {'mode': 'stage', 'partition_written': False,
                'backup_sha256': resolved['before_sha256'],
                'candidate_sha256': resolved['new_sha256']}

    def flash_receipt(self, profile):
        resolved = DEPLOY.resolve_profile(profile)
        return {'mode': 'flash', 'partition_written': 'recovery', 'bytes': DEPLOY.SIZE,
                'before_sha256': resolved['before_sha256'],
                'readback_sha256': resolved['new_sha256'], 'reboot_performed': False}

    def test_forward_stage_persists_receipt_and_durable_consumed_marker(self):
        calls = []

        def transport(path, command, *, input_data, timeout, project_root):
            calls.append((command, input_data, timeout))
            profile = DEPLOY.resolve_profile('camera-forward')
            self.assertEqual(path, self.artifacts / 'tools/s22-ssh')
            self.assertEqual(project_root, self.artifacts,
                             'transport trust context must be the canonical artifact root')
            pending = json.loads((self.state_root() / f'{identity}.json').read_text())
            self.assertEqual(pending['status'], 'pending',
                             'pending guard marker must be durable before transport')
            self.assertIn(profile['before_sha256'], command)
            self.assertIn(profile['new_sha256'], command)
            self.assertIn('/dev/block/by-name/recovery', command)
            self.assertIn('PARTNAME=recovery', command)
            self.assertIn('os.makedev(259,0)', command)
            self.assertEqual(input_data, self.camera)
            return self.result(self.stage_receipt('camera-forward'))

        identity = DEPLOY.TRIAL_IDENTITIES[('camera-forward', 'stage')]
        self.assertEqual(self.run_fake('camera-forward', 'stage', identity, transport), 0)
        self.assertEqual(len(calls), 1, 'transport must not be retried')
        receipt = self.receipt_dir(identity) / 'camera-recovery-forward-stage.json'
        parsed = json.loads(receipt.read_text())
        self.assertEqual(parsed['written_image_sha256'], self.camera_sha)
        self.assertFalse(parsed['partition_written'])
        marker = json.loads((self.state_root() / f'{identity}.json').read_text())
        self.assertEqual(marker['status'], 'complete')
        self.assertEqual(marker['outcome'], 'success')

    def test_reverse_flash_is_exact_hci_rollback_and_has_distinct_identity(self):
        calls = []

        def transport(path, command, *, input_data, timeout, project_root):
            calls.append((command, input_data))
            profile = DEPLOY.resolve_profile('camera-reverse')
            self.assertIn(profile['before_sha256'], command)
            self.assertIn(profile['new_sha256'], command)
            self.assertEqual(input_data, b'')
            return self.result(self.flash_receipt('camera-reverse'))

        identity = DEPLOY.TRIAL_IDENTITIES[('camera-reverse', 'flash')]
        self.assertEqual(self.run_fake('camera-reverse', 'flash', identity, transport), 0)
        self.assertEqual(len(calls), 1)
        receipt = self.receipt_dir(identity) / 'camera-recovery-reverse-flash.json'
        parsed = json.loads(receipt.read_text())
        self.assertEqual(parsed['before_sha256'], self.camera_sha)
        self.assertEqual(parsed['readback_sha256'], self.baseline_sha)
        self.assertFalse(parsed['reboot_performed'])

    def test_invalid_remote_receipt_leaves_unknown_marker_and_no_local_receipt(self):
        calls = []

        def transport(path, command, *, input_data, timeout, project_root):
            calls.append(command)
            bad = self.stage_receipt('camera-forward')
            bad['partition_written'] = 'recovery'
            return self.result(bad)

        identity = DEPLOY.TRIAL_IDENTITIES[('camera-forward', 'stage')]
        with self.assertRaisesRegex(RuntimeError, 'invalid receipt'):
            self.run_fake('camera-forward', 'stage', identity, transport)
        self.assertEqual(len(calls), 1, 'invalid receipt must not trigger a retry')
        marker = json.loads((self.state_root() / f'{identity}.json').read_text())
        self.assertEqual(marker['status'], 'unknown')
        self.assertFalse((self.receipt_dir(identity) / 'camera-recovery-forward-stage.json').exists())

    def test_timeout_is_unknown_and_blocks_every_later_camera_operation(self):
        calls = []

        def timeout_transport(*args, **kwargs):
            calls.append((args, kwargs))
            raise subprocess.TimeoutExpired('fake-ssh', 1)

        identity = DEPLOY.TRIAL_IDENTITIES[('camera-forward', 'flash')]
        with self.assertRaisesRegex(RuntimeError, 'outcome may be unknown'):
            self.run_fake('camera-forward', 'flash', identity, timeout_transport)
        self.assertEqual(len(calls), 1)
        marker = json.loads((self.state_root() / f'{identity}.json').read_text())
        self.assertEqual(marker['status'], 'unknown')

        other = DEPLOY.TRIAL_IDENTITIES[('camera-reverse', 'stage')]
        with self.assertRaisesRegex(GUARD.TrialGuardError, 'unresolved trial marker'):
            self.run_fake('camera-reverse', 'stage', other,
                          lambda *args, **kwargs: self.fail('must not call transport'))

    def test_partial_remote_failure_is_unknown_and_never_retried(self):
        calls = []

        def partial_transport(*args, **kwargs):
            calls.append(1)
            return SimpleNamespace(returncode=23, stdout=b'',
                                   stderr=b'RECOVERY write failed after partial bytes')

        identity = DEPLOY.TRIAL_IDENTITIES[('camera-forward', 'flash')]
        with self.assertRaisesRegex(RuntimeError, 'outcome may be unknown'):
            self.run_fake('camera-forward', 'flash', identity, partial_transport)
        self.assertEqual(calls, [1], 'partial remote failure must not trigger a retry')
        marker = json.loads((self.state_root() / f'{identity}.json').read_text())
        self.assertEqual(marker['status'], 'unknown')

    def test_consumed_complete_identity_cannot_be_reused(self):
        identity = DEPLOY.TRIAL_IDENTITIES[('camera-forward', 'stage')]
        first_calls = []
        self.run_fake('camera-forward', 'stage', identity,
                      lambda *args, **kwargs: (first_calls.append(1) or self.result(self.stage_receipt('camera-forward'))))
        second_calls = []
        with self.assertRaisesRegex(GUARD.TrialGuardError, 'already has a durable marker'):
            self.run_fake('camera-forward', 'stage', identity,
                          lambda *args, **kwargs: (second_calls.append(1) or self.result(self.stage_receipt('camera-forward'))),
                          receipt_dir=self.receipt_dir(identity, suffix='second'))
        self.assertEqual((len(first_calls), len(second_calls)), (1, 0))

    def test_wrong_artifact_fails_before_guard_or_transport(self):
        (self.artifacts / DEPLOY.CAMERA_IMAGE).write_bytes(b'bad')
        identity = DEPLOY.TRIAL_IDENTITIES[('camera-forward', 'stage')]
        calls = []
        with self.assertRaisesRegex(RuntimeError, 'no device operation attempted'):
            self.run_fake('camera-forward', 'stage', identity,
                          lambda *args, **kwargs: calls.append(1))
        self.assertEqual(calls, [])
        self.assertFalse(self.state_root().exists())


class RenderedRemoteProfileTests(unittest.TestCase):
    def test_exact_shared_remote_body_stages_and_flashes_both_directions(self):
        for byte_base, byte_target in ((0x42, 0x43), (0x43, 0x42)):
            sandbox_class = type('DirectionSandbox', (REMOTE_FIXTURES.RenderedRemoteSandbox,), {
                'BASE_BYTE': byte_base, 'CANDIDATE_BYTE': byte_target,
            })
            sandbox = sandbox_class()
            try:
                sandbox.stage_dir = sandbox.root / 'srv/s22/camera-recovery-test'
                profile = SHARED.render_remote(
                    base_sha=sandbox.base_sha, new_sha=sandbox.candidate_sha,
                    staging_directory='/srv/s22/camera-recovery-test',
                    rollback_filename='camera-recovery-rollback.img')
                sandbox.rendered = profile
                before = sandbox.device_write_bytes
                stage = sandbox.execute('stage', candidate_input=True)
                self.assertIsNone(stage.error, str(stage.error))
                self.assertEqual(json.loads(stage.stdout), {
                    'mode': 'stage', 'partition_written': False,
                    'backup_sha256': sandbox.base_sha,
                    'candidate_sha256': sandbox.candidate_sha,
                })
                self.assertEqual(sandbox.device_write_bytes, before)
                self.assertEqual(
                    sandbox.hash_file(sandbox.stage_dir / 'camera-recovery-rollback.img'),
                    sandbox.base_sha)
                self.assertEqual(sandbox.hash_file(sandbox.stage_dir / 'recovery.img'), sandbox.candidate_sha)
                flash = sandbox.execute('flash')
                self.assertIsNone(flash.error, str(flash.error))
                receipt = json.loads(flash.stdout)
                self.assertEqual(receipt['before_sha256'], sandbox.base_sha)
                self.assertEqual(receipt['readback_sha256'], sandbox.candidate_sha)
                self.assertFalse(receipt['reboot_performed'])
                self.assertEqual(sandbox.device_write_bytes, sandbox.SIZE)
                self.assertEqual(sandbox.hash_file(sandbox.partition), sandbox.candidate_sha)
            finally:
                sandbox.close()


if __name__ == '__main__':
    unittest.main(verbosity=2)
