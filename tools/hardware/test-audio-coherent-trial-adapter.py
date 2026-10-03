#!/usr/bin/env python3
"""Host-only safety tests for the inactive audio RECOVERY trial adapter."""
from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
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


ADAPTER = load('s22_audio_coherent_trial_adapter_test',
               ROOT / 'tools/hardware/deploy-audio-coherent-recovery.py')
OBSERVER = load('s22_audio_coherent_reboot_once_test',
                ROOT / 'tools/hardware/audio-coherent-recovery-reboot-once.py')
REMOTE_FIXTURES = load('s22_audio_coherent_remote_fixtures',
                       ROOT / 'tools/hardware/test-recovery-deployment-hardening.py')


class FakeClock:
    def __init__(self, now=0.0):
        self.now = float(now)
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def identity(profile, recovery_sha, boot_id='11111111-1111-4111-8111-111111111111',
             module_role='baseline'):
    modules = {
        name: {'loaded': True, 'gnu_build_id': roles[module_role]}
        for name, roles in ADAPTER.AUDIO_MODULES.items()
    }
    return {
        'schema': 's22-audio-coherent-recovery-identity/v1',
        'boot_id': boot_id,
        'pid1': 'native-guardian',
        'native_ready': True,
        'kernel_gnu_build_id': ADAPTER.RUNNING_KERNEL_BUILD_ID,
        'kernel_release': '5.10.260-g4e5c5ad7d950',
        'camera_module_loaded': True,
        'camera_module_gnu_build_id': '59e54c032c545fff3ba52156f226fb6d69aadf64',
        'recovery_sha256': recovery_sha,
        'recovery_target': {
            'alias_target': '/dev/sda16', 'rdev': '259:0',
            'capacity_bytes': ADAPTER.PROFILE.PARTITION_SIZE,
            'sysfs_sectors': ADAPTER.PROFILE.PARTITION_SIZE // 512,
            'partition_name': 'recovery', 'mounted': False,
        },
        'audio_modules': modules,
    }


def readiness(boot_id='11111111-1111-4111-8111-111111111111'):
    return {
        'boot_id': boot_id,
        'uptime_seconds': 240.0,
        'pid1': 'native-guardian',
        'native_ready': True,
        'persistent_ready': {'ready': True, 'mount_ready': True},
        'health': {'status': 'ok'},
        'slots': {'count': 1, 'processing': [False]},
        'assistant_idle': True,
        'readiness': {
            'kernel_remote_control': True, 'model_api_health': True,
            'model_idle': True, 'desktop_environment': True,
            'desktop_pi_status': 'absent', 'browser_terminal_status': 'absent',
            'dedicated_pi_session_status': 'absent',
        },
        'serious_fault': False,
        'kernel_log_classification': {
            'input_available': True, 'assessment': 'no_indicators',
            'fatal_indicators': [], 'hung_task_warning_count': 0,
            'call_trace_count': 0, 'liveness_unresolved': False,
            'capture_complete': True, 'coverage_complete': True,
            'full_boot_log_coverage': False,
            'bytes': 37, 'sha256': sha(b'complete current kernel ring fixture'),
        },
        'network_state': {'ready': True, 'interfaces': [
            {'name': 'wlan0', 'operstate': 'up', 'carrier': '1'}]},
        'power_state': {
            'battery_status': 'Charging', 'battery_capacity_percent': 80,
            'battery_temperature_celsius': 30.0,
            'thermal_all_readable': True, 'thermal_zone_count': 1,
            'thermal_max_temperature_celsius': 50.0,
        },
        'device_tree_model': 'Samsung SM-S901B r0s',
        'device_tree_compatible': ['samsung,s5e9925'],
        'boot_model': 'SM-S901B', 'boot_hardware': 's5e9925',
        'bootloader_model_match': True,
        'gnu_build_id': ADAPTER.RUNNING_KERNEL_BUILD_ID,
        'kernel_release': '5.10.260-g4e5c5ad7d950',
        'boot_reset_first_record': '[  1] / R / INFORM3(12345674) > RECOVERY > native',
        'tmux_server_running': False,
    }


def run_rendered_readiness_collector(dmesg_text: str, *, dmesg_returncode: int = 0) -> dict:
    """Execute the actual rendered collector locally with only dmesg stubbed."""
    script = OBSERVER._readiness_script()
    output = io.StringIO()

    def fake_run(argv, **kwargs):
        if argv == ['dmesg']:
            return subprocess.CompletedProcess(argv, dmesg_returncode,
                                                dmesg_text if dmesg_returncode == 0 else '', '')
        # The rendered snapshot may ask whether the optional Pi tmux socket is
        # live. No process is launched during this source-only test.
        return subprocess.CompletedProcess(argv, 1, '', 'test-only subprocess stub')

    with mock.patch.object(subprocess, 'run', side_effect=fake_run), \
            mock.patch('urllib.request.build_opener', side_effect=OSError('local HTTP disabled')):
        with contextlib.redirect_stdout(output):
            exec(compile(script, 'rendered-audio-readiness-test', 'exec'),
                 {'__name__': '__main__'})
    return json.loads(output.getvalue())


def readiness_from_rendered_collector(boot_id: str, dmesg_text: str,
                                      *, dmesg_returncode: int = 0) -> dict:
    collected = run_rendered_readiness_collector(
        dmesg_text, dmesg_returncode=dmesg_returncode)
    state = readiness(boot_id)
    state['serious_fault'] = collected['serious_fault']
    state['kernel_log_classification'] = collected['kernel_log_classification']
    return state


class KernelDiagnosticCoverageTests(unittest.TestCase):
    def test_python_optimization_flag_matches_requested_mode(self):
        expected = os.environ.get('AUDIO_EXPECT_PYTHONOPTIMIZE')
        if expected is not None:
            self.assertIn(expected, ('0', '1', '2'))
            self.assertEqual(sys.flags.optimize, int(expected))

    def test_actual_rendered_empty_collector_output_is_rejected_without_fatal_text(self):
        script = OBSERVER._readiness_script()
        self.assertEqual(script.count(
            "state['kernel_log_classification']['input_available']="), 1)
        self.assertIn('log_classification.input_available', script)
        state = readiness_from_rendered_collector(
            '55555555-5555-4555-8555-555555555555', '')
        logs = state['kernel_log_classification']
        self.assertIs(state['serious_fault'], False)
        self.assertEqual(logs['fatal_indicators'], [])
        self.assertIs(logs['input_available'], True)
        self.assertEqual(logs['assessment'], 'incomplete')
        self.assertIs(logs['capture_complete'], True)
        self.assertIs(logs['coverage_complete'], False)
        with self.assertRaisesRegex(ValueError, 'input/capture/coverage'):
            OBSERVER.validate_readiness_snapshot(state, post_reboot=True)

    def test_actual_rendered_unavailable_collector_input_is_not_ready(self):
        state = readiness_from_rendered_collector(
            '55555555-5555-4555-8555-555555555555', '', dmesg_returncode=1)
        logs = state['kernel_log_classification']
        self.assertIsNone(state['serious_fault'])
        self.assertIs(logs['input_available'], False)
        self.assertEqual(logs['assessment'], 'incomplete')
        self.assertIs(logs['capture_complete'], False)
        self.assertIs(logs['coverage_complete'], False)
        with self.assertRaisesRegex(ValueError, 'serious-fault|input/capture/coverage'):
            OBSERVER.validate_readiness_snapshot(state, post_reboot=True)

    def test_complete_current_ring_is_not_promoted_to_full_boot_or_progress(self):
        state = readiness_from_rendered_collector(
            '55555555-5555-4555-8555-555555555555',
            '[    0.000000] Linux boot diagnostic sample\n')
        logs = state['kernel_log_classification']
        self.assertIs(logs['input_available'], True)
        self.assertEqual(logs['assessment'], 'no_indicators')
        self.assertIs(logs['capture_complete'], True)
        self.assertIs(logs['coverage_complete'], True)
        self.assertIs(logs['full_boot_log_coverage'], False)
        self.assertNotIn('tz_progress_measured', logs)
        accepted = OBSERVER.validate_readiness_snapshot(state, post_reboot=True)
        redacted = OBSERVER._redacted_kernel_diagnostics(
            accepted['kernel_log_classification'])
        self.assertIs(redacted['available'], True)
        self.assertEqual(redacted['assessment'], 'no_indicators')
        self.assertIs(redacted['full_boot_log_coverage'], False)

    def test_full_boot_coverage_is_retained_as_an_exact_boolean_either_way(self):
        for value in (False, True):
            with self.subTest(full_boot_log_coverage=value):
                state = readiness()
                state['kernel_log_classification']['full_boot_log_coverage'] = value
                accepted = OBSERVER.validate_readiness_snapshot(state, post_reboot=True)
                self.assertIs(accepted['kernel_log_classification'][
                    'full_boot_log_coverage'], value)
                redacted = OBSERVER._redacted_kernel_diagnostics(
                    accepted['kernel_log_classification'])
                self.assertIs(redacted['full_boot_log_coverage'], value)

    def test_missing_null_numeric_and_incomplete_coverage_fields_fail_closed(self):
        base = readiness()
        variants = []
        for field in ('input_available', 'capture_complete', 'coverage_complete'):
            variants.append((field, 'missing', None))
            for value in (None, False, 0, 0.0):
                variants.append((field, repr(value), value))
        for value in ('missing', None, 'incomplete', 'fatal', 'hung_task_warning', 0, 0.0):
            variants.append(('assessment', repr(value), value))
        for value in ('missing', None, 0, 0.0, 1):
            variants.append(('full_boot_log_coverage', repr(value), value))
        for field, label, value in variants:
            with self.subTest(field=field, value=label):
                state = json.loads(json.dumps(base))
                if value == 'missing':
                    state['kernel_log_classification'].pop(field)
                else:
                    state['kernel_log_classification'][field] = value
                self.assertEqual(state['kernel_log_classification']['fatal_indicators'], [])
                self.assertIs(state['serious_fault'], False)
                with self.assertRaises(ValueError):
                    OBSERVER.validate_readiness_snapshot(state, post_reboot=True)

    def test_trace_only_assessment_is_not_a_readiness_allowlist(self):
        state = readiness_from_rendered_collector(
            '55555555-5555-4555-8555-555555555555',
            '[    0.000000] Call Trace:\n')
        logs = state['kernel_log_classification']
        self.assertEqual(logs['assessment'], 'trace_only')
        self.assertEqual(logs['call_trace_count'], 1)
        self.assertEqual(logs['fatal_indicators'], [])
        with self.assertRaisesRegex(ValueError, 'no-indicators'):
            OBSERVER.validate_readiness_snapshot(state, post_reboot=True)


