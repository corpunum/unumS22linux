#!/usr/bin/env python3
"""Collect and classify bounded, read-only TrustZone task observations.

``--render-remote`` prints the fixed native-Linux shell collector. ``--capture``
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
MAX_TARGETS = 16
_ROLES = ("worker", "iwlog", "chub_log")
_RELEASE_RE = re.compile(r"^[A-Za-z0-9._+-]{1,64}$")
_UINT64_MAX = (1 << 64) - 1
ROOT = Path(__file__).resolve().parents[2]


class EvidenceError(ValueError):
    """Invalid or incomplete collector input."""


# This program emits bounded numeric fields and fixed allowlisted labels. It
# reads selected procfs files and sysctls; it does not write device state, read
# TrustZone payloads, inspect logs, or invoke an SMC.
_REMOTE_SCRIPT = r'''#!/bin/sh
LC_ALL=C
export LC_ALL
PROC_ROOT=/proc
SAMPLE_INTERVAL=2
MAX_SCAN=4096
MAX_TARGETS=16

json_uint() {
    case "$1" in
        ''|*[!0-9]*) printf 'null' ;;
        *) printf '%s' "$1" ;;
    esac
}

read_uint_file() {
    uint_data=$(head -c 129 "$1" 2>/dev/null) || return 0
    [ "${#uint_data}" -le 128 ] || return 0
    printf '%s\n' "$uint_data" | \
        awk 'NR == 1 { if ($1 ~ /^[0-9]+$/) print $1; exit }'
}

read_uptime() {
    uptime_data=$(head -c 129 "$PROC_ROOT/uptime" 2>/dev/null) || return 0
    [ "${#uptime_data}" -le 128 ] || return 0
    printf '%s\n' "$uptime_data" | \
        awk 'NR == 1 { if ($1 ~ /^[0-9]+([.][0-9]+)?$/) print $1; exit }'
}

read_boot_id() {
    boot_data=$(head -c 129 "$PROC_ROOT/sys/kernel/random/boot_id" 2>/dev/null) || return 0
    [ "${#boot_data}" -le 128 ] || return 0
    printf '%s\n' "$boot_data" | \
        awk 'NR == 1 { if (length($1) == 36 && $1 ~ /^[0-9a-fA-F-]+$/) print tolower($1); exit }'
}

read_kernel_release() {
    release_data=$(head -c 257 "$PROC_ROOT/sys/kernel/osrelease" 2>/dev/null) || return 0
    [ "${#release_data}" -le 256 ] || return 0
    printf '%s' "$release_data" | tr -cd 'A-Za-z0-9._+-' | cut -c1-64
}

read_status_uint() {
    status_data=$(head -c 65537 "$1" 2>/dev/null) || return 0
    [ "${#status_data}" -le 65536 ] || return 0
    printf '%s\n' "$status_data" | \
        awk -F: -v key="$2" '$1 == key { gsub(/[[:space:]]/, "", $2); if ($2 ~ /^[0-9]+$/) print $2; exit }'
}

read_wchan_class() {
    wchan_data=$(head -c 513 "$1" 2>/dev/null) || wchan_data=
    [ "${#wchan_data}" -le 512 ] || { printf 'unavailable'; return; }
    raw=$(printf '%s\n' "$wchan_data" | awk 'NR == 1 { print; exit }')
    case "$raw" in
        schedule|__schedule) printf 'scheduler_wait' ;;
        schedule_timeout|schedule_timeout_*|io_schedule) printf 'scheduler_timeout' ;;
        ''|0|0x*) printf 'unavailable' ;;
        *) printf 'other' ;;
    esac
}

read_stack_summary() {
    stack_file=$1
    stack_role=$2
    stack_data=$(head -c 32769 "$stack_file" 2>/dev/null) || return 1
    [ "${#stack_data}" -le 32768 ] || return 1
    if [ "$stack_role" = worker ]; then
        printf '%s\n' "$stack_data" | awk '
            /__schedule[+]0x/ { a=1 }
            /(^|[[:space:]])schedule[+]0x/ { b=1 }
            /tz_worker_handler[+]0x/ { c=1 }
            /smpboot_thread_fn[+]0x/ { d=1 }
            /kthread[+]0x/ { e=1 }
            /ret_from_fork[+]0x/ { f=1 }
            END { printf "%d %d %d %d %d %d\n", a, b, c, d, e, f }
        '
    else
        printf '%s\n' "$stack_data" | awk '
            /__schedule[+]0x/ { a=1 }
            /(^|[[:space:]])schedule[+]0x/ { b=1 }
            /tz_iwlog_kthread_handler[+]0x/ { c=1 }
            /kthread[+]0x/ { d=1 }
            /ret_from_fork[+]0x/ { e=1 }
            END { printf "%d %d %d %d %d\n", a, b, c, d, e }
        '
    fi
}

emit_task() {
    task_dir=$1
    role=$2
    slot=$3

    stat_source=$(head -c 4097 "$task_dir/stat" 2>/dev/null) || stat_source=
    [ "${#stat_source}" -le 4096 ] || stat_source=
    stat_data=$(printf '%s\n' "$stat_source" | \
        awk '{ sub(/^.*\) /, ""); if (NF >= 20) print $1, $20 }')
    set -- $stat_data
    raw_state=${1:-}
    start_ticks=${2:-}
    case "$raw_state" in
        R|S|D|T|t|Z|X|I|K|W) task_state=$raw_state ;;
        *) task_state=unknown ;;
    esac
    case "$start_ticks" in
        ''|*[!0-9]*) stat_available=false; start_ticks_json=null ;;
        *) stat_available=true; start_ticks_json=$start_ticks ;;
    esac

    voluntary=$(read_status_uint "$task_dir/status" voluntary_ctxt_switches)
    nonvoluntary=$(read_status_uint "$task_dir/status" nonvoluntary_ctxt_switches)
    case "$voluntary:$nonvoluntary" in
        *[!0-9:]*|:|*:|:*) status_available=false ;;
        *) status_available=true ;;
    esac

    sched_source=$(head -c 257 "$task_dir/schedstat" 2>/dev/null) || sched_source=
    [ "${#sched_source}" -le 256 ] || sched_source=
    sched_data=$(printf '%s\n' "$sched_source" | \
        awk 'NR == 1 { if (NF >= 3 && $1 ~ /^[0-9]+$/ && $2 ~ /^[0-9]+$/ && $3 ~ /^[0-9]+$/) print $1, $2, $3; exit }')
    set -- $sched_data
    run_ns=${1:-}
    run_delay_ns=${2:-}
    run_count=${3:-}
    case "$run_ns:$run_delay_ns:$run_count" in
        *[!0-9:]*|::|:*|*::|*:) schedstat_available=false ;;
        *) schedstat_available=true ;;
    esac

    wchan_class=$(read_wchan_class "$task_dir/wchan")
    if [ "$role" = chub_log ]; then
        stack_available=false
        wait_stack_match_json=null
    else
        if stack_flags=$(read_stack_summary "$task_dir/stack" "$role"); then
            set -- $stack_flags
            if [ "$role" = worker ]; then
                if [ "${1:-0}${2:-0}${3:-0}${4:-0}${5:-0}${6:-0}" = 111111 ]; then
                    wait_stack_match_json=true
                else
                    wait_stack_match_json=false
                fi
            else
                if [ "${1:-0}${2:-0}${3:-0}${4:-0}${5:-0}" = 11111 ]; then
                    wait_stack_match_json=true
                else
                    wait_stack_match_json=false
                fi
            fi
            stack_available=true
        else
            stack_available=false
            wait_stack_match_json=false
        fi
    fi

    printf '{"role":"%s","slot":%s,"state":"%s","stat_available":%s,"start_ticks":%s,' \
        "$role" "$slot" "$task_state" "$stat_available" "$start_ticks_json"
    printf '"status_available":%s,"voluntary_context_switches":%s,"nonvoluntary_context_switches":%s,' \
        "$status_available" "$(json_uint "$voluntary")" "$(json_uint "$nonvoluntary")"
    printf '"schedstat_available":%s,"run_time_ns":%s,"run_delay_ns":%s,"run_count":%s,' \
        "$schedstat_available" "$(json_uint "$run_ns")" "$(json_uint "$run_delay_ns")" "$(json_uint "$run_count")"
    printf '"wchan_class":"%s","stack_available":%s,"wait_stack_match":%s}' \
        "$wchan_class" "$stack_available" "$wait_stack_match_json"
}

emit_sample() {
    sample_index=$1
    sample_uptime=$(read_uptime)
    scan_count=0
    target_count=0
    worker_slot=0
    iwlog_slot=0
    chub_slot=0
    scan_capped=0
    target_capped=0
    unreadable_comm=0
    enumeration_errors=0
    task_comma=0

    printf '{"sample_index":%s,"uptime_seconds":%s,"tasks_scanned":' \
        "$sample_index" "$(case "$sample_uptime" in ''|*[!0-9.]*|*.*.*) printf null ;; *) printf '%s' "$sample_uptime" ;; esac)"
    # The actual task list is emitted after bounded enumeration so its count
    # and completeness flags are available without buffering raw procfs data.
    # Keep only sanitized records in shell memory.
    task_records=
    for process_dir in "$PROC_ROOT"/[0-9]*; do
        [ -d "$process_dir" ] || continue
        process_name=${process_dir##*/}
        case "$process_name" in ''|*[!0-9]*) continue ;; esac
        task_group="$process_dir/task"
        if [ ! -d "$task_group" ]; then
            [ -d "$process_dir" ] && enumeration_errors=$((enumeration_errors + 1))
            continue
        fi
        for task_dir in "$task_group"/[0-9]*; do
            [ -d "$task_dir" ] || continue
            task_name=${task_dir##*/}
            case "$task_name" in ''|*[!0-9]*) continue ;; esac
            if [ "$scan_count" -ge "$MAX_SCAN" ]; then
                scan_capped=1
                break 2
            fi
            scan_count=$((scan_count + 1))
            if task_comm=$(awk 'NR == 1 { print; exit }' "$task_dir/comm" 2>/dev/null); then
                :
            else
                unreadable_comm=$((unreadable_comm + 1))
                continue
            fi
            if [ -z "$task_comm" ]; then
                unreadable_comm=$((unreadable_comm + 1))
                continue
            fi
            case "$task_comm" in
                tz_worker_threa) role=worker; slot=$worker_slot; worker_slot=$((worker_slot + 1)) ;;
                tz_iwlog_thread) role=iwlog; slot=$iwlog_slot; iwlog_slot=$((iwlog_slot + 1)) ;;
                chub_log_kthrea) role=chub_log; slot=$chub_slot; chub_slot=$((chub_slot + 1)) ;;
                *) continue ;;
            esac
            if [ "$target_count" -ge "$MAX_TARGETS" ]; then
                target_capped=1
                break 2
            fi
            record=$(emit_task "$task_dir" "$role" "$slot")
            if [ "$task_comma" -eq 1 ]; then task_records="$task_records,$record"; else task_records=$record; task_comma=1; fi
            target_count=$((target_count + 1))
        done
    done
    if [ "$scan_capped" -eq 0 ] && [ "$target_capped" -eq 0 ] \
            && [ "$unreadable_comm" -eq 0 ] && [ "$enumeration_errors" -eq 0 ]; then
        scan_complete=true
    else
        scan_complete=false
    fi
    printf '%s,"enumeration":{"complete":%s,"scan_capped":%s,"target_capped":%s,"unreadable_comm_count":%s,"enumeration_error_count":%s},"tasks":[' \
        "$scan_count" "$scan_complete" "$( [ "$scan_capped" -eq 1 ] && printf true || printf false )" \
        "$( [ "$target_capped" -eq 1 ] && printf true || printf false )" \
        "$unreadable_comm" "$enumeration_errors"
    printf '%s]}\n' "$task_records"
}

kernel_release=$(read_kernel_release)
case "$kernel_release" in ''|*[!A-Za-z0-9._+-]*) kernel_release=unknown ;; esac
boot_uptime=$(read_uptime)
case "$boot_uptime" in ''|*[!0-9.]*|*.*.*) boot_uptime_json=null ;; *) boot_uptime_json=$boot_uptime ;; esac
boot_id_start=$(read_boot_id)
release_start=$(read_kernel_release)
boot_id_0_before=$(read_boot_id)
release_0_before=$(read_kernel_release)
sample_0=$(emit_sample 0)
boot_id_0_after=$(read_boot_id)
release_0_after=$(read_kernel_release)
sleep "$SAMPLE_INTERVAL"
boot_id_1_before=$(read_boot_id)
release_1_before=$(read_kernel_release)
sample_1=$(emit_sample 1)
boot_id_1_after=$(read_boot_id)
release_1_after=$(read_kernel_release)

if [ -n "$boot_id_start" ] && [ -n "$boot_id_0_before" ] && [ -n "$boot_id_0_after" ] \
        && [ -n "$boot_id_1_before" ] && [ -n "$boot_id_1_after" ]; then
    if [ "$boot_id_start" = "$boot_id_0_before" ] && [ "$boot_id_start" = "$boot_id_0_after" ] \
            && [ "$boot_id_start" = "$boot_id_1_before" ] && [ "$boot_id_start" = "$boot_id_1_after" ]; then
        boot_identity_consistent=true
    else
        boot_identity_consistent=false
    fi
else
    boot_identity_consistent=null
fi
if [ -n "$release_start" ] && [ -n "$release_0_before" ] && [ -n "$release_0_after" ] \
        && [ -n "$release_1_before" ] && [ -n "$release_1_after" ]; then
    if [ "$release_start" = "$release_0_before" ] && [ "$release_start" = "$release_0_after" ] \
            && [ "$release_start" = "$release_1_before" ] && [ "$release_start" = "$release_1_after" ]; then
        kernel_release_consistent=true
    else
        kernel_release_consistent=false
    fi
else
    kernel_release_consistent=null
fi

case "$kernel_release" in unknown) kernel_release_json=null ;; *) kernel_release_json="\"$kernel_release\"" ;; esac
sys_hung_task_timeout=$(json_uint "$(read_uint_file "$PROC_ROOT/sys/kernel/hung_task_timeout_secs")")
sys_hung_task_warnings=$(json_uint "$(read_uint_file "$PROC_ROOT/sys/kernel/hung_task_warnings")")
sys_hung_task_panic=$(json_uint "$(read_uint_file "$PROC_ROOT/sys/kernel/hung_task_panic")")
sys_watchdog_thresh=$(json_uint "$(read_uint_file "$PROC_ROOT/sys/kernel/watchdog_thresh")")
sys_panic_on_warn=$(json_uint "$(read_uint_file "$PROC_ROOT/sys/kernel/panic_on_warn")")
sys_tainted=$(json_uint "$(read_uint_file "$PROC_ROOT/sys/kernel/tainted")")

printf '{"schema":"tz-progress-capture/v2","boot":{"kernel_release":%s,"uptime_seconds_at_start":%s,"boot_identity_consistent":%s,"kernel_release_consistent":%s},' \
    "$kernel_release_json" "$boot_uptime_json" "$boot_identity_consistent" "$kernel_release_consistent"
printf '"sysctls":{"hung_task_timeout_secs":%s,"hung_task_warnings":%s,"hung_task_panic":%s,"watchdog_thresh":%s,"panic_on_warn":%s,"tainted":%s},' \
    "$sys_hung_task_timeout" "$sys_hung_task_warnings" "$sys_hung_task_panic" \
    "$sys_watchdog_thresh" "$sys_panic_on_warn" "$sys_tainted"
printf '"sampling":{"interval_seconds":%s,"samples":[%s,%s]}}\n' \
    "$SAMPLE_INTERVAL" "$sample_0" "$sample_1"
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
    """Return the fixed collector, optionally rooted at fake procfs for tests."""
    root = shlex.quote(str(proc_root))
    return _REMOTE_SCRIPT.replace("PROC_ROOT=/proc", "PROC_ROOT=" + root, 1)


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
                                    project_root=ROOT)


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
                      help="print the fixed read-only POSIX shell collector")
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
