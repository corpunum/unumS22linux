#!/usr/bin/env python3
"""Read-only, destination-specific host capacity audit.

This tool measures the filesystem containing each explicitly declared output
or incidental destination. It never creates files, contacts a device, or
estimates reclaimable space. Paths are consumed locally and are not echoed in
the report.

Plan schema (JSON):
{
  "schema": 1,
  "operations": [{
    "id": "candidate-build",
    "targets": [{
      "id": "output", "path": "/host/path/build", "path_type": "directory",
      "required_bytes": 1000000, "required_inodes": 3,
      "reserve_bytes": 0, "reserve_inodes": 0
    }]
  }]
}

Every target is checked independently and targets sharing a filesystem inside
one operation are also summed conservatively. Put concurrent destinations in
one operation. A missing path's directories and final file/directory consume
inodes automatically; required_inodes covers additional entries created by
the operation. Existing bytes are reported as context and never subtracted
from required space.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import sys
from typing import Any, Iterable


SCHEMA_VERSION = 1
MAX_PLAN_BYTES = 1_048_576
MAX_OPERATIONS = 64
MAX_TARGETS_PER_OPERATION = 128
SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")
MOUNTINFO_PATH = Path("/proc/self/mountinfo")
PID1_MOUNTINFO_PATH = Path("/proc/1/mountinfo")


@dataclass(frozen=True)
class Mount:
    major_minor: str
    root: str
    point: str
    fs_type: str
    options: str

    @property
    def group_key(self) -> tuple[str, str, str]:
        # Used only for grouping inside this invocation; never emitted.
        return (self.major_minor, self.root, self.fs_type)


def _unescape_mount_field(value: str) -> str:
    return re.sub(
        r"\\(040|011|012|134)",
        lambda match: {
            "040": " ", "011": "\t", "012": "\n", "134": "\\"
        }[match.group(1)],
        value,
    )


def parse_mountinfo(contents: str) -> tuple[Mount, ...]:
    """Parse Linux mountinfo without exposing mount source/options to callers."""
    mounts: list[Mount] = []
    for line in contents.splitlines():
        if not line:
            continue
        sides = line.split(" - ", 1)
        if len(sides) != 2:
            continue
        left = sides[0].split()
        right = sides[1].split()
        if len(left) < 6 or len(right) < 3:
            continue
        mounts.append(Mount(
            major_minor=left[2],
            root=_unescape_mount_field(left[3]),
            point=_unescape_mount_field(left[4]),
            fs_type=right[0],
            options=_unescape_mount_field(" ".join(right[2:])),
        ))
    return tuple(mounts)


def mount_for(path: Path, mounts: Iterable[Mount]) -> Mount | None:
    """Return the most specific mountpoint containing an absolute path."""
    absolute = Path(os.path.abspath(path))
    matches: list[Mount] = []
    for mount in mounts:
        point = Path(mount.point)
        try:
            absolute.relative_to(point)
        except ValueError:
            continue
        matches.append(mount)
    if not matches:
        return None
    return max(matches, key=lambda item: len(Path(item.point).parts))


def _statvfs(path: Path) -> dict[str, int]:
    stats = os.statvfs(path)
    unit = stats.f_frsize or stats.f_bsize
    return {
        "total_bytes": stats.f_blocks * unit,
        "available_bytes": stats.f_bavail * unit,
        "used_bytes": max(0, stats.f_blocks - stats.f_bfree) * unit,
        "total_inodes": stats.f_files,
        "available_inodes": stats.f_favail,
        "used_inodes": max(0, stats.f_files - stats.f_ffree),
    }


def _mount_summary(path: Path, mounts: tuple[Mount, ...]) -> dict[str, Any]:
    mount = mount_for(path, mounts)
    if mount is None:
        return {
            "filesystem_type": None,
            "mount_is_overlay": None,
            "overlay_upper_present": None,
            "same_as_shell_root": None,
            "mount_status": "mount_unknown",
        }
    root_mount = mount_for(Path("/"), mounts)
    options = mount.options.split()
    return {
        "filesystem_type": mount.fs_type,
        "mount_is_overlay": mount.fs_type == "overlay",
        "overlay_upper_present": (
            any(option.startswith("upperdir=") for option in options)
            if mount.fs_type == "overlay" else False
        ),
        "same_as_shell_root": (
            mount.group_key == root_mount.group_key if root_mount else None
        ),
        "mount_status": "ok",
    }


def _namespace_relation() -> str:
    try:
        current = os.readlink("/proc/self/ns/mnt")
        pid1 = os.readlink("/proc/1/ns/mnt")
    except OSError:
        return "unavailable"
    return "same" if current == pid1 else "different"


def _root_observation(label: str, path: Path, mount_path: Path,
                      mounts: tuple[Mount, ...],
                      root_same: bool | None, namespace_relation: str) -> dict[str, Any]:
    result: dict[str, Any] = {"view": label, "audit_status": "ok"}
    try:
        result.update(_statvfs(path))
    except OSError:
        result.update({
            "total_bytes": None, "available_bytes": None, "used_bytes": None,
            "total_inodes": None, "available_inodes": None, "used_inodes": None,
        })
        result["audit_status"] = "unavailable"
    result.update(_mount_summary(mount_path, mounts))
    if label == "pid1_root":
        result["same_as_shell_root"] = root_same
    if label == "pid1_root":
        result["mount_namespace_matches_shell"] = namespace_relation
        result["root_matches_shell"] = root_same
    return result


def inspect_host_roots() -> dict[str, Any]:
    """Capture this process root and PID 1 root, without reading phone state."""
    try:
        shell_mounts = parse_mountinfo(MOUNTINFO_PATH.read_text(encoding="utf-8"))
    except OSError:
        shell_mounts = ()
    try:
        pid1_mounts = parse_mountinfo(PID1_MOUNTINFO_PATH.read_text(encoding="utf-8"))
    except OSError:
        pid1_mounts = ()
    try:
        shell_stat = os.stat("/")
        pid1_stat = os.stat("/proc/1/root")
        root_same = (shell_stat.st_dev, shell_stat.st_ino) == (pid1_stat.st_dev, pid1_stat.st_ino)
    except OSError:
        root_same = None
    relation = _namespace_relation()
    return {
        "shell_root": _root_observation("shell_root", Path("/"), Path("/"),
                                         shell_mounts, True, relation),
        # /proc/1/root is the host-visible handle used for statvfs; the PID1
        # mount table names its own root as `/`.
        "pid1_root": _root_observation("pid1_root", Path("/proc/1/root"),
                                       Path("/"), pid1_mounts, root_same, relation),
    }


def _safe_id(value: Any, where: str) -> str:
    if not isinstance(value, str) or SAFE_ID.fullmatch(value) is None:
        raise ValueError(f"{where} must be a short alphanumeric identifier")
    return value


def _nonnegative_int(value: Any, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{where} must be a nonnegative integer")
    return value


def load_plan(path: Path) -> list[dict[str, Any]]:
    """Load and validate the bounded local JSON plan."""
    size = path.stat().st_size
    if size > MAX_PLAN_BYTES:
        raise ValueError("plan is larger than the supported limit")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema") != SCHEMA_VERSION:
        raise ValueError("plan schema must be 1")
    operations = data.get("operations")
    if not isinstance(operations, list) or not operations or len(operations) > MAX_OPERATIONS:
        raise ValueError("plan must contain 1 to 64 operations")
    seen_operations: set[str] = set()
    normalized: list[dict[str, Any]] = []
    for op_index, operation in enumerate(operations):
        where = f"operations[{op_index}]"
        if not isinstance(operation, dict):
            raise ValueError(f"{where} must be an object")
        op_id = _safe_id(operation.get("id"), f"{where}.id")
        if op_id in seen_operations:
            raise ValueError(f"duplicate operation id: {op_id}")
        seen_operations.add(op_id)
        targets = operation.get("targets")
        if not isinstance(targets, list) or not targets or len(targets) > MAX_TARGETS_PER_OPERATION:
            raise ValueError(f"{where} must contain 1 to 128 targets")
        seen_targets: set[str] = set()
        normalized_targets: list[dict[str, Any]] = []
        for target_index, target in enumerate(targets):
            twhere = f"{where}.targets[{target_index}]"
            if not isinstance(target, dict):
                raise ValueError(f"{twhere} must be an object")
            target_id = _safe_id(target.get("id"), f"{twhere}.id")
            if target_id in seen_targets:
                raise ValueError(f"duplicate target id in {op_id}: {target_id}")
            seen_targets.add(target_id)
            raw_path = target.get("path")
            if not isinstance(raw_path, str) or not raw_path or "\x00" in raw_path:
                raise ValueError(f"{twhere}.path must be a nonempty absolute path")
            path_value = Path(raw_path)
            if not path_value.is_absolute() or ".." in path_value.parts:
                raise ValueError(f"{twhere}.path must be absolute and contain no '..'")
            path_type = target.get("path_type")
            if path_type not in {"file", "directory"}:
                raise ValueError(f"{twhere}.path_type must be file or directory")
            normalized_targets.append({
                "id": target_id,
                "path": path_value,
                "path_type": path_type,
                "required_bytes": _nonnegative_int(target.get("required_bytes", 0), f"{twhere}.required_bytes"),
                "required_inodes": _nonnegative_int(target.get("required_inodes", 0), f"{twhere}.required_inodes"),
                "reserve_bytes": _nonnegative_int(target.get("reserve_bytes", 0), f"{twhere}.reserve_bytes"),
                "reserve_inodes": _nonnegative_int(target.get("reserve_inodes", 0), f"{twhere}.reserve_inodes"),
            })
        normalized.append({"id": op_id, "targets": normalized_targets})
    return normalized


def _path_shape(path: Path, path_type: str) -> dict[str, Any]:
    """Inspect path components without following symlinks or changing state."""
    if path == Path("/"):
        try:
            info = os.lstat(path)
            if not os.path.isdir(path):
                return {"status": "type_mismatch", "missing_components": 0,
                        "exists": True, "existing_bytes": info.st_size}
            return {"status": "ok", "missing_components": 0,
                    "exists": True, "existing_bytes": None}
        except OSError:
            return {"status": "path_unavailable", "missing_components": 0,
                    "exists": None, "existing_bytes": None}
    current = Path("/")
    missing_components: list[Path] = []
    try:
        for index, component in enumerate(path.parts[1:]):
            current = current / component
            final = index == len(path.parts[1:]) - 1
            try:
                info = os.lstat(current)
            except FileNotFoundError:
                missing_components.append(current)
                continue
            except OSError:
                return {"status": "path_unavailable", "missing_components": 0,
                        "exists": None, "existing_bytes": None}
            if os.path.islink(current):
                return {"status": "symlink", "missing_components": 0,
                        "exists": True, "existing_bytes": None}
            if not final and not os.path.isdir(current):
                return {"status": "ancestor_not_directory", "missing_components": 0,
                        "exists": None, "existing_bytes": None}
            if final:
                expected_mode = os.path.isdir(current) if path_type == "directory" else os.path.isfile(current)
                if not expected_mode:
                    return {"status": "type_mismatch", "missing_components": 0,
                            "exists": True, "existing_bytes": getattr(info, "st_size", None)}
                return {"status": "ok", "missing_components": 0,
                        "exists": True,
                        "existing_bytes": info.st_size if path_type == "file" else None}
        # All path components were absent. Count all needed directory/file entries.
        return {"status": "ok", "missing_components": len(missing_components),
                "exists": False, "existing_bytes": None}
    except (OSError, RuntimeError):
        return {"status": "path_unavailable", "missing_components": 0,
                "exists": None, "existing_bytes": None}


def _nearest_existing_parent(path: Path) -> Path | None:
    candidate = path
    while True:
        try:
            info = os.lstat(candidate)
        except FileNotFoundError:
            if candidate == candidate.parent:
                return None
            candidate = candidate.parent
            continue
        except OSError:
            return None
        if os.path.islink(candidate):
            return None
        if os.path.isdir(candidate):
            return candidate
        if candidate == candidate.parent:
            return None
        candidate = candidate.parent


def audit_target(target: dict[str, Any], mounts: tuple[Mount, ...],
                 shell_root: Path = Path("/")) -> dict[str, Any]:
    """Inspect one destination; never emits its path or mount source."""
    path: Path = target["path"]
    shape = _path_shape(path, target["path_type"])
    result: dict[str, Any] = {
        "id": target["id"],
        "path_visible": False,
        "path_type": target["path_type"],
        "destination_status": shape["status"],
        "destination_exists": shape["exists"],
        "existing_bytes_context_only": shape["existing_bytes"],
        "required_bytes": target["required_bytes"],
        "reserve_bytes": target["reserve_bytes"],
        "required_inodes": target["required_inodes"],
        "reserve_inodes": target["reserve_inodes"],
        "auto_path_inodes": shape["missing_components"],
        "audited_path_is_symlink": shape["status"] == "symlink",
    }
    measured_path = path if shape["exists"] and shape["status"] == "ok" else _nearest_existing_parent(path)
    if shape["status"] != "ok" or measured_path is None:
        result.update({
            "filesystem_type": None, "mount_is_overlay": None,
            "same_as_shell_root": None, "available_bytes": None,
            "available_inodes": None, "needed_bytes": target["required_bytes"] + target["reserve_bytes"],
            "needed_inodes": target["required_inodes"] + shape["missing_components"] + target["reserve_inodes"],
            "capacity_status": "not_auditable",
        })
        return result
    try:
        space = _statvfs(measured_path)
    except OSError:
        result.update({
            "filesystem_type": None, "mount_is_overlay": None,
            "same_as_shell_root": None, "available_bytes": None,
            "available_inodes": None, "needed_bytes": target["required_bytes"] + target["reserve_bytes"],
            "needed_inodes": target["required_inodes"] + shape["missing_components"] + target["reserve_inodes"],
            "capacity_status": "not_auditable",
        })
        return result
    mount = mount_for(measured_path, mounts)
    fs_group_same_root = None
    if mount is not None:
        root_mount = mount_for(shell_root, mounts)
        fs_group_same_root = mount.group_key == root_mount.group_key if root_mount else None
    needed_bytes = target["required_bytes"] + target["reserve_bytes"]
    needed_inodes = target["required_inodes"] + shape["missing_components"] + target["reserve_inodes"]
    enough_bytes = space["available_bytes"] >= needed_bytes
    enough_inodes = space["available_inodes"] >= needed_inodes
    result.update({
        "filesystem_type": mount.fs_type if mount else None,
        "mount_is_overlay": mount.fs_type == "overlay" if mount else None,
        "same_as_shell_root": fs_group_same_root,
        "available_bytes": space["available_bytes"],
        "available_inodes": space["available_inodes"],
        "needed_bytes": needed_bytes,
        "needed_inodes": needed_inodes,
        "capacity_status": "sufficient" if enough_bytes and enough_inodes else "insufficient",
    })
    return result


def audit_plan(operations: list[dict[str, Any]], mounts: tuple[Mount, ...]) -> dict[str, Any]:
    audited: list[dict[str, Any]] = []
    overall_ok = True
    for operation in operations:
        targets = [audit_target(target, mounts) for target in operation["targets"]]
        # One operation may write several files to the same filesystem. Sum all
        # declared needs per mount and compare with the measured free capacity.
        groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
        for target_spec, target_result in zip(operation["targets"], targets):
            if target_result["capacity_status"] == "not_auditable":
                overall_ok = False
                continue
            measured_path = (target_spec["path"] if target_result["destination_exists"]
                             else _nearest_existing_parent(target_spec["path"]))
            mount = mount_for(measured_path, mounts) if measured_path else None
            if mount is None:
                target_result["capacity_status"] = "not_auditable"
                overall_ok = False
                continue
            groups.setdefault(mount.group_key, []).append(target_result)
        operation_fs: list[dict[str, Any]] = []
        for grouped_targets in groups.values():
            byte_need = sum(item["needed_bytes"] for item in grouped_targets)
            inode_need = sum(item["needed_inodes"] for item in grouped_targets)
            # The same mount reports identical availability for each member.
            byte_avail = min(item["available_bytes"] for item in grouped_targets)
            inode_avail = min(item["available_inodes"] for item in grouped_targets)
            status = "sufficient" if byte_avail >= byte_need and inode_avail >= inode_need else "insufficient"
            if status != "sufficient":
                overall_ok = False
                for item in grouped_targets:
                    item["capacity_status"] = "insufficient"
                    item["operation_aggregate_capacity_status"] = "insufficient"
            else:
                for item in grouped_targets:
                    item["operation_aggregate_capacity_status"] = "sufficient"
            operation_fs.append({
                "filesystem_type": grouped_targets[0]["filesystem_type"],
                "target_ids": [item["id"] for item in grouped_targets],
                "available_bytes": byte_avail,
                "needed_bytes": byte_need,
                "available_inodes": inode_avail,
                "needed_inodes": inode_need,
                "capacity_status": status,
            })
        audited.append({"id": operation["id"], "targets": targets,
                        "filesystem_aggregates": operation_fs})
    return {"plan_schema": SCHEMA_VERSION, "read_only": True,
            "overall_capacity_status": "sufficient" if overall_ok else "review_required",
            "operations": audited}


def _read_mounts() -> tuple[Mount, ...]:
    try:
        return parse_mountinfo(MOUNTINFO_PATH.read_text(encoding="utf-8"))
    except OSError:
        return ()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path,
                        help="local JSON plan listing every primary and incidental destination")
    args = parser.parse_args(argv)
    try:
        report: dict[str, Any] = {
            "tool": "s22-capacity-rescue-audit",
            "host_views": inspect_host_roots(),
            "scope": "local_host_only_no_phone_access",
        }
        if args.plan is not None:
            mounts = _read_mounts()
            report["plan_audit"] = audit_plan(load_plan(args.plan), mounts)
        print(json.dumps(report, sort_keys=True, indent=2))
        if args.plan is not None and report["plan_audit"]["overall_capacity_status"] != "sufficient":
            return 1
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        # Do not include exception strings which can embed caller-supplied paths.
        print(json.dumps({"tool": "s22-capacity-rescue-audit",
                          "audit_status": "invalid_or_unavailable_input",
                          "error_type": type(exc).__name__,
                          "read_only": True}, sort_keys=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
