#!/usr/bin/env python3
"""Read-only ENN ELF/API preflight.

This tool invokes host ``readelf``/``c++filt`` only.  It never dlopens,
executes, relocates, or calls an AArch64/ARM vendor ELF and never accesses a
phone or NPU device.  Its result is a loader plan, not a runtime acceptance.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path

NEEDED_RE = re.compile(r"Shared library: \[(.*?)\]")
SONAME_RE = re.compile(r"Library soname: \[(.*?)\]")
API_NAMES = (
    "EnnInitialize",
    "EnnDeinitialize",
    "EnnGetMetaInfo",
    "EnnOpenModel",
    "EnnOpenModelFromMemory",
    "EnnCloseModel",
)


def run(*args: str) -> str:
    return subprocess.check_output(args, text=True, stderr=subprocess.DEVNULL)


def elf_info(path: Path) -> dict[str, object] | None:
    try:
        header = run("readelf", "-h", str(path))
        dynamic = run("readelf", "-d", str(path))
        symbols = run("readelf", "--dyn-syms", "--wide", str(path))
    except (OSError, subprocess.CalledProcessError):
        return None
    fields: dict[str, object] = {
        "path": str(path),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "size": path.stat().st_size,
        "class": next((line.split(":", 1)[1].strip() for line in header.splitlines() if line.strip().startswith("Class:")), "unknown"),
        "machine": next((line.split(":", 1)[1].strip() for line in header.splitlines() if line.strip().startswith("Machine:")), "unknown"),
        "needed": NEEDED_RE.findall(dynamic),
        "soname": next(iter(SONAME_RE.findall(dynamic)), None),
        "defined_export_symbols": [],
        "defined_api_symbols": [],
    }
    defined: list[dict[str, str]] = []
    defined_api: list[dict[str, str]] = []
    for line in symbols.splitlines():
        if re.search(r"\bUND\b", line):
            continue
        parts = line.split()
        if not parts:
            continue
        raw = parts[-1]
        # Keep the complete defined-export set without spawning one host
        # process per symbol.  Only ENN/query-looking names need demangling;
        # the raw name remains the authoritative full export record.
        if re.search(r"(?:Enn|Version|Device)", raw, re.IGNORECASE):
            try:
                demangled = run("c++filt", raw).strip()
            except (OSError, subprocess.CalledProcessError):
                demangled = raw
        else:
            demangled = raw
        entry = {"raw": raw, "demangled": demangled}
        defined.append(entry)
        if any(name in demangled for name in API_NAMES):
            defined_api.append(entry)
    fields["defined_export_symbols"] = defined
    fields["defined_api_symbols"] = defined_api
    return fields


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("rootfs/npu-vendor-assets-decompressed"))
    parser.add_argument("--api", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=Path("rootfs/hardware-reuse-20260921/enn-loader-preflight.json"))
    args = parser.parse_args()

    api = args.api or args.root / "libenn_public_api_cpp_lib__7c1"
    info = elf_info(api)
    if info is None:
        raise SystemExit(f"not a readable ELF: {api}")
    same_abi: list[dict[str, object]] = []
    for path in sorted(args.root.glob("*.so*")):
        candidate = elf_info(path)
        if candidate is not None and candidate["class"] == info["class"] and candidate["machine"] == info["machine"]:
            same_abi.append(candidate)
    by_soname: dict[str, list[str]] = {}
    for candidate in same_abi:
        soname = candidate.get("soname")
        if isinstance(soname, str):
            by_soname.setdefault(soname, []).append(str(candidate["path"]))

    closure: list[dict[str, object]] = []
    unresolved: list[str] = []
    ambiguous: list[str] = []
    root_soname = str(info.get("soname") or api.name)
    info_by_path = {str(item["path"]): item for item in same_abi}
    pending = [root_soname]
    seen: set[str] = set()
    while pending:
        soname = pending.pop(0)
        if soname in seen:
            continue
        seen.add(soname)
        paths = by_soname.get(soname, [])
        if not paths:
            unresolved.append(soname)
            continue
        report_paths = list(paths)
        api_path = str(api)
        # The selected API filename is a recovered asset alias without the
        # SONAME-shaped `.so` substring, so include it explicitly in the root
        # record even though the generic asset glob does not match it.
        if soname == root_soname and api_path not in report_paths:
            report_paths.append(api_path)
            info_by_path[api_path] = info
        path_hashes = {
            path: str(info_by_path[path]["sha256"])
            for path in report_paths
        }
        distinct_hashes = set(path_hashes.values())
        is_ambiguous = len(distinct_hashes) > 1
        if is_ambiguous:
            ambiguous.append(soname)
        if is_ambiguous and soname != root_soname:
            closure.append({
                "soname": soname,
                "paths": report_paths,
                "sha256_by_path": path_hashes,
                "selected": None,
                "selection": "ambiguous_different_bytes",
            })
            continue
        # The root must be the exact API object requested, even when another
        # path has the same SONAME.  For dependencies identical-byte aliases
        # are safe to resolve deterministically, but remain visible above.
        selected = str(api) if soname == root_soname else paths[0]
        selected_info = info_by_path[selected]
        closure.append({
            "soname": soname,
            "paths": report_paths,
            "sha256_by_path": path_hashes,
            "selected": selected,
            "selection": "exact_api_path" if soname == root_soname else "first_identical_bytes",
            "ambiguous_aliases": is_ambiguous,
        })
        pending.extend(str(dep) for dep in selected_info["needed"])

    defined_names = [entry["demangled"] for entry in info["defined_api_symbols"]]
    query_candidates = [
        entry for entry in info["defined_export_symbols"]
        if re.search(r"(?:Version|Device)", entry["demangled"], re.IGNORECASE)
    ]
    result = {
        "format": "enn-loader-preflight-v1",
        "api": info,
        "same_abi_assets": len(same_abi),
        "closure": closure,
        "ambiguous_sonames": sorted(set(ambiguous)),
        "unresolved_needed_names": sorted(set(unresolved)),
        "entrypoint_assessment": {
            "exported_initialize": any("EnnInitialize" in name for name in defined_names),
            "exported_metadata_query": any("EnnGetMetaInfo" in name for name in defined_names),
            "exported_model_open": any("EnnOpenModel(" in name for name in defined_names),
            "exported_memory_model_open": any("EnnOpenModelFromMemory" in name for name in defined_names),
            "version_or_device_query_found": bool(query_candidates),
            "version_or_device_query_candidates": query_candidates,
            "smallest_known_runtime_entry": "EnnInitialize()",
            "smallest_known_model_entry": "EnnOpenModelFromMemory(const char *, uint32, handle *)",
            "safe_host_call": False,
            "reason": "No public matching header/ABI structs; initialization/model-open may reach vendor services, firmware, buffers, or /dev/vertex10.",
        },
        "guards": {
            "vendor_elf_executed": False,
            "dlopen_attempted": False,
            "phone_accessed": False,
            "npu_ioctl_attempted": False,
        },
        "limitations": [
            "DT_NEEDED closure is a static dependency graph, not proof every library or service is called.",
            "Unresolved names may be supplied by the Android system/vendor image; this tool does not fabricate them.",
            "The AArch64 API symbol signatures are demangled observations, not a substitute for matching headers.",
            "Version/device candidates are reported from the full defined dynamic-symbol set; absence is not proof that an unexported query does not exist.",
            "SONAME aliases with different bytes are marked ambiguous and are not traversed into the closure.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "api_class": info["class"],
        "api_machine": info["machine"],
        "defined_api_symbols": len(info["defined_api_symbols"]),
        "same_abi_assets": len(same_abi),
        "closure_entries": len(closure),
        "ambiguous_sonames": len(set(ambiguous)),
        "unresolved_needed_names": len(set(unresolved)),
        "safe_host_call": False,
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
