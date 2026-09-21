#!/usr/bin/env python3
"""Expose only the verified card0 ALSA control in the running Arch session.

Default is read-only validation. --apply creates a private mountpoint and
binds the existing node, never opening a PCM or writing a mixer control.
This is session-only: it does not change desktop startup configuration.
"""
from __future__ import annotations
import argparse, json, os, stat, subprocess
from pathlib import Path

SOURCE = Path("/dev/snd/controlC0")
TARGET_ROOT = Path("/mnt/omarchy-trial/dev/snd")
TARGET = TARGET_ROOT / "controlC0"

def checked_directory(path):
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise RuntimeError('unsafe directory: ' + str(path))
    return info

def checked_node(path, device):
    info = path.lstat()
    if not stat.S_ISCHR(info.st_mode) or info.st_rdev != device or info.st_uid != 0:
        raise RuntimeError('wrong ALSA node: ' + str(path))
    if stat.S_IMODE(info.st_mode) not in (0o600, 0o660):
        raise RuntimeError('unexpected ALSA node permissions')
    return info

def mounts():
    result = {}
    for line in Path('/proc/self/mountinfo').read_text().splitlines():
        left, right = line.split(' - ', 1)
        a, b = left.split(), right.split()
        result[a[4]] = {'root': a[3], 'fs': b[0], 'device': a[2], 'options': a[5].split(',')}
    return result

def main() -> int:
    ap = argparse.ArgumentParser(); ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    if os.geteuid() != 0 or Path('/proc/1/comm').read_text().strip() != 'native-guardian':
        raise RuntimeError('requires the native guardian root session')
    if Path('/proc/asound/card0/id').read_text().strip() != 'RainbowPrince':
        raise RuntimeError('unexpected sound card')
    sysnode = Path('/sys/class/sound/controlC0').resolve(strict=True)
    if sysnode != Path('/sys/devices/platform/sound/sound/card0/controlC0'):
        raise RuntimeError('unexpected sound class ancestry')
    event = dict(line.split('=', 1) for line in (sysnode/'uevent').read_text().splitlines() if '=' in line)
    if event.get('DEVNAME') != 'snd/controlC0' or (sysnode/'dev').read_text().strip() != '116:114':
        raise RuntimeError('sound sysfs identity changed')
    if (event.get('MAJOR'), event.get('MINOR')) != ('116', '114'):
        raise RuntimeError('uevent identity changed')
    device = os.makedev(116, 114)
    for directory in ('/dev', '/dev/snd', '/mnt', '/mnt/omarchy-trial/dev'):
        checked_directory(Path(directory))
    before = mounts()
    if before.get('/dev', {}).get('fs') != 'tmpfs' or before.get('/mnt/omarchy-trial/dev', {}).get('fs') != 'tmpfs':
        raise RuntimeError('expected native and Arch tmpfs device directories')
    arch_mount = before.get('/mnt/omarchy-trial', {})
    if (arch_mount.get('fs'), arch_mount.get('device'), arch_mount.get('root')) != ('ext4', '259:20', '/arch'):
        raise RuntimeError('Arch root is not its expected mounted filesystem')
    # The existing extracted Arch root belongs to alarm; its mounted /dev is
    # independently root-owned and cannot be replaced by that unprivileged user.
    if not stat.S_ISDIR(Path('/mnt/omarchy-trial').lstat().st_mode):
        raise RuntimeError('Arch mountpoint is not a directory')
    src = checked_node(SOURCE, device)
    if TARGET.parent.exists() or TARGET.parent.is_symlink():
        checked_directory(TARGET.parent)
    if str(TARGET) in before:
        dst = checked_node(TARGET, device)
        if (src.st_dev, src.st_ino) != (dst.st_dev, dst.st_ino):
            raise RuntimeError('existing bind points to another source')
        already = True
    else:
        already = False
        if TARGET.exists() or TARGET.is_symlink():
            raise RuntimeError('refusing to overwrite an unmounted existing target')
        if args.apply:
            TARGET.parent.mkdir(mode=0o755, exist_ok=True)
            checked_directory(TARGET.parent)
            fd = os.open(TARGET, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            os.close(fd)
            try:
                subprocess.run(['mount', '--bind', str(SOURCE), str(TARGET)], check=True,
                               capture_output=True, text=True, timeout=10)
            except Exception:
                if str(TARGET) not in mounts():
                    TARGET.unlink()
                raise
            dst = checked_node(TARGET, device)
            if str(TARGET) not in mounts() or (src.st_dev, src.st_ino) != (dst.st_dev, dst.st_ino):
                raise RuntimeError('post-bind source identity differs; stop and inspect')
    print(json.dumps({'apply': args.apply, 'already_mounted': already,
                      'source': str(SOURCE), 'target': str(TARGET), 'rdev': '116:114',
                      'session_only': True, 'pcm_exposed': [], 'mixer_writes': []}, sort_keys=True))
    return 0
if __name__ == "__main__": raise SystemExit(main())
