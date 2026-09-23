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
import os
from pathlib import Path
import shlex
import stat
import subprocess

ROOT=Path(__file__).resolve().parents[2]
BEFORE='1c1b77a5e532e50b8274cfc68921aa9b1bfe6d4ae9a3459281be0cc033c5c3d5'
AFTER='758fc9d30491e17b7c829a89d338ba69476efa15a1280deb8a1b9b8009687f4b'
DIRECTORY='/srv/s22/audio-extra-v2-20260922'
LINEAGE_SHA='b5bf01c4a47091eb95078fc69b133b44c2b453b31c23433594c5b605e3747b55'


def fail(message):
    raise ValueError(message)


def read_regular_file(path, label, root):
    """Read a repository artifact without following a final symlink."""
    path=Path(path)
    try:
        metadata=path.lstat()
        resolved=path.resolve(strict=True)
    except OSError as error:
        fail(f'{label} is missing or unreadable: {path}: {error}')
    if not stat.S_ISREG(metadata.st_mode):
        fail(f'{label} must be a non-symlink regular file: {path}')
    try:
        resolved.relative_to(Path(root).resolve(strict=True))
    except (OSError, ValueError):
        fail(f'{label} is outside the repository root: {resolved}')
    flags=os.O_RDONLY|getattr(os,'O_CLOEXEC',0)|getattr(os,'O_NOFOLLOW',0)
    try:
        fd=os.open(path,flags)
    except OSError as error:
        fail(f'{label} could not be opened without following symlinks: {error}')
    try:
        info=os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or (info.st_dev,info.st_ino)!=(metadata.st_dev,metadata.st_ino):
            fail(f'{label} changed during validation: {path}')
        chunks=[]
        while True:
            chunk=os.read(fd,1024*1024)
            if not chunk:
                break
            chunks.append(chunk)
        return b''.join(chunks)
    finally:
        os.close(fd)


def validate_artifacts(image_path, rollback_path, lineage_path, *, size,
                       candidate_sha, rollback_sha, lineage_sha, manifest_path,
                       expected_manifest_image, root):
    """Validate every payload before any stage/flash subprocess can run."""
    image=read_regular_file(image_path,'candidate recovery image',root)
    rollback=read_regular_file(rollback_path,'rollback recovery image',root)
    lineage=read_regular_file(lineage_path,'LineageOS rollback image',root)
    manifest_bytes=read_regular_file(manifest_path,'candidate build manifest',root)
    try:
        manifest=json.loads(manifest_bytes)
    except (TypeError,ValueError) as error:
        fail(f'candidate build manifest is not valid JSON: {error}')
    if not isinstance(manifest,dict):
        fail('candidate build manifest must be a JSON object')
    if manifest.get('image')!=expected_manifest_image:
        fail('candidate build manifest names an unexpected image path')
    if manifest.get('phone_access') is not False:
        fail('candidate build manifest does not confirm host-only image construction')
    if manifest.get('extra_count')!=20:
        fail('candidate build manifest does not describe exactly 20 ABOX extras')
    if manifest.get('image_sha256')!=candidate_sha:
        fail('candidate build manifest image hash does not match the pinned review candidate')
    if manifest.get('base_image_sha256')!=rollback_sha:
        fail('candidate build manifest base-image hash does not match the rollback image')
    if len(image)!=size:
        fail(f'candidate recovery image has {len(image)} bytes; expected {size}')
    if len(rollback)!=size:
        fail(f'rollback recovery image has {len(rollback)} bytes; expected {size}')
    actual=hashlib.sha256(image).hexdigest()
    if actual!=candidate_sha:
        fail(f'candidate recovery image SHA-256 mismatch: {actual}')
    actual=hashlib.sha256(rollback).hexdigest()
    if actual!=rollback_sha:
        fail(f'rollback recovery image SHA-256 mismatch: {actual}')
    actual=hashlib.sha256(lineage).hexdigest()
    if actual!=lineage_sha:
        fail(f'LineageOS rollback image SHA-256 mismatch: {actual}')

    # Only the payload/AVB-derived fields may differ from the rollback image.
    mutable=set(range(16,20))|set(range(576,608))|set(range(1636,1644))
    if len(image)<2048 or len(rollback)<2048:
        fail('candidate or rollback image is too short for boot-header comparison')
    if any(image[index]!=rollback[index] for index in range(2048) if index not in mutable):
        fail('candidate changed immutable boot header bytes')
    return image

