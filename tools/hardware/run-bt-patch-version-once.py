#!/usr/bin/env python3
"""Reuse the complete accepted board supervisor for one PatchVerReq query.

Build the phone executable with aarch64-linux-gnu-gcc -static. This runner
never substitutes a host-architecture executable. All staging, kernel/trace
capture, USB/WLAN preflight and post-trial vote checks remain shared.
"""
import importlib.util
from pathlib import Path
import struct

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('board_supervisor', ROOT/'tools/hardware/run-bt-board-once.py')
board=importlib.util.module_from_spec(spec)
spec.loader.exec_module(board)
board.SOURCE=ROOT/'tools/hardware/bt-qca6490-patch-version-probe.c'
board.BINARY=ROOT/'builds/bt-patch-version-20260922/bt-qca6490-patch-version-probe'
board.DEST='/srv/s22/bt-patch-version-20260922/bt-qca6490-patch-version-probe'
board.NOTE='Opaque PatchVerReq identity query only; no firmware/NVM/baud/HCI or pairing acceptance.'

if __name__=='__main__':
    if board.BINARY.is_file():
        data=board.BINARY.read_bytes()
        if len(data)<64 or data[:6]!=b'\x7fELF\x02\x01' or struct.unpack_from('<H',data,18)[0]!=183:
            raise RuntimeError('phone binary must be little-endian AArch64 ELF64')
    board.main()
