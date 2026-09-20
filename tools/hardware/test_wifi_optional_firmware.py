#!/usr/bin/env python3
"""Portable mocked tests for the exact optional-firmware handler."""
import fcntl
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest import mock

SOURCE = Path(__file__).with_name('wifi-optional-firmware.py')
spec = importlib.util.spec_from_file_location('optional_fw', SOURCE)
mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)


class OptionalFirmwareTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); root = Path(self.tmp.name)
        self.request = root / 'request'; self.request.mkdir()
        self.owner = Path('/sys/devices/platform/qcom,cnss-qca6490/node')
        self.pid1 = root / 'pid1'; self.pid1.mkdir()
        self.search = root / 'firmware_path'; self.search.write_text('/vendor/firmware\n')
        self.common = [
            mock.patch.object(mod, 'REQUEST', self.request),
            mock.patch.object(mod, 'PID1_ROOT', self.pid1),
            mock.patch.object(mod, 'FIRMWARE_SEARCH_PATH', self.search),
            mock.patch.object(mod.os, 'uname', return_value=type('U', (), {'release': '5.10.260-g4e5c5ad7d950'})()),
        ]
        for p in self.common: p.start()

    def tearDown(self):
        for p in reversed(self.common): p.stop()
        self.tmp.cleanup()

    def write_uevent(self, firmware=mod.NAME, async_value='0'):
        (self.request / 'uevent').write_text(f'FIRMWARE={firmware}\nASYNC={async_value}\n')

    def request_proxy(self, exists=True, owner=None):
        backing = self.request
        class Proxy:
            def exists(self): return exists
            def resolve(self, strict=True): return owner or self_owner
            def __truediv__(self, name): return backing / name
        self_owner = self.owner
        return Proxy()

    def test_absent_is_not_pending(self):
        with mock.patch.object(mod, 'REQUEST', self.request_proxy(False)):
            self.assertFalse(mod.handle()['pending'])

    def test_exact_owner_and_name_are_accepted(self):
        self.write_uevent()
        with mock.patch.object(mod, 'REQUEST', self.request_proxy()): result = mod.handle(False)
        self.assertTrue(result['pending']); self.assertFalse(result['acknowledged_missing'])

    def test_wrong_owner_rejected(self):
        self.write_uevent()
        with mock.patch.object(mod, 'REQUEST', self.request_proxy(owner=Path('/wrong-owner'))):
            with self.assertRaisesRegex(RuntimeError, 'owner'): mod.handle()

    def test_nonmatching_async_rejected(self):
        self.write_uevent(async_value='1')
        with mock.patch.object(mod, 'REQUEST', self.request_proxy()):
            with self.assertRaisesRegex(RuntimeError, 'identity'): mod.handle()

    def test_present_firmware_rejected(self):
        self.write_uevent(); path = self.pid1 / 'vendor/firmware' / mod.NAME
        path.parent.mkdir(parents=True); path.write_text('fixture')
        with mock.patch.object(mod, 'REQUEST', self.request_proxy()):
            with self.assertRaisesRegex(RuntimeError, 'actually present'): mod.handle()

    def test_inspect_does_not_write_loading(self):
        self.write_uevent()
        with mock.patch.object(Path, 'write_text', side_effect=AssertionError('write')):
            with mock.patch.object(mod, 'REQUEST', self.request_proxy()): mod.handle(False)

    def test_serve_requires_ack(self):
        with self.assertRaises(ValueError): mod.serve(False, lock_path=self.pid1/'l', meta_path=self.pid1/'m')

    def test_serve_lock_rejects_second_owner(self):
        lock_path = self.pid1 / 'lock'; lock_path.touch()
        with lock_path.open('a+') as held:
            fcntl.flock(held.fileno(), fcntl.LOCK_EX)
            with self.assertRaisesRegex(RuntimeError, 'already owned'):
                mod.serve(True, lock_path=lock_path, meta_path=self.pid1/'meta')


if __name__ == '__main__': unittest.main()