class InactiveByDefaultTests(unittest.TestCase):
    def test_empty_authorization_allowlist_and_false_flag_are_literal(self):
        self.assertIs(ADAPTER.CURRENT_AUDIO_EXECUTION_AUTHORIZED, False)
        self.assertEqual(ADAPTER.EXECUTION_AUTHORIZED_TRIALS, frozenset())

    def test_execute_intent_and_ack_text_do_not_authorize_stage_flash_or_reboot(self):
        with tempfile.TemporaryDirectory(prefix='audio-no-auth-') as directory:
            root = Path(directory)
            receipts = root / 'receipts'
            guard_parent = root / 'guard'
            guard_parent.mkdir(mode=0o700)
            state_root = guard_parent / 'state'
            for args in (
                ['--profile', 'audio-forward', '--stage', '--execute',
                 '--trial-identity', ADAPTER.TRIAL_IDENTITY],
                ['--profile', 'audio-reverse', '--flash', '--execute',
                 '--trial-identity', ADAPTER.TRIAL_IDENTITY],
            ):
                with self.subTest(args=args), self.assertRaises(PermissionError):
                    ADAPTER.main(args)
            for args in (
                ['--profile', 'audio-forward', '--request-reboot', '--execute',
                 '--trial-identity', ADAPTER.TRIAL_IDENTITY],
                ['--profile', 'audio-forward', '--observe-once', '--execute',
                 '--trial-identity', ADAPTER.TRIAL_IDENTITY],
                ['--profile', 'audio-forward', '--observe-stability', '--execute',
                 '--trial-identity', ADAPTER.TRIAL_IDENTITY],
            ):
                with self.subTest(args=args), self.assertRaises(PermissionError):
                    OBSERVER.main(args)
            self.assertFalse(receipts.exists())
            self.assertFalse(state_root.exists())

    def test_source_pin_check_matches_reviewed_helpers(self):
        actual = ADAPTER.verify_source_pins(ROOT)
        self.assertEqual(actual, ADAPTER.PINNED_SOURCES)
        self.assertIn('tools/s22-ssh', actual)
        observer_actual = OBSERVER.verify_source_pins(ROOT)
        self.assertEqual(
            observer_actual['tools/hardware/deploy-audio-coherent-recovery.py'],
            sha((ROOT / 'tools/hardware/deploy-audio-coherent-recovery.py').read_bytes()))

    def test_synthetic_test_allowlist_still_requires_exact_trial_identity(self):
        with mock.patch.object(ADAPTER, 'CURRENT_AUDIO_EXECUTION_AUTHORIZED', True), \
             mock.patch.object(ADAPTER, 'EXECUTION_AUTHORIZED_TRIALS',
                               frozenset({'synthetic-audio-test-trial'})), \
             mock.patch.object(ADAPTER.PROFILE, 'TRIAL_IDENTITY',
                               'synthetic-audio-test-trial'), \
             mock.patch.object(ADAPTER, 'TRIAL_IDENTITY', 'synthetic-audio-test-trial'):
            ADAPTER.require_execution_authorized('synthetic-audio-test-trial')
            with self.assertRaises(PermissionError):
                ADAPTER.require_execution_authorized('other-audio-trial')

    def test_identity_script_extends_only_the_pinned_readonly_capture(self):
        script = ADAPTER.identity_snapshot_script()
        self.assertIn("state['schema']='s22-audio-coherent-recovery-identity/v1'", script)
        self.assertIn("/sys/module/'+name+'/notes/.note.gnu.build-id", script)
        self.assertNotIn("os.open('/dev/sda16',os.O_RDWR", script)
        self.assertEqual(script.count("state['audio_modules']=audio_modules"), 1)


