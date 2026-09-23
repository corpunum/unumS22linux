#!/usr/bin/env python3
"""Stage, or explicitly write, the reviewed audio-only RECOVERY candidate.

Never reboots. The only block-device write is the separately selected --flash
operation on exact recovery/sda16 (259:0), after old/new full-image hash checks.
No BOOT, MISC, EFS, identity, PIT, bootloader or TrustZone access.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import stat
import subprocess

ROOT = Path(__file__).resolve().parents[2]
# Exact reviewed tools/s22-ssh contents; updates require renewed source review.
APPROVED_SSH_WRAPPER_SHA256 = '7e9d31035762de50ccc6c5614d8348532fd912bf59c41c9d410a4c7bfe49dd1d'
# This root-owned directory is verified before it is the only SSH PATH entry.
TRUSTED_SSH_BIN_DIR = Path('/usr/bin')
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

def validate_directory_metadata(info, label, expected_uid, private=False):
 require(stat.S_ISDIR(info.st_mode),label+' is not a real directory')
 require(info.st_uid==expected_uid,label+' is not owned by the expected uid')
 mask=0o077 if private else 0o022
 require((info.st_mode&mask)==0,
         label+(' is accessible by group/other users' if private else ' is writable by group/other users'))

def require_same_directory(opened, current, label):
 require((opened.st_dev,opened.st_ino)==(current.st_dev,current.st_ino),
         label+' path was replaced during validation')

def open_parent_directory(path, label, expected_uid=0):
 try:
  before=os.lstat(path)
 except OSError as error:
  raise RuntimeError(label+' is unavailable: '+str(error)) from error
 validate_directory_metadata(before,label,expected_uid,private=False)
 flags=os.O_RDONLY|os.O_CLOEXEC|os.O_DIRECTORY|os.O_NOFOLLOW
 try:
  fd=os.open(path,flags)
 except OSError as error:
  raise RuntimeError(label+' could not be opened without following symlinks: '+str(error)) from error
 try:
  opened=os.fstat(fd)
  validate_directory_metadata(opened,label,expected_uid,private=False)
  require_same_directory(opened,before,label)
  return fd
 except Exception:
  os.close(fd)
  raise

def verify_child_directory(parent_fd, name, child_fd, label, expected_uid=0):
 try:
  current=os.stat(name,dir_fd=parent_fd,follow_symlinks=False)
 except OSError as error:
  raise RuntimeError(label+' path is unavailable under its approved parent: '+str(error)) from error
 opened=os.fstat(child_fd)
 validate_directory_metadata(opened,label,expected_uid,private=True)
 validate_directory_metadata(current,label,expected_uid,private=True)
 require_same_directory(opened,current,label)

def open_child_directory(parent_fd, name, label, create=False, expected_uid=0):
 if create:
  os.mkdir(name,mode=0o700,dir_fd=parent_fd)
 try:
  current=os.stat(name,dir_fd=parent_fd,follow_symlinks=False)
 except OSError as error:
  raise RuntimeError(label+' is unavailable under its approved parent: '+str(error)) from error
 validate_directory_metadata(current,label,expected_uid,private=True)
 flags=os.O_RDONLY|os.O_CLOEXEC|os.O_DIRECTORY|os.O_NOFOLLOW
 try:
  fd=os.open(name,flags,dir_fd=parent_fd)
 except OSError as error:
  raise RuntimeError(label+' could not be opened safely under its approved parent: '+str(error)) from error
 try:
  verify_child_directory(parent_fd,name,fd,label,expected_uid)
  return fd
 except Exception:
  os.close(fd)
  raise

def sync_directory(fd, label, flush=os.fsync):
 try:
  flush(fd)
 except OSError as error:
  raise RuntimeError(label+' fsync failed; staging durability is not established: '+str(error)) from error

def create_durable_staging_directory(parent_fd, name, label, expected_uid=0, flush=os.fsync):
 fd=open_child_directory(parent_fd,name,label,create=True,expected_uid=expected_uid)
 try:
  sync_directory(parent_fd,label+' parent',flush)
  return fd
 except Exception:
  os.close(fd)
  raise

def read_staged_file(directory_fd, name, label, expected_sha, expected_size, expected_uid=0):
 fd=os.open(name,os.O_RDONLY|os.O_CLOEXEC|os.O_NOFOLLOW,dir_fd=directory_fd)
 try:
  metadata=os.fstat(fd)
  require(stat.S_ISREG(metadata.st_mode) and metadata.st_uid==expected_uid,
          label+' is not an expected-owner regular file')
  require((metadata.st_mode&0o077)==0,label+' permissions expose staged data')
  validate_size(metadata.st_size,expected_size,label)
  data=read_all(fd,expected_size,os.read,label)
 finally:
  os.close(fd)
 validate_digest(data,expected_sha,label)
 return data

def write_staged_file(directory_fd, name, content, label, expected_uid=0):
 fd=os.open(name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_CLOEXEC|os.O_NOFOLLOW,
            0o600,dir_fd=directory_fd)
 try:
  metadata=os.fstat(fd)
  require(stat.S_ISREG(metadata.st_mode) and metadata.st_uid==expected_uid,
          label+' was not created as an expected-owner regular file')
  require((metadata.st_mode&0o077)==0,label+' permissions expose staged data')
  write_all_fd(fd,content,lambda descriptor,chunk,offset:os.write(descriptor,chunk),
               os.fsync,label)
 finally:
  os.close(fd)

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
parent_fd=open_parent_directory(parent,'staging parent')

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
directory_name='__S22_STAGING_DIRECTORY_NAME__'
candidate_name='recovery.img'
backup_name='__S22_ROLLBACK_FILENAME__'
os.umask(0o077)

if mode=='stage':
 data=sys.stdin.buffer.read(size+1)
 validate_size(len(data),size,'staged candidate')
 validate_digest(data,new_sha,'staged candidate')
 space=os.fstatvfs(parent_fd)
 validate_free_space(space.f_bavail*space.f_frsize,space.f_favail,size)
 try:
  dfd=create_durable_staging_directory(parent_fd,directory_name,'staging directory')
  try:
   write_staged_file(dfd,backup_name,old,'rollback copy')
   write_staged_file(dfd,candidate_name,data,'staged candidate')
   sync_directory(dfd,'staging directory')
   verify_child_directory(parent_fd,directory_name,dfd,'staging directory')
   staged_backup=read_staged_file(dfd,backup_name,'rollback copy',base_sha,size)
   staged_candidate=read_staged_file(dfd,candidate_name,'staged candidate',new_sha,size)
   verify_child_directory(parent_fd,directory_name,dfd,'staging directory')
  finally:
   os.close(dfd)
  require(len(staged_backup)==size and len(staged_candidate)==size,
          'staged file size mismatch; no flash permitted')
 except Exception as error:
  raise RuntimeError('staging did not complete; inspect the partial staging directory and do not flash: '+str(error)) from error
 print(json.dumps({'mode':mode,'partition_written':False,'backup_sha256':base_sha,'candidate_sha256':new_sha}))
elif mode=='flash':
 dfd=open_child_directory(parent_fd,directory_name,'staging directory')
 try:
  data=read_staged_file(dfd,candidate_name,'staged candidate',new_sha,size)
  rollback=read_staged_file(dfd,backup_name,'rollback copy',base_sha,size)
  verify_child_directory(parent_fd,directory_name,dfd,'staging directory')
 finally:
  os.close(dfd)
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
        "__S22_STAGING_DIRECTORY_NAME__": staging.name,
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


def validate_approved_ssh_wrapper(path):
    """Return an immutable snapshot of the approved Bash wrapper's opened inode."""
    path=Path(path)
    try:
        before=path.lstat()
    except OSError as error:
        raise ValueError(f'approved SSH wrapper is unavailable: {path}: {error}') from error
    if not stat.S_ISREG(before.st_mode) or not (before.st_mode&0o111):
        raise ValueError(f'approved SSH wrapper must be a non-symlink executable regular file: {path}')
    try:
        source_fd=os.open(path,os.O_RDONLY|getattr(os,'O_CLOEXEC',0)|getattr(os,'O_NOFOLLOW',0))
    except OSError as error:
        raise ValueError(f'approved SSH wrapper could not be opened safely: {path}: {error}') from error
    snapshot_fd=None
    try:
        opened=os.fstat(source_fd)
        if (not stat.S_ISREG(opened.st_mode) or not (opened.st_mode&0o111)
                or (opened.st_dev,opened.st_ino)!=(before.st_dev,before.st_ino)):
            raise ValueError(f'approved SSH wrapper changed during validation: {path}')
        if opened.st_size>1024*1024:
            raise ValueError(f'approved SSH wrapper is unexpectedly large: {path}')
        os.lseek(source_fd,0,os.SEEK_SET)
        first_line=os.read(source_fd,256).split(b'\n',1)[0]
        if first_line!=b'#!/usr/bin/env bash':
            raise ValueError(f'approved SSH wrapper is not the expected Bash script: {path}')
        os.lseek(source_fd,0,os.SEEK_SET)
        seal_names=('F_ADD_SEALS','F_GET_SEALS','F_SEAL_SEAL','F_SEAL_SHRINK','F_SEAL_GROW','F_SEAL_WRITE')
        if (not hasattr(os,'memfd_create') or not hasattr(os,'MFD_ALLOW_SEALING')
                or any(not hasattr(fcntl,name) for name in seal_names)):
            raise ValueError('host cannot pin the approved SSH wrapper in a sealed snapshot')
        snapshot_fd=os.memfd_create(
            's22-approved-ssh-wrapper',
            getattr(os,'MFD_CLOEXEC',0)|getattr(os,'MFD_ALLOW_SEALING',0))
        digest=hashlib.sha256()
        while True:
            block=os.read(source_fd,65536)
            if not block:
                break
            digest.update(block)
            offset=0
            while offset<len(block):
                count=os.write(snapshot_fd,block[offset:])
                if count<=0:
                    raise ValueError('could not complete the approved SSH wrapper snapshot')
                offset+=count
        after=os.fstat(source_fd)
        state=lambda info:(info.st_dev,info.st_ino,info.st_mode,info.st_size,info.st_mtime_ns,info.st_ctime_ns)
        if state(opened)!=state(after):
            raise ValueError(f'approved SSH wrapper contents changed during snapshot: {path}')
        if digest.hexdigest()!=APPROVED_SSH_WRAPPER_SHA256:
            raise ValueError(
                'approved SSH wrapper content SHA-256 mismatch: expected '
                +APPROVED_SSH_WRAPPER_SHA256+', got '+digest.hexdigest())
        current=path.lstat()
        if (not stat.S_ISREG(current.st_mode)
                or (current.st_dev,current.st_ino)!=(opened.st_dev,opened.st_ino)):
            raise ValueError(f'approved SSH wrapper pathname changed during snapshot: {path}')
        seals=(fcntl.F_SEAL_SEAL|fcntl.F_SEAL_SHRINK|fcntl.F_SEAL_GROW|fcntl.F_SEAL_WRITE)
        fcntl.fcntl(snapshot_fd,fcntl.F_ADD_SEALS,seals)
        if fcntl.fcntl(snapshot_fd,fcntl.F_GET_SEALS)&seals!=seals:
            raise ValueError('approved SSH wrapper snapshot could not be made immutable')
        os.lseek(snapshot_fd,0,os.SEEK_SET)
        snapshot_digest=hashlib.sha256()
        while True:
            block=os.read(snapshot_fd,65536)
            if not block:
                break
            snapshot_digest.update(block)
        if snapshot_digest.digest()!=digest.digest():
            raise ValueError('approved SSH wrapper snapshot content did not match the opened object')
        os.lseek(snapshot_fd,0,os.SEEK_SET)
        os.close(source_fd)
        return snapshot_fd
    except Exception as error:
        os.close(source_fd)
        if snapshot_fd is not None:
            os.close(snapshot_fd)
        if isinstance(error,ValueError):
            raise
        raise ValueError(f'approved SSH wrapper could not be pinned safely: {path}: {error}') from error


