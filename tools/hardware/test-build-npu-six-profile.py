#!/usr/bin/env python3
"""Hardware-free fail-closed and process-cleanup checks for the NPU builder."""
from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

BUILDER_PATH = Path(__file__).with_name("build-npu-six-profile.py")
SPEC = importlib.util.spec_from_file_location("npu_six_profile_builder", BUILDER_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("cannot load NPU six-profile builder")
BUILDER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = BUILDER
SPEC.loader.exec_module(BUILDER)


class FakeProcess:
    def __init__(self, pid: int = 4321, *, wait_results: list[object] | None = None):
        self.pid = pid
        self.wait_results = list(wait_results or [0])
        self.wait_timeouts: list[float | None] = []
        self.returncode: int | None = None

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        self.wait_timeouts.append(timeout)
        result = self.wait_results.pop(0)
        if isinstance(result, subprocess.TimeoutExpired):
            raise result
        self.returncode = int(result)
        return self.returncode


class NpuSixBuildSafetyTests(unittest.TestCase):
    @staticmethod
    def git(source: Path, *args: str) -> str:
        result = subprocess.run(
            ["git", *args], cwd=source, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr)
        return result.stdout.strip()

    def test_monitor_error_interrupts_and_reaps_owned_process_group(self) -> None:
        process = FakeProcess(wait_results=[0])
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            phase: dict[str, object] = {"source_commit": "pinned"}
            with (
                mock.patch.object(BUILDER.subprocess, "Popen", return_value=process),
                mock.patch.object(BUILDER, "resource_sample", side_effect=OSError("disk sample failed")),
                mock.patch.object(BUILDER.time, "sleep", return_value=None),
                mock.patch.object(BUILDER.os, "killpg") as killpg,
                self.assertRaisesRegex(OSError, "disk sample failed"),
            ):
                BUILDER.run_monitored_build(["make"], Path(directory), output, {}, phase)
            killpg.assert_called_once_with(process.pid, BUILDER.signal.SIGINT)
            self.assertEqual(process.wait_timeouts, [30])
            self.assertEqual(phase["phase"], "build_running")
            self.assertEqual(phase["make_pid"], process.pid)
            self.assertTrue((output / "build-phase.json").is_file())

    def test_process_group_escalation_uses_only_bounded_waits(self) -> None:
        process = FakeProcess(wait_results=[
            subprocess.TimeoutExpired("make", 30),
            subprocess.TimeoutExpired("make", 15),
            -9,
        ])
        with mock.patch.object(BUILDER.os, "killpg") as killpg:
            self.assertTrue(BUILDER.stop_own_process_group(process))
        self.assertEqual(
            [call.args[1] for call in killpg.call_args_list],
            [BUILDER.signal.SIGINT, BUILDER.signal.SIGTERM, BUILDER.signal.SIGKILL],
        )
        self.assertEqual(process.wait_timeouts, [30, 15, 5])

    def test_unreaped_group_is_reported_after_bounded_sigkill_wait(self) -> None:
        process = FakeProcess(wait_results=[
            subprocess.TimeoutExpired("make", 30),
            subprocess.TimeoutExpired("make", 15),
            subprocess.TimeoutExpired("make", 5),
        ])
        with mock.patch.object(BUILDER.os, "killpg"):
            self.assertFalse(BUILDER.stop_own_process_group(process))
        self.assertEqual(process.wait_timeouts, [30, 15, 5])

    def test_resource_abort_stops_only_the_owned_build(self) -> None:
        process = FakeProcess(wait_results=[-2])
        low = {
            "time_utc": "test",
            "elapsed_seconds": 10.0,
            "mem_available_bytes": BUILDER.MIN_REMAINING_MEM - 1,
            "disk_free_bytes": BUILDER.MIN_REMAINING_DISK + 1,
        }
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            with (
                mock.patch.object(BUILDER.subprocess, "Popen", return_value=process),
                mock.patch.object(BUILDER, "resource_sample", return_value=low),
                mock.patch.object(BUILDER.time, "sleep", return_value=None),
                mock.patch.object(BUILDER.os, "killpg") as killpg,
                contextlib.redirect_stdout(io.StringIO()),
            ):
                result = BUILDER.run_monitored_build(["make"], Path(directory), output, {})
        self.assertEqual(result, (-2, True))
        killpg.assert_called_once_with(process.pid, BUILDER.signal.SIGINT)

    def test_tail_keeps_only_last_lines_without_read_text(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "large.log"
            path.write_text("".join(f"line-{index}\n" for index in range(20000)))
            with mock.patch.object(Path, "read_text", side_effect=AssertionError("whole-file read")):
                self.assertEqual(BUILDER.tail(path, 2), "line-19998\nline-19999")

    def test_source_preflight_rejects_dirty_tree(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            responses = [
                "head", f"head {BUILDER.BASE_COMMIT}", " M drivers/vision/npu/core/npu-device.c",
            ]
            with mock.patch.object(BUILDER, "git", side_effect=responses), \
                    mock.patch.object(BUILDER, "verify_patch_tree") as replay:
                with self.assertRaisesRegex(BUILDER.BuildError, "must be clean"):
                    BUILDER.verify_source(source)
            replay.assert_not_called()

    def test_source_preflight_rejects_wrong_parent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            with mock.patch.object(BUILDER, "git", side_effect=[
                    "head", "head unrelated-parent"]):
                with self.assertRaisesRegex(BUILDER.BuildError, "directly on the pinned base"):
                    BUILDER.verify_source(source)

    def test_patched_tree_gate_replays_only_the_exact_ordered_stack(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            source = temp / "kernel"
            source.mkdir()
            patch_root = temp / "helper-root"
            patch_dir = patch_root / "tools/hardware"
            patch_dir.mkdir(parents=True)
            self.git(source, "init", "-q")
            self.git(source, "config", "user.name", "NPU Test")
            self.git(source, "config", "user.email", "npu-test@example.invalid")
            tracked = source / "fixture.c"
            tracked.write_text("profile-0\n")
            self.git(source, "add", "fixture.c")
            self.git(source, "commit", "-qm", "pinned fixture")
            base = self.git(source, "rev-parse", "HEAD")

            for index, (name, _digest) in enumerate(BUILDER.PATCHES, start=1):
                tracked.write_text(f"profile-{index}\n")
                patch = self.git(source, "diff", "--binary")
                (patch_dir / name).write_text(patch + "\n")
                self.git(source, "add", "fixture.c")
                self.git(source, "commit", "-qm", f"fixture patch {index}")

            with mock.patch.object(BUILDER, "ROOT", patch_root), \
                    mock.patch.object(BUILDER, "BASE_COMMIT", base):
                BUILDER.verify_patch_tree(source)
                with mock.patch.object(BUILDER, "PATCHES", tuple(reversed(BUILDER.PATCHES))):
                    with self.assertRaisesRegex(BUILDER.BuildError, "command failed"):
                        BUILDER.verify_patch_tree(source)

                (source / "off-stack.txt").write_text("unexpected tree content\n")
                self.git(source, "add", "off-stack.txt")
                self.git(source, "commit", "-qm", "unexpected source change")
                with self.assertRaisesRegex(BUILDER.BuildError, "not exactly the ordered six-patch stack"):
                    BUILDER.verify_patch_tree(source)

    def test_output_preflight_rejects_reuse_and_unscoped_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "builds"
            root.mkdir()
            with self.assertRaisesRegex(BUILDER.BuildError, "under"):
                BUILDER.validate_output_path(Path(directory) / "elsewhere" / "npu-six-patch-out-x", root)
            existing = root / "npu-six-patch-out-existing"
            existing.mkdir()
            with self.assertRaisesRegex(BUILDER.BuildError, "refusing to reuse"):
                BUILDER.validate_output_path(existing, root)
            linked = root / "npu-six-patch-out-link"
            linked.symlink_to(root / "npu-six-patch-out-target")
            with self.assertRaisesRegex(BUILDER.BuildError, "symbolic-link"):
                BUILDER.validate_output_path(linked, root)

    def test_config_preflight_rejects_hash_and_required_option_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".config"
            path.write_text("CONFIG_EXYNOS_NPU=m\nCONFIG_LTO_CLANG_THIN=y\n")
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
            with self.assertRaisesRegex(BUILDER.BuildError, "SHA-256 mismatch"):
                BUILDER.verify_config(path, "0" * 64, ())
            with self.assertRaisesRegex(BUILDER.BuildError, "lacks required"):
                BUILDER.verify_config(path, actual, ("CONFIG_CFI_CLANG=y",))

    def test_toolchain_receipt_resolves_symlinks_and_hashes_payloads(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in BUILDER.LLVM_TOOLS:
                path = root / name
                path.write_bytes((name + "-payload").encode())
                path.chmod(0o755)
            (root / "ld.lld.real").write_bytes(b"linker-payload")
            (root / "ld.lld.real").chmod(0o755)
            (root / "ld.lld").unlink()
            (root / "ld.lld").symlink_to("ld.lld.real")
            identities = BUILDER.toolchain_identities(root)
        linker = next(item for item in identities if item["name"] == "ld.lld")
        self.assertTrue(str(linker["resolved_path"]).endswith("ld.lld.real"))
        self.assertEqual(linker["sha256"], hashlib.sha256(b"linker-payload").hexdigest())

    def test_jobs_preflight_rejects_more_than_two(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as raised:
                BUILDER.parse_args(["--jobs", "3"])
        self.assertEqual(raised.exception.code, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
