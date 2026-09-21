#!/usr/bin/env python3
"""One exact private RAM patch transfer and final-event capture; no NVM/HCI."""
import importlib.util
from pathlib import Path
import struct
ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('board',ROOT/'tools/hardware/run-bt-board-once.py')
board=importlib.util.module_from_spec(spec);spec.loader.exec_module(board)
board.SOURCE=ROOT/'tools/hardware/bt-qca6490-patch-capture.c'
board.BINARY=ROOT/'builds/bt-patch-20260922/bt-qca6490-patch-capture'
board.DEST='/srv/s22/bt-patch-20260922/bt-qca6490-patch-capture'
board.EXTRA_SOURCES=[ROOT/'tools/hardware/bt-qca6490-patch-version-probe.c',
                     ROOT/'tools/hardware/bt-qca6490-baud-probe.c',Path(__file__),
                     ROOT/'builds/bt-patch-20260922/qca-patch-private.h']
board.NOTE='Stock RAM patch806segments; finalresponse captured only. No NVM/reset/HCI acceptance.'
if __name__=='__main__':
    data=board.BINARY.read_bytes()
    assert data[:6]==b'\x7fELF\x02\x01' and struct.unpack_from('<H',data,18)[0]==183
    board.main()
