#!/usr/bin/env python3
"""Collect a bounded, passive Samsung CPIF/sysfs inventory.

This tool reads only fixed sysfs text attributes and directory-entry names.
It never opens /dev nodes, reads modem identifiers or traces, sends network
traffic, or changes device state. Inventory is not cellular-service acceptance.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA = "s22-cellular-readiness-evidence/v1"
MAX_ATTRIBUTE_BYTES = 128
MAX_DIRECTORY_ENTRIES = 256

# These values come from the pinned Samsung CPIF source's modem_state_string[]
# table. Keep the parser closed over this exact vocabulary.
MODEM_STATES = {
    "OFFLINE": "offline",
    "CRASH_RESET": "crash",
    "CRASH_EXIT": "crash",
    "BOOTING": "transitional",
    "ONLINE": "online",
    "NV_REBUILDING": "transitional",
    "LOADER_DONE": "transitional",
    "SIM_ATTACH": "transitional",
    "SIM_DETACH": "transitional",
    "WDT_RESET": "crash",
    "INIT": "init",
}

OPERSTATES = {
    "unknown",
    "notpresent",
    "down",
    "lowerlayerdown",
    "testing",
    "dormant",
    "up",
}

CPIF_MODULES = (
    "cpif",
    "cpif_page",
    "exynos_cpif_iommu",
    "mcu_ipc",
    "shm_ipc",
    "direct_dm",
    "dev_ril_bridge",
)

RMNET_INTERFACES = tuple(f"rmnet{index}" for index in range(8))
UMTS_CLASS_ENTRY = re.compile(r"^umts_[A-Za-z0-9_-]{1,32}$")

READINESS_KEYS = (
    "sim_presence",
    "sim_network_registration",
    "cellular_data_session",
    "cellular_route_forced",
    "dns_over_cellular",
    "https_over_cellular",
    "ims_registration",
    "outgoing_voice_call",
    "incoming_voice_call",
    "two_way_voice_audio",
    "portable_assistant_cellular_mission",
)


def _lstat_status(path: Path, parent: Path) -> str:
    """Return present/absent/unknown without following the final symlink."""
    return _lstat_result(path, parent)["status"]


def _stat_error_name(error: OSError) -> str:
    if isinstance(error, PermissionError):
        return "permission_denied"
    if isinstance(error, FileNotFoundError):
        return "not_found"
    return "stat_error"


def _lstat_result(path: Path, parent: Path) -> dict[str, Any]:
    """Return a bounded status and sanitized cause for a sysfs lstat."""
    try:
        path.lstat()
        return {"status": "present", "error": None}
    except FileNotFoundError:
        try:
            parent.stat()
        except OSError as error:
            return {"status": "unknown", "error": _stat_error_name(error)}
        return {"status": "absent", "error": None}
    except OSError as error:
        return {"status": "unknown", "error": _stat_error_name(error)}


def _read_ascii(path: Path, limit: int = MAX_ATTRIBUTE_BYTES) -> dict[str, Any]:
    """Read at most limit+1 bytes from one allowlisted sysfs attribute."""
    try:
        with path.open("rb") as stream:
            raw = stream.read(limit + 1)
    except FileNotFoundError:
        return {"status": "missing", "value": None}
    except OSError:
        return {"status": "unreadable", "value": None}

    if len(raw) > limit:
        return {"status": "oversize", "value": None}
    try:
        value = raw.decode("ascii")
    except UnicodeDecodeError:
        return {"status": "non_ascii", "value": None}
    return {"status": "ok", "value": value}


def _single_kernel_token(result: dict[str, Any], vocabulary: set[str]) -> dict[str, Any]:
    if result["status"] != "ok":
        return {"value": None, "status": result["status"], "classification": "unknown"}

    raw = result["value"]
    if raw.endswith("\n"):
        raw = raw[:-1]
    if "\n" in raw or "\r" in raw or raw not in vocabulary:
        return {"value": None, "status": "malformed", "classification": "unknown"}
    return {"value": raw, "status": "ok", "classification": MODEM_STATES.get(raw, "unknown")}


def _bounded_names(path: Path) -> dict[str, Any]:
    """List a bounded number of entry names; do not stat unrelated entries."""
    try:
        names: list[str] = []
        seen = 0
        with os.scandir(path) as entries:
            for entry in entries:
                if seen >= MAX_DIRECTORY_ENTRIES:
                    return {"status": "truncated", "names": names}
                seen += 1
                names.append(entry.name)
        return {"status": "ok", "names": names}
    except FileNotFoundError:
        return {"status": "missing", "names": []}
    except OSError:
        return {"status": "unreadable", "names": []}


def _driver_binding(cpif_path: Path, device_status: str, sysfs_root: Path) -> str:
    if device_status != "present":
        return "unknown"
    driver_path = cpif_path / "driver"
    try:
        metadata = driver_path.lstat()
    except FileNotFoundError:
        return "unbound"
    except OSError:
        return "unknown"
    if not stat.S_ISLNK(metadata.st_mode):
        return "unknown"

    driver_root_path = sysfs_root / "bus" / "platform" / "drivers"
    try:
        driver_root_metadata = driver_root_path.lstat()
        if not stat.S_ISDIR(driver_root_metadata.st_mode):
            return "unknown"
        driver_root = driver_root_path.resolve(strict=True)
        target = driver_path.resolve(strict=True)
    except OSError:
        return "unknown"
    if not driver_root.is_dir() or not target.is_dir() or target.parent != driver_root:
        return "unknown"

    expected_path = driver_root_path / "cp_interface"
    try:
        expected_metadata = expected_path.lstat()
        expected_target = expected_path.resolve(strict=True)
    except OSError:
        expected_metadata = None
        expected_target = None
    if (expected_metadata is not None
            and stat.S_ISDIR(expected_metadata.st_mode)
            and expected_target == target):
        return "bound_cp_interface"
    return "bound_other"


def _module_inventory(sysfs_root: Path) -> dict[str, Any]:
    module_root = sysfs_root / "module"
    root_result = _lstat_result(module_root, sysfs_root)
    root_status = root_result["status"]
    if root_status != "present":
        unknown = list(CPIF_MODULES)
        return {
            "status": root_status,
            "listed": [],
            "not_listed": [],
            "unknown": unknown,
            "errors": {
                name: root_result["error"] or f"module_root_{root_status}"
                for name in CPIF_MODULES
            },
            "per_module": {name: "unknown" for name in CPIF_MODULES},
        }

    listed: list[str] = []
    not_listed: list[str] = []
    unknown: list[str] = []
    errors: dict[str, str] = {}
    per_module: dict[str, str] = {}
    for name in CPIF_MODULES:
        result = _lstat_result(module_root / name, module_root)
        status = result["status"]
        if status == "present":
            listed.append(name)
            per_module[name] = "listed"
        elif status == "absent":
            not_listed.append(name)
            per_module[name] = "not_listed"
        else:
            unknown.append(name)
            per_module[name] = "unknown"
            errors[name] = result["error"] or "stat_error"
    return {
        "status": "partial" if unknown else "ok",
        "listed": listed,
        "not_listed": not_listed,
        "unknown": unknown,
        "errors": errors,
        "per_module": per_module,
    }


def _rmnet_inventory(sysfs_root: Path) -> dict[str, Any]:
    net_root = sysfs_root / "class" / "net"
    entries = _bounded_names(net_root)
    if entries["status"] != "ok":
        return {"status": entries["status"], "interfaces": []}

    names = set(entries["names"])
    interfaces: list[dict[str, Any]] = []
    for name in RMNET_INTERFACES:
        if name not in names:
            continue
        result = _read_ascii(net_root / name / "operstate", limit=32)
        operstate = None
        status = result["status"]
        if status == "ok":
            raw = result["value"]
            if raw.endswith("\n"):
                raw = raw[:-1]
            if "\n" in raw or "\r" in raw or raw not in OPERSTATES:
                status = "malformed"
            else:
                operstate = raw
        interfaces.append({"name": name, "operstate": operstate, "read_status": status})

    return {"status": "ok", "interfaces": interfaces}


def _umts_class_inventory(sysfs_root: Path) -> dict[str, Any]:
    misc_root = sysfs_root / "class" / "misc"
    entries = _bounded_names(misc_root)
    if entries["status"] != "ok":
        return {"status": entries["status"], "entries": []}
    names = sorted(name for name in entries["names"] if UMTS_CLASS_ENTRY.fullmatch(name))
    return {"status": "ok", "entries": names}


def collect(sysfs_root: Path = Path("/sys")) -> dict[str, Any]:
    """Collect a redacted inventory from fixed sysfs interfaces only."""
    cpif_parent = sysfs_root / "bus" / "platform" / "devices"
    cpif_path = cpif_parent / "cpif"
    device_status = _lstat_status(cpif_path, cpif_parent)

    modem_state_result = _read_ascii(cpif_path / "modem_state")
    modem_state = _single_kernel_token(modem_state_result, set(MODEM_STATES))
    if modem_state["value"] == "ONLINE":
        cp_online_observation = "observed"
    elif modem_state["value"] is not None:
        cp_online_observation = "not_observed"
    else:
        cp_online_observation = "unknown"

    readiness: dict[str, str] = {key: "unknown" for key in READINESS_KEYS}
    readiness["cp_online_state"] = cp_online_observation

    return {
        "schema": SCHEMA,
        "collected_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "collection_scope": "passive_fixed_sysfs_inventory",
        "cpif": {
            "platform_device": device_status,
            "driver_binding": _driver_binding(cpif_path, device_status, sysfs_root),
            "modem_state": modem_state,
            "module_inventory": _module_inventory(sysfs_root),
            "umts_class_inventory": _umts_class_inventory(sysfs_root),
        },
        "rmnet_inventory": _rmnet_inventory(sysfs_root),
        "readiness_observations": readiness,
        "limitations": [
            "CPIF device binding, module entries, UMTS class names, and rmnet operstate are inventory only.",
            "ONLINE is the Samsung CP boot state; it does not establish SIM presence or network registration.",
            "No forced-cellular data route, DNS, HTTPS, IMS, call, or two-way audio test is performed.",
            "SIM, registration, cellular internet, IMS, voice, and assistant readiness remain unknown without separate evidence.",
            "Only fixed sysfs names and bounded ASCII attributes are read; no /dev node or /proc trace is opened.",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sysfs-root",
        type=Path,
        default=Path("/sys"),
        help="sysfs root (default: /sys; use a fake tree for host tests)",
    )
    args = parser.parse_args(argv)
    json.dump(collect(args.sysfs_root), sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
