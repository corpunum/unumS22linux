#!/usr/bin/env python3
"""Synthetic pass/mismatch tests for the host-only NPU preflight."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
CHECKER = ROOT / "tools/hardware/npu-boot-preflight.py"
AIE = ROOT / "rootfs/npu-firmware-closure-20260921/vendor/firmware/AIE.bin"
RULES = ROOT / "rootfs/npu-firmware-closure-20260921/vendor/firmware/dsp_reloc_rules.bin"

CONFIG = """CONFIG_NPU_USE_BOOT_IOCTL=y
CONFIG_NPU_USE_HW_DEVICE=y
CONFIG_DSP_USE_VS4L=y
CONFIG_EXYNOS_IMGLOADER=m
CONFIG_NPU_MAILBOX_VERSION=9
# CONFIG_NPU_SECURE_MODE is not set
"""
SOURCE = {
    "drivers/vision/npu/core/include/npu-binary.h": '#define FW_BASE_NAME\t\t"AIE"\n#define NPU_FW_NAME\t\t(FW_BASE_NAME ".bin")\n',
    "drivers/vision/npu/core/npu-session.c": 'int npu_session_NW_CMD_POWER_NOTIFY(struct npu_session *session, bool on) { wait_event(session->wq, 1); return 0; }\n',
    "drivers/vision/npu/core/npu-vertex.c": 'int npu_hwdev_normal_bootup(struct npu_device *device, struct npu_vertex_ctx *vctx, struct vs4l_ctrl *ctrl) { npu_session_NW_CMD_POWER_NOTIFY(session, true); return 0; }\n',
    "drivers/vision/npu/core/npu-system.c": 'npu_firmware_file_read_signature(&system->binary, 0, 0, 0);\n',
}


def run(config_text: str, root: Path) -> tuple[int, dict]:
    cfg = root / "config"
    cfg.write_text(config_text)
    source = root / "source"
    for rel, text in SOURCE.items():
        path = source / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    proc = subprocess.run(
        ["python3", str(CHECKER), "--source", str(source), "--config", str(cfg),
         "--aie", str(AIE), "--dsp-rules", str(RULES)],
        capture_output=True, text=True, check=False,
    )
    return proc.returncode, json.loads(proc.stdout)


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="npu-preflight-") as name:
        root = Path(name)
        status, result = run(CONFIG, root)
        assert status == 0
        assert result["preflight_pass"] is True
        assert result["config_matches"] is True
        assert result["artifact_closure_pass"] is True
        assert result["live_probe_validated"] is False
        assert result["checks"]["known_lifecycle_gaps"]["normal_boot_unwind_missing"] is True

        bad = CONFIG.replace("CONFIG_NPU_MAILBOX_VERSION=9", "CONFIG_NPU_MAILBOX_VERSION=8")
        status, result = run(bad, root)
        assert status != 0
        assert result["preflight_pass"] is False
        assert result["config_matches"] is False
        assert result["artifact_closure_pass"] is True
    print("npu boot preflight synthetic pass/mismatch cases passed")


if __name__ == "__main__":
    main()
