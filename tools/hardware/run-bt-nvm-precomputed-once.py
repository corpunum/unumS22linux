#!/usr/bin/env python3
"""One volatile NVM diagnostic with the measured precomputed baud path."""
import importlib.util
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('nvm',ROOT/'tools/hardware/run-bt-nvm-capture-once.py')
nvm=importlib.util.module_from_spec(spec);spec.loader.exec_module(nvm)
board=nvm.board
board.SOURCE=ROOT/'tools/hardware/bt-qca6490-nvm-precomputed.c'
board.BINARY=ROOT/'builds/bt-nvm-20260922/bt-qca6490-nvm-precomputed'
board.DEST='/srv/s22/bt-nvm-20260922/bt-qca6490-nvm-precomputed'
board.EXTRA_SOURCES += [ROOT/'tools/hardware/bt-qca6490-nvm-capture.c',Path(__file__)]
board.NOTE='Measured precomputed baud path, volatilepatch+board+NVM thenpoweroff; diagnosticzeroaddress, no reset/HCI/RF.'
if __name__=='__main__':board.main()
