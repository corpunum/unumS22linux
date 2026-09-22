#!/usr/bin/env python3
"""Stage/flash only the reviewed 20-extra ABOX RECOVERY candidate.

Uses the same exact recovery/sda16 identity, size, rollback and readback gates
as the previously tested deployer. Never reboots and never writes other
partitions. Default is a plan; stage and flash are separate explicit actions.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import shlex
import subprocess

ROOT=Path(__file__).resolve().parents[2]
BEFORE='1c1b77a5e532e50b8274cfc68921aa9b1bfe6d4ae9a3459281be0cc033c5c3d5'
AFTER='758fc9d30491e17b7c829a89d338ba69476efa15a1280deb8a1b9b8009687f4b'
DIRECTORY='/srv/s22/audio-extra-v2-20260922'

def main():
    ap=argparse.ArgumentParser(description=__doc__);choice=ap.add_mutually_exclusive_group()
    choice.add_argument('--stage',action='store_true');choice.add_argument('--flash',action='store_true');args=ap.parse_args()
    spec=importlib.util.spec_from_file_location('audio_deployer',ROOT/'tools/hardware/deploy-audio-recovery.py')
    base=importlib.util.module_from_spec(spec);spec.loader.exec_module(base)
    image=(ROOT/'builds/audio-extra-v2-20260922/recovery.img').read_bytes()
    rollback=(ROOT/'builds/audio-early-20260922/recovery.img').read_bytes()
    assert len(image)==len(rollback)==base.SIZE
    assert hashlib.sha256(image).hexdigest()==AFTER and hashlib.sha256(rollback).hexdigest()==BEFORE
    assert hashlib.sha256((ROOT/'lineage/build-20260915/recovery.img').read_bytes()).hexdigest()=='b5bf01c4a47091eb95078fc69b133b44c2b453b31c23433594c5b605e3747b55'
    # Validate the immutable header bytes independently of the builder.
    mutable=set(range(16,20))|set(range(576,608))|set(range(1636,1644))
    assert all(image[i]==rollback[i] for i in range(2048) if i not in mutable)
    if not (args.stage or args.flash):
        print(json.dumps({'partition':'recovery','before':BEFORE,'candidate':AFTER,'directory':DIRECTORY,'execute':False}));return
    mode='stage' if args.stage else 'flash'
    receipt_path=ROOT/'rootfs/main-driver-loop-20260921'/('audio-extra-recovery-'+mode+'.json')
    assert not receipt_path.exists()
    # Single-pass replacement: the current candidate was the previous new
    # image, and must now be the exact before/rollback image.
    code=base.REMOTE.replace(base.BASE_SHA,'BASE_SENTINEL').replace(base.NEW_SHA,AFTER).replace('BASE_SENTINEL',BEFORE)
    code=code.replace('/srv/s22/audio-early-20260922',DIRECTORY).replace('native-v3-rollback.img','audio-early-rollback.img')
    result=subprocess.run([str(ROOT/'tools/s22-ssh'),'python3 -c '+shlex.quote(code)+' '+mode],
                          input=image if args.stage else b'',capture_output=True,timeout=100)
    if result.returncode:raise RuntimeError(mode+' failed; do not reboot: '+result.stderr.decode(errors='replace'))
    receipt=json.loads(result.stdout)
    with receipt_path.open('x') as f:json.dump(receipt,f,indent=2);f.write('\n')
    print(json.dumps(receipt,indent=2))

if __name__=='__main__':main()
