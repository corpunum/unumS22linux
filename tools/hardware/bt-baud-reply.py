#!/usr/bin/env python3
"""Fail-closed parser for the captured QCA 0xfc48 baud reply.

This offline tool parses one H4 Command Complete frame. It does not claim that
the vendor command's first return parameter is a standard HCI status, because
the exact QCA6490 command-specific return schema is not established here.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from typing import Any


H4_EVENT = 0x04
HCI_COMMAND_COMPLETE = 0x0E
QCA_SET_BAUD_OPCODE = 0xFC48
OBSERVED_NUM_COMMAND_PACKETS = 1
OBSERVED_RETURN_PARAMETER_COUNT = 1

GENERIC_HCI_STATUS_CANDIDATES = {
    0x00: "Success",
    0x01: "Unknown HCI Command",
}


def _refusal(classification: str, reason: str, **fields: Any) -> dict[str, Any]:
    result: dict[str, Any] = {
        "classification": classification,
        "structurally_valid": False,
        "decision": "REFUSE",
        "reason": reason,
    }
    result.update(fields)
    return result


def classify_frame(frame: bytes) -> dict[str, Any]:
    """Parse one response frame and refuse any unproven FC48 interpretation."""
    if len(frame) < 1:
        return _refusal("invalid_frame", "empty H4 frame")
    if frame[0] != H4_EVENT:
        return _refusal("invalid_frame", "packet is not an H4 event")
    if len(frame) < 3:
        return _refusal("invalid_frame", "truncated H4 event header")
    if frame[1] != HCI_COMMAND_COMPLETE:
        return _refusal(
            "unexpected_event",
            "event is not HCI Command Complete",
            event_code=f"0x{frame[1]:02x}",
        )

    parameter_length = frame[2]
    expected_length = 3 + parameter_length
    if len(frame) != expected_length:
        return _refusal(
            "invalid_frame",
            "H4 parameter length does not match the supplied frame",
            declared_length=expected_length,
            received_length=len(frame),
        )
    if parameter_length < 3:
        return _refusal(
            "invalid_frame",
            "Command Complete lacks its command-credit and opcode fields",
            parameter_length=parameter_length,
        )

    command_credits = frame[3]
    opcode = frame[4] | (frame[5] << 8)
    if opcode != QCA_SET_BAUD_OPCODE:
        return _refusal(
            "unexpected_opcode",
            "Command Complete opcode does not match QCA set-baud opcode 0xfc48",
            opcode=f"0x{opcode:04x}",
        )

    return_parameter_count = parameter_length - 3
    if (
        command_credits != OBSERVED_NUM_COMMAND_PACKETS
        or return_parameter_count != OBSERVED_RETURN_PARAMETER_COUNT
    ):
        return _refusal(
            "unresolved_layout",
            "valid Command Complete differs from the single-byte observed layout",
            structurally_valid=True,
            event="HCI_Command_Complete",
            opcode=f"0x{opcode:04x}",
            command_credits=command_credits,
            return_parameter_count=return_parameter_count,
        )

    return_byte = frame[6]
    return {
        "classification": "unresolved_vendor_return_parameter",
        "structurally_valid": True,
        "decision": "REFUSE",
        "event": "HCI_Command_Complete",
        "opcode": f"0x{opcode:04x}",
        "command_credits": command_credits,
        "return_parameter_count": return_parameter_count,
        "return_parameter": f"0x{return_byte:02x}",
        "generic_hci_status_candidate": GENERIC_HCI_STATUS_CANDIDATES.get(return_byte),
        "status_semantics_confirmed": False,
        "reason": (
            "The byte is the first Command Complete return parameter. The pinned "
            "HCI core interprets it as status, but the FC48 vendor return schema "
            "is not established; do not accept or normalize it."
        ),
    }


def parse_hex_frame(value: str) -> bytes:
    """Parse whitespace- or comma-separated hexadecimal octets."""
    tokens = [token for token in re.split(r"[\s,]+", value.strip()) if token]
    if not tokens:
        raise ValueError("provide one H4 frame as hexadecimal octets")

    octets = bytearray()
    for token in tokens:
        digits = token[2:] if token.lower().startswith("0x") else token
        if not re.fullmatch(r"[0-9a-fA-F]{1,2}", digits):
            raise ValueError(f"invalid hexadecimal octet: {token!r}")
        octets.append(int(digits, 16))
    return bytes(octets)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "frame",
        nargs="?",
        help="one H4 event as whitespace-separated hex octets; reads stdin if omitted",
    )
    args = parser.parse_args(argv)

    source = args.frame if args.frame is not None else sys.stdin.read()
    try:
        frame = parse_hex_frame(source)
    except ValueError as error:
        result = _refusal("invalid_input", str(error))
        print(json.dumps(result, sort_keys=True))
        return 2

    result = classify_frame(frame)
    print(json.dumps(result, sort_keys=True))
    return 3 if result["structurally_valid"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
