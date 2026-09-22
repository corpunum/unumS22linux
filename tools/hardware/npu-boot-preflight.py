#!/usr/bin/env python3
"""Host-only NPU BOOTUP preflight; never opens a device or stages firmware.

This checks the pinned source/config and private host artifacts needed before a
separate device decision. It never authorizes or performs a device probe.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

EXPECTED_AIE = "a9843bbf520c08263c563f3200f0cbceb09c68fbdd1279d24236c3da704d9dc2"
EXPECTED_DSP_RULES = "468c0d2cc7c3a11fa80c3799385217bb4c36bb4cc369e754f73baae50e8b0a5d"
EXPECTED_CONFIG = {
    "CONFIG_NPU_USE_BOOT_IOCTL": "y",
    "CONFIG_NPU_USE_HW_DEVICE": "y",
    "CONFIG_DSP_USE_VS4L": "y",
    "CONFIG_EXYNOS_IMGLOADER": "m",
    "CONFIG_NPU_MAILBOX_VERSION": "9",
    "CONFIG_NPU_SECURE_MODE": None,
}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def symbol(config: str, name: str) -> str | None:
    m = re.search(rf"^{re.escape(name)}=(.*)$", config, re.MULTILINE)
    return m.group(1) if m else None


def function_body(text: str, marker: str) -> str:
    start = text.find(marker)
    if start < 0:
        return ""
    brace = text.find("{", start)
    if brace < 0:
        return ""
    depth = 0
    for pos in range(brace, len(text)):
        if text[pos] == "{":
            depth += 1
        elif text[pos] == "}":
            depth -= 1
            if depth == 0:
                return text[start : pos + 1]
    return ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[2])
    ap.add_argument("--source", type=Path)
    ap.add_argument("--config", type=Path)
    ap.add_argument("--aie", type=Path)
    ap.add_argument("--dsp-rules", type=Path)
    args = ap.parse_args()

    repo = args.repo.resolve()
    source = (args.source or repo / "lineage/android_kernel_samsung_s5e9925").resolve()
    config_path = (args.config or repo / "builds/close-range-clang21-llvm1-O-20260922/.config").resolve()
    aie = (args.aie or repo / "rootfs/npu-firmware-closure-20260921/vendor/firmware/AIE.bin").resolve()
    rules = (args.dsp_rules or repo / "rootfs/npu-firmware-closure-20260921/vendor/firmware/dsp_reloc_rules.bin").resolve()

    result: dict[str, object] = {"device_access": False, "staging": False, "checks": {}}
    checks = result["checks"]
    assert isinstance(checks, dict)

    cfg = config_path.read_text() if config_path.is_file() else ""
    config_values = {
        "CONFIG_NPU_USE_BOOT_IOCTL": symbol(cfg, "CONFIG_NPU_USE_BOOT_IOCTL"),
        "CONFIG_NPU_USE_HW_DEVICE": symbol(cfg, "CONFIG_NPU_USE_HW_DEVICE"),
        "CONFIG_DSP_USE_VS4L": symbol(cfg, "CONFIG_DSP_USE_VS4L"),
        "CONFIG_EXYNOS_IMGLOADER": symbol(cfg, "CONFIG_EXYNOS_IMGLOADER"),
        "CONFIG_NPU_MAILBOX_VERSION": symbol(cfg, "CONFIG_NPU_MAILBOX_VERSION"),
        "CONFIG_NPU_SECURE_MODE": symbol(cfg, "CONFIG_NPU_SECURE_MODE"),
    }
    checks["config"] = {"values": config_values, "expected": EXPECTED_CONFIG,
                         "matches": config_values == EXPECTED_CONFIG}

    binary_h = source / "drivers/vision/npu/core/include/npu-binary.h"
    session_c = source / "drivers/vision/npu/core/npu-session.c"
    vertex_c = source / "drivers/vision/npu/core/npu-vertex.c"
    system_c = source / "drivers/vision/npu/core/npu-system.c"
    binary = binary_h.read_text() if binary_h.is_file() else ""
    session = session_c.read_text() if session_c.is_file() else ""
    vertex = vertex_c.read_text() if vertex_c.is_file() else ""
    system = system_c.read_text() if system_c.is_file() else ""
    normal_boot = function_body(vertex, "int npu_hwdev_normal_bootup(")

    checks["source"] = {
        "normal_fw_name_AIE": (
            '#define FW_BASE_NAME\t\t"AIE"' in binary
            and '#define NPU_FW_NAME\t\t(FW_BASE_NAME ".bin")' in binary
        ),
        "normal_boot_has_power_notify": "npu_session_NW_CMD_POWER_NOTIFY(session, true)" in normal_boot,
        "power_notify_has_unbounded_wait": "wait_event(session->wq" in session,
        "normal_boot_unwind_missing": "npu_hwdev_shutdown(device, ctrl->value)" not in normal_boot,
        "system_calls_signature_loader": "npu_firmware_file_read_signature" in system,
    }

    checks["artifacts"] = {}
    artifacts = checks["artifacts"]
    assert isinstance(artifacts, dict)
    for label, path, expected in (("AIE.bin", aie, EXPECTED_AIE), ("dsp_reloc_rules.bin", rules, EXPECTED_DSP_RULES)):
        actual = sha256(path) if path.is_file() else None
        artifacts[label] = {"path": str(path), "exists": path.is_file(), "sha256": actual, "expected": expected, "match": actual == expected}

    checks["artifact_closure"] = {
        "required": ["AIE.bin"],
        "optional": ["dsp_reloc_rules.bin"],
        "required_matches": artifacts["AIE.bin"]["match"],
        "optional_matches": artifacts["dsp_reloc_rules.bin"]["match"],
    }
    checks["source_route"] = {
        "normal_fw_name_AIE": checks["source"]["normal_fw_name_AIE"],
        "normal_boot_has_power_notify": checks["source"]["normal_boot_has_power_notify"],
        "system_calls_signature_loader": checks["source"]["system_calls_signature_loader"],
    }
    checks["known_lifecycle_gaps"] = {
        "normal_boot_unwind_missing": checks["source"]["normal_boot_unwind_missing"],
        "power_notify_has_unbounded_wait": checks["source"]["power_notify_has_unbounded_wait"],
    }
    result["live_probe_validated"] = False
    result["reason"] = "host preflight only; lifecycle gaps remain and no device probe was performed"
    result["config_matches"] = checks["config"]["matches"]
    result["artifact_closure_pass"] = checks["artifact_closure"]["required_matches"]
    result["source_route_pass"] = all(checks["source_route"].values())
    result["preflight_pass"] = bool(result["config_matches"] and result["artifact_closure_pass"] and result["source_route_pass"])
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["preflight_pass"] else 2


if __name__ == "__main__":
    sys.exit(main())
