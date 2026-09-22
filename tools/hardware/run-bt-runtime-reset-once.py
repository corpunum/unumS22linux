#!/usr/bin/env python3
"""One bounded normal NVM/reset/readback trial, then power off."""
import importlib.util
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('nvm',ROOT/'tools/hardware/run-bt-nvm-precomputed-once.py')
nvm=importlib.util.module_from_spec(spec);spec.loader.exec_module(nvm)
board=nvm.board
board.SOURCE=ROOT/'tools/hardware/bt-qca6490-runtime-reset.c'
board.BINARY=ROOT/'builds/bt-runtime-nvm-20260922/bt-qca6490-runtime-reset'
board.DEST='/srv/s22/bt-runtime-20260922/bt-qca6490-runtime-reset'
board.EXTRA_SOURCES += [ROOT/'builds/bt-runtime-nvm-20260922/s22_nvm_payload.h',Path(__file__)]
board.NOTE='Linux-generated address configuration; volatile patch/NVM/reset/readback then poweroff. No factory identity, discovery, advertising, pairing, or registered HCI claim.'
if __name__=='__main__':board.main()
