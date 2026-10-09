#!/usr/bin/env python3
"""Host-only failure injection for the proposed camera PHY-LDO unwind patch."""
from __future__ import annotations

from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
PATCH = ROOT / "tools/hardware/camera-resource-unwind.patch"
HARNESS = ROOT / "tools/hardware/camera-resource-unwind-harness.c"
PINNED_SOURCE = Path(
    "/home/corpunum/s22-linux/lineage/android_kernel_samsung_s5e9925")
PINNED_COMMIT = "4e5c5ad7d950e4de0688b5663965f2075654b2ad"


def extract_added_function(patch_text: str, function_name: str) -> str:
    """Extract a complete newly-added C function from a unified diff."""
    added: list[str] = []
    for line in patch_text.splitlines():
        if line.startswith("+++"):
            continue
        if line.startswith("+"):
            added.append(line[1:])

    signature = re.compile(
        r"^static\s+int\s+" + re.escape(function_name) + r"\s*\(")
    start = next((i for i, line in enumerate(added) if signature.match(line)), None)
    if start is None:
        raise ValueError("added function not found in patch")

    source: list[str] = []
    depth = 0
    saw_body = False
    for line in added[start:]:
        source.append(line)
        depth += line.count("{") - line.count("}")
        saw_body = saw_body or "{" in line
        if saw_body and depth == 0:
            return "\n".join(source) + "\n"
    raise ValueError("added C function has unbalanced braces")


def extract_added_label_block(patch_text: str, label: str, end_context: str) -> str:
    """Extract an added label and its statements up to the next context line."""
    lines = patch_text.splitlines()
    start = next((i for i, line in enumerate(lines)
                  if line == "+" + label + ":"), None)
    if start is None:
        raise ValueError("added label not found in patch")

    block: list[str] = []
    for line in lines[start:]:
        if line.startswith("+"):
            block.append(line[1:])
            continue
        if line == " " + end_context:
            return "\n".join(block) + "\n"
        if line.startswith("@@") or line.startswith("diff --git "):
            break
    raise ValueError("added label block end context not found")


def checked_source_patch() -> None:
    if not (PINNED_SOURCE / ".git").exists():
        raise unittest.SkipTest("pinned vendor source checkout is unavailable")
    revision = subprocess.run(
        ["git", "-C", str(PINNED_SOURCE), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=False)
    if revision.returncode:
        raise AssertionError("could not verify pinned camera source revision")
    if revision.stdout.strip() != PINNED_COMMIT:
        raise AssertionError("camera patch source revision is not the reviewed pin")
    result = subprocess.run(
        ["git", "apply", "--check", str(PATCH)], cwd=PINNED_SOURCE,
        capture_output=True, text=True, check=False)
    if result.returncode:
        raise AssertionError("camera patch does not apply cleanly: " + result.stderr)


class CameraResourceUnwindTests(unittest.TestCase):
    def test_patch_is_pinned_and_applies_without_modifying_source(self) -> None:
        checked_source_patch()
        self.assertTrue(PINNED_SOURCE.is_dir())

    def test_failed_first_resource_path_rolls_back_without_counting_acquire(self) -> None:
        patch_text = PATCH.read_text(encoding="utf-8")
        self.assertIn("ret = is_resource_enable_phy_ldos(resourcemgr);", patch_text)
        self.assertIn("goto err_first_resource_phy_ldo;", patch_text)
        label_start = patch_text.index("+err_first_resource_phy_ldo:")
        label_end = patch_text.index("\n p_err:", label_start)
        failure_path = patch_text[label_start:label_end]
        self.assertIn("is_resourcemgr_deinit_dynamic_mem(resourcemgr)", failure_path)
        self.assertIn("is_resource_clear(resourcemgr);", failure_path)
        self.assertIn("pm_relax(&core->pdev->dev);", failure_path)
        self.assertIn("goto rsc_err;", failure_path)
        self.assertNotIn("atomic_inc(", failure_path)
        self.assertIn("+\tgoto p_err;", patch_text[:label_start])

    def test_pinned_baseline_failure_reaches_counted_error_label(self) -> None:
        checked_source_patch()
        source = (PINNED_SOURCE /
                  "drivers/media/platform/exynos/camera/is-resourcemgr.c").read_text(
                      encoding="utf-8")
        old_loop = re.search(
            r"for\s*\(i\s*=\s*0;\s*i\s*<\s*resourcemgr->num_phy_ldos;\s*i\+\+\)\s*"
            r"\{\s*ret\s*=\s*regulator_enable\(resourcemgr->phy_ldos\[i\]\);\s*"
            r"if\s*\(ret\)\s*\{[^}]*goto\s+p_err;", source, re.DOTALL)
        if old_loop is None:
            self.fail("pinned baseline LDO error path changed")
        counted_label = source.find("\np_err:", old_loop.end())
        self.assertGreaterEqual(counted_label, 0)
        self.assertIn("atomic_inc(&resource->rsccount);", source[counted_label:])
        self.assertIn("atomic_inc(&core->rsccount);", source[counted_label:])

    def test_extracted_kernel_helper_with_injected_enable_and_disable_failures(self) -> None:
        compiler = shutil.which("cc")
        if not compiler:
            self.skipTest("host C compiler is unavailable")
        function = extract_added_function(
            PATCH.read_text(encoding="utf-8"), "is_resource_enable_phy_ldos")
        cleanup = extract_added_label_block(
            PATCH.read_text(encoding="utf-8"),
            "err_first_resource_phy_ldo", "p_err:")
        harness = HARNESS.read_text(encoding="utf-8")
        helper_marker = "/* CAMERA_RESOURCE_UNWIND_HELPER */"
        cleanup_marker = "/* CAMERA_RESOURCE_FAILURE_LABEL */"
        self.assertEqual(harness.count(helper_marker), 1)
        self.assertEqual(harness.count(cleanup_marker), 1)
        generated = harness.replace(helper_marker, function)
        generated = generated.replace(cleanup_marker, cleanup.rstrip())

        with tempfile.TemporaryDirectory(prefix="camera-resource-unwind-") as temp:
            temp_path = Path(temp)
            c_source = temp_path / "camera-resource-unwind.c"
            c_source.write_text(generated, encoding="utf-8")
            for optimization in ("-O0", "-O2"):
                with self.subTest(optimization=optimization):
                    binary = temp_path / ("harness-" + optimization[1:])
                    build = subprocess.run(
                        [compiler, "-std=gnu89", "-Wall", "-Wextra", "-Werror",
                         optimization, str(c_source), "-o", str(binary)],
                        capture_output=True, text=True, check=False)
                    self.assertEqual(build.returncode, 0, build.stderr)
                    run = subprocess.run([str(binary)], capture_output=True,
                                         text=True, check=False)
                    self.assertEqual(run.returncode, 0, run.stderr)
                    self.assertIn("PASS", run.stdout)


if __name__ == "__main__":
    unittest.main()
