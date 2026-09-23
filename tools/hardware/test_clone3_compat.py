#!/usr/bin/env python3
"""Host-only real filter/fallback tests. Never execute a phone test here."""
import concurrent.futures
import ctypes
import errno
import fcntl
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
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

    def test_captured_output_subprocess_fallback(self):
        # The close_range-only filter must survive exec and let libc/Python
        # fall back to individual closes when subprocess captures both pipes.
        child = (
            'import subprocess,sys; '
            'p=subprocess.run([sys.executable,"-c",'
            '"import sys; print(\'CAPTURED_STDOUT\'); '
            'print(\'CAPTURED_STDERR\',file=sys.stderr)"],'
            'capture_output=True,text=True,check=True,timeout=5); '
            'assert p.stdout=="CAPTURED_STDOUT\\n",repr(p.stdout); '
            'assert p.stderr=="CAPTURED_STDERR\\n",repr(p.stderr); '
            'print("CAPTURED_SUBPROCESS_OK")'
        )
        result = self.run_case('--', sys.executable, '-c', child)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('CAPTURED_SUBPROCESS_OK', result.stdout)

    def test_concurrent_repeated_child_cleanup(self):
        # Exercise overlapping fork/exec/wait paths and ensure every captured
        # child completes and is reaped, rather than just testing one spawn.
        child = r'''
import concurrent.futures, subprocess, sys

def one(index):
    code = "import sys; print('OUT-' + sys.argv[1]); print('ERR-' + sys.argv[1], file=sys.stderr)"
    result = subprocess.run(
        [sys.executable, "-c", code, str(index)],
        capture_output=True, text=True, check=True, timeout=8)
    assert result.stdout == f"OUT-{index}\n", repr(result.stdout)
    assert result.stderr == f"ERR-{index}\n", repr(result.stderr)
    return index

with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
    completed = list(pool.map(one, range(48)))
assert completed == list(range(48)), completed
print("REAPED_CHILDREN=48")
'''
        result = self.run_case('--', sys.executable, '-c', child)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('REAPED_CHILDREN=48', result.stdout)