def main(argv=None, *, base_module=None):
    ap=argparse.ArgumentParser(description=__doc__);choice=ap.add_mutually_exclusive_group()
    choice.add_argument('--stage',action='store_true');choice.add_argument('--flash',action='store_true');args=ap.parse_args(argv)
    if base_module is None:
        spec=importlib.util.spec_from_file_location('audio_deployer',ROOT/'tools/hardware/deploy-audio-recovery.py')
        if spec is None or spec.loader is None:
            raise SystemExit('cannot load shared RECOVERY deployer safety gates')
        base=importlib.util.module_from_spec(spec);spec.loader.exec_module(base)
    else:
        base=base_module
    image=validate_artifacts(
        ROOT/'builds/audio-extra-v2-20260922/recovery.img',
        ROOT/'builds/audio-early-20260922/recovery.img',
        ROOT/'lineage/build-20260915/recovery.img',
        size=base.SIZE,candidate_sha=AFTER,rollback_sha=BEFORE,
        lineage_sha=LINEAGE_SHA,
        manifest_path=ROOT/'builds/audio-extra-v2-20260922/manifest.json',
        expected_manifest_image='builds/audio-extra-v2-20260922/recovery.img',root=ROOT,
    )
    if not (args.stage or args.flash):
        print(json.dumps({'partition':'recovery','before':BEFORE,'candidate':AFTER,'directory':DIRECTORY,'execute':False}));return
    mode='stage' if args.stage else 'flash'
    receipt_path=ROOT/'rootfs/main-driver-loop-20260921'/('audio-extra-recovery-'+mode+'.json')
    base.ensure_new_receipt(receipt_path)
    ssh=ROOT/'tools/s22-ssh'
    try:
        base.validate_approved_ssh_wrapper(ssh)
    except ValueError as error:
        raise SystemExit(str(error)) from error
    # Render the candidate/rollback hashes and staging paths from this
    # deployment's explicit manifest; no broad edits to a baked remote script.
    code=base.render_remote(
        base_sha=BEFORE,new_sha=AFTER,staging_directory=DIRECTORY,
        rollback_filename='audio-early-rollback.img',
    )
    try:
        result=subprocess.run([str(ssh),'python3 -c '+shlex.quote(code)+' '+mode],
                              input=image if args.stage else b'',capture_output=True,timeout=100)
    except (OSError,subprocess.TimeoutExpired) as error:
        raise RuntimeError(mode+' transport failed or timed out; remote outcome may be unknown; inspect before retry: '+str(error)) from error
    if result.returncode:
        detail=result.stderr.decode(errors='replace')
        raise RuntimeError(mode+' failed; no reboot was requested; inspect write outcome before any retry: '+detail)
    try:
        receipt=json.loads(result.stdout)
        base.validate_remote_receipt(receipt,mode=mode,before_sha=BEFORE,
                                     candidate_sha=AFTER,size=base.SIZE)
    except (TypeError,ValueError) as error:
        raise RuntimeError(mode+' returned an invalid receipt; operation outcome must be independently checked before retry: '+str(error)) from error
    try:
        with receipt_path.open('x') as stream:
            json.dump(receipt,stream,indent=2)
            stream.write('\n')
    except OSError as error:
        raise RuntimeError(mode+' succeeded remotely but local receipt could not be persisted; inspect device state before retry: '+str(error)) from error
    print(json.dumps(receipt,indent=2))

if __name__=='__main__':main()
