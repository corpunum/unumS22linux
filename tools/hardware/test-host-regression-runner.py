#!/usr/bin/env python3
"""Unit tests for the fixed host-regression runner policy."""
from __future__ import annotations

import contextlib
import io
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).with_name("run-host-regressions.py")
SPEC = importlib.util.spec_from_file_location("host_regression_runner", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise ImportError(f"could not load runner at {SCRIPT}")
runner = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = runner
SPEC.loader.exec_module(runner)

EXPECTED_HOST_TEST_PATHS = (
    "tools/hardware/test-npu-session-lifecycle.py",
    "tools/hardware/test-npu-boot-preflight.py",
    "tools/hardware/test-audio-progress-snapshot.py",
    "tools/hardware/test-input-power-readiness.py",
    "tools/hardware/test-recovery-deployment-hardening.py",
    "tools/hardware/test-bt-h4-ibs-bridge.py",
    "tools/hardware/test-bt-qca6490-patch-receipt.py",
    "tools/hardware/test-close-range-kernel-fix.py",
)


class RunnerPolicyTests(unittest.TestCase):
    def test_allowlist_is_explicit_unique_and_local(self) -> None:
        self.assertEqual(tuple(case.path for case in runner.HOST_TESTS),
                         EXPECTED_HOST_TEST_PATHS)
        paths = runner.validate_allowlist()
        self.assertEqual(len(paths), len(EXPECTED_HOST_TEST_PATHS))
        self.assertEqual(len(set(paths)), len(paths))
        self.assertTrue(all(path.is_file() for path in paths))
        self.assertTrue(all(path.is_relative_to(runner.ROOT) for path in paths))
        self.assertFalse(any("firmware" in str(path).lower() for path in paths))

    def test_rejects_absolute_parent_and_duplicate_entries(self) -> None:
        invalid = (
            (runner.HostTest("/tmp/external.py"),),
            (runner.HostTest("tools/hardware/../other.py"),),
            (runner.HostTest("tools/hardware/test-npu-session-lifecycle.py"),) * 2,
            (runner.HostTest("tools/hardware/test-npu-session-lifecycle.py",
                             optimization_safe=False),),
        )
        for tests in invalid:
            with self.subTest(tests=tests), self.assertRaises(ValueError):
                runner._resolve_test_paths(runner.ROOT, tests)

    def test_rejects_in_memory_substitution_of_live_device_script(self) -> None:
        unsafe_path = "tools/hardware/wifi-bringup-once.py"
        self.assertTrue((runner.ROOT / unsafe_path).is_file())
        original = runner.HOST_TESTS
        runner.HOST_TESTS = (runner.HostTest(unsafe_path), *original[1:])
        try:
            with self.assertRaisesRegex(ValueError, "reviewed allowlist"):
                runner.validate_allowlist()
        finally:
            runner.HOST_TESTS = original

    def test_rejects_symlink_entries_and_path_escape(self) -> None:
        with tempfile.TemporaryDirectory(prefix="s22-runner-policy-") as temp:
            root = Path(temp)
            (root / "tools/hardware").mkdir(parents=True)
            external = root / "outside.py"
            external.write_text("pass\n", encoding="utf-8")
            link = root / "tools/hardware/test-link.py"
            link.symlink_to(external)
            with self.assertRaisesRegex(ValueError, "symlinked path component"):
                runner._resolve_test_paths(
                    root, (runner.HostTest("tools/hardware/test-link.py"),))

        with tempfile.TemporaryDirectory(prefix="s22-runner-internal-link-") as temp:
            root = Path(temp)
            alternate = root / "alternate"
            alternate.mkdir()
            (alternate / "test-link.py").write_text("pass\n", encoding="utf-8")
            (root / "tools").mkdir()
            (root / "tools/hardware").symlink_to(alternate, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "symlinked path component"):
                runner._resolve_test_paths(
                    root, (runner.HostTest("tools/hardware/test-link.py"),))

        with tempfile.TemporaryDirectory(prefix="s22-runner-escape-") as temp, \
                tempfile.TemporaryDirectory(prefix="s22-runner-external-") as outside_temp:
            root = Path(temp)
            external = Path(outside_temp)
            (external / "test-escape.py").write_text("pass\n", encoding="utf-8")
            (root / "tools").mkdir()
            (root / "tools/hardware").symlink_to(external, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "symlinked path component"):
                runner._resolve_test_paths(
                    root, (runner.HostTest("tools/hardware/test-escape.py"),))

    def test_child_environment_drops_inherited_credentials_and_device_overrides(self) -> None:
        with tempfile.TemporaryDirectory(prefix="s22-runner-env-") as temp:
            env = runner.child_environment(Path(temp))
        self.assertEqual(set(env), {
            "PATH", "HOME", "TMPDIR", "TMP", "TEMP", "LANG", "LC_ALL", "TZ",
        })
        self.assertEqual(env["PATH"], runner.os.defpath)
        self.assertFalse(any(name.startswith("S22_") for name in env))
        self.assertFalse(any("TOKEN" in name or "KEY" in name for name in env))

    def test_optimization_skips_assert_only_standalone_checks(self) -> None:
        normal_only = {case.path for case in runner.HOST_TESTS if not case.optimization_safe}
        self.assertEqual(normal_only, {
            "tools/hardware/test-bt-qca6490-patch-receipt.py",
            "tools/hardware/test-close-range-kernel-fix.py",
        })
        for case in runner.HOST_TESTS:
            command = runner.command_for(Path("/repo") / case.path,
                                         optimized=case.optimization_safe)
            self.assertIn("-I", command)
            self.assertIn("-B", command)
            self.assertEqual("-O" in command, case.optimization_safe)
            self.assertEqual(command[-1], str(Path("/repo") / case.path))

    def test_runs_only_explicit_scripts_and_aggregates_failures(self) -> None:
        calls = []

        def fake_run(command, **kwargs):
            calls.append((command, kwargs))
            status = 1 if (command[-1].endswith("test-npu-boot-preflight.py") and
                           "-O" not in command) else 0
            return runner.subprocess.CompletedProcess(command, status)

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = runner.run_suite("both", run=fake_run)
        self.assertEqual(result, 1)
        self.assertIn("Host regression summary: 1 failure(s)", output.getvalue())
        expected_normal = len(runner.HOST_TESTS)
        expected_optimized = sum(case.optimization_safe for case in runner.HOST_TESTS)
        self.assertEqual(len(calls), expected_normal + expected_optimized)
        commands = [call[0] for call in calls]
        allowed = {str(path) for path in runner.validate_allowlist()}
        self.assertTrue(all(command[-1] in allowed for command in commands))
        self.assertTrue(all("-O" not in command for command in commands[:expected_normal]))
        self.assertTrue(all("-O" in command for command in commands[expected_normal:]))
        for _, kwargs in calls:
            self.assertEqual(kwargs["cwd"], runner.ROOT)
            self.assertNotIn("GITHUB_TOKEN", kwargs["env"])
            self.assertNotIn("S22_NPU_KERNEL_TREE", kwargs["env"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
