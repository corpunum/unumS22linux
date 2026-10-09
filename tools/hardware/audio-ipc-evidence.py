#!/usr/bin/env python3
"""Summarize local ABOX/RDMA evidence without exposing raw logs or overclaiming.

This host-only tool reads a route receipt, its private kernel-delta text, and
optionally a tracefs DAPM snapshot. It does not contact or change a device.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


ASSESSMENT_SCHEMA = "audio-ipc-evidence/v1"
IPC_PCMPLAYBACK = 2
RDMA2_CHANNEL = 2
PCM_PLTDAI_TRIGGER = 17
PCM_PLTDAI_POINTER = 18
_DAPM_WIDGETS = (
    "ABOX SPUS OUT2-SIFS0",
    "ABOX SIFS0",
    "ABOX SIFS0 OUT",
    "ABOX UAIF1 SPK",
    "ABOX UAIF1 PLA",
)
_DAPM_TRACE_NAMES = {
    "SPUS OUT2-SIFS0": "ABOX SPUS OUT2-SIFS0",
    "SIFS0": "ABOX SIFS0",
    "SIFS0 OUT": "ABOX SIFS0 OUT",
    "UAIF1 SPK": "ABOX UAIF1 SPK",
    "UAIF1 PLA": "ABOX UAIF1 PLA",
}

_TRIGGER_API = re.compile(r"\babox_rdma_trigger\((?P<cmd>-?\d+)\)")
_IPC_SCHEDULE = re.compile(
    r"\babox_schedule_ipc\((?P<hw_irq>\d+),\s*\d+,\s*"
    r"(?P<atomic>[01]),\s*(?P<sync>[01])\)"
)
_IPC_SEND = re.compile(
    r"\babox_ipc_send\((?P<ipcid>\d+),\s*(?P<task_id>\d+),\s*"
    r"(?P<msgtype>\d+),"
)
_RDMA_HANDLER = re.compile(r"\babox_rdma_ipc_handler\((?P<msgtype>\d+)\)")
_ALSA_POINTER = re.compile(r"\babox_rdma_pointer:\s*pointer=(?P<value>[0-9a-fA-F]+)")
_DAPM_EVENT = re.compile(
    r"\bsnd_soc_dapm_widget_power:\s*widget=(?P<widget>[^\r\n]+?)\s+"
    r"val=(?P<val>[01])(?:\s|$)"
)
_RDMA2_DEVICE_PREFIX = re.compile(r"\b18c51200\.abox-rdma:")


class EvidenceError(ValueError):
    pass


def _read_json(path: Path) -> dict[str, Any]:
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise EvidenceError(f"cannot read JSON input: {path}") from exc
    if not isinstance(result, dict):
        raise EvidenceError("receipt root must be an object")
    return result


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise EvidenceError(f"cannot read text input: {path}") from exc


def _matches(pattern: re.Pattern[str], text: str) -> list[re.Match[str]]:
    return [match for line in text.splitlines() if (match := pattern.search(line))]


def _line_matches(pattern: re.Pattern[str], text: str) -> list[tuple[str, re.Match[str]]]:
    return [(line, match) for line in text.splitlines()
            if (match := pattern.search(line))]


def _sample_is_running(sample: Any) -> bool:
    return (isinstance(sample, dict)
            and isinstance(sample.get("alsa_status"), str)
            and sample["alsa_status"].startswith("state: RUNNING"))


def _widget_state(value: Any, widget: str) -> str | None:
    if not isinstance(value, dict) or value.get("status") != "ok":
        return None
    text = value.get("value")
    if not isinstance(text, str):
        return None
    match = re.search(rf"{re.escape(widget)}:\s*(On|Off)\b", text)
    return match.group(1).lower() if match else None


def _receipt_dapm(receipt: dict[str, Any]) -> dict[str, Any]:
    run = receipt.get("result")
    samples = run.get("progress_samples") if isinstance(run, dict) else None
    running = [sample for sample in samples if _sample_is_running(sample)] \
        if isinstance(samples, list) else []
    result: dict[str, Any] = {}
    for widget in _DAPM_WIDGETS:
        states: dict[str, int] = {}
        for sample in running:
            observations = sample.get("observations")
            dapm = observations.get("dapm") if isinstance(observations, dict) else None
            if not isinstance(dapm, dict):
                continue
            for component, widgets in dapm.items():
                if not isinstance(component, str) or not component.endswith(".abox"):
                    continue
                if not isinstance(widgets, dict):
                    continue
                state = _widget_state(widgets.get(widget), widget)
                if state is not None:
                    states[state] = states.get(state, 0) + 1
                    break
        result[widget] = {
            "running_samples_observed": sum(states.values()),
            "states": states,
        }
    return result


def build_report(receipt: dict[str, Any], kernel_delta: str,
                 dapm_trace: str = "") -> dict[str, Any]:
    assessment = receipt.get("assessment")
    assessment = assessment if isinstance(assessment, dict) else {}
    source_path = assessment.get("source_path_assessment")
    source_path = source_path if isinstance(source_path, dict) else {}

    trigger_api = _line_matches(_TRIGGER_API, kernel_delta)
    trigger_api_rdma2 = [line for line, _ in trigger_api
                         if _RDMA2_DEVICE_PREFIX.search(line)]
    trigger_api_unattributed = len(trigger_api) - len(trigger_api_rdma2)
    schedule = _matches(_IPC_SCHEDULE, kernel_delta)
    async_schedule = [match for match in schedule
                      if match["atomic"] == "1" and match["sync"] == "0"]
    sends = _matches(_IPC_SEND, kernel_delta)
    playback_task2_trigger_sends = [match for match in sends
                              if int(match["ipcid"]) == IPC_PCMPLAYBACK
                              and int(match["task_id"]) == RDMA2_CHANNEL
                              and int(match["msgtype"]) == PCM_PLTDAI_TRIGGER]
    handlers = _line_matches(_RDMA_HANDLER, kernel_delta)
    pointer_callbacks = [(line, match) for line, match in handlers
                         if int(match["msgtype"]) == PCM_PLTDAI_POINTER]
    pointer_callbacks_rdma2 = [line for line, _ in pointer_callbacks
                               if _RDMA2_DEVICE_PREFIX.search(line)]
    pointer_computations = _line_matches(_ALSA_POINTER, kernel_delta)
    pointer_computations_rdma2 = [line for line, _ in pointer_computations
                                  if _RDMA2_DEVICE_PREFIX.search(line)]

    dapm_transitions = {widget: {"on_events": 0, "off_events": 0}
                        for widget in _DAPM_WIDGETS}
    dapm_events = _matches(_DAPM_EVENT, dapm_trace)
    for event in dapm_events:
        widget = _DAPM_TRACE_NAMES.get(event["widget"])
        if widget is not None:
            field = "on_events" if event["val"] == "1" else "off_events"
            dapm_transitions[widget][field] += 1

    dma = assessment.get("dma_progress")
    dma = dma if isinstance(dma, dict) else {}
    hw_ptr_advance = dma.get("hw_ptr_advance")
    if isinstance(hw_ptr_advance, bool) or not isinstance(hw_ptr_advance, int):
        hw_ptr_advance = None
    run = receipt.get("result")
    samples = run.get("progress_samples") if isinstance(run, dict) else None
    running_samples = (sum(_sample_is_running(sample) for sample in samples)
                       if isinstance(samples, list) else None)
    if running_samples is None:
        running_samples = source_path.get("running_sample_count")
        if isinstance(running_samples, bool) or not isinstance(running_samples, int):
            running_samples = None

    return {
        "schema": ASSESSMENT_SCHEMA,
        "evidence": {
            "trigger_api_entries_rdma2_device_prefixed": len(trigger_api_rdma2),
            "trigger_api_entries_unattributed": trigger_api_unattributed,
            "async_schedule_entries_atomic_nonsync": len(async_schedule),
            "playback_task2_trigger_sender_entries": len(playback_task2_trigger_sends),
            "rdma_pointer_handler_entries_rdma2_device_prefixed": len(pointer_callbacks_rdma2),
            "rdma_pointer_handler_entries_unattributed": len(pointer_callbacks) - len(pointer_callbacks_rdma2),
            "alsa_pointer_function_logs_rdma2_device_prefixed": len(pointer_computations_rdma2),
            "alsa_pointer_function_logs_unattributed": len(pointer_computations) - len(pointer_computations_rdma2),
            "dapm_trace_events": dapm_transitions,
            "receipt_dapm_samples": _receipt_dapm(receipt),
            "running_samples": running_samples,
            "hw_ptr_advance": hw_ptr_advance,
            "source_assessment_pointer_stage": source_path.get(
                "pointer_ipc_stage", "not_in_receipt"),
        },
        "semantics": {
            "trigger_api_entry": "device-prefixed entry can identify RDMA2 trigger API; it is not firmware delivery or completion",
            "async_schedule_entry": "generic asynchronous request shape; no IPC payload/task/channel attribution or queue return",
            "playback_task2_trigger_sender_entry": "host sender entry matching playback IPC/task2/trigger; not send return or firmware completion",
            "rdma_pointer_handler_entry": "device-prefixed entry can identify RDMA2 handler selection; payload/progress still unverified",
            "alsa_pointer_function_log": "computed_alsa_pointer; not_firmware_callback_evidence",
            "dapm_trace_event": "software_widget_power_transition_only",
            "queue_or_sender_success_is_firmware_completion": False,
            "pointer_callback_is_dma_progress_proof": False,
            "dapm_state_is_physical_clock_or_audio_proof": False,
            "absence_of_log_marker_is_negative_device_evidence": False,
        },
        "limits": {
            "trigger_api_attribution": "an unprefixed trigger marker is not attributed to RDMA2; even a device-prefixed marker records API entry only",
            "pointer_handler_attribution": "an unprefixed pointer-handler marker is not attributed to a device/channel; prefixed marker identifies selected RDMA2 dev only, not payload correctness",
            "schedule_attribution": "the generic schedule marker omits the IPC message and task/channel, so it is not RDMA2 delivery evidence",
            "queue_put_return": "not exposed by the existing kernel-delta log",
            "abox_msg_send_return": "not exposed by the existing kernel-delta log",
            "callback_payload": "not exposed by the existing handler debug marker",
            "dapm_sample_pairing": "only per-sample DAPM values paired with alsa_status state: RUNNING are counted; missing widget reads are unknown, not Off",
            "coverage": "counts are observations only; absent markers remain unknown without capture-coverage proof",
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("receipt", type=Path, help="local route receipt JSON")
    parser.add_argument("kernel_delta", type=Path, help="local kernel-delta log")
    parser.add_argument("--dapm-trace", type=Path,
                        help="optional tracefs snapshot with snd_soc_dapm_widget_power events")
    args = parser.parse_args(argv)
    try:
        report = build_report(
            _read_json(args.receipt),
            _read_text(args.kernel_delta),
            _read_text(args.dapm_trace) if args.dapm_trace else "",
        )
    except EvidenceError as exc:
        print(f"audio-ipc-evidence: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
