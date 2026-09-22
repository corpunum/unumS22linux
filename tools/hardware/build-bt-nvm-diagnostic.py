#!/usr/bin/env python3
"""Build an offline, source-gated diagnostic NVM image; never transmits it."""
from __future__ import annotations
import argparse, hashlib, importlib.util, json, struct, sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NVM = ROOT / "rootfs/bt-audio-vendor-assets/vendor/firmware/hpnv21.bab"
XML = ROOT / "rootfs/bt-audio-vendor-assets/vendor/firmware/bt_nvm_loading.xml"
OUT = ROOT / "builds/bt-nvm-20260922"
NVM_SHA = "66bbfd26f80dc4c402bafe24d071faae7b3e126e938b04e8904a5b3394c4b992"
XML_SHA = "44a24fc5b1e935f121fde63212ba2d899e373c720b63c4a2536b53909d3593d5"
XML_TAGS = frozenset((36, 38, 83, 87, 204))

spec = importlib.util.spec_from_file_location("inspect_bt_nvm", Path(__file__).with_name("inspect-bt-nvm.py"))
parser = importlib.util.module_from_spec(spec)
assert spec.loader
spec.loader.exec_module(parser)


def records(raw: bytes):
    parsed = parser.parse_bab(raw)
    pos, result = parser.HEADER_SIZE, []
    for item in parsed["tags"]:
        tag, length = struct.unpack_from("<HH", raw, pos)
        payload_start = pos + parser.RECORD_HEADER_SIZE
        end = payload_start + length
        result.append((tag, length, pos, payload_start, end))
        pos = end
    return result


def transform(raw: bytes, xml_path: Path = XML):
    if hashlib.sha256(raw).hexdigest() != NVM_SHA:
        raise ValueError("refusing unexpected hpnv21.bab hash")
    xml_raw = xml_path.read_bytes()
    if hashlib.sha256(xml_raw).hexdigest() != XML_SHA:
        raise ValueError("refusing unexpected stock R0 XML hash")
    parsed = parser.parse_bab(raw)
    # Validate and consume one hash-checked snapshot, never re-read its path.
    xml = parser.parse_xml(xml_path, {x["id"]: x["length"] for x in parsed["tags"]}, source_bytes=xml_raw)
    xml_root = ET.fromstring(xml_raw)
    seen_xml = {item["id"] for item in xml["overrides"]}
    if seen_xml != XML_TAGS:
        raise ValueError(f"stock XML override set changed: {sorted(seen_xml)}")
    out = bytearray(raw)
    changes = []
    by_tag = {tag: (length, start, payload, end) for tag, length, start, payload, end in records(raw)}

    def setbyte(tag, offset, value, reason):
        length, _, payload, _ = by_tag[tag]
        if not 0 <= offset < length or not 0 <= value <= 255:
            raise ValueError("change outside record bounds")
        at = payload + offset
        old = out[at]
        if old != value:
            out[at] = value
            changes.append({"tag": tag, "offset": offset, "old": old, "new": value, "reason": reason})

    # ReadTlvInfo/UpdateNewNvmFormat normal packed-key branch, plus XML R0.
    for offset in range(6):
        setbyte(2, offset, 0, "HAL GetLocalAddress diagnostic fixture: observed all-zero address")
    setbyte(17, 1, 0x0e, "HAL recognized-key baud field: verified 3M enum")
    setbyte(27, 1, 3, "HAL UpdateNewNvmFormat")
    for offset, value in zip((5, 6, 7, 8), (0x01, 0x01, 0x09, 0x08)):
        setbyte(27, offset, value, "HAL UpdateNewNvmFormat")
    for item in xml["overrides"]:
        tag = item["id"]
        if tag in (2, 17, 27):
            raise ValueError("XML overlaps HAL normal-key mutation")
        node = xml_root.find(f"Tag{tag}")
        values = [int(x.attrib["value"], 0) for x in node.findall("./Changes/*")]
        if item["change_type"] == "entire" and not values:
            import re
            values = [int(x, 0) for x in re.findall(r"0x[0-9a-fA-F]+", "".join(node.find("Changes").itertext()))]
        if len(values) != item["change_count"]:
            raise ValueError(f"XML Tag{tag} value count changed")
        for offset, value in zip(item["changed_offsets"], values):
            setbyte(tag, offset, value, "stock bt_nvm_loading.xml R0 override")
    transformed = bytes(out)
    allowed = {(2, i) for i in range(6)} | {(17, 1), (27, 1)} | {(27, i) for i in (5, 6, 7, 8)}
    allowed |= {(item['id'], offset) for item in xml['overrides'] for offset in item['changed_offsets']}
    if any((item['tag'], item['offset']) not in allowed for item in changes):
        raise ValueError('change outside reviewed tag/offset closure')
    return transformed, changes


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", type=Path, default=OUT)
    args = ap.parse_args()
    raw = NVM.read_bytes()
    transformed, changes = transform(raw)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    image = args.out_dir / "hpnv21-normal-key-diagnostic.bab"
    meta = args.out_dir / "hpnv21-normal-key-diagnostic.json"
    image.write_bytes(transformed)
    meta.write_text(json.dumps({"original_sha256": hashlib.sha256(raw).hexdigest(),
        "transformed_sha256": hashlib.sha256(transformed).hexdigest(),
        "changes": changes, "key": "0x400c021000130201",
        "allowlist": {
            "tag2_payload_offsets": [0, 1, 2, 3, 4, 5],
            "tag17_payload_offsets": [1],
            "tag27_payload_offsets": [1, 5, 6, 7, 8],
            "xml_tags": sorted(XML_TAGS),
        },
        "source_provenance": {
            "normal_key": "0x400c021000130201",
            "tag17": "private HAL ReadTlvInfo/UpdateNewNvmFormat: record byte 13 (payload[1])",
            "tag27": "private HAL UpdateNewNvmFormat: record byte 13 and bytes 17..20",
            "tag2": "private HAL GetLocalAddress path; diagnostic preserves observed all-zero address",
            "tag35": "unchanged; private HAL TCS attempt is outside this offline transform",
        },
        "execution": "offline build only; not a phone command"}, indent=2) + "\n")
    print(json.dumps({"image": str(image), "metadata": str(meta), "transformed_sha256": hashlib.sha256(transformed).hexdigest(), "change_count": len(changes)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
