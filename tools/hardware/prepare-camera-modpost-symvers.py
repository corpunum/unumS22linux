#!/usr/bin/env python3
"""Prepare a pinned modpost dependency list excluding fimc-is's old exports."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys


DEFAULT_OWNER = "drivers/media/platform/exynos/camera/fimc-is"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def filter_symvers(
    data: bytes,
    *,
    exclude_owner: str,
    expected_input_sha256: str,
    expected_excluded_count: int,
    expected_excluded_sha256: str,
    expected_module_layout_crc: str,
) -> tuple[bytes, dict[str, object]]:
    """Validate an exact aggregate Module.symvers and remove one module owner."""
    source_hash = sha256(data)
    if source_hash != expected_input_sha256.lower():
        raise ValueError("input Module.symvers SHA-256 differs from the pinned value")
    if not exclude_owner or any(char.isspace() for char in exclude_owner):
        raise ValueError("excluded module owner must be one nonempty field")

    kept: list[bytes] = []
    excluded: list[bytes] = []
    module_layout: list[tuple[bytes, bytes]] = []
    for number, line in enumerate(data.splitlines(keepends=True), 1):
        if not line.endswith(b"\n") or line.endswith(b"\r\n"):
            raise ValueError(f"Module.symvers row {number} has unsupported line ending")
        fields = line[:-1].split()
        if (len(fields) < 4
                or re.fullmatch(rb"0x[0-9a-fA-F]{8}", fields[0]) is None):
            raise ValueError(f"Module.symvers row {number} is malformed")
        if fields[1] == b"module_layout":
            module_layout.append((fields[0].lower(), fields[2]))
        if fields[2].decode("ascii", errors="strict") == exclude_owner:
            excluded.append(line)
        else:
            kept.append(line)

    excluded_data = b"".join(excluded)
    excluded_hash = sha256(excluded_data)
    if len(excluded) != expected_excluded_count:
        raise ValueError(
            f"excluded row count {len(excluded)} differs from pinned count "
            f"{expected_excluded_count}"
        )
    if excluded_hash != expected_excluded_sha256.lower():
        raise ValueError("excluded module export SHA-256 differs from the pinned value")
    expected_layout = expected_module_layout_crc.lower().encode("ascii")
    if (len(module_layout) != 1
            or module_layout[0] != (expected_layout, b"vmlinux")):
        raise ValueError("module_layout must have the exact pinned vmlinux CRC")

    output = b"".join(kept)
    if not output:
        raise ValueError("filtered dependency symvers is empty")
    return output, {
        "input_sha256": source_hash,
        "input_rows": len(kept) + len(excluded),
        "excluded_owner": exclude_owner,
        "excluded_rows": len(excluded),
        "excluded_sha256": excluded_hash,
        "dependency_rows": len(kept),
        "dependency_sha256": sha256(output),
        "module_layout_crc": expected_module_layout_crc.lower(),
    }


def write_new_file(path: Path, data: bytes) -> None:
    """Create a mode-0600 output without replacing an existing path.

    A failed write may leave a partial file. Preserve it as evidence instead
    of unlinking a path that could have been replaced by another process.
    """
    if path.is_symlink() or path.exists():
        raise FileExistsError(f"refusing to overwrite output: {path}")
    if not path.parent.is_dir():
        raise FileNotFoundError(f"output directory does not exist: {path.parent}")

    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as output:
        output.write(data)
        output.flush()
        os.fsync(output.fileno())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True,
                        help="exact matching aggregate Module.symvers")
    parser.add_argument("--output", type=Path, required=True,
                        help="new dependency symvers path; existing files are rejected")
    parser.add_argument("--exclude-owner", default=DEFAULT_OWNER)
    parser.add_argument("--expected-input-sha256", required=True)
    parser.add_argument("--expected-excluded-count", type=int, required=True)
    parser.add_argument("--expected-excluded-sha256", required=True)
    parser.add_argument("--expected-module-layout-crc", required=True)
    args = parser.parse_args()

    try:
        if args.input.is_symlink() or not args.input.is_file():
            raise ValueError("input must be a regular non-symlink file")
        data = args.input.read_bytes()
        filtered, receipt = filter_symvers(
            data,
            exclude_owner=args.exclude_owner,
            expected_input_sha256=args.expected_input_sha256,
            expected_excluded_count=args.expected_excluded_count,
            expected_excluded_sha256=args.expected_excluded_sha256,
            expected_module_layout_crc=args.expected_module_layout_crc,
        )
        write_new_file(args.output, filtered)
        print(json.dumps(receipt, sort_keys=True))
        return 0
    except (OSError, UnicodeError, ValueError) as error:
        print(f"camera modpost symvers: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
