#!/usr/bin/env python3
"""Build static manifests for the no-call cellular loader root.

Only readelf, hashing, and filesystem metadata are used. No staged ELF is
loaded or executed.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path

NEEDED = re.compile(r"Shared library: \[(.+?)\]")
SONAME = re.compile(r"Library soname: \[(.+?)\]")


def dynamic(path: Path) -> tuple[str, list[str]] | None:
    p = subprocess.run(["readelf", "-d", str(path)], text=True,
                       capture_output=True, check=True)
    lines = p.stdout.splitlines()
    soname = next((m.group(1) for line in lines if (m := SONAME.search(line))), path.name)
    return soname, [m.group(1) for line in lines if (m := NEEDED.search(line))]


def main() -> int:
    root = Path(__file__).resolve().parents[2] / "rootfs/cellular-loader-20260921"
    manifests = root / "manifests"
    manifests.mkdir(exist_ok=True)
    objects: dict[Path, tuple[str, list[str]]] = {}
    regular = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise SystemExit(f"symlink forbidden: {path.relative_to(root)}")
        if not path.is_file():
            if path.exists():
                raise SystemExit(f"non-regular file forbidden: {path.relative_to(root)}")
            continue
        if path == manifests / "files.json":
            continue
        regular.append(path)
        # linker64 is the interpreter, not a candidate for its ld-android.so
        # SONAME; libdl/libc resolve that SONAME to the distinct ld-android.so.
        if (path.suffix == ".so" or path.name in {"linker64", "cellular-dlopen-probe"}) \
                and path.name != "linker64":
            objects[path] = dynamic(path)
    by_soname: dict[str, list[Path]] = {}
    for path, (soname, _needs) in objects.items():
        by_soname.setdefault(soname, []).append(path)
    roots = [root / "bin/cellular-dlopen-probe", root / "vendor/lib64/libsec-ril.so"]
    pending = [name for path in roots for name in objects[path][1]]
    seen: set[str] = set()
    selected: dict[str, Path] = {}
    missing: set[str] = set()
    ambiguous: dict[str, list[str]] = {}
    while pending:
        name = pending.pop(0)
        if name in seen:
            continue
        seen.add(name)
        candidates = by_soname.get(name, [])
        if not candidates:
            missing.add(name)
            continue
        hashes = {hashlib.sha256(p.read_bytes()).hexdigest() for p in candidates}
        if len(hashes) != 1:
            ambiguous[name] = [str(p.relative_to(root)) for p in candidates]
            continue
        selected[name] = candidates[0]
        pending.extend(objects[candidates[0]][1])
    closure = {
        "format": "s22-cellular-loader-closure-v1",
        "root": str(root),
        "roots": [str(p.relative_to(root)) for p in roots],
        "objects_scanned": len(objects),
        "reachable_needed_names": sorted(seen),
        "selected": {name: {"path": str(path.relative_to(root)),
                             "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                     for name, path in sorted(selected.items())},
        "missing": sorted(missing),
        "ambiguous": ambiguous,
        "counts": {"objects_scanned": len(objects), "reachable": len(seen),
                   "missing": len(missing), "ambiguous": len(ambiguous)},
        "host_elf_execution": False,
    }
    (manifests / "closure.json").write_text(json.dumps(closure, indent=2, sort_keys=True) + "\n")
    regular = sorted(set(regular + [manifests / "closure.json"]))
    files = [{"path": str(p.relative_to(root)), "bytes": p.stat().st_size,
              "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in regular]
    manifest = {"format": "s22-cellular-loader-files-v1", "static_only": True,
                "execution_performed": False, "files": files}
    (manifests / "files.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"files": len(files), "objects_scanned": len(objects),
                      "missing": sorted(missing), "ambiguous": sorted(ambiguous),
                      "execution_performed": False}))
    return 2 if missing or ambiguous else 0


if __name__ == "__main__":
    raise SystemExit(main())
