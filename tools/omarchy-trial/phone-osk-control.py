#!/usr/bin/env python3
"""Inspect or show the sole Squeekboard in the isolated Arch phone trial."""
import os
from pathlib import Path
import subprocess
import sys

trial = Path('/mnt/omarchy-trial')
mode = sys.argv[1] if len(sys.argv) == 2 else '--inspect'
if mode not in ('--inspect', '--show'):
    raise SystemExit('Use --inspect or --show')
pids = subprocess.check_output(['pidof', 'squeekboard'], text=True).split()
if len(pids) != 1:
    raise SystemExit('Need exactly one Squeekboard')
proc = Path('/proc') / pids[0]
if Path(os.readlink(proc / 'root')) != trial:
    raise SystemExit('Squeekboard is not in the expected trial')
env = dict(item.split(b'=', 1) for item in (proc / 'environ').read_bytes().split(b'\0') if b'=' in item)
address = env[b'DBUS_SESSION_BUS_ADDRESS'].decode()
command = ['chroot', str(trial), '/usr/bin/env', '-i', 'HOME=/root',
           'PATH=/usr/bin', 'XDG_RUNTIME_DIR=/run/user/0',
           'DBUS_SESSION_BUS_ADDRESS=' + address, '/usr/bin/gdbus']
if mode == '--inspect':
    command += ['introspect', '--session', '--dest', 'sm.puri.OSK0',
                '--object-path', '/sm/puri/OSK0']
else:
    command += ['call', '--session', '--dest', 'sm.puri.OSK0',
                '--object-path', '/sm/puri/OSK0', '--method', 'sm.puri.OSK0.SetVisible', 'true']
raise SystemExit(subprocess.run(command, timeout=10).returncode)
