#!/usr/bin/env python3
"""Host-only ABOX log-policy/reader-safety preflight.

Reads a pinned source tree, one existing kernel O-tree's config/command files,
and a sanitized JSON metadata snapshot. It never contacts a device, changes a
setting, opens a log reader, or prints log payloads.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any


PINNED_SOURCE_COMMIT = "4e5c5ad7d950e4de0688b5663965f2075654b2ad"
REPORT_SCHEMA = "audio-log-coverage/v1"
DEBUG_LEVEL = 5
ABOX_OBJECTS = {
    "abox.o": "abox.c",
    "abox_rdma.o": "abox_rdma.c",
    "abox_ipc.o": "abox_ipc.c",
}
CONFIG_KEYS = (
    "CONFIG_DYNAMIC_DEBUG",
    "CONFIG_DYNAMIC_DEBUG_CORE",
    "CONFIG_EXYNOS_MEMORY_LOGGER",
    "CONFIG_SND_SOC_SAMSUNG_ABOX",
    "CONFIG_SND_SOC_SAMSUNG_ABOX_VERSION",
    "CONFIG_SND_SOC_SAMSUNG_ABOX_V4",
)
SOURCE_MATCH_FILES = (
    "sound/soc/samsung/abox/abox.c",
    "sound/soc/samsung/abox/abox_rdma.c",
    "sound/soc/samsung/abox/abox_ipc.c",
    "sound/soc/samsung/abox/abox_memlog.h",
    "drivers/soc/samsung/memlogger.c",
    "include/soc/samsung/memlogger.h",
    "include/linux/dev_printk.h",
)


class CoverageError(ValueError):
    """Invalid or unsafe host evidence input."""


def _git_head(root: Path) -> str | None:
    try:
        return subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def _source_text(root: Path, relative: str) -> str:
    try:
        return (root / relative).read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise CoverageError(f"missing/unreadable pinned source file: {relative}") from exc


def _region(source: str, start: str, end: str) -> str:
    first = source.find(start)
    if first < 0:
        return ""
    last = source.find(end, first + len(start))
    return source[first:last] if last >= 0 else ""


def audit_source(source_root: Path) -> dict[str, Any]:
    head = _git_head(source_root)
    if head != PINNED_SOURCE_COMMIT:
        raise CoverageError("source tree does not match the pinned ABOX audit commit")
    if not _source_files_match_commit(source_root, head, SOURCE_MATCH_FILES):
        raise CoverageError("audited source files differ from pinned commit blobs")

    memlog = _source_text(source_root, "drivers/soc/samsung/memlogger.c")
    memlog_header = _source_text(source_root, "include/soc/samsung/memlogger.h")
    abox_log = _source_text(source_root, "sound/soc/samsung/abox/abox_memlog.h")
    dev_printk = _source_text(source_root, "include/linux/dev_printk.h")

    read_fn = _region(memlog, "static ssize_t memlog_read(",
                      "static unsigned int memlog_poll(")
    open_fn = _region(memlog, "static int memlog_open(",
                      "static void memlog_free_file_prvobj(")
    string_fn = _region(memlog, "static ssize_t memlog_obj_to_string_show(",
                        "static int memlog_obj_create_sysfs(")
    level_store = _region(memlog, "static ssize_t memlog_obj_level_store(",
                          "static ssize_t memlog_obj_level_show(")
    level_show = _region(memlog, "static ssize_t memlog_obj_level_show(",
                         "static ssize_t memlog_obj_enable_store(")
    enable_store = _region(memlog, "static ssize_t memlog_obj_enable_store(",
                           "static ssize_t memlog_obj_enable_show(")
    enable_show = _region(memlog, "static ssize_t memlog_obj_enable_show(",
                          "static ssize_t memlog_obj_name_show(")
    write_fn = _region(memlog, "int memlog_write_vsprintf(",
                       "EXPORT_SYMBOL(memlog_write_vsprintf);")

    checks = {
        "abox_dbg_has_dev_dbg_path": "dev_dbg(dev, \"\" fmt" in abox_log,
        "abox_dbg_writes_memlog_debug_level": (
            "memlog_write_printf(abox_data->drv_log_obj" in abox_log
            and "MEMLOG_LEVEL_DEBUG" in abox_log
        ),
        "debug_level_is_5": "#define MEMLOG_LEVEL_DEBUG\t\t(5)" in memlog_header,
        "writer_rejects_level_above_object_limit": (
            "log_level > obj->log_level" in write_fn and "return -EPERM;" in write_fn
        ),
        "chardev_open_stores_shared_object": "filep->private_data = prvobj;" in open_fn,
        "chardev_read_uses_shared_cursor_and_advances": (
            "prvobj->read_ptr" in read_fn and "prvobj->remained -= copy_size;" in read_fn
            and "prvobj->read_ptr += copy_size;" in read_fn
        ),
        "chardev_read_and_to_string_use_object_mutex": (
            "mutex_lock(&prvobj->file_mutex);" in read_fn
            and "mutex_lock(&prvobj->file_mutex);" in string_fn
        ),
        "to_string_uses_and_advances_same_cursor": (
            "prvobj->to_string(prvobj->read_ptr" in string_fn
            and "prvobj->remained -= parsed_data_size;" in string_fn
            and "prvobj->read_ptr += parsed_data_size;" in string_fn
        ),
        "to_string_attribute_only_for_file_objects": (
            "if (prvobj->obj.log_type == MEMLOG_TYPE_FILE)" in memlog
            and "prvobj->to_string_ka.show = memlog_obj_to_string_show;" in memlog
        ),
        "level_enable_accessors_are_unsynchronized": all(
            region and "mutex_lock" not in region and "raw_spin_lock" not in region
            for region in (level_store, level_show, enable_store, enable_show)
        ),
        "dev_dbg_dynamic_debug_condition_source_present": (
            "defined(CONFIG_DYNAMIC_DEBUG)" in dev_printk
            and "defined(CONFIG_DYNAMIC_DEBUG_CORE) && defined(DYNAMIC_DEBUG_MODULE)" in dev_printk
        ),
    }
    return {
        "commit": head,
        "checks": checks,
        "all_checks_pass": all(checks.values()),
        "interpretation": "static source-contract checks against pinned commit blobs only; kernel C is not extracted, compiled, or executed",
        "reader_model": {
            "cursor_owner": "shared memlog_obj_prv read_ptr/remained, not per-open file position",
            "reader_lock": "object-wide file_mutex serializes memlog_read and to_string show",
            "read_behavior": "both read paths advance/deplete the shared cursor; no source-proven peek/rollback API",
            "metadata_locking": "enable/level show/store are plain accesses without the cursor mutex or log spinlock",
        },
    }


def _file_sha256(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _source_files_match_commit(source_root: Path, commit: str,
                               relative_files: tuple[str, ...]) -> bool:
    for relative in relative_files:
        try:
            committed = subprocess.run(
                ["git", "-C", str(source_root), "show", f"{commit}:{relative}"],
                check=True, capture_output=True,
            ).stdout
            working = (source_root / relative).read_bytes()
        except (OSError, subprocess.CalledProcessError):
            return False
        if working != committed:
            return False
    return True


def _compiled_source_match(source_root: Path, compiled_root: Path) -> dict[str, Any]:
    files = {
        relative: {
            "pinned_sha256": _file_sha256(source_root / relative),
            "compiled_sha256": _file_sha256(compiled_root / relative),
        }
        for relative in SOURCE_MATCH_FILES
    }
    matches = all(
        row["pinned_sha256"] is not None
        and row["pinned_sha256"] == row["compiled_sha256"]
        for row in files.values()
    )
    return {
        "compiled_source_commit": _git_head(compiled_root),
        "relevant_files_match_pinned_source": matches,
        "files": files,
        "purpose": "ties retained O-tree objects to source text used for static interpretation",
    }


def _parse_config(path: Path) -> tuple[dict[str, str], str | None]:
    values: dict[str, str] = {}
    try:
        data = path.read_text(encoding="utf-8")
    except OSError:
        return values, None
    for line in data.splitlines():
        if line.startswith("# ") and line.endswith(" is not set"):
            key = line[2:-11]
            values[key] = "n"
        elif line.startswith("CONFIG_") and "=" in line:
            key, value = line.split("=", 1)
            values[key] = value.strip().strip('"')
    return values, hashlib.sha256(data.encode("utf-8")).hexdigest()


_MACRO_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _parse_macro_flags(tokens: list[str]) -> dict[str, bool] | None:
    """Return final direct -D/-U states; None means malformed flag syntax."""
    macros: dict[str, bool] = {}
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token in ("-D", "-U"):
            if index + 1 >= len(tokens):
                return None
            operation, operand = token, tokens[index + 1]
            index += 2
        elif token.startswith("-D") and len(token) > 2:
            operation, operand = "-D", token[2:]
            index += 1
        elif token.startswith("-U") and len(token) > 2:
            operation, operand = "-U", token[2:]
            index += 1
        else:
            index += 1
            continue

        name = operand.split("=", 1)[0] if operation == "-D" else operand
        if not _MACRO_NAME.fullmatch(name):
            return None
        macros[name] = operation == "-D"
    return macros


def _command_record(path: Path, expected_source: str) -> dict[str, Any]:
    try:
        content = path.read_text(encoding="utf-8", errors="replace").replace("\\\n", " ")
    except OSError:
        return {"available": False, "dev_dbg": "unknown"}
    line = next((row for row in content.splitlines()
                 if row.startswith("cmd_") and ":=" in row), None)
    if line is None:
        return {"available": False, "dev_dbg": "unknown"}
    try:
        tokens = shlex.split(line.split(":=", 1)[1])
    except ValueError:
        return {"available": False, "dev_dbg": "unknown"}
    macros = _parse_macro_flags(tokens)
    source_tokens = [token for token in tokens if token.endswith("/" + expected_source)]
    source_path = Path(source_tokens[-1]).resolve() if source_tokens else None
    source_root = _git_root(source_path.parent) if source_path and source_path.exists() else None
    source_path_matches = False
    if source_root and source_path:
        try:
            relative = source_path.relative_to(source_root).as_posix()
            source_path_matches = relative.endswith(
                "sound/soc/samsung/abox/" + expected_source
            )
        except ValueError:
            pass
    return {
        "available": True,
        "direct_macro_flags_parseable": macros is not None,
        "defines_module": macros.get("MODULE", False) if macros is not None else None,
        "defines_debug": macros.get("DEBUG", False) if macros is not None else None,
        "defines_dynamic_debug_module": (
            macros.get("DYNAMIC_DEBUG_MODULE", False) if macros is not None else None
        ),
        "forced_includes_present": "-include" in tokens,
        "source_file": expected_source if source_path else None,
        "source_commit": _git_head(source_root) if source_root else None,
        "source_path_matches_expected": source_path_matches,
        "_source_root": source_root,
    }


def _git_root(path: Path) -> Path | None:
    try:
        value = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None
    return Path(value)


def _dev_dbg_class(config: dict[str, str], command: dict[str, Any],
                   source_matches: bool) -> str:
    if not command.get("available"):
        return "unknown_missing_compiled_command"
    if not command.get("direct_macro_flags_parseable"):
        return "unknown_malformed_command_line_define_flags"
    if not source_matches:
        return "unknown_compiled_source_differs_from_pinned_audit"
    if config.get("CONFIG_DYNAMIC_DEBUG") == "y":
        return "config_predicts_dynamic_debug_path_effective_macros_unverified"
    if (config.get("CONFIG_DYNAMIC_DEBUG_CORE") == "y"
            and command.get("defines_dynamic_debug_module")):
        return "command_line_config_predicts_dynamic_debug_path_unverified"
    if command.get("defines_debug"):
        return "command_line_config_predicts_direct_debug_path_unverified"
    if (config.get("CONFIG_DYNAMIC_DEBUG") == "n"
            and config.get("CONFIG_DYNAMIC_DEBUG_CORE") == "y"):
        return "command_line_config_predicts_no_dev_dbg_effective_macros_unverified"
    return "unknown_config_or_compiler_flags"


def _snapshot_summary(metadata: dict[str, Any]) -> dict[str, Any]:
    snapshot = metadata.get("snapshot")
    if not isinstance(snapshot, dict):
        raise CoverageError("metadata must include a snapshot object")
    if snapshot.get("scope") not in ("current", "historical_trial"):
        raise CoverageError("snapshot.scope must be current or historical_trial")
    objects = snapshot.get("objects")
    if not isinstance(objects, dict):
        raise CoverageError("snapshot.objects must contain sanitized object metadata")
    result: dict[str, Any] = {
        "scope": snapshot["scope"],
        "observed_at": snapshot.get("observed_at"),
        "trial_id": snapshot.get("trial_id"),
        "linked_trial_window": snapshot.get("linked_trial_window") is True,
        "objects": {},
    }
    for name in ("abox-mem", "abox-file"):
        row = objects.get(name)
        if not isinstance(row, dict):
            result["objects"][name] = {"state": "unknown_missing_metadata"}
            continue
        enabled = row.get("enabled")
        level = row.get("level")
        if enabled not in (True, False, 0, 1) or isinstance(enabled, str):
            result["objects"][name] = {"state": "unknown_invalid_enable_value"}
            continue
        if isinstance(level, bool) or not isinstance(level, int) or not (0 <= level <= DEBUG_LEVEL):
            result["objects"][name] = {"state": "unknown_invalid_level_value"}
            continue
        result["objects"][name] = {"enabled": bool(enabled), "level": level}
    return result


def _memlog_debug_state(snapshot: dict[str, Any]) -> dict[str, Any]:
    mem = snapshot["objects"]["abox-mem"]
    file_obj = snapshot["objects"]["abox-file"]
    if "enabled" not in mem or "level" not in mem:
        state = "unknown_incomplete_abox_mem_metadata"
    elif not mem["enabled"]:
        state = "blocked_object_disabled"
    elif mem["level"] < DEBUG_LEVEL:
        state = "filtered_debug_level_above_object_limit"
    else:
        state = "debug_level_passes_policy_but_capture_not_proven"
    return {
        "debug_level": DEBUG_LEVEL,
        "abox_mem": mem,
        "abox_mem_debug_message_state": state,
        "abox_file": file_obj,
        "abox_file_sink_state": (
            "disabled" if file_obj.get("enabled") is False
            else "enabled" if file_obj.get("enabled") is True
            else "unknown"
        ),
    }


def _reader_decision(reader: Any) -> dict[str, Any]:
    if reader == "metadata_only":
        return {
            "requested_reader": reader,
            "metadata_preflight_allowed": True,
            "payload_read_allowed": False,
            "decision": "metadata_only_no_log_buffer_opened",
        }
    if reader in ("memlog_char_device", "memlog_to_string"):
        return {
            "requested_reader": reader,
            "metadata_preflight_allowed": True,
            "payload_read_allowed": False,
            "decision": "refuse_consuming_shared_cursor_reader",
        }
    return {
        "requested_reader": "unknown",
        "metadata_preflight_allowed": True,
        "payload_read_allowed": False,
        "decision": "refuse_unrecognized_reader",
    }


def build_report(metadata: dict[str, Any], source_root: Path,
                 build_root: Path) -> dict[str, Any]:
    if metadata.get("schema") != "audio-log-metadata/v1":
        raise CoverageError("metadata schema must be audio-log-metadata/v1")
    if metadata.get("policy_write_requested") is True:
        raise CoverageError("preflight refuses requests that would change logging policy")
    source = audit_source(source_root)
    config, config_hash = _parse_config(build_root / ".config")
    commands: dict[str, dict[str, Any]] = {}
    for obj, source_file in ABOX_OBJECTS.items():
        command = _command_record(
            build_root / "sound/soc/samsung/abox" / f".{obj}.cmd", source_file,
        )
        commands[obj] = command
    source_roots = {
        row["_source_root"] for row in commands.values() if row.get("_source_root")
    }
    if len(source_roots) == 1:
        compiled_source = _compiled_source_match(source_root, source_roots.pop())
    else:
        compiled_source = {
            "compiled_source_commit": None,
            "relevant_files_match_pinned_source": False,
            "files": {},
            "purpose": "cannot tie retained O-tree objects to one source root",
        }
    for command in commands.values():
        command.pop("_source_root", None)
        command["dev_dbg"] = _dev_dbg_class(
            config, command, compiled_source["relevant_files_match_pinned_source"],
        )
    snapshot = _snapshot_summary(metadata)
    reader = _reader_decision(metadata.get("requested_reader", "metadata_only"))
    memlog = _memlog_debug_state(snapshot)

    dbg_no_macro_prediction = all(
        command["dev_dbg"] == "command_line_config_predicts_no_dev_dbg_effective_macros_unverified"
        for command in commands.values()
    )
    debug_filtered = memlog["abox_mem_debug_message_state"] in (
        "blocked_object_disabled", "filtered_debug_level_above_object_limit",
    )
    if dbg_no_macro_prediction and debug_filtered:
        producer_state = "memlog_debug_filtered; dev_dbg_no_macro_prediction_unverified"
    else:
        producer_state = "dev_dbg_effective_state_or_memlog_policy_unknown"
    policy_key = (
        "current_policy" if snapshot["scope"] == "current"
        else "saved_historical_policy"
    )

    return {
        "schema": REPORT_SCHEMA,
        "source": source,
        "build": {
            "config_sha256": config_hash,
            "config": {key: config.get(key, "unknown") for key in CONFIG_KEYS},
            "abox_debug_objects": commands,
            "compiled_source_identity": compiled_source,
            "compile_flags_are_observations_not_reconstruction": True,
        },
        "metadata_snapshot": snapshot,
        policy_key: {
            "scope": snapshot["scope"],
            "memlog": memlog,
            "producer_state": producer_state,
            "historical_capture_coverage": "unknown_current_or_saved_policy_does_not_prove_marker_capture",
        },
        "reader_safety": {
            **reader,
            "source_contract_proven_nonconsuming_payload_reader": False,
            "shared_cursor_restore_api_found": False,
        },
        "restoration_requirements": {
            "policy_writes_performed": False,
            "exact_original_values_required_per_object": ["enabled", "level"],
            "restore_and_readback_required": True,
            "global_level_change_allowed": False,
            "serialized_atomic_metadata_update_proven": False,
            "reason": "level/enable accessors are not serialized as an atomic pair; this tool cannot guarantee exact runtime rollback",
        },
        "next_capture": {
            "same_muted_stream_rerun_ready": False,
            "required_before_another_stream": [
                "Do not read memlog char device or to_string: both consume the shared object cursor.",
                "Do not change global or per-object verbosity from this preflight.",
                "Use an independently reviewed bounded diagnostic with a non-consuming fixed-metadata channel for queue result, IPC sender boundary, and selected pointer handler fields.",
                "Correlate any current metadata snapshot to a capture window; historical policy remains unknown without that linkage.",
            ],
        },
        "semantics": {
            "source_checks_execute_kernel_c": False,
            "metadata_snapshot_proves_historical_coverage": False,
            "missing_compile_command_is_negative_runtime_evidence": False,
            "debug_message_policy_pass_means_emitted": False,
            "safe_payload_capture_proven": False,
        },
    }


def _read_metadata(path: str) -> dict[str, Any]:
    try:
        raw = sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")
        value = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CoverageError("cannot read sanitized metadata JSON") from exc
    if not isinstance(value, dict):
        raise CoverageError("metadata root must be an object")
    forbidden = {"log_text", "raw_log", "raw_logs", "payload", "trace", "dmesg"}

    def contains_payload_key(node: Any) -> bool:
        if isinstance(node, dict):
            return any(key.lower() in forbidden or contains_payload_key(child)
                       for key, child in node.items())
        if isinstance(node, list):
            return any(contains_payload_key(child) for child in node)
        return False

    if contains_payload_key(value):
        raise CoverageError("raw log/payload keys are not accepted by this preflight")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("metadata", help="sanitized JSON path, or '-' to read JSON from stdin")
    parser.add_argument("--source-tree", required=True, type=Path,
                        help="read-only pinned kernel source checkout")
    parser.add_argument("--build-tree", required=True, type=Path,
                        help="existing read-only kernel O-tree with .config and .cmd files")
    args = parser.parse_args(argv)
    try:
        report = build_report(_read_metadata(args.metadata), args.source_tree, args.build_tree)
    except CoverageError as exc:
        print(f"audio-log-coverage: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
