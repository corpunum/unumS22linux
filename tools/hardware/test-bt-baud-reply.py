#!/usr/bin/env python3
"""Host-only negative regressions for bt-baud-reply.py."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import unittest


SCRIPT = Path(__file__).with_name("bt-baud-reply.py")
SPEC = importlib.util.spec_from_file_location("bt_baud_reply", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
bt_baud_reply = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bt_baud_reply)


def synthetic_cc(
    return_parameters: bytes = b"\x7f",
    credits: int = 1,
    packet_type: int = bt_baud_reply.H4_EVENT,
    event_code: int = bt_baud_reply.HCI_COMMAND_COMPLETE,
    opcode: int = bt_baud_reply.QCA_SET_BAUD_OPCODE,
    declared_length: int | None = None,
) -> bytes:
    """Build a synthetic H4 CC with the observed FC48 one-byte layout."""
    params = bytes((credits, opcode & 0xFF, opcode >> 8)) + return_parameters
    length = len(params) if declared_length is None else declared_length
    return bytes((
        packet_type,
        event_code,
        length,
    )) + params


class BaudReplyTests(unittest.TestCase):
    def test_observed_layout_is_not_normalized(self) -> None:
        # Synthetic fixture matches the observed schema, not a private capture.
        result = bt_baud_reply.classify_frame(synthetic_cc(b"\x01"))

        self.assertTrue(result["structurally_valid"])
        self.assertEqual(result["event"], "HCI_Command_Complete")
        self.assertEqual(result["opcode"], "0xfc48")
        self.assertEqual(result["command_credits"], 1)
        self.assertEqual(result["return_parameter"], "0x01")
        self.assertEqual(
            result["generic_hci_status_candidate"], "Unknown HCI Command"
        )
        self.assertFalse(result["status_semantics_confirmed"])
        self.assertEqual(result["classification"], "unresolved_vendor_return_parameter")
        self.assertEqual(result["decision"], "REFUSE")

    def test_zero_byte_is_not_accepted_as_success(self) -> None:
        result = bt_baud_reply.classify_frame(synthetic_cc(b"\x00"))

        self.assertEqual(result["generic_hci_status_candidate"], "Success")
        self.assertFalse(result["status_semantics_confirmed"])
        self.assertEqual(result["decision"], "REFUSE")

    def test_wrong_packet_type_is_rejected(self) -> None:
        result = bt_baud_reply.classify_frame(synthetic_cc(b"\x7f", packet_type=0x01))
        self.assertEqual(result["classification"], "invalid_frame")

    def test_truncated_event_header_is_rejected(self) -> None:
        result = bt_baud_reply.classify_frame(bytes.fromhex("04 0e"))
        self.assertEqual(result["classification"], "invalid_frame")

    def test_declared_length_mismatch_is_rejected(self) -> None:
        result = bt_baud_reply.classify_frame(
            synthetic_cc(b"\x7f", declared_length=5)
        )
        self.assertEqual(result["classification"], "invalid_frame")

    def test_non_command_complete_event_is_rejected(self) -> None:
        result = bt_baud_reply.classify_frame(synthetic_cc(b"\x7f", event_code=0xFF))
        self.assertEqual(result["classification"], "unexpected_event")

    def test_wrong_opcode_is_rejected(self) -> None:
        result = bt_baud_reply.classify_frame(synthetic_cc(b"\x7f", opcode=0xFC49))
        self.assertEqual(result["classification"], "unexpected_opcode")

    def test_unobserved_command_credit_or_return_layout_is_refused(self) -> None:
        extra_return = bt_baud_reply.classify_frame(
            synthetic_cc(b"\x7f\x00")
        )
        zero_credits = bt_baud_reply.classify_frame(synthetic_cc(b"\x7f", credits=0))

        self.assertEqual(extra_return["classification"], "unresolved_layout")
        self.assertEqual(zero_credits["classification"], "unresolved_layout")
        self.assertEqual(extra_return["decision"], "REFUSE")
        self.assertEqual(zero_credits["decision"], "REFUSE")

    def test_cli_reports_refusal_with_nonzero_exit(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                " ".join(f"{octet:02x}" for octet in synthetic_cc(b"\x01")),
            ],
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 3)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["decision"], "REFUSE")
        self.assertEqual(payload["classification"], "unresolved_vendor_return_parameter")


if __name__ == "__main__":
    unittest.main(verbosity=2)
