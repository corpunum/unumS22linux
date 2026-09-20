#!/usr/bin/env python3
"""Phone-side acceptance of deployed files, before enabling desktop startup."""
import hashlib
import json
import os
from pathlib import Path
import subprocess

ROOT = Path('/srv/s22')
ARCH = ROOT/'arch'
MARKER = {
    'schema': 's22-persistent-v1',
    'uuid': '1dd55c26-bd57-489a-9d9b-4c60e6f430eb',
    'arch_archive_sha256': '363f836b6d45673a26e117493b034038d7b845c092c2883039bd8128cda67646',
    'model_sha256': 'cd70221bebaee0503e0f6717e174250cd7825aa88438b3aabec9ad55731d9bb1',
    'server_sha256': '3532573016a7d26a45179b9a108a11aaa3073f766cc6c94494e0b6f6098219a6',
    'chat_sha256': '1f8cf8949103c3771045c652d2b2ea607ecbd65e080e04139c30de0eb9b1bb16',
}


def main():
    assert Path('/proc/1/comm').read_text().strip() == 'native-guardian'
    mounts=[line.split() for line in Path('/proc/self/mountinfo').read_text().splitlines()]
    assert any(r[2]=='259:20' and r[4]==str(ROOT) and 'rw' in r[5].split(',') for r in mounts)
    assert MARKER['uuid'] in subprocess.check_output(['blkid','/dev/sda36'],text=True)
    for relative,key in (
        ('model-bench/models/Qwen3.5-2B-Q4_0.gguf','model_sha256'),
        ('model-bench/server/bin/llama-server','server_sha256'),
        ('arch/usr/local/bin/s22-chat','chat_sha256')):
        with (ROOT/relative).open('rb') as handle:
            assert hashlib.file_digest(handle,'sha256').hexdigest() == MARKER[key],relative
    aqua=ARCH/'opt/s22-aquamarine/libaquamarine.so.0.15.1'
    with aqua.open('rb') as handle:
        assert hashlib.file_digest(handle,'sha256').hexdigest() == '6fb3f4377ac3d14e4d74a18bdc5316e0acc23779e6894a1dab449cdb294d42ca'
    assert os.readlink(ARCH/'opt/s22-ui/shell') == '/opt/omarchy-source/shell'
    packages=list((ARCH/'var/lib/pacman/local').glob('*/files'))
    assert len(packages)==349,len(packages)
    checked=0
    missing=[]
    for package in packages:
        rows=package.read_text().splitlines()
        if '%FILES%' not in rows:
            continue
        for relative in rows[rows.index('%FILES%')+1:]:
            if not relative or relative.startswith('%'):
                break
            checked+=1
            if not os.path.lexists(ARCH/relative):
                missing.append(relative)
    assert not missing,missing[:30]
    check=subprocess.run(['chroot',str(ARCH),'/usr/bin/pacman','-Dk'],capture_output=True,text=True)
    assert check.returncode==0,check.stdout+check.stderr
    output=ROOT/'.persistent-ready.json'
    with output.open('x') as handle:
        json.dump(MARKER,handle,indent=2)
        handle.write('\n')
        handle.flush()
        os.fsync(handle.fileno())
    output.chmod(0o600)
    fd=os.open(ROOT,os.O_RDONLY|os.O_DIRECTORY)
    os.fsync(fd)
    os.close(fd)
    print(json.dumps({'model_server_chat_aquamarine_hashes_match':True,
                      'registered_packages':len(packages),'registered_paths_present':checked,
                      'pacman_database_check':check.stdout.strip(),
                      'deployment_marker_created':True,'boot_accepted':False},indent=2))


if __name__ == '__main__':
    main()
