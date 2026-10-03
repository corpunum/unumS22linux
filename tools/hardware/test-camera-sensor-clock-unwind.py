#!/usr/bin/env python3
"""Extract and failure-test the pinned r0s v10.1 sensor clock path."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
PATCH = ROOT / "tools/hardware/camera-sensor-clock-unwind.patch"
HARNESS = ROOT / "tools/hardware/camera-sensor-clock-unwind-harness.c"
SOURCE_RELATIVE = Path(
    "drivers/media/platform/exynos/camera/ischain/is-v10_1_0/setup-is-sensor.c")
CALLER_RELATIVE = Path(
    "drivers/media/platform/exynos/camera/is-device-sensor_v2.c")
SETUP_RELATIVE = Path(
    "drivers/media/platform/exynos/camera/ischain/is-v10_1_0/setup-is.c")
PINNED_SOURCE = Path(
    "/home/corpunum/s22-linux/lineage/android_kernel_samsung_s5e9925")
PINNED_COMMIT = "4e5c5ad7d950e4de0688b5663965f2075654b2ad"
PINNED_TREE = "5c46cbe12dadbcdb64eec4344c9e8ff0f8a75dee"
PINNED_DERIVED = Path("/home/corpunum/s22-workers/camera-kernel-build-20260927")
DERIVED_COMMIT = "3fca50941422439b2019db2e4a3dc1016b2138a1"
DERIVED_TREE = "5aad5cf1dbaa0f430377737141f0547e971b0a2d"
SOURCE_SHA256 = "be9ea9769e2b1678cea68f1a86514aa0ccd559bfa9316840a1a83268bfef1182"
CALLER_SHA256 = "b88e203fa7ca7fdcf109f969d516601a9cba4702c98a3a0b338b3a6dbcf55265"
SETUP_SHA256 = "0e3668ca54f6d60abb8a22dc3a18d15b441fcf3c5154b2f0ee61e481d37b88b2"
PATCHED_FUNCTIONS = (
    "exynos9925_is_csi_gate",
    "exynos9925_is_sensor_iclk_cfg",
    "exynos9925_is_sensor_iclk_on",
    "exynos9925_is_sensor_iclk_off",
    "exynos_is_sensor_iclk_cfg",
    "exynos_is_sensor_iclk_on",
    "exynos_is_sensor_iclk_off",
)
CALLER_FUNCTIONS = ("is_sensor_iclk_on", "is_sensor_iclk_off")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def checked_revision(repository: Path, commit: str, tree: str) -> None:
    result = subprocess.run(
        ["git", "-C", str(repository), "rev-parse", "HEAD", "HEAD^{tree}"],
        capture_output=True, text=True, check=False,
    )
    if result.returncode:
        raise AssertionError(f"cannot identify pinned camera source {repository}: {result.stderr}")
    actual = result.stdout.splitlines()
    if actual != [commit, tree]:
        raise AssertionError(f"camera source identity differs at {repository}: {actual}")


def pinned_blob(repository: Path, relative: Path, expected_sha256: str) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(repository), "show", f"HEAD:{relative.as_posix()}"],
        capture_output=True, check=False,
    )
    if result.returncode:
        raise AssertionError(f"cannot read pinned source blob {relative}: {result.stderr.decode(errors='replace')}")
    if sha256(result.stdout) != expected_sha256:
        raise AssertionError(f"pinned source blob hash mismatch: {relative}")
    working = repository / relative
    if not working.is_file() or working.is_symlink():
        raise AssertionError(f"pinned working source is not a regular file: {working}")
    if sha256(working.read_bytes()) != expected_sha256:
        raise AssertionError(f"working source differs from pinned blob: {relative}")
    return result.stdout


def extract_function(source: str, name: str) -> str:
    """Extract one complete C function body from the exact pinned source."""
    pattern = re.compile(
        r"^(?:static\s+)?(?:inline\s+)?int\s+" + re.escape(name) + r"\s*\(",
        re.MULTILINE,
    )
    match = pattern.search(source)
    if match is None:
        raise ValueError(f"function not found: {name}")
    start = match.start()
    brace = source.find("{", match.end())
    if brace < 0:
        raise ValueError(f"function body missing: {name}")
    depth = 0
    in_string = False
    escaped = False
    for index in range(brace, len(source)):
        char = source[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[start:index + 1] + "\n"
    raise ValueError(f"unbalanced C body: {name}")


def apply_patch_to_temporary_source(source: bytes, temp_root: Path) -> bytes:
    target = temp_root / SOURCE_RELATIVE
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(source)
    check = subprocess.run(
        ["git", "apply", "--check", str(PATCH)], cwd=temp_root,
        capture_output=True, text=True, check=False,
    )
    if check.returncode:
        raise AssertionError("clock patch does not apply to temporary pinned source: " + check.stderr)
    applied = subprocess.run(
        ["git", "apply", str(PATCH)], cwd=temp_root,
        capture_output=True, text=True, check=False,
    )
    if applied.returncode:
        raise AssertionError("cannot apply clock patch to temporary source: " + applied.stderr)
    return target.read_bytes()


def harness_source(setup_source: bytes, caller_source: bytes) -> str:
    functions = [
        extract_function(setup_source.decode("utf-8"), name)
        for name in PATCHED_FUNCTIONS
    ]
    functions.extend(
        extract_function(caller_source.decode("utf-8"), name)
        for name in CALLER_FUNCTIONS
    )
    template = HARNESS.read_text(encoding="utf-8")
    marker = "/* CAMERA_SENSOR_CLOCK_FUNCTIONS */"
    if template.count(marker) != 1:
        raise AssertionError("C harness extraction marker must appear exactly once")
    return template.replace(marker, "\n\n".join(functions))


def compile_and_run(source: str, compiler: str, temp_root: Path,
                    *, baseline: bool, optimization: str) -> str:
    c_source = temp_root / ("camera-clock-baseline.c" if baseline else "camera-clock-patched.c")
    c_source.write_text(source, encoding="utf-8")
    binary = temp_root / (("clock-baseline-" if baseline else "clock-patched-") + optimization[2:])
    command = [
        compiler, "-std=gnu89", "-Wall", "-Wextra", "-Werror",
        "-Wno-unused-parameter", optimization,
    ]
    if baseline:
        command.append("-DCAMERA_EXPECT_BASELINE")
    command.extend([str(c_source), "-o", str(binary)])
    build = subprocess.run(command, capture_output=True, text=True, check=False)
    if build.returncode:
        raise AssertionError(f"extracted C compile failed ({optimization}): {build.stderr}")
    run = subprocess.run([str(binary)], capture_output=True, text=True, check=False)
    expected = "BASELINE_REPRODUCED" if baseline else "PASS: extracted sensor clock callbacks"
    if run.returncode or expected not in run.stdout:
        raise AssertionError(
            f"extracted C run failed ({optimization}, baseline={baseline}): "
            f"stdout={run.stdout!r}, stderr={run.stderr!r}"
        )
    return run.stdout.strip()


class CameraSensorClockUnwindTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.compiler = shutil.which("cc")
        if not cls.compiler:
            raise RuntimeError("host C compiler is required for extracted-C regressions")
        cls.baseline_setup = pinned_blob(PINNED_SOURCE, SOURCE_RELATIVE, SOURCE_SHA256)
        cls.caller = pinned_blob(PINNED_SOURCE, CALLER_RELATIVE, CALLER_SHA256)
        cls.setup_helpers = pinned_blob(PINNED_SOURCE, SETUP_RELATIVE, SETUP_SHA256)
        cls.baseline_text = cls.baseline_setup.decode("utf-8")
        cls.caller_text = cls.caller.decode("utf-8")
        cls.setup_helpers_text = cls.setup_helpers.decode("utf-8")

    def test_exact_source_pins_and_patch_apply_to_original_and_derived_tree(self) -> None:
        checked_revision(PINNED_SOURCE, PINNED_COMMIT, PINNED_TREE)
        checked_revision(PINNED_DERIVED, DERIVED_COMMIT, DERIVED_TREE)
        derived = pinned_blob(PINNED_DERIVED, SOURCE_RELATIVE, SOURCE_SHA256)
        self.assertEqual(derived, self.baseline_setup)
        patch_headers = re.findall(
            r"^diff --git (.+)$", PATCH.read_text(encoding="utf-8"), re.MULTILINE,
        )
        expected_path = SOURCE_RELATIVE.as_posix()
        self.assertEqual(patch_headers, [f"a/{expected_path} b/{expected_path}"])
        with tempfile.TemporaryDirectory(prefix="camera-clock-patch-base-") as temporary:
            patched = apply_patch_to_temporary_source(self.baseline_setup, Path(temporary))
            self.assertNotEqual(patched, self.baseline_setup)
            text = patched.decode("utf-8")
            for function in PATCHED_FUNCTIONS:
                self.assertIn(function, text)
            self.assertEqual(
                extract_function(text, "exynos9925_is_sensor_mclk_on"),
                extract_function(self.baseline_text, "exynos9925_is_sensor_mclk_on"),
            )
            self.assertEqual(
                extract_function(text, "exynos9925_is_sensor_mclk_off"),
                extract_function(self.baseline_text, "exynos9925_is_sensor_mclk_off"),
            )
        with tempfile.TemporaryDirectory(prefix="camera-clock-patch-derived-") as temporary:
            patched = apply_patch_to_temporary_source(derived, Path(temporary))
            self.assertNotEqual(patched, derived)

    def test_pinned_clock_helper_contract_and_actual_caller_order(self) -> None:
        enable = extract_function(self.setup_helpers_text, "is_enable")
        disable = extract_function(self.setup_helpers_text, "is_disable")
        self.assertIn("ret = clk_prepare_enable(clk);", enable)
        self.assertIn("return ret;", enable)
        self.assertIn("clk_disable_unprepare(clk);", disable)
        self.assertIn("return 0;", disable)
        missing_clock = disable.index("if (IS_ERR_OR_NULL(clk))")
        disable_call = disable.index("clk_disable_unprepare(clk);")
        self.assertLess(missing_clock, disable_call)
        self.assertIn("return -EINVAL;", disable[missing_clock:disable_call])
        self.assertNotIn("ret = clk_disable_unprepare", disable)
        caller = extract_function(self.caller_text, "is_sensor_iclk_on")
        self.assertLess(caller.index("pdata->iclk_cfg("), caller.index("pdata->iclk_on("))
        self.assertLess(caller.index("pdata->iclk_on("), caller.index("set_bit(IS_SENSOR_ICLK_ON"))

    def test_actual_pinned_baseline_reproduces_hidden_failure_and_state_bug(self) -> None:
        compiler_version = subprocess.run(
            [self.compiler, "--version"], capture_output=True, text=True, check=False,
        )
        self.assertEqual(compiler_version.returncode, 0, compiler_version.stderr)
        for optimization in ("-O0", "-O2"):
            with self.subTest(optimization=optimization):
                source = harness_source(self.baseline_setup, self.caller)
                with tempfile.TemporaryDirectory(prefix="camera-clock-baseline-") as temporary:
                    output = compile_and_run(
                        source, self.compiler, Path(temporary), baseline=True,
                        optimization=optimization,
                    )
                self.assertIn("BASELINE_REPRODUCED", output)

    def test_actual_patched_c_propagates_and_unwinds_each_owned_vote(self) -> None:
        with tempfile.TemporaryDirectory(prefix="camera-clock-patched-source-") as temporary:
            patched_setup = apply_patch_to_temporary_source(
                self.baseline_setup, Path(temporary),
            )
        source = harness_source(patched_setup, self.caller)
        for optimization in ("-O0", "-O2"):
            with self.subTest(optimization=optimization):
                with tempfile.TemporaryDirectory(prefix="camera-clock-patched-c-") as temporary:
                    output = compile_and_run(
                        source, self.compiler, Path(temporary), baseline=False,
                        optimization=optimization,
                    )
                self.assertIn("PASS: extracted sensor clock callbacks", output)

    def test_effective_python_optimization_is_reported(self) -> None:
        expected = os.environ.get("CAMERA_EXPECT_PYTHONOPTIMIZE")
        if expected is not None:
            self.assertEqual(sys_flags_optimize(), int(expected))
        if os.environ.get("PYTHONOPTIMIZE") == "1":
            self.assertEqual(sys_flags_optimize(), 1)


def sys_flags_optimize() -> int:
    import sys
    return sys.flags.optimize


if __name__ == "__main__":
    import sys

    print(
        f"PYTHON_TEST_FLAGS isolated={sys.flags.isolated} "
        f"no_site={sys.flags.no_site} optimize={sys.flags.optimize} "
        f"PYTHONOPTIMIZE={os.environ.get('PYTHONOPTIMIZE', '<unset>')}"
    )
    unittest.main(verbosity=2)
