#!/usr/bin/env python3
"""S22-only inventory/stream helper. Opens phone block devices O_RDONLY only.

Streamed ciphertext is not a decrypted or restore-tested personal-file backup.
No mounts, ioctls changing state, key operations or block writes are performed.
"""
import argparse
import fcntl
import gzip
import hashlib
import json
import os
from pathlib import Path
import stat
import struct
import sys


# Exact already-observed layout. No arbitrary paths or whole-disk streaming.
EXPECTED = {
    'metadata': ('sda26', 65536),
    'keystorage': ('sda7', 1024),
    'keyrefuge': ('sda22', 106496),
    'efs': ('sda1', 32768),
    'sec_efs': ('sda2', 32768),
    'boot': ('sda14', 131072),
    'vendor_boot': ('sda15', 65536),
    'recovery': ('sda16', 196608),
    'dtbo': ('sda12', 16384),
    'vbmeta': ('sda24', 128),
    'vbmeta_system': ('sda25', 128),
    'userdata': ('sda36', 221257728),
    'super': ('sda30', 22937600),
    'prism': ('sda31', 2867200),
    'optics': ('sda32', 61440),
}


def inventory():
    if Path('/proc/1/comm').read_text().strip() != 'native-guardian':
        raise RuntimeError('Not the expected native recovery environment')
    result = {}
    for entry in Path('/sys/class/block').iterdir():
        props = dict(line.split('=', 1) for line in (entry / 'uevent').read_text().splitlines()
                     if '=' in line)
        name = props.get('PARTNAME')
        if name not in EXPECTED:
            continue
        expected_node, expected_sectors = EXPECTED[name]
        sectors = int((entry / 'size').read_text())
        if props['DEVNAME'] != expected_node or sectors != expected_sectors or name in result:
            raise RuntimeError(f'Partition identity/size mismatch: {name}')
        major, minor = map(int, (entry / 'dev').read_text().split(':'))
        source = Path('/dev') / expected_node
        info = source.stat()
        if not stat.S_ISBLK(info.st_mode) or (os.major(info.st_rdev), os.minor(info.st_rdev)) != (major, minor):
            raise RuntimeError(f'Block node identity mismatch: {name}')
        if any((entry / 'holders').iterdir()):
            raise RuntimeError(f'Partition has an active holder: {name}')
        for mounts in ('/proc/self/mountinfo', '/proc/1/mountinfo'):
            for line in Path(mounts).read_text().splitlines():
                if line.split()[2] == f'{major}:{minor}':
                    raise RuntimeError(f'Partition is mounted: {name}')
        result[name] = {'source': str(source), 'major': major, 'minor': minor,
                        'start_sector': int((entry / 'start').read_text()),
                        'sectors': sectors, 'bytes': sectors * 512}
    if set(result) != set(EXPECTED):
        raise RuntimeError('Expected partition set is incomplete')
    return result


def stream(name, parts, compress):
    item = parts[name]
    fd = os.open(item['source'], os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        capacity, = struct.unpack('Q', fcntl.ioctl(fd, 0x80081272, bytes(8)))  # BLKGETSIZE64, read-only
        if (os.major(info.st_rdev), os.minor(info.st_rdev)) != (item['major'], item['minor']) or capacity != item['bytes']:
            raise RuntimeError('Opened block device failed identity/capacity check')
        print(json.dumps({'event': 'start', 'partition': name, 'wire_compression': 'gzip' if compress else 'none', **item}), file=sys.stderr, flush=True)
        digest = hashlib.sha256()
        remaining = capacity
        writer = gzip.GzipFile(fileobj=sys.stdout.buffer, mode='wb', compresslevel=1, mtime=0) if compress else sys.stdout.buffer
        try:
            while remaining:
                chunk = os.read(fd, min(4 * 1024 * 1024, remaining))
                if not chunk:
                    raise RuntimeError('Premature end of block device')
                digest.update(chunk)
                writer.write(chunk)
                remaining -= len(chunk)
        finally:
            if compress:
                writer.close()
        sys.stdout.buffer.flush()
        print(json.dumps({'event': 'complete', 'partition': name, 'bytes': capacity,
                          'sha256': digest.hexdigest()}), file=sys.stderr, flush=True)
    finally:
        os.close(fd)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stream', choices=EXPECTED)
    parser.add_argument('--gzip', action='store_true', help='compress the transport, hashing original bytes')
    args = parser.parse_args()
    parts = inventory()
    if args.stream:
        stream(args.stream, parts, args.gzip)
    else:
        print(json.dumps({'partitions': parts, 'block_devices_opened_for_write': False}, indent=2))
