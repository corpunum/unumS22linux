#!/usr/bin/env python3
"""Populate the already formatted/mounted S22 userdata; never format or flash.

Refuses an existing installation. Root/model archives stay private/local.
The separately reviewed supervisor and startup hook are NOT activated here.
"""
import hashlib
import json
from pathlib import Path
import shlex
import subprocess

PROJECT = Path(__file__).resolve().parents[2]
SSH = PROJECT / 'tools/s22-ssh'
UUID = '1dd55c26-bd57-489a-9d9b-4c60e6f430eb'
REMOTE_CHECK = '''
from pathlib import Path
import os, re, subprocess
assert Path('/proc/1/comm').read_text().strip() == 'native-guardian'
assert Path('/sys/class/block/sda36/size').read_text().strip() == '221257728'
assert 'PARTNAME=userdata' in Path('/sys/class/block/sda36/uevent').read_text()
out=subprocess.check_output(['blkid','/dev/sda36'], text=True)
assert 'UUID="1dd55c26-bd57-489a-9d9b-4c60e6f430eb"' in out
mounts=[line.split() for line in Path('/proc/self/mountinfo').read_text().splitlines()]
assert any(row[2]=='259:20' and row[4]=='/srv/s22' and 'rw' in row[5].split(',') for row in mounts)
assert not Path('/srv/s22/.persistent-ready.json').exists()
assert not any(Path('/srv/s22/arch').iterdir())
assert not any(Path('/srv/s22/model-bench').iterdir())
assert os.statvfs('/srv/s22').f_bavail * os.statvfs('/srv/s22').f_frsize > 8*1024**3
print('Mounted userdata identity and empty deployment paths verified.')
'''


def remote(command):
    subprocess.run([str(SSH), command], check=True)


def main():
    manifest = json.loads((PROJECT/'tools/persistence/persistent-artifacts-20260920.json').read_text())
    entries = [manifest['archive'], *manifest['artifacts'], manifest['fixup']]
    for record in entries:
        path = PROJECT/record['path']
        assert path.stat().st_size == record['size_bytes']
        with path.open('rb') as handle:
            assert hashlib.file_digest(handle, 'sha256').hexdigest() == record['sha256']
    remote('python3 -c ' + shlex.quote(REMOTE_CHECK))
    scp = ['scp', '-o', 'StrictHostKeyChecking=yes', '-o',
           f'UserKnownHostsFile={PROJECT}/evidence/native-linux-20260919/native-v2-known-hosts',
           '-o', 'ConnectTimeout=5']
    for record in entries:
        path = PROJECT/record['path']
        final = '/srv/s22/artifacts/' + path.name
        partial = final + '.part'
        remote(f'test ! -e {shlex.quote(final)} && test ! -e {shlex.quote(partial)}')
        print(f'Transferring {path.name}: {record["size_bytes"]} bytes', flush=True)
        subprocess.run(scp + [str(path), 'root@10.55.0.2:'+partial], check=True)
        remote('set -eu; ' +
               f'echo {shlex.quote(record["sha256"]+"  "+partial)} | sha256sum -c -; ' +
               f'chmod 600 {shlex.quote(partial)}; mv {shlex.quote(partial)} {shlex.quote(final)}')
    remote('set -eu; '
           'tar --numeric-owner --xattrs --acls -xpf /srv/s22/artifacts/persistent-arch-20260920.tar.gz -C /srv/s22/arch; '
           'tar --numeric-owner --xattrs --acls -xpf /srv/s22/artifacts/persistent-arch-fixup-20260920.tar.gz -C /srv/s22/arch; '
           'chroot /srv/s22/arch /usr/bin/ldconfig; '
           'mkdir -p /srv/s22/model-bench/models /srv/s22/model-bench/server; '
           'mv /srv/s22/artifacts/Qwen3.5-2B-Q4_0.gguf /srv/s22/model-bench/models/Qwen3.5-2B-Q4_0.gguf; '
           'tar -xzf /srv/s22/artifacts/server-runtime.tar.gz -C /srv/s22/model-bench/server; sync')
    print('Files populated and synced. No boot hook or ready marker activated.', flush=True)


if __name__ == '__main__':
    main()
