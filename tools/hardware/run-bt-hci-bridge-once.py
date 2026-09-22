#!/usr/bin/env python3
"""One preinitialized kernel-H4/IBS handoff, bounded to twenty seconds."""
import importlib.util
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('runtime',ROOT/'tools/hardware/run-bt-runtime-reset-once.py')
runtime=importlib.util.module_from_spec(spec);spec.loader.exec_module(runtime)
board=runtime.board
board.SOURCE=ROOT/'tools/hardware/bt-qca6490-hci-bridge-probe.c'
board.BINARY=ROOT/'builds/bt-runtime-nvm-20260922/bt-qca6490-hci-bridge-probe'
board.DEST='/srv/s22/bt-runtime-20260922/bt-qca6490-hci-bridge-probe'
board.PHONE_TIMEOUT=35
board.HOST_TIMEOUT=45
board.EXTRA_SOURCES += [ROOT/'tools/hardware/bt-h4-ibs-bridge.c',
                       ROOT/'tools/hardware/bt-qca6490-runtime-reset.c',Path(__file__)]
board.NOTE='Real QCA6490 initialization, 20s PTY-H4/IBS bridge to existing Linux HCI; detach then poweroff. No deliberate discovery/advertising/pairing; inspect captured command opcodes and init outcome.'
if __name__=='__main__':board.main()
