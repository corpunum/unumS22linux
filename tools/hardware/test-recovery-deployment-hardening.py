#!/usr/bin/env python3
"""Host-only regressions for RECOVERY artifact and embedded target gates."""
from __future__ import annotations

import hashlib
import fcntl
import importlib.util
import builtins
import contextlib
import io
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


DEPLOY = load("s22_deploy_audio_extra", ROOT / "tools/hardware/deploy-audio-extra-recovery.py")
BASE = load("s22_deploy_audio_base", ROOT / "tools/hardware/deploy-audio-recovery.py")
BUILDER = load("s22_build_bt_hci_recovery", ROOT / "tools/hardware/build-bt-hci-recovery.py")


class ArtifactValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="s22-recovery-gates-")
        self.root = Path(self.temp.name)
        self.candidate_path = self.root / "candidate.img"
        self.rollback_path = self.root / "rollback.img"
        self.lineage_path = self.root / "lineage.img"
        self.manifest_path = self.root / "manifest.json"
        rollback = bytearray(b"R" * 2048)
        candidate = bytearray(rollback)
        for start, end in ((16, 20), (576, 608), (1636, 1644)):
            candidate[start:end] = b"C" * (end - start)
        self.candidate = bytes(candidate)
        self.rollback = bytes(rollback)
        self.lineage = b"known-good rollback test payload"
        self.candidate_path.write_bytes(self.candidate)
        self.rollback_path.write_bytes(self.rollback)
        self.lineage_path.write_bytes(self.lineage)
        self.hashes = {
            "candidate_sha": hashlib.sha256(self.candidate).hexdigest(),
            "rollback_sha": hashlib.sha256(self.rollback).hexdigest(),
            "lineage_sha": hashlib.sha256(self.lineage).hexdigest(),
        }
        self.manifest_path.write_text(json.dumps({
            "image": "builds/audio-extra-v2-20260922/recovery.img",
            "image_sha256": self.hashes["candidate_sha"],
            "base_image_sha256": self.hashes["rollback_sha"],
            "extra_count": 20,
            "phone_access": False,
        }))

    def tearDown(self):
        self.temp.cleanup()

    def validate(self, candidate_path=None, rollback_path=None, lineage_path=None, **overrides):
        args = {
            "size": 2048,
            **self.hashes,
            "manifest_path": self.manifest_path,
            "expected_manifest_image": "builds/audio-extra-v2-20260922/recovery.img",
            "root": self.root,
        }
        args.update(overrides)
        return DEPLOY.validate_artifacts(
            candidate_path or self.candidate_path,
            rollback_path or self.rollback_path,
            lineage_path or self.lineage_path,
            **args,
        )

    def test_valid_exact_size_hashes_and_header_preservation(self):
        self.assertEqual(self.validate(), self.candidate)

    def test_wrong_candidate_hash_is_rejected(self):
        self.candidate_path.write_bytes(b"X" + self.candidate[1:])
        with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
            self.validate()

    def test_wrong_rollback_hash_is_rejected(self):
        self.rollback_path.write_bytes(b"X" + self.rollback[1:])
        with self.assertRaisesRegex(ValueError, "rollback recovery image SHA-256 mismatch"):
            self.validate()

    def test_build_manifest_must_match_image_and_rollback_provenance(self):
        manifest = json.loads(self.manifest_path.read_text())
        manifest["base_image_sha256"] = "0" * 64
        self.manifest_path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, "base-image hash does not match"):
            self.validate()
        with self.assertRaisesRegex(ValueError, "candidate build manifest is missing"):
            self.validate(manifest_path=self.root / "missing-manifest.json")

    def test_incorrect_partition_size_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "expected 2049"):
            self.validate(size=2049)

    def test_missing_rollback_is_rejected_before_operation(self):
        with self.assertRaisesRegex(ValueError, "rollback recovery image is missing"):
            self.validate(rollback_path=self.root / "missing-rollback.img")

    def test_symlink_candidate_is_rejected(self):
        link = self.root / "candidate-link.img"
        link.symlink_to(self.candidate_path)
        with self.assertRaisesRegex(ValueError, "non-symlink regular file"):
            self.validate(candidate_path=link)

    def test_immutable_header_change_is_rejected(self):
        changed = bytearray(self.candidate)
        changed[300] ^= 1
        self.candidate_path.write_bytes(changed)
        self.hashes["candidate_sha"] = hashlib.sha256(changed).hexdigest()
        manifest = json.loads(self.manifest_path.read_text())
        manifest["image_sha256"] = self.hashes["candidate_sha"]
        self.manifest_path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, "immutable boot header"):
            self.validate()

    def test_base_host_gate_refuses_wrong_hash(self):
        with self.assertRaisesRegex(ValueError, "incorrect size or SHA-256"):
            BASE.validate_host_artifacts(self.candidate_path, self.rollback_path, self.lineage_path)

    def test_candidate_hash_gate_survives_optimized_host_python(self):
        probe = r'''import importlib.util,sys
spec=importlib.util.spec_from_file_location("deploy",sys.argv[1])
mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
try:
 mod.validate_artifacts(sys.argv[2],sys.argv[3],sys.argv[4],size=2048,
  candidate_sha="0"*64,rollback_sha=sys.argv[5],lineage_sha=sys.argv[6],
  manifest_path=sys.argv[7],expected_manifest_image="builds/audio-extra-v2-20260922/recovery.img",root=sys.argv[8])
except ValueError as error:
 print("REFUSED:"+str(error));sys.exit(23)
print("UNSAFE_ACCEPT");sys.exit(0)
'''
        for mode in ("normal", "-O", "PYTHONOPTIMIZE=1"):
            env = os.environ.copy()
            argv = [sys.executable]
            if mode == "-O":
                argv.append("-O")
            elif mode == "PYTHONOPTIMIZE=1":
                env["PYTHONOPTIMIZE"] = "1"
            argv.extend([
                "-c", probe,
                str(ROOT / "tools/hardware/deploy-audio-extra-recovery.py"),
                str(self.candidate_path), str(self.rollback_path), str(self.lineage_path),
                self.hashes["rollback_sha"], self.hashes["lineage_sha"],
                str(self.manifest_path), str(self.root),
            ])
            with self.subTest(mode=mode):
                result = subprocess.run(argv, text=True, capture_output=True, env=env, check=False)
                self.assertEqual(result.returncode, 23, result.stdout + result.stderr)
                self.assertIn("candidate build manifest image hash", result.stdout)


class ExtraDeploymentEntrypointTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="s22-extra-deploy-entry-")
        self.root = Path(self.temp.name)
        builds = self.root / "builds"
        (builds / "audio-extra-v2-20260922").mkdir(parents=True)
        (builds / "audio-early-20260922").mkdir()
        (self.root / "lineage/build-20260915").mkdir(parents=True)
        candidate = bytearray(b"R" * 2048)
        for start, end in ((16, 20), (576, 608), (1636, 1644)):
            candidate[start:end] = b"C" * (end - start)
        self.candidate = bytes(candidate)
        self.rollback = b"R" * 2048
        self.lineage = b"known good lineage fixture"
        (builds / "audio-extra-v2-20260922/recovery.img").write_bytes(self.candidate)
        (builds / "audio-early-20260922/recovery.img").write_bytes(self.rollback)
        (self.root / "lineage/build-20260915/recovery.img").write_bytes(self.lineage)
        manifest = {
            "image": "builds/audio-extra-v2-20260922/recovery.img",
            "image_sha256": hashlib.sha256(self.candidate).hexdigest(),
            "base_image_sha256": hashlib.sha256(self.rollback).hexdigest(),
            "extra_count": 20,
            "phone_access": False,
        }
        (builds / "audio-extra-v2-20260922/manifest.json").write_text(json.dumps(manifest))
        (self.root / "rootfs/main-driver-loop-20260921").mkdir(parents=True)
        ssh = self.root / "tools/s22-ssh"
        ssh.parent.mkdir(parents=True)
        ssh.write_bytes((ROOT / "tools/s22-ssh").read_bytes())
        ssh.chmod(0o755)
        self.base = SimpleNamespace(
            SIZE=2048,
            BASE_SHA=BASE.BASE_SHA,
            NEW_SHA=BASE.NEW_SHA,
            render_remote=BASE.render_remote,
            ensure_new_receipt=BASE.ensure_new_receipt,
            validate_approved_ssh_wrapper=BASE.validate_approved_ssh_wrapper,
            run_approved_ssh_wrapper=BASE.run_approved_ssh_wrapper,
            validate_remote_receipt=BASE.validate_remote_receipt,
        )
        self.hashes = {
            "BEFORE": hashlib.sha256(self.rollback).hexdigest(),
            "AFTER": hashlib.sha256(self.candidate).hexdigest(),
            "LINEAGE_SHA": hashlib.sha256(self.lineage).hexdigest(),
        }

    def tearDown(self):
        self.temp.cleanup()

    def call_main(self, argv, subprocess_mock):
        with mock.patch.object(DEPLOY, "ROOT", self.root), \
             mock.patch.object(DEPLOY, "BEFORE", self.hashes["BEFORE"]), \
             mock.patch.object(DEPLOY, "AFTER", self.hashes["AFTER"]), \
             mock.patch.object(DEPLOY, "LINEAGE_SHA", self.hashes["LINEAGE_SHA"]), \
             mock.patch.object(DEPLOY.subprocess, "run", subprocess_mock):
            return DEPLOY.main(argv, base_module=self.base)

    def test_stage_entrypoint_validates_then_uses_mocked_transport(self):
        transport = mock.Mock(return_value=SimpleNamespace(
            returncode=0,
            stdout=json.dumps({
                "mode": "stage", "partition_written": False,
                "backup_sha256": self.hashes["BEFORE"],
                "candidate_sha256": self.hashes["AFTER"],
            }).encode(),
            stderr=b""))
        self.call_main(["--stage"], transport)
        transport.assert_called_once()
        args, kwargs = transport.call_args
        self.assertEqual(args[0][0:2], ["/bin/bash", "-c"])
        self.assertEqual(kwargs["pass_fds"], (int(kwargs["env"]["S22_APPROVED_SSH_FD"]),))
        command = args[0][-1]
        self.assertIn(self.hashes["BEFORE"], command)
        self.assertIn(self.hashes["AFTER"], command)
        self.assertIn("audio-extra-v2-20260922", command)
        self.assertIn("dir_fd=parent_fd", command)
        self.assertEqual(kwargs["input"], self.candidate)
        receipt = self.root / "rootfs/main-driver-loop-20260921/audio-extra-recovery-stage.json"
        self.assertTrue(receipt.is_file())

    def test_bad_hash_or_stale_receipt_refuses_before_transport(self):
        transport = mock.Mock()
        with mock.patch.object(DEPLOY, "ROOT", self.root), \
             mock.patch.object(DEPLOY, "BEFORE", self.hashes["BEFORE"]), \
             mock.patch.object(DEPLOY, "AFTER", "0" * 64), \
             mock.patch.object(DEPLOY, "LINEAGE_SHA", self.hashes["LINEAGE_SHA"]), \
             mock.patch.object(DEPLOY.subprocess, "run", transport):
            with self.assertRaisesRegex(ValueError, "candidate build manifest image hash"):
                DEPLOY.main(["--stage"], base_module=self.base)
        transport.assert_not_called()

        receipt = self.root / "rootfs/main-driver-loop-20260921/audio-extra-recovery-flash.json"
        receipt.write_text("old")
        with self.assertRaisesRegex(ValueError, "existing deployment receipt"):
            self.call_main(["--flash"], transport)
        transport.assert_not_called()

    def test_extra_deployer_rejects_symlinked_ssh_wrapper_before_transport(self):
        ssh = self.root / "tools/s22-ssh"
        ssh.unlink()
        target = self.root / "tools/real-ssh"
        target.write_text("#!/bin/sh\nexit 0\n")
        target.chmod(0o755)
        ssh.symlink_to(target)
        transport = mock.Mock()
        with self.assertRaisesRegex(SystemExit, "non-symlink executable regular file"):
            self.call_main(["--stage"], transport)
        transport.assert_not_called()

    def test_ssh_wrapper_replacement_after_open_cannot_substitute_executed_bytes(self):
        tools = self.root / "tools"
        evidence = self.root / "evidence/native-linux-20260919"
        tools.mkdir(parents=True, exist_ok=True)
        evidence.mkdir(parents=True)
        (evidence / "native-v2-known-hosts").write_text("fixture host key\n")
        wrapper = tools / "s22-ssh"
        wrapper.write_bytes((ROOT / "tools/s22-ssh").read_bytes())
        wrapper.chmod(0o755)
        fd = BASE.validate_approved_ssh_wrapper(wrapper)
        evil = tools / "replacement-ssh"
        evil.write_text("#!/usr/bin/env bash\nprintf 'REPLACEMENT_EXECUTED\\n'\nexit 88\n")
        evil.chmod(0o755)
        wrapper.unlink()
        wrapper.symlink_to(evil)
        fake_bin = self.root / "fake-bin"
        fake_bin.mkdir()
        fake_ssh = fake_bin / "ssh"
        fake_ssh.write_text("#!/usr/bin/env bash\nprintf 'PINNED_WRAPPER_REACHED\\n'\n")
        fake_ssh.chmod(0o755)
        argv, environment = BASE.build_approved_ssh_invocation(
            fd, wrapper, "python3 -c pass", project_root=self.root)
        environment["PATH"] = str(fake_bin) + os.pathsep + environment["PATH"]
        try:
            result = subprocess.run(
                argv, capture_output=True, text=True, timeout=5,
                pass_fds=(fd,), env=environment, check=False)
        finally:
            os.close(fd)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PINNED_WRAPPER_REACHED", result.stdout)
        self.assertNotIn("REPLACEMENT_EXECUTED", result.stdout)

    def test_inherited_fake_ssh_is_not_selected(self):
        fake_bin = self.root / "hostile-bin"
        fake_bin.mkdir()
        marker = self.root / "fake-ssh-ran"
        fake_ssh = fake_bin / "ssh"
        fake_ssh.write_text(
            "#!/usr/bin/env bash\nprintf 'FAKE_SSH_RAN' > " + shlex.quote(str(marker)) + "\n")
        fake_ssh.chmod(0o755)
        fd = BASE.validate_approved_ssh_wrapper(ROOT / "tools/s22-ssh")
        try:
            with mock.patch.dict(os.environ, {"PATH": str(fake_bin) + ":/usr/bin:/bin"}):
                _argv, environment = BASE.build_approved_ssh_invocation(
                    fd, ROOT / "tools/s22-ssh", "ssh -V", project_root=ROOT)
            self.assertEqual(environment["PATH"], "/usr/bin")
            result = subprocess.run(
                ["/bin/bash", "-c", "ssh -V"], capture_output=True, text=True,
                timeout=5, env=environment, check=False)
        finally:
            os.close(fd)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("OpenSSH", result.stdout + result.stderr)
        self.assertFalse(marker.exists(), "inherited PATH fake ssh was executed")

    def test_invalid_remote_receipt_is_not_recorded_as_success(self):
        transport = mock.Mock(return_value=SimpleNamespace(
            returncode=0,
            stdout=b'{"mode":"stage","partition_written":false,"backup_sha256":"wrong","candidate_sha256":"wrong"}',
            stderr=b""))
        with self.assertRaisesRegex(RuntimeError, "receipt mismatch"):
            self.call_main(["--stage"], transport)
        receipt = self.root / "rootfs/main-driver-loop-20260921/audio-extra-recovery-stage.json"
        self.assertFalse(receipt.exists())
        transport.assert_called_once()


