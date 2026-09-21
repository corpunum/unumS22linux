#!/usr/bin/env python3
"""One verified RAM patch followed by the HAL's board-ID query; no NVM/HCI."""
import importlib.util
from pathlib import Path
import struct
ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('patch',ROOT/'tools/hardware/run-bt-patch-capture-once.py')
patch=importlib.util.module_from_spec(spec);spec.loader.exec_module(patch)
board=patch.board
board.SOURCE=ROOT/'tools/hardware/bt-qca6490-postpatch-board.c'
board.BINARY=ROOT/'builds/bt-patch-20260922/bt-qca6490-postpatch-board'
board.DEST='/srv/s22/bt-patch-20260922/bt-qca6490-postpatch-board'
board.EXTRA_SOURCES += [ROOT/'tools/hardware/bt-qca6490-patch-capture.c',Path(__file__)]
board.NOTE='Full RAM patch ACK then board query, matching HAL sequence. No NVM/reset/HCI acceptance.'
if __name__=='__main__':
    data=board.BINARY.read_bytes()
    assert data[:6]==b'\x7fELF\x02\x01' and struct.unpack_from('<H',data,18)[0]==183
    board.main()
