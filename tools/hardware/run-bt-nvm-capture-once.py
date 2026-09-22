#!/usr/bin/env python3
"""One non-transmitting NVM transport trial; power off before reset/HCI."""
import importlib.util
from pathlib import Path
import struct
ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('patch',ROOT/'tools/hardware/run-bt-patch-capture-once.py')
patch=importlib.util.module_from_spec(spec);spec.loader.exec_module(patch)
board=patch.board
board.SOURCE=ROOT/'tools/hardware/bt-qca6490-nvm-capture.c'
board.BINARY=ROOT/'builds/bt-nvm-20260922/bt-qca6490-nvm-capture'
board.DEST='/srv/s22/bt-nvm-20260922/bt-qca6490-nvm-capture'
board.EXTRA_SOURCES += [ROOT/'tools/hardware/bt-qca6490-patch-capture.c',
                       ROOT/'builds/bt-nvm-20260922/qca-nvm-private.h',Path(__file__)]
board.NOTE='Volatile patch+board+29 NVM segments, then poweroff; all-zero diagnostic address, no reset/HCI/RF acceptance.'
if __name__=='__main__':
    data=board.BINARY.read_bytes()
    assert data[:6]==b'\x7fELF\x02\x01' and struct.unpack_from('<H',data,18)[0]==183
    board.main()