def build_approved_ssh_invocation(wrapper_fd, path, remote_command, project_root=ROOT):
    """Build a Bash source invocation that executes only the pinned wrapper fd."""
    fd_path=f'/proc/self/fd/{wrapper_fd}'
    launcher=(
        'dirname() {\n'
        ' if [[ $# -eq 2 && "$1" == "--" && ( "$2" == "/proc/self/fd/${S22_APPROVED_SSH_FD}" '
        '|| "$2" == "/dev/fd/${S22_APPROVED_SSH_FD}" ) ]]; then\n'
        '  printf "%s\\n" "${S22_APPROVED_PROJECT_ROOT}/tools"\n'
        ' else\n'
        '  command dirname "$@"\n'
        ' fi\n'
        '}\n'
        'source "$S22_APPROVED_SSH_FD_PATH" "$@"\n'
    )
    argv=['/bin/bash','-c',launcher,str(path),remote_command]
    environment=os.environ.copy()
    environment.pop('BASH_ENV',None)
    environment.pop('ENV',None)
    environment['PATH']=verified_ssh_path()
    environment['S22_APPROVED_SSH_FD']=str(wrapper_fd)
    environment['S22_APPROVED_SSH_FD_PATH']=fd_path
    environment['S22_APPROVED_PROJECT_ROOT']=str(Path(project_root))
    return argv,environment


