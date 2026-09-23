#!/usr/bin/env python3
"""Exercise the embedded remote wrapper cleanup paths without a phone."""
import importlib.util
import io
import json
from pathlib import Path
import signal
import subprocess
import sys
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    'audio_route', Path(__file__).with_name('run-audio-route-prepare-once.py'))
route = importlib.util.module_from_spec(spec)
spec.loader.exec_module(route)

ROUTES = ('ABOX SPUS OUT2', 'ABOX UAIF1 SPK')


class FakeChild:
    def __init__(self, command, timeout=False):
        self.command = command
        self.timeout = timeout
        self.returncode = None

    def communicate(self, timeout=None):
        if self.timeout and self.returncode is None:
            raise subprocess.TimeoutExpired(self.command, timeout)
        if self.returncode is None:
            self.returncode = 0
        return ('', '')

    def terminate(self):
        self.returncode = -signal.SIGTERM

    def kill(self):
        self.returncode = -signal.SIGKILL

    def poll(self):
        return self.returncode


class FakeRuntime:
    def __init__(self, fail_restore=None, child_timeout=False, interrupt_on_second_route=False):
        self.values = {name: '0' for name in ROUTES}
        self.fail_restore = fail_restore
        self.child_timeout = child_timeout
        self.interrupt_on_second_route = interrupt_on_second_route
        self.signal_handlers = {}
        self.restore_failed = False
        self.child = None

    def run(self, command, capture_output=True, text=True, timeout=5):
        if command[0] != 'amixer':
            raise AssertionError('unexpected child process command')
        if 'cget' in command:
            operation = command.index('cget')
            name = command[operation + 1].removeprefix('name=')
            value = 'off' if 'AMP Enable Switch' in name else self.values[name]
            stdout = '  : values=' + value + '\n'
            if name in ROUTES:
                stdout += "Item #0 'RESERVED'\nItem #1 'SIFS0'\n"
            return subprocess.CompletedProcess(command, 0, stdout, '')
        if 'cset' in command:
            operation = command.index('cset')
            name = command[operation + 1].removeprefix('name=')
            value = command[operation + 2]
            if value == '0' and name == self.fail_restore:
                self.restore_failed = True
                return subprocess.CompletedProcess(command, 1, '', 'synthetic restore failure')
            self.values[name] = value
            if (self.interrupt_on_second_route and value == '1'
                    and name == ROUTES[1]):
                self.signal_handlers[signal.SIGTERM](signal.SIGTERM, None)
            return subprocess.CompletedProcess(command, 0, '', '')
        raise AssertionError('unexpected amixer operation')

    def popen(self, command, stdout=None, stderr=None, text=True):
        self.child = FakeChild(command, self.child_timeout)
        return self.child

    def read_text(self, path, *args, **kwargs):
        path = str(path)
        if path == '/proc/1/comm':
            return 'native-guardian\n'
        if path == '/proc/asound/card0/pcm2p/sub0/status':
            return 'closed\n'
        raise AssertionError('unexpected file read')

    def signal(self, signum, handler):
        self.signal_handlers[signum] = handler
        return signal.SIG_DFL

    def execute(self):
        request = {'helper': 'pass', 'observer': '', 'trial_id': 'host-test'}
        stdin = io.StringIO(json.dumps(request))
        stdout = io.StringIO()
        namespace = {'__name__': '__main__'}
        def read_text(path, *args, **kwargs):
            return self.read_text(path, *args, **kwargs)
        with patch('sys.stdin', stdin), patch('sys.stdout', stdout), \
            patch('pathlib.Path.read_text', read_text), \
             patch('subprocess.run', self.run), patch('subprocess.Popen', self.popen), \
             patch('signal.signal', self.signal):
            if self.child_timeout:
                # Expire the fixed ten-second stream deadline after its start.
                calls = [0]
                def monotonic():
                    calls[0] += 1
                    return 0.0 if calls[0] == 1 else 11.0
                with patch('time.monotonic', side_effect=monotonic):
                    exec(route.WRAPPER, namespace)
            else:
                exec(route.WRAPPER, namespace)
        return json.loads(stdout.getvalue())


class WrapperCleanup(unittest.TestCase):
    def test_successful_child_restores_both_selectors_and_keeps_amp_off(self):
        fake = FakeRuntime()
        result = fake.execute()
        self.assertTrue(result['child_exited'])
        self.assertTrue(result['cleanup_verified'])
        self.assertTrue(result['amps_still_off'])
        self.assertEqual(result['after_status'], 'closed')
        self.assertTrue(all(fake.values[name] == '0' for name in ROUTES))

    def test_partial_selector_restore_is_recorded_without_false_cleanup_pass(self):
        fake = FakeRuntime(fail_restore=ROUTES[1])
        result = fake.execute()
        self.assertTrue(result['child_exited'])
        self.assertFalse(result['cleanup_verified'])
        self.assertFalse(result['restored'][ROUTES[1]]['verified'])
        self.assertIn('route_write:' + ROUTES[1], result['cleanup_errors'])

    def test_child_timeout_is_reaped_then_selectors_are_restored(self):
        fake = FakeRuntime(child_timeout=True)
        result = fake.execute()
        self.assertTrue(result['child_deadline_exceeded'])
        self.assertTrue(result['child_interrupted'])
        self.assertTrue(result['child_exited'])
        self.assertTrue(result['cleanup_verified'])
        self.assertTrue(all(fake.values[name] == '0' for name in ROUTES))

    def test_signal_before_child_start_restores_after_closed_pcm_check(self):
        fake = FakeRuntime(interrupt_on_second_route=True)
        result = fake.execute()
        self.assertTrue(result['wrapper_interrupted'])
        self.assertIsNone(fake.child)
        self.assertEqual(result['pcm_status_before_restore'], 'closed')
        self.assertTrue(result['cleanup_verified'])
        self.assertTrue(all(fake.values[name] == '0' for name in ROUTES))


if __name__ == '__main__':
    unittest.main()
