#!/usr/bin/env python3
"""Host-only failure injection for the camera runtime-PM acquire unwind."""
from __future__ import annotations

from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
LDO_PATCH = ROOT / "tools/hardware/camera-resource-unwind.patch"
PATCH = ROOT / "tools/hardware/camera-runtime-pm-unwind.patch"
HARNESS = ROOT / "tools/hardware/camera-runtime-pm-unwind-harness.c"
PINNED_SOURCE = Path(
    "/home/corpunum/s22-linux/lineage/android_kernel_samsung_s5e9925")
PINNED_COMMIT = "4e5c5ad7d950e4de0688b5663965f2075654b2ad"
RESOURCE_SOURCE = Path(
    "drivers/media/platform/exynos/camera/is-resourcemgr.c")
PM_HEADER = Path("include/linux/pm_runtime.h")


def extract_function(source: str, name: str, *, added_diff: bool = False) -> str:
    """Extract one balanced C function from source or added unified-diff lines."""
    if added_diff:
        source = "\n".join(
            line[1:] for line in source.splitlines()
            if line.startswith("+") and not line.startswith("+++"))
    signature = re.compile(
        r"^(?:static\s+)?(?:inline\s+)?int\s+" + re.escape(name) + r"\s*\(",
        re.MULTILINE)
    match = signature.search(source)
    if match is None:
        raise ValueError("function not found: " + name)
    start = match.start()
    brace = source.find("{", match.end())
    if brace < 0:
        raise ValueError("function body not found: " + name)
    depth = 0
    for index in range(brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start:index + 1] + "\n"
    raise ValueError("function has unbalanced braces: " + name)


def checked_patch_sequence() -> tuple[str, str]:
    """Apply-check both ordered patches in a temp copy, never in vendor source."""
    if not (PINNED_SOURCE / ".git").exists():
        raise unittest.SkipTest("pinned vendor source checkout is unavailable")
    revision = subprocess.run(
        ["git", "-C", str(PINNED_SOURCE), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=False)
    if revision.returncode or revision.stdout.strip() != PINNED_COMMIT:
        raise AssertionError("camera patch source is not the reviewed pinned revision")

    with tempfile.TemporaryDirectory(prefix="camera-runtime-pm-patch-") as temp:
        temp_root = Path(temp)
        for relative in (RESOURCE_SOURCE, PM_HEADER):
            staged = temp_root / relative
            staged.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(PINNED_SOURCE / relative, staged)
        for patch_file in (LDO_PATCH, PATCH):
            check = subprocess.run(
                ["git", "apply", "--check", str(patch_file)], cwd=temp_root,
                capture_output=True, text=True, check=False)
            if check.returncode:
                raise AssertionError(
                    "ordered camera patch does not apply cleanly: " + check.stderr)
            applied = subprocess.run(
                ["git", "apply", str(patch_file)], cwd=temp_root,
                capture_output=True, text=True, check=False)
            if applied.returncode:
                raise AssertionError(
                    "could not stage camera patch in temporary copy: " + applied.stderr)
        return ((temp_root / RESOURCE_SOURCE).read_text(encoding="utf-8"),
                (temp_root / PM_HEADER).read_text(encoding="utf-8"))


class CameraRuntimePmUnwindTests(unittest.TestCase):
    def test_baseline_ignored_return_and_ordered_patch_application(self) -> None:
        patched, _ = checked_patch_sequence()
        original = (PINNED_SOURCE / RESOURCE_SOURCE).read_text(encoding="utf-8")
        self.assertIn("pm_runtime_get_sync(&resource->pdev->dev);", original)
        self.assertNotIn(
            "ret = pm_runtime_get_sync(&resource->pdev->dev);", original)
        sensor_case = patched[patched.index("ret = is_resource_sensor_power_on("):]
        self.assertRegex(
            sensor_case,
            r"ret = is_resource_sensor_power_on\([^;]+;\s*"
            r"if\s*\(ret\)\s*goto rsc_err;\s*break;",
        )
        p_err = patched.index("\np_err:", patched.index("int is_resource_get("))
        rsc_err = patched.index("\nrsc_err:", p_err)
        self.assertIn("atomic_inc(&resource->rsccount);", patched[p_err:rsc_err])
        self.assertIn("atomic_inc(&core->rsccount);", patched[p_err:rsc_err])

    def test_sensor_power_bit_is_set_only_after_success(self) -> None:
        patch_text = PATCH.read_text(encoding="utf-8")
        power_on = extract_function(
            patch_text, "is_resource_sensor_power_on", added_diff=True)
        error_branch = power_on.split("if (ret) {", 1)[1].split(
            "\n\t}\n", 1)[0]
        self.assertIn("return ret;", error_branch)
        self.assertNotIn("set_bit(", error_branch)
        self.assertLess(power_on.index("return ret;"), power_on.index("set_bit("))
        self.assertIn("if (rsccount == 0)", error_branch)

    def test_extracted_runtime_pm_and_resource_helpers_with_injected_failures(self) -> None:
        compiler = shutil.which("cc")
        if not compiler:
            self.skipTest("host C compiler is unavailable")
        patch_text = PATCH.read_text(encoding="utf-8")
        helper_names = (
            "is_resource_disable_phy_ldos",
            "is_resource_sensor_runtime_get",
            "is_resource_sensor_power_on",
        )
        helpers = "\n".join(
            extract_function(patch_text, name, added_diff=True)
            for name in helper_names)
        template = HARNESS.read_text(encoding="utf-8")
        self.assertEqual(template.count("/* CAMERA_SENSOR_RUNTIME_HELPERS */"), 1)
        self.assertEqual(template.count("/* CAMERA_PM_RUNTIME_HELPER */"), 1)
        template = template.replace("/* CAMERA_SENSOR_RUNTIME_HELPERS */", helpers)
        pm_source = (PINNED_SOURCE / PM_HEADER).read_text(encoding="utf-8")
        pm_helper = extract_function(pm_source, "pm_runtime_resume_and_get")

        with tempfile.TemporaryDirectory(prefix="camera-runtime-pm-c-") as temp:
            temp_root = Path(temp)
            c_source = temp_root / "camera-runtime-pm-unwind.c"
            for config_pm in (False, True):
                generated = template.replace(
                    "/* CAMERA_PM_RUNTIME_HELPER */",
                    pm_helper if config_pm else "")
                c_source.write_text(generated, encoding="utf-8")
                for optimization in ("-O0", "-O2"):
                    with self.subTest(config_pm=config_pm, optimization=optimization):
                        binary = temp_root / (
                            "harness-" + ("pm" if config_pm else "direct")
                            + optimization[1:])
                        command = [compiler, "-std=gnu89", "-Wall", "-Wextra",
                                   "-Werror", optimization]
                        if config_pm:
                            command.append("-DCONFIG_PM")
                        command.extend([str(c_source), "-o", str(binary)])
                        build = subprocess.run(
                            command, capture_output=True, text=True, check=False)
                        self.assertEqual(build.returncode, 0, build.stderr)
                        run = subprocess.run(
                            [str(binary)], capture_output=True, text=True,
                            check=False)
                        self.assertEqual(run.returncode, 0, run.stderr)
                        self.assertIn("PASS", run.stdout)


if __name__ == "__main__":
    unittest.main()
