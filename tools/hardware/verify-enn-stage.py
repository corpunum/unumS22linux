#!/usr/bin/env python3
"""Verify the host-staged ENN ELF DT_NEEDED closure without loading it."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path

NEEDED_RE = re.compile(r"NEEDED\).*?\[(.*?)\]")
SONAME_RE = re.compile(r"SONAME\).*?\[(.*?)\]")


def dynamic(path: Path) -> tuple[str, list[str]] | None:
    try:
        text = subprocess.check_output(
            ["readelf", "-d", str(path)], text=True, stderr=subprocess.DEVNULL
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    soname = SONAME_RE.search(text)
    return (soname.group(1) if soname else path.name, NEEDED_RE.findall(text))


def interpreter(path: Path) -> str | None:
    try:
        text = subprocess.check_output(
            ["readelf", "-l", str(path)], text=True, stderr=subprocess.DEVNULL
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    match = re.search(r"Requesting program interpreter: (.*?)\]", text)
    return match.group(1) if match else None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--root", type=Path, default=Path("rootfs/npu-compat-20260921")
    )
    parser.add_argument(
        "--output", type=Path, default=None,
        help="closure JSON; defaults to ROOT/manifests/closure.json",
    )
    args = parser.parse_args()
    root = args.root.resolve()
    output = args.output or root / "manifests/closure.json"
    objects: dict[Path, tuple[str, list[str]]] = {}
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        info = dynamic(path)
        if info is not None:
            objects[path] = info

    by_soname: dict[str, list[Path]] = {}
    for path, (soname, _needs) in objects.items():
        by_soname.setdefault(soname, []).append(path)

    roots = [root / "bin/enn-dlopen-probe", root / "vendor/lib64/libenn_public_api_cpp_lib.so"]
    if (root / "bin/enn-init-probe").is_file():
        roots.append(root / "bin/enn-init-probe")
    if any(path not in objects for path in roots):
        missing_roots = [str(path.relative_to(root)) for path in roots if path not in objects]
        raise SystemExit("missing ELF roots: " + ", ".join(missing_roots))
    probe_interpreter = interpreter(roots[0])
    if probe_interpreter != "/system/bin/linker64":
        raise SystemExit(f"unexpected probe interpreter: {probe_interpreter!r}")
    if (root / "bin/enn-init-probe").is_file() and interpreter(root / "bin/enn-init-probe") != probe_interpreter:
        raise SystemExit("initializer interpreter differs from the verified Bionic loader")
    linker = root / "system/bin/linker64"
    if not linker.is_file() or linker.is_symlink() or not linker.stat().st_mode & 0o111:
        raise SystemExit("missing or non-executable system/bin/linker64")
    pending: list[str] = []
    for path in roots:
        pending.extend(objects[path][1])
    seen: set[str] = set()
    missing: set[str] = set()
    ambiguous: dict[str, list[str]] = {}
    selected: dict[str, Path] = {}
    while pending:
        soname = pending.pop(0)
        if soname in seen:
            continue
        seen.add(soname)
        candidates = by_soname.get(soname, [])
        if not candidates:
            missing.add(soname)
            continue
        hashes = {hashlib.sha256(path.read_bytes()).hexdigest() for path in candidates}
        if len(hashes) > 1:
            ambiguous[soname] = [str(path.relative_to(root)) for path in candidates]
            continue
        selected[soname] = candidates[0]
        pending.extend(objects[candidates[0]][1])

    result = {
        "format": "s22-enn-stage-closure-v1",
        "root": str(root),
        "roots": [str(path.relative_to(root)) for path in roots],
        "interpreter": probe_interpreter,
        "objects_scanned": len(objects),
        "reachable_needed_names": sorted(seen),
        "selected": {
            soname: {
                "path": str(path.relative_to(root)),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
            for soname, path in sorted(selected.items())
        },
        "missing": sorted(missing),
        "ambiguous": ambiguous,
        "fail_fast_shims": [],
        "shim_policy": "no abort shims are staged in the NPU root; all reachable libraries are real recovered/canonical baseline objects",
        "host_elf_execution": False,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "objects_scanned": len(objects),
        "reachable_needed_names": len(seen),
        "missing": sorted(missing),
        "ambiguous": sorted(ambiguous),
        "host_elf_execution": False,
    }))
    if missing or ambiguous:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
