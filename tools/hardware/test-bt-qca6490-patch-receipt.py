#!/usr/bin/env python3
"""Host-only fixture checks for the private QCA PatchVer response."""

import struct

RECEIPT = bytes.fromhex(
    "04 0e 12 01 00 fc 00 19 0c 13 00 00 00 e6 38 01 02 "
    "10 02 0c 40"
)


def packed_key(frame: bytes) -> int:
    if len(frame) < 3 or frame[0:2] != b"\x04\x0e":
        raise ValueError("not a Command Complete event")
    if frame[2] != len(frame) - 3:
        raise ValueError("declared event length mismatch")
    if len(frame) < 21 or frame[6] != 0:
        raise ValueError("short or failed PatchVer response")
    if frame[4:6] != b"\x00\xfc" or frame[7] != 0x19:
        raise ValueError("wrong vendor command")
    low16 = struct.unpack_from("<H", frame, 15)[0]
    middle = struct.unpack_from("<I", frame, 9)[0]
    high = struct.unpack_from("<I", frame, 17)[0]
    return low16 | (middle << 16) | (high << 32)


def main() -> None:
    assert len(RECEIPT) == 21
    assert packed_key(RECEIPT) == 0x400C021000130201
    for bad in (RECEIPT[:-1], bytes([*RECEIPT[:2], RECEIPT[2] + 1, *RECEIPT[3:]]),
                bytes([*RECEIPT[:6], 1, *RECEIPT[7:]]),
                bytes([*RECEIPT[:7], 0x06, *RECEIPT[8:]])):
        try:
            packed_key(bad)
        except ValueError:
            pass
        else:
            raise AssertionError("malformed PatchVer fixture was accepted")
    print("bt-qca6490 PatchVer receipt: PASS (host-only)")


if __name__ == "__main__":
    main()