class _SandboxPath:
    def __init__(self, value, sandbox):
        self.sandbox = sandbox
        self.virtual = str(value)
        self.real = sandbox.root / self.virtual.lstrip("/")

    def __fspath__(self):
        return os.fspath(self.real)

    def __str__(self):
        return self.virtual

    def read_text(self, *args, **kwargs):
        return self.real.read_text(*args, **kwargs)

    def stat(self):
        return self.sandbox.fake_os.stat(self)

    def is_symlink(self):
        return self.real.is_symlink()

    def resolve(self, strict=False):
        resolved = self.real.resolve(strict=strict)
        return _SandboxPath("/" + resolved.relative_to(self.sandbox.root).as_posix(), self.sandbox)


class _SandboxOS:
    def __init__(self, sandbox):
        self.sandbox = sandbox
        self.real = os
        self.fd_kinds = {}
        self.fd_paths = {}

    def __getattr__(self, name):
        return getattr(self.real, name)

    def _mapped_path(self, path):
        if isinstance(path, str) and path.startswith("/"):
            return self.sandbox.root / path.lstrip("/")
        return path

    def _metadata(self, info, path=None, *, block=False):
        mode = info.st_mode
        rdev = getattr(info, "st_rdev", 0)
        if block or (path is not None and os.path.abspath(os.fspath(path)) == str(self.sandbox.partition)):
            mode = (mode & 0o7777) | __import__("stat").S_IFBLK
            rdev = os.makedev(259, 0)
        fields = ("st_mode", "st_ino", "st_dev", "st_nlink", "st_gid", "st_size",
                  "st_atime", "st_mtime", "st_ctime")
        result = {field: getattr(info, field) for field in fields if hasattr(info, field)}
        result.update(st_uid=0, st_rdev=rdev, st_mode=mode)
        return SimpleNamespace(**result)

    def getuid(self):
        return 0

    def geteuid(self):
        return 0

    def umask(self, _mask):
        return 0o022

    def stat(self, path, *, dir_fd=None, follow_symlinks=True):
        mapped = self._mapped_path(path) if dir_fd is None else path
        info = self.real.stat(mapped, dir_fd=dir_fd, follow_symlinks=follow_symlinks)
        return self._metadata(info, mapped if dir_fd is None else None)

    def lstat(self, path, *, dir_fd=None):
        mapped = self._mapped_path(path) if dir_fd is None else path
        info = self.real.lstat(mapped, dir_fd=dir_fd)
        return self._metadata(info, mapped if dir_fd is None else None)

    def fstat(self, fd):
        info = self.real.fstat(fd)
        return self._metadata(info, block=self.fd_kinds.get(fd) == "partition")

    def fstatvfs(self, _fd):
        return SimpleNamespace(f_bavail=1_000_000, f_frsize=4096, f_favail=1_000_000)

    def open(self, path, flags, mode=0o777, *, dir_fd=None):
        if dir_fd is None:
            mapped = self._mapped_path(path)
            fd = self.real.open(mapped, flags, mode)
            actual_path = Path(mapped)
        else:
            fd = self.real.open(path, flags, mode, dir_fd=dir_fd)
            parent = self.fd_paths.get(dir_fd)
            actual_path = parent / os.fspath(path) if parent is not None else None
        kind = "partition" if actual_path == self.sandbox.partition else "file"
        if flags & os.O_DIRECTORY:
            kind = "directory"
        elif flags & os.O_WRONLY and dir_fd is not None:
            kind = "staged-file"
        self.fd_kinds[fd] = kind
        if actual_path is not None:
            self.fd_paths[fd] = actual_path
        return fd

    def close(self, fd):
        self.fd_kinds.pop(fd, None)
        self.fd_paths.pop(fd, None)
        return self.real.close(fd)

    def write(self, fd, data):
        if self.fd_kinds.get(fd) != "staged-file":
            return self.real.write(fd, data)
        limit = self.sandbox.fail_staged_write_after
        written = self.sandbox.current_operation_staged_writes
        if limit is not None:
            if written >= limit:
                raise OSError("injected fake staging-file write failure")
            data = data[:limit - written]
        count = self.real.write(fd, data)
        self.sandbox.current_operation_staged_writes += count
        self.sandbox.staged_write_bytes += count
        return count

    def pwrite(self, fd, data, offset):
        if self.fd_kinds.get(fd) != "partition":
            return self.real.pwrite(fd, data, offset)
        limit = self.sandbox.fail_partition_write_after
        written = self.sandbox.current_operation_writes
        if limit is not None:
            if written >= limit:
                raise OSError("injected fake block-device write failure")
            data = data[:limit - written]
        count = self.real.pwrite(fd, data, offset)
        self.sandbox.current_operation_writes += count
        self.sandbox.device_write_bytes += count
        return count


