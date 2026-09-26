#!/usr/bin/env python3
"""One preinitialized kernel-H4/IBS handoff, bounded to twenty seconds."""
import importlib.util
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('runtime',ROOT/'tools/hardware/run-bt-runtime-reset-once.py')
runtime=importlib.util.module_from_spec(spec);spec.loader.exec_module(runtime)
board=runtime.board
board.SOURCE=ROOT/'tools/hardware/bt-qca6490-hci-bridge-probe.c'
board.BINARY=ROOT/'builds/bt-next-20260926/bt-qca6490-hci-bridge-probe'
board.DEST='/srv/s22/bt-next-20260926/bt-qca6490-hci-bridge-probe'
board.PHONE_TIMEOUT=35
board.HOST_TIMEOUT=45
board.EXTRA_SOURCES += [ROOT/'tools/hardware/bt-h4-ibs-bridge.c',
                       ROOT/'tools/hardware/bt-qca6490-runtime-reset.c',Path(__file__)]
board.NOTE='Verified QCA initialization order followed by a bounded PTY-H4/IBS handoff; HCIUARTGETDEVICE is the registration receipt, then detach before poweroff. No discovery/advertising/pairing. A separate controller-attachment trial still requires exact RECOVERY identity/readback, independent procedure review, and explicit authorization.'
LIVE_TRIAL_GATE = (
    'Controller-registration trial remains disabled: the prior authorization '
    'covered one raw-HCI socket create/close check only, and that check has '
    'already passed. Before a new attachment trial, the coordinator must '
    'verify the exact RECOVERY candidate boot/build identity and full readback, '
    'independently review the attach/detach/poweroff procedure, and obtain '
    'separate explicit authorization.'
)
if __name__=='__main__':
    if '--execute' in sys.argv:
        raise SystemExit(LIVE_TRIAL_GATE)
    board.main()
