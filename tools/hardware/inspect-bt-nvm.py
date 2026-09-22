#!/usr/bin/env python3
"""Offline parser for the recovered QTI Hastings type-2 NVM container.

This tool never opens a device and deliberately redacts tag 2 payload bytes.
It describes XML overrides but does not apply HAL mutations.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


HEADER_SIZE = 4
RECORD_HEADER_SIZE = 12


def parse_bab(data: bytes) -> dict:
    if len(data) < HEADER_SIZE or data[0] != 2:
        raise ValueError("expected type-2 container")
    declared = int.from_bytes(data[1:4], "little")
    if declared != len(data) - HEADER_SIZE:
        raise ValueError(f"container length {declared} != actual {len(data)-4}")
    tags, seen = [], set()
    pos = HEADER_SIZE
    while pos < len(data):
        if len(data) - pos < RECORD_HEADER_SIZE:
            raise ValueError(f"truncated record header at 0x{pos:x}")
        tag, length = struct.unpack_from("<HH", data, pos)
        end = pos + RECORD_HEADER_SIZE + length
        if end > len(data):
            raise ValueError(f"tag {tag} payload exceeds file at 0x{pos:x}")
        if tag in seen:
            raise ValueError(f"duplicate tag {tag}")
        seen.add(tag)
        item = {"id": tag, "length": length}
        # Never emit tag 2 bytes: this is the address-bearing record.
        if tag == 2:
            item["payload"] = "REDACTED"
        tags.append(item)
        pos = end
    if pos != len(data):
        raise ValueError("record walk did not end at file boundary")
    return {"container_type": 2, "declared_length": declared, "tags": tags}


def parse_xml(path: Path, nvm_tags: dict[int, int] | None = None, *, source_bytes: bytes | None = None) -> dict:
    root = ET.fromstring(source_bytes) if source_bytes is not None else ET.parse(path).getroot()
    if root.tag != "Tag":
        raise ValueError("XML root is not Tag")
    overrides, seen = [], set()
    for node in root:
        if not node.tag.startswith("Tag") or not node.tag[3:].isdigit():
            continue
        tag = int(node.tag[3:])
        if tag in seen:
            raise ValueError(f"duplicate XML tag {tag}")
        seen.add(tag)
        length_node = node.find("Length")
        declared_len = int(length_node.attrib["len"]) if length_node is not None else None
        offsets = [int(x.attrib["value"], 0) for x in node.findall("./Offset/*")]
        changes = [int(x.attrib["value"], 0) for x in node.findall("./Changes/*")]
        count_node = node.find("OffsetCount")
        change_count_node = node.find("ChangesCount")
        change_type_node = node.find("ChangeType")
        change_type = change_type_node.attrib.get("type") if change_type_node is not None else "default"
        if change_type == "entire" and not offsets:
            count = int(count_node.attrib["count"]) if count_node is not None else None
            offsets = list(range(count)) if count is not None else []
            changes_node = node.find("Changes")
            text = "".join(changes_node.itertext()) if changes_node is not None else ""
            changes = [int(x, 0) for x in re.findall(r"0x[0-9a-fA-F]+", text)]
        if count_node is not None and int(count_node.attrib["count"]) != len(offsets):
            raise ValueError(f"Tag{tag} offset count mismatch")
        if change_count_node is not None and int(change_count_node.attrib["count"]) != len(changes):
            raise ValueError(f"Tag{tag} change count mismatch")
        if len(offsets) != len(changes):
            raise ValueError(f"Tag{tag} offset/change count mismatch")
        if len(set(offsets)) != len(offsets):
            raise ValueError(f"Tag{tag} duplicate offset")
        if any(not 0 <= value <= 0xff for value in changes):
            raise ValueError(f"Tag{tag} change byte outside 0..255")
        if declared_len is not None and any(o < 0 or o >= declared_len for o in offsets):
            raise ValueError(f"Tag{tag} offset outside declared length")
        if nvm_tags is not None:
            if tag not in nvm_tags:
                raise ValueError(f"XML Tag{tag} absent from NVM")
            if declared_len != nvm_tags[tag]:
                raise ValueError(f"Tag{tag} XML length != NVM length")
        overrides.append({"id": tag, "length": declared_len, "changed_offsets": offsets,
                          "change_count": len(changes),
                          "change_type": change_type})
    expected = root.attrib.get("count")
    if expected is not None and int(expected) != len(overrides):
        raise ValueError("XML Tag count mismatch")
    return {"path": str(path), "overrides": overrides}


def inspect(nvm: Path, xml: Path) -> dict:
    raw = nvm.read_bytes()
    parsed = parse_bab(raw)
    nvm_tags = {item["id"]: item["length"] for item in parsed["tags"]}
    return {"nvm": {"path": str(nvm), "sha256": hashlib.sha256(raw).hexdigest(), **parsed},
            "xml": parse_xml(xml, nvm_tags),
            "hal_mutations_not_applied": {
                "tag17": "recognized-key branch writes GetMaxBaudrate to record byte13 (no IBS-bit clear)",
                "tag27": "recognized packed-key branch writes record byte13=3 and bytes17..20=0x08090101",
                "tag35": "recognized key checks record payload byte118 bit2; if set, sends TCS configuration ioctl",
            },
            "execution": "offline-only; no device, UART, EFS, or transmission access"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("nvm", type=Path)
    ap.add_argument("xml", type=Path)
    args = ap.parse_args()
    try:
        print(json.dumps(inspect(args.nvm, args.xml), indent=2, sort_keys=True))
    except (OSError, ValueError, ET.ParseError) as exc:
        print(f"inspect-bt-nvm: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
