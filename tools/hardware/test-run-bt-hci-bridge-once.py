#!/usr/bin/env python3
"""Host-only gates for the one-shot Bluetooth registration adapter."""
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shlex
import struct
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


adapter = load("bt_hci_registration_adapter",
               ROOT / "tools/hardware/run-bt-hci-bridge-once.py")
observer_fixtures = load("bt_hci_observer_fixtures",
                         ROOT / "tools/hardware/test-audio-recovery-observer.py")
observer = observer_fixtures.observer


def synthetic_aarch64_elf():
    data = bytearray(512)
    data[:6] = b"\x7fELF\x02\x01"
    struct.pack_into("<H", data, 18, 183)
    struct.pack_into("<Q", data, 40, 64)
    struct.pack_into("<HH", data, 58, 64, 1)
    struct.pack_into("<I", data, 68, 7)  # SHT_NOTE
    struct.pack_into("<QQ", data, 88, 128, 36)
    build_id = bytes.fromhex("0123456789abcdef0123456789abcdef01234567")
    struct.pack_into("<III", data, 128, 4, len(build_id), 3)
    data[140:144] = b"GNU\0"
    data[144:164] = build_id
    return bytes(data), build_id.hex()


FIXTURE_ARTIFACT, FIXTURE_BUILD_ID = synthetic_aarch64_elf()
FIXTURE_ARTIFACT_SHA256 = hashlib.sha256(FIXTURE_ARTIFACT).hexdigest()


@contextlib.contextmanager
def fixture_artifact_pins():
    with mock.patch.multiple(
            adapter,
            EXPECTED_ARTIFACT_SHA256=FIXTURE_ARTIFACT_SHA256,
            EXPECTED_ARTIFACT_SIZE=len(FIXTURE_ARTIFACT),
            EXPECTED_ARTIFACT_BUILD_ID=FIXTURE_BUILD_ID):
        yield


