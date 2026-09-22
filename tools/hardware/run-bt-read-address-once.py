#!/usr/bin/env python3
"""Private Read_BD_ADDR query after verified RAM patch and board response."""
import importlib.util
from pathlib import Path
import struct
ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('previous',ROOT/'tools/hardware/run-bt-postpatch-board-once.py')
previous=importlib.util.module_from_spec(spec);spec.loader.exec_module(previous)
board=previous.board
board.SOURCE=ROOT/'tools/hardware/bt-qca6490-read-address.c'
board.BINARY=ROOT/'builds/bt-patch-20260922/bt-qca6490-read-address'
board.DEST='/srv/s22/bt-patch-20260922/bt-qca6490-read-address'
board.EXTRA_SOURCES += [Path(__file__)]
board.NOTE='Private standard Read_BD_ADDR after RAM patch/board. No NVM/reset/radio/HCI/EFS operation.'
if __name__=='__main__':
    data=board.BINARY.read_bytes()
    assert data[:6]==b'\x7fELF\x02\x01' and struct.unpack_from('<H',data,18)[0]==183
    board.main()
