#!/usr/bin/env python3
"""Stage, or explicitly write, the reviewed audio-only RECOVERY candidate.

Never reboots. The only block-device write is the separately selected --flash
operation on exact recovery/sda16 (259:0), after old/new full-image hash checks.
No BOOT, MISC, EFS, identity, PIT, bootloader or TrustZone access.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import stat
import subprocess

ROOT = Path(__file__).resolve().parents[2]
SIZE = 100663296
BASE_SHA = '1a827b43d29141efb47f530902dd4e4ee2b6d780515893c9ecd676ad27efd7d1'
NEW_SHA = '1c1b77a5e532e50b8274cfc68921aa9b1bfe6d4ae9a3459281be0cc033c5c3d5'
LINEAGE_SHA = 'b5bf01c4a47091eb95078fc69b133b44c2b453b31c23433594c5b605e3747b55'
IMAGE = ROOT/'builds/audio-early-20260922/recovery.img'

# Kept separately so the exact identity gate can be exercised in host-only
# subprocesses under ordinary Python, -O, and PYTHONOPTIMIZE=1.
REMOTE_GUARDS = r'''
def require(condition, message):
 if not condition:
  raise RuntimeError(message)

def validate_size(actual, expected, label):
 require(actual==expected, label+' has incorrect size/capacity')

def validate_digest(data, expected, label):
 actual=hashlib.sha256(data).hexdigest()
 require(actual==expected,label+' SHA-256 mismatch')

def read_all(fd, expected_size, reader, label):
 data=bytearray()
 while len(data)<expected_size:
  chunk=reader(fd,min(expected_size-len(data),1048576))
  require(bool(chunk),'short '+label+' read')
  data.extend(chunk)
 validate_size(len(data),expected_size,label)
 return bytes(data)

def write_all_fd(fd, content, writer, flush, label):
 offset=0
 while offset<len(content):
  try:
   count=writer(fd,content[offset:offset+1048576],offset)
  except OSError as error:
   raise RuntimeError(label+' failed after %d bytes; operation outcome may be partial: %s'%(offset,error)) from error
  require(count>0,label+' made no progress after %d bytes; outcome may be partial'%offset)
  offset+=count
 try:
  flush(fd)
 except OSError as error:
  raise RuntimeError(label+' fsync failed after the write; outcome may be partial: '+str(error)) from error
 return offset

def validate_free_space(available_bytes, available_inodes, image_size):
 require(available_bytes>=image_size*2+1048576,
         'insufficient free staging bytes for candidate, rollback, and filesystem overhead')
 require(available_inodes>=3,'insufficient free staging inodes for directory, candidate, and rollback')

def validate_recovery_target(pid1, kernel, temperature, resolved_node,
                             has_partition_tag, sectors, is_block, rdev,
                             mounted, expected_rdev, expected_sectors):
 require(pid1=='native-guardian', 'unexpected PID 1; refusing RECOVERY operation')
 require(kernel=='5.10.260-g4e5c5ad7d950', 'unexpected running kernel; refusing RECOVERY operation')
 require(0<=temperature<420, 'battery temperature is unsafe or unreadable')
 require(resolved_node=='/dev/sda16', 'recovery alias does not resolve to /dev/sda16')
 require(has_partition_tag, 'sda16 is not labeled PARTNAME=recovery')
 validate_size(sectors,expected_sectors,'RECOVERY partition in sysfs')
 require(is_block and rdev==expected_rdev, 'RECOVERY target is not exact block device 259:0')
 require(not mounted, 'RECOVERY target is mounted; refusing write')
'''

REMOTE_TEMPLATE = r'''
import fcntl,hashlib,json,os,pathlib,stat,struct,sys
p=pathlib.Path
size=100663296
base_sha='__S22_BASE_SHA__'
new_sha='__S22_NEW_SHA__'
''' + REMOTE_GUARDS + r'''
mode=sys.argv[1]
require(mode in ('stage','flash'), 'unknown operation')
require(os.geteuid()==0, 'RECOVERY operation requires root')
lockfd=os.open('/run/s22-recovery-operation.lock',os.O_CREAT|os.O_RDWR|os.O_CLOEXEC|os.O_NOFOLLOW,0o600)
lock_info=os.fstat(lockfd)
require(stat.S_ISREG(lock_info.st_mode) and lock_info.st_uid==0,
        'RECOVERY operation lock is not a root-owned regular file')
try:
 fcntl.flock(lockfd,fcntl.LOCK_EX|fcntl.LOCK_NB)
except BlockingIOError as error:
 os.close(lockfd)
 raise RuntimeError('another RECOVERY operation owns the device lock') from error
expected_rdev=os.makedev(259,0)
alias=p('/dev/block/by-name/recovery')
node=p('/dev/sda16')
resolved=str(alias.resolve(strict=True))
uevent=p('/sys/class/block/sda16/uevent').read_text().splitlines()
sectors=int(p('/sys/class/block/sda16/size').read_text())
info=node.stat()
mount_entries=p('/proc/self/mountinfo').read_text().splitlines()
mounted=any(len(line.split())>2 and line.split()[2]=='259:0' for line in mount_entries)
validate_recovery_target(
 p('/proc/1/comm').read_text().strip(),
 p('/proc/sys/kernel/osrelease').read_text().strip(),
 int(p('/sys/class/power_supply/battery/temp').read_text()),
 resolved,'PARTNAME=recovery' in uevent,sectors,
 stat.S_ISBLK(info.st_mode),info.st_rdev,mounted,expected_rdev,196608)
require(not node.is_symlink(), '/dev/sda16 unexpectedly became a symlink')
parent=p('/srv/s22')
parent_info=parent.lstat()
require(stat.S_ISDIR(parent_info.st_mode) and parent_info.st_uid==0 and not parent.is_symlink(),
        'staging parent is not a root-owned real directory')

def check_fd(fd):
 opened=os.fstat(fd)
 require(stat.S_ISBLK(opened.st_mode) and opened.st_rdev==expected_rdev,
         'opened RECOVERY fd identity changed; refusing operation')
 capacity=struct.unpack('<Q',fcntl.ioctl(fd,0x80081272,b'\0'*8))[0]
 validate_size(capacity,size,'opened RECOVERY fd')

def read_recovery():
 fd=os.open(node,os.O_RDONLY|os.O_CLOEXEC|os.O_NOFOLLOW)
 try:
  check_fd(fd)
  return read_all(fd,size,os.read,'RECOVERY partition')
 finally:
  os.close(fd)

old=read_recovery()
validate_digest(old,base_sha,'recovery baseline; no write')
directory=p('__S22_STAGING_DIRECTORY__')
require(directory.parent==parent,'staging directory escaped the approved parent')
candidate=directory/'recovery.img'
backup=directory/'__S22_ROLLBACK_FILENAME__'
os.umask(0o077)

def read_root_file(path,label,want_sha):
 require(not path.is_symlink() and path.is_file(),label+' is missing or is a symlink')
 fd=os.open(path,os.O_RDONLY|os.O_CLOEXEC|os.O_NOFOLLOW)
 try:
  metadata=os.fstat(fd)
  require(stat.S_ISREG(metadata.st_mode) and metadata.st_uid==0,
          label+' is not a root-owned regular file')
  validate_size(metadata.st_size,size,label)
  data=read_all(fd,size,os.read,label)
 finally:
  os.close(fd)
 validate_digest(data,want_sha,label)
 return data

def write_all(path,content):
 fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_CLOEXEC|os.O_NOFOLLOW,0o600)
 try:
  write_all_fd(fd,content,lambda descriptor,chunk,offset:os.write(descriptor,chunk),
               os.fsync,'staging write')
 finally:
  os.close(fd)

if mode=='stage':
 data=sys.stdin.buffer.read(size+1)
 validate_size(len(data),size,'staged candidate')
 validate_digest(data,new_sha,'staged candidate')
 space=os.statvfs(parent)
 validate_free_space(space.f_bavail*space.f_frsize,space.f_favail,size)
 try:
  directory.mkdir(mode=0o700,exist_ok=False)
  metadata=directory.lstat()
  require(stat.S_ISDIR(metadata.st_mode) and metadata.st_uid==0 and not directory.is_symlink(),
          'staging directory is not a new root-owned directory')
  write_all(backup,old)
  write_all(candidate,data)
  dfd=os.open(directory,os.O_RDONLY|os.O_DIRECTORY|os.O_CLOEXEC|os.O_NOFOLLOW)
  try: os.fsync(dfd)
  finally: os.close(dfd)
  staged_backup=read_root_file(backup,'rollback copy',base_sha)
  staged_candidate=read_root_file(candidate,'staged candidate',new_sha)
  require(len(staged_backup)==size and len(staged_candidate)==size,
          'staged file size mismatch; no flash permitted')
 except Exception as error:
  raise RuntimeError('staging did not complete; inspect the partial staging directory and do not flash: '+str(error)) from error
 print(json.dumps({'mode':mode,'partition_written':False,'backup_sha256':base_sha,'candidate_sha256':new_sha}))
elif mode=='flash':
 metadata=directory.lstat()
 require(stat.S_ISDIR(metadata.st_mode) and metadata.st_uid==0 and
         not directory.is_symlink() and (metadata.st_mode&0o077)==0,
         'staging directory is not a private root-owned directory')
 data=read_root_file(candidate,'staged candidate',new_sha)
 rollback=read_root_file(backup,'rollback copy',base_sha)
 validate_size(len(data),size,'staged candidate')
 validate_size(len(rollback),size,'rollback copy')
 fd=os.open(node,os.O_RDWR|os.O_CLOEXEC|os.O_NOFOLLOW)
 try:
  check_fd(fd)
  write_all_fd(fd,data,os.pwrite,os.fsync,'RECOVERY write; do not retry or reboot')
 finally:
  os.close(fd)
 readback=read_recovery()
 validate_digest(readback,new_sha,'RECOVERY readback; do not reboot')
 actual=hashlib.sha256(readback).hexdigest()
print(json.dumps({'mode':mode,'partition_written':'recovery','bytes':size,
  'before_sha256':base_sha,'readback_sha256':actual,'reboot_performed':False}))
'''


def render_remote(*, base_sha, new_sha, staging_directory, rollback_filename):
    """Render the embedded, fail-closed operation with one explicit manifest."""
    for label, digest in (("base", base_sha), ("candidate", new_sha)):
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError(f"{label} recovery SHA-256 must be 64 lowercase hex characters")
    staging = Path(staging_directory)
    if (not staging.is_absolute() or staging.parent != Path("/srv/s22")
            or staging.name in ("", ".", "..")
            or not re.fullmatch(r"[A-Za-z0-9_.-]+", staging.name)):
        raise ValueError("staging directory must be one safe child of /srv/s22")
    if (Path(rollback_filename).name != rollback_filename
            or not re.fullmatch(r"[A-Za-z0-9_.-]+", rollback_filename)):
        raise ValueError("rollback filename must be a safe basename")
    values = {
        "__S22_BASE_SHA__": base_sha,
        "__S22_NEW_SHA__": new_sha,
        "__S22_STAGING_DIRECTORY__": str(staging),
        "__S22_ROLLBACK_FILENAME__": rollback_filename,
    }
    rendered = REMOTE_TEMPLATE
    for marker, value in values.items():
        if rendered.count(marker) != 1:
            raise RuntimeError(f"remote template marker must occur exactly once: {marker}")
        rendered = rendered.replace(marker, value)
    if "__S22_" in rendered:
        raise RuntimeError("remote template has unresolved deployment markers")
    compile(rendered, "embedded-recovery-deployer", "exec")
    return rendered


REMOTE = render_remote(
    base_sha=BASE_SHA, new_sha=NEW_SHA,
    staging_directory="/srv/s22/audio-early-20260922",
    rollback_filename="native-v3-rollback.img",
)


def read_host_artifact(path, label):
    path=Path(path)
    try:
        metadata=path.lstat()
    except OSError as error:
        raise ValueError(f'{label} is missing or unreadable: {path}: {error}') from error
    if not stat.S_ISREG(metadata.st_mode):
        raise ValueError(f'{label} must be a non-symlink regular file: {path}')
    flags=os.O_RDONLY|getattr(os,'O_CLOEXEC',0)|getattr(os,'O_NOFOLLOW',0)
    fd=os.open(path,flags)
    try:
        opened=os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode) or (opened.st_dev,opened.st_ino)!=(metadata.st_dev,metadata.st_ino):
            raise ValueError(f'{label} changed during validation: {path}')
        data=bytearray()
        while True:
            chunk=os.read(fd,1024*1024)
            if not chunk: break
            data.extend(chunk)
        return bytes(data)
    finally:
        os.close(fd)


def validate_host_artifacts(image_path, base_path, lineage_path):
    image=read_host_artifact(image_path,'candidate image')
    base=read_host_artifact(base_path,'rollback/base image')
    lineage=read_host_artifact(lineage_path,'known-good LineageOS recovery image')
    if len(image)!=SIZE or hashlib.sha256(image).hexdigest()!=NEW_SHA:
        raise ValueError('candidate image has an incorrect size or SHA-256')
    if len(base)!=SIZE or hashlib.sha256(base).hexdigest()!=BASE_SHA:
        raise ValueError('rollback/base image has an incorrect size or SHA-256')
    if hashlib.sha256(lineage).hexdigest()!=LINEAGE_SHA:
        raise ValueError('known-good LineageOS recovery image SHA-256 mismatch')
    return image


def ensure_new_receipt(path):
    path=Path(path)
    if path.exists() or path.is_symlink():
        raise ValueError(f'refusing to overwrite existing deployment receipt: {path}')
    return path


def validate_remote_receipt(receipt, *, mode, before_sha, candidate_sha, size):
    if not isinstance(receipt,dict) or receipt.get('mode')!=mode:
        raise ValueError('remote deployment receipt has an invalid mode or shape')
    if mode=='stage':
        expected={'partition_written':False,'backup_sha256':before_sha,
                  'candidate_sha256':candidate_sha}
    elif mode=='flash':
        expected={'partition_written':'recovery','bytes':size,
                  'before_sha256':before_sha,'readback_sha256':candidate_sha,
                  'reboot_performed':False}
    else:
        raise ValueError(f'unsupported receipt mode: {mode}')
    for key,value in expected.items():
        if receipt.get(key)!=value:
            raise ValueError(f'remote deployment receipt mismatch for {key}')
    return receipt


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    choice=parser.add_mutually_exclusive_group()
    choice.add_argument('--stage',action='store_true')
    choice.add_argument('--flash',action='store_true')
    args=parser.parse_args(argv)
    try:
        image=validate_host_artifacts(
            IMAGE,ROOT/'builds/native_handoff_v3.img',
            ROOT/'lineage/build-20260915/recovery.img')
    except (OSError,ValueError) as error:
        raise SystemExit(f'host artifact validation failed; no device operation attempted: {error}') from error
    if not(args.stage or args.flash):
        print(json.dumps({'partition':'recovery','bytes':SIZE,'before':BASE_SHA,
                          'candidate':NEW_SHA,'execution':False},indent=2))
        return 0
    mode='stage' if args.stage else 'flash'
    out=ROOT/'rootfs/main-driver-loop-20260921'/('audio-recovery-'+mode+'.json')
    try:
        ensure_new_receipt(out)
    except ValueError as error:
        raise SystemExit(str(error)) from error
    ssh=ROOT/'tools/s22-ssh'
    if not ssh.is_file() or not os.access(ssh,os.X_OK):
        raise SystemExit(f'approved SSH wrapper is missing or not executable: {ssh}')
    command='python3 -c '+shlex.quote(REMOTE)+' '+mode
    try:
        result=subprocess.run([str(ssh),command],input=image if args.stage else b'',
                              capture_output=True,timeout=100)
    except (OSError,subprocess.TimeoutExpired) as error:
        raise RuntimeError(mode+' transport failed or timed out; remote outcome may be unknown; inspect before retry: '+str(error)) from error
    if result.returncode:
        detail=result.stderr.decode(errors='replace')
        raise RuntimeError(mode+' failed; no reboot was requested; inspect write outcome before any retry: '+detail)
    try:
        receipt=json.loads(result.stdout)
        validate_remote_receipt(receipt,mode=mode,before_sha=BASE_SHA,
                                candidate_sha=NEW_SHA,size=SIZE)
    except (TypeError,ValueError) as error:
        raise RuntimeError(mode+' returned an invalid receipt; operation outcome must be independently checked before retry: '+str(error)) from error
    try:
        with out.open('x') as stream:
            json.dump(receipt,stream,indent=2)
            stream.write('\n')
    except OSError as error:
        raise RuntimeError(mode+' succeeded remotely but local receipt could not be persisted; inspect device state before retry: '+str(error)) from error
    print(json.dumps(receipt,indent=2))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
