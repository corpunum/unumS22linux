#!/usr/bin/env python3
"""Run the repository's explicit hardware-free host regression allowlist.

This runner deliberately does not discover tests. It launches only the fixed
scripts below, one process at a time, with a small temporary home and a clean
environment. Tests marked optimization_safe also run under Python -O; scripts
that rely on bare assert statements are only run normally.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Callable, Sequence


ROOT = Path(__file__).resolve().parents[2]
TIMEOUT_SECONDS = 180


@dataclass(frozen=True)
class HostTest:
    path: str
    optimization_safe: bool = True
    normal_only_reason: str = ""


# Keep this an explicit allowlist. Do not replace it with unittest discovery,
# globbing, a caller-supplied path, or a whole-directory scan. Validation below
# checks this sequence against the independently pinned reviewed path set.
REVIEWED_HOST_TEST_PATHS = (
    "tools/hardware/test-npu-session-lifecycle.py",
    "tools/hardware/test-npu-boot-preflight.py",
    "tools/hardware/test-audio-progress-snapshot.py",
    "tools/hardware/test-audio-route-assessment.py",
    "tools/hardware/test-audio-wrapper-cleanup.py",
    "tools/hardware/test-audio-snapshot-sync-20260924.py",
    "tools/hardware/test-audio-recovery-observer.py",
    "tools/hardware/test-input-power-readiness.py",
    "tools/hardware/test-recovery-deployment-hardening.py",
    "tools/hardware/test-hci-recovery-profile.py",
    "tools/hardware/test-s22-hci-candidate-preflight-20260924.py",
    "tools/hardware/test-bt-h4-ibs-bridge.py",
    "tools/hardware/test-run-bt-hci-bridge-once.py",
    "tools/hardware/test-bt-qca6490-patch-receipt.py",
    "tools/hardware/test-close-range-kernel-fix.py",
    "tools/hardware/test_trustzone_log_classifier.py",
    "tools/pi-web/test_pi_readiness.py",
    "tools/pi-web/test_agent_web.py",
)

HOST_TESTS = (
    HostTest("tools/hardware/test-npu-session-lifecycle.py"),
    HostTest("tools/hardware/test-npu-boot-preflight.py"),
    HostTest("tools/hardware/test-audio-progress-snapshot.py"),
    HostTest("tools/hardware/test-audio-route-assessment.py"),
    HostTest("tools/hardware/test-audio-wrapper-cleanup.py"),
    HostTest("tools/hardware/test-audio-snapshot-sync-20260924.py"),
    HostTest("tools/hardware/test-audio-recovery-observer.py"),
    HostTest("tools/hardware/test-input-power-readiness.py"),
    HostTest("tools/hardware/test-recovery-deployment-hardening.py"),
    HostTest("tools/hardware/test-hci-recovery-profile.py"),
    HostTest("tools/hardware/test-s22-hci-candidate-preflight-20260924.py"),
    HostTest("tools/hardware/test-bt-h4-ibs-bridge.py"),
    HostTest("tools/hardware/test-run-bt-hci-bridge-once.py"),
    HostTest(
        "tools/hardware/test-bt-qca6490-patch-receipt.py",
        optimization_safe=False,
        normal_only_reason="its standalone contract checks use bare assert statements",
    ),
    HostTest(
        "tools/hardware/test-close-range-kernel-fix.py",
        optimization_safe=False,
        normal_only_reason="its standalone contract checks use bare assert statements",
    ),
    HostTest("tools/hardware/test_trustzone_log_classifier.py"),
    HostTest("tools/pi-web/test_pi_readiness.py"),
    HostTest(
        "tools/pi-web/test_agent_web.py",
        optimization_safe=False,
        normal_only_reason="the launcher deliberately refuses Python optimization",
    ),
)


def _resolve_test_paths(root: Path, tests: Sequence[HostTest]) -> tuple[Path, ...]:
    """Resolve entries after set-level validation; reject symlinks and escapes."""
    resolved_root = root.resolve(strict=True)
    paths: list[Path] = []
    seen: set[str] = set()
    for test in tests:
        relative = Path(test.path)
        if (relative.is_absolute() or ".." in relative.parts or
                test.path in seen or not (
                    test.path.startswith("tools/hardware/") or
                    test.path.startswith("tools/pi-web/")
                ) or
                not test.optimization_safe and not test.normal_only_reason):
            raise ValueError(f"invalid or duplicate allowlist entry: {test.path}")
        seen.add(test.path)
        candidate = resolved_root
        for component in relative.parts:
            candidate = candidate / component
            if candidate.is_symlink():
                raise ValueError(f"symlinked path component in allowlist: {test.path}")
        if not candidate.is_file():
            raise FileNotFoundError(f"allowlisted test is missing: {test.path}")
        resolved = candidate.resolve(strict=True)
        if not resolved.is_relative_to(resolved_root):
            raise ValueError(f"allowlisted test escapes repository: {test.path}")
        paths.append(resolved)
    return tuple(paths)


def validate_allowlist(root: Path = ROOT,
                       tests: Sequence[HostTest] | None = None) -> tuple[Path, ...]:
    """Require the exact reviewed script sequence before resolving its files."""
    if tests is None:
        tests = HOST_TESTS
    selected = tuple(test.path for test in tests)
    if selected != REVIEWED_HOST_TEST_PATHS:
        raise ValueError("host test paths differ from the reviewed allowlist")
    return _resolve_test_paths(root, tests)


def child_environment(temp_root: Path) -> dict[str, str]:
    """Return a credential-free, deterministic environment for child tests."""
    home = temp_root / "home"
    temporary = temp_root / "tmp"
    home.mkdir(parents=True, exist_ok=True)
    temporary.mkdir(parents=True, exist_ok=True)
    return {
        "PATH": os.defpath,
        "HOME": str(home),
        "TMPDIR": str(temporary),
        "TMP": str(temporary),
        "TEMP": str(temporary),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "TZ": "UTC",
    }


def command_for(path: Path, *, optimized: bool,
                interpreter: str = sys.executable) -> list[str]:
    command = [interpreter, "-I", "-B"]
    if optimized:
        command.append("-O")
    command.append(str(path))
    return command


def _invoke(command: Sequence[str], *, root: Path, env: dict[str, str],
            timeout: int = TIMEOUT_SECONDS,
            run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run
            ) -> int:
    try:
        result = run(command, cwd=root, env=env, check=False, timeout=timeout)
    except subprocess.TimeoutExpired:
        print(f"TIMEOUT after {timeout}s: {Path(command[-1]).name}", file=sys.stderr)
        return 124
    return int(result.returncode)


def run_suite(mode: str = "both", *, root: Path = ROOT,
              run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run
              ) -> int:
    if mode not in {"normal", "optimized", "both"}:
        raise ValueError(f"unsupported mode: {mode}")
    tests = validate_allowlist(root)
    with tempfile.TemporaryDirectory(prefix="s22-host-regressions-") as temporary:
        env = child_environment(Path(temporary))
        failures = 0

        if mode in {"normal", "both"}:
            for spec, path in zip(HOST_TESTS, tests):
                print(f"RUN normal  {spec.path}", flush=True)
                status = _invoke(command_for(path, optimized=False),
                                 root=root, env=env, run=run)
                if status:
                    failures += 1
                    print(f"FAIL normal {spec.path} (exit {status})", flush=True)
                else:
                    print(f"PASS normal {spec.path}", flush=True)

        if mode in {"optimized", "both"}:
            for spec, path in zip(HOST_TESTS, tests):
                if not spec.optimization_safe:
                    print(f"SKIP -O    {spec.path}: {spec.normal_only_reason}", flush=True)
                    continue
                print(f"RUN -O      {spec.path}", flush=True)
                status = _invoke(command_for(path, optimized=True),
                                 root=root, env=env, run=run)
                if status:
                    failures += 1
                    print(f"FAIL -O     {spec.path} (exit {status})", flush=True)
                else:
                    print(f"PASS -O     {spec.path}", flush=True)

    print(f"Host regression summary: {failures} failure(s)", flush=True)
    return 1 if failures else 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("normal", "optimized", "both"),
                        default="both", help="normal, -O, or both (default)")
    args = parser.parse_args(argv)
    return run_suite(args.mode)


if __name__ == "__main__":
    raise SystemExit(main())