class NativeCloseRangeTests(unittest.TestCase):
    """Exercise the host kernel's native close_range syscall semantics."""

    CLOSE_RANGE_UNSHARE = 1 << 1
    CLOSE_RANGE_CLOEXEC = 1 << 2

    @classmethod
    def setUpClass(cls):
        if not sys.platform.startswith('linux'):
            raise unittest.SkipTest('native close_range tests require Linux')
        cls.libc = ctypes.CDLL(None, use_errno=True)
        cls.close_range = getattr(cls.libc, 'close_range', None)
        if cls.close_range is None:
            raise unittest.SkipTest('host libc does not export close_range')
        cls.close_range.argtypes = [ctypes.c_uint, ctypes.c_uint, ctypes.c_int]
        cls.close_range.restype = ctypes.c_int

    @classmethod
    def call_close_range(cls, first, last, flags=0):
        ctypes.set_errno(0)
        result = cls.close_range(first, last, flags)
        return result, ctypes.get_errno()

    @staticmethod
    def assert_fd_open(test, fd):
        test.assertGreaterEqual(fcntl.fcntl(fd, fcntl.F_GETFD), 0)

    @staticmethod
    def assert_fd_closed(test, fd):
        with test.assertRaises(OSError) as raised:
            fcntl.fcntl(fd, fcntl.F_GETFD)
        test.assertEqual(raised.exception.errno, errno.EBADF)

    def descriptor_probe(self, fd):
        code = '''
import errno, fcntl, sys
fd = int(sys.argv[1])
try:
    fcntl.fcntl(fd, fcntl.F_GETFD)
except OSError as exc:
    if exc.errno == errno.EBADF:
        print("CLOSED")
        sys.exit(0)
    raise
print("OPEN")
sys.exit(1)
'''
        return subprocess.run(
            [sys.executable, '-c', code, str(fd)], close_fds=False,
            capture_output=True, text=True, timeout=8)

    def test_normal_close_range_closes_only_selected_descriptor(self):
        fds = [os.open(os.devnull, os.O_RDONLY) for _ in range(3)]
        try:
            before, target, after = fds
            result, error = self.call_close_range(target, target, 0)
            if result == -1 and error == errno.ENOSYS:
                self.skipTest('host kernel lacks close_range')
            self.assertEqual((result, error), (0, 0))
            self.assert_fd_open(self, before)
            self.assert_fd_closed(self, target)
            self.assert_fd_open(self, after)
        finally:
            for fd in fds:
                try:
                    os.close(fd)
                except OSError as exc:
                    if exc.errno != errno.EBADF:
                        raise

    def test_cloexec_flag_controls_exec_inheritance(self):
        fd = os.open(os.devnull, os.O_RDONLY)
        try:
            os.set_inheritable(fd, True)
            inherited = self.descriptor_probe(fd)
            self.assertEqual(inherited.returncode, 1, inherited.stderr)
            self.assertEqual(inherited.stdout.strip(), 'OPEN')

            result, error = self.call_close_range(fd, fd, self.CLOSE_RANGE_CLOEXEC)
            if result == -1 and error in (errno.ENOSYS, errno.EINVAL):
                self.skipTest('host kernel lacks CLOSE_RANGE_CLOEXEC support')
            self.assertEqual((result, error), (0, 0))
            self.assertTrue(fcntl.fcntl(fd, fcntl.F_GETFD) & fcntl.FD_CLOEXEC)

            after_exec = self.descriptor_probe(fd)
            self.assertEqual(after_exec.returncode, 0, after_exec.stderr)
            self.assertEqual(after_exec.stdout.strip(), 'CLOSED')
        finally:
            try:
                os.close(fd)
            except OSError as exc:
                if exc.errno != errno.EBADF:
                    raise

    def test_invalid_bounds_and_flags_fail_without_closing_fd(self):
        fd = os.open(os.devnull, os.O_RDONLY)
        try:
            result, error = self.call_close_range(fd + 1, fd, 0)
            self.assertEqual((result, error), (-1, errno.EINVAL))
            self.assert_fd_open(self, fd)

            result, error = self.call_close_range(fd, fd, 1 << 30)
            self.assertEqual((result, error), (-1, errno.EINVAL))
            self.assert_fd_open(self, fd)
        finally:
            os.close(fd)

    def test_unshare_flag_isolates_concurrent_thread_fd_table(self):
        fd = os.open(os.devnull, os.O_RDONLY)
        entered = threading.Event()
        inspect = threading.Event()
        observed = {}

        def observer():
            entered.set()
            if not inspect.wait(8):
                observed['error'] = 'inspection timed out'
                return
            try:
                fcntl.fcntl(fd, fcntl.F_GETFD)
                observed['open'] = True
            except OSError as exc:
                observed['open'] = False
                observed['errno'] = exc.errno

        thread = threading.Thread(target=observer)
        thread.start()
        try:
            self.assertTrue(entered.wait(8), 'observer thread did not start')
            result, error = self.call_close_range(fd, fd, self.CLOSE_RANGE_UNSHARE)
            if result == -1 and error in (errno.ENOSYS, errno.EINVAL):
                self.skipTest('host kernel lacks CLOSE_RANGE_UNSHARE support')
            self.assertEqual((result, error), (0, 0))
            self.assert_fd_closed(self, fd)
            inspect.set()
            thread.join(timeout=8)
            self.assertFalse(thread.is_alive(), 'observer thread did not finish')
            self.assertEqual(observed.get('open'), True, observed)
        finally:
            inspect.set()
            thread.join(timeout=8)
            try:
                os.close(fd)
            except OSError as exc:
                if exc.errno != errno.EBADF:
                    raise


if __name__ == '__main__':
    unittest.main()
