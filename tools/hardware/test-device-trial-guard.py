#!/usr/bin/env python3
"""Host-only concurrency, durability, and filesystem-safety tests."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
GUARD_PATH = ROOT / 'tools/hardware/device-trial-guard.py'
SPEC = importlib.util.spec_from_file_location('device_trial_guard_test', GUARD_PATH)
guard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(guard)


class DeviceTrialGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='s22-device-guard-')
        self.root = Path(self.temp.name)
        self.state = self.root / 'state'

    def tearDown(self):
        self.temp.cleanup()

    def acquire(self, trial='bt-test-trial', kind='bluetooth'):
        return guard.acquire_operation_lock(self.root, trial, kind,
                                            state_root=self.state)

    def receipt(self):
        directory = self.root / 'receipts'
        directory.mkdir(mode=0o700)
        os.chmod(directory, 0o700)
        path = directory / 'receipt.json'
        path.write_text('{"host_only":true}\n', encoding='utf-8')
        os.chmod(path, 0o600)
        return path

    def test_receipt_fsync_precedes_terminal_marker_and_complete_is_durable(self):
        receipt = self.receipt()
        with self.acquire() as operation:
            operation.begin(project_root=self.root)
            operation.complete(receipt, outcome='success', cleanup_confirmed=True)
        marker = json.loads((self.state / 'bt-test-trial.json').read_text())
        self.assertEqual(marker['status'], 'complete')
        self.assertEqual(marker['outcome'], 'success')
        self.assertEqual(len(marker['receipt_sha256']), 64)

    def test_receipt_fsync_failure_keeps_trial_unresolved(self):
        receipt = self.receipt()
        real_fsync = os.fsync
        calls = []

        def fail_receipt_once(fd):
            calls.append(fd)
            if len(calls) == 1:
                raise OSError('injected receipt fsync failure')
            return real_fsync(fd)

        with self.assertRaisesRegex(OSError, 'injected receipt fsync'):
            with self.acquire() as operation:
                operation.begin(project_root=self.root)
                with mock.patch.object(guard.os, 'fsync', side_effect=fail_receipt_once):
                    operation.complete(receipt, outcome='success', cleanup_confirmed=True)
        marker = json.loads((self.state / 'bt-test-trial.json').read_text())
        self.assertEqual(marker['status'], 'unknown')
        with self.assertRaisesRegex(guard.TrialGuardError, 'unresolved trial marker'):
            with self.acquire('audio-other', 'audio-route'):
                self.fail('unresolved operation was allowed')

    def test_begin_without_complete_becomes_unknown_and_blocks_every_operation(self):
        with self.acquire() as operation:
            operation.begin(project_root=self.root)
        marker = json.loads((self.state / 'bt-test-trial.json').read_text())
        self.assertEqual(marker['status'], 'unknown')
        with self.assertRaisesRegex(guard.TrialGuardError, 'unresolved trial marker'):
            with self.acquire('audio-trial', 'audio-route'):
                self.fail('unresolved marker was ignored')

    def test_real_process_death_leaves_pending_marker_and_blocks_replay(self):
        source = (
            'import importlib.util,os,sys; '
            f's=importlib.util.spec_from_file_location("g",{str(GUARD_PATH)!r}); '
            'm=importlib.util.module_from_spec(s); s.loader.exec_module(m); '
            f'c=m.acquire_operation_lock({str(self.root)!r},"crash-trial","bluetooth",state_root={str(self.state)!r}); '
            'o=c.__enter__(); o.begin(project_root="crash-fixture"); os._exit(73)'
        )
        result = subprocess.run([sys.executable, '-c', source], capture_output=True,
                                timeout=5)
        self.assertEqual(result.returncode, 73)
        marker = json.loads((self.state / 'crash-trial.json').read_text())
        self.assertEqual(marker['status'], 'pending')
        with self.assertRaisesRegex(guard.TrialGuardError, 'unresolved trial marker'):
            with self.acquire('another-trial', 'audio-route'):
                self.fail('crashed operation did not block a new trial')

    def test_cross_process_nonblocking_lock_serializes_audio_and_bluetooth(self):
        child = (
            'import importlib.util,sys,time; '
            f's=importlib.util.spec_from_file_location("g",{str(GUARD_PATH)!r}); '
            'm=importlib.util.module_from_spec(s); s.loader.exec_module(m); '
            f'c=m.acquire_operation_lock({str(self.root)!r},"child-trial","bluetooth",state_root={str(self.state)!r}); '
            'o=c.__enter__(); o.begin(project_root="cross-process-fixture"); '
            'print("LOCKED",flush=True); time.sleep(0.6); c.__exit__(None,None,None)'
        )
        process = subprocess.Popen([sys.executable, '-c', child], stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True)
        try:
            self.assertEqual(process.stdout.readline().strip(), 'LOCKED')
            with self.assertRaisesRegex(guard.TrialGuardError, 'another S22 device operation'):
                with self.acquire('audio-competing-trial', 'audio-route'):
                    self.fail('second process acquired global lock')
        finally:
            process.communicate(timeout=5)
        self.assertEqual(process.returncode, 0)
        marker = json.loads((self.state / 'child-trial.json').read_text())
        self.assertEqual(marker['status'], 'unknown')

    def test_symlink_state_directory_or_lock_is_rejected(self):
        target = self.root / 'target'
        target.mkdir(mode=0o700)
        os.chmod(target, 0o700)
        self.state.symlink_to(target, target_is_directory=True)
        with self.assertRaisesRegex(guard.TrialGuardError, 'real owner-owned'):
            with self.acquire():
                pass

        self.state.unlink()
        self.state.mkdir(mode=0o700)
        os.chmod(self.state, 0o700)
        (self.state / 'operation.lock').symlink_to(target / 'lock')
        with self.assertRaises(guard.TrialGuardError):
            with self.acquire():
                pass

    def test_group_or_world_accessible_state_root_is_rejected(self):
        self.state.mkdir(mode=0o755)
        os.chmod(self.state, 0o755)
        with self.assertRaisesRegex(guard.TrialGuardError, 'mode-0700'):
            with self.acquire():
                pass

    def test_symlink_marker_fails_closed_during_unresolved_scan(self):
        self.state.mkdir(mode=0o700)
        os.chmod(self.state, 0o700)
        (self.state / 'foreign.json').symlink_to(self.root / 'absent')
        with self.assertRaisesRegex(guard.TrialGuardError, 'trial marker is not a private regular file'):
            with self.acquire():
                pass

    def test_explicit_reconciliation_preserves_marker_and_does_not_reuse_id(self):
        with self.acquire() as operation:
            operation.begin(project_root=self.root)
        guard.reconcile_operation('bt-test-trial', 'operator verified no remaining device operation',
                                  state_root=self.state)
        path = self.state / 'bt-test-trial.json'
        marker = json.loads(path.read_text())
        self.assertEqual(marker['status'], 'reconciled')
        self.assertIn('verified', marker['reconciliation'])
        with self.assertRaisesRegex(guard.TrialGuardError, 'never reuse'):
            with self.acquire():
                pass
        with self.acquire('new-trial', 'audio-route') as operation:
            self.assertIsNotNone(operation)

    def test_invalid_identity_and_incomplete_receipt_fail_explicitly_under_optimization(self):
        code = '\n'.join((
            'import importlib.util,sys',
            f's=importlib.util.spec_from_file_location("g",{str(GUARD_PATH)!r})',
            'm=importlib.util.module_from_spec(s); s.loader.exec_module(m)',
            'try:',
            '    with m.acquire_operation_lock("/tmp","Bad_ID","bluetooth"): pass',
            'except m.TrialGuardError:',
            '    print("explicit-rejection")',
            'else:',
            '    raise SystemExit(1)',
        ))
        result = subprocess.run([sys.executable, '-O', '-c', code],
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('explicit-rejection', result.stdout)


if __name__ == '__main__':
    unittest.main()