class _SandboxFcntl:
    def __init__(self):
        self.real = fcntl

    def __getattr__(self, name):
        return getattr(self.real, name)

    def ioctl(self, fd, request, argument=0, mutate_flag=True):
        if request == 0x80081272:
            return __import__("struct").pack("<Q", BASE.SIZE)
        return self.real.ioctl(fd, request, argument, mutate_flag)


class _RepeatedInput:
    def __init__(self, byte_value, size):
        self.byte_value = byte_value
        self.size = size
        self.sent = False

    def read(self, limit=-1):
        if self.sent:
            return b""
        self.sent = True
        amount = self.size if limit < 0 else min(self.size, limit)
        return bytes([self.byte_value]) * amount


class RenderedRemoteSandbox:
    SIZE = BASE.SIZE
    BASE_BYTE = 0x42
    CANDIDATE_BYTE = 0x43
    CHUNK_SIZE = 1024 * 1024

    def __init__(self):
        self.temp = tempfile.TemporaryDirectory(prefix="s22-rendered-deployer-")
        self.root = Path(self.temp.name)
        self.partition = self.root / "dev/sda16"
        self.stage_dir = self.root / "srv/s22/rendered-integration"
        self.base_sha = self.digest_repeated(self.BASE_BYTE)
        self.candidate_sha = self.digest_repeated(self.CANDIDATE_BYTE)
        self.device_write_bytes = 0
        self.current_operation_writes = 0
        self.fail_partition_write_after = None
        self.staged_write_bytes = 0
        self.current_operation_staged_writes = 0
        self.fail_staged_write_after = None
        self._create_filesystem()
        self.fake_os = _SandboxOS(self)
        self.rendered = BASE.render_remote(
            base_sha=self.base_sha, new_sha=self.candidate_sha,
            staging_directory="/srv/s22/rendered-integration",
            rollback_filename="rollback.img",
        )

    @classmethod
    def digest_repeated(cls, byte_value):
        digest = hashlib.sha256()
        chunk = bytes([byte_value]) * cls.CHUNK_SIZE
        for _ in range(cls.SIZE // cls.CHUNK_SIZE):
            digest.update(chunk)
        return digest.hexdigest()

    @classmethod
    def write_repeated(cls, path, byte_value):
        chunk = bytes([byte_value]) * cls.CHUNK_SIZE
        with path.open("wb") as stream:
            for _ in range(cls.SIZE // cls.CHUNK_SIZE):
                stream.write(chunk)

    @classmethod
    def hash_file(cls, path):
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(cls.CHUNK_SIZE), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _create_filesystem(self):
        for relative in (
            "run", "dev/block/by-name", "sys/class/block/sda16",
            "sys/class/power_supply/battery", "proc/1", "proc/sys/kernel",
            "proc/self", "srv/s22",
        ):
            (self.root / relative).mkdir(parents=True, exist_ok=True)
        (self.root / "srv/s22").chmod(0o755)
        (self.root / "proc/1/comm").write_text("native-guardian\n")
        (self.root / "proc/sys/kernel/osrelease").write_text("5.10.260-g4e5c5ad7d950\n")
        (self.root / "sys/class/power_supply/battery/temp").write_text("278\n")
        (self.root / "sys/class/block/sda16/uevent").write_text("PARTNAME=recovery\n")
        (self.root / "sys/class/block/sda16/size").write_text("196608\n")
        (self.root / "proc/self/mountinfo").write_text("")
        self.write_repeated(self.partition, self.BASE_BYTE)
        (self.root / "dev/block/by-name/recovery").symlink_to(self.partition)

    def set_pid1(self, value):
        (self.root / "proc/1/comm").write_text(value + "\n")

    def write_partition(self, byte_value):
        self.write_repeated(self.partition, byte_value)

    def execute(self, mode, *, candidate_input=False):
        self.current_operation_writes = 0
        self.current_operation_staged_writes = 0
        input_stream = _RepeatedInput(self.CANDIDATE_BYTE, self.SIZE) if candidate_input \
            else _RepeatedInput(self.CANDIDATE_BYTE, 0)
        fake_sys = SimpleNamespace(
            argv=["remote-deployer", mode],
            stdin=SimpleNamespace(buffer=input_stream),
        )
        fake_pathlib = SimpleNamespace(Path=lambda value: _SandboxPath(value, self))
        fake_fcntl = _SandboxFcntl()
        fake_modules = {
            "fcntl": fake_fcntl, "os": self.fake_os,
            "pathlib": fake_pathlib, "sys": fake_sys,
        }
        real_import = builtins.__import__

        def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
            if level == 0 and name in fake_modules:
                return fake_modules[name]
            return real_import(name, globals, locals, fromlist, level)

        builtin_namespace = vars(builtins).copy()
        builtin_namespace["__import__"] = fake_import
        namespace = {"__builtins__": builtin_namespace}
        output = io.StringIO()
        error = None
        with contextlib.redirect_stdout(output):
            try:
                exec(self.rendered, namespace)
            except Exception as caught:
                error = caught
        self.close_open_fds()
        return SimpleNamespace(stdout=output.getvalue(), error=error)

    def close_open_fds(self):
        for fd in tuple(self.fake_os.fd_kinds):
            try:
                self.fake_os.close(fd)
            except OSError:
                pass

    def close(self):
        self.close_open_fds()
        self.temp.cleanup()


class EmbeddedTargetGateTests(unittest.TestCase):
    def test_rendered_stage_flash_and_failure_dispatch(self):
        """Run the exact remote body with import/syscall shims over a temp FS.

        The rendered operation is executed unchanged. Only its imported sys,
        pathlib, os, and fcntl interfaces are redirected to a temporary tree;
        a regular file models the RECOVERY block device and reports the
        reviewed 259:0 identity and capacity. SSH is not involved.
        """
        sandbox = RenderedRemoteSandbox()
        try:
            before = sandbox.device_write_bytes
            stage = sandbox.execute("stage", candidate_input=True)
            self.assertEqual(
                stage.stdout.count("\n"), 1,
                f"stdout={stage.stdout!r}; error={type(stage.error).__name__}: {stage.error}",
            )
            stage_receipt = json.loads(stage.stdout)
            self.assertEqual(stage_receipt, {
                "mode": "stage", "partition_written": False,
                "backup_sha256": sandbox.base_sha,
                "candidate_sha256": sandbox.candidate_sha,
            })
            self.assertEqual(sandbox.device_write_bytes, before)
            rollback = sandbox.stage_dir / "rollback.img"
            candidate = sandbox.stage_dir / "recovery.img"
            self.assertEqual(rollback.stat().st_size, sandbox.SIZE)
            self.assertEqual(candidate.stat().st_size, sandbox.SIZE)
            self.assertEqual(sandbox.hash_file(rollback), sandbox.base_sha)
            self.assertEqual(sandbox.hash_file(candidate), sandbox.candidate_sha)
            self.assertIsNone(
                stage.error,
                "rendered stage emitted its verified staging receipt, then failed: "
                f"{type(stage.error).__name__}: {stage.error}; stdout={stage.stdout!r}",
            )

            flash = sandbox.execute("flash")
            self.assertIsNone(flash.error, f"rendered flash failed: {flash.error}; {flash.stdout!r}")
            self.assertEqual(flash.stdout.count("\n"), 1, flash.stdout)
            self.assertEqual(json.loads(flash.stdout), {
                "mode": "flash", "partition_written": "recovery",
                "bytes": sandbox.SIZE, "before_sha256": sandbox.base_sha,
                "readback_sha256": sandbox.candidate_sha,
                "reboot_performed": False,
            })
            self.assertEqual(sandbox.device_write_bytes - before, sandbox.SIZE)

            writes_before_refusals = sandbox.device_write_bytes
            invalid_mode = sandbox.execute("invalid")
            self.assertIsNotNone(invalid_mode.error)
            self.assertIn("unknown operation", str(invalid_mode.error))
            self.assertEqual(invalid_mode.stdout, "")
            self.assertEqual(sandbox.device_write_bytes, writes_before_refusals)

            sandbox.set_pid1("unexpected-init")
            failed_validation = sandbox.execute("flash")
            self.assertIsNotNone(failed_validation.error)
            self.assertIn("unexpected PID 1", str(failed_validation.error))
            self.assertEqual(failed_validation.stdout, "")
            self.assertEqual(sandbox.device_write_bytes, writes_before_refusals)

            sandbox.set_pid1("native-guardian")
            sandbox.write_partition(sandbox.BASE_BYTE)
            sandbox.fail_partition_write_after = 4096
            partial_flash = sandbox.execute("flash")
            self.assertIsNotNone(partial_flash.error)
            self.assertIn("outcome may be partial", str(partial_flash.error))
            self.assertEqual(partial_flash.stdout, "")
            self.assertEqual(sandbox.device_write_bytes - writes_before_refusals, 4096)
            with sandbox.partition.open("rb") as stream:
                self.assertEqual(stream.read(4096), bytes([sandbox.CANDIDATE_BYTE]) * 4096)
                self.assertEqual(stream.read(4096), bytes([sandbox.BASE_BYTE]) * 4096)
        finally:
            sandbox.close()

    def test_partial_staging_write_emits_no_success_receipt_and_never_flashes(self):
        sandbox = RenderedRemoteSandbox()
        sandbox.fail_staged_write_after = 4096
        try:
            result = sandbox.execute("stage", candidate_input=True)
            self.assertIsNotNone(result.error)
            self.assertIn("staging did not complete", str(result.error))
            self.assertIn("outcome may be partial", str(result.error))
            self.assertEqual(result.stdout, "")
            self.assertEqual(sandbox.current_operation_staged_writes, 4096)
            self.assertEqual(sandbox.device_write_bytes, 0)
        finally:
            sandbox.close()

    def _optimized_probe(self, mode):
        probe = r'''import importlib.util,sys
path=sys.argv[1]
spec=importlib.util.spec_from_file_location("target",path)
mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
import os,stat
ns={"os":os,"stat":stat};exec(mod.REMOTE_GUARDS,ns)
try:
 ns["validate_recovery_target"]("native-guardian","5.10.260-g4e5c5ad7d950",278,
  "/dev/sda17",True,196608,True,2590,False,2590,196608)
except RuntimeError as error:
 print("REFUSED:"+str(error));sys.exit(23)
print("UNSAFE_ACCEPT");sys.exit(0)
'''
        env = os.environ.copy()
        argv = [sys.executable]
        if mode == "-O":
            argv.append("-O")
        elif mode == "PYTHONOPTIMIZE=1":
            env["PYTHONOPTIMIZE"] = "1"
        argv.extend(["-c", probe, str(ROOT / "tools/hardware/deploy-audio-recovery.py")])
        return subprocess.run(argv, text=True, capture_output=True, env=env, check=False)

    def test_wrong_partition_identity_is_refused_normally(self):
        result = self._optimized_probe("normal")
        self.assertEqual(result.returncode, 23, result.stdout + result.stderr)
        self.assertIn("alias does not resolve", result.stdout)

    def test_wrong_partition_identity_is_refused_with_python_o(self):
        result = self._optimized_probe("-O")
        self.assertEqual(result.returncode, 23, result.stdout + result.stderr)
        self.assertIn("alias does not resolve", result.stdout)

    def test_wrong_partition_identity_is_refused_with_pythonoptimize(self):
        result = self._optimized_probe("PYTHONOPTIMIZE=1")
        self.assertEqual(result.returncode, 23, result.stdout + result.stderr)
        self.assertIn("alias does not resolve", result.stdout)

    def test_remote_write_gate_contains_no_assertions(self):
        compile(BASE.REMOTE, "remote-deployer", "exec", optimize=1)
        self.assertNotIn("assert ", BASE.REMOTE)
        self.assertIn("flock(lockfd", BASE.REMOTE)
        self.assertIn("RECOVERY write; do not retry or reboot", BASE.REMOTE)

    def test_manifest_renderer_rejects_unsafe_values_and_resolves_all_markers(self):
        rendered = BASE.render_remote(
            base_sha="a" * 64, new_sha="b" * 64,
            staging_directory="/srv/s22/test-candidate",
            rollback_filename="rollback.img",
        )
        self.assertIn("base_sha='" + "a" * 64 + "'", rendered)
        self.assertIn("new_sha='" + "b" * 64 + "'", rendered)
        self.assertIn("directory_name='test-candidate'", rendered)
        self.assertIn("dir_fd=parent_fd", rendered)
        self.assertNotIn("__S22_", rendered)
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            BASE.render_remote(base_sha="bad", new_sha="b" * 64,
                               staging_directory="/srv/s22/test", rollback_filename="rollback.img")
        with self.assertRaisesRegex(ValueError, "safe child"):
            BASE.render_remote(base_sha="a" * 64, new_sha="b" * 64,
                               staging_directory="/srv/s22/../outside", rollback_filename="rollback.img")

    def _guards(self):
        namespace = {"hashlib": hashlib, "os": os, "stat": __import__("stat")}
        exec(BASE.REMOTE_GUARDS, namespace)
        return namespace

    def test_staging_parent_must_be_root_owned_and_not_group_or_other_writable(self):
        validate = self._guards()["validate_directory_metadata"]
        valid = SimpleNamespace(st_mode=0o40755, st_uid=0)
        validate(valid, "staging parent", 0, private=False)
        with self.assertRaisesRegex(RuntimeError, "expected uid"):
            validate(SimpleNamespace(st_mode=0o40755, st_uid=1000), "staging parent", 0)
        with self.assertRaisesRegex(RuntimeError, "writable by group/other"):
            validate(SimpleNamespace(st_mode=0o40777, st_uid=0), "staging parent", 0)
        with self.assertRaisesRegex(RuntimeError, "real directory"):
            validate(SimpleNamespace(st_mode=0o120777, st_uid=0), "staging parent", 0)

    def test_staging_directory_open_is_anchored_and_detects_path_replacement(self):
        guards = self._guards()
        with tempfile.TemporaryDirectory(prefix="s22-staging-dirfd-") as temporary:
            parent = Path(temporary)
            parent_link = parent.parent / (parent.name + "-parent-link")
            parent_link.symlink_to(parent, target_is_directory=True)
            with self.assertRaisesRegex(RuntimeError, "not a real directory"):
                guards["open_parent_directory"](
                    parent_link, "test parent", expected_uid=os.geteuid())
            parent_link.unlink()
            parent_fd = guards["open_parent_directory"](
                parent, "test parent", expected_uid=os.geteuid())
            try:
                child_fd = guards["open_child_directory"](
                    parent_fd, "private-stage", "test staging", create=True,
                    expected_uid=os.geteuid())
                try:
                    guards["verify_child_directory"](
                        parent_fd, "private-stage", child_fd, "test staging",
                        expected_uid=os.geteuid())
                    moved = parent / "moved-stage"
                    (parent / "private-stage").rename(moved)
                    (parent / "private-stage").mkdir(mode=0o700)
                    with self.assertRaisesRegex(RuntimeError, "path was replaced"):
                        guards["verify_child_directory"](
                            parent_fd, "private-stage", child_fd, "test staging",
                            expected_uid=os.geteuid())
                finally:
                    os.close(child_fd)
            finally:
                os.close(parent_fd)

    def test_staging_helpers_use_no_follow_and_verify_private_file_bytes(self):
        guards = self._guards()
        with tempfile.TemporaryDirectory(prefix="s22-staging-files-") as temporary:
            parent = Path(temporary)
            parent_fd = guards["open_parent_directory"](
                parent, "test parent", expected_uid=os.geteuid())
            try:
                child_fd = guards["open_child_directory"](
                    parent_fd, "private-stage", "test staging", create=True,
                    expected_uid=os.geteuid())
                try:
                    content = b"rollback fixture"
                    digest = hashlib.sha256(content).hexdigest()
                    guards["write_staged_file"](
                        child_fd, "rollback.img", content, "rollback",
                        expected_uid=os.geteuid())
                    self.assertEqual(guards["read_staged_file"](
                        child_fd, "rollback.img", "rollback", digest, len(content),
                        expected_uid=os.geteuid()), content)
                    link = parent / "private-stage" / "rollback-link.img"
                    link.symlink_to(parent / "private-stage" / "rollback.img")
                    with self.assertRaises(OSError):
                        guards["read_staged_file"](
                            child_fd, "rollback-link.img", "rollback", digest, len(content),
                            expected_uid=os.geteuid())
                finally:
                    os.close(child_fd)
            finally:
                os.close(parent_fd)

    def test_parent_directory_fsync_follows_stage_mkdir_and_precedes_receipt(self):
        guards = self._guards()
        with tempfile.TemporaryDirectory(prefix="s22-stage-parent-fsync-") as temporary:
            parent = Path(temporary)
            parent_fd = guards["open_parent_directory"](
                parent, "test parent", expected_uid=os.geteuid())
            events = []
            def observe_parent_fsync(fd):
                self.assertEqual(fd, parent_fd)
                self.assertTrue((parent / "private-stage").is_dir())
                events.append("parent-fsync")
            try:
                child_fd = guards["create_durable_staging_directory"](
                    parent_fd, "private-stage", "test staging",
                    expected_uid=os.geteuid(), flush=observe_parent_fsync)
                os.close(child_fd)
                self.assertEqual(events, ["parent-fsync"])
                with self.assertRaisesRegex(RuntimeError, "fsync failed"):
                    guards["create_durable_staging_directory"](
                        parent_fd, "failed-stage", "test staging",
                        expected_uid=os.geteuid(),
                        flush=lambda _fd: (_ for _ in ()).throw(OSError("injected fsync failure")))
            finally:
                os.close(parent_fd)

        stage_start = BASE.REMOTE.index("if mode=='stage':")
        stage_end = BASE.REMOTE.index("elif mode=='flash':", stage_start)
        stage = BASE.REMOTE[stage_start:stage_end]
        create = stage.index("create_durable_staging_directory(parent_fd")
        receipt = stage.index("print(json.dumps({'mode':mode,'partition_written':False")
        helper_create = BASE.REMOTE_GUARDS.index("fd=open_child_directory(parent_fd,name,label,create=True")
        helper_sync = BASE.REMOTE_GUARDS.index("sync_directory(parent_fd,label+' parent',flush)")
        self.assertLess(create, receipt)
        self.assertLess(helper_create, helper_sync)

    def test_wrong_device_capacity_and_mounted_partition_are_refused(self):
        validate = self._guards()["validate_recovery_target"]
        good = ("native-guardian", "5.10.260-g4e5c5ad7d950", 278,
                "/dev/sda16", True, 196608, True, 2590, False, 2590, 196608)
        for values, text in (
            (good[:5] + (196607,) + good[6:], "capacity"),
            (good[:7] + (2591,) + good[8:], "exact block device"),
            (good[:8] + (True,) + good[9:], "mounted"),
        ):
            with self.subTest(text=text), self.assertRaisesRegex(RuntimeError, text):
                validate(*values)

    def test_readback_hash_mismatch_is_refused(self):
        validate_digest = self._guards()["validate_digest"]
        with self.assertRaisesRegex(RuntimeError, "RECOVERY readback SHA-256 mismatch"):
            validate_digest(b"wrong bytes", hashlib.sha256(b"expected").hexdigest(), "RECOVERY readback")

    def test_short_read_is_refused(self):
        read_all = self._guards()["read_all"]
        responses = iter((b"partial", b""))
        with self.assertRaisesRegex(RuntimeError, "short RECOVERY read"):
            read_all(1, 10, lambda _fd, _count: next(responses), "RECOVERY")

    def test_short_writes_are_completed_and_interrupted_write_fails_closed(self):
        write_all_fd = self._guards()["write_all_fd"]
        output = bytearray(6)
        def one_byte_writer(_fd, chunk, offset):
            output[offset:offset+1] = chunk[:1]
            return min(1, len(chunk))
        flushed = []
        self.assertEqual(write_all_fd(1, b"abcdef", one_byte_writer, lambda _fd: flushed.append(True), "test write"), 6)
        self.assertEqual(output, b"abcdef")
        self.assertEqual(flushed, [True])
        def interrupted(_fd, _chunk, offset):
            if offset >= 2:
                raise OSError("injected interruption")
            return 2
        with self.assertRaisesRegex(RuntimeError, "failed after 2 bytes"):
            write_all_fd(1, b"abcdef", interrupted, lambda _fd: None, "test write")

    def test_zero_write_and_fsync_failure_are_refused(self):
        write_all_fd = self._guards()["write_all_fd"]
        with self.assertRaisesRegex(RuntimeError, "made no progress"):
            write_all_fd(1, b"x", lambda _fd, _chunk, _offset: 0, lambda _fd: None, "test write")
        with self.assertRaisesRegex(RuntimeError, "fsync failed"):
            write_all_fd(1, b"x", lambda _fd, chunk, _offset: len(chunk),
                         lambda _fd: (_ for _ in ()).throw(OSError("injected fsync")), "test write")

    def test_insufficient_staging_space_is_refused(self):
        validate = self._guards()["validate_free_space"]
        with self.assertRaisesRegex(RuntimeError, "insufficient free staging bytes"):
            validate(100, 100, 100)
        with self.assertRaisesRegex(RuntimeError, "insufficient free staging inodes"):
            validate(10_000_000, 2, 100)

    def test_stale_receipt_is_refused_including_symlink(self):
        with tempfile.TemporaryDirectory(prefix="s22-receipt-test-") as directory:
            target = Path(directory) / "receipt.json"
            target.write_text("old receipt")
            with self.assertRaisesRegex(ValueError, "existing deployment receipt"):
                BASE.ensure_new_receipt(target)
            target.unlink()
            target.symlink_to(Path(directory) / "missing")
            with self.assertRaisesRegex(ValueError, "existing deployment receipt"):
                BASE.ensure_new_receipt(target)

    def test_wrapper_hash_pin_rejects_regular_shim_and_symlinks(self):
        with tempfile.TemporaryDirectory(prefix="s22-ssh-wrapper-") as directory:
            root = Path(directory)
            approved = root / "s22-ssh"
            approved.write_bytes((ROOT / "tools/s22-ssh").read_bytes())
            approved.chmod(0o755)
            fd = BASE.validate_approved_ssh_wrapper(approved)
            try:
                required = (fcntl.F_SEAL_SEAL | fcntl.F_SEAL_SHRINK
                            | fcntl.F_SEAL_GROW | fcntl.F_SEAL_WRITE)
                self.assertEqual(fcntl.fcntl(fd, fcntl.F_GET_SEALS) & required, required)
            finally:
                os.close(fd)
            approved.write_text("#!/usr/bin/env bash\nexit 0\n")
            approved.chmod(0o755)
            with self.assertRaisesRegex(ValueError, "content SHA-256 mismatch"):
                BASE.validate_approved_ssh_wrapper(approved)
            link = root / "approved-link"
            link.symlink_to(approved)
            with self.assertRaisesRegex(ValueError, "non-symlink executable"):
                BASE.validate_approved_ssh_wrapper(link)


class AvbVerificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="s22-avb-verify-")
        self.root = Path(self.temp.name)
        # Match the recovery partition name embedded in the AVB footer.
        self.image = self.root / "recovery.img"
        self.avbtool = Path(os.environ.get("S22_AVBTOOL", BUILDER.DEFAULT_AVBTOOL))

    def tearDown(self):
        self.temp.cleanup()

    def test_failed_avb_verifier_is_fatal(self):
        if not self.avbtool.is_file():
            self.skipTest(f"trusted avbtool unavailable: {self.avbtool}")
        self.image.write_bytes(b"synthetic candidate")
        failure = subprocess.CalledProcessError(1, ["pinned-avbtool"], stderr="footer mismatch")
        runner = mock.Mock(side_effect=failure)
        with self.assertRaisesRegex(RuntimeError, "trusted avbtool verify_image failed.*footer mismatch"):
            BUILDER.verify_image(self.avbtool, self.image, runner=runner)
        command = runner.call_args.args[0]
        self.assertRegex(command[1], r"/proc/self/fd/\d+$")
        self.assertEqual(runner.call_args.kwargs["pass_fds"], (int(command[1].rsplit("/", 1)[1]),))

    def test_untrusted_avbtool_shim_is_rejected_before_injected_runner(self):
        self.image.write_bytes(b"synthetic candidate")
        shim = self.root / "avbtool.py"
        shim.write_text("print('verified')\n")
        runner = mock.Mock()
        with self.assertRaisesRegex(RuntimeError, "avbtool identity mismatch"):
            BUILDER.verify_image(shim, self.image, runner=runner)
        runner.assert_not_called()

    def test_trusted_verifier_snapshot_is_sealed_against_mutation(self):
        if not self.avbtool.is_file():
            self.skipTest(f"trusted avbtool unavailable: {self.avbtool}")
        fd = BUILDER.open_trusted_avbtool(self.avbtool)
        try:
            required = (fcntl.F_SEAL_SEAL | fcntl.F_SEAL_SHRINK
                        | fcntl.F_SEAL_GROW | fcntl.F_SEAL_WRITE)
            self.assertEqual(fcntl.fcntl(fd, fcntl.F_GET_SEALS) & required, required)
            with self.assertRaises(OSError):
                os.write(fd, b"tamper")
        finally:
            os.close(fd)

    def test_candidate_builder_requires_verify_before_publication(self):
        source = (ROOT / "tools/hardware/build-bt-hci-recovery.py").read_text()
        footer = source.index('"add_hash_footer"')
        verify = source.index("avb_verification = verify_image(args.avbtool, candidate)")
        publish = source.rindex("out.mkdir()")
        self.assertLess(footer, verify)
        self.assertLess(verify, publish)

    def test_real_avb_footer_verifies_and_corruption_fails(self):
        if not self.avbtool.is_file():
            self.skipTest(f"avbtool.py unavailable: {self.avbtool}")
        self.image.write_bytes(bytes((index % 251 for index in range(512 * 1024))))
        BUILDER.run_trusted_avbtool(
            self.avbtool, "add_hash_footer", "--image", self.image,
            "--partition_size", str(2 * 1024 * 1024), "--partition_name", "recovery",
            "--algorithm", "NONE", "--rollback_index", "0", "--salt", "11" * 32,
        )
        verified = BUILDER.verify_image(self.avbtool, self.image)
        self.assertTrue(verified)
        damaged = bytearray(self.image.read_bytes())
        damaged[123] ^= 1
        self.image.write_bytes(damaged)
        with self.assertRaisesRegex(RuntimeError, "trusted avbtool verify_image failed"):
            BUILDER.verify_image(self.avbtool, self.image)


if __name__ == "__main__":
    unittest.main(verbosity=2)
