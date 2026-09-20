#!/usr/bin/env python3
"""Host-side private raw backup; never authorizes deletion or claims file restore.

Writes backup artifacts only beneath /home/corpunum/s22-private-backups.
Uses the existing pinned SSH host key. No phone files or partitions are written.
Partial files are retained on error, never silently overwritten or resumed.
"""
import datetime
import gzip
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import time


PROJECT = Path(__file__).resolve().parents[2]
BASE = Path('/home/corpunum/s22-private-backups')
ORDER = ('metadata', 'keystorage', 'keyrefuge', 'efs', 'sec_efs', 'boot',
         'vendor_boot', 'recovery', 'dtbo', 'vbmeta', 'vbmeta_system',
         'userdata', 'super', 'prism', 'optics')
ZERO_CHUNK = bytes(4 * 1024 * 1024)


def artifact_json(path, value):
    with path.open('x', encoding='utf-8') as handle:
        json.dump(value, handle, indent=2)
        handle.write('\n')
        handle.flush()
        os.fsync(handle.fileno())


def main():
    os.umask(0o077)
    BASE.mkdir(mode=0o700, exist_ok=True)
    info = BASE.lstat()
    if BASE.is_symlink() or BASE.resolve() != BASE or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise RuntimeError('Private backup base must be owned by this user, mode 0700, not a symlink')
    started = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    target = BASE / started
    target.mkdir(mode=0o700)
    print(f'BACKUP_DIRECTORY={target}', flush=True)
    source = (PROJECT / 'tools/persistence/read-partition.py').read_text()
    ssh = [str(PROJECT / 'tools/s22-ssh')]
    command = 'nice -n 20 python3 -c ' + shlex.quote(source)
    inventory = json.loads(subprocess.check_output(ssh + [command], timeout=30))
    parts = inventory['partitions']
    if set(parts) != set(ORDER):
        raise RuntimeError('Remote inventory differs from backup plan')
    required = sum(p['bytes'] for p in parts.values())
    if shutil.disk_usage(target).free < required + 20 * 1024 ** 3:
        raise RuntimeError('Insufficient host space including 20 GiB reserve')
    artifact_json(target / 'inventory.json', inventory)
    artifact_json(target / 'backup-scope.json', {
        'started_utc': started, 'source_helper_sha256': hashlib.sha256(source.encode()).hexdigest(),
        'total_bytes': required, 'order': ORDER, 'data_classification': 'PRIVATE_DEVICE_SNAPSHOT',
        'userdata_is_ciphertext': True, 'decrypted_file_backup': False,
        'wire_compression': 'gzip-level-1', 'zero_block_storage': 'sparse-file-holes',
        'file_restore_verified': False, 'permits_erasure': False,
        'limitations': 'Hardware/key dependencies and personal file restorability are not proven by raw hashes.',
    })
    records = []
    for name in ORDER:
        expected = parts[name]['bytes']
        partial = target / f'{name}.img.partial'
        log_path = target / f'{name}.source.jsonl'
        digest = hashlib.sha256()
        received = 0
        began = reported = time.monotonic()
        print(f'START {name} expected_bytes={expected}', flush=True)
        with log_path.open('xb') as source_log, partial.open('xb') as output:
            proc = subprocess.Popen(ssh + [command + ' --gzip --stream ' + shlex.quote(name)],
                                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=source_log)
            reader = gzip.GzipFile(fileobj=proc.stdout, mode='rb')
            try:
                while chunk := reader.read(4 * 1024 * 1024):
                    received += len(chunk)
                    if received > expected:
                        raise RuntimeError('Remote stream exceeds validated partition size')
                    if chunk == ZERO_CHUNK[:len(chunk)]:
                        output.seek(len(chunk), os.SEEK_CUR)
                    else:
                        output.write(chunk)
                    digest.update(chunk)
                    now = time.monotonic()
                    if now - reported >= 20:
                        print(f'PROGRESS {name} {received}/{expected} bytes '
                              f'{received / 1024**2 / (now-began):.1f} decoded MiB/s', flush=True)
                        reported = now
                result = proc.wait(timeout=30)
                if result or received != expected:
                    raise RuntimeError(f'{name}: source failed or short stream ({result}, {received}/{expected})')
                output.truncate(expected)
                output.flush()
                os.fsync(output.fileno())
            finally:
                reader.close()
                proc.stdout.close()
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait()
        events = [json.loads(line) for line in log_path.read_text().splitlines() if line.startswith('{')]
        done = [event for event in events if event.get('event') == 'complete']
        if len(done) != 1 or done[0]['partition'] != name or done[0]['bytes'] != expected or done[0]['sha256'] != digest.hexdigest():
            raise RuntimeError(f'{name}: source/transfer digest mismatch')
        print(f'HOST_REREAD {name}', flush=True)
        with partial.open('rb') as saved:
            disk_digest = hashlib.file_digest(saved, 'sha256').hexdigest()
        if disk_digest != digest.hexdigest():
            raise RuntimeError(f'{name}: saved-image digest mismatch')
        final = target / f'{name}.img'
        if final.exists():
            raise RuntimeError('Unexpected completed-image collision')
        partial.rename(final)
        final.chmod(0o400)
        record = {'partition': name, **parts[name], 'filename': final.name,
                  'sha256': disk_digest, 'source_hash_match': True,
                  'host_reread_hash_match': True, 'elapsed_seconds': round(time.monotonic()-began, 2)}
        artifact_json(target / f'{name}.verified.json', record)
        records.append(record)
        print(f'RAW_VERIFIED {name} bytes={received} sha256={disk_digest}', flush=True)
    artifact_json(target / 'raw-backup-complete.json', {
        'partitions': records, 'raw_transfer_and_host_reread_verified': True,
        'decrypted_file_backup': False, 'file_restore_verified': False, 'permits_erasure': False,
    })
    print(f'RAW_BACKUP_COMPLETE={target}', flush=True)
    print('DECRYPTED_FILE_RESTORE_NOT_VERIFIED; NO_ERASURE_AUTHORIZED_BY_THIS_RESULT', flush=True)


if __name__ == '__main__':
    main()
