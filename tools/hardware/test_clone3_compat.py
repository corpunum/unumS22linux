#!/usr/bin/env python3
"""Host-only real filter/fallback tests. Never execute a phone test here."""
from pathlib import Path
import subprocess
import tempfile
import unittest

SOURCE = Path(__file__).with_name('clone3-compat.c')


class CompatTests(unittest.TestCase):
    source = SOURCE
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='s22-clone3-host-')
        cls.binary = str(Path(cls.tmp.name) / 'clone3-compat')
        subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror', '-O2',
                        str(cls.source), '-o', cls.binary], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_case(self, *args):
        return subprocess.run([self.binary, *args], capture_output=True, text=True, timeout=10)

    def test_self_test(self):
        result = self.run_case('--self-test')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('=ENOSYS', result.stdout)

    def test_spawn_fallback(self):
        code = ('import os,subprocess; '
                'p=os.posix_spawn("/bin/true",["true"],os.environ); '
                'assert os.waitpid(p,0)[1]==0; '
                'subprocess.run(["/bin/true"],check=True); print("SPAWN_OK")')
        result = self.run_case('--', 'python3', '-c', code)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('SPAWN_OK', result.stdout)

    def test_exit_status(self):
        self.assertEqual(self.run_case('--', '/bin/sh', '-c', 'exit 17').returncode, 17)

    def test_missing_command(self):
        self.assertEqual(self.run_case('--').returncode, 2)
        self.assertEqual(self.run_case('--', '/s22/no-such-command').returncode, 5)


class CloseRangeCompatTests(CompatTests):
    source = SOURCE.with_name('close-range-compat.c')


if __name__ == '__main__':
    unittest.main()
