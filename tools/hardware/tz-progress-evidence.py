#!/usr/bin/env python3
"""Collect and classify bounded, read-only TrustZone task observations.

``--render-remote`` prints the fixed native-Linux Python collector command. ``--capture``
runs it over the project's sealed USB SSH wrapper; ``--input`` classifies an
existing sanitized capture without contacting a device.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import re
import shlex
import sys
from pathlib import Path
from typing import Any


REPORT_SCHEMA = "tz-progress-evidence/v1"
CAPTURE_SCHEMA = "tz-progress-capture/v2"
MAX_CAPTURE_BYTES = 2 * 1024 * 1024
PINNED_SOURCE_COMMIT = "4e5c5ad7d950e4de0688b5663965f2075654b2ad"
DERIVED_KERNEL_COMMIT = "3fca50941422439b2019db2e4a3dc1016b2138a1"
MAX_SCAN = 4096
MAX_PROCESSES = 4096
MAX_TARGETS = 16
_ROLES = ("worker", "iwlog", "chub_log")
_RELEASE_RE = re.compile(r"^[A-Za-z0-9._+-]{1,64}$")
_UINT64_MAX = (1 << 64) - 1
ROOT = Path(__file__).resolve().parents[2]
# Keep reviewed helper source isolated from the original recovery host's sealed
# USB SSH wrapper and verified host-key artifact; never fall back to ROOT.
ARTIFACT_ROOT = Path("/home/corpunum/s22-linux")


class EvidenceError(ValueError):
    """Invalid or incomplete collector input."""


# This native Python program emits bounded numeric fields and fixed allowlisted labels.
# It reads selected procfs files; it does not write device state, read TrustZone
# payloads or kernel logs, or invoke an SMC.
_REMOTE_PROGRAM = r'''
import json
import math
import os
import re
import time

PROC_ROOT = "/proc"
SAMPLE_INTERVAL = 2
MAX_PROCESSES = 4096
MAX_SCAN = 4096
MAX_TARGETS = 16
UINT64_MAX = (1 << 64) - 1
ROLE_BY_COMM = {
    "tz_worker_threa": "worker",
    "tz_iwlog_thread": "iwlog",
    "chub_log_kthrea": "chub_log",
}
TASK_STATES = {"R", "S", "D", "T", "t", "Z", "X", "I", "K", "W"}


def read_limited(path, limit):
    """Read at most limit + 1 bytes, so overflow is detected without prefetch."""
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0))
    except OSError:
        return None
    try:
        chunks = []
        total = 0
        while total <= limit:
            chunk = os.read(fd, min(8192, limit + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > limit:
                return None
        return b"".join(chunks)
    except OSError:
        return None
    finally:
        os.close(fd)


def parse_uint(raw):
    if raw is None or re.fullmatch(rb"[0-9]+", raw) is None:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value <= UINT64_MAX else None


def read_first_uint(path, limit=128):
    data = read_limited(path, limit)
    if data is None:
        return None
    fields = data.split()
    return parse_uint(fields[0]) if fields else None


def read_uptime():
    data = read_limited(os.path.join(PROC_ROOT, "uptime"), 128)
    if data is None:
        return None
    fields = data.split()
    if not fields or re.fullmatch(rb"[0-9]+(?:[.][0-9]+)?", fields[0]) is None:
        return None
    try:
        value = float(fields[0])
    except ValueError:
        return None
    return value if math.isfinite(value) and 0 <= value <= 1.0e10 else None


def read_boot_id():
    data = read_limited(
        os.path.join(PROC_ROOT, "sys/kernel/random/boot_id"), 128)
    if data is None:
        return None
    fields = data.split()
    if not fields or re.fullmatch(rb"[0-9a-fA-F-]{36}", fields[0]) is None:
        return None
    return fields[0].decode("ascii").lower()


def read_kernel_release():
    data = read_limited(os.path.join(PROC_ROOT, "sys/kernel/osrelease"), 256)
    if data is None:
        return None
    text = data.decode("ascii", errors="replace")
    release = re.sub(r"[^A-Za-z0-9._+-]", "", text)[:64]
    return release or None


def read_status_counters(task_dir):
    data = read_limited(os.path.join(task_dir, "status"), 65536)
    if data is None:
        return None, None
    wanted = {
        b"voluntary_ctxt_switches": None,
        b"nonvoluntary_ctxt_switches": None,
    }
    for line in data.splitlines():
        key, separator, value = line.partition(b":")
        if separator and key in wanted and wanted[key] is None:
            wanted[key] = parse_uint(value.strip())
    voluntary = wanted[b"voluntary_ctxt_switches"]
    nonvoluntary = wanted[b"nonvoluntary_ctxt_switches"]
    if voluntary is None or nonvoluntary is None:
        return None, None
    return voluntary, nonvoluntary


def read_stat(task_dir):
    data = read_limited(os.path.join(task_dir, "stat"), 4096)
    if data is None:
        return "unknown", None
    line = data.splitlines()[0] if data.splitlines() else b""
    close = line.rfind(b") ")
    if close < 0:
        return "unknown", None
    fields = line[close + 2:].split()
    if len(fields) < 20:
        return "unknown", None
    try:
        state = fields[0].decode("ascii")
    except UnicodeDecodeError:
        state = "unknown"
    start_ticks = parse_uint(fields[19])
    return (state if state in TASK_STATES else "unknown"), start_ticks


def read_schedstat(task_dir):
    data = read_limited(os.path.join(task_dir, "schedstat"), 256)
    if data is None:
        return None
    lines = data.splitlines()
    fields = lines[0].split() if lines else []
    values = [parse_uint(token) for token in fields[:3]]
    if len(values) != 3 or any(value is None for value in values):
        return None
    return values


def read_wchan_class(task_dir):
    data = read_limited(os.path.join(task_dir, "wchan"), 512)
    if data is None:
        return "unavailable"
    lines = data.splitlines()
    raw = lines[0].decode("ascii", errors="replace") if lines else ""
    if raw in ("schedule", "__schedule"):
        return "scheduler_wait"
    if raw == "schedule_timeout" or raw.startswith("schedule_timeout_") or raw == "io_schedule":
        return "scheduler_timeout"
    if not raw or raw == "0" or raw.startswith("0x"):
        return "unavailable"
    return "other"


def read_stack_match(task_dir, role):
    data = read_limited(os.path.join(task_dir, "stack"), 32768)
    if data is None:
        return False, False
    if role == "worker":
        patterns = (
            rb"__schedule[+]0x",
            rb"(^|[ \t])schedule[+]0x",
            rb"tz_worker_handler[+]0x",
            rb"smpboot_thread_fn[+]0x",
            rb"kthread[+]0x",
            rb"ret_from_fork[+]0x",
        )
    else:
        patterns = (
            rb"__schedule[+]0x",
            rb"(^|[ \t])schedule[+]0x",
            rb"tz_iwlog_kthread_handler[+]0x",
            rb"kthread[+]0x",
            rb"ret_from_fork[+]0x",
        )
    matched = all(re.search(pattern, data, re.MULTILINE) for pattern in patterns)
    return True, bool(matched)


def emit_task(task_dir, role, slot):
    state, start_ticks = read_stat(task_dir)
    voluntary, nonvoluntary = read_status_counters(task_dir)
    schedstat = read_schedstat(task_dir)
    if schedstat is None:
        run_time = run_delay = run_count = None
    else:
        run_time, run_delay, run_count = schedstat
    if role == "chub_log":
        stack_available, wait_match = False, None
    else:
        stack_available, wait_match = read_stack_match(task_dir, role)
    return {
        "role": role,
        "slot": slot,
        "state": state,
        "stat_available": start_ticks is not None,
        "start_ticks": start_ticks,
        "status_available": voluntary is not None and nonvoluntary is not None,
        "voluntary_context_switches": voluntary,
        "nonvoluntary_context_switches": nonvoluntary,
        "schedstat_available": schedstat is not None,
        "run_time_ns": run_time,
        "run_delay_ns": run_delay,
        "run_count": run_count,
        "wchan_class": read_wchan_class(task_dir),
        "stack_available": stack_available,
        "wait_stack_match": wait_match,
    }


class VisitBudget:
    def __init__(self, limit):
        self.limit = limit
        self.visits = 0
        self.capped = False

    def entries(self, iterator):
        while self.visits < self.limit:
            try:
                entry = next(iterator)
            except StopIteration:
                return
            self.visits += 1
            yield entry
        self.capped = True


def emit_sample(sample_index):
    uptime = read_uptime()
    process_budget = VisitBudget(MAX_PROCESSES)
    task_budget = VisitBudget(MAX_SCAN)
    target_candidates = []
    scan_capped = False
    target_capped = False
    unreadable_comm = 0
    enumeration_errors = 0
    stop = False

    try:
        processes = os.scandir(PROC_ROOT)
    except OSError:
        processes = None
        enumeration_errors = 1

    if processes is not None:
        with processes:
            for process in process_budget.entries(processes):
                if not process.name.isdigit():
                    continue
                try:
                    process_is_dir = process.is_dir(follow_symlinks=False)
                except OSError:
                    enumeration_errors = min(enumeration_errors + 1, MAX_SCAN)
                    process_is_dir = False
                if not process_is_dir:
                    continue
                task_root = os.path.join(process.path, "task")
                try:
                    task_group = os.scandir(task_root)
                except OSError:
                    enumeration_errors = min(enumeration_errors + 1, MAX_SCAN)
                    continue

                with task_group:
                    for task in task_budget.entries(task_group):
                        if not task.name.isdigit():
                            continue
                        try:
                            task_is_dir = task.is_dir(follow_symlinks=False)
                        except OSError:
                            enumeration_errors = min(enumeration_errors + 1, MAX_SCAN)
                            continue
                        if not task_is_dir:
                            continue
                        comm_data = read_limited(os.path.join(task.path, "comm"), 128)
                        if comm_data is None:
                            unreadable_comm = min(unreadable_comm + 1, MAX_SCAN)
                            role = None
                        else:
                            comm_lines = comm_data.splitlines()
                            comm = (comm_lines[0].decode("ascii", errors="replace")
                                    if comm_lines else "")
                            if not comm:
                                unreadable_comm = min(unreadable_comm + 1, MAX_SCAN)
                                role = None
                            else:
                                role = ROLE_BY_COMM.get(comm)
                        if role is not None:
                            if len(target_candidates) >= MAX_TARGETS:
                                target_capped = True
                                stop = True
                            else:
                                target_candidates.append((
                                    role, process.name, task.name, task.path))
                        if stop:
                            break
                    if task_budget.capped:
                        scan_capped = True
                        stop = True
                    if stop:
                        break
            if process_budget.capped:
                scan_capped = True

    slots = {role: 0 for role in ROLE_BY_COMM.values()}
    targets = []
    for role, _pid, _tid, task_dir in sorted(target_candidates):
        targets.append(emit_task(task_dir, role, slots[role]))
        slots[role] += 1

    complete = (
        not scan_capped and not target_capped
        and unreadable_comm == 0 and enumeration_errors == 0
    )
    return {
        "sample_index": sample_index,
        "uptime_seconds": uptime,
        "tasks_scanned": task_budget.visits,
        "enumeration": {
            "complete": complete,
            "scan_capped": scan_capped,
            "target_capped": target_capped,
            "unreadable_comm_count": unreadable_comm,
            "enumeration_error_count": enumeration_errors,
        },
        "tasks": targets,
    }


def all_consistent(values):
    return bool(values) and all(value is not None for value in values) and len(set(values)) == 1


kernel_release = read_kernel_release()
boot_uptime = read_uptime()
boot_id_start = read_boot_id()
release_start = read_kernel_release()
boot_id_0_before = read_boot_id()
release_0_before = read_kernel_release()
sample_0 = emit_sample(0)
boot_id_0_after = read_boot_id()
release_0_after = read_kernel_release()
time.sleep(SAMPLE_INTERVAL)
boot_id_1_before = read_boot_id()
release_1_before = read_kernel_release()
sample_1 = emit_sample(1)
boot_id_1_after = read_boot_id()
release_1_after = read_kernel_release()

boot_identity_consistent = (
    all_consistent((boot_id_start, boot_id_0_before, boot_id_0_after,
                    boot_id_1_before, boot_id_1_after))
    if all(value is not None for value in
           (boot_id_start, boot_id_0_before, boot_id_0_after,
            boot_id_1_before, boot_id_1_after))
    else None
)
kernel_release_consistent = (
    all_consistent((release_start, release_0_before, release_0_after,
                    release_1_before, release_1_after))
    if all(value is not None for value in
           (release_start, release_0_before, release_0_after,
            release_1_before, release_1_after))
    else None
)

sysctl_names = (
    "hung_task_timeout_secs", "hung_task_warnings", "hung_task_panic",
    "watchdog_thresh", "panic_on_warn", "tainted",
)
sysctls = {
    name: read_first_uint(os.path.join(PROC_ROOT, "sys/kernel", name))
    for name in sysctl_names
}
capture = {
    "schema": "tz-progress-capture/v2",
    "boot": {
        "kernel_release": kernel_release,
        "uptime_seconds_at_start": boot_uptime,
        "boot_identity_consistent": boot_identity_consistent,
        "kernel_release_consistent": kernel_release_consistent,
    },
    "sysctls": sysctls,
    "sampling": {
        "interval_seconds": SAMPLE_INTERVAL,
        "samples": [sample_0, sample_1],
    },
}
print(json.dumps(capture, separators=(",", ":"), allow_nan=False))
'''


def _is_uint(value: Any) -> bool:
    return (isinstance(value, int) and not isinstance(value, bool)
            and 0 <= value <= _UINT64_MAX)


def _is_number(value: Any) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


def _valid_sample(sample: Any, expected_index: int) -> bool:
    if (not isinstance(sample, dict)
            or type(sample.get("sample_index")) is not int
            or sample.get("sample_index") != expected_index
            or not _is_number(sample.get("uptime_seconds"))
            or not 0 <= sample["uptime_seconds"] <= 1.0e10
            or not _is_uint(sample.get("tasks_scanned"))
            or sample["tasks_scanned"] > MAX_SCAN):
        return False
    enumeration = sample.get("enumeration")
    tasks = sample.get("tasks")
    if (not isinstance(enumeration, dict) or not isinstance(tasks, list)
            or len(tasks) > MAX_TARGETS
            or type(enumeration.get("complete")) is not bool
            or type(enumeration.get("scan_capped")) is not bool
            or type(enumeration.get("target_capped")) is not bool
            or not _is_uint(enumeration.get("unreadable_comm_count"))
            or enumeration["unreadable_comm_count"] > MAX_SCAN
            or not _is_uint(enumeration.get("enumeration_error_count"))
            or enumeration["enumeration_error_count"] > MAX_SCAN):
        return False
    return all(_valid_task(task) for task in tasks)


def _valid_task(task: Any) -> bool:
    if not isinstance(task, dict):
        return False
    role = task.get("role")
    slot = task.get("slot")
    if role not in _ROLES or not _is_uint(slot) or slot >= MAX_TARGETS:
        return False
    if task.get("state") not in ("R", "S", "D", "T", "t", "Z", "X", "I", "K", "W", "unknown"):
        return False
    for key in ("stat_available", "status_available", "schedstat_available", "stack_available"):
        if type(task.get(key)) is not bool:
            return False
    if task["stat_available"]:
        if not _is_uint(task.get("start_ticks")):
            return False
    elif task.get("start_ticks") is not None:
        return False
    status_values = (task.get("voluntary_context_switches"),
                     task.get("nonvoluntary_context_switches"))
    if task["status_available"]:
        if not all(_is_uint(value) for value in status_values):
            return False
    elif any(value is not None for value in status_values):
        return False
    sched_values = (task.get("run_time_ns"), task.get("run_delay_ns"), task.get("run_count"))
    if task["schedstat_available"]:
        if not all(_is_uint(value) for value in sched_values):
            return False
    elif any(value is not None for value in sched_values):
        return False
    if task.get("wchan_class") not in (
            "scheduler_wait", "scheduler_timeout", "unavailable", "other"):
        return False
    wait_match = task.get("wait_stack_match")
    if wait_match is not None and type(wait_match) is not bool:
        return False
    if task["stack_available"] and wait_match is None:
        return False
    if not task["stack_available"] and role != "chub_log" and wait_match is True:
        return False
    if role == "chub_log" and (task["stack_available"] or wait_match is not None):
        return False
    return True


def _task_map(sample: dict[str, Any]) -> dict[tuple[str, int], dict[str, Any]]:
    tasks: dict[tuple[str, int], dict[str, Any]] = {}
    slots_by_role = {role: [] for role in _ROLES}
    for task in sample["tasks"]:
        role = task.get("role")
        slot = task.get("slot")
        key = (role, slot)
        if key in tasks:
            raise EvidenceError("duplicate target slot in capture")
        tasks[key] = task
        slots_by_role[role].append(slot)
    for slots in slots_by_role.values():
        if sorted(slots) != list(range(len(slots))):
            raise EvidenceError("target slots are not a bounded contiguous sequence")
    return tasks


def _compare_counter_group(
    first: dict[str, Any], second: dict[str, Any], keys: tuple[str, ...], available_key: str
) -> str:
    if first.get(available_key) is not True or second.get(available_key) is not True:
        return "unknown"
    left = [first.get(key) for key in keys]
    right = [second.get(key) for key in keys]
    if not all(_is_uint(value) for value in (*left, *right)):
        return "unknown"
    if any(new < old for old, new in zip(left, right)):
        return "counter_decreased_identity_or_capture_uncertain"
    return "changed" if left != right else "unchanged"


def _aggregate_change(states: list[str], coverage_complete: bool) -> str:
    if any(state == "changed" for state in states):
        return "observed"
    if coverage_complete and states and all(state == "unchanged" for state in states):
        return "no_change_observed"
    return "unknown_incomplete_or_inconsistent"


def _enumeration_complete(sample: dict[str, Any]) -> bool:
    enumeration = sample["enumeration"]
    scanned = sample.get("tasks_scanned")
    unreadable = enumeration.get("unreadable_comm_count")
    enumeration_errors = enumeration.get("enumeration_error_count")
    return (
        enumeration.get("complete") is True
        and enumeration.get("scan_capped") is False
        and enumeration.get("target_capped") is False
        and _is_uint(scanned) and scanned <= MAX_SCAN
        and _is_uint(unreadable) and unreadable == 0
        and _is_uint(enumeration_errors) and enumeration_errors == 0
        and len(sample["tasks"]) <= MAX_TARGETS
    )


def _role_report(
    role: str,
    first: dict[tuple[str, int], dict[str, Any]],
    second: dict[tuple[str, int], dict[str, Any]],
    capture_complete: bool,
    comparison_window_valid: bool,
) -> dict[str, Any]:
    role_keys = sorted((key for key in first.keys() | second.keys() if key[0] == role),
                       key=lambda key: key[1])
    sched_states: list[str] = []
    switch_states: list[str] = []
    state_changes = 0
    wait_both = 0
    comparable = 0
    task_identity_uncertainty = False
    for key in role_keys:
        a = first.get(key)
        b = second.get(key)
        if a is None or b is None:
            task_identity_uncertainty = True
            sched_states.append("unknown")
            switch_states.append("unknown")
            continue
        if a.get("stat_available") is not True or b.get("stat_available") is not True:
            task_identity_uncertainty = True
            sched_states.append("unknown")
            switch_states.append("unknown")
            continue
        a_start = a.get("start_ticks")
        b_start = b.get("start_ticks")
        if not (_is_uint(a_start) and _is_uint(b_start)) or a_start != b_start:
            task_identity_uncertainty = True
            sched_states.append("unknown")
            switch_states.append("unknown")
            continue
        comparable += 1
        if not comparison_window_valid:
            sched_states.append("unknown")
            switch_states.append("unknown")
            continue
        sched_states.append(_compare_counter_group(
            a, b, ("run_time_ns", "run_delay_ns", "run_count"), "schedstat_available"))
        switch_states.append(_compare_counter_group(
            a, b, ("voluntary_context_switches", "nonvoluntary_context_switches"),
            "status_available"))
        if a.get("state") != b.get("state"):
            state_changes += 1
        if (a.get("wait_stack_match") is True and b.get("wait_stack_match") is True
                and role in ("worker", "iwlog")):
            wait_both += 1

    role_coverage = (
        capture_complete and bool(role_keys) and comparable == len(role_keys)
        and not task_identity_uncertainty
    )
    counter_uncertainty = any(
        state == "counter_decreased_identity_or_capture_uncertain"
        for state in (*sched_states, *switch_states)
    )
    counter_coverage_complete = (
        role_coverage
        and all(state in ("changed", "unchanged") for state in sched_states)
        and all(state in ("changed", "unchanged") for state in switch_states)
    )
    source_supported = role in ("worker", "iwlog")
    return {
        "source_wait_path_supported": source_supported,
        "matched_task_slots_in_either_sample": len(role_keys),
        "same_task_pairs": comparable,
        "expected_wait_stack_in_both_samples": wait_both,
        "expected_wait_stack_assessment": (
            "observed_for_at_least_one_same_task" if wait_both else
            "not_observed_or_unavailable"
        ),
        "scheduler_counters": _aggregate_change(sched_states, role_coverage),
        "context_switch_counters": _aggregate_change(switch_states, role_coverage),
        "task_state_changes_observed": state_changes,
        "pair_coverage_complete": role_coverage,
        "counter_coverage_complete": counter_coverage_complete,
        "task_identity_uncertainty": task_identity_uncertainty,
        "identity_or_counter_uncertainty": task_identity_uncertainty or counter_uncertainty,
    }


def build_report(capture: Any) -> dict[str, Any]:
    """Summarize sanitized procfs observations without inferring TEE completion."""
    if not isinstance(capture, dict) or capture.get("schema") != CAPTURE_SCHEMA:
        raise EvidenceError("unrecognized capture schema")
    sampling = capture.get("sampling")
    samples = sampling.get("samples") if isinstance(sampling, dict) else None
    if not isinstance(samples, list) or len(samples) != 2:
        raise EvidenceError("capture must contain exactly two samples")
    if not all(_valid_sample(sample, index) for index, sample in enumerate(samples)):
        raise EvidenceError("capture sample shape is invalid")
    interval = sampling.get("interval_seconds")
    if type(interval) is not int or interval != 2:
        raise EvidenceError("capture interval does not match the rendered collector")

    boot = capture.get("boot")
    if not isinstance(boot, dict):
        raise EvidenceError("capture boot identity summary is missing")
    release = boot.get("kernel_release")
    if release is not None and (not isinstance(release, str) or not _RELEASE_RE.fullmatch(release)):
        raise EvidenceError("capture kernel release is malformed")
    boot_uptime = boot.get("uptime_seconds_at_start")
    if boot_uptime is not None and (
            not _is_number(boot_uptime) or not 0 <= boot_uptime <= 1.0e10):
        raise EvidenceError("capture starting uptime is malformed")
    boot_identity_consistent = boot.get("boot_identity_consistent")
    kernel_release_consistent = boot.get("kernel_release_consistent")
    if (boot_identity_consistent is not None and type(boot_identity_consistent) is not bool
            or kernel_release_consistent is not None and type(kernel_release_consistent) is not bool):
        raise EvidenceError("capture boot consistency flags are malformed")
    sysctls_in = capture.get("sysctls")
    sysctl_keys = (
        "hung_task_timeout_secs", "hung_task_warnings", "hung_task_panic",
        "watchdog_thresh", "panic_on_warn", "tainted",
    )
    if not isinstance(sysctls_in, dict) or any(
            key not in sysctls_in or
            (sysctls_in[key] is not None and not _is_uint(sysctls_in[key]))
            for key in sysctl_keys):
        raise EvidenceError("capture read-only sysctl summary is malformed")

    first_sample, second_sample = samples
    uptime_delta = second_sample["uptime_seconds"] - first_sample["uptime_seconds"]
    if not 1.0 <= uptime_delta <= 10.0:
        uptime_delta = None

    first_tasks = _task_map(first_sample)
    second_tasks = _task_map(second_sample)
    sample_enumeration_complete = all(_enumeration_complete(sample) for sample in samples)
    separated = uptime_delta is not None
    capture_complete = (
        sample_enumeration_complete and separated
        and boot_identity_consistent is True and kernel_release_consistent is True
    )
    comparison_window_valid = (
        capture_complete
    )

    role_results = {
        role: _role_report(role, first_tasks, second_tasks, capture_complete,
                           comparison_window_valid)
        for role in _ROLES
    }
    any_scheduler_activity = any(
        row["scheduler_counters"] == "observed" for row in role_results.values()
    )
    any_context_switch_activity = any(
        row["context_switch_counters"] == "observed" for row in role_results.values()
    )
    any_task_identity_uncertainty = any(
        row["task_identity_uncertainty"] for row in role_results.values()
    )
    any_identity_or_counter_uncertainty = any(
        row["identity_or_counter_uncertainty"] for row in role_results.values()
    )
    safe_release = release if isinstance(release, str) and _RELEASE_RE.fullmatch(release) else None
    safe_boot_uptime = boot_uptime if _is_number(boot_uptime) else None
    sysctls = {
        key: sysctls_in[key] for key in sysctl_keys
    }
    global_identity_uncertainty = (
        boot_identity_consistent is not True or kernel_release_consistent is not True
        or any_identity_or_counter_uncertainty
    )
    complete_for_comparison = (
        capture_complete and not any_task_identity_uncertainty
        and all(row["counter_coverage_complete"] for row in role_results.values())
    )
    scheduler_no_change = all(
        row["scheduler_counters"] == "no_change_observed" for row in role_results.values()
    )
    context_no_change = all(
        row["context_switch_counters"] == "no_change_observed" for row in role_results.values()
    )

    return {
        "schema": REPORT_SCHEMA,
        "capture": {
            "origin_attested": False,
            "two_sample_window_seconds": uptime_delta,
            "procfs_enumeration_complete": sample_enumeration_complete,
            "boot_identity_consistent": boot_identity_consistent,
            "kernel_release_consistent": kernel_release_consistent,
            "complete_for_comparison": complete_for_comparison,
            "task_identity_uncertainty": any_task_identity_uncertainty,
            "identity_or_counter_uncertainty": global_identity_uncertainty,
        },
        "boot": {
            "kernel_release": safe_release,
            "uptime_seconds_at_start": safe_boot_uptime,
        },
        "read_only_sysctls": sysctls,
        "tasks": role_results,
        "assessment": {
            "scheduler_activity": (
                "observed" if complete_for_comparison and any_scheduler_activity else
                "not_observed" if complete_for_comparison and scheduler_no_change
                and not global_identity_uncertainty else "unknown"
            ),
            "context_switch_activity": (
                "observed" if complete_for_comparison and any_context_switch_activity else
                "not_observed" if complete_for_comparison and context_no_change
                and not global_identity_uncertainty else "unknown"
            ),
            "tee_request_completion": "unknown_not_measured_by_procfs_counters",
            "fault": "not_assessed_by_procfs_only_capture",
            "liveness": "unknown_request_state_and_correlated_completion_not_observable",
            "existing_hung_task_warning_cleared": False,
            "camera_observer_disposition": "not_accepted_preserved",
        },
        "limits": {
            "source_commit_reviewed": PINNED_SOURCE_COMMIT,
            "derived_kernel_tree_commit": DERIVED_KERNEL_COMMIT,
            "trustzone_source_paths_unchanged_in_derived_tree": True,
            "procfs_task_counters_show_scheduler_activity_only": True,
            "wait_stack_match_proves_no_outstanding_secure_request": False,
            "post_smc_or_client_completion_event_collected": False,
            "warning_settings_modified": False,
            "raw_stacks_pids_boot_uuid_and_endpoints_emitted": False,
        },
    }


def render_remote(proc_root: str | Path = "/proc") -> str:
    """Return a shell-quoted Python collector command for the fixed proc root."""
    program = _REMOTE_PROGRAM.replace(
        'PROC_ROOT = "/proc"', "PROC_ROOT = " + repr(str(proc_root)), 1)
    return "python3 -c " + shlex.quote(program)


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise EvidenceError("trusted native USB SSH helper is unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _default_remote(command: str, timeout: int):
    """Use the existing sealed-wrapper read-only native USB SSH route."""
    audio = _load_module("s22_tz_progress_remote_helpers",
                         ROOT / "tools/hardware/audio-recovery-reboot-once.py")
    return audio.run_trusted_remote("usb", None, command, timeout=timeout,
                                    project_root=ARTIFACT_ROOT)


def capture_remote(remote=_default_remote) -> dict[str, Any]:
    """Run only the fixed read-only collector and return its bounded JSON."""
    command = "PATH=/usr/bin:/bin sh -c " + shlex.quote(render_remote())
    try:
        result = remote(command, 15)
    except Exception as error:
        raise EvidenceError("native USB SSH procfs capture failed") from error
    if getattr(result, "returncode", 1) != 0:
        raise EvidenceError("native USB SSH procfs capture returned an error")
    output = getattr(result, "stdout", None)
    if isinstance(output, bytes):
        try:
            payload = output.decode("utf-8")
        except UnicodeDecodeError as error:
            raise EvidenceError("native USB SSH procfs capture was not UTF-8") from error
    elif isinstance(output, str):
        payload = output
    else:
        raise EvidenceError("native USB SSH procfs capture returned no text")
    if len(payload.encode("utf-8")) > MAX_CAPTURE_BYTES:
        raise EvidenceError("native USB SSH procfs capture exceeds the size limit")
    try:
        capture = json.loads(payload)
    except json.JSONDecodeError as error:
        raise EvidenceError("native USB SSH procfs capture was not valid JSON") from error
    return capture


def load_capture(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as source:
            payload = source.read(MAX_CAPTURE_BYTES + 1)
    except OSError as exc:
        raise EvidenceError("could not read capture input") from exc
    if len(payload) > MAX_CAPTURE_BYTES:
        raise EvidenceError("capture input exceeds the size limit")
    try:
        value = json.loads(payload)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise EvidenceError("capture input is not valid JSON") from exc
    if not isinstance(value, dict):
        raise EvidenceError("capture input must be a JSON object")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--render-remote", action="store_true",
                      help="print the fixed read-only native Python collector command")
    mode.add_argument("--capture", action="store_true",
                      help="collect over native USB SSH with the sealed project wrapper")
    mode.add_argument("--input", type=Path, help="classify sanitized capture JSON")
    args = parser.parse_args(argv)
    try:
        if args.render_remote:
            sys.stdout.write(render_remote() + "\n")
            return 0
        capture = capture_remote() if args.capture else load_capture(args.input)
        report = build_report(capture)
    except EvidenceError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
