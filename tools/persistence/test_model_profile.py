#!/usr/bin/env python3
"""Host-only tests for the bounded persistent-desktop model profiles."""
import importlib.util
import socket
import tempfile
import unittest
import json
from pathlib import Path
from unittest import mock


SOURCE = Path(__file__).with_name("start-persistent-desktop.py")
SPEC = importlib.util.spec_from_file_location("persistent_desktop_model_profile", SOURCE)
MOD = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MOD)


class ModelProfileTests(unittest.TestCase):
    def test_absent_selector_defaults_to_2b_and_keeps_command(self):
        with tempfile.TemporaryDirectory() as td:
            profile = MOD.selected_model_profile(Path(td) / "missing")
        self.assertEqual(profile, "qwen2b")
        self.assertEqual(MOD.model_id(profile), MOD.QWEN2B_MODEL_ID)
        self.assertNotIn("-b", MOD.model_command(profile))
        self.assertNotIn("-ub", MOD.model_command(profile))
        self.assertNotIn("--cache-ram", MOD.model_command(profile))

    def test_qwen4b_and_alias_use_exact_cpu_runtime_args(self):
        for value in ("qwen4b", "s22-qwen4b"):
            with tempfile.TemporaryDirectory() as td:
                selector = Path(td) / "profile"
                selector.write_text(value + "\n")
                profile = MOD.selected_model_profile(selector)
            self.assertEqual(profile, "qwen4b")
            command = MOD.model_command(profile)
            self.assertIn(MOD.QWEN4B_MODEL_ID, command)
            self.assertEqual(command[-6:], ["-b", "64", "-ub", "32", "--cache-ram", "0"])
            self.assertIn("-ngl", command)
            self.assertEqual(command[command.index("-ngl") + 1], "0")
            self.assertEqual(command[command.index("--host") + 1], "127.0.0.1")
            self.assertEqual(command[command.index("--port") + 1], "8089")
            self.assertEqual(command[command.index("-n") + 1], "20")

    def test_unknown_and_empty_profiles_are_rejected(self):
        for value in ("bad", "", "qwen3b"):
            with tempfile.TemporaryDirectory() as td:
                selector = Path(td) / "profile"
                selector.write_text(value)
                with self.assertRaises(MOD.Failure):
                    MOD.selected_model_profile(selector)

    def test_hash_helper_and_4b_hash_mismatch_are_detectable(self):
        with tempfile.NamedTemporaryFile() as file:
            file.write(b"profile fixture")
            file.flush()
            self.assertNotEqual(MOD.sha256_file(Path(file.name)), MOD.QWEN4B_SHA256)

    def test_existing_port_blocks_second_model_before_spawn(self):
        log = Path(tempfile.mkdtemp()) / "model.log"
        occupied = mock.MagicMock()
        with mock.patch.object(MOD.socket, "create_connection", return_value=occupied), \
             mock.patch.object(MOD.subprocess, "Popen") as popen:
            with self.assertRaises(MOD.Failure):
                MOD.start_model(log, retries=1, profile="qwen4b")
        popen.assert_not_called()

    def test_preflight_checks_real_artifact_paths_and_both_models(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            arch, models = root / 'arch', root / 'model-bench'
            expected = [models / 'models' / Path(MOD.QWEN4B_MODEL_ID).name,
                        models / 'models/Qwen3.5-2B-Q4_0.gguf',
                        models / 'server/bin/llama-server', arch / 'usr/local/bin/s22-chat']
            for path in expected:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch()
            marker = root / '.persistent-ready.json'
            marker.write_text(json.dumps(dict(schema='s22-persistent-v1', uuid=MOD.UUID,
                **{k: 'a' * 64 for k in ['arch_archive_sha256', 'model_sha256', 'server_sha256', 'chat_sha256']})))
            real_read = Path.read_text
            def read(path, *a, **kw):
                return {'/proc/1/comm': 'native-guardian',
                        '/sys/class/block/sda36/uevent': 'PARTNAME=userdata',
                        '/sys/class/block/sda36/start': '28594176'}.get(str(path), '') or real_read(path, *a, **kw)
            real_realpath = MOD.os.path.realpath
            def realpath(path):
                return '/dev/sda36' if str(path) == MOD.DEVICE else real_realpath(path)
            with mock.patch.object(MOD, 'MOUNT', root), mock.patch.object(MOD, 'ARCH', arch), \
                 mock.patch.object(MOD, 'MODEL', models), mock.patch.object(MOD, 'DEPLOYMENT', marker), \
                 mock.patch.object(MOD, 'selected_model_profile', return_value='qwen4b'), \
                 mock.patch.object(Path, 'read_text', read), \
                 mock.patch.object(MOD.os.path, 'exists', return_value=True), \
                 mock.patch.object(MOD.os.path, 'islink', return_value=True), \
                 mock.patch.object(MOD.os.path, 'realpath', side_effect=realpath), \
                 mock.patch.object(MOD, 'stat_device', return_value=(259, 20)), \
                 mock.patch.object(MOD, 'partition_size_sectors', return_value=MOD.EXPECTED_SECTORS), \
                 mock.patch.object(MOD, 'filesystem_uuid', return_value=MOD.UUID), \
                 mock.patch.object(MOD, 'is_mounted', return_value=True), \
                 mock.patch.object(MOD, 'mount_details', return_value=('259:20', 'ext4', 'rw,noatime')), \
                 mock.patch.object(MOD, 'sha256_file', side_effect=lambda p: MOD.QWEN4B_SHA256 if p == expected[0] else 'a'*64) as sha:
                self.assertEqual(MOD.preflight(mounted_ok=True)['model_profile'], 'qwen4b')
                self.assertEqual([call.args[0] for call in sha.call_args_list], expected)
                sha.side_effect = lambda p: 'b'*64 if p == expected[0] else 'a'*64
                with self.assertRaisesRegex(MOD.Failure, 'SHA-256 mismatch'):
                    MOD.preflight(mounted_ok=True)

    def test_reuse_validates_and_never_owns_existing_mount(self):
        with mock.patch.object(MOD, 'is_mounted', return_value=True), \
             mock.patch.object(MOD, 'preflight') as check, \
             mock.patch.object(MOD, 'mount_fs') as mount:
            self.assertEqual(MOD.prepare_data_mount(True), [])
            check.assert_called_once_with(mounted_ok=True)
            mount.assert_not_called()
            check.side_effect = MOD.Failure('invalid existing mount')
            with self.assertRaises(MOD.Failure):
                MOD.prepare_data_mount(True)
            mount.assert_not_called()

    def test_fresh_start_retains_mount_ownership(self):
        with mock.patch.object(MOD, 'is_mounted', return_value=False), \
             mock.patch.object(MOD, 'preflight') as check, \
             mock.patch.object(MOD, 'mount_fs') as mount:
            self.assertEqual(MOD.prepare_data_mount(False), [MOD.MOUNT])
            check.assert_called_once_with()
            mount.assert_called_once()


if __name__ == "__main__":
    unittest.main()
