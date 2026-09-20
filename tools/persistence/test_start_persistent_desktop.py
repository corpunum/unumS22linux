#!/usr/bin/env python3
"""Host-only unit checks for start-persistent-desktop.py.

These tests deliberately replace every mount/device/process boundary.  They
must remain runnable on a development host and must never invoke --check or
--foreground against the real phone/root filesystem.
"""
import importlib.util
import json
import os
import signal
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SOURCE = Path(__file__).with_name("start-persistent-desktop.py")
SPEC = importlib.util.spec_from_file_location("persistent_desktop_under_test", SOURCE)
MOD = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MOD)


class SupervisorUnitTests(unittest.TestCase):
    def test_bind_file_creates_regular_placeholder_and_mounts(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src = root / "null"
            dst = root / "candidate" / "dev" / "null"
            src.write_bytes(b"")
            calls = []
            with mock.patch.object(MOD, "is_mounted", return_value=False), \
                 mock.patch.object(MOD, "command", side_effect=lambda *a, **k: calls.append(a)):
                MOD.bind_file(src, dst)
            self.assertTrue(dst.is_file())
            self.assertEqual(calls, [("mount", "-o", "bind", str(src), str(dst))])

    def test_health_rejects_non_ok_json(self):
        class Response:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self): return json.dumps({"status": "loading"}).encode()
        opener = mock.Mock()
        opener.open.return_value = Response()
        with mock.patch.object(MOD.urllib.request, "build_opener", return_value=opener):
            self.assertFalse(MOD.healthy("http://127.0.0.1:8089/health"))

    def test_health_accepts_only_status_ok(self):
        class Response:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self): return b'{"status":"ok"}'
        opener = mock.Mock()
        opener.open.return_value = Response()
        with mock.patch.object(MOD.urllib.request, "build_opener", return_value=opener):
            self.assertTrue(MOD.healthy("http://127.0.0.1:8089/health"))

    def test_terminate_targets_only_owned_process_group(self):
        class Proc:
            pid = 4242
            def wait(self, timeout): return 0
        with mock.patch.object(MOD.os, "killpg") as killpg:
            MOD.terminate(Proc())
        self.assertEqual(killpg.call_args_list,
                         [mock.call(4242, signal.SIGTERM),
                          mock.call(4242, signal.SIGKILL)])

    def test_preflight_rejects_wrong_uuid_before_mount(self):
        mount = Path(tempfile.mkdtemp())
        with mock.patch.object(MOD, "MOUNT", mount), \
             mock.patch.object(MOD, "DEVICE", "/dev/block/by-name/userdata"), \
             mock.patch.object(MOD.Path, "read_text", return_value="native-guardian\n"), \
             mock.patch.object(MOD.os.path, "exists", return_value=True), \
             mock.patch.object(MOD.os.path, "islink", return_value=True), \
             mock.patch.object(MOD.os.path, "realpath", return_value="/dev/sda36"), \
             mock.patch.object(MOD, "stat_device", return_value=(259, 20)), \
             mock.patch.object(MOD, "partition_size_sectors", return_value=MOD.EXPECTED_SECTORS), \
             mock.patch.object(MOD, "filesystem_uuid", return_value="00000000-0000-0000-0000-000000000000"), \
             self.assertRaises(MOD.Failure):
            MOD.preflight()


if __name__ == "__main__":
    unittest.main()
