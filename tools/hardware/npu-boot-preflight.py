#!/usr/bin/env python3
"""Host-only NPU artifact/source audit; never authorizes BOOTUP.

This checks the source/config and host artifacts. A successful artifact audit
is not BOOTUP readiness or authorization. Lifecycle regressions, firmware
boot/shutdown, live validation, rescue readiness and owner authorization are
separate required gates. This script has no option to assert those gates.
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


def read_source(path: Path) -> tuple[str, bool]:
    """Read source without mistaking absent or unreadable input for evidence."""
    try:
        return path.read_text(), True
    except (OSError, UnicodeError):
        return "", False


def evaluate_readiness(
    *,
    config_matches: bool,
    required_artifacts_match: bool,
    source_route_pass: bool,
    lifecycle_gaps: dict[str, bool | None],
    lifecycle_source_available: bool = False,
) -> dict[str, object]:
    """Keep host artifact checks separate from permission to touch the NPU.

    The response-timeout property is source-derived. Caller return under a
    stalled publisher, publisher progress, retained-resource cleanup after a
    detached call, callback lifetime, late power-state safety, device teardown
    ordering, and authorization remain false: this candidate preserves an
    unbounded publication drain and does not implement a detached waiter. A
    passing artifact audit still exits 2.
    """
    artifact_pass = bool(config_matches and required_artifacts_match and source_route_pass)
    lifecycle_source_available = bool(lifecycle_source_available)
    gates = {
        # The POWER response wait can time out even though the caller may
        # still block forever while draining an in-flight publication. Keep
        # these separate so the response timeout is not mistaken for full
        # publication-path liveness.
        "power_response_timeout_bounded": (
            lifecycle_source_available
            and not lifecycle_gaps.get("power_response_timeout_missing", True)
        ),
        # The safe stack-waiter path deliberately waits for the publisher to
        # return. No independently reviewed detach/resource-pin path exists.
        "publication_caller_return_bounded": False,
        # The current drain waits indefinitely for a publisher lease. A host
        # checker cannot promise liveness if that publication stalls.
        "publication_drain_liveness_resolved": False,
        "publisher_progress_bounded": False,
        "detached_waiter_resource_cleanup_kernel_validated": False,
        "detached_waiter_outstanding_cap_validated": False,
        "normal_boot_error_unwind_resolved": lifecycle_source_available and not lifecycle_gaps.get("normal_boot_unwind_missing", True),
        "publication_storage_lifetime_kernel_validated": False,
        "callback_lifetime_kernel_validated": False,
        "late_power_transition_safe_after_close": False,
        "device_teardown_resources_pinned_through_publication": False,
        "firmware_boot_and_shutdown_device_tested": False,
        "live_probe_validated": False,
        "independent_recovery_path_verified": False,
        "owner_authorized_for_bootup": False,
    }
    bootup_ready = artifact_pass and all(gates.values())
    # Readiness is still not permission. This host-only tool never grants it.
    bootup_authorized = False
    blockers = [name for name, passed in gates.items() if not passed]
    exit_code = 0 if bootup_ready and bootup_authorized else 2
    return {
        "artifact_preflight_pass": artifact_pass,
        "bootup_ready": bootup_ready,
        "bootup_authorized": bootup_authorized,
        "readiness_gates": gates,
        "readiness_blockers": blockers,
        "exit_code": exit_code,
    }


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
    protodrv_c = source / "drivers/vision/npu/core/npu-protodrv.c"
    binary, binary_available = read_source(binary_h)
    session, session_available = read_source(session_c)
    vertex, vertex_available = read_source(vertex_c)
    system, system_available = read_source(system_c)
    proto, proto_available = read_source(protodrv_c)
    lifecycle_source_available = all((session_available, vertex_available, proto_available))
    normal_boot = function_body(vertex, "int npu_hwdev_normal_bootup(")
    power_notify = function_body(session, "int npu_session_NW_CMD_POWER_NOTIFY(")
    power_wait = function_body(session, "static int npu_session_wait_power_request(")
    publish_drain = function_body(session, "static void npu_power_wait_cancel_and_drain(")
    timeout_ms = re.search(
        r"^#define\s+NPU_POWER_WAIT_TIMEOUT_MS\s+(\d+)\s*$",
        session,
        re.MULTILINE,
    )
    power_response_timeout_bounded = (
        "npu_session_wait_power_request(session, NPU_NW_CMD_POWER_CTL)" in power_notify
        and "timeout = msecs_to_jiffies(NPU_POWER_WAIT_TIMEOUT_MS)" in power_wait
        and "wait_for_completion_timeout(&waiter.completion, timeout)" in power_wait
        and timeout_ms is not None
        and int(timeout_ms.group(1)) > 0
    )
    callback = function_body(session, "int npu_session_save_power_result(")

    checks["source"] = {
        "normal_fw_name_AIE": (
            '#define FW_BASE_NAME\t\t"AIE"' in binary
            and '#define NPU_FW_NAME\t\t(FW_BASE_NAME ".bin")' in binary
        ),
        "normal_boot_has_power_notify": "npu_session_NW_CMD_POWER_NOTIFY(session, true)" in normal_boot,
        "power_response_timeout_bounded": power_response_timeout_bounded,
        "publication_drain_wait_unbounded": (
            "npu_power_wait_cancel_and_drain(&waiter)" in power_wait
            and "wait_for_completion(&waiter->publish_done)" in publish_drain
            and "wait_for_completion_timeout" not in publish_drain
        ),
        "stack_waiter_drain_source_contract": (
            "struct npu_power_waiter waiter;" in power_wait
            and "npu_power_wait_cancel_and_drain(&waiter)" in power_wait
            and "wait_for_completion(&waiter->publish_done)" in publish_drain
            and "wait_for_completion_timeout" not in publish_drain
        ),
        "callback_lookup_is_cookie_and_req_id_scoped": (
            "spin_lock_irqsave(&npu_power_waiters_lock, flags)" in callback
            and "npu_power_waiter_find(cookie)" in callback
            and "waiter->req_id == result.nw.npu_req_id" in callback
            and "!waiter->cancelled" in callback
        ),
        "normal_boot_unwind_missing": "npu_hwdev_shutdown(device, ctrl->value)" not in normal_boot,
        "system_calls_signature_loader": "npu_firmware_file_read_signature" in system,
        "lifecycle_sources_available": lifecycle_source_available,
        "binary_source_available": binary_available,
        "system_source_available": system_available,
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
        "lifecycle_sources_available": lifecycle_source_available,
    }
    checks["known_lifecycle_gaps"] = {
        "normal_boot_unwind_missing": (
            checks["source"]["normal_boot_unwind_missing"]
            if lifecycle_source_available else None
        ),
        "power_response_timeout_missing": (
            not checks["source"]["power_response_timeout_bounded"]
            if lifecycle_source_available else None
        ),
        "publication_drain_wait_unbounded": (
            checks["source"]["publication_drain_wait_unbounded"]
            if lifecycle_source_available else None
        ),
    }
    result["config_matches"] = checks["config"]["matches"]
    result["artifact_closure_pass"] = checks["artifact_closure"]["required_matches"]
    result["source_route_pass"] = all(checks["source_route"].values())
    readiness = evaluate_readiness(
        config_matches=bool(result["config_matches"]),
        required_artifacts_match=bool(result["artifact_closure_pass"]),
        source_route_pass=bool(result["source_route_pass"]),
        lifecycle_gaps=checks["known_lifecycle_gaps"],
        lifecycle_source_available=lifecycle_source_available,
    )
    result.update(readiness)
    result["live_probe_validated"] = readiness["readiness_gates"]["live_probe_validated"]
    result["reason"] = (
        "host artifact/source audit only; the POWER response timeout does not establish "
        "bounded caller return under a stalled publisher, publisher progress, detached "
        "resource cleanup, kernel callback lifetime, late power-state safety, device "
        "teardown pinning, or firmware runtime; live probe, independent recovery, and "
        "BOOTUP authorization remain unestablished"
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return int(result["exit_code"])


if __name__ == "__main__":
    sys.exit(main())
