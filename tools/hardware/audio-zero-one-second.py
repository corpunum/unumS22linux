#!/usr/bin/env python3
"""Execute exactly one native RDMA2 digital-zero playback, never capture.

The controller must supervise the validated temporary digital route and its
rollback. exec preserves the supervised PID, including timeout termination.
"""
import os
from pathlib import Path

if Path('/proc/1/comm').read_text().strip()!='native-guardian':
    raise SystemExit('requires the reviewed native guardian session')
os.execvp('aplay',['aplay','--nonblock','-v','-D','hw:0,2','-t','raw','-f','S16_LE',
                  '-r','48000','-c','2','--buffer-size=8192','--period-size=1024',
                  '-d','1','/dev/zero'])
