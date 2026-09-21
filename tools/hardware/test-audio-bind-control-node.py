#!/usr/bin/env python3
"""Host-only identity/permission checks. No mount or real device opens."""
import importlib.util
import os
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

spec = importlib.util.spec_from_file_location('audio_bind', Path(__file__).with_name('audio-bind-control-node.py'))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class IdentityTests(unittest.TestCase):
    def test_permissions(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            os.chmod(p, 0o755)
            if os.geteuid() == 0:
                mod.checked_directory(p)
            os.chmod(p, 0o777)
            with self.assertRaises(RuntimeError):
                mod.checked_directory(p)

    def test_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)/'link'; p.symlink_to(tmp)
            with self.assertRaises(RuntimeError):
                mod.checked_directory(p)

    def test_audio_group_writable_node_allowed(self):
        info = SimpleNamespace(st_mode=stat.S_IFCHR | 0o660, st_rdev=os.makedev(116,114), st_uid=0)
        path = Mock(); path.lstat.return_value=info
        self.assertIs(mod.checked_node(path, info.st_rdev), info)
        for mode in (stat.S_IFCHR | 0o666, stat.S_IFREG | 0o600, stat.S_IFLNK | 0o600):
            info.st_mode=mode
            with self.assertRaises(RuntimeError):mod.checked_node(path, info.st_rdev)

    def test_wrong_device_or_owner_rejected(self):
        path=Mock()
        for dev, uid in ((os.makedev(116,115),0),(os.makedev(116,114),1000)):
            path.lstat.return_value=SimpleNamespace(st_mode=stat.S_IFCHR | 0o600,st_rdev=dev,st_uid=uid)
            with self.assertRaises(RuntimeError):mod.checked_node(path,os.makedev(116,114))


if __name__ == '__main__':unittest.main()
