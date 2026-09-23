#!/usr/bin/env python3
"""Synthetic pass/mismatch tests for the host-only NPU preflight."""
from __future__ import annotations

import json
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
CHECKER = ROOT / "tools/hardware/npu-boot-preflight.py"
AIE = ROOT / "rootfs/npu-firmware-closure-20260921/vendor/firmware/AIE.bin"
RULES = ROOT / "rootfs/npu-firmware-closure-20260921/vendor/firmware/dsp_reloc_rules.bin"
SPEC = importlib.util.spec_from_file_location("npu_boot_preflight", CHECKER)
assert SPEC is not None and SPEC.loader is not None
PREFLIGHT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PREFLIGHT)

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
    "drivers/vision/npu/core/npu-protodrv.c": 'static int nw_mgmt_op_get_request(void) { return 1; }\n',
}


def check(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def run(config_text: str, root: Path, *, missing: str | None = None,
        unreadable: str | None = None) -> tuple[int, dict]:
    cfg = root / "config"
    cfg.write_text(config_text)
    source = root / "source"
    for rel, text in SOURCE.items():
        path = source / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_dir():
            path.rmdir()
        path.write_text(text)
    if missing is not None:
        (source / missing).unlink()
    if unreadable is not None:
        unreadable_path = source / unreadable
        unreadable_path.unlink()
        unreadable_path.mkdir()
    proc = subprocess.run(
        [sys.executable, str(CHECKER), "--source", str(source), "--config", str(cfg),
         "--aie", str(root / "missing-AIE.bin"), "--dsp-rules", str(root / "missing-dsp-rules.bin")],
        capture_output=True, text=True, check=False,
    )
    return proc.returncode, json.loads(proc.stdout)


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="npu-preflight-") as name:
        root = Path(name)
        status, result = run(CONFIG, root)
        check(status == 2, "synthetic preflight must remain fail-closed")
        check(result["artifact_preflight_pass"] is False, "missing artifacts must fail")
        check(result["config_matches"] is True, "synthetic config should match")
        check(result["live_probe_validated"] is False, "host preflight cannot validate live probe")
        check(result["bootup_ready"] is False, "host preflight cannot establish readiness")
        check(result["bootup_authorized"] is False, "host preflight cannot authorize BOOTUP")
        check(result["exit_code"] == status, "JSON and process exit status must match")
        check(result["checks"]["known_lifecycle_gaps"]["normal_boot_unwind_missing"] is True,
              "synthetic source must expose missing unwind")

        bad = CONFIG.replace("CONFIG_NPU_MAILBOX_VERSION=9", "CONFIG_NPU_MAILBOX_VERSION=8")
        status, result = run(bad, root)
        check(status == 2, "config mismatch must fail closed")
        check(result["artifact_preflight_pass"] is False, "config mismatch must fail artifacts")
        check(result["bootup_ready"] is False, "config mismatch must fail readiness")
        check(result["bootup_authorized"] is False, "config mismatch must not authorize")
        check(result["config_matches"] is False, "config mismatch must be reported")

        # Each required lifecycle source missing or unreadable is unknown, not
        # proof that a source-pattern gap is absent. All lifecycle gates stay
        # false and this tool never authorizes BOOTUP.
        for unavailable, mode in (("drivers/vision/npu/core/npu-session.c", "missing"),
                                  ("drivers/vision/npu/core/npu-vertex.c", "unreadable"),
                                  ("drivers/vision/npu/core/npu-protodrv.c", "missing")):
            kwargs = {mode: unavailable}
            status, result = run(CONFIG, root, **kwargs)
            check(status == 2, f"{mode} lifecycle source must fail closed")
            gates = result["readiness_gates"]
            check(gates["power_notify_wait_resolved"] is False,
                  f"{mode} lifecycle source must not resolve POWER wait")
            check(gates["normal_boot_error_unwind_resolved"] is False,
                  f"{mode} lifecycle source must not resolve boot unwind")
            check(not any(gates.values()), f"{mode} lifecycle source must leave all lifecycle gates false")
            check(result["bootup_authorized"] is False, f"{mode} lifecycle source must not authorize BOOTUP")

    # Even a fully passing synthetic artifact/config/source audit cannot
    # mark BOOTUP ready while kernel lifecycle and device/authorization gates
    # remain unproven.
    readiness = PREFLIGHT.evaluate_readiness(
        config_matches=True,
        required_artifacts_match=True,
        source_route_pass=True,
        lifecycle_gaps={
            "normal_boot_unwind_missing": False,
            "power_notify_has_unbounded_wait": False,
        },
    )
    check(readiness["artifact_preflight_pass"] is True, "synthetic artifacts should pass")
    check(readiness["bootup_ready"] is False, "unproven gates must block readiness")
    check(readiness["bootup_authorized"] is False, "host checker must never authorize")
    check(readiness["exit_code"] == 2, "unproven gates must return status 2")
    check("callback_close_race_regressions_passed" in readiness["readiness_blockers"],
          "callback race gate must remain a blocker")
    check("live_probe_validated" in readiness["readiness_blockers"], "live gate must remain a blocker")
    print("npu boot preflight synthetic pass/mismatch cases passed")


if __name__ == "__main__":
    main()
