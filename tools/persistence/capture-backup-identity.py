#!/usr/bin/env python3
"""Capture a private read-only device/generation receipt for a backup session."""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess

PROJECT = Path(__file__).resolve().parents[2]
BASE = Path('/home/corpunum/s22-private-backups')
REMOTE = '''
import json, os
from pathlib import Path
if Path('/proc/1/comm').read_text().strip() != 'native-guardian':
    raise RuntimeError('Not native recovery')
def bounded(path, limit=4096):
    try:
        with open(path, 'rb') as handle:
            return handle.read(limit).replace(b'\\0', b'').decode('utf-8', 'replace')
    except FileNotFoundError:
        return None
print(json.dumps({
    'native_pid1': bounded('/proc/1/comm'),
    'boot_id': bounded('/proc/sys/kernel/random/boot_id'),
    'boot_reset': bounded('/proc/boot_reset'),
    'device_tree_model': bounded('/proc/device-tree/model'),
    'device_tree_compatible': bounded('/proc/device-tree/compatible'),
    'cmdline': bounded('/proc/cmdline'),
    'bootconfig': bounded('/proc/bootconfig', 16384),
    'kernel': list(os.uname()),
    'uptime': bounded('/proc/uptime'),
}))
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('phase', choices=('during', 'after'))
    args = parser.parse_args()
    directory = args.directory.resolve(strict=True)
    if directory.parent != BASE or directory.stat().st_uid != os.getuid() or directory.stat().st_mode & 0o077:
        raise RuntimeError('Expected an owned private backup session directory')
    if not (directory / 'backup-scope.json').is_file():
        raise RuntimeError('Backup scope is absent')
    os.umask(0o077)
    identity = json.loads(subprocess.check_output(
        [str(PROJECT / 'tools/s22-ssh'), 'python3 -c ' + shlex.quote(REMOTE)], timeout=30))
    identity['captured_utc'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    identity['phase'] = args.phase
    identity['pinned_known_hosts_sha256'] = hashlib.sha256(
        (PROJECT / 'evidence/native-linux-20260919/native-v2-known-hosts').read_bytes()).hexdigest()
    identity['classification'] = 'PRIVATE_DEVICE_IDENTITY'
    if args.phase == 'after':
        earlier = json.loads((directory / 'identity-during.json').read_text())
        identity['same_boot_generation_as_during'] = identity['boot_id'] == earlier['boot_id']
        if not identity['same_boot_generation_as_during']:
            raise RuntimeError('Boot generation changed during backup')
    output = directory / f'identity-{args.phase}.json'
    with output.open('x') as handle:
        json.dump(identity, handle, indent=2)
        handle.write('\n')
        handle.flush()
        os.fsync(handle.fileno())
    print(f'Private identity receipt captured: {output}')


if __name__ == '__main__':
    main()
