#!/usr/bin/env python3
"""One latency-controlled baud transition; never patch/NVM/reset/HCI."""
import importlib.util
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('board',ROOT/'tools/hardware/run-bt-board-once.py')
board=importlib.util.module_from_spec(spec);spec.loader.exec_module(board)
board.SOURCE=ROOT/'tools/hardware/bt-qca6490-baud-precomputed.c'
board.BINARY=ROOT/'builds/bt-baud-20260922/bt-qca6490-baud-precomputed'
board.DEST='/srv/s22/bt-baud-20260922/bt-qca6490-baud-precomputed'
board.EXTRA_SOURCES=[ROOT/'tools/hardware/bt-qca6490-baud-probe.c',
                     ROOT/'tools/hardware/bt-qca6490-patch-version-probe.c',Path(__file__)]
board.NOTE='Precomputed termios, no logging in baud-switch interval; no patch/NVM/reset/HCI.'
if __name__=='__main__':board.main()
