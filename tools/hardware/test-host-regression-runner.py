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
    "tools/hardware/test-npu-refcount-transaction.py",
    "tools/hardware/test-npu-default-boot-callback.py",
    "tools/hardware/test-npu-probe-unwind.py",
    "tools/hardware/test-npu-candidate-stack.py",
    "tools/hardware/test-npu-reconciled-stack.py",
    "tools/hardware/test-npu-shutdown-ownership.py",
    "tools/hardware/test-npu-full-lifecycle-profile.py",
    "tools/hardware/test-npu-shutdown-error-propagation.py",
    "tools/hardware/test-npu-publication-liveness-c.py",
    "tools/hardware/test-npu-mailbox-debug-walk-bounds.py",
    "tools/hardware/test-npu-mailbox-msgid-validation.py",
    "tools/hardware/test-npu-fw-report-lock-unwind.py",
    "tools/hardware/test-npu-interface-open-unwind.py",
    "tools/hardware/test-npu-system-resume-error-unwind.py",
    "tools/hardware/test-npu-report-close-lifetime.py",
    "tools/hardware/test-npu-imgloader-shutdown-status.py",
    "tools/hardware/test-npu-publication-ownership.py",
    "tools/hardware/test-npu-publication-drain-ownership.py",
    "tools/hardware/test-build-npu-six-profile.py",
    "tools/hardware/test-build-npu-twelve-module-only.py",
    "tools/hardware/test-device-trial-guard.py",
    "tools/hardware/test-audio-control-readiness.py",
    "tools/hardware/test-audio-progress-snapshot.py",
    "tools/hardware/test-audio-route-assessment.py",
    "tools/hardware/test-audio-wrapper-cleanup.py",
    "tools/hardware/test-audio-snapshot-sync-20260924.py",
    "tools/hardware/test-audio-ipc-evidence.py",
    "tools/hardware/test-audio-log-coverage.py",
    "tools/hardware/test-audio-log-capture-profile.py",
    "tools/hardware/test-audio-ipc-observation.py",
    "tools/hardware/test-audio-ipc-error-path.py",
    "tools/hardware/test-audio-ipc-worker-pm.py",
    "tools/hardware/test-audio-ipc-duplicate-rekick.py",
    "tools/hardware/test-audio-ipc-trace-private-abi.py",
    "tools/hardware/test-audio-dma-evidence.py",
    "tools/hardware/test-audio-recovery-observer.py",
    "tools/hardware/test-input-power-readiness.py",
    "tools/hardware/test-button-event-evidence.py",
    "tools/hardware/test-cellular-readiness-evidence.py",
    "tools/hardware/test-camera-readiness-once.py",
    "tools/hardware/test-camera-resource-unwind.py",
    "tools/hardware/test-camera-runtime-pm-unwind.py",
    "tools/hardware/test-camera-sensor-clock-unwind.py",
    "tools/hardware/test-prepare-camera-modpost-symvers.py",
    "tools/hardware/test-build-camera-module-recovery.py",
    "tools/hardware/test-build-audio-coherent-recovery.py",
    "tools/hardware/test-audio-coherent-recovery-profile.py",
    "tools/hardware/test-audio-coherent-trial-adapter.py",
    "tools/hardware/test-camera-recovery-profile.py",
    "tools/hardware/test-camera-recovery-reboot.py",
    "tools/hardware/test-recovery-deployment-hardening.py",
    "tools/hardware/test-hci-recovery-profile.py",
    "tools/hardware/test-s22-hci-candidate-preflight-20260924.py",
    "tools/hardware/test-bt-h4-ibs-bridge.py",
    "tools/hardware/test-bt-hci-lifecycle-c.py",
    "tools/hardware/test-build-bt-transport-candidate.py",
    "tools/hardware/test-bt-baud-reply.py",
    "tools/hardware/test-run-bt-hci-bridge-once.py",
    "tools/hardware/test-bt-qca6490-patch-receipt.py",
    "tools/hardware/test-close-range-kernel-fix.py",
    "tools/hardware/test_trustzone_log_classifier.py",
    "tools/hardware/test-tz-progress-evidence.py",
    "tools/pi-web/test_pi_readiness.py",
    "tools/pi-web/test_agent_web.py",
    "tools/hardware/test_s22_buttons.py",
    "tools/hardware/test_s22_assistant.py",
    "tools/hardware/test_s22_modem.py",
    "tools/hardware/test_s22_keepalive.py",
    "tools/persistence/test_start_persistent_desktop.py",
    "tools/hardware/phoned/test_s22_phoned.py",
    "tools/hardware/voip/test_s22_sip.py",
    "tools/hardware/test_s22_converse.py",
    "tools/openunum-phone/test_check_model_pin.py",
    "tools/openunum-phone/test_phone_plugin.py",
    "tools/hardware/camera/test_s22_camera.py",
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
            "tools/pi-web/test_agent_web.py",
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
