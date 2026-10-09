#!/usr/bin/env python3
"""Passively collect and classify S22 physical-button evdev evidence.

Collection opens only event nodes whose sysfs EV_KEY and key capability
bitmaps contain KEY_POWER, KEY_VOLUMEUP, or KEY_VOLUMEDOWN. It uses read-only,
nonblocking descriptors and issues no ioctls, grabs, writes, or injections.
An observed kernel event sequence does not prove that a human caused it or
that the compositor/audio route acted on it.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import os
from pathlib import Path
import select
import stat
import struct
import sys
import time
from typing import Any

EVENT = struct.Struct("@llHHi")
EV_SYN = 0
EV_KEY = 1
SYN_REPORT = 0
SYN_DROPPED = 3
KEY_CODES = {
    "KEY_POWER": 116,
    "KEY_VOLUMEUP": 115,
    "KEY_VOLUMEDOWN": 114,
}
MAX_SECONDS = 300.0
DEFAULT_MAX_RECORDS = 4096
MAX_RECORDS_LIMIT = 100000


def _readiness_module() -> Any:
    path = Path(__file__).with_name("input-power-readiness.py")
    spec = importlib.util.spec_from_file_location("input_power_readiness_shared", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load input capability reader at {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _valid_event(event: object) -> bool:
    if not isinstance(event, dict):
        return False
    for field in ("type", "code", "value", "sec", "usec"):
        if type(event.get(field)) is not int:
            return False
    return isinstance(event.get("device"), str)


def _key_transition(state: dict[str, bool], value: int) -> None:
    if value == 1:
        state["press_seen"] = True
        state["down"] = True
    elif value == 2:
        state["repeat_seen"] = True
    elif value == 0:
        state["release_seen"] = True
        if state["down"]:
            state["release_after_press_seen"] = True
        state["down"] = False
    else:
        state["invalid_value"] = True


def _inventory_proves_button(item: dict[str, object], button: str) -> bool:
    """Recheck raw sysfs bits before offline analysis treats a source as capable."""
    capabilities = item.get("capabilities")
    if not isinstance(capabilities, dict):
        return False
    if capabilities.get("format") != "linux-input-input_print_bitmap":
        return False
    word_bits = capabilities.get("word_bits")
    if type(word_bits) is not int or word_bits not in (32, 64):
        return False
    ev_record = capabilities.get("ev")
    key_record = capabilities.get("key")
    if not isinstance(ev_record, dict) or not isinstance(key_record, dict):
        return False
    if ev_record.get("status") != "valid" or key_record.get("status") != "valid":
        return False
    ev_raw, key_raw = ev_record.get("raw"), key_record.get("raw")
    if not isinstance(ev_raw, str) or not isinstance(key_raw, str):
        return False
    readiness = _readiness_module()
    try:
        ev_codes = readiness.parse_sysfs_bitmap(
            ev_raw, word_bits=word_bits, max_code=readiness.EV_MAX)
        key_codes = readiness.parse_sysfs_bitmap(
            key_raw, word_bits=word_bits, max_code=readiness.KEY_MAX)
    except (AttributeError, TypeError, ValueError):
        return False
    code = KEY_CODES[button]
    advertised = item.get("button_capabilities")
    return (EV_KEY in ev_codes and code in key_codes and
            isinstance(advertised, dict) and advertised.get(button) is True)


def analyze_button_events(inventory: list[dict[str, object]],
                          events: list[dict[str, object]],
                          node_statuses: list[dict[str, object]] | None = None
                          ) -> dict[str, object]:
    """Classify complete EV_KEY transitions by capable node and key code.

    Only key transitions enclosed by SYN_REPORT are considered. After
    SYN_DROPPED, records are ignored through the next SYN_REPORT; the capture
    is marked incomplete because this passive reader does not query device
    state to reconstruct the missing transitions.
    """
    node_statuses = node_statuses or []
    status_by_device = {
        str(item.get("device")): item for item in node_statuses
        if isinstance(item, dict) and isinstance(item.get("device"), str)
    }
    inventory_by_device = {
        str(item.get("event")): item for item in inventory
        if isinstance(item, dict) and isinstance(item.get("event"), str)
    }
    events_by_device: dict[str, list[dict[str, object]]] = {}
    invalid_records_by_device: set[str] = set()
    for event in events:
        if _valid_event(event):
            events_by_device.setdefault(str(event["device"]), []).append(event)
        elif isinstance(event, dict) and isinstance(event.get("device"), str):
            invalid_records_by_device.add(str(event["device"]))

    analysis: dict[str, object] = {}
    for button, code in KEY_CODES.items():
        sources: list[dict[str, object]] = []
        for device, item in inventory_by_device.items():
            button_caps = item.get("button_capabilities")
            identity = item.get("node_identity")
            if (not isinstance(button_caps, dict) or button_caps.get(button) is not True or
                    item.get("button_observation_candidate") is not True or
                    item.get("present") is not True or item.get("key_capable") is not True or
                    not isinstance(identity, dict) or identity.get("status") != "matched" or
                    identity.get("expected") != item.get("sysfs_dev") or
                    identity.get("actual") != item.get("sysfs_dev") or
                    not _inventory_proves_button(item, button)):
                continue
            node_events = events_by_device.get(device, [])
            transitions = {"press_seen": False, "release_seen": False,
                           "release_after_press_seen": False, "repeat_seen": False,
                           "down": False, "invalid_value": False}
            frame: list[dict[str, object]] = []
            in_drop = False
            dropped = False
            capability_violation = False
            invalid_record = device in invalid_records_by_device
            for event in node_events:
                event_type, event_code = event["type"], event["code"]
                if event_type == EV_SYN and event_code == SYN_DROPPED:
                    dropped = True
                    in_drop = True
                    frame.clear()
                    continue
                if in_drop:
                    if event_type == EV_SYN and event_code == SYN_REPORT:
                        in_drop = False
                    continue
                if event_type == EV_SYN and event_code == SYN_REPORT:
                    for transition in frame:
                        if transition["code"] != code:
                            continue
                        _key_transition(transitions, int(transition["value"]))
                    frame.clear()
                    continue
                if event_type == EV_KEY and event_code in KEY_CODES.values():
                    caps = item.get("button_capabilities")
                    supported_name = next(
                        (name for name, candidate_code in KEY_CODES.items()
                         if candidate_code == event_code), None)
                    if not isinstance(caps, dict) or caps.get(supported_name) is not True:
                        capability_violation = True
                    else:
                        frame.append(event)

            partial_frame = bool(frame) or in_drop
            node_status = status_by_device.get(device, {})
            partial_record = node_status.get("partial_record") is True
            read_error = bool(node_status.get("read_error"))
            open_error = bool(node_status.get("open_error"))
            eof = node_status.get("eof") is True
            dropped = dropped or node_status.get("syn_dropped") is True
            event_limit = node_status.get("event_limit_reached") is True
            incomplete = (dropped or partial_frame or partial_record or read_error or
                          open_error or eof or event_limit or capability_violation or
                          transitions["invalid_value"] or invalid_record)
            if incomplete:
                node_result = "incomplete"
            elif transitions["release_after_press_seen"]:
                node_result = ("observed_press_release_with_repeat"
                               if transitions["repeat_seen"] else "observed_press_release")
            elif transitions["press_seen"]:
                node_result = "press_without_release"
            elif transitions["repeat_seen"]:
                node_result = "repeat_without_press_release"
            elif transitions["release_seen"]:
                node_result = "release_without_prior_press"
            else:
                node_result = "no_matching_key_events"
            sources.append({
                "device": device,
                "name": item.get("name"),
                "capability_present": True,
                "built_in_physical_button_acceptance": "not_established",
                "event_count": len(node_events),
                "press_seen": transitions["press_seen"],
                "release_seen": transitions["release_seen"],
                "release_after_press_seen": transitions["release_after_press_seen"],
                "repeat_seen": transitions["repeat_seen"],
                "syn_dropped": dropped,
                "partial_frame": partial_frame,
                "partial_record": partial_record,
                "event_limit_reached": event_limit,
                "capability_violation": capability_violation,
                "invalid_key_value": transitions["invalid_value"],
                "invalid_event_record": invalid_record,
                "read_error": read_error,
                "open_error": node_status.get("open_error"),
                "read_eof": eof,
                "status": node_result,
            })

        if not sources:
            overall = "no_capable_node"
        elif any(source["status"] == "incomplete" for source in sources):
            overall = "incomplete_capture"
        elif any(source["release_after_press_seen"] for source in sources):
            overall = "observed_press_release"
            if any(source["repeat_seen"] for source in sources):
                overall = "observed_press_release_with_repeat"
        elif any(source["press_seen"] for source in sources):
            overall = "press_without_release"
        elif any(source["repeat_seen"] for source in sources):
            overall = "repeat_without_press_release"
        elif any(source["release_seen"] for source in sources):
            overall = "release_without_prior_press"
        else:
            overall = "no_matching_key_events"
        analysis[button] = {
            "code": code,
            "status": overall,
            "capable_nodes": [source["device"] for source in sources],
            "nodes": sources,
            "physical_origin": "requires owner-confirmed physical action; evdev cannot prove origin",
            "built_in_physical_button_acceptance": "not_established",
            "compositor_or_audio_effect": "not measured by this collector",
        }
    return analysis


def _selected_events(events: list[dict[str, object]], device: str,
                     name: str | None, sec: int, usec: int,
                     event_type: int, code: int, value: int) -> dict[str, object] | None:
    if event_type == EV_SYN and code in (SYN_REPORT, SYN_DROPPED):
        pass
    elif event_type == EV_KEY and code in KEY_CODES.values():
        pass
    else:
        return None
    event: dict[str, object] = {
        "device": device, "name": name or "", "sec": sec, "usec": usec,
        "type": event_type, "code": code, "value": value,
    }
    events.append(event)
    return event


def collect(seconds: float, *, sys_root: Path = Path("/sys"),
            dev_root: Path = Path("/dev"),
            max_events: int = DEFAULT_MAX_RECORDS) -> dict[str, object]:
    if (not isinstance(seconds, (int, float)) or isinstance(seconds, bool) or
            not math.isfinite(seconds) or seconds < 0 or seconds > MAX_SECONDS):
        raise ValueError(f"seconds must be between 0 and {MAX_SECONDS:g}")
    if type(max_events) is not int or not 1 <= max_events <= MAX_RECORDS_LIMIT:
        raise ValueError(f"max_events must be between 1 and {MAX_RECORDS_LIMIT}")

    readiness = _readiness_module()
    inventory = readiness.inputs(sys_root, dev_root)
    candidates = [item for item in inventory
                  if item.get("button_observation_candidate") is True]
    events: list[dict[str, object]] = []
    node_statuses: list[dict[str, object]] = []
    capture: dict[str, object] = {
        "requested_seconds": float(seconds),
        "max_event_records": max_events,
        "event_record_size": EVENT.size,
        "event_byte_order": sys.byteorder,
        "eligible_node_count": len(candidates),
        "event_records_read": 0,
        "read_only_open": True,
        "ioctl_used": False,
        "write_used": False,
        "injection_used": False,
        "evdev_grab_used": False,
        "node_statuses": node_statuses,
        "complete_window": False,
        "status": "not_started",
    }
    if seconds == 0:
        for item in candidates:
            node_statuses.append({
                "device": str(item["event"]), "name": item.get("name"),
                "opened": False, "open_error": "zero_duration_not_opened",
                "read_error": None, "eof": False, "partial_record": False,
                "partial_record_bytes": 0, "event_limit_reached": False,
                "syn_dropped": False,
            })
        capture["status"] = "zero_duration"
        capture["detail"] = "no event nodes opened for a zero-length window"
        return {"schema_version": 1, "collector": "passive_read_only_evdev",
                "inventory": inventory, "capture": capture, "events": events,
                "button_analysis": analyze_button_events(inventory, events, node_statuses)}
    if not candidates:
        capture["status"] = "no_eligible_key_nodes"
        capture["detail"] = "no present event node has EV_KEY and a target button capability"
        return {"schema_version": 1, "collector": "passive_read_only_evdev",
                "inventory": inventory, "capture": capture, "events": events,
                "button_analysis": analyze_button_events(inventory, events, node_statuses)}

    handles: list[tuple[int, dict[str, object], bytearray]] = []
    try:
        for item in candidates:
            status: dict[str, object] = {
                "device": str(item["event"]), "name": item.get("name"),
                "expected_sysfs_dev": item.get("sysfs_dev"),
                "opened": False, "open_error": None, "read_error": None,
                "eof": False, "partial_record": False,
                "partial_record_bytes": 0, "event_limit_reached": False,
                "syn_dropped": False,
            }
            node_statuses.append(status)
            try:
                fd = os.open(str(item["event"]),
                             os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
            except OSError as error:
                status["open_error"] = type(error).__name__
                continue
            try:
                opened_stat = os.fstat(fd)
            except OSError as error:
                os.close(fd)
                status["open_error"] = f"fstat_{type(error).__name__}"
                continue
            expected_text = item.get("sysfs_dev")
            try:
                expected = tuple(int(part) for part in str(expected_text).split(":"))
            except (TypeError, ValueError):
                expected = ()
            if not stat.S_ISCHR(opened_stat.st_mode):
                os.close(fd)
                status["open_error"] = "opened_node_not_character_device"
                continue
            actual = (os.major(opened_stat.st_rdev), os.minor(opened_stat.st_rdev))
            if len(expected) != 2 or actual != expected:
                os.close(fd)
                status["open_error"] = "opened_node_device_number_mismatch"
                status["opened_dev"] = f"{actual[0]}:{actual[1]}"
                continue
            status["opened"] = True
            status["opened_dev"] = f"{actual[0]}:{actual[1]}"
            handles.append((fd, item, bytearray()))

        if not handles:
            capture["status"] = "no_nodes_opened"
            return {"schema_version": 1, "collector": "passive_read_only_evdev",
                    "inventory": inventory, "capture": capture, "events": events,
                    "button_analysis": analyze_button_events(inventory, events, node_statuses)}

        deadline = time.monotonic() + float(seconds)
        records_read = 0
        active = {fd for fd, _, _ in handles}
        handle_by_fd = {fd: (item, buffer) for fd, item, buffer in handles}
        limit_reached = False
        while active:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or records_read >= max_events:
                break
            ready, _, _ = select.select(list(active), [], [], min(0.25, remaining))
            for fd in ready:
                if time.monotonic() >= deadline or records_read >= max_events:
                    break
                item, buffer = handle_by_fd[fd]
                node_status = next(status for status in node_statuses
                                   if status["device"] == item["event"])
                while time.monotonic() < deadline and records_read < max_events:
                    remaining_records = max_events - records_read
                    read_size = min(EVENT.size * 64, EVENT.size * remaining_records)
                    try:
                        data = os.read(fd, read_size)
                    except BlockingIOError:
                        break
                    except OSError as error:
                        node_status["read_error"] = type(error).__name__
                        active.discard(fd)
                        break
                    if not data:
                        node_status["eof"] = True
                        active.discard(fd)
                        break
                    buffer.extend(data)
                    while len(buffer) >= EVENT.size and records_read < max_events:
                        sec, usec, event_type, code, value = EVENT.unpack_from(buffer)
                        del buffer[:EVENT.size]
                        records_read += 1
                        if event_type == EV_SYN and code == SYN_DROPPED:
                            node_status["syn_dropped"] = True
                        _selected_events(events, str(item["event"]),
                                         str(item.get("name") or ""), sec, usec,
                                         event_type, code, value)
                    if records_read >= max_events:
                        limit_reached = True
                        break
                    if time.monotonic() >= deadline:
                        break
            if records_read >= max_events:
                limit_reached = True
                break

        for fd, item, buffer in handles:
            status = next(item_status for item_status in node_statuses
                          if item_status["device"] == item["event"])
            if buffer:
                status["partial_record"] = True
                status["partial_record_bytes"] = len(buffer)
            if limit_reached:
                status["event_limit_reached"] = True
        capture["event_records_read"] = records_read
        capture["complete_window"] = not limit_reached
        if limit_reached:
            capture["status"] = "event_record_limit_reached"
        elif any(status["open_error"] for status in node_statuses):
            capture["status"] = "one_or_more_nodes_unavailable"
            capture["complete_window"] = False
        elif any(status["read_error"] or status["eof"] for status in node_statuses):
            capture["status"] = "one_or_more_streams_ended_or_failed"
            capture["complete_window"] = False
        elif any(status["partial_record"] for status in node_statuses):
            capture["status"] = "partial_event_record"
            capture["complete_window"] = False
        elif any(status["syn_dropped"] for status in node_statuses):
            capture["status"] = "syn_dropped_observed"
            capture["complete_window"] = False
        else:
            capture["status"] = "window_completed"
            capture["complete_window"] = True
    finally:
        for fd, _, _ in handles:
            os.close(fd)

    button_analysis = analyze_button_events(inventory, events, node_statuses)
    if capture["status"] == "window_completed" and any(
            node.get("partial_frame") is True
            for button in button_analysis.values()
            for node in button.get("nodes", [])):
        capture["status"] = "unterminated_event_frame"
        capture["complete_window"] = False
    return {"schema_version": 1, "collector": "passive_read_only_evdev",
            "inventory": inventory, "capture": capture, "events": events,
            "button_analysis": button_analysis}


def analyze_capture(document: object) -> dict[str, object]:
    if not isinstance(document, dict):
        raise ValueError("capture document must be a JSON object")
    inventory = document.get("inventory")
    events = document.get("events")
    capture = document.get("capture")
    if not isinstance(inventory, list) or not isinstance(events, list) or not isinstance(capture, dict):
        raise ValueError("capture document requires inventory, events, and capture fields")
    node_statuses = capture.get("node_statuses", [])
    if not isinstance(node_statuses, list):
        raise ValueError("capture.node_statuses must be a list")
    return {
        "schema_version": document.get("schema_version"),
        "collector": "offline_analyzer",
        "capture_status": capture.get("status"),
        "button_analysis": analyze_button_events(inventory, events, node_statuses),
        "claim_limits": {
            "physical_origin": "not inferable from evdev data alone",
            "compositor_effect": "not measured",
            "audio_route_effect": "not measured",
        },
    }


def main(argv: list[str] | None = None, *, sys_root: Path = Path("/sys"),
         dev_root: Path = Path("/dev")) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--seconds", type=float, metavar="SECONDS",
                        help="passively collect for a bounded window")
    action.add_argument("--analyze", type=Path, metavar="JSON",
                        help="reanalyze a prior collector JSON file")
    parser.add_argument("--max-events", type=int, default=DEFAULT_MAX_RECORDS,
                        help=f"maximum raw input_event records (default {DEFAULT_MAX_RECORDS})")
    args = parser.parse_args(argv)
    if args.analyze is not None:
        try:
            document = json.loads(args.analyze.read_text(encoding="utf-8"))
            result = analyze_capture(document)
        except (OSError, json.JSONDecodeError, ValueError) as error:
            parser.error(f"cannot analyze capture: {error}")
    elif args.seconds is not None:
        if (not math.isfinite(args.seconds) or args.seconds < 0 or
                args.seconds > MAX_SECONDS):
            parser.error(f"--seconds must be between 0 and {MAX_SECONDS:g}")
        try:
            result = collect(args.seconds, sys_root=sys_root, dev_root=dev_root,
                             max_events=args.max_events)
        except ValueError as error:
            parser.error(str(error))
    else:
        readiness = _readiness_module()
        result = {
            "mode": "inventory_only",
            "collector": "passive_read_only_evdev",
            "inventory": readiness.inputs(sys_root, dev_root),
            "event_nodes_opened": False,
        }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
