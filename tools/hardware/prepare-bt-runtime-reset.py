#!/usr/bin/env python3
"""Create private Linux BT identity and compile the reset/readback candidate.

No device operations. Identity is ordinary Linux configuration, never factory
identity or a protected-partition read. Existing identity is never overwritten.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import secrets
import subprocess

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('runtime',Path(__file__).with_name('build-bt-runtime-nvm.py'))
runtime=importlib.util.module_from_spec(spec);spec.loader.exec_module(runtime)

def main():
    os.umask(0o077)
    private=ROOT/'rootfs/bt-linux-config-20260922'
    private.mkdir(mode=0o700,exist_ok=True)
    identity=private/'address.json'
    if not identity.exists() and not identity.is_symlink():
        display=b'\x22\x22'+secrets.token_bytes(4)
        runtime.write_new(identity,(json.dumps({'source':'linux-generated',
            'display_address':':'.join(f'{b:02x}' for b in display)})+'\n').encode())
    runtime.check_private_input(identity)
    runtime.parse_address(json.loads(identity.read_text()))
    subprocess.run(['python3',str(ROOT/'tools/hardware/build-bt-runtime-nvm.py'),
                    '--address-file',str(identity)],check=True)
    source=ROOT/'tools/hardware/bt-qca6490-runtime-reset.c'
    binary=runtime.OUT/'bt-qca6490-runtime-reset'
    subprocess.run(['aarch64-linux-gnu-gcc','-static','-std=c11','-Wall','-Wextra','-Werror',
                    '-O2',str(source),'-o',str(binary)],check=True)
    binary.chmod(0o700)
    receipt={'binary_sha256':hashlib.sha256(binary.read_bytes()).hexdigest(),
             'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
             'identity_source':'linux-generated','device_operations':False}
    runtime.write_new(runtime.OUT/'reset-build.json',(json.dumps(receipt,indent=2)+'\n').encode())
    print(json.dumps(receipt,indent=2))

if __name__=='__main__':main()
