#!/usr/bin/env python3
"""Interpret per-sample ALSA/RDMA evidence and aggregate IPC markers locally.

This host-only adapter validates the progress observer's CLOCK_MONOTONIC
sample windows, then summarizes existing kernel-delta marker counts. It does
not contact a device or print raw receipt/log contents.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import Any


REPORT_SCHEMA = "audio-dma-evidence/v1"
MAX_JSON_BYTES = 8 * 1024 * 1024
MAX_TEXT_BYTES = 16 * 1024 * 1024
_REQUIRED_REGISTERS = ("1200", "1230", "1238")
_TRIAL_ID = re.compile(r"^[a-z0-9-]{1,64}$")
_MEMLOG_STATES = {
    "unknown_incomplete_abox_mem_metadata", "blocked_object_disabled",
    "filtered_debug_level_above_object_limit",
    "debug_level_passes_policy_but_capture_not_proven", "unknown",
}


class EvidenceError(ValueError):
    """Invalid local evidence input."""


def _load_ipc_parser():
    path = Path(__file__).with_name("audio-ipc-evidence.py")
    spec = importlib.util.spec_from_file_location("audio_dma_ipc_parser", path)
    if spec is None or spec.loader is None:
        raise EvidenceError("existing audio IPC parser is unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


IPC = _load_ipc_parser()


def _uint(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _is_running(sample: Any) -> bool:
    return (isinstance(sample, dict)
            and isinstance(sample.get("alsa_status"), str)
            and sample["alsa_status"].startswith("state: RUNNING"))


def _interval(record: Any, window_start: int, window_end: int) -> tuple[int, int] | None:
    if not isinstance(record, dict) or record.get("status") != "ok":
        return None
    start = record.get("start_monotonic_ns")
    end = record.get("end_monotonic_ns")
    if not (_uint(start) and _uint(end) and start <= end
            and window_start <= start <= end <= window_end):
        return None
    return start, end


def _same_sample_pair(sample: Any) -> dict[str, Any] | None:
    if not _is_running(sample):
        return None
    sync = sample.get("synchronization")
    if (not isinstance(sync, dict) or sync.get("clock_domain") != "CLOCK_MONOTONIC"
            or sync.get("all_observations_timed") is not True):
        return None
    window_start = sync.get("observation_window_start_monotonic_ns")
    window_end = sync.get("observation_window_end_monotonic_ns")
    timestamp = sample.get("sample_monotonic_ns")
    sequence = sample.get("sequence")
    observation_count = sync.get("observation_count")
    timed_count = sync.get("timed_observation_count")
    if not (_uint(window_start) and _uint(window_end) and window_start <= window_end
            and _uint(timestamp) and window_start <= timestamp <= window_end
            and _uint(sequence) and _uint(observation_count) and observation_count > 0
            and _uint(timed_count) and timed_count == observation_count):
        return None
    observations = sample.get("observations")
    if not isinstance(observations, dict):
        return None
    alsa_interval = _interval(observations.get("alsa_status"), window_start, window_end)
    dma_record = observations.get("rdma2_registers")
    dma_interval = _interval(dma_record, window_start, window_end)
    if alsa_interval is None or dma_interval is None:
        return None
    status_record = observations.get("alsa_status")
    status_text = sample.get("alsa_status")
    status_counters = re.search(r"(?m)^hw_ptr\s*:\s*(\d+)\s*$", status_text)
    if (not isinstance(status_record, dict) or status_record.get("value") != status_text
            or status_counters is None):
        return None
    gate_intervals = []
    for key, expected in (("abox_runtime_status", "active"),
                          ("abox_cache_only", "N"), ("abox_service", "1")):
        record = observations.get(key)
        interval = _interval(record, window_start, window_end)
        if interval is None or record.get("value") != expected:
            return None
        gate_intervals.append(interval)
    # A common capture window is necessary, but reads are sequential rather
    # than simultaneous. Keep the PM gate in the same sample as well.
    if (sample.get("runtime_status") != "active" or sample.get("cache_only") != "N"
            or str(sample.get("service")) != "1"):
        return None
    counters = sample.get("alsa_counters")
    if not isinstance(counters, dict):
        return None
    hw_ptr = counters.get("hw_ptr")
    if not _uint(hw_ptr) or hw_ptr != int(status_counters.group(1)):
        return None
    regs = dma_record.get("registers") if isinstance(dma_record, dict) else None
    flat_regs = sample.get("registers")
    if not isinstance(regs, dict) or not isinstance(flat_regs, dict):
        return None
    if any(not _uint(regs.get(key)) or regs.get(key) != flat_regs.get(key)
           for key in _REQUIRED_REGISTERS):
        return None

    ctrl = sample.get("rdma2_ctrl")
    status = sample.get("rdma2_status")
    status_add = sample.get("rdma2_status_add")
    if not (isinstance(ctrl, dict) and isinstance(ctrl.get("enable"), bool)
            and ctrl.get("raw") == regs["1200"]
            and isinstance(status, dict) and isinstance(status.get("progress"), bool)
            and status.get("raw") == regs["1230"]
            and _uint(status.get("rbuf_offset")) and _uint(status.get("rbuf_count"))
            and isinstance(status_add, dict)
            and status_add.get("raw") == regs["1238"]
            and _uint(status_add.get("current_address"))):
        return None
    return {
        "sequence": sequence,
        "sample_time_ns": timestamp,
        "read_midpoint_separation_ns": abs(
            (sum(alsa_interval) // 2) - (sum(dma_interval) // 2)),
        "pm_gate_max_read_duration_ns": max(end - start for start, end in gate_intervals),
        "hw_ptr": hw_ptr,
        "ctrl_enable": ctrl["enable"],
        "status_progress": status["progress"],
        "rbuf_offset": status["rbuf_offset"],
        "rbuf_count": status["rbuf_count"],
        "current_address": status_add["current_address"],
        "rdma_signature": (
            ctrl["enable"], status["progress"], status["rbuf_offset"],
            status["rbuf_count"], status_add["current_address"],
        ),
    }


def _sampled_movement(points: list[dict[str, Any]], field: str) -> dict[str, Any]:
    ordered = sorted(points, key=lambda point: point["sequence"])
    adjacent = [
        (left, right) for left, right in zip(ordered, ordered[1:])
        if right["sequence"] == left["sequence"] + 1
        and right["sample_time_ns"] > left["sample_time_ns"]
    ]
    if not adjacent:
        return {"state": "unknown_insufficient_adjacent_pairs", "adjacent_pair_count": 0}
    movement = any(left[field] != right[field] for left, right in adjacent)
    return {
        "state": "movement_observed_between_adjacent_samples" if movement
                 else "no_change_between_adjacent_samples",
        "adjacent_pair_count": len(adjacent),
    }


def _coverage_summary(report: Any) -> dict[str, Any]:
    """Current logging state may explain filtering; it never proves trial capture."""
    if not isinstance(report, dict):
        return {
            "report_present": False,
            "historical_capture_coverage": "unknown_not_supplied",
            "current_abox_mem_debug_state": "unknown_not_supplied",
            "missing_marker_interpretation": "unknown_not_negative_evidence",
        }
    policy = report.get("current_policy")
    memlog = policy.get("memlog") if isinstance(policy, dict) else None
    state = (memlog.get("abox_mem_debug_message_state")
             if isinstance(memlog, dict) else None)
    if state not in _MEMLOG_STATES:
        state = "unknown"
    return {
        "report_present": True,
        "report_schema_recognized": report.get("schema") == "audio-log-coverage/v1",
        "current_abox_mem_debug_state": state,
        "historical_capture_coverage": "unknown_current_policy_does_not_prove_trial_capture",
        "missing_marker_interpretation": "unknown_not_negative_evidence",
    }


def build_report(receipt: dict[str, Any], kernel_delta: str,
                 log_coverage: dict[str, Any] | None = None) -> dict[str, Any]:
    result = receipt.get("result") if isinstance(receipt, dict) else None
    raw_samples = result.get("progress_samples") if isinstance(result, dict) else None
    samples = raw_samples if isinstance(raw_samples, list) else []
    running_count = sum(_is_running(sample) for sample in samples)
    trial_ids = [sample.get("trial_id") if isinstance(sample, dict) else None
                 for sample in samples]
    same_trial = bool(trial_ids) and all(
        isinstance(value, str) and _TRIAL_ID.fullmatch(value)
                                        for value in trial_ids) and len(set(trial_ids)) == 1
    paired = ([point for sample in samples
               if (point := _same_sample_pair(sample)) is not None]
              if same_trial else [])
    hw_movement = _sampled_movement(paired, "hw_ptr")
    rdma_movement = _sampled_movement(paired, "rdma_signature")
    ipc = IPC.build_report(receipt if isinstance(receipt, dict) else {}, kernel_delta)
    marker_keys = (
        "trigger_api_entries_rdma2_device_prefixed",
        "trigger_api_entries_unattributed",
        "async_schedule_entries_atomic_nonsync",
        "playback_task2_trigger_sender_entries",
        "rdma_pointer_handler_entries_rdma2_device_prefixed",
        "rdma_pointer_handler_entries_unattributed",
        "alsa_pointer_function_logs_rdma2_device_prefixed",
        "alsa_pointer_function_logs_unattributed",
    )
    marker_counts = {key: ipc["evidence"].get(key, 0) for key in marker_keys}
    sampled_values = {
        name: sorted({point[name] for point in paired})
        for name in ("ctrl_enable", "status_progress", "rbuf_offset", "rbuf_count",
                     "current_address", "hw_ptr")
    }
    return {
        "schema": REPORT_SCHEMA,
        "trial_id": trial_ids[0] if same_trial else None,
        "sample_trial_ids_consistent": same_trial,
        "sampled_dma": {
            "running_sample_count": running_count,
            "same_sample_alsa_rdma_pair_count": len(paired),
            "running_sample_pair_gap_count": running_count - len(paired),
            "read_window_relation": "same_bounded_CLOCK_MONOTONIC_sample_window_not_simultaneous",
            "max_alsa_rdma_midpoint_separation_ns": max(
                (point["read_midpoint_separation_ns"] for point in paired), default=None),
            "sampled_values": sampled_values,
            "rdma2_registers_vs_hw_ptr": {
                "rdma": rdma_movement,
                "alsa_hw_ptr": hw_movement,
                "interpretation": "sampled_movement_only_not_firmware_or_physical_clock_proof",
            },
            "pairing_completeness": (
                "complete_for_running_samples" if running_count > 0 and
                running_count == len(paired) else
                "partial_or_unknown" if paired else "unknown_no_valid_running_pairs"),
        },
        "aggregate_ipc_markers": {
            "counts": marker_counts,
            "time_joined_to_each_running_sample": False,
            "capture_coverage": _coverage_summary(log_coverage),
            "interpretation": "aggregate_host_log_observations_only; absent_or_filtered_markers_are_unknown",
        },
        "semantics": {
            "queue_or_sender_marker_is_firmware_completion": False,
            "pointer_handler_marker_is_dma_progress_proof": False,
            "stationary_sampled_registers_prove_dsp_failure": False,
            "missing_marker_proves_boundary_not_reached": False,
            "current_filtered_policy_proves_historical_absence": False,
        },
        "limits": {
            "synchronization": "ALSA status and RDMA2 registers must both have successful timed reads contained in the same declared sample window; this does not make sequential reads simultaneous",
            "kernel_delta": "aggregate marker counts are intentionally not joined to individual userspace samples",
            "coverage": "current Memlogger policy and absent markers do not establish historical per-callsite capture coverage",
        },
    }


def _read_json(path: Path, maximum: int = MAX_JSON_BYTES) -> dict[str, Any]:
    try:
        with path.open("rb") as source:
            raw = source.read(maximum + 1)
        if len(raw) > maximum:
            raise EvidenceError("JSON input exceeds the host parser bound")
        value = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise EvidenceError("cannot read local JSON evidence") from error
    if not isinstance(value, dict):
        raise EvidenceError("JSON evidence root must be an object")
    return value


def _read_text(path: Path, maximum: int = MAX_TEXT_BYTES) -> str:
    try:
        with path.open("rb") as source:
            raw = source.read(maximum + 1)
        if len(raw) > maximum:
            raise EvidenceError("text input exceeds the host parser bound")
        return raw.decode("utf-8", errors="replace")
    except OSError as error:
        raise EvidenceError("cannot read local kernel-delta evidence") from error


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("receipt", type=Path, help="private route receipt JSON")
    parser.add_argument("kernel_delta", type=Path, help="private kernel-delta text")
    parser.add_argument("--log-coverage", type=Path,
                        help="optional audio-log-coverage JSON; current policy only")
    args = parser.parse_args(argv)
    try:
        report = build_report(
            _read_json(args.receipt), _read_text(args.kernel_delta),
            _read_json(args.log_coverage) if args.log_coverage else None,
        )
    except EvidenceError as error:
        print(f"audio-dma-evidence: {error}", file=sys.stderr)
        return 2
    print(json.dumps(report, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
