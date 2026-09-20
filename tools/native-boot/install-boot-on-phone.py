#!/usr/bin/env python3
"""Run over pinned SSH: validate (default), or write ONLY this phone's BOOT.

Requires an exact staged image SHA and --write to modify BOOT. This installer
is intentionally specific to the September 20 native conversion and the
verified original BOOT. It is not a general flashing utility.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat

ORIGINAL = '0962dbdd67b748858189b46d464820ec7d1f3ea843cc7c3d033b69c40931b44e'
RECOVERY = '1a827b43d29141efb47f530902dd4e4ee2b6d780515893c9ecd676ad27efd7d1'
VENDOR = '383b6f6789e655b070929914db5639e6d9bacc78823ada7bf553962b5b4888be'
LOWER = '1139f04222e577f18dc2d540721c0be037700952780d54cfb919e39c5d15ac1b'
SIZE = 67108864
DEVICE = Path('/dev/block/by-name/boot')


def require(condition, message):
    if not condition:
        raise SystemExit(message)


def digest(path):
    with open(path, 'rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def check_other_partitions():
    require(digest('/dev/block/by-name/recovery') == RECOVERY, 'RECOVERY changed')
    require(digest('/dev/block/by-name/vendor_boot') == VENDOR, 'vendor_boot changed')


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--image', type=Path, required=True)
    ap.add_argument('--sha256', required=True)
    ap.add_argument('--write', action='store_true')
    args = ap.parse_args()
    require(re.fullmatch('[0-9a-f]{64}',args.sha256), 'Invalid expected SHA')
    require(os.geteuid() == 0, 'Requires native root')
    require(os.readlink('/proc/1/exe') == '/system/bin/native-guardian', 'Wrong PID1')
    with open('/proc/boot_reset','rb') as f:
        bore = f.read(4096).replace(b'\0',b'').decode()
    last = re.search(r'^last: (\d+)',bore,re.M)
    require(last is not None, 'Missing BORE counter')
    record = re.search(r'^\[\s*'+last.group(1)+r'\].*$',bore,re.M)
    require(record is not None and '> RECOVERY >' in record.group(0), 'Not running from RECOVERY')
    require(DEVICE.resolve() == Path('/dev/sda14'), 'Wrong BOOT alias')
    devstat = DEVICE.stat()
    require(stat.S_ISBLK(devstat.st_mode) and (os.major(devstat.st_rdev),os.minor(devstat.st_rdev)) == (8,14), 'Wrong BOOT device')
    sysdev = Path('/sys/class/block/sda14')
    require('PARTNAME=boot\n' in (sysdev/'uevent').read_text(), 'Wrong PARTNAME')
    require((sysdev/'size').read_text().strip() == '131072', 'Wrong BOOT size')
    require((sysdev/'start').read_text().strip() == '248960', 'Wrong BOOT start')
    require((sysdev/'ro').read_text().strip() == '0', 'BOOT is read-only')
    require(not list((sysdev/'holders').iterdir()), 'BOOT has holders')
    require(all(line.split()[2] != '8:14' for line in Path('/proc/1/mountinfo').read_text().splitlines()), 'BOOT is mounted')
    require(not args.image.is_symlink() and stat.S_ISREG(args.image.stat().st_mode), 'Image is not a regular file')
    require(args.image.stat().st_size == SIZE, 'Wrong image size')
    require(digest(args.image) == args.sha256, 'Staged image hash mismatch')
    require(digest(DEVICE) == ORIGINAL, 'BOOT is no longer the verified original; refusing overwrite')
    check_other_partitions()
    lower_source = Path('/proc/1/root/native/rootfs.tar.xz')
    cache_root = Path('/proc/1/root/cache/s22-linux')
    lower_target = cache_root/'lower-rootfs.tar.xz'
    require(not cache_root.is_symlink() and cache_root.is_dir(), 'Invalid CACHE directory')
    require('PARTNAME=cache\n' in Path('/sys/class/block/sda33/uevent').read_text(), 'Wrong CACHE partition')
    require(any(line.split()[2:5] == ['259:17','/','/cache'] for line in Path('/proc/1/mountinfo').read_text().splitlines()), 'Unexpected CACHE mount')
    require(digest(lower_source) == LOWER and lower_source.stat().st_size == 22123828, 'Wrong lower source')
    require(not lower_target.is_symlink(), 'Refusing symlink CACHE archive')
    if lower_target.exists():
        require(lower_target.is_file() and digest(lower_target) == LOWER, 'Existing CACHE archive differs')
    else:
        fs = os.statvfs(cache_root)
        require(fs.f_bavail*fs.f_frsize > 22123828+32*1024*1024, 'Insufficient CACHE headroom')
    result = {'bore_before':last.group(1), 'boot_before_sha256':ORIGINAL,
              'candidate_sha256':args.sha256, 'image_bytes':SIZE,
              'recovery_unchanged_sha256':RECOVERY,'vendor_boot_unchanged_sha256':VENDOR,
              'cache_lower_sha256':LOWER, 'write_completed':False}
    if not args.write:
        print(json.dumps(result,indent=2))
        return
    if not lower_target.exists():
        with lower_source.open('rb') as src, lower_target.open('xb') as dst:
            while data := src.read(1024*1024):
                dst.write(data)
            dst.flush()
            os.fsync(dst.fileno())
        lower_target.chmod(0o400)
        dirfd = os.open(cache_root,os.O_DIRECTORY)
        os.fsync(dirfd)
        os.close(dirfd)
    require(digest(lower_target) == LOWER, 'Persistent lower read-back mismatch')
    # No partition path comes from a command-line argument.
    with args.image.open('rb') as src:
        fd = os.open(DEVICE,os.O_WRONLY|os.O_SYNC)
        try:
            require(os.fstat(fd).st_rdev == devstat.st_rdev, 'BOOT device changed')
            written = 0
            while data := src.read(1024*1024):
                view = memoryview(data)
                while view:
                    n = os.write(fd,view)
                    require(n > 0, 'Short BOOT write')
                    written += n
                    view = view[n:]
            require(written == SIZE, 'Incomplete BOOT write')
            os.fsync(fd)
        finally:
            os.close(fd)
    os.sync()
    require(digest(DEVICE) == args.sha256, 'BOOT read-back mismatch; DO NOT REBOOT')
    check_other_partitions()
    result.update(write_completed=True, boot_readback_sha256=args.sha256)
    print(json.dumps(result,indent=2))


if __name__ == '__main__':
    main()
