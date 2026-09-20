#!/usr/bin/env python3
import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock


def load(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


host = load('provision-active-wifi')
phone = load('wifi-install-profile')


class ProvisionTests(unittest.TestCase):
    def test_wpa_reference_vector_and_saved_hex(self):
        self.assertEqual(host.derive_psk('password', 'IEEE'),
                         'f42c6fc52df0ebef9ebb4b90b38a5f902e83fe1b135a70e23aed762e9710a12e')
        self.assertEqual(host.derive_psk('AB' * 32, 'ignored'), 'ab' * 32)
        for bad in ('', 'short', 'z' * 64):
            with self.assertRaises(RuntimeError):
                host.derive_psk(bad, 'fixture')

    def test_metadata_never_reads_secret_or_prints_identity(self):
        def query(*args, **kwargs):
            self.assertFalse(kwargs.get('secrets', False))
            if args[:2] == ('-f', 'UUID,TYPE'):
                return 'fixture-uuid:802-11-wireless'
            return {'802-11-wireless.ssid': 'Private Fixture',
                    '802-11-wireless-security.key-mgmt': 'wpa-psk'}[args[1]]
        with tempfile.TemporaryDirectory() as directory:
            scan = Path(directory) / 'scan.txt'
            scan.write_text('BSS redacted\n\tSSID: Private Fixture\n')
            output = io.StringIO()
            with mock.patch.object(host, 'nm', side_effect=query), \
                 mock.patch.object(host.subprocess, 'run') as run, contextlib.redirect_stdout(output):
                host.provision(scan)
            run.assert_not_called()
            self.assertNotIn('Private Fixture', output.getvalue())
            self.assertNotIn('fixture-uuid', output.getvalue())

    def test_invalid_payloads_leave_no_profile(self):
        invalid = [None, [], {}, {'ssid_hex': 'zz', 'psk_hex': '00' * 32},
                   {'ssid_hex': '00\n0', 'psk_hex': '00' * 32},
                   {'ssid_hex': '00', 'psk_hex': '0' * 63 + '\n'}]
        with tempfile.TemporaryDirectory() as directory:
            dest = Path(directory) / 'private' / 'profile'
            with mock.patch.object(phone, 'DEST', dest):
                for payload in invalid:
                    with self.subTest(payload=payload), self.assertRaises(SystemExit):
                        phone.install(payload)
                    self.assertFalse(dest.exists())

    def test_private_write_readback_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            dest = Path(directory) / 'private' / 'profile'
            payload = {'ssid_hex': 'Fixture'.encode().hex(), 'psk_hex': '00' * 32}
            with mock.patch.object(phone, 'DEST', dest), contextlib.redirect_stdout(io.StringIO()):
                phone.install(payload)
                before = dest.read_bytes()
                with self.assertRaises(SystemExit):
                    phone.install(payload)
            self.assertEqual(dest.read_bytes(), before)
            self.assertEqual(dest.stat().st_mode & 0o777, 0o600)
            self.assertEqual(dest.parent.stat().st_mode & 0o777, 0o700)


if __name__ == '__main__':
    unittest.main()
