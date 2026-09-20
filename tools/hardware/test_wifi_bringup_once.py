#!/usr/bin/env python3
"""Mocked host tests for the future one-shot Wi-Fi harness."""
import fcntl
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

SOURCE = Path(__file__).with_name('wifi-bringup-once.py')
spec = importlib.util.spec_from_file_location('bringup', SOURCE)
mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)


class BringupTests(unittest.TestCase):
    def test_calibration_requires_fresh_success_marker(self):
        before = 'Calibration completed successfully\n'
        self.assertFalse(mod.calibration_success_since(before, before))
        self.assertTrue(mod.calibration_success_since(before, before + before))

    def test_responder_requires_exact_metadata_and_command(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); pid = 4242
            proc = root / str(pid); proc.mkdir()
            script = root / 'wifi-optional-firmware.py'; script.write_text('# exact')
            (proc / 'cmdline').write_bytes(b'/usr/bin/python3\0' + str(script).encode() + b'\0--serve\0--acknowledge\0')
            (proc / 'status').write_text('Name:\tresponder\nUid:\t0\t0\t0\t0\n')
            meta = root / 'meta'; meta.write_text(json.dumps({
                'acknowledge': True, 'owner': '4242:x', 'pid': pid,
                'ready': True, 'request': mod.OPTIONAL_NAME}) + '\n')
            lock = root / 'lock'; lock.touch()
            with lock.open('r') as held:
                fcntl.flock(held.fileno(), fcntl.LOCK_EX)
                with mock.patch.object(mod, 'RESPONDER', script), \
                     mock.patch.object(mod, 'RESPONDER_SHA', mod.digest(script)), \
                     mock.patch.object(mod, 'RESPONDER_META', meta), \
                     mock.patch.object(mod, 'RESPONDER_LOCK', lock):
                    self.assertTrue(mod.responder_ready(root)['ready'])

    def test_responder_rejects_wrong_request(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); meta = root / 'meta'
            script = root / 'script'; script.write_text('# exact')
            meta.write_text(json.dumps({'acknowledge': True, 'pid': 2,
                                        'ready': True, 'request': 'other'}) + '\n')
            with mock.patch.object(mod, 'RESPONDER', script), \
                 mock.patch.object(mod, 'RESPONDER_SHA', mod.digest(script)), \
                 mock.patch.object(mod, 'RESPONDER_META', meta):
                with self.assertRaisesRegex(RuntimeError, 'metadata'):
                    mod.responder_ready(root)

    def test_responder_rejects_wrong_hash(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); script = root / 'script'; script.write_text('# exact')
            meta = root / 'meta'; meta.write_text('{}\n')
            with mock.patch.object(mod, 'RESPONDER', script), \
                 mock.patch.object(mod, 'RESPONDER_SHA', '0' * 64), \
                 mock.patch.object(mod, 'RESPONDER_META', meta):
                with self.assertRaisesRegex(RuntimeError, 'hash'):
                    mod.responder_ready(root)

    def test_responder_rejects_wrong_command(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); pid = 4242; proc = root / str(pid); proc.mkdir()
            script = root / 'script'; script.write_text('# exact')
            (proc / 'cmdline').write_bytes(b'/usr/bin/python3\0' + str(script).encode() + b'\0--watch\0')
            (proc / 'status').write_text('Uid:\t0\t0\t0\t0\n')
            meta = root / 'meta'; meta.write_text(json.dumps({'acknowledge': True,
                'owner': '4242:x', 'pid': pid, 'ready': True,
                'request': mod.OPTIONAL_NAME}) + '\n')
            lock = root / 'lock'; lock.touch()
            with lock.open('r') as held:
                fcntl.flock(held.fileno(), fcntl.LOCK_EX)
                with mock.patch.object(mod, 'RESPONDER', script), \
                     mock.patch.object(mod, 'RESPONDER_SHA', mod.digest(script)), \
                     mock.patch.object(mod, 'RESPONDER_META', meta), \
                     mock.patch.object(mod, 'RESPONDER_LOCK', lock):
                    with self.assertRaisesRegex(RuntimeError, 'command'):
                        mod.responder_ready(root)

    def test_responder_rejects_unlocked_lock(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); pid = 4242; proc = root / str(pid); proc.mkdir()
            script = root / 'script'; script.write_text('# exact')
            (proc / 'cmdline').write_bytes(b'/usr/bin/python3\0' + str(script).encode() + b'\0--serve\0--acknowledge\0')
            (proc / 'status').write_text('Uid:\t0\t0\t0\t0\n')
            meta = root / 'meta'; meta.write_text(json.dumps({'acknowledge': True,
                'owner': '4242:x', 'pid': pid, 'ready': True,
                'request': mod.OPTIONAL_NAME}) + '\n')
            lock = root / 'lock'; lock.touch()
            with mock.patch.object(mod, 'RESPONDER', script), \
                 mock.patch.object(mod, 'RESPONDER_SHA', mod.digest(script)), \
                 mock.patch.object(mod, 'RESPONDER_META', meta), \
                 mock.patch.object(mod, 'RESPONDER_LOCK', lock):
                with self.assertRaisesRegex(RuntimeError, 'not held'):
                    mod.responder_ready(root)


if __name__ == '__main__': unittest.main()