class FakeBoard:
    __file__ = str(ROOT / "tools/hardware/run-bt-board-once.py")
    SOURCE = None
    BINARY = None
    DEST = None
    EXTRA_SOURCES = []
    NOTE = None


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="bt-registration-adapter-")
        self.root = Path(self.temp.name)
        self.receipt_root = self.root / "observer"
        self.out_patch = mock.patch.object(observer, "OUT", self.receipt_root)
        self.out_patch.start()
        self.board = FakeBoard()
        self.events = []
        self.receipt_path = observer.observer_receipt_path(
            observer.OUT, observer.TRIAL_ID)
        self.write_receipt(observer_fixtures.completed_observer())

    def tearDown(self):
        self.out_patch.stop()
        self.temp.cleanup()

    def write_receipt(self, value):
        self.receipt_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.receipt_path.parent, 0o700)
        fd = os.open(self.receipt_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream)

    def dependencies(self, **overrides):
        values = {
            "observer": observer,
            "board": self.board,
            "workspace": self.root,
            "trusted_root": self.root,
            "local_validator": lambda: self._local_ok(),
            "transport_validator": lambda: self.events.append("transport"),
            "snapshot_reader": lambda: self.snapshot(),
            "candidate_hasher": lambda: self.hash_candidate(),
            "controller_reader": lambda: self.controllers(),
            "board_invoker": lambda board, name: self.invoke_board(board, name),
        }
        values.update(overrides)
        return values

    def _local_ok(self):
        self.events.append("local")
        return adapter.EXPECTED_ARTIFACT_SHA256

    def snapshot(self, **changes):
        self.events.append("snapshot")
        return observer_fixtures.snapshot(**changes)

    def hash_candidate(self, value=adapter.EXPECTED_RECOVERY_SHA256):
        self.events.append("candidate_hash")
        return value

    def controllers(self, names=(), boot_id="candidate-boot-id"):
        self.events.append("controllers")
        return {"boot_id": boot_id, "controllers": list(names)}

    def invoke_board(self, board, name):
        self.events.append("board_main")
        self.assertEqual(name, adapter.TRIAL_ID)
        self.assertEqual(board.BINARY, adapter.ARTIFACT)
        self.assertEqual(board.DEST, adapter.DEST)
        return "host fake only"

    def run_trial(self, **overrides):
        return adapter.run_trial(adapter.TRIAL_ID,
                                 **self.dependencies(**overrides))

    def test_happy_preflight_orders_all_fake_reads_before_board_main(self):
        result = self.run_trial()
        self.assertEqual(result["trial_identity"], adapter.TRIAL_ID)
        self.assertEqual(result["recovery_sha256"], adapter.EXPECTED_RECOVERY_SHA256)
        self.assertEqual(self.events, ["local", "transport", "snapshot",
                                       "candidate_hash", "controllers", "board_main"])

    def test_bad_completed_observer_receipt_stops_before_live_reads_and_board(self):
        receipt = observer_fixtures.completed_observer(status="incomplete")
        self.write_receipt(receipt)
        with self.assertRaisesRegex(adapter.GateError, "observer receipt is invalid"):
            self.run_trial()
        self.assertEqual(self.events, ["local", "transport"])

    def test_changed_source_or_artifact_provenance_stops_before_phone_reads(self):
        self.events.clear()
        def bad_local_provenance():
            self.events.append("local")
            raise adapter.GateError("prebuilt bridge artifact SHA-256 mismatch")
        with self.assertRaisesRegex(adapter.GateError, "artifact SHA-256 mismatch"):
            self.run_trial(local_validator=bad_local_provenance)
        self.assertEqual(self.events, ["local"])

    def test_live_build_id_mismatch_stops_before_hash_controller_or_board(self):
        with self.assertRaisesRegex(RuntimeError, "GNU build ID"):
            self.run_trial(snapshot_reader=lambda: self.snapshot(
                gnu_build_id="0" * 40))
        self.assertEqual(self.events, ["local", "transport", "snapshot"])

    def test_live_full_recovery_hash_mismatch_stops_before_controller_or_board(self):
        with self.assertRaisesRegex(adapter.GateError, "full RECOVERY hash"):
            self.run_trial(candidate_hasher=lambda: self.hash_candidate("0" * 64))
        self.assertEqual(self.events, ["local", "transport", "snapshot", "candidate_hash"])

    def test_live_boot_must_match_the_completed_observer_receipt(self):
        self.write_receipt(observer_fixtures.completed_observer(boot_id="different-boot"))
        with self.assertRaisesRegex(adapter.GateError, "boot ID differs"):
            self.run_trial()
        self.assertEqual(self.events, ["local", "transport", "snapshot"])

    def test_native_model_and_wlan_baseline_failures_stop_before_board(self):
        cases = (
            {"native_ready": False},
            {"readiness": dict(observer_fixtures.snapshot()["readiness"],
                                model_api_health=False)},
            {"network_state": {"ready": False, "interfaces": []}},
        )
        for changes in cases:
            with self.subTest(changes=changes):
                self.events.clear()
                with self.assertRaises(RuntimeError):
                    self.run_trial(snapshot_reader=lambda c=changes: self.snapshot(**c))
                self.assertNotIn("board_main", self.events)

    def test_controller_must_be_absent_and_checked_on_the_same_boot(self):
        failures = (
            {"controller_reader": lambda: self.controllers(["hci0"])},
            {"controller_reader": lambda: self.controllers(boot_id="stale-boot")},
        )
        for changes in failures:
            with self.subTest(changes=changes):
                self.events.clear()
                with self.assertRaises(adapter.GateError):
                    self.run_trial(**changes)
                self.assertNotIn("board_main", self.events)

    def test_existing_named_receipt_path_refuses_before_preflight(self):
        path = adapter.trial_receipt_directory(self.root)
        path.parent.mkdir(parents=True)
        path.mkdir()
        with self.assertRaisesRegex(adapter.GateError, "already exists"):
            self.run_trial()
        self.assertEqual(self.events, [])

    def test_wrong_trial_name_refuses_before_any_preflight(self):
        with self.assertRaisesRegex(adapter.GateError, "exact controller-registration"):
            adapter.run_trial("other-trial", **self.dependencies())
        self.assertEqual(self.events, [])

    def test_actual_provenance_gate_rejects_changed_build_input_and_binary(self):
        with tempfile.TemporaryDirectory(prefix="bt-build-input-mismatch-") as temporary:
            root = Path(temporary)
            first_source = next(iter(adapter.BUILD_INPUT_SHA256))
            source = root / first_source
            source.parent.mkdir(parents=True)
            source.write_bytes(b"not the reviewed source")
            with self.assertRaisesRegex(adapter.GateError, "source fingerprint changed"):
                adapter.validate_local_provenance(
                    root=root, artifact_path=root / "missing-artifact", private_headers=())

        with tempfile.TemporaryDirectory(prefix="bt-artifact-mismatch-") as temporary:
            artifact = Path(temporary) / "probe"
            changed = bytearray(FIXTURE_ARTIFACT)
            changed[0] ^= 0xff
            artifact.write_bytes(changed)
            os.chmod(artifact, 0o700)
            with fixture_artifact_pins():
                with self.assertRaisesRegex(adapter.GateError, "artifact SHA-256 mismatch"):
                    adapter.validate_local_provenance(
                        root=ROOT, artifact_path=artifact, private_headers=())

    def test_replaced_temp_artifact_after_preflight_never_reaches_remote_stage(self):
        with tempfile.TemporaryDirectory(prefix="bt-artifact-toctou-") as temporary:
            workspace = Path(temporary) / "workspace"
            trusted_root = Path(temporary) / "trusted"
            hardware = workspace / "tools/hardware"
            gpu = workspace / "tools/gpu-compat"
            hardware.mkdir(parents=True)
            gpu.mkdir(parents=True)
            (trusted_root / "tools/gpu-compat").mkdir(parents=True)
            board_source = ROOT / "tools/hardware/run-bt-board-once.py"
            board_copy = hardware / board_source.name
            board_copy.write_bytes(board_source.read_bytes())
            source = hardware / "source.c"
            accepted_source = hardware / "accepted.c"
            source.write_text("int source;\n", encoding="utf-8")
            accepted_source.write_text("int accepted;\n", encoding="utf-8")
            (workspace / "tools/hardware/run-bt-version-once.py").write_text(
                "# test fixture\n", encoding="utf-8")
            (gpu / "run-trial.py").write_text("# workflow hash fixture\n",
                                             encoding="utf-8")
            remote_log = Path(temporary) / "remote.log"
            (trusted_root / "tools/gpu-compat/run-trial.py").write_text(
                "import json,pathlib,subprocess\n"
                f"log=pathlib.Path({str(remote_log)!r})\n"
                "def phone_health(): return {'boot_id': 'host-only-fake'}\n"
                "def remote(command,timeout=25):\n"
                "    with log.open('a',encoding='utf-8') as stream: stream.write(command+'\\n')\n"
                "    if command == 'dmesg': return subprocess.CompletedProcess([],0,'','')\n"
                "    if command.startswith('python3 -c '):\n"
                "        return subprocess.CompletedProcess([],0,json.dumps({'device_fds':[],'independent_usb':{}}),'')\n"
                "    return subprocess.CompletedProcess([],0,'','')\n",
                encoding="utf-8")

            artifact = Path(temporary) / "probe"
            artifact.write_bytes(FIXTURE_ARTIFACT)
            os.chmod(artifact, 0o700)
            stage_calls = []
            class RemoteCounter:
                def run_approved_ssh_wrapper(self, *args, **kwargs):
                    stage_calls.append((args, kwargs))
                    return subprocess.CompletedProcess([], 0, b"", b"")

            board = adapter.load_board()
            board.__file__ = str(board_copy)
            board.ROOT = workspace
            board.SOURCE = source
            board.ACCEPTED_SOURCE = accepted_source
            board.BINARY = artifact
            board.DEST = str(Path(temporary) / "staging/probe")
            board.EXTRA_SOURCES = []
            board.HOST_TIMEOUT = 3
            board.accepted_runner = lambda: SimpleNamespace(METADATA="{}")
            fake_observer = SimpleNamespace(_trusted_deployer=lambda: RemoteCounter())

            def validate_then_use_later():
                return adapter.validate_local_provenance(
                    artifact_path=artifact, private_headers=())

            def board_invoker(board, name):
                self.assertEqual(name, adapter.TRIAL_ID)
                # Replace only after adapter preflight; actual board.main then
                # rereads BINARY and builds its dynamic staging digest.
                replacement = bytearray(FIXTURE_ARTIFACT)
                replacement[-1] ^= 0xff
                artifact.write_bytes(replacement)
                self.events.append("replacement")
                board.ROOT = workspace
                board.SOURCE = source
                board.ACCEPTED_SOURCE = accepted_source
                board.BINARY = artifact
                board.DEST = str(Path(temporary) / "staging/probe")
                board.EXTRA_SOURCES = []
                self.events.append("board_main")
                return adapter.run_board_main(
                    board, name, fake_observer, trusted_root=trusted_root)

            dependencies = self.dependencies(
                board=board,
                workspace=self.root,
                trusted_root=trusted_root,
                local_validator=validate_then_use_later,
                board_invoker=board_invoker,
            )
            with fixture_artifact_pins():
                with self.assertRaisesRegex(adapter.GateError,
                                            "staged bridge artifact SHA-256 mismatch"):
                    adapter.run_trial(adapter.TRIAL_ID, **dependencies)

            self.assertEqual(self.events, ["transport", "snapshot", "candidate_hash",
                                           "controllers", "replacement", "board_main"])
            self.assertEqual(stage_calls, [])
            remote_commands = remote_log.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(remote_commands), 2)
            self.assertTrue(remote_commands[0].startswith("python3 -c "))
            self.assertEqual(remote_commands[1], "dmesg")
            self.assertFalse(any(command.startswith("timeout ")
                                 for command in remote_commands))
            self.assertFalse(Path(board.DEST).exists())

    def test_execute_without_separate_owner_ack_refuses_before_imports(self):
        error = io.StringIO()
        with mock.patch.object(adapter, "load_observer", side_effect=AssertionError), \
                mock.patch.object(adapter, "load_board", side_effect=AssertionError), \
                contextlib.redirect_stderr(error):
            rc = adapter.main([adapter.TRIAL_ID, "--execute"])
        if __debug__:
            self.assertEqual(rc, 1)
            self.assertIn("separate owner authorization", error.getvalue())
        else:
            self.assertEqual(rc, 2)
            self.assertIn("optimized Python is refused", error.getvalue())

    def test_authorized_cli_dispatches_only_exact_name_to_injected_board(self):
        fake = {"observer": observer, "board": self.board,
                "workspace": self.root, "trusted_root": self.root,
                "local_validator": lambda: None,
                "transport_validator": lambda: None,
                "snapshot_reader": lambda: observer_fixtures.snapshot(),
                "candidate_hasher": lambda: adapter.EXPECTED_RECOVERY_SHA256,
                "controller_reader": lambda: {"boot_id": "candidate-boot-id",
                                                "controllers": []},
                "board_invoker": lambda board, name: "host fake only"}
        with mock.patch.object(adapter, "load_observer", return_value=observer), \
                mock.patch.object(adapter, "load_board", return_value=self.board), \
                mock.patch.object(adapter, "run_trial", return_value={"trial_identity": adapter.TRIAL_ID}) as run:
            rc = adapter.main([adapter.TRIAL_ID, "--execute",
                               "--ack-separate-controller-authorization"],
                              dependencies=fake)
        if __debug__:
            self.assertEqual(rc, 0)
            run.assert_called_once()
        else:
            self.assertEqual(rc, 2)
            run.assert_not_called()

    def test_supervisor_timeout_is_reported_as_failure_not_trial_success(self):
        output = io.StringIO()
        error = io.StringIO()
        with mock.patch.object(adapter, "run_trial",
                               side_effect=subprocess.TimeoutExpired("remote", 45)), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
            rc = adapter.main([adapter.TRIAL_ID, "--execute",
                               "--ack-separate-controller-authorization"],
                              dependencies={})
        if __debug__:
            self.assertEqual(rc, 1)
            self.assertEqual(output.getvalue(), "")
            self.assertIn("refusing controller-registration trial", error.getvalue())
        else:
            self.assertEqual(rc, 2)
            self.assertIn("optimized Python is refused", error.getvalue())

    def test_optimized_cli_refuses_before_loading_transport_or_board(self):
        code = "\n".join((
            "import importlib.util,sys",
            f"s=importlib.util.spec_from_file_location('adapter',{str(ROOT / 'tools/hardware/run-bt-hci-bridge-once.py')!r})",
            "m=importlib.util.module_from_spec(s)",
            "s.loader.exec_module(m)",
            "calls=[]",
            "m.load_observer=lambda: calls.append('observer')",
            "m.load_board=lambda: calls.append('board')",
            f"rc=m.main([{adapter.TRIAL_ID!r},'--execute','--ack-separate-controller-authorization'])",
            "print('rc=%d calls=%s'%(rc,calls))",
            "if rc != 2 or calls: raise SystemExit(1)",
        ))
        result = subprocess.run([sys.executable, "-O", "-c", code], cwd=ROOT,
                                capture_output=True, text=True, timeout=3)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("calls=[]", result.stdout)
        self.assertIn("optimized Python is refused", result.stderr)

    def test_actual_board_stage_keeps_asserts_active_under_remote_pythonoptimize(self):
        with tempfile.TemporaryDirectory(prefix="bt-stage-isolated-") as temporary:
            workspace = Path(temporary) / "workspace"
            trusted_root = Path(temporary) / "trusted"
            hardware = workspace / "tools/hardware"
            gpu = workspace / "tools/gpu-compat"
            hardware.mkdir(parents=True)
            gpu.mkdir(parents=True)
            (trusted_root / "tools/gpu-compat").mkdir(parents=True)
            board_source = ROOT / "tools/hardware/run-bt-board-once.py"
            board_copy = hardware / board_source.name
            board_copy.write_bytes(board_source.read_bytes())
            (hardware / "source.c").write_text("int source;\n", encoding="utf-8")
            (hardware / "accepted.c").write_text("int accepted;\n", encoding="utf-8")
            (workspace / "tools/hardware/run-bt-version-once.py").write_text(
                "# test fixture\n", encoding="utf-8")
            (gpu / "run-trial.py").write_text("# workflow hash fixture\n", encoding="utf-8")
            (trusted_root / "tools/gpu-compat/run-trial.py").write_text(
                "import json\n"
                "import subprocess\n"
                "def phone_health(): return {'boot_id': 'host-only-fake'}\n"
                "def remote(command, timeout=25):\n"
                "    if command == 'dmesg':\n"
                "        return subprocess.CompletedProcess([], 0, '', '')\n"
                "    if command.startswith('python3 -c '):\n"
                "        return subprocess.CompletedProcess([], 0, json.dumps({'device_fds': [], 'independent_usb': {}}), '')\n"
                "    return subprocess.CompletedProcess([], 0, '', '')\n",
                encoding="utf-8")

            binary = workspace / "builds/probe"
            binary.parent.mkdir()
            binary.write_bytes(FIXTURE_ARTIFACT)
            destination = workspace / "staging/probe"
            board = adapter.load_board()
            board.__file__ = str(board_copy)
            board.ROOT = workspace
            board.SOURCE = hardware / "source.c"
            board.ACCEPTED_SOURCE = hardware / "accepted.c"
            board.BINARY = binary
            board.DEST = str(destination)
            board.EXTRA_SOURCES = []
            board.HOST_TIMEOUT = 3
            board.accepted_runner = lambda: SimpleNamespace(METADATA="{}")

            class LocalStageDeployer:
                command = None
                process = None

                def run_approved_ssh_wrapper(self, path, command, *, input_data,
                                             timeout, project_root):
                    self.command = command
                    words = shlex.split(command)
                    self.process = subprocess.run(
                        words, input=input_data + b"tamper", capture_output=True,
                        timeout=timeout,
                        env={**os.environ, "PYTHONOPTIMIZE": "1"})
                    return self.process

            deployer = LocalStageDeployer()
            fake_observer = SimpleNamespace(_trusted_deployer=lambda: deployer)
            with fixture_artifact_pins():
                with self.assertRaises(RuntimeError) as failure:
                    adapter.run_board_main(board, adapter.TRIAL_ID, fake_observer,
                                           trusted_root=trusted_root)

            self.assertEqual(shlex.split(deployer.command)[:2], ["python3", "-I"])
            self.assertNotEqual(deployer.process.returncode, 0)
            self.assertIn(b"AssertionError", deployer.process.stderr)
            self.assertIn("AssertionError", str(failure.exception))
            self.assertFalse(destination.exists())


if __name__ == "__main__":
    unittest.main()
