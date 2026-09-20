#!/usr/bin/env python3
"""Validate and print AOSP liblp metadata from a read-only captured prefix.

Reference: platform/system/core fs_mgr/liblp/include/liblp/metadata_format.h.
Default mode only reads a captured file. Optional device-specific vendor mode
opens the known super block device O_RDONLY and streams its validated vendor
extents to stdout. Neither mode writes a block device or maps partitions.
"""
import hashlib
import argparse
import fcntl
import json
import os
import stat
import struct
import sys
from pathlib import Path


def read(data):
    geom = data[4096:8192]
    magic, size = struct.unpack_from('<II', geom)
    if magic != 0x616c4467 or not 52 <= size <= 4096:
        raise ValueError('invalid geometry')
    if hashlib.sha256(geom[:8] + bytes(32) + geom[40:size]).digest() != geom[8:40]:
        raise ValueError('geometry checksum mismatch')
    max_size, slots, block_size = struct.unpack_from('<III', geom, 40)
    if not 1 <= slots <= 4 or max_size > 1048576:
        raise ValueError('unexpected metadata geometry')
    header = data[12288:12288 + max_size]
    magic, major, minor, hsize = struct.unpack_from('<IHHI', header)
    if magic != 0x414c5030 or major != 10 or minor > 2 or hsize not in (128, 256):
        raise ValueError('unsupported LP header')
    if hashlib.sha256(header[:12] + bytes(32) + header[44:hsize]).digest() != header[12:44]:
        raise ValueError('header checksum mismatch')
    tsize, = struct.unpack_from('<I', header, 44)
    if tsize + hsize > max_size or tsize + hsize > len(header):
        raise ValueError('truncated metadata tables')
    tables = header[hsize:hsize + tsize]
    if hashlib.sha256(tables).digest() != header[48:80]:
        raise ValueError('table checksum mismatch')

    def entries(offset, expected_size):
        start, count, stride = struct.unpack_from('<III', header, offset)
        if stride != expected_size or start + count * stride > len(tables):
            raise ValueError('invalid table descriptor')
        return [tables[start + i * stride:start + (i + 1) * stride] for i in range(count)]

    extents = [dict(zip(('sectors', 'type', 'physical_sector', 'source'),
                       struct.unpack('<QIQI', entry))) for entry in entries(92, 24)]
    partitions = []
    for entry in entries(80, 52):
        name, attrs, first, count, group = struct.unpack('<36sIIII', entry)
        if first + count > len(extents):
            raise ValueError('invalid extent range')
        selected = extents[first:first + count]
        partitions.append({'name': name.split(b'\0')[0].decode('ascii'), 'attributes': attrs,
                           'size_bytes': sum(e['sectors'] for e in selected) * 512,
                           'extents': selected})
    return {'validated_sha256_checksums': True, 'slot': 0, 'metadata_max_size': max_size,
            'metadata_slots': slots, 'logical_block_size': block_size, 'partitions': partitions}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('prefix')
    parser.add_argument('--stream-vendor-readonly', action='store_true',
                        help='stream only vendor extents from the verified S22 super device to stdout')
    args = parser.parse_args()
    prefix = Path(args.prefix).read_bytes()
    metadata = read(prefix)
    if not args.stream_vendor_readonly:
        print(json.dumps(metadata, indent=2))
    else:
        # This mode is intentionally device-specific and always O_RDONLY.
        source = '/dev/block/by-name/super'
        if os.path.realpath(source) != '/dev/sda30':
            raise RuntimeError('unexpected super device identity')
        fd = os.open(source, os.O_RDONLY | os.O_CLOEXEC)
        try:
            if not stat.S_ISBLK(os.fstat(fd).st_mode):
                raise RuntimeError('source is not a block device')
            capacity, = struct.unpack('Q', fcntl.ioctl(fd, 0x80081272, bytes(8)))
            if capacity != 11744051200 or os.pread(fd, len(prefix), 0) != prefix:
                raise RuntimeError('super geometry or metadata changed')
            vendor, = [p for p in metadata['partitions'] if p['name'] == 'vendor']
            if vendor['attributes'] != 1 or not 0 < vendor['size_bytes'] <= 2147483648:
                raise RuntimeError('unexpected vendor partition')
            for extent in vendor['extents']:
                offset, length = extent['physical_sector'] * 512, extent['sectors'] * 512
                if extent['source'] != 0 or extent['type'] != 0 or offset < 1048576 or offset + length > capacity:
                    raise RuntimeError('unsafe vendor extent')
            for extent in vendor['extents']:
                offset, remaining = extent['physical_sector'] * 512, extent['sectors'] * 512
                while remaining:
                    data = os.pread(fd, min(1048576, remaining), offset)
                    if not data:
                        raise RuntimeError('short block-device read')
                    sys.stdout.buffer.write(data)
                    offset += len(data)
                    remaining -= len(data)
        finally:
            os.close(fd)
