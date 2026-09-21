#!/usr/bin/python3
"""In-process filter check before the accepted one-child agent tool test."""
import ctypes
import errno
import json
import os
from pathlib import Path

assert os.uname().machine == 'aarch64'
assert os.getuid() == os.geteuid() == 1000
libc = ctypes.CDLL(None, use_errno=True)
status = dict(line.split(':', 1) for line in Path('/proc/self/status').read_text().splitlines() if ':' in line)
assert status['Seccomp'].strip() == '2' and status['NoNewPrivs'].strip() == '1'
assert all(int(status[key].strip(), 16) == 0 for key in ('CapEff', 'CapPrm', 'CapBnd'))
# Invalid reversed bounds cannot close a descriptor even without the filter.
ctypes.set_errno(0)
result = libc.syscall(ctypes.c_long(436), ctypes.c_uint(1), ctypes.c_uint(0), ctypes.c_uint(0))
assert result == -1 and ctypes.get_errno() == errno.ENOSYS, 'close_range filter not inherited'
import subprocess
child = subprocess.run(['/usr/bin/printf', 'PI_SPAWN_OK'], capture_output=True,
                       text=True, check=True, timeout=3)
assert child.stdout == 'PI_SPAWN_OK'
print(json.dumps(dict(uid=os.getuid(), inherited_close_range_filter=True,
                     captured_output=child.stdout, child_returncode=child.returncode)))
