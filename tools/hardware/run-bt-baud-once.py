#!/usr/bin/env python3
"""Guarded one-shot native Bluetooth 3M transport diagnostic, no firmware."""
import importlib.util
from pathlib import Path
import struct

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('board',ROOT/'tools/hardware/run-bt-board-once.py')
board=importlib.util.module_from_spec(spec);spec.loader.exec_module(board)
board.SOURCE=ROOT/'tools/hardware/bt-qca6490-baud-probe.c'
board.BINARY=ROOT/'builds/bt-baud-20260922/bt-qca6490-baud-probe'
board.DEST='/srv/s22/bt-baud-20260922/bt-qca6490-baud-probe'
board.NOTE='Baud transport and exact identity only; no firmware/NVM/HCI/pairing acceptance.'
board.EXTRA_SOURCES=[ROOT/'tools/hardware/bt-qca6490-patch-version-probe.c',Path(__file__)]
if __name__=='__main__':
    if board.BINARY.exists():
        data=board.BINARY.read_bytes()
        assert data[:6]==b'\x7fELF\x02\x01' and struct.unpack_from('<H',data,18)[0]==183
    board.main()