class AudioOperationFixture(unittest.TestCase):
    SYNTHETIC = 'synthetic-audio-test-trial'
    SIZE = 4096

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='audio-coherent-adapter-')
        self.root = Path(self.temp.name)
        self.receipt_dir = self.root / 'receipts' / self.SYNTHETIC
        guard_parent = self.root / 'guard'
        guard_parent.mkdir(mode=0o700)
        self.state_root = guard_parent / 'state'
        self.profile_patches = contextlib.ExitStack()

        forward = dict(ADAPTER.PROFILE.PROFILES['audio-forward'])
        reverse = dict(ADAPTER.PROFILE.PROFILES['audio-reverse'])
        self.base = b'B' * self.SIZE
        self.candidate = b'C' * self.SIZE
        self.base_sha = sha(self.base)
        self.candidate_sha = sha(self.candidate)
        forward.update({
            'before_sha256': self.base_sha, 'target_sha256': self.candidate_sha,
            'staging_directory': '/srv/s22/rendered-integration',
            'rollback_filename': 'rollback.img',
        })
        reverse.update({
            'before_sha256': self.candidate_sha, 'target_sha256': self.base_sha,
            'staging_directory': '/srv/s22/rendered-integration',
            'rollback_filename': 'rollback.img',
        })
        self.profile_patches.enter_context(mock.patch.object(
            ADAPTER.PROFILE, 'PARTITION_SIZE', self.SIZE))
        self.profile_patches.enter_context(mock.patch.object(
            ADAPTER.PROFILE, 'TRIAL_IDENTITY', self.SYNTHETIC))
        self.profile_patches.enter_context(mock.patch.object(
            ADAPTER.PROFILE, 'PROFILES', {'audio-forward': forward, 'audio-reverse': reverse}))
        self.profile_patches.enter_context(mock.patch.object(
            ADAPTER, 'TRIAL_IDENTITY', self.SYNTHETIC))
        self.profile_patches.enter_context(mock.patch.object(
            ADAPTER, 'CURRENT_AUDIO_EXECUTION_AUTHORIZED', True))
        self.profile_patches.enter_context(mock.patch.object(
            ADAPTER, 'EXECUTION_AUTHORIZED_TRIALS', frozenset({self.SYNTHETIC})))
        for name, value in (
            ('ADAPTER', ADAPTER), ('SHARED', ADAPTER.SHARED),
            ('GUARD', ADAPTER.GUARD), ('PROFILE', ADAPTER.PROFILE),
            ('AUDIO', ADAPTER.AUDIO_OBSERVER), ('CAMERA', ADAPTER.CAMERA_OBSERVER),
        ):
            self.profile_patches.enter_context(mock.patch.object(OBSERVER, name, value))
        adapter_digest = sha((ROOT / 'tools/hardware/deploy-audio-coherent-recovery.py').read_bytes())
        self.profile_patches.enter_context(mock.patch.object(
            OBSERVER, 'DEPLOY_ADAPTER_SHA256', adapter_digest))

        profile_deploy = ADAPTER.PROFILE.DEPLOY
        small_template = profile_deploy.REMOTE_TEMPLATE.replace(
            'size=100663296', f'size={self.SIZE}').replace('196608', str(self.SIZE // 512))
        self.profile_patches.enter_context(mock.patch.object(
            profile_deploy, 'REMOTE_TEMPLATE', small_template))
        self.profile_patches.enter_context(mock.patch.object(profile_deploy, 'SIZE', self.SIZE))
        self.profile_patches.enter_context(mock.patch.object(
            ADAPTER.PROFILE, 'PARTITION_SIZE', self.SIZE))

        fixture_base = REMOTE_FIXTURES.BASE
        fixture_template = fixture_base.REMOTE_TEMPLATE.replace(
            'size=100663296', f'size={self.SIZE}').replace('196608', str(self.SIZE // 512))
        self.profile_patches.enter_context(mock.patch.object(
            fixture_base, 'REMOTE_TEMPLATE', fixture_template))
        self.profile_patches.enter_context(mock.patch.object(fixture_base, 'SIZE', self.SIZE))
        self.profile_patches.enter_context(mock.patch.object(
            REMOTE_FIXTURES.RenderedRemoteSandbox, 'SIZE', self.SIZE))
        self.profile_patches.enter_context(mock.patch.object(
            REMOTE_FIXTURES.RenderedRemoteSandbox, 'CHUNK_SIZE', 1024))

    def tearDown(self):
        self.profile_patches.close()
        self.temp.cleanup()

    def make_sandbox(self, profile_name):
        profile = ADAPTER.PROFILE.resolve_profile(profile_name)
        sandbox = REMOTE_FIXTURES.RenderedRemoteSandbox()
        before_data = self.base if profile_name == 'audio-forward' else self.candidate
        target_data = self.candidate if profile_name == 'audio-forward' else self.base
        before_byte = before_data[0]
        target_byte = target_data[0]
        sandbox.write_partition(before_byte)
        sandbox.BASE_BYTE = before_byte
        sandbox.CANDIDATE_BYTE = target_byte
        sandbox.base_sha = sha(before_data)
        sandbox.candidate_sha = sha(target_data)
        sandbox.stage_dir = sandbox.root / 'srv/s22/rendered-integration'
        sandbox.rendered = ADAPTER.PROFILE.render_remote(profile_name)
        (sandbox.root / 'sys/class/block/sda16/size').write_text(f'{self.SIZE // 512}\n')
        boot_path = sandbox.root / 'proc/sys/kernel/random/boot_id'
        boot_path.parent.mkdir(parents=True, exist_ok=True)
        boot_path.write_text('11111111-1111-4111-8111-111111111111\n')
        return sandbox, target_data

    def transport_for(self, sandbox, target_data, calls):
        def run(_ssh_path, command, *, input_data, timeout, project_root):
            mode = 'stage' if command.endswith(' stage') else 'flash'
            calls.append((mode, command, len(input_data), timeout))
            if mode == 'stage':
                if input_data != target_data:
                    return subprocess.CompletedProcess(command, 1, b'', b'wrong staged bytes')
            elif input_data != b'':
                return subprocess.CompletedProcess(command, 1, b'', b'unexpected flash stdin')
            result = sandbox.execute(mode, candidate_input=(mode == 'stage'))
            if result.error is not None:
                return subprocess.CompletedProcess(command, 1, result.stdout.encode(),
                                                   str(result.error).encode())
            return subprocess.CompletedProcess(command, 0, result.stdout.encode(), b'')
        return run

    def make_identity_reader(self, profile_name, boot_id='11111111-1111-4111-8111-111111111111',
                             change_boot_after=False):
        profile = ADAPTER.PROFILE.resolve_profile(profile_name)
        calls = []
        role = 'candidate' if profile['before_role'] == 'audio_candidate' else 'baseline'
        def read(expected_sha):
            calls.append(expected_sha)
            return identity(profile, expected_sha,
                            boot_id=('22222222-2222-4222-8222-222222222222'
                                     if change_boot_after and len(calls) > 1 else boot_id),
                            module_role=role)
        return read, calls

    def stage(self, profile_name, sandbox, target_data, calls):
        profile = ADAPTER.PROFILE.resolve_profile(profile_name)
        with mock.patch.object(ADAPTER, '_profile_image', return_value=(profile, target_data)):
            return ADAPTER._run_profile_operation(
                profile_name, 'stage', project_root=ROOT, receipt_dir=self.receipt_dir,
                state_root=self.state_root,
                transport=self.transport_for(sandbox, target_data, calls))

    def flash(self, profile_name, sandbox, target_data, calls, identity_reader=None):
        profile = ADAPTER.PROFILE.resolve_profile(profile_name)
        with mock.patch.object(ADAPTER, '_profile_image', return_value=(profile, target_data)):
            return ADAPTER._run_profile_operation(
                profile_name, 'flash', project_root=ROOT, receipt_dir=self.receipt_dir,
                state_root=self.state_root,
                transport=self.transport_for(sandbox, target_data, calls),
                identity_reader=identity_reader or self.make_identity_reader(profile_name)[0])

    def run_operation(self, profile_name, mode, target_data, *, transport, identity_reader=None):
        profile = ADAPTER.PROFILE.resolve_profile(profile_name)
        with mock.patch.object(ADAPTER, '_profile_image', return_value=(profile, target_data)):
            return ADAPTER._run_profile_operation(
                profile_name, mode, project_root=ROOT, receipt_dir=self.receipt_dir,
                state_root=self.state_root, transport=transport,
                identity_reader=identity_reader)

    def test_forward_and_reverse_use_real_rendered_stage_flash_with_zero_stage_writes(self):
        for profile_name in ('audio-forward', 'audio-reverse'):
            with self.subTest(profile=profile_name):
                sandbox, target = self.make_sandbox(profile_name)
                calls = []
                try:
                    before_writes = sandbox.device_write_bytes
                    stage_receipt = self.stage(profile_name, sandbox, target, calls)
                    self.assertEqual(stage_receipt['mode'], 'stage')
                    self.assertEqual(sandbox.device_write_bytes, before_writes)
                    flash_receipt = self.flash(profile_name, sandbox, target, calls)
                    self.assertEqual(flash_receipt['readback_sha256'], sha(target))
                    self.assertEqual(sandbox.device_write_bytes - before_writes, self.SIZE)
                    self.assertFalse(flash_receipt['reboot_performed'])
                    self.assertEqual([item[0] for item in calls], ['stage', 'flash'])
                    self.assertEqual(calls[0][2], self.SIZE)
                    self.assertEqual(calls[1][2], 0)
                    ADAPTER.validate_flash_receipt(
                        profile_name, flash_receipt, state_root=self.state_root,
                        receipt_path=self.receipt_dir / f'{profile_name}-flash.json')
                finally:
                    sandbox.close()

    def test_flash_boot_bound_guard_rejects_identity_change_after_readback(self):
        sandbox, target = self.make_sandbox('audio-forward')
        calls = []
        try:
            self.stage('audio-forward', sandbox, target, calls)
            reader, samples = self.make_identity_reader('audio-forward', change_boot_after=True)
            with self.assertRaisesRegex(ValueError, 'crossed a boot'):
                self.flash('audio-forward', sandbox, target, calls, identity_reader=reader)
            self.assertEqual(len([call for call in calls if call[0] == 'flash']), 1)
            marker = ADAPTER.GUARD._read_marker(
                self.state_root / f'{ADAPTER.operation_id("audio-forward", "flash")}.json')
            self.assertEqual(marker['status'], 'unknown')
            self.assertTrue((self.receipt_dir / 'audio-forward-flash-remote.json').exists())
            self.assertEqual(samples, [self.base_sha, self.candidate_sha])
        finally:
            sandbox.close()

    def test_flash_prewrite_requires_profile_source_module_ids(self):
        sandbox, target = self.make_sandbox('audio-forward')
        calls = []
        try:
            self.stage('audio-forward', sandbox, target, calls)
            wrong_modules = lambda expected: identity(
                ADAPTER.PROFILE.resolve_profile('audio-forward'), expected,
                module_role='candidate')
            before_writes = sandbox.device_write_bytes
            with self.assertRaisesRegex(ValueError, 'loaded module GNU build ID mismatch'):
                self.flash('audio-forward', sandbox, target, calls,
                           identity_reader=wrong_modules)
            self.assertEqual(sandbox.device_write_bytes, before_writes)
            self.assertEqual([item[0] for item in calls], ['stage'])
            marker = ADAPTER.GUARD._read_marker(
                self.state_root / f'{ADAPTER.operation_id("audio-forward", "flash")}.json')
            self.assertEqual(marker['status'], 'unknown')
        finally:
            sandbox.close()

    def test_wrong_current_partition_baseline_prevents_flash_and_keeps_unknown_marker(self):
        sandbox, target = self.make_sandbox('audio-forward')
        calls = []
        try:
            self.stage('audio-forward', sandbox, target, calls)
            sandbox.write_partition(0x58)
            before_writes = sandbox.device_write_bytes
            with self.assertRaisesRegex(RuntimeError, 'outcome may be unknown'):
                self.flash('audio-forward', sandbox, target, calls)
            self.assertEqual(sandbox.device_write_bytes, before_writes)
            self.assertFalse((self.receipt_dir / 'audio-forward-flash.json').exists())
            marker = ADAPTER.GUARD._read_marker(
                self.state_root / f'{ADAPTER.operation_id("audio-forward", "flash")}.json')
            self.assertEqual(marker['status'], 'unknown')
            self.assertEqual([item[0] for item in calls], ['stage', 'flash'])
        finally:
            sandbox.close()

    def test_partial_readback_write_has_no_success_receipt_or_retry(self):
        sandbox, target = self.make_sandbox('audio-forward')
        calls = []
        try:
            self.stage('audio-forward', sandbox, target, calls)
            sandbox.fail_partition_write_after = 512
            before_writes = sandbox.device_write_bytes
            with self.assertRaisesRegex(RuntimeError, 'outcome may be unknown'):
                self.flash('audio-forward', sandbox, target, calls)
            self.assertEqual(sandbox.device_write_bytes - before_writes, 512)
            self.assertFalse((self.receipt_dir / 'audio-forward-flash.json').exists())
            marker = ADAPTER.GUARD._read_marker(
                self.state_root / f'{ADAPTER.operation_id("audio-forward", "flash")}.json')
            self.assertEqual(marker['status'], 'unknown')
            with self.assertRaises((RuntimeError, ValueError)):
                self.flash('audio-forward', sandbox, target, calls)
            self.assertEqual([item[0] for item in calls].count('flash'), 1)
        finally:
            sandbox.close()

    def test_tampered_stage_receipt_wrong_profile_or_wrong_trial_is_rejected_before_flash(self):
        sandbox, target = self.make_sandbox('audio-forward')
        calls = []
        try:
            self.stage('audio-forward', sandbox, target, calls)
            path = self.receipt_dir / 'audio-forward-stage.json'
            receipt = json.loads(path.read_text())
            receipt['profile'] = 'audio-reverse'
            path.write_text(json.dumps(receipt))
            with self.assertRaisesRegex(ValueError, 'profile identity mismatch'):
                self.flash('audio-forward', sandbox, target, calls)
            self.assertEqual([item[0] for item in calls], ['stage'])
        finally:
            sandbox.close()

    def test_flash_receipt_rejects_bool_numeric_and_extra_key_confusion(self):
        sandbox, target = self.make_sandbox('audio-forward')
        calls = []
        try:
            self.stage('audio-forward', sandbox, target, calls)
            receipt = self.flash('audio-forward', sandbox, target, calls)
            invalid = dict(receipt, bytes=float(receipt['bytes']))
            with self.assertRaisesRegex(ValueError, 'bytes'):
                ADAPTER.validate_flash_receipt(
                    'audio-forward', invalid, state_root=self.state_root,
                    receipt_path=self.receipt_dir / 'audio-forward-flash.json')
            invalid = dict(receipt, unexpected='ignored?')
            with self.assertRaisesRegex(ValueError, 'unexpected fields'):
                ADAPTER.validate_flash_receipt(
                    'audio-forward', invalid, state_root=self.state_root,
                    receipt_path=self.receipt_dir / 'audio-forward-flash.json')
        finally:
            sandbox.close()

    def test_unknown_transport_is_one_attempt_and_guard_stays_unresolved(self):
        profile = ADAPTER.PROFILE.resolve_profile('audio-forward')
        calls = []
        def transport(*args, **kwargs):
            calls.append(kwargs)
            raise subprocess.TimeoutExpired('ssh', 10)
        with self.assertRaisesRegex(RuntimeError, 'unknown'):
            self.run_operation('audio-forward', 'stage', self.candidate, transport=transport)
        self.assertEqual(len(calls), 1)
        marker = ADAPTER.GUARD._read_marker(
            self.state_root / f'{ADAPTER.operation_id("audio-forward", "stage")}.json')
        self.assertEqual(marker['status'], 'unknown')
        with self.assertRaises((RuntimeError, ValueError)):
            self.run_operation('audio-forward', 'stage', self.candidate, transport=transport)
        self.assertEqual(len(calls), 1)

    def test_malformed_remote_stage_receipt_creates_no_success_receipt(self):
        calls = []
        def transport(*args, **kwargs):
            calls.append(1)
            return subprocess.CompletedProcess('remote', 0,
                b'{"mode":"stage","partition_written":0,"backup_sha256":"bad","candidate_sha256":"bad"}', b'')
        with self.assertRaisesRegex(RuntimeError, 'invalid receipt'):
            self.run_operation('audio-forward', 'stage', self.candidate, transport=transport)
        self.assertEqual(len(calls), 1)
        self.assertFalse((self.receipt_dir / 'audio-forward-stage.json').exists())
        marker = ADAPTER.GUARD._read_marker(
            self.state_root / f'{ADAPTER.operation_id("audio-forward", "stage")}.json')
        self.assertEqual(marker['status'], 'unknown')

    def test_stage_marker_receipt_binding_and_one_use_path_reject_replay(self):
        sandbox, target = self.make_sandbox('audio-forward')
        calls = []
        try:
            self.stage('audio-forward', sandbox, target, calls)
            with self.assertRaisesRegex(ValueError, 'overwrite|existing'):
                self.stage('audio-forward', sandbox, target, calls)
            self.assertEqual(len(calls), 1)
            marker = ADAPTER.GUARD._read_marker(
                self.state_root / f'{ADAPTER.operation_id("audio-forward", "stage")}.json')
            self.assertEqual(marker['status'], 'complete')
            self.assertEqual(marker['receipt_path'], str(self.receipt_dir / 'audio-forward-stage.json'))
        finally:
            sandbox.close()

    def _completed_forward_flash(self):
        return self._completed_profile_flash('audio-forward')

    def _completed_profile_flash(self, profile_name):
        sandbox, target = self.make_sandbox(profile_name)
        calls = []
        self.stage(profile_name, sandbox, target, calls)
        self.flash(profile_name, sandbox, target, calls)
        return sandbox, target, calls

    def _restart_workspace(self):
        self.tearDown()
        self.setUp()

    def _completed_forward_initial_observation(self, reboot_outcome='ACKNOWLEDGED'):
        return self._completed_initial_observation('audio-forward', reboot_outcome)

    def _completed_reverse_initial_observation(self):
        return self._completed_initial_observation('audio-reverse')

    def _completed_initial_observation(self, profile_name, reboot_outcome='ACKNOWLEDGED'):
        sandbox, _target, _calls = self._completed_profile_flash(profile_name)
        original_boot = '11111111-1111-4111-8111-111111111111'
        new_boot = '33333333-3333-4333-8333-333333333333'
        profile = ADAPTER.PROFILE.resolve_profile(profile_name)
        reboot_commands = []

        def reboot_remote(_ssh_path, command, **kwargs):
            reboot_commands.append(command)
            if reboot_outcome == 'UNKNOWN':
                raise subprocess.TimeoutExpired(command, kwargs.get('timeout'))
            return subprocess.CompletedProcess(command, 0, b'', b'')

        before_role = ('candidate' if profile['before_role'] == 'audio_candidate'
                       else 'baseline')
        target_role = ('candidate' if profile['target_role'] == 'audio_candidate'
                       else 'baseline')
        current = lambda expected: identity(
            profile, expected, boot_id=original_boot, module_role=before_role)
        if reboot_outcome == 'UNKNOWN':
            with self.assertRaisesRegex(RuntimeError, 'never retry'):
                OBSERVER.request_recovery_once(
                    profile_name, receipts_root=self.root / 'receipts',
                    state_root=self.state_root, project_root=ROOT,
                    transport=reboot_remote,
                    snapshotter=lambda: readiness(original_boot),
                    identity_reader=current,
                    helper_reader=OBSERVER._expected_helpers)
        else:
            OBSERVER.request_recovery_once(
                profile_name, receipts_root=self.root / 'receipts',
                state_root=self.state_root, project_root=ROOT,
                transport=reboot_remote,
                snapshotter=lambda: readiness(original_boot),
                identity_reader=current,
                helper_reader=OBSERVER._expected_helpers)
        target_identity = lambda expected: identity(
            profile, expected, boot_id=new_boot, module_role=target_role)
        OBSERVER.observe_reboot_once(
            profile_name, receipts_root=self.root / 'receipts',
            state_root=self.state_root, project_root=ROOT,
            snapshotter=lambda: readiness(new_boot), identity_reader=target_identity)
        return sandbox, new_boot, reboot_commands

    def _stability_transport(self, boot_id, *, startup_uptimes=None, states=None,
                             identities=None, failures=None, clock=None,
                             query_cost=0.0, profile_name='audio-forward',
                             advancing_uptime=False):
        profile = ADAPTER.PROFILE.resolve_profile(profile_name)
        target_role = ('candidate' if profile['target_role'] == 'audio_candidate'
                       else 'baseline')
        startup_values = list(startup_uptimes or [180.0])
        state_values = list(states or [readiness(boot_id)] * 3)
        identity_values = list(identities or [
            identity(profile, profile['target_sha256'], boot_id=boot_id,
                     module_role=target_role) for _ in range(3)])
        failed_attempts = {key: set(value) for key, value in (failures or {}).items()}
        counts = {'startup': 0, 'readiness': 0, 'identity': 0}
        calls = []
        remote_uptime_anchor = None
        remote_uptime_anchor_host = None

        def transport(_ssh_path, command, *, input_data, timeout, project_root):
            nonlocal remote_uptime_anchor, remote_uptime_anchor_host
            if 'audio_modules' in command:
                label = 'identity'
            elif 'kernel_log_classification' in command:
                label = 'readiness'
            elif "/proc/uptime" in command:
                label = 'startup'
            else:
                raise AssertionError('unexpected remote command in stability observer')
            counts[label] += 1
            calls.append({'label': label, 'timeout': timeout, 'command': command,
                          'remaining_at_start': (OBSERVER.STABILITY_TOTAL_DEADLINE_SECONDS -
                                                clock.now) if clock is not None else None})
            if clock is not None and query_cost:
                clock.now += query_cost
            if counts[label] in failed_attempts.get(label, set()):
                raise subprocess.TimeoutExpired(command, timeout)
            if label == 'startup':
                uptime = startup_values.pop(0) if startup_values else 180.0
                value = {'boot_id': boot_id, 'uptime_seconds': uptime}
                if advancing_uptime and clock is not None:
                    remote_uptime_anchor = uptime
                    remote_uptime_anchor_host = clock.now
            elif label == 'readiness':
                value = dict(state_values.pop(0) if state_values else readiness(boot_id))
                if advancing_uptime and clock is not None:
                    if remote_uptime_anchor is None or remote_uptime_anchor_host is None:
                        raise AssertionError('advancing fake uptime has no startup clock anchor')
                    value['uptime_seconds'] = (
                        remote_uptime_anchor + clock.now - remote_uptime_anchor_host)
            else:
                value = identity_values.pop(0) if identity_values else identity(
                    profile, profile['target_sha256'], boot_id=boot_id,
                    module_role=target_role)
            return subprocess.CompletedProcess(command, 0, json.dumps(value), '')

        return transport, calls, counts

    def test_reboot_ack_uses_guarded_helper_once_and_initial_observation_is_profile_bound(self):
        sandbox, target, calls = self._completed_forward_flash()
        remote_calls = []
        original_boot = '11111111-1111-4111-8111-111111111111'
        new_boot = '33333333-3333-4333-8333-333333333333'
        current = lambda expected: identity(
            ADAPTER.PROFILE.resolve_profile('audio-forward'), expected,
            boot_id=original_boot, module_role='baseline')
        def reboot_remote(_ssh_path, command, **kwargs):
            remote_calls.append(command)
            return subprocess.CompletedProcess(command, 0, b'', b'')
        try:
            request = OBSERVER.request_recovery_once(
                'audio-forward', receipts_root=self.root / 'receipts',
                state_root=self.state_root, project_root=ROOT, transport=reboot_remote,
                snapshotter=lambda: readiness(original_boot), identity_reader=current,
                helper_reader=OBSERVER._expected_helpers)
            self.assertEqual(request['outcome'], 'ACKNOWLEDGED')
            self.assertEqual(len(remote_calls), 1)
            remote_argv = shlex.split(remote_calls[0])
            self.assertEqual(remote_argv[:4], ['python3', '-I', '-B', '-c'])
            self.assertIn("os.execve('/usr/local/sbin/s22-reboot'", remote_argv[4])
            self.assertIn("'/usr/local/sbin/s22-reboot','recovery'", remote_argv[4])
            self.assertNotIn('&&', remote_argv[4])
            with self.assertRaises((ValueError, FileExistsError)):
                OBSERVER.request_recovery_once(
                    'audio-forward', receipts_root=self.root / 'receipts',
                    state_root=self.state_root, project_root=ROOT, transport=reboot_remote,
                    snapshotter=lambda: readiness(original_boot), identity_reader=current,
                    helper_reader=OBSERVER._expected_helpers)
            self.assertEqual(len(remote_calls), 1)

            target_identity = lambda expected: identity(
                ADAPTER.PROFILE.resolve_profile('audio-forward'), expected,
                boot_id=new_boot, module_role='candidate')
            observed = OBSERVER.observe_reboot_once(
                'audio-forward', receipts_root=self.root / 'receipts',
                state_root=self.state_root, project_root=ROOT,
                snapshotter=lambda: readiness(new_boot), identity_reader=target_identity)
            self.assertEqual(observed['status'], 'initial-observation-complete')
            self.assertEqual(observed['baseline_boot_id'], original_boot)
            self.assertEqual(observed['observed_boot_id'], new_boot)
            self.assertEqual(observed['audio_module_gnu_build_ids'],
                             OBSERVER._expected_profile_modules('audio-forward'))
            self.assertFalse(observed['audio_hardware_acceptance'])
            self.assertFalse(observed['bootability_claim'])
            self.assertEqual(observed['dedicated_pi_session_status'], 'absent')
            with self.assertRaises((ValueError, FileExistsError)):
                OBSERVER.observe_reboot_once(
                    'audio-forward', receipts_root=self.root / 'receipts',
                    state_root=self.state_root, project_root=ROOT,
                    snapshotter=lambda: readiness(new_boot), identity_reader=target_identity)
        finally:
            sandbox.close()

    def test_reboot_disconnect_is_unknown_never_reissued_but_can_be_observed_readonly(self):
        sandbox, target, _ = self._completed_forward_flash()
        remote_calls = []
        original_boot = '11111111-1111-4111-8111-111111111111'
        def disconnect(_ssh_path, command, **kwargs):
            remote_calls.append(command)
            raise subprocess.TimeoutExpired(command, 15)
        current = lambda expected: identity(
            ADAPTER.PROFILE.resolve_profile('audio-forward'), expected,
            boot_id=original_boot, module_role='baseline')
        try:
            with self.assertRaisesRegex(RuntimeError, 'never retry'):
                OBSERVER.request_recovery_once(
                    'audio-forward', receipts_root=self.root / 'receipts',
                    state_root=self.state_root, project_root=ROOT, transport=disconnect,
                    snapshotter=lambda: readiness(original_boot), identity_reader=current,
                    helper_reader=OBSERVER._expected_helpers)
            self.assertEqual(len(remote_calls), 1)
            marker_path = self.state_root / f'{OBSERVER._reboot_marker_id("audio-forward")}.json'
            self.assertEqual(ADAPTER.GUARD._read_marker(marker_path)['status'], 'unknown')
            with self.assertRaises((ValueError, RuntimeError)):
                OBSERVER.request_recovery_once(
                    'audio-forward', receipts_root=self.root / 'receipts',
                    state_root=self.state_root, project_root=ROOT, transport=disconnect,
                    snapshotter=lambda: readiness(original_boot), identity_reader=current,
                    helper_reader=OBSERVER._expected_helpers)
            self.assertEqual(len(remote_calls), 1)

            new_boot = '44444444-4444-4444-8444-444444444444'
            observed = OBSERVER.observe_reboot_once(
                'audio-forward', receipts_root=self.root / 'receipts',
                state_root=self.state_root, project_root=ROOT,
                snapshotter=lambda: readiness(new_boot),
                identity_reader=lambda expected: identity(
                    ADAPTER.PROFILE.resolve_profile('audio-forward'), expected,
                    boot_id=new_boot, module_role='candidate'))
            self.assertEqual(observed['reboot_request_outcome'], 'UNKNOWN')
            self.assertEqual(ADAPTER.GUARD._read_marker(marker_path)['status'], 'unknown')
        finally:
            sandbox.close()

    def test_reboot_helper_mismatch_fails_before_request_and_is_not_retried(self):
        sandbox, _target, _ = self._completed_forward_flash()
        remote_calls = []
        boot_id = '11111111-1111-4111-8111-111111111111'
        def remote(_ssh_path, command, **kwargs):
            remote_calls.append(command)
            return subprocess.CompletedProcess(command, 0, b'', b'')
        current = lambda expected: identity(
            ADAPTER.PROFILE.resolve_profile('audio-forward'), expected,
            boot_id=boot_id, module_role='baseline')
        bad_helpers = OBSERVER._expected_helpers()
        bad_helpers['helpers']['reboot']['sha256'] = '0' * 64
        try:
            with self.assertRaisesRegex(ADAPTER.CAMERA_OBSERVER.CameraObserverError,
                                        'helper identity'):
                OBSERVER.request_recovery_once(
                    'audio-forward', receipts_root=self.root / 'receipts',
                    state_root=self.state_root, project_root=ROOT, transport=remote,
                    snapshotter=lambda: readiness(boot_id), identity_reader=current,
                    helper_reader=lambda: bad_helpers)
            self.assertEqual(remote_calls, [])
            marker = ADAPTER.GUARD._read_marker(
                self.state_root / f'{OBSERVER._reboot_marker_id("audio-forward")}.json')
            self.assertEqual(marker['status'], 'unknown')
        finally:
            sandbox.close()

    def test_reboot_preflight_requires_profile_source_module_ids(self):
        sandbox, _target, _calls = self._completed_forward_flash()
        remote_calls = []
        boot_id = '11111111-1111-4111-8111-111111111111'
        def remote(_ssh_path, command, **kwargs):
            remote_calls.append(command)
            return subprocess.CompletedProcess(command, 0, b'', b'')
        profile = ADAPTER.PROFILE.resolve_profile('audio-forward')
        current = lambda expected: identity(profile, expected, boot_id=boot_id,
                                            module_role='candidate')
        try:
            with self.assertRaisesRegex(ValueError, 'loaded module GNU build ID mismatch'):
                OBSERVER.request_recovery_once(
                    'audio-forward', receipts_root=self.root / 'receipts',
                    state_root=self.state_root, project_root=ROOT, transport=remote,
                    snapshotter=lambda: readiness(boot_id), identity_reader=current,
                    helper_reader=OBSERVER._expected_helpers)
            self.assertEqual(remote_calls, [])
            marker = ADAPTER.GUARD._read_marker(
                self.state_root / f'{OBSERVER._reboot_marker_id("audio-forward")}.json')
            self.assertEqual(marker['status'], 'unknown')
        finally:
            sandbox.close()

    def test_incomplete_rendered_log_cannot_complete_initial_observation(self):
        sandbox, _target, _calls = self._completed_forward_flash()
        remote_calls = []
        original_boot = '11111111-1111-4111-8111-111111111111'
        new_boot = '66666666-6666-4666-8666-666666666666'

        def remote(_ssh_path, command, **kwargs):
            remote_calls.append(command)
            return subprocess.CompletedProcess(command, 0, b'', b'')

        profile = ADAPTER.PROFILE.resolve_profile('audio-forward')
        try:
            OBSERVER.request_recovery_once(
                'audio-forward', receipts_root=self.root / 'receipts',
                state_root=self.state_root, project_root=ROOT, transport=remote,
                snapshotter=lambda: readiness(original_boot),
                identity_reader=lambda expected: identity(
                    profile, expected, boot_id=original_boot, module_role='baseline'),
                helper_reader=OBSERVER._expected_helpers)
            self.assertEqual(len(remote_calls), 1)

            bad_snapshot = readiness_from_rendered_collector(new_boot, '')
            target_identity = lambda expected: identity(
                profile, expected, boot_id=new_boot, module_role='candidate')
            with self.assertRaisesRegex(ValueError, 'input/capture/coverage'):
                OBSERVER.observe_reboot_once(
                    'audio-forward', receipts_root=self.root / 'receipts',
                    state_root=self.state_root, project_root=ROOT,
                    snapshotter=lambda: bad_snapshot, identity_reader=target_identity)

            paths = OBSERVER._receipt_paths('audio-forward', self.root / 'receipts')
            self.assertFalse(paths['observation'].exists())
            failure_path = paths['directory'] / 'audio-forward-initial-observation-failure.json'
            self.assertTrue(failure_path.is_file())
            failure = json.loads(failure_path.read_text())
            self.assertEqual(failure['status'], 'UNKNOWN')
            marker_path = self.state_root / f'{OBSERVER._observation_marker_id("audio-forward")}.json'
            marker = ADAPTER.GUARD._read_marker(marker_path)
            self.assertEqual(marker['status'], 'unknown')
            self.assertNotEqual(marker.get('outcome'), 'success')
        finally:
            sandbox.close()

    def test_observation_rejects_unchanged_boot_wrong_hash_and_wrong_module_ids(self):
        # The small validated identity surface rejects each mismatch before a
        # success receipt can be emitted.
        state = readiness('55555555-5555-4555-8555-555555555555')
        bad = dict(state, boot_reset_first_record='panic')
        with self.assertRaisesRegex(ValueError, 'RECOVERY'):
            OBSERVER.validate_readiness_snapshot(bad, post_reboot=True)
        profile = ADAPTER.PROFILE.resolve_profile('audio-forward')
        wrong = identity(profile, profile['target_sha256'],
                         module_role='baseline')
        with self.assertRaisesRegex(ValueError, 'GNU build ID mismatch'):
            ADAPTER.validate_audio_identity(
                wrong, expected_recovery_sha=profile['target_sha256'],
                expected_modules=OBSERVER._expected_profile_modules('audio-forward'))

    def test_bounded_stability_waits_for_uptime_and_delayed_readiness_then_samples_freshly(self):
        sandbox, boot_id, reboot_commands = self._completed_forward_initial_observation()
        profile = ADAPTER.PROFILE.resolve_profile('audio-forward')
        clock = FakeClock()
        not_ready = json.loads(json.dumps(readiness(boot_id)))
        not_ready['health']['status'] = 'starting'
        not_ready['readiness']['model_api_health'] = False
        states = [not_ready, readiness(boot_id), readiness(boot_id), readiness(boot_id)]
        identities = [identity(profile, profile['target_sha256'], boot_id=boot_id,
                                module_role='candidate') for _ in states]
        transport, calls, counts = self._stability_transport(
            boot_id, startup_uptimes=[120.0, 180.0], states=states,
            identities=identities, failures={'startup': {1}}, clock=clock,
            query_cost=0.1, advancing_uptime=True)
        try:
            result = OBSERVER.observe_stability(
                'audio-forward', receipts_root=self.root / 'receipts',
                state_root=self.state_root, project_root=ROOT,
                transport=transport, clock=clock, sleeper=clock.sleep)
            self.assertEqual(result['status'], 'bounded-stability-observed')
            self.assertEqual(result['reboot_request_outcome'], 'ACKNOWLEDGED')
            self.assertEqual(result['sample_count'], 3)
            self.assertEqual(result['stability_seconds'], 195.0)
            self.assertEqual(result['sample_offsets_seconds'], [0.0, 90.0, 195.0])
            self.assertAlmostEqual(result['remote_stability_seconds'], 195.0)
            self.assertGreaterEqual(result['remote_stability_seconds'], 180.0)
            self.assertEqual(result['startup_readiness_polls'], 2)
            self.assertEqual(result['startup_uptime_probes'], 2)
            self.assertEqual(result['read_only_reconnect_attempts'], 1)
            self.assertEqual(result['remote_query_attempts'], len(calls))
            self.assertFalse(result['audio_hardware_acceptance'])
            self.assertFalse(result['bootability_claim'])
            self.assertEqual(counts, {'startup': 3, 'readiness': 4, 'identity': 4})
            self.assertEqual(len(reboot_commands), 1)
            self.assertTrue(all(0 < call['timeout'] <= {
                'startup': OBSERVER.STABILITY_STARTUP_QUERY_CAP_SECONDS,
                'readiness': OBSERVER.STABILITY_READINESS_QUERY_CAP_SECONDS,
                'identity': OBSERVER.STABILITY_IDENTITY_QUERY_CAP_SECONDS,
            }[call['label']] for call in calls))
            self.assertTrue(all(call['timeout'] <= call['remaining_at_start']
                                for call in calls))
            self.assertLess(clock.now, OBSERVER.STABILITY_TOTAL_DEADLINE_SECONDS)
            receipt_paths = OBSERVER._stability_receipt_paths(
                'audio-forward', self.root / 'receipts')
            sample_files = [json.loads(path.read_text()) for path in receipt_paths['samples']]
            self.assertEqual([sample['index'] for sample in sample_files], [0, 1, 2])
            self.assertTrue(all(sample['status'] == 'ready' for sample in sample_files))
            self.assertTrue(all(sample['remote_uptime_valid'] is True
                                for sample in sample_files))
            sample_uptimes = [sample['remote_uptime_seconds'] for sample in sample_files]
            self.assertEqual(result['remote_sample_uptimes_seconds'], sample_uptimes)
            self.assertEqual([item['remote_uptime_seconds'] for item in result['samples']],
                             sample_uptimes)
            sample_remote_intervals = [sample['remote_interval_seconds']
                                       for sample in sample_files]
            result_remote_intervals = result['remote_sample_intervals_seconds']
            self.assertIsNone(sample_remote_intervals[0])
            self.assertIsNone(result_remote_intervals[0])
            for actual in (sample_remote_intervals[1:] + result_remote_intervals[1:]):
                self.assertAlmostEqual(actual, 90.0 if actual < 100 else 105.0)
            self.assertEqual([sample['host_interval_seconds'] for sample in sample_files],
                             [None, 90.0, 105.0])
            self.assertTrue(all(sample['identity']['recovery_sha256'] == profile['target_sha256']
                                for sample in sample_files))
            self.assertTrue(all(sample['identity']['audio_module_gnu_build_ids'] ==
                                OBSERVER._expected_profile_modules('audio-forward')
                                for sample in sample_files))
            self.assertEqual(len({item['sha256'] for item in result['samples']}), 3)
        finally:
            sandbox.close()

    def test_bounded_stability_succeeds_for_reverse_profile_target_module_role(self):
        sandbox, boot_id, reboot_commands = self._completed_reverse_initial_observation()
        clock = FakeClock()
        transport, calls, counts = self._stability_transport(
            boot_id, startup_uptimes=[180.0], clock=clock,
            profile_name='audio-reverse', advancing_uptime=True)
        try:
            result = OBSERVER.observe_stability(
                'audio-reverse', receipts_root=self.root / 'receipts',
                state_root=self.state_root, project_root=ROOT,
                transport=transport, clock=clock, sleeper=clock.sleep)
            expected_before = ADAPTER.expected_profile_modules('audio-reverse', before=True)
            expected_after = ADAPTER.expected_profile_modules('audio-reverse', before=False)
            flash = json.loads(OBSERVER._receipt_paths(
                'audio-reverse', self.root / 'receipts')['flash'].read_text())
            preboot_modules = {
                name: item['gnu_build_id']
                for name, item in flash['prewrite_identity']['audio_modules'].items()}
            self.assertEqual(preboot_modules, expected_before)
            self.assertEqual(result['status'], 'bounded-stability-observed')
            self.assertEqual(result['audio_module_gnu_build_ids'], expected_after)
            self.assertNotEqual(expected_before, expected_after)
            self.assertGreaterEqual(result['remote_stability_seconds'], 180.0)
            receipt_paths = OBSERVER._stability_receipt_paths(
                'audio-reverse', self.root / 'receipts')
            sample_files = [json.loads(path.read_text()) for path in receipt_paths['samples']]
            self.assertTrue(all(sample['identity']['audio_module_gnu_build_ids'] == expected_after
                                for sample in sample_files))
            self.assertEqual(counts, {'startup': 1, 'readiness': 3, 'identity': 3})
            self.assertEqual(len(reboot_commands), 1)
            self.assertEqual(result['remote_query_attempts'], len(calls))
        finally:
            sandbox.close()

    def test_stability_requires_fresh_finite_exact_remote_uptime(self):
        cases = (
            ('replayed', [240.0, 240.0, 240.0], 'remote_uptime_not_advancing'),
            ('decreasing', [240.0, 239.0, 238.0], 'remote_uptime_not_advancing'),
            ('implausibly_slow', [240.0, 250.0, 260.0],
             'remote_uptime_progression_mismatch'),
            ('nonfinite', [float('nan'), 240.0, 240.0], 'ambiguous_response'),
            ('boolean_type', [True, 240.0, 240.0], 'ambiguous_response'),
            ('string_type', ['240.0', 240.0, 240.0], 'ambiguous_response'),
            ('host_interval_long_remote_interval_short',
             [240.0, 315.0, 405.0], 'remote_interval_short'),
        )
        for case_index, (case, uptimes, expected_category) in enumerate(cases):
            if case_index:
                self._restart_workspace()
            with self.subTest(case=case):
                sandbox, boot_id, _ = self._completed_forward_initial_observation()
                profile = ADAPTER.PROFILE.resolve_profile('audio-forward')
                clock = FakeClock()
                states = []
                for uptime in uptimes:
                    state = readiness(boot_id)
                    state['uptime_seconds'] = uptime
                    states.append(state)
                if case == 'replayed':
                    replayed = readiness(boot_id)
                    states = [replayed] * 3
                repeated_identity = identity(
                    profile, profile['target_sha256'], boot_id=boot_id,
                    module_role='candidate')
                identities = ([repeated_identity] * 3 if case == 'replayed' else None)
                transport, _calls, _counts = self._stability_transport(
                    boot_id, startup_uptimes=[180.0], states=states,
                    identities=identities, clock=clock, query_cost=0.1)
                try:
                    with self.assertRaises((RuntimeError, ValueError)):
                        OBSERVER.observe_stability(
                            'audio-forward', receipts_root=self.root / 'receipts',
                            state_root=self.state_root, project_root=ROOT,
                            transport=transport, clock=clock, sleeper=clock.sleep)
                    paths = OBSERVER._stability_receipt_paths(
                        'audio-forward', self.root / 'receipts')
                    failure = json.loads(paths['failure'].read_text())
                    self.assertEqual(failure['failure_category'], expected_category)
                    self.assertFalse(paths['result'].exists())
                    sample_paths = [*paths['startup'], *paths['samples']]
                    sample_records = [json.loads(path.read_text()) for path in sample_paths
                                      if path.is_file()]
                    self.assertTrue(sample_records)
                    if case == 'replayed':
                        replayed_sample = next(
                            sample for sample in sample_records
                            if sample['remote_uptime_seconds'] == 240.0 and
                            sample['remote_interval_seconds'] == 0.0)
                        self.assertEqual(replayed_sample['remote_uptime_valid'], True)
                    elif case == 'decreasing':
                        decreasing_sample = next(
                            sample for sample in sample_records
                            if sample['remote_uptime_seconds'] == 239.0)
                        self.assertEqual(decreasing_sample['remote_interval_seconds'], -1.0)
                    elif case in ('nonfinite', 'boolean_type', 'string_type'):
                        invalid = sample_records[-1]
                        self.assertIsNone(invalid['remote_uptime_seconds'])
                        self.assertFalse(invalid['remote_uptime_valid'])
                        expected_type = {'nonfinite': 'float', 'boolean_type': 'bool',
                                         'string_type': 'str'}[case]
                        self.assertEqual(invalid['remote_uptime_input_type'], expected_type)
                    elif case == 'host_interval_long_remote_interval_short':
                        self.assertEqual(failure['host_sample_offsets_seconds'],
                                         [0.0, 90.0, 195.0])
                        self.assertEqual(failure['remote_sample_uptimes_seconds'],
                                         [240.0, 315.0, 405.0])
                        self.assertEqual(failure['remote_stability_seconds'], 165.0)
                finally:
                    sandbox.close()

    def test_stability_rejects_changed_boot_and_wrong_image_or_module_identity(self):
        for mismatch_index, mismatch in enumerate(
                ('startup_boot', 'later_boot', 'image', 'module')):
            if mismatch_index:
                self._restart_workspace()
            with self.subTest(mismatch=mismatch):
                sandbox, boot_id, _ = self._completed_forward_initial_observation()
                profile = ADAPTER.PROFILE.resolve_profile('audio-forward')
                clock = FakeClock()
                transport, calls, _ = self._stability_transport(
                    boot_id, startup_uptimes=[180.0], clock=clock)
                if mismatch == 'startup_boot':
                    transport, calls, _ = self._stability_transport(
                        '99999999-9999-4999-8999-999999999999',
                        startup_uptimes=[180.0], clock=clock)
                elif mismatch == 'later_boot':
                    changed_boot = '99999999-9999-4999-8999-999999999999'
                    changed_state = readiness(changed_boot)
                    changed_identity = identity(
                        profile, profile['target_sha256'], boot_id=changed_boot,
                        module_role='candidate')
                    transport, calls, _ = self._stability_transport(
                        boot_id, startup_uptimes=[180.0],
                        states=[readiness(boot_id), changed_state],
                        identities=[identity(profile, profile['target_sha256'],
                                             boot_id=boot_id, module_role='candidate'),
                                    changed_identity], clock=clock)
                elif mismatch == 'image':
                    wrong = identity(profile, '0' * 64, boot_id=boot_id,
                                     module_role='candidate')
                    transport, calls, _ = self._stability_transport(
                        boot_id, startup_uptimes=[180.0],
                        identities=[wrong], clock=clock)
                else:
                    wrong = identity(profile, profile['target_sha256'],
                                     boot_id=boot_id, module_role='baseline')
                    transport, calls, _ = self._stability_transport(
                        boot_id, startup_uptimes=[180.0],
                        identities=[wrong], clock=clock)
                try:
                    with self.assertRaises((ValueError, RuntimeError)):
                        OBSERVER.observe_stability(
                            'audio-forward', receipts_root=self.root / 'receipts',
                            state_root=self.state_root, project_root=ROOT,
                            transport=transport, clock=clock, sleeper=clock.sleep)
                    receipt_paths = OBSERVER._stability_receipt_paths(
                        'audio-forward', self.root / 'receipts')
                    failure = json.loads(receipt_paths['failure'].read_text())
                    expected = {'startup_boot': 'changed_boot',
                                'later_boot': 'changed_boot',
                                'image': 'wrong_identity',
                                'module': 'wrong_identity'}[mismatch]
                    self.assertEqual(failure['failure_category'], expected)
                    self.assertFalse(receipt_paths['result'].exists())
                    self.assertTrue(len(calls) <= (5 if mismatch == 'later_boot' else 3))
                finally:
                    sandbox.close()

    def test_fatal_and_hung_task_diagnostics_are_terminal_not_startup_waits(self):
        cases = ('fatal_assessment', 'hung_task')
        for case_index, case in enumerate(cases):
            if case_index:
                self._restart_workspace()
            with self.subTest(case=case):
                sandbox, boot_id, _ = self._completed_forward_initial_observation()
                state = readiness(boot_id)
                if case == 'fatal_assessment':
                    state['kernel_log_classification']['assessment'] = 'fatal'
                else:
                    state['kernel_log_classification']['assessment'] = 'hung_task_warning'
                    state['kernel_log_classification']['hung_task_warning_count'] = 1
                clock = FakeClock()
                transport, _calls, counts = self._stability_transport(
                    boot_id, startup_uptimes=[180.0], states=[state], clock=clock)
                try:
                    with self.assertRaises(RuntimeError):
                        OBSERVER.observe_stability(
                            'audio-forward', receipts_root=self.root / 'receipts',
                            state_root=self.state_root, project_root=ROOT,
                            transport=transport, clock=clock, sleeper=clock.sleep)
                    receipt_paths = OBSERVER._stability_receipt_paths(
                        'audio-forward', self.root / 'receipts')
                    failure = json.loads(receipt_paths['failure'].read_text())
                    self.assertEqual(failure['failure_category'], 'serious_fault')
                    self.assertEqual(counts, {'startup': 1, 'readiness': 1, 'identity': 1})
                    self.assertFalse(receipt_paths['result'].exists())
                finally:
                    sandbox.close()

    def test_trace_only_is_unready_not_terminal_and_requires_fresh_ready_poll(self):
        sandbox, boot_id, reboot_commands = self._completed_forward_initial_observation()
        trace_only = readiness(boot_id)
        trace_only['kernel_log_classification']['assessment'] = 'trace_only'
        trace_only['kernel_log_classification']['call_trace_count'] = 1
        profile = ADAPTER.PROFILE.resolve_profile('audio-forward')
        clock = FakeClock()
        states = [trace_only, readiness(boot_id), readiness(boot_id), readiness(boot_id)]
        identities = [identity(profile, profile['target_sha256'], boot_id=boot_id,
                                module_role='candidate') for _ in states]
        transport, _calls, counts = self._stability_transport(
            boot_id, startup_uptimes=[180.0], states=states,
            identities=identities, clock=clock, advancing_uptime=True)
        try:
            result = OBSERVER.observe_stability(
                'audio-forward', receipts_root=self.root / 'receipts',
                state_root=self.state_root, project_root=ROOT,
                transport=transport, clock=clock, sleeper=clock.sleep)
            self.assertEqual(result['status'], 'bounded-stability-observed')
            self.assertEqual(result['startup_readiness_polls'], 2)
            self.assertEqual(counts, {'startup': 1, 'readiness': 4, 'identity': 4})
            startup_path = OBSERVER._stability_receipt_paths(
                'audio-forward', self.root / 'receipts')['startup'][0]
            startup = json.loads(startup_path.read_text())
            self.assertEqual(startup['kernel_diagnostics']['assessment'], 'trace_only')
            self.assertEqual(len(reboot_commands), 1)
        finally:
            sandbox.close()

    def test_later_fault_transport_gap_timeout_and_empty_log_all_remain_unknown(self):
        cases = ('later_fault', 'transport_gap', 'timeout', 'empty_log')
        for case_index, case in enumerate(cases):
            if case_index:
                self._restart_workspace()
            with self.subTest(case=case):
                sandbox, boot_id, _ = self._completed_forward_initial_observation()
                profile = ADAPTER.PROFILE.resolve_profile('audio-forward')
                clock = FakeClock()
                state_values = [readiness(boot_id) for _ in range(3)]
                identities = [identity(profile, profile['target_sha256'], boot_id=boot_id,
                                        module_role='candidate') for _ in range(3)]
                failures = None
                query_cost = 0.0
                if case == 'later_fault':
                    state_values[1]['serious_fault'] = True
                    state_values[1]['kernel_log_classification']['fatal_indicators'] = ['Oops']
                elif case == 'transport_gap':
                    failures = {'readiness': {2, 3}}
                elif case == 'timeout':
                    query_cost = OBSERVER.STABILITY_TOTAL_DEADLINE_SECONDS + 1
                else:
                    empty = readiness_from_rendered_collector(boot_id, '')
                    state_values = [empty, empty]
                    identities = identities[:2]
                transport, calls, _ = self._stability_transport(
                    boot_id, startup_uptimes=[180.0], states=state_values,
                    identities=identities, failures=failures, clock=clock,
                    query_cost=query_cost)
                try:
                    with self.assertRaises((ValueError, RuntimeError, TimeoutError)):
                        OBSERVER.observe_stability(
                            'audio-forward', receipts_root=self.root / 'receipts',
                            state_root=self.state_root, project_root=ROOT,
                            transport=transport, clock=clock, sleeper=clock.sleep)
                    receipt_paths = OBSERVER._stability_receipt_paths(
                        'audio-forward', self.root / 'receipts')
                    failure = json.loads(receipt_paths['failure'].read_text())
                    expected = {
                        'later_fault': 'serious_fault',
                        'transport_gap': 'transport_gap',
                        'timeout': 'timeout',
                        'empty_log': 'unready_startup',
                    }[case]
                    self.assertEqual(failure['failure_category'], expected)
                    self.assertEqual(failure['retry_allowed'], False)
                    self.assertFalse(receipt_paths['result'].exists())
                finally:
                    sandbox.close()

    def test_stability_requires_clock_progress_and_never_reboots_after_unknown(self):
        sandbox, boot_id, _ = self._completed_forward_initial_observation()
        clock = FakeClock()
        transport, calls, _ = self._stability_transport(
            boot_id, startup_uptimes=[180.0], clock=clock, query_cost=0.1,
            advancing_uptime=True)
        try:
            with self.assertRaises(RuntimeError):
                OBSERVER.observe_stability(
                    'audio-forward', receipts_root=self.root / 'receipts',
                    state_root=self.state_root, project_root=ROOT,
                    transport=transport, clock=clock, sleeper=lambda _seconds: None)
            failure = json.loads(OBSERVER._stability_receipt_paths(
                'audio-forward', self.root / 'receipts')['failure'].read_text())
            self.assertEqual(failure['failure_category'], 'sample_gap')
            self.assertFalse(OBSERVER._stability_receipt_paths(
                'audio-forward', self.root / 'receipts')['result'].exists())
        finally:
            sandbox.close()

        self._restart_workspace()
        sandbox, boot_id, reboot_commands = self._completed_forward_initial_observation('UNKNOWN')
        reboot_marker = self.state_root / f'{OBSERVER._reboot_marker_id("audio-forward")}.json'
        before = reboot_marker.read_bytes()
        clock = FakeClock()
        transport, calls, _ = self._stability_transport(
            boot_id, clock=clock, advancing_uptime=True)
        try:
            result = OBSERVER.observe_stability(
                'audio-forward', receipts_root=self.root / 'receipts',
                state_root=self.state_root, project_root=ROOT,
                transport=transport, clock=clock, sleeper=clock.sleep)
            self.assertEqual(result['reboot_request_outcome'], 'UNKNOWN')
            self.assertEqual(reboot_marker.read_bytes(), before)
            self.assertEqual(len(reboot_commands), 1)
            self.assertFalse(any('s22-reboot' in call['command'] for call in calls))
            with self.assertRaises((ValueError, FileExistsError)):
                OBSERVER.request_recovery_once(
                    'audio-forward', receipts_root=self.root / 'receipts',
                    state_root=self.state_root, project_root=ROOT,
                    transport=lambda *_args, **_kwargs: self.fail('reboot was replayed'),
                    snapshotter=lambda: readiness(boot_id),
                    identity_reader=lambda expected: identity(
                        ADAPTER.PROFILE.resolve_profile('audio-forward'), expected,
                        boot_id=boot_id, module_role='baseline'),
                    helper_reader=OBSERVER._expected_helpers)
        finally:
            sandbox.close()

    def test_old_stability_receipt_and_consumed_marker_are_preserved_and_refused(self):
        sandbox, boot_id, _ = self._completed_forward_initial_observation()
        clock = FakeClock()
        transport, calls, _ = self._stability_transport(
            boot_id, clock=clock, advancing_uptime=True)
        try:
            OBSERVER.observe_stability(
                'audio-forward', receipts_root=self.root / 'receipts',
                state_root=self.state_root, project_root=ROOT,
                transport=transport, clock=clock, sleeper=clock.sleep)
            paths = OBSERVER._stability_receipt_paths(
                'audio-forward', self.root / 'receipts')
            old_hash = sha(paths['result'].read_bytes())
            replay_calls = []
            with self.assertRaises(ValueError):
                OBSERVER.observe_stability(
                    'audio-forward', receipts_root=self.root / 'receipts',
                    state_root=self.state_root, project_root=ROOT,
                    transport=lambda *_args, **_kwargs: replay_calls.append('called'),
                    clock=FakeClock(), sleeper=lambda _seconds: None)
            self.assertEqual(replay_calls, [])
            self.assertEqual(sha(paths['result'].read_bytes()), old_hash)
        finally:
            sandbox.close()

        self._restart_workspace()
        sandbox, boot_id, _ = self._completed_forward_initial_observation()
        marker_id = OBSERVER._stability_marker_id('audio-forward')
        marker_kind = 'audio-coherent-forward-stability'
        with ADAPTER.GUARD.acquire_operation_lock(
                ROOT, marker_id, marker_kind, state_root=self.state_root) as operation:
            operation.begin(project_root=ROOT)
        marker_path = self.state_root / f'{marker_id}.json'
        marker_before = marker_path.read_bytes()
        paths = OBSERVER._stability_receipt_paths(
            'audio-forward', self.root / 'receipts')
        replay_calls = []
        try:
            with self.assertRaises(RuntimeError):
                OBSERVER.observe_stability(
                    'audio-forward', receipts_root=self.root / 'receipts',
                    state_root=self.state_root, project_root=ROOT,
                    transport=lambda *_args, **_kwargs: replay_calls.append('called'),
                    clock=FakeClock(), sleeper=lambda _seconds: None)
            failure = json.loads(paths['failure'].read_text())
            self.assertEqual(failure['failure_category'], 'ambiguous_marker')
            self.assertEqual(replay_calls, [])
            self.assertEqual(marker_path.read_bytes(), marker_before)
        finally:
            sandbox.close()


if __name__ == '__main__':
    unittest.main(verbosity=2)
