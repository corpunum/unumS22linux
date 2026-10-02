#!/usr/bin/env python3
"""Host-only ABOX capture-gap profile from sanitized aggregates and pinned source.

The profile never contacts a device, opens a trace/log reader, changes logging
or trace policy, or builds a kernel. It checks whether the pinned source has
existing tracepoints for the missing boundaries and runs a small, attributed
preprocessor fixture for the exact public dev_dbg() selection block.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


PINNED_SOURCE_COMMIT = "3fca50941422439b2019db2e4a3dc1016b2138a1"
PROFILE_SCHEMA = "audio-log-capture-profile/v1"
TRIAL_SCHEMA = "s22-audio-capture-summary/v1"
LEGACY_O_TREE = Path("/tmp/s22-hci-candidate-build-20260924")

PINNED_FILES = (
    "sound/soc/samsung/abox/abox.c",
    "sound/soc/samsung/abox/abox_ipc.c",
    "sound/soc/samsung/abox/abox_memlog.h",
    "sound/soc/samsung/abox/abox_rdma.c",
    "drivers/soc/samsung/memlogger.c",
    "include/linux/dev_printk.h",
    "include/soc/samsung/memlogger.h",
    "kernel/trace/trace.c",
    "sound/core/pcm_lib.c",
    "sound/core/pcm_trace.h",
)

# Verbatim selection block from include/linux/dev_printk.h at
# PINNED_SOURCE_COMMIT (GPL-2.0). The host fixture supplies only the surrounding
# names needed to preprocess this block; it does not claim to recreate a kernel
# translation unit or its build flags.
PUBLIC_DEV_DBG_FRAGMENT = """#if defined(CONFIG_DYNAMIC_DEBUG) || \\
\t(defined(CONFIG_DYNAMIC_DEBUG_CORE) && defined(DYNAMIC_DEBUG_MODULE))
#define dev_dbg(dev, fmt, ...)\t\t\t\t\t\t\\
\tdynamic_dev_dbg(dev, dev_fmt(fmt), ##__VA_ARGS__)
#elif defined(DEBUG)
#define dev_dbg(dev, fmt, ...)\t\t\t\t\t\t\\
\tdev_printk(KERN_DEBUG, dev, dev_fmt(fmt), ##__VA_ARGS__)
#else
#define dev_dbg(dev, fmt, ...)\t\t\t\t\t\t\\
({\t\t\t\t\t\t\t\t\t\\
\tif (0)\t\t\t\t\t\t\t\t\\
\t\tdev_printk(KERN_DEBUG, dev, dev_fmt(fmt), ##__VA_ARGS__); \\
})
#endif
"""

RAW_KEYS = {
    "raw_log", "raw_logs", "raw_trace", "log_text", "dmesg", "trace",
    "trace_payload", "kernel_trace", "kernel_delta", "payload", "memlog_payload",
}
_ABOX_TRACE_DECL = re.compile(r"\b(?:TRACE_EVENT|TRACE_EVENT_FN|DEFINE_EVENT)\s*\(")
_TRACE_CALL = re.compile(r"\btrace_[A-Za-z0-9_]+\s*\(")
_MACROS = (
    ("no_enabling_macros", ()),
    ("DEBUG", ("-DDEBUG",)),
    ("CONFIG_DYNAMIC_DEBUG", ("-DCONFIG_DYNAMIC_DEBUG=1",)),
    ("CONFIG_DYNAMIC_DEBUG_CORE_only", ("-DCONFIG_DYNAMIC_DEBUG_CORE=1",)),
    ("CONFIG_DYNAMIC_DEBUG_CORE_and_module", (
        "-DCONFIG_DYNAMIC_DEBUG_CORE=1", "-DDYNAMIC_DEBUG_MODULE=1",
    )),
)


class ProfileError(ValueError):
    """Input or source does not support a safe, bounded profile."""


def _git_head(root: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip()


def _git_source_files(root: Path) -> list[str]:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "ls-files", "sound/soc/samsung/abox"],
            check=True, capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ProfileError("cannot enumerate pinned ABOX source files") from exc
    return [line for line in result.stdout.splitlines()
            if line.endswith((".c", ".h"))]


def _read_text(root: Path, relative: str) -> str:
    try:
        return (root / relative).read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ProfileError(f"missing or unreadable pinned source file: {relative}") from exc


def _source_files_match_commit(root: Path, relative_files: list[str]) -> bool:
    for relative in relative_files:
        try:
            committed = subprocess.run(
                ["git", "-C", str(root), "show", f"{PINNED_SOURCE_COMMIT}:{relative}"],
                check=True, capture_output=True, timeout=5,
            ).stdout
            working = (root / relative).read_bytes()
        except (OSError, subprocess.SubprocessError):
            return False
        if working != committed:
            return False
    return True


def _region(source: str, start: str, end: str) -> str:
    first = source.find(start)
    if first < 0:
        return ""
    last = source.find(end, first + len(start))
    return source[first:last] if last >= 0 else ""


def _extract_dev_dbg_fragment(header: str) -> str:
    start_marker = "#if defined(CONFIG_DYNAMIC_DEBUG) || \\\n\t(defined"
    end_marker = "\n#ifdef CONFIG_PRINTK\n#define dev_level_once"
    start = header.find(start_marker)
    end = header.find(end_marker, start + len(start_marker)) if start >= 0 else -1
    if start < 0 or end < 0:
        raise ProfileError("cannot isolate dev_dbg selection block from pinned header")
    fragment = header[start:end].rstrip("\n") + "\n"
    if fragment != PUBLIC_DEV_DBG_FRAGMENT:
        raise ProfileError("pinned dev_dbg block differs from the reviewed GPL fixture")
    return fragment


def _select_preprocessed_branch(output: str) -> str:
    if re.search(r"\bdynamic_dev_dbg\s*\(", output):
        return "dynamic_debug"
    if re.search(r"\bif\s*\(\s*0\s*\)\s*dev_printk\s*\(", output):
        return "compiled_out"
    if re.search(r"\bdev_printk\s*\(\s*KERN_DEBUG", output):
        return "printk_debug"
    raise ProfileError("preprocessor output did not match a reviewed dev_dbg branch")


def preprocess_dev_dbg_fragment(compiler: str | None = None) -> dict[str, Any]:
    """Run cc -E on the exact header block with explicitly controlled defines."""
    compiler_path = shutil.which(compiler or "cc") or shutil.which("gcc")
    if compiler_path is None:
        return {
            "state": "not_run_compiler_unavailable",
            "cases": {},
            "target_build_macro_state": "unknown",
        }

    program = (
        "#define dev_fmt(fmt) fmt\n"
        "#define KERN_DEBUG KERN_DEBUG_TOKEN\n"
        "struct device;\n"
        + PUBLIC_DEV_DBG_FRAGMENT
        + "void audio_capture_profile_probe(struct device *dev) {\n"
        + "\tdev_dbg(dev, \"CAPTURE_PROFILE_MARKER\");\n}\n"
    )
    cases: dict[str, Any] = {}
    for name, flags in _MACROS:
        try:
            result = subprocess.run(
                [compiler_path, "-E", "-P", "-x", "c", *flags, "-"],
                input=program, check=True, capture_output=True, text=True, timeout=5,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise ProfileError(f"bounded dev_dbg preprocessing failed for {name}") from exc
        cases[name] = {
            "explicit_defines": list(flags),
            "selected_branch": _select_preprocessed_branch(result.stdout),
        }

    version = "unknown"
    try:
        version_result = subprocess.run(
            [compiler_path, "--version"], check=True, capture_output=True,
            text=True, timeout=3,
        )
        version = version_result.stdout.splitlines()[0] if version_result.stdout else "unknown"
    except (OSError, subprocess.SubprocessError):
        pass

    return {
        "state": "executed_exact_public_header_fragment",
        "compiler": compiler_path,
        "compiler_version": version,
        "fragment_source": "include/linux/dev_printk.h",
        "fragment_sha256": hashlib.sha256(PUBLIC_DEV_DBG_FRAGMENT.encode()).hexdigest(),
        "cases": cases,
        "target_build_macro_state": "unknown_no_retained_o_tree_or_cmd_files",
        "meaning": (
            "actual preprocessing of the pinned macro block with explicit fixture defines; "
            "not preprocessing of an ABOX translation unit or evidence of its build flags"
        ),
    }


def _trace_reader_contract(trace_c: str) -> dict[str, Any]:
    open_body = _region(trace_c, "static int tracing_open(struct inode", "/*\n * Some tracers")
    open_iter = _region(trace_c, "__tracing_open(struct inode", "int tracing_open_generic(")
    increment = _region(trace_c, "static void trace_iterator_increment(", "static struct trace_entry *\npeek_next_entry(")
    pipe = _region(trace_c, "tracing_read_pipe(struct file", "static int tracing_snapshot_release(")
    fops = _region(trace_c, "static const struct file_operations tracing_fops = {", "};")
    readme = _region(trace_c, "static const char readme_msg[] =", "tracing_readme_read(")

    checks = {
        "trace_uses_seq_reader": ".read\t\t= seq_read" in fops,
        "trace_iterator_advances_private_ring_iterator": (
            "ring_buffer_iter_advance(buf_iter);" in increment
            and "ring_buffer_consume(" not in increment
        ),
        "trace_open_can_pause_when_pause_on_trace_is_set": (
            "TRACE_ITER_PAUSE_ON_TRACE" in open_iter and "tracing_stop_tr(tr);" in open_iter
        ),
        "write_truncate_can_clear_trace_buffer": (
            "FMODE_WRITE" in open_body and "O_TRUNC" in open_body
            and "tracing_reset_online_cpus(trace_buf);" in open_body
        ),
        "trace_pipe_is_a_consuming_reader": (
            "trace_consume(iter);" in pipe
        ),
        "readme_distinguishes_trace_pipe_consumption": (
            "trace_pipe\\t\\t- A consuming read" in readme
        ),
    }
    if not all(checks.values()):
        raise ProfileError("pinned tracefs reader contract is incomplete; refusing trace recommendation")
    return {
        "checks": checks,
        "static_trace_snapshot_nonconsuming": True,
        "trace_pipe_consumes_records": True,
        "trace_read_may_pause_active_tracing": True,
        "write_truncate_may_clear_buffer": True,
    }


def audit_source(source_root: Path) -> dict[str, Any]:
    head = _git_head(source_root)
    if head != PINNED_SOURCE_COMMIT:
        raise ProfileError("source tree is not the pinned derived 3fca509 checkout")

    abox_files = _git_source_files(source_root)
    checked_files = sorted(set(PINNED_FILES).union(abox_files))
    if not _source_files_match_commit(source_root, checked_files):
        raise ProfileError("source files differ from pinned commit blobs")
    texts = {relative: _read_text(source_root, relative) for relative in PINNED_FILES}
    _extract_dev_dbg_fragment(texts["include/linux/dev_printk.h"])

    abox_sources = [texts[path] for path in (
        "sound/soc/samsung/abox/abox.c",
        "sound/soc/samsung/abox/abox_ipc.c",
        "sound/soc/samsung/abox/abox_rdma.c",
    )]
    extra_abox_sources = [
        _read_text(source_root, relative) for relative in abox_files
        if relative not in PINNED_FILES
    ]
    all_abox = "\n".join(abox_sources + extra_abox_sources)
    abox_trace_decls = sorted(set(_ABOX_TRACE_DECL.findall(all_abox)))
    abox_trace_calls = sorted(set(_TRACE_CALL.findall(all_abox)))

    pcm_trace = texts["sound/core/pcm_trace.h"]
    pcm_lib = texts["sound/core/pcm_lib.c"]
    pointer_call = pcm_lib.find("pos = substream->ops->pointer(substream);")
    trace_call = pcm_lib.find("trace_hwptr(substream, pos, in_interrupt);")
    pcm_trace_guarded = (
        "#ifdef CONFIG_SND_PCM_XRUN_DEBUG\n#define CREATE_TRACE_POINTS\n#include \"pcm_trace.h\"" in pcm_lib
        and "#define trace_hwptr(substream, pos, in_interrupt)" in pcm_lib
    )
    pcm_hwptr_event = (
        "TRACE_SYSTEM snd_pcm" in pcm_trace
        and "TRACE_EVENT(hwptr," in pcm_trace
        and all(token in pcm_trace for token in (
            "__field( unsigned int, card )", "__field( unsigned int, device )",
            "__field( snd_pcm_uframes_t, pos )", "__field( snd_pcm_uframes_t, old_hw_ptr )",
        ))
        and pointer_call >= 0 and trace_call > pointer_call
        and pcm_trace_guarded
    )
    if not pcm_hwptr_event:
        raise ProfileError("pinned ALSA hwptr event contract is incomplete")

    rdma = texts["sound/soc/samsung/abox/abox_rdma.c"]
    ipc = texts["sound/soc/samsung/abox/abox_ipc.c"]
    abox = texts["sound/soc/samsung/abox/abox.c"]
    memlogger = texts["drivers/soc/samsung/memlogger.c"]
    memlog_header = texts["include/soc/samsung/memlogger.h"]
    abox_memlog = texts["sound/soc/samsung/abox/abox_memlog.h"]
    memlog_read = _region(memlogger, "static ssize_t memlog_read(", "static unsigned int memlog_poll(")
    to_string = _region(memlogger, "static ssize_t memlog_obj_to_string_show(", "static int memlog_obj_create_sysfs(")
    shared_cursor = "filep->private_data = prvobj;" in memlogger
    consuming_readers = (
        shared_cursor
        and "prvobj->read_ptr += copy_size;" in memlog_read
        and "prvobj->remained -= copy_size;" in memlog_read
        and "prvobj->read_ptr += parsed_data_size;" in to_string
        and "prvobj->remained -= parsed_data_size;" in to_string
    )

    flow = {
        "trigger_sets_playback_channel_and_message_type": all(token in rdma for token in (
            "pcmtask_msg->msgtype = PCM_PLTDAI_TRIGGER;",
            "msg.task_id = pcmtask_msg->channel_id = data->id;",
            "return abox_rdma_request_ipc(data, &msg, atomic, 0);",
        )),
        "trigger_request_uses_queue_path": all(token in abox for token in (
            "ret = abox_ipc_queue_put(data, dev, hw_irq, msg, size);",
            "queue_work(data->ipc_workqueue, &data->ipc_work);",
        )),
        "queue_put_result_returned_without_success_marker": (
            "return ret;" in _region(abox, "static int abox_schedule_ipc(", "int abox_request_ipc(")
            and "abox_msg_send" not in _region(abox, "static int abox_schedule_ipc(", "int abox_request_ipc(")
        ),
        "mailbox_send_return_not_traced": (
            "ret = abox_msg_send(&cmd, data, count);" in ipc
            and "return ret;" in ipc
            and not _TRACE_CALL.search(ipc)
        ),
        "irq_dispatch_selects_registered_handler": (
            "action->handler(ipc_id, action->data, ipc)" in ipc
        ),
        "rdma_pointer_handler_has_channel_type_and_payload": all(token in rdma for token in (
            "int id = pcmtask_msg->channel_id;",
            "case PCM_PLTDAI_POINTER:",
            "data->pointer = pcmtask_msg->param.pointer;",
            "snd_pcm_period_elapsed(data->substream);",
        )),
        "abox_debug_uses_memlog_level_5": (
            "dev_dbg(dev, \"\" fmt" in abox_memlog
            and "memlog_write_printf(abox_data->drv_log_obj" in abox_memlog
            and "MEMLOG_LEVEL_DEBUG" in abox_memlog
            and "#define MEMLOG_LEVEL_DEBUG\t\t(5)" in memlog_header
        ),
        "memlogger_rejects_level_above_object_policy": (
            "log_level > obj->log_level" in memlogger
            and "return -EPERM;" in _region(
                memlogger, "int memlog_write_vsprintf(", "EXPORT_SYMBOL(memlog_write_vsprintf);",
            )
        ),
        "memlog_payload_readers_advance_shared_cursor": consuming_readers,
    }
    if not all(flow.values()):
        raise ProfileError("pinned ABOX boundary or reader source contract is incomplete")

    return {
        "commit": head,
        "pinned_source_files_verified": len(checked_files),
        "abox_source_files_reviewed": len(abox_files),
        "abox_static_trace_declarations": abox_trace_decls,
        "abox_static_trace_calls": abox_trace_calls,
        "abox_has_static_tracepoint_for_capture_boundaries": bool(
            abox_trace_decls or abox_trace_calls
        ),
        "alsa_hwptr_event": {
            "declared": True,
            "source_call_after_driver_pointer_callback": True,
            "compiled_only_with": "CONFIG_SND_PCM_XRUN_DEBUG",
            "runtime_compilation_state": "unknown_without_retained_config",
            "fields": ["card", "device", "substream", "stream", "pos", "old_hw_ptr"],
            "does_not_cover": [
                "ABOX trigger queue result", "mailbox send result",
                "selected ABOX handler channel/message/pointer payload",
            ],
        },
        "source_flow_checks": flow,
        "memlog_reader": {
            "shared_cursor": shared_cursor,
            "char_device_and_to_string_advance_cursor": consuming_readers,
        },
        "tracefs_reader": _trace_reader_contract(texts["kernel/trace/trace.c"]),
    }


def _contains_raw_key(node: Any) -> bool:
    if isinstance(node, dict):
        return any(key.lower() in RAW_KEYS or _contains_raw_key(value)
                   for key, value in node.items())
    if isinstance(node, list):
        return any(_contains_raw_key(value) for value in node)
    return False


def _count_map(value: Any, *, name: str, allowed_keys: set[str] | None = None) -> dict[str, int]:
    if not isinstance(value, dict) or not value:
        raise ProfileError(f"{name} must be a non-empty count map")
    result: dict[str, int] = {}
    for key, count in value.items():
        if not isinstance(key, str) or (allowed_keys is not None and key not in allowed_keys):
            raise ProfileError(f"{name} contains an unrecognized key")
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise ProfileError(f"{name} counts must be non-negative integers")
        result[key] = count
    return result


def validate_trial_summary(summary: dict[str, Any]) -> dict[str, Any]:
    if summary.get("schema") != TRIAL_SCHEMA:
        raise ProfileError(f"summary schema must be {TRIAL_SCHEMA}")
    if _contains_raw_key(summary):
        raise ProfileError("raw logs, trace payloads, and payload contents are not accepted")
    expected_summary_keys = {
        "schema", "trial_id", "sample_count", "status_counts",
        "running_hw_ptr_counts", "running_rdma_enable_counts",
        "retained_boundary_marker_counts", "capture_coverage", "log_policy",
        "memlog_payload_reader_opened",
    }
    if set(summary) != expected_summary_keys:
        raise ProfileError("capture summary has missing or unrecognized fields")
    trial_id = summary.get("trial_id")
    if not isinstance(trial_id, str) or not trial_id.strip():
        raise ProfileError("trial_id must be non-empty text")
    sample_count = summary.get("sample_count")
    if isinstance(sample_count, bool) or not isinstance(sample_count, int) or sample_count <= 0:
        raise ProfileError("sample_count must be a positive integer")
    status_counts = _count_map(
        summary.get("status_counts"), name="status_counts", allowed_keys={"RUNNING", "OTHER"},
    )
    hw_ptr_counts = _count_map(summary.get("running_hw_ptr_counts"), name="running_hw_ptr_counts")
    rdma_counts = _count_map(
        summary.get("running_rdma_enable_counts"), name="running_rdma_enable_counts",
        allowed_keys={"true", "false"},
    )
    if any(not re.fullmatch(r"(?:0|[1-9][0-9]*)", value) for value in hw_ptr_counts):
        raise ProfileError("running_hw_ptr_counts keys must be canonical non-negative positions")
    running = status_counts.get("RUNNING", 0)
    if sum(status_counts.values()) != sample_count:
        raise ProfileError("status_counts must sum to sample_count")
    if running == 0 or sum(hw_ptr_counts.values()) != running or sum(rdma_counts.values()) != running:
        raise ProfileError("RUNNING pointer/register count maps must cover each RUNNING sample")
    if summary.get("capture_coverage") != "unknown":
        raise ProfileError("capture coverage must remain unknown for this profile")

    markers = summary.get("retained_boundary_marker_counts")
    if not isinstance(markers, dict):
        raise ProfileError("retained_boundary_marker_counts must be an object")
    marker_keys = {"request_queue_result", "mailbox_sender", "selected_pointer_handler"}
    if set(markers) != marker_keys:
        raise ProfileError("boundary marker count keys do not match the reviewed profile")
    marker_counts = _count_map(markers, name="retained_boundary_marker_counts", allowed_keys=marker_keys)

    policy = summary.get("log_policy")
    if not isinstance(policy, dict) or policy.get("scope") not in ("current", "historical_trial"):
        raise ProfileError("log_policy must identify current or historical_trial scope")
    linked = policy.get("linked_to_trial")
    if not isinstance(linked, bool):
        raise ProfileError("log_policy.linked_to_trial must be boolean")
    debug_level = policy.get("debug_level")
    if isinstance(debug_level, bool) or not isinstance(debug_level, int) or debug_level < 0:
        raise ProfileError("log_policy.debug_level must be a non-negative integer")
    objects = policy.get("objects")
    if (not isinstance(objects, dict)
            or set(policy) != {"scope", "linked_to_trial", "debug_level", "objects"}
            or set(objects) != {"abox-mem", "abox-file"}):
        raise ProfileError("log_policy.objects must contain sanitized object metadata")
    normalized_objects: dict[str, dict[str, Any]] = {}
    for name in ("abox-mem", "abox-file"):
        row = objects.get(name)
        if (not isinstance(row, dict) or set(row) != {"enabled", "level"}
                or not isinstance(row.get("enabled"), bool)):
            raise ProfileError(f"log_policy.objects.{name} enable value is unknown")
        level = row.get("level")
        if isinstance(level, bool) or not isinstance(level, int) or not 0 <= level <= 5:
            raise ProfileError(f"log_policy.objects.{name} level is invalid")
        normalized_objects[name] = {"enabled": row["enabled"], "level": level}
    if summary.get("memlog_payload_reader_opened") is not False:
        raise ProfileError("profile requires explicit confirmation that no payload reader was opened")

    return {
        "trial_id": trial_id,
        "sample_count": sample_count,
        "running_samples": running,
        "running_hw_ptr_counts": hw_ptr_counts,
        "running_rdma_enable_counts": rdma_counts,
        "retained_boundary_marker_counts": marker_counts,
        "capture_coverage": "unknown",
        "log_policy": {
            "scope": policy["scope"],
            "linked_to_trial": linked,
            "debug_level": debug_level,
            "objects": normalized_objects,
        },
    }


def summarize_capture_gap(trial: dict[str, Any]) -> dict[str, Any]:
    hw_ptr_counts = trial["running_hw_ptr_counts"]
    rdma_counts = trial["running_rdma_enable_counts"]
    all_hw_ptr_zero = all(int(value) == 0 for value in hw_ptr_counts)
    rdma_disabled_all = set(rdma_counts) == {"false"}
    policy = trial["log_policy"]
    memlog = policy["objects"]["abox-mem"]
    debug_filtered_now = memlog["enabled"] and policy["debug_level"] > memlog["level"]
    markers = trial["retained_boundary_marker_counts"]

    boundaries = {}
    for name, count in markers.items():
        boundaries[name] = {
            "retained_marker_count": count,
            "status": "unknown_no_marker_with_unproven_coverage" if count == 0 else "marker_present_semantics_limited",
        }
    historical_policy = (
        "linked_historical_policy_filters_debug"
        if policy["scope"] == "historical_trial" and policy["linked_to_trial"]
        and debug_filtered_now
        else "current_policy_filters_debug_but_trial_policy_unlinked"
        if debug_filtered_now
        else "trial_policy_or_marker_capture_coverage_unknown"
    )
    return {
        "observed": {
            "running_samples": trial["running_samples"],
            "all_running_hw_ptr_values_zero": all_hw_ptr_zero,
            "all_running_rdma_enable_values_false": rdma_disabled_all,
        },
        "producer_boundaries": boundaries,
        "current_abox_mem_debug_policy_filters_level": debug_filtered_now,
        "historical_policy_assessment": historical_policy,
        "localization": (
            "No progress was observed in sampled ALSA hw_ptr or RDMA enable state. "
            "The evidence does not locate whether the request queue, mailbox send, "
            "firmware receive/handler selection, or subsequent RDMA progress failed."
            if all_hw_ptr_zero and rdma_disabled_all else
            "Sampled status does not establish a unique capture gap location."
        ),
        "absence_is_negative_evidence": False,
    }


def trace_read_decision(runtime: dict[str, Any]) -> dict[str, Any]:
    ready = (
        runtime.get("trace_owner_verified") is True
        and runtime.get("pause_on_trace") is False
        and runtime.get("snd_pcm_hwptr_event_enabled") is True
        and runtime.get("capture_window_linked_to_trace_buffer") is True
    )
    return {
        "read_only_trace_snapshot_ready": ready,
        "decision": "allow_read_only_snapshot_review" if ready else "refuse_until_owner_and_trace_state_are_verified",
    }


def _build_context(source_root: Path) -> dict[str, Any]:
    exists = LEGACY_O_TREE.is_dir()
    files = [
        ".config",
        "sound/soc/samsung/abox/.abox.o.cmd",
        "sound/soc/samsung/abox/.abox_rdma.o.cmd",
        "sound/soc/samsung/abox/.abox_ipc.o.cmd",
    ]
    present = [relative for relative in files if (LEGACY_O_TREE / relative).is_file()]
    source_records = [relative for relative in files if (source_root / relative).is_file()]
    return {
        "candidate_o_tree": str(LEGACY_O_TREE),
        "candidate_o_tree_present": exists,
        "required_build_records_present": present,
        "source_tree_build_records_present": source_records,
        "whole_abox_translation_unit_preprocessing": "not_performed",
        "reason": (
            "the prior O-tree and its .config/.cmd records are unavailable; "
            "the older saved command report targets another source commit"
        ),
        "source_tree_commit": PINNED_SOURCE_COMMIT,
    }


def build_report(summary: dict[str, Any], source_root: Path,
                 compiler: str | None = None) -> dict[str, Any]:
    trial = validate_trial_summary(summary)
    source = audit_source(source_root)
    trace = source["tracefs_reader"]
    runtime = {
        "trace_owner_verified": False,
        "tracing_on": "unknown_not_inspected",
        "pause_on_trace": "unknown_not_inspected",
        "snd_pcm_hwptr_event_enabled": "unknown_not_inspected",
        "capture_window_linked_to_trace_buffer": False,
    }
    read_decision = trace_read_decision(runtime)
    fixture = preprocess_dev_dbg_fragment(compiler)
    return {
        "schema": PROFILE_SCHEMA,
        "source": {
            "commit": source["commit"],
            "pinned_source_files_verified": source["pinned_source_files_verified"],
            "abox_source_files_reviewed": source["abox_source_files_reviewed"],
        },
        "trial": trial,
        "capture_gap": summarize_capture_gap(trial),
        "existing_facilities": {
            "abox_static_tracepoints": {
                "present": source["abox_has_static_tracepoint_for_capture_boundaries"],
                "declarations": source["abox_static_trace_declarations"],
                "calls": source["abox_static_trace_calls"],
            },
            "alsa_hwptr": source["alsa_hwptr_event"],
            "source_flow_checks": source["source_flow_checks"],
            "tracefs_reader": trace,
            "memlog_reader": source["memlog_reader"],
        },
        "read_decision": {
            "trace_snapshot_runtime": runtime,
            **read_decision,
            "reason": (
                "the trace snapshot iterator is non-consuming, but read-open may pause tracing; "
                "runtime ownership, pause_on_trace, event enablement, and window linkage are unknown"
            ),
            "allowed_interface_if_later_reviewed": "O_RDONLY trace snapshot only; never trace_pipe",
            "trace_policy_writes_allowed": False,
            "restore_original_trace_state_required_before_any_future_change": True,
            "memlog_payload_read_allowed": False,
        },
        "build_context": _build_context(source_root),
        "header_preprocessor_fixture": fixture,
        "semantics": {
            "preprocessor_fixture_is_actual_abox_build_evidence": False,
            "tracepoint_source_presence_proves_runtime_availability": False,
            "hwptr_event_proves_firmware_completion": False,
            "zero_hwptr_and_disabled_rdma_locate_exact_failure_boundary": False,
            "missing_marker_proves_event_did_not_occur": False,
            "device_or_stream_access_performed": False,
            "trace_or_logging_policy_changed": False,
            "payload_reader_opened": False,
        },
    }


def _read_summary(path: str) -> dict[str, Any]:
    try:
        raw = sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")
        value = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ProfileError("cannot read sanitized capture summary JSON") from exc
    if not isinstance(value, dict):
        raise ProfileError("capture summary root must be an object")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("summary", help="sanitized aggregate JSON path, or '-' for stdin")
    parser.add_argument("--source-tree", required=True, type=Path,
                        help="read-only pinned source checkout at derived 3fca509")
    parser.add_argument("--compiler", help="host C preprocessor executable (default: cc)")
    args = parser.parse_args(argv)
    try:
        report = build_report(_read_summary(args.summary), args.source_tree, args.compiler)
    except ProfileError as exc:
        print(f"audio-log-capture-profile: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
