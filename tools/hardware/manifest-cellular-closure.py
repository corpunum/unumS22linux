#!/usr/bin/env python3
"""Create a static manifest for a recovered Samsung cellular ELF closure.

This tool deliberately uses the host ``readelf`` parser only.  It never
executes recovered AArch64 programs, invokes a dynamic loader, or opens a
device.  A recovered tree is classified against an explicitly supplied
isolated Android-system directory, not against the host's glibc libraries.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path


NEEDED_RE = re.compile(r"Shared library: \[(.+?)\]")
HEADER_FIELDS = {
    "Class:": "class",
    "Data:": "data",
    "Type:": "type",
    "Machine:": "machine",
}


def readelf(path: Path, *args: str) -> str:
    # readelf parses bytes; it does not load or run an ELF.  Keep this call
    # explicit so a future change cannot accidentally use an executable probe.
    p = subprocess.run(
        ["readelf", *args, str(path)],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**os.environ, "LC_ALL": "C"},
    )
    return p.stdout


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def elf_header(path: Path) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in readelf(path, "-h").splitlines():
        stripped = line.strip()
        for marker, key in HEADER_FIELDS.items():
            if stripped.startswith(marker):
                fields[key] = stripped[len(marker) :].strip()
                break
    return fields


def needed(path: Path) -> list[str]:
    values = []
    for line in readelf(path, "-d").splitlines():
        match = NEEDED_RE.search(line)
        if match:
            values.append(match.group(1))
    return values


def classify(name: str, vendor_libs: set[str], system_libs: set[str]) -> str:
    if name in vendor_libs:
        return "recovered_vendor"
    if name in system_libs:
        return "available_isolated_system"
    return "missing"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("root", type=Path, help="isolated closure root containing vendor/")
    ap.add_argument("output", type=Path, help="JSON manifest path")
    ap.add_argument(
        "--system-root",
        action="append",
        type=Path,
        default=[],
        help="isolated system lib directory; repeat for more than one",
    )
    ap.add_argument("--source-image-sha256", default=None)
    ap.add_argument("--source-tree", type=Path, default=None)
    args = ap.parse_args()

    root = args.root.resolve()
    if not root.is_dir() or not (root / "vendor").is_dir():
        raise SystemExit(f"closure root must contain vendor/: {root}")
    system_roots = [path.resolve() for path in args.system_root]
    system_libs = {
        path.name
        for directory in system_roots
        if directory.is_dir()
        for path in directory.iterdir()
        if path.is_file()
    }
    vendor_dir = root / "vendor"
    vendor_libs = {
        path.name
        for path in vendor_dir.rglob("*")
        if path.is_file() and path.suffix == ".so"
    }

    files: list[dict[str, object]] = []
    for path in sorted(path for path in vendor_dir.rglob("*") if path.is_file()):
        with path.open("rb") as stream:
            raw = stream.read(4)
        record: dict[str, object] = {
            "path": "/" + path.relative_to(root).as_posix(),
            "bytes": path.stat().st_size,
            "sha256": sha256(path),
            "kind": "ELF" if raw == b"\x7fELF" else "data",
        }
        if raw == b"\x7fELF":
            header = elf_header(path)
            deps = needed(path)
            record["elf"] = header
            record["needed"] = [
                {
                    "name": name,
                    "status": classify(name, vendor_libs, system_libs),
                }
                for name in deps
            ]
        files.append(record)

    dependencies = sorted(
        {
            dep["name"]
            for record in files
            for dep in record.get("needed", [])
        }
    )
    status = {
        name: classify(name, vendor_libs, system_libs) for name in dependencies
    }
    manifest = {
        "schema": "s22-cellular-runtime-closure-2026-09-21",
        "static_only": True,
        "execution_performed": False,
        "source_image_sha256": args.source_image_sha256,
        "source_tree": str(args.source_tree.resolve()) if args.source_tree else None,
        "system_roots": [str(path) for path in system_roots],
        "files": files,
        "dependencies": status,
        "counts": {
            "files": len(files),
            "elf_files": sum(record["kind"] == "ELF" for record in files),
            "missing_dependencies": sum(value == "missing" for value in status.values()),
            "available_isolated_system": sum(
                value == "available_isolated_system" for value in status.values()
            ),
            "recovered_vendor": sum(value == "recovered_vendor" for value in status.values()),
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print(json.dumps(manifest["counts"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
