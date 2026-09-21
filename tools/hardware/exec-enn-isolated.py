#!/usr/bin/env python3
"""Run a root-specific ENN loader or initialization diagnostic in isolation.

This helper is intended for the supervised phone attempt.  It does not run an
ARM ELF on the host, expose hardware nodes, or start an Android service.
The original root permits only dlopen/dlsym. The separate init root permits
only its reviewed no-argument initializer, with no model or buffer calls.
"""

from __future__ import annotations

import ctypes
import os
from pathlib import Path
import stat
import sys

CLONE_NEWNS = 0x00020000
MS_RDONLY = 1
MS_NOSUID = 2
MS_NODEV = 4
MS_NOEXEC = 8
MS_PRIVATE = 1 << 18
MS_REC = 1 << 14
PR_CAPBSET_DROP = 24
PR_SET_NO_NEW_PRIVS = 38


def fail(message: str) -> "NoReturn":
    raise SystemExit(message)


def main() -> int:
    if len(sys.argv) < 2:
        fail("usage: exec-enn-isolated.py ROOT [--proc] ROOT_SPECIFIC_PROBE")
    root = Path(sys.argv[1]).resolve()
    roots = {Path("/srv/s22/npu-compat-20260921"): "/bin/enn-dlopen-probe",
             Path("/srv/s22/npu-init-20260921"): "/bin/enn-init-probe"}
    if root not in roots:
        fail(f"unexpected isolated root: {root}")
    argv = sys.argv[2:]
    want_proc = False
    if argv and argv[0] == "--proc":
        want_proc = True
        argv.pop(0)
    if argv != [roots[root]]:
        fail("only the exact probe assigned to this reviewed root is allowed")
    probe = root / roots[root].lstrip('/')
    if probe.is_symlink() or not probe.is_file() or probe.stat().st_uid != 0 or probe.stat().st_mode & 0o022:
        fail("probe is absent or not a root-owned non-group/world-writable file")
    for path in [root, *root.rglob("*")]:
        if path.is_symlink():
            fail(f"symlinks are forbidden in the staged root: {path}")
        info = path.lstat()
        if info.st_uid != 0 or info.st_gid != 0:
            fail(f"staged path is not root-owned: {path}")
        if info.st_mode & 0o022:
            fail(f"staged path is writable by group/world: {path}")
        if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
            fail(f"special file forbidden before private device setup: {path}")

    libc = ctypes.CDLL(None, use_errno=True)
    if libc.unshare(CLONE_NEWNS) != 0:
        raise OSError(ctypes.get_errno(), "private mount namespace failed")
    if libc.mount(None, b"/", None, MS_REC | MS_PRIVATE, None) != 0:
        raise OSError(ctypes.get_errno(), "private mount propagation failed")

    if want_proc:
        target = os.fsencode(root / "proc")
        if libc.mount(b"proc", target, b"proc", MS_RDONLY | MS_NOSUID | MS_NODEV | MS_NOEXEC, None) != 0:
            raise OSError(ctypes.get_errno(), "read-only proc mount failed")

    # Do not inherit the phone's /dev.  Only the four ordinary entropy/basic
    # character devices are created in a private tmpfs; no NPU, DRM, DMA-heap,
    # binder, display, or input node can enter the chroot.
    target = os.fsencode(root / "dev")
    # Do not set MS_NODEV here: the four explicit character nodes must remain
    # usable after the tmpfs mount. No other node is ever created.
    if libc.mount(b"tmpfs", target, b"tmpfs", MS_NOSUID | MS_NOEXEC, b"mode=0755,size=1m") != 0:
        raise OSError(ctypes.get_errno(), "private device tmpfs mount failed")
    for name in ("null", "zero", "random", "urandom"):
        source = Path("/dev") / name
        source_info = source.stat()
        if not stat.S_ISCHR(source_info.st_mode):
            fail(f"host device is not a character device: {source}")
        path = root / "dev" / name
        os.mknod(path, stat.S_IFCHR | 0o600, source_info.st_rdev)
        os.chown(path, 1000, 1000)

    last_cap = int(Path("/proc/sys/kernel/cap_last_cap").read_text())
    for cap in range(last_cap + 1):
        if libc.prctl(PR_CAPBSET_DROP, cap, 0, 0, 0) != 0:
            raise OSError(ctypes.get_errno(), "capability bounding-set drop failed")
    if libc.prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "NoNewPrivs failed")

    status_fd = os.open("/proc/self/status", os.O_RDONLY | os.O_CLOEXEC)
    os.chroot(root)
    os.chdir("/")
    os.setgroups([])
    os.setgid(1000)
    os.setuid(1000)
    status = os.pread(status_fd, 16384, 0).decode()
    os.close(status_fd)
    fields = dict(line.split(":", 1) for line in status.splitlines() if ":" in line)
    if any(int(fields[key].strip(), 16) != 0 for key in ("CapEff", "CapPrm", "CapBnd")):
        fail("capability drop verification failed")
    if fields["NoNewPrivs"].strip() != "1":
        fail("NoNewPrivs verification failed")
    if os.getuid() != os.geteuid() or os.getuid() != 1000 or os.getgid() != 1000:
        fail("UID/GID drop verification failed")

    env = {
        "PATH": "/system/bin:/bin",
        "LD_LIBRARY_PATH": "/vendor/lib64:/system/lib64",
        "ANDROID_ROOT": "/system",
        "ANDROID_DATA": "/data",
        "TMPDIR": "/data/local/tmp",
    }
    print("ISOLATED uid=1000 caps=0 NoNewPrivs=1 devices=null,zero,random,urandom", flush=True)
    os.execve(argv[0], argv, env)
    return 127


if __name__ == "__main__":
    raise SystemExit(main())
