#!/usr/bin/env python3
"""fs-ctl.py FN [ARG...]: one call on unum-fastshell's shell.sock (no touchd allow-list).

  fs-ctl.py screenshot half     the shell's own canvas, 540x1170, into the OpenUnum gallery
  fs-ctl.py open camera | perf | state | home
"""
import json
import socket
import sys

SOCK = '/mnt/omarchy-trial/run/s22-touch/shell.sock'

if len(sys.argv) < 2:
    sys.exit(__doc__)
s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
s.settimeout(10)
s.connect(SOCK)
s.sendall((json.dumps({'fn': sys.argv[1], 'args': sys.argv[2:]}) + '\n').encode())
buf = b''
while not buf.endswith(b'\n'):
    b = s.recv(65536)
    if not b:
        break
    buf += b
print(buf.decode().strip())
