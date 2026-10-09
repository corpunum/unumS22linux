#!/usr/bin/env python3
"""Read-only native input, button-capability, power, and camera inventory.

Inventory is capability/configuration evidence only. The bounded event mode
delegates to the passive target-button collector and never injects or grabs.
Neither mode changes display power, suspends, reboots, or alters USB.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import stat
import struct

KEY_POWER = 116
KEY_VOLUMEUP = 115
KEY_VOLUMEDOWN = 114
EV_KEY = 1
KEY_MAX = 0x2FF
EV_MAX = 0x1F
BITMAP_WORD_BITS = struct.calcsize("@L") * 8
BUTTON_CODES = {
    "KEY_POWER": KEY_POWER,
    "KEY_VOLUMEUP": KEY_VOLUMEUP,
    "KEY_VOLUMEDOWN": KEY_VOLUMEDOWN,
}
BITMAP_FORMAT = "linux-input-input_print_bitmap"


class BitmapFormatError(ValueError):
    """A sysfs input bitmap is missing, malformed, or out of range."""


def parse_sysfs_bitmap(raw: str, *, word_bits: int = BITMAP_WORD_BITS,
                       max_code: int = KEY_MAX) -> set[int]:
    """Parse Linux input ``input_print_bitmap`` text.

    The pinned driver prints native-unsigned-long words, most-significant word
    first, separated by spaces. Within each word, bit zero is the least
    significant bit. Leading zero words are omitted; lower/interior words are
    retained, including zero words. ``word_bits`` must match the running
    kernel's ``BITS_PER_LONG``.
    """
    if word_bits not in (32, 64):
        raise BitmapFormatError("word_bits must be 32 or 64")
    if not isinstance(raw, str):
        raise BitmapFormatError("bitmap must be text")
    if max_code < 0:
        raise BitmapFormatError("max_code must be non-negative")
    value = raw.strip()
    if not value or "\n" in value or "\r" in value:
        raise BitmapFormatError("bitmap must contain one non-empty line")
    words = value.split(" ")
    if any(not word for word in words):
        raise BitmapFormatError("bitmap words must be separated by single spaces")
    max_words = (max_code + 1 + word_bits - 1) // word_bits
    if len(words) > max_words:
        raise BitmapFormatError("bitmap contains too many words")
    if len(words) > 1 and words[0] == "0":
        raise BitmapFormatError("bitmap has a non-canonical leading zero word")

    parsed: list[int] = []
    max_hex_digits = word_bits // 4
    for word in words:
        if (len(word) > max_hex_digits or
                any(char not in "0123456789abcdefABCDEF" for char in word)):
            raise BitmapFormatError("bitmap word is not a valid native-width hex value")
        parsed.append(int(word, 16))

    bits: set[int] = set()
    for word_index, word in enumerate(reversed(parsed)):
        while word:
            low_bit = word & -word
            offset = low_bit.bit_length() - 1
            code = word_index * word_bits + offset
            if code > max_code:
                raise BitmapFormatError("bitmap sets a bit beyond the advertised maximum")
            bits.add(code)
            word ^= low_bit
    return bits


def capability_record(path: Path, *, max_code: int) -> dict[str, object]:
    try:
        raw = path.read_text(encoding="ascii").strip()
    except FileNotFoundError:
        return {"status": "missing", "path": str(path), "raw": None, "codes": []}
    except (OSError, UnicodeError) as error:
        return {"status": "unreadable", "path": str(path), "raw": None,
                "error": type(error).__name__, "codes": []}
    try:
        codes = parse_sysfs_bitmap(raw, max_code=max_code)
    except BitmapFormatError as error:
        return {"status": "invalid", "path": str(path), "raw": raw,
                "error": str(error), "codes": []}
    return {"status": "valid", "path": str(path), "raw": raw,
            "codes": sorted(codes)}


def node_identity(event: Path, sysfs_dev: str | None) -> dict[str, object]:
    """Check that an event node is the character device named by sysfs."""
    if not sysfs_dev:
        return {"status": "sysfs_dev_missing", "expected": sysfs_dev}
    match = re.fullmatch(r"([0-9]+):([0-9]+)", sysfs_dev)
    if match is None:
        return {"status": "sysfs_dev_invalid", "expected": sysfs_dev}
    expected = (int(match.group(1)), int(match.group(2)))
    try:
        node_stat = event.stat()
    except OSError as error:
        return {"status": "node_unavailable", "expected": sysfs_dev,
                "error": type(error).__name__}
    if not stat.S_ISCHR(node_stat.st_mode):
        return {"status": "not_character_device", "expected": sysfs_dev}
    actual = (os.major(node_stat.st_rdev), os.minor(node_stat.st_rdev))
    return {
        "status": "matched" if actual == expected else "device_number_mismatch",
        "expected": sysfs_dev,
        "actual": f"{actual[0]}:{actual[1]}",
    }


def read(path: Path) -> str | None:
    try:
        return path.read_text().strip()
    except OSError:
        return None


def inputs(sys_root: Path = Path("/sys"),
           dev_root: Path = Path("/dev")) -> list[dict[str, object]]:
    result = []
    for sysdev in sorted((sys_root / "class/input").glob("event*")):
        name = read(sysdev / "device/name")
        event = dev_root / "input" / sysdev.name
        event_caps = capability_record(sysdev / "device/capabilities/ev", max_code=EV_MAX)
        key_caps = capability_record(sysdev / "device/capabilities/key", max_code=KEY_MAX)
        sysfs_dev = read(sysdev / "dev")
        identity = node_identity(event, sysfs_dev)
        event_codes = set(event_caps["codes"]) if event_caps["status"] == "valid" else set()
        key_codes = set(key_caps["codes"]) if key_caps["status"] == "valid" else set()
        caps_complete = event_caps["status"] == "valid" and key_caps["status"] == "valid"
        key_capable: bool | None = None
        if caps_complete:
            key_capable = EV_KEY in event_codes and bool(key_codes)
        button_capabilities: dict[str, bool | None] = {}
        for button, code in BUTTON_CODES.items():
            button_capabilities[button] = (code in key_codes
                                           if key_caps["status"] == "valid" else None)
        present = event.exists()
        button_candidate = bool(
            present and identity["status"] == "matched" and key_capable is True and
            any(value is True for value in button_capabilities.values()))
        result.append({
            "event": str(event),
            "name": name,
            "physical_origin": "unknown_from_name_or_capabilities",
            "present": present,
            "sysfs_dev": sysfs_dev,
            "node_identity": identity,
            "capabilities": {
                "format": BITMAP_FORMAT,
                "word_bits": BITMAP_WORD_BITS,
                "word_order": "most-significant-word-first",
                "bit_order": "bit 0 is the least-significant bit of the final word",
                "ev": event_caps,
                "key": key_caps,
            },
            "button_capabilities": button_capabilities,
            "key_capable": key_capable,
            "button_observation_candidate": button_candidate,
        })
    return result


def power(sys_root: Path = Path("/sys")) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for base in sorted((sys_root / "class/power_supply").glob("*")):
        if not base.is_dir():
            continue
        values = {}
        for key in ("capacity", "status", "health", "online", "voltage_now",
                    "current_now", "temp", "type", "scope"):
            value = read(base / key)
            if value is not None:
                values[key] = value
        result[base.name] = values
    return result


def thermal(sys_root: Path = Path("/sys")) -> dict[str, dict[str, str]]:
    result = {}
    for base in sorted((sys_root / "class/thermal").glob("thermal_zone*")):
        typ = read(base / "type")
        temp = read(base / "temp")
        if typ is not None or temp is not None:
            result[base.name] = {"type": typ or "", "temp_millidegrees": temp or ""}
    return result


def cameras(sys_root: Path = Path("/sys"),
            dev_root: Path = Path("/dev")) -> list[dict[str, str | bool]]:
    result = []
    for dev in sorted(dev_root.glob("video*")):
        name = read(sys_root / "class/video4linux" / dev.name / "name")
        result.append({"device": str(dev), "name": name or "", "present": True})
    return result


def observe(seconds: float, sys_root: Path = Path("/sys"),
            dev_root: Path = Path("/dev"), *, max_events: int = 4096) -> dict[str, object]:
    """Collect bounded events only from nodes with matching capability bits."""
    if not math.isfinite(seconds) or seconds < 0 or seconds > 300:
        raise ValueError("seconds must be between 0 and 300")
    helper_path = Path(__file__).with_name("button-event-evidence.py")
    spec = importlib.util.spec_from_file_location("button_event_evidence", helper_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load event evidence collector at {helper_path}")
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    return helper.collect(seconds, sys_root=sys_root, dev_root=dev_root,
                         max_events=max_events)


def main(argv: list[str] | None = None, *, sys_root: Path = Path("/sys"),
         dev_root: Path = Path("/dev")) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", type=float, metavar="SECONDS",
                    help="passively collect target-button events for 0 to 300 seconds")
    ap.add_argument("--camera", action="store_true", help="include read-only V4L2 node inventory")
    args = ap.parse_args(argv)
    result: dict[str, object] = {
        "inputs": inputs(sys_root, dev_root),
        "power": power(sys_root),
        "thermal": thermal(sys_root),
    }
    if args.camera:
        result["cameras"] = cameras(sys_root, dev_root)
    if args.events is not None:
        if not math.isfinite(args.events) or args.events < 0 or args.events > 300:
            ap.error("--events must be between 0 and 300 seconds")
        result["button_event_evidence"] = observe(args.events, sys_root, dev_root)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
