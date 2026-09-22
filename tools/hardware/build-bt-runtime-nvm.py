#!/usr/bin/env python3
"""Build a private normal-profile NVM candidate with an explicit Linux address.

This tool never generates an address, reads a device, or transmits an image.
The address JSON is private input and must explicitly classify its source as
``linux-generated``; it is never treated as factory/OTP identity.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import stat
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "builds/bt-runtime-nvm-20260922"

spec = importlib.util.spec_from_file_location("build_nvm", Path(__file__).with_name("build-bt-nvm-diagnostic.py"))
base = importlib.util.module_from_spec(spec); assert spec.loader; spec.loader.exec_module(base)


def parse_address(doc: object) -> tuple[bytes, str]:
    if not isinstance(doc, dict) or doc.get("source") != "linux-generated":
        raise ValueError("address source must be explicitly linux-generated")
    text = doc.get("display_address")
    if not isinstance(text, str) or not re.fullmatch(r"[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5}", text):
        raise ValueError("display_address must be six colon-separated bytes")
    parts = text.split(":")
    if len(parts) != 6:
        raise ValueError("display_address must contain six bytes")
    try:
        display = bytes(int(part, 16) for part in parts)
    except ValueError as exc:
        raise ValueError("display_address contains a non-hex byte") from exc
    if len(display) != 6 or display[:2] != b"\x22\x22":
        raise ValueError("HAL-generated address must have 22:22 prefix")
    return display[::-1], "linux-generated"


def check_private_input(path: Path) -> None:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
        raise ValueError("address file must be a regular file owned by the current user")
    if stat.S_IMODE(info.st_mode) not in (0o400, 0o600):
        raise ValueError("address file mode must be 0400 or 0600")


def write_new(path: Path, data: bytes, mode: int = 0o600) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        raise


def c_header(data: bytes) -> bytes:
    values = ", ".join(f"0x{value:02x}" for value in data)
    return ("#pragma once\n#include <stdint.h>\n\n"
            f"static const uint8_t s22_nvm_payload[] = {{{values}}};\n"
            f"static const uint32_t s22_nvm_payload_len = {len(data)}u;\n").encode()


def replace_tag2(raw: bytes, controller_address: bytes) -> tuple[bytes, int]:
    if len(controller_address) != 6:
        raise ValueError("controller address must contain six bytes")
    parsed = base.parser.parse_bab(raw)
    pos = 4
    out = bytearray(raw)
    changes = 0
    for item in parsed["tags"]:
        tag = int.from_bytes(raw[pos:pos + 2], "little")
        length = int.from_bytes(raw[pos + 2:pos + 4], "little")
        if tag == 2:
            if length != 6:
                raise ValueError("tag 2 must be six bytes")
            for offset, value in enumerate(controller_address):
                at = pos + 12 + offset
                if out[at] != value:
                    out[at] = value; changes += 1
            return bytes(out), changes
        pos += 12 + length
    raise ValueError("NVM has no tag 2")


def build(raw: bytes, address_doc: object) -> tuple[bytes, dict]:
    transformed, base_changes = base.transform(raw)
    controller, source = parse_address(address_doc)
    transformed, address_changes = replace_tag2(transformed, controller)
    metadata = {"original_sha256": hashlib.sha256(raw).hexdigest(),
                "transformed_sha256": hashlib.sha256(transformed).hexdigest(),
                "base_transform_change_count": len(base_changes),
                "tag2_change_count": address_changes,
                "address_sha256": hashlib.sha256(controller).hexdigest(),
                "address_source": source,
                "address_classification": "not-factory-no-EFS",
                "key": "0x400c021000130201",
                "execution": "offline build only; no device, EFS, UART, or transmission"}
    return transformed, metadata


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--address-file", type=Path, required=True)
    ap.add_argument("--out-dir", type=Path, default=OUT)
    args = ap.parse_args()
    check_private_input(args.address_file)
    address_doc = json.loads(args.address_file.read_text())
    raw = base.NVM.read_bytes()
    transformed, metadata = build(raw, address_doc)
    args.out_dir.mkdir(parents=True, exist_ok=False, mode=0o700)
    os.chmod(args.out_dir, 0o700)
    image = args.out_dir / "hpnv21-normal-runtime-address.bab"
    header = args.out_dir / "s22_nvm_payload.h"
    receipt = args.out_dir / "hpnv21-normal-runtime-address.json"
    header_bytes = c_header(transformed)
    metadata["header_sha256"] = hashlib.sha256(header_bytes).hexdigest()
    write_new(image, transformed)
    write_new(header, header_bytes)
    write_new(receipt, (json.dumps(metadata, indent=2) + "\n").encode())
    print(json.dumps({"image": str(image), "metadata": str(receipt),
                      "header": str(header),
                      "transformed_sha256": metadata["transformed_sha256"],
                      "address_source": metadata["address_source"],
                      "address_classification": metadata["address_classification"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