def verified_ssh_path():
    """Use only the root-owned, non-writable system directory for ssh lookup."""
    directory=TRUSTED_SSH_BIN_DIR
    try:
        directory_info=directory.lstat()
        ssh_info=(directory/'ssh').lstat()
    except OSError as error:
        raise ValueError(f'trusted system SSH path is unavailable: {error}') from error
    if (not stat.S_ISDIR(directory_info.st_mode) or directory_info.st_uid!=0
            or (directory_info.st_mode&0o022)!=0):
        raise ValueError(f'trusted SSH directory is not root-owned and protected: {directory}')
    if (not stat.S_ISREG(ssh_info.st_mode) or ssh_info.st_uid!=0
            or not (ssh_info.st_mode&0o111) or (ssh_info.st_mode&0o022)!=0):
        raise ValueError(f'trusted SSH executable is not a protected root-owned file: {directory}/ssh')
    return str(directory)


def run_approved_ssh_wrapper(path, remote_command, *, input_data, timeout=100, project_root=ROOT):
    wrapper_fd=validate_approved_ssh_wrapper(path)
    try:
        argv,environment=build_approved_ssh_invocation(wrapper_fd,path,remote_command,project_root)
        return subprocess.run(argv,input=input_data,capture_output=True,timeout=timeout,
                              pass_fds=(wrapper_fd,),env=environment)
    finally:
        os.close(wrapper_fd)


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
    command='python3 -c '+shlex.quote(REMOTE)+' '+mode
    try:
        result=run_approved_ssh_wrapper(ssh,command,input_data=image if args.stage else b'',
                                       timeout=100,project_root=ROOT)
    except ValueError as error:
        raise SystemExit(str(error)) from error
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
