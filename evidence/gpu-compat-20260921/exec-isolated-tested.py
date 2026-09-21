#!/usr/bin/env python3
"""Phone-native Alpine helper: enter one isolated root and drop privileges.

Optional read-only proc/sys views and selected device nodes live in a private
mount namespace. No partition access or system driver selection happens here.
This is not a complete sandbox: selected GPU ioctls act on real hardware.
"""
import ctypes
import os
from pathlib import Path
import stat
import sys

def main():
    root = Path(sys.argv[1]).resolve()
    if root.parent != Path('/srv/s22/gpu-compat-20260921') or root.name not in {
        'base', 'opencl-real', 'opencl-headless', 'vulkan-headless'}:
        raise SystemExit('Unexpected isolated runtime root')
    argv = sys.argv[2:]
    mounts = []
    while argv and argv[0] in {'--proc', '--sys'}:
        mounts.append(argv.pop(0)[2:])
    if not argv or not argv[0].startswith('/system/bin/'):
        raise SystemExit('Expected isolated test executable')
    libc = ctypes.CDLL(None, use_errno=True)
    devices = []
    allowed = {'null', 'random', 'urandom', 'dri/renderD128', 'dri/card0',
               'dma_heap/system', 'dma_heap/system-uncached'}
    for path in (root/'dev').rglob('*'):
        mode = path.lstat()
        if stat.S_ISCHR(mode.st_mode):
            name = str(path.relative_to(root/'dev'))
            assert name in allowed and mode.st_rdev == (Path('/dev')/name).stat().st_rdev
            if name.startswith('dri/'):
                assert str((Path('/sys/class/drm')/path.name/'device/driver').resolve()) == '/sys/bus/platform/drivers/sgpu'
            devices.append((name, mode.st_rdev))
    if mounts or devices:
        # Private mount namespace; these views vanish with the test process.
        if libc.unshare(0x00020000) != 0:  # CLONE_NEWNS
            raise OSError(ctypes.get_errno(), 'private mount namespace failed')
        if libc.mount(None, b'/', None, 16384 | 262144, None) != 0:  # REC|PRIVATE
            raise OSError(ctypes.get_errno(), 'private propagation failed')
        for name in mounts:
            target = os.fsencode(root/name)
            if name == 'proc':
                result = libc.mount(b'proc', target, b'proc', 1 | 2 | 4 | 8, None)
            else:
                result = libc.mount(b'/sys', target, None, 4096, None)
                if result == 0:
                    result = libc.mount(None, target, None, 4096 | 32 | 1 | 2 | 4 | 8, None)
            if result != 0:
                raise OSError(ctypes.get_errno(), 'read-only mount failed: '+name)
        if devices:
            # Userdata is intentionally nodev. Do not change that mount flag:
            # a tiny process-private dev tmpfs supplies only selected nodes.
            target = os.fsencode(root/'dev')
            if libc.mount(b'tmpfs', target, b'tmpfs', 2 | 8, b'mode=0755,size=1m') != 0:
                raise OSError(ctypes.get_errno(), 'private device mount failed')
            for name, device in devices:
                path = root/'dev'/name
                path.parent.mkdir(parents=True, exist_ok=True)
                os.mknod(path, stat.S_IFCHR | 0o600, device)
                os.chown(path, 1000, 1000)
    last = int(Path('/proc/sys/kernel/cap_last_cap').read_text())
    for cap in range(last + 1):
        if libc.prctl(24, cap, 0, 0, 0) != 0:  # PR_CAPBSET_DROP
            raise OSError(ctypes.get_errno(), 'capability drop failed')
    if libc.prctl(38, 1, 0, 0, 0) != 0:  # PR_SET_NO_NEW_PRIVS
        raise OSError(ctypes.get_errno(), 'NoNewPrivs failed')
    status_fd = os.open('/proc/self/status', os.O_RDONLY | os.O_CLOEXEC)
    os.chroot(root)
    os.chdir('/')
    os.setgroups([])
    os.setgid(1000)
    os.setuid(1000)
    status = os.read(status_fd, 16384).decode()
    os.close(status_fd)
    fields = dict(line.split(':', 1) for line in status.splitlines() if ':' in line)
    assert all(int(fields[key].strip(), 16) == 0 for key in ['CapEff', 'CapPrm', 'CapBnd'])
    assert fields['NoNewPrivs'].strip() == '1'
    assert os.getuid() == os.geteuid() == os.getgid() == os.getegid() == 1000
    env = {'PATH': '/system/bin', 'LD_LIBRARY_PATH': '/vendor/lib64:/vendor/lib64/hw:/system/lib64',
           'ANDROID_ROOT': '/system', 'ANDROID_DATA': '/data', 'TMPDIR': '/data/local/tmp',
           'OCL_ICD_FILENAMES': '/vendor/lib64/libSGPUOpenCL.so'}
    print('ISOLATED uid=1000 caps=0 NoNewPrivs=1', flush=True)
    os.execve(argv[0], argv, env)

if __name__ == '__main__':
    main()
