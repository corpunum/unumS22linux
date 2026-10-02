#!/usr/bin/env python3
"""Hardware-free fail-closed and process-cleanup checks for the NPU builder."""
from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

BUILDER_PATH = Path(__file__).with_name("build-npu-six-profile.py")
SPEC = importlib.util.spec_from_file_location("npu_profile_builder", BUILDER_PATH)
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


class NpuBuildProfileSafetyTests(unittest.TestCase):
    @staticmethod
    def git(source: Path, *args: str) -> str:
        result = subprocess.run(
            ["git", *args], cwd=source, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr)
        return result.stdout.strip()

    @staticmethod
    def group_exists(pgid: int) -> bool:
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    def spawn_group_with_sig_ignoring_descendant(
            self, *, leader_exits: bool) -> tuple[subprocess.Popen[str], int]:
        descendant_code = (
            "import signal,time; "
            "signal.signal(signal.SIGINT, signal.SIG_IGN); "
            "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
            "print('DESCENDANT_READY', flush=True); time.sleep(120)"
        )
        leader_code = (
            "import subprocess,sys,time\n"
            "descendant = subprocess.Popen([sys.executable, '-c', sys.argv[1]], "
            "stdout=subprocess.PIPE, text=True)\n"
            "ready = descendant.stdout.readline()\n"
            "print(f'DESCENDANT_PID={descendant.pid}', flush=True)\n"
            "if sys.argv[2] == 'exit': raise SystemExit(0)\n"
            "time.sleep(120)\n"
        )
        process = subprocess.Popen(
            [sys.executable, "-c", leader_code, descendant_code,
             "exit" if leader_exits else "wait"],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, start_new_session=True,
        )
        try:
            assert process.stdout is not None
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                if not selector.select(timeout=5):
                    raise AssertionError("controlled process group did not become ready")
                line = process.stdout.readline().strip()
            if not line.startswith("DESCENDANT_PID="):
                raise AssertionError(f"unexpected controlled leader output: {line!r}")
            return process, int(line.split("=", 1)[1])
        except BaseException:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                pass
            if process.stdout is not None:
                process.stdout.close()
            raise

    @staticmethod
    def kill_and_bounded_wait(process: subprocess.Popen[str]) -> None:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            pass

    def test_monitor_error_interrupts_and_confirms_owned_group_absence(self) -> None:
        process = FakeProcess(wait_results=[0])
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            phase: dict[str, object] = {"source_commit": "pinned"}
            with (
                mock.patch.object(BUILDER.subprocess, "Popen", return_value=process),
                mock.patch.object(BUILDER, "resource_sample", side_effect=OSError("disk sample failed")),
                mock.patch.object(BUILDER.time, "sleep", return_value=None),
                mock.patch.object(BUILDER, "process_group_exists", side_effect=[True, False]),
                mock.patch.object(BUILDER.os, "killpg") as killpg,
                self.assertRaisesRegex(OSError, "disk sample failed"),
            ):
                BUILDER.run_monitored_build(["make"], Path(directory), output, {}, phase)
            killpg.assert_called_once_with(process.pid, BUILDER.signal.SIGINT)
            self.assertEqual(process.wait_timeouts, [0])
            self.assertEqual(phase["phase"], "build_running")
            self.assertEqual(phase["make_pid"], process.pid)
            self.assertTrue((output / "build-phase.json").is_file())

    def test_monitor_error_is_preserved_when_group_cleanup_is_unconfirmed(self) -> None:
        process = FakeProcess()
        original_error = OSError("resource sample failed")
        stderr = io.StringIO()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            with (
                mock.patch.object(BUILDER.subprocess, "Popen", return_value=process),
                mock.patch.object(BUILDER, "resource_sample", side_effect=original_error),
                mock.patch.object(BUILDER.time, "sleep", return_value=None),
                mock.patch.object(BUILDER, "process_group_exists", return_value=True),
                mock.patch.object(BUILDER, "CLEANUP_STAGE_TIMEOUTS", (0, 0, 0)),
                mock.patch.object(BUILDER.os, "killpg"),
                contextlib.redirect_stderr(stderr),
                self.assertRaises(OSError) as raised,
            ):
                BUILDER.run_monitored_build(["make"], Path(directory), output, {})
        self.assertIs(raised.exception, original_error)
        self.assertIn("owned process-group absence was not confirmed", stderr.getvalue())

    def test_cleanup_exception_does_not_replace_original_monitor_exception(self) -> None:
        process = FakeProcess()
        original_error = OSError("monitor sampling failed")
        stderr = io.StringIO()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            with (
                mock.patch.object(BUILDER.subprocess, "Popen", return_value=process),
                mock.patch.object(BUILDER, "resource_sample", side_effect=original_error),
                mock.patch.object(BUILDER.time, "sleep", return_value=None),
                mock.patch.object(BUILDER, "stop_own_process_group",
                                  side_effect=RuntimeError("cleanup sampling failed")),
                contextlib.redirect_stderr(stderr),
                self.assertRaises(OSError) as raised,
            ):
                BUILDER.run_monitored_build(["make"], Path(directory), output, {})
        self.assertIs(raised.exception, original_error)
        self.assertIn("cleanup raised RuntimeError: cleanup sampling failed", stderr.getvalue())

    def test_process_group_escalation_uses_only_bounded_waits(self) -> None:
        process = FakeProcess(wait_results=[-9])
        with (
            mock.patch.object(BUILDER.os, "killpg") as killpg,
            mock.patch.object(BUILDER, "process_group_exists", side_effect=[True, True, True, False]),
            mock.patch.object(BUILDER, "CLEANUP_STAGE_TIMEOUTS", (0, 0, 0)),
            mock.patch.object(BUILDER.time, "sleep", return_value=None),
        ):
            self.assertTrue(BUILDER.stop_own_process_group(process))
        self.assertEqual(
            [call.args[1] for call in killpg.call_args_list],
            [BUILDER.signal.SIGINT, BUILDER.signal.SIGTERM, BUILDER.signal.SIGKILL],
        )
        self.assertEqual(process.wait_timeouts, [0])

    def test_unreaped_group_is_reported_after_bounded_sigkill_wait(self) -> None:
        process = FakeProcess()
        with (
            mock.patch.object(BUILDER.os, "killpg") as killpg,
            mock.patch.object(BUILDER, "process_group_exists", return_value=True),
            mock.patch.object(BUILDER, "CLEANUP_STAGE_TIMEOUTS", (0, 0, 0)),
        ):
            self.assertFalse(BUILDER.stop_own_process_group(process))
        self.assertEqual(
            [call.args[1] for call in killpg.call_args_list],
            [BUILDER.signal.SIGINT, BUILDER.signal.SIGTERM, BUILDER.signal.SIGKILL],
        )
        self.assertEqual(process.wait_timeouts, [])

    def test_real_group_cleanup_continues_after_leader_exits_first(self) -> None:
        process, descendant_pid = self.spawn_group_with_sig_ignoring_descendant(
            leader_exits=False)
        try:
            self.assertEqual(os.getpgid(descendant_pid), process.pid)
            with mock.patch.object(
                    BUILDER, "CLEANUP_STAGE_TIMEOUTS", (0.5, 0.5, 2.0), create=True):
                self.assertTrue(BUILDER.stop_own_process_group(process))
            self.assertIsNotNone(process.poll())
            self.assertFalse(self.group_exists(process.pid))
        finally:
            self.kill_and_bounded_wait(process)
            if process.stdout is not None:
                process.stdout.close()

    def test_real_cleanup_checks_residual_group_after_leader_was_reaped(self) -> None:
        process, descendant_pid = self.spawn_group_with_sig_ignoring_descendant(
            leader_exits=True)
        try:
            self.assertEqual(os.getpgid(descendant_pid), process.pid)
            process.wait(timeout=3)
            self.assertTrue(self.group_exists(process.pid))
            with mock.patch.object(
                    BUILDER, "CLEANUP_STAGE_TIMEOUTS", (0.5, 0.5, 2.0), create=True):
                self.assertTrue(BUILDER.stop_own_process_group(process))
            self.assertFalse(self.group_exists(process.pid))
        finally:
            self.kill_and_bounded_wait(process)
            if process.stdout is not None:
                process.stdout.close()

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
                mock.patch.object(BUILDER, "process_group_exists", side_effect=[True, False]),
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

    def test_builder_receipt_uses_captured_launch_bytes(self) -> None:
        launch_digest = "a" * 64
        phase = {"executed_builder_sha256": launch_digest}
        with mock.patch.object(BUILDER, "sha256", side_effect=AssertionError("late file read")):
            self.assertEqual(BUILDER.builder_hash_from_launch(phase), launch_digest)
        with self.assertRaisesRegex(BUILDER.BuildError, "lacks the builder SHA-256"):
            BUILDER.builder_hash_from_launch({})
        with self.assertRaisesRegex(BUILDER.BuildError, "lacks the builder SHA-256"):
            BUILDER.builder_hash_from_launch({"executed_builder_sha256": "_" * 64})

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
                    BUILDER.verify_source(source, BUILDER.PROFILES["six"])
            with mock.patch.object(BUILDER, "git", side_effect=[
                    "head", "head unrelated-parent"]):
                with self.assertRaisesRegex(
                        BUILDER.BuildError, "directly on the pinned native HCI/camera base"):
                    BUILDER.verify_source(source, BUILDER.PROFILES["native-eight"])

    def test_profiles_keep_six_defaults_and_pin_native_eight_separately(self) -> None:
        default_args = BUILDER.parse_args([])
        self.assertEqual(default_args.profile, "six")
        self.assertIsNone(default_args.kernel_source)
        self.assertIsNone(default_args.output)
        self.assertIsNone(default_args.config)

        six = BUILDER.PROFILES["six"]
        native = BUILDER.PROFILES["native-eight"]
        self.assertEqual(six.source_base_commit, BUILDER.BASE_COMMIT)
        self.assertEqual(six.patches, BUILDER.PATCHES)
        self.assertEqual(six.default_source, BUILDER.DEFAULT_SOURCE)
        self.assertEqual(six.default_output, BUILDER.DEFAULT_OUTPUT)
        self.assertEqual(six.output_prefix, "npu-six-patch-out-")

        native_args = BUILDER.parse_args(["--profile", "native-eight"])
        self.assertEqual(native_args.profile, "native-eight")
        self.assertEqual(native.source_base_commit, BUILDER.NATIVE_EIGHT_SOURCE_BASE)
        self.assertEqual(native.patches, BUILDER.NATIVE_EIGHT_PATCHES)
        self.assertEqual(len(native.patches), 8)
        self.assertEqual(
            [name for name, _digest in native.patches[-2:]],
            ["npu-mailbox-missing-callback-reclaim.patch",
             "npu-mailbox-debug-walk-bounds.patch"],
        )
        self.assertEqual(native.config_sha256, BUILDER.NATIVE_EIGHT_CONFIG_SHA256)
        self.assertNotEqual(native.config_sha256, six.config_sha256)
        self.assertNotEqual(native.default_source, six.default_source)
        self.assertNotEqual(native.default_output, six.default_output)
        self.assertEqual(native.default_output.name,
                         "npu-native-eight-out-clang18-20261002")
        self.assertEqual(native.output_prefix, "npu-native-eight-out-clang18-")
        self.assertEqual(six.toolchain_bin, BUILDER.TOOLCHAIN_BIN)
        self.assertEqual(six.clang_sha256, BUILDER.CLANG_SHA256)
        self.assertEqual(six.ld_lld_sha256, BUILDER.LD_LLD_SHA256)
        self.assertEqual(six.llvm_tools, BUILDER.LLVM_TOOLS)
        self.assertEqual(six.llvm_tool_sha256, ())
        self.assertEqual(native.toolchain_bin, Path("/usr/lib/llvm-18/bin"))
        self.assertNotEqual(native.toolchain_bin, six.toolchain_bin)
        self.assertEqual(native.clang_sha256, BUILDER.NATIVE_EIGHT_CLANG_SHA256)
        self.assertEqual(native.ld_lld_sha256, BUILDER.NATIVE_EIGHT_LD_LLD_SHA256)
        self.assertEqual(native.clang_version_first_line,
                         "Ubuntu clang version 18.1.3 (1ubuntu1)")
        self.assertEqual(native.lld_version_first_line,
                         "Ubuntu LLD 18.1.3 (compatible with GNU linkers)")
        self.assertEqual(native.llvm_tools, BUILDER.NATIVE_EIGHT_LLVM_TOOLS)
        self.assertEqual(dict(native.llvm_tool_sha256),
                         BUILDER.NATIVE_EIGHT_LLVM_TOOL_SHA256)

    def test_profile_toolchain_version_guard_rejects_wrong_compiler(self) -> None:
        six = BUILDER.PROFILES["six"]
        native = BUILDER.PROFILES["native-eight"]
        clang21 = (
            "Android (14054515, +pgo, +bolt, +lto, +mlgo, based on r563880c) "
            "clang version 21.0.0"
        )
        clang18 = "Ubuntu clang version 18.1.3 (1ubuntu1)"
        lld18 = "Ubuntu LLD 18.1.3 (compatible with GNU linkers)"
        BUILDER.verify_toolchain_versions(six, clang21)
        BUILDER.verify_toolchain_versions(native, clang18, lld18)
        with self.assertRaisesRegex(BUILDER.BuildError, "unexpected native-eight Clang"):
            BUILDER.verify_toolchain_versions(native, clang21, lld18)
        with self.assertRaisesRegex(BUILDER.BuildError, "unexpected native-eight LLD"):
            BUILDER.verify_toolchain_versions(native, clang18, "LLD 21.0.0")
        with self.assertRaisesRegex(BUILDER.BuildError, "unexpected Android Clang"):
            BUILDER.verify_toolchain_versions(six, clang18)

    def test_patch_hash_manifest_fails_closed_and_keeps_ownership_excluded(self) -> None:
        six = BUILDER.PROFILES["six"]
        native = BUILDER.PROFILES["native-eight"]
        six_inputs = BUILDER.verify_patch_inputs(six.patches)
        native_inputs = BUILDER.verify_patch_inputs(native.patches)
        self.assertEqual(len(six_inputs), 7)
        self.assertEqual(len(native_inputs), 9)
        self.assertEqual(
            [item["name"] for item in native_inputs[:-1]],
            [name for name, _digest in native.patches],
        )
        self.assertEqual(native_inputs[-1]["name"],
                         BUILDER.FROZEN_OWNERSHIP_PATCH[0])
        self.assertNotIn(BUILDER.FROZEN_OWNERSHIP_PATCH[0],
                         [name for name, _digest in native.patches])
        native_receipt = BUILDER.patch_receipt_fields(native, native_inputs)
        self.assertEqual(len(native_receipt["patches"]), 8)
        self.assertEqual(len(native_receipt["excluded_patch_inputs"]), 1)
        self.assertEqual(
            native_receipt["excluded_patch_inputs"][0]["name"],
            BUILDER.FROZEN_OWNERSHIP_PATCH[0],
        )
        six_receipt = BUILDER.patch_receipt_fields(six, six_inputs)
        self.assertEqual(len(six_receipt["patches"]), 7)
        self.assertNotIn("excluded_patch_inputs", six_receipt)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            patch_dir = root / "tools/hardware"
            patch_dir.mkdir(parents=True)
            for name, _digest in (*native.patches, BUILDER.FROZEN_OWNERSHIP_PATCH):
                source_patch = BUILDER.ROOT / "tools/hardware" / name
                (patch_dir / name).write_bytes(source_patch.read_bytes())
            tampered = patch_dir / native.patches[-1][0]
            tampered.write_bytes(tampered.read_bytes() + b"tamper\n")
            with mock.patch.object(BUILDER, "ROOT", root):
                with self.assertRaisesRegex(BUILDER.BuildError, "patch SHA-256 mismatch"):
                    BUILDER.verify_patch_inputs(native.patches)

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

            six_head = ""
            for index, (name, _digest) in enumerate(
                    BUILDER.NATIVE_EIGHT_PATCHES, start=1):
                tracked.write_text(f"profile-{index}\n")
                patch = self.git(source, "diff", "--binary")
                (patch_dir / name).write_text(patch + "\n")
                self.git(source, "add", "fixture.c")
                self.git(source, "commit", "-qm", f"fixture patch {index}")
                if index == len(BUILDER.PATCHES):
                    six_head = self.git(source, "rev-parse", "HEAD")
            eight_head = self.git(source, "rev-parse", "HEAD")

            with mock.patch.object(BUILDER, "ROOT", patch_root):
                self.git(source, "checkout", "--detach", six_head)
                BUILDER.verify_patch_tree(
                    source, source_base_commit=base,
                    patches=BUILDER.PATCHES, stack_label="six-patch",
                )
                with self.assertRaisesRegex(BUILDER.BuildError,
                                            "ordered native-eight NPU patch stack"):
                    BUILDER.verify_patch_tree(
                        source, source_base_commit=base,
                        patches=BUILDER.NATIVE_EIGHT_PATCHES,
                        stack_label="native-eight NPU patch",
                    )
                self.git(source, "checkout", "--detach", eight_head)
                BUILDER.verify_patch_tree(
                    source, source_base_commit=base,
                    patches=BUILDER.NATIVE_EIGHT_PATCHES,
                    stack_label="native-eight NPU patch",
                )
                with self.assertRaisesRegex(BUILDER.BuildError,
                                            "ordered six-patch stack"):
                    BUILDER.verify_patch_tree(
                        source, source_base_commit=base,
                        patches=BUILDER.PATCHES, stack_label="six-patch",
                    )
                with mock.patch.object(BUILDER, "NATIVE_EIGHT_PATCHES",
                                       tuple(reversed(BUILDER.NATIVE_EIGHT_PATCHES))):
                    with self.assertRaisesRegex(BUILDER.BuildError, "command failed"):
                        BUILDER.verify_patch_tree(
                            source, source_base_commit=base,
                            patches=BUILDER.NATIVE_EIGHT_PATCHES,
                            stack_label="native-eight NPU patch",
                        )

                (source / "off-stack.txt").write_text("unexpected tree content\n")
                self.git(source, "add", "off-stack.txt")
                self.git(source, "commit", "-qm", "unexpected source change")
                with self.assertRaisesRegex(
                        BUILDER.BuildError,
                        "not exactly the ordered native-eight NPU patch stack"):
                    BUILDER.verify_patch_tree(
                        source, source_base_commit=base,
                        patches=BUILDER.NATIVE_EIGHT_PATCHES,
                        stack_label="native-eight NPU patch",
                    )

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
            native_output = root / "npu-native-eight-out-clang18-fresh"
            BUILDER.validate_output_path(native_output, root,
                                         "npu-native-eight-out-clang18-")
            with self.assertRaisesRegex(BUILDER.BuildError,
                                        "npu-native-eight-out-clang18-"):
                BUILDER.validate_output_path(
                    root / "npu-native-eight-out-20261002", root,
                    "npu-native-eight-out-clang18-",
                )
            with self.assertRaisesRegex(BUILDER.BuildError,
                                        "npu-native-eight-out-clang18-"):
                BUILDER.validate_output_path(
                    root / "npu-six-patch-out-cross-profile", root,
                    "npu-native-eight-out-clang18-",
                )

    def test_config_preflight_rejects_hash_and_required_option_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".config"
            path.write_text("CONFIG_EXYNOS_NPU=m\nCONFIG_LTO_CLANG_THIN=y\n")
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
            with self.assertRaisesRegex(BUILDER.BuildError, "SHA-256 mismatch"):
                BUILDER.verify_config(path, "0" * 64, ())
            with self.assertRaisesRegex(BUILDER.BuildError, "lacks required"):
                BUILDER.verify_config(path, actual, ("CONFIG_CFI_CLANG=y",))
            with self.assertRaisesRegex(BUILDER.BuildError, "SHA-256 mismatch"):
                BUILDER.verify_config(
                    path, BUILDER.PROFILES["native-eight"].config_sha256,
                    BUILDER.PROFILES["native-eight"].required_config,
                )

    def test_native_profile_has_no_config_fallback(self) -> None:
        native = BUILDER.PROFILES["native-eight"]
        self.assertEqual(native.config_sha256,
                         "d762d5fc71e369013ee36657d007063ce1f0faca9707d5f9ccfba2597b7fcd16")
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / ".config"
            with self.assertRaisesRegex(BUILDER.BuildError, "preserved config is missing"):
                BUILDER.verify_config(missing, native.config_sha256,
                                      native.required_config)

    def test_native_profile_pins_every_llvm_and_gnu_tool_hash(self) -> None:
        llvm_tools = [
            {"name": name, "sha256": digest}
            for name, digest in BUILDER.NATIVE_EIGHT_LLVM_TOOL_SHA256.items()
        ]
        cross_tools = [
            {"name": name, "sha256": digest}
            for name, digest in BUILDER.NATIVE_EIGHT_GNU_TOOL_SHA256.items()
        ]
        self.assertEqual(
            set(BUILDER.NATIVE_EIGHT_LLVM_TOOL_SHA256),
            set(BUILDER.NATIVE_EIGHT_LLVM_TOOLS),
        )
        BUILDER.verify_profile_toolchain_identities(
            BUILDER.PROFILES["native-eight"], llvm_tools, cross_tools
        )

        llvm_tools[0]["sha256"] = "0" * 64
        with self.assertRaisesRegex(BUILDER.BuildError,
                                    "native-eight pinned LLVM tool hash mismatch"):
            BUILDER.verify_profile_toolchain_identities(
                BUILDER.PROFILES["native-eight"], llvm_tools, cross_tools
            )

        llvm_tools[0]["sha256"] = BUILDER.NATIVE_EIGHT_LLVM_TOOL_SHA256[
            str(llvm_tools[0]["name"])
        ]
        cross_tools[0]["sha256"] = "0" * 64
        with self.assertRaisesRegex(BUILDER.BuildError,
                                    "native-eight pinned GNU cross tool hash mismatch"):
            BUILDER.verify_profile_toolchain_identities(
                BUILDER.PROFILES["native-eight"], llvm_tools, cross_tools
            )

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
