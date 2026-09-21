#!/usr/bin/env python3
"""Focused host-only tests for npu-open-once.py; never contacts the phone."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools/hardware/npu-open-once.py"


class NpuOpenOncePlanTests(unittest.TestCase):
    def run_plan(self) -> dict[str, object]:
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--plan"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        return json.loads(result.stdout)

    def test_plan_is_host_only_and_source_matched(self) -> None:
        plan = self.run_plan()
        self.assertFalse(plan["phone_contact"])
        self.assertEqual(plan["device_contract"]["path"], "/dev/vertex10")
        self.assertEqual(plan["device_contract"]["major"], 82)
        self.assertEqual(plan["device_contract"]["minor"], 10)
        self.assertEqual(plan["source_config"], {
            "CONFIG_NPU_USE_BOOT_IOCTL": True,
            "CONFIG_NPU_USE_HW_DEVICE": True,
            "CONFIG_DSP_USE_VS4L": True,
        })
        self.assertTrue(all(plan["source_checks"].values()))
        self.assertEqual(
            plan["kernel_git_state"]["commit"],
            "4e5c5ad7d950e4de0688b5663965f2075654b2ad",
        )
        self.assertTrue(plan["kernel_git_state"]["clean"])
        assets = plan["firmware_contract"]["recovered_assets"]
        self.assertTrue(assets["AIE.bin__3ec"]["present"])
        self.assertTrue(assets["dsp_reloc_rules.bin__424"]["present"])
        self.assertFalse(assets["AIE.bin__3ec"]["exact_runtime_path"])

    def test_plan_forbids_hardware_operations_beyond_open_close(self) -> None:
        plan = self.run_plan()
        self.assertEqual(plan["safety_boundary"]["allowed"], ["open(O_RDONLY|O_CLOEXEC)", "close"])
        forbidden = set(plan["safety_boundary"]["forbidden"])
        self.assertIn("ioctl", forbidden)
        self.assertIn("BOOTUP", forbidden)
        self.assertIn("firmware load", forbidden)
        self.assertIn("model", forbidden)
        self.assertIn("buffer", forbidden)


if __name__ == "__main__":
    unittest.main()
