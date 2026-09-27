#!/usr/bin/env python3
"""Prepare or explicitly run the pinned camera-module RECOVERY profile.

The default invocation is a host-only plan. Stage and flash are distinct,
one-use operations guarded by device-trial-guard; this adapter never reboots.
Its explicit CLI intent is not evidence of independent rescue or authorization
to run a phone trial.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
ARTIFACT_ROOT = Path('/home/corpunum/s22-linux')
PARTITION = 'recovery'
SIZE = 100663296
CAMERA_IMAGE = 'builds/camera-module-recovery-20260927/recovery.img'
CAMERA_MANIFEST = 'builds/camera-module-recovery-20260927/manifest.json'
BASELINE_IMAGE = 'builds/bt-hci-loader-compatible-20260924-repro/recovery.img'
PACKAGE_EVIDENCE = 'evidence/s22-camera-package-20260927.json'
CAMERA_IMAGE_SHA256 = 'b10412715756da3cc8ee221368b49f179cc0c64ab7bd2802976480905e6d8d2f'
CAMERA_MANIFEST_SHA256 = 'dc6970230542ab1ab391d2964ff64d88ecba150097de3a4d44f8dc856a1a00b8'
PACKAGE_EVIDENCE_SHA256 = '841e14b428e7832286cecb768cd14ad1305694f47c232ee8f6881a73978b3385'
BASELINE_IMAGE_SHA256 = '42da267f3dd9f94f30f62a95fb2ac13f91d4cf98f1a2307f7cc14e45d9c49be5'
CAMERA_MODULE_BUILD_ID = '59e54c032c545fff3ba52156f226fb6d69aadf64'
BASELINE_MODULE_BUILD_ID = '8286071582b5efedff0e0c6169ba1a23018fb814'
RUNNING_KERNEL_BUILD_ID = 'b2dda820b18d410d9bf12f1bd2584567d545991d'

# Shared pure parsers are embedded in the remote read-only snapshot and tested
# locally so failed/truncated procfs evidence cannot become a negative fact.
CAMERA_PROC_VALIDATION = r'''import re
def read_complete_proc_text(path,limit,label,allow_empty=False):
 try:
  with open(path,'rb') as f:data=f.read(limit+1)
 except OSError as error:raise RuntimeError(label+' read failed') from error
 if len(data)>=limit:raise RuntimeError(label+' exceeds bounded read limit')
 if not data and not allow_empty:raise RuntimeError(label+' is empty')
 if data and not data.endswith(b'\n'):raise RuntimeError(label+' is incomplete')
 try:return data.decode('utf-8','strict')
 except UnicodeError as error:raise RuntimeError(label+' is not UTF-8') from error
def mountinfo_has_recovery(text):
 if not text or not text.endswith('\n'):raise ValueError('mountinfo is empty or incomplete')
 lines=text.splitlines()
 if not lines:raise ValueError('mountinfo has no records')
 mounted=False
 for line in lines:
  fields=line.split()
  try:separator=fields.index('-')
  except ValueError as error:raise ValueError('mountinfo record has no separator') from error
  if (len(fields)<10 or separator<6 or len(fields)<separator+4 or
      not fields[0].isdigit() or not fields[1].isdigit() or
      not re.fullmatch(r'[0-9]+:[0-9]+',fields[2]) or
      not fields[4].startswith('/') or not fields[separator+1] or
      not fields[separator+2]):
   raise ValueError('mountinfo record is malformed')
  if fields[2]=='259:0':mounted=True
 return mounted
def fimc_is_live_in_modules(text):
 if not isinstance(text,str):raise ValueError('/proc/modules is unavailable')
 live=False
 for line in text.splitlines():
  fields=line.split()
  if (len(fields)<6 or not re.fullmatch(r'[A-Za-z0-9_]+',fields[0]) or
      not fields[1].isdigit() or not fields[2].isdigit() or
      fields[4] not in ('Live','Loading','Unloading') or
      not re.fullmatch(r'(?:0x[0-9a-fA-F]+|-)',fields[5])):
   raise ValueError('/proc/modules record is malformed')
  if fields[0]=='fimc_is' and fields[4]=='Live':live=True
 return live
'''

# Narrow read-only identity capture used immediately before a camera flash and
# again after the shared renderer's full-image readback. It does not access
# camera nodes, firmware, kernel logs, or module controls.
CAMERA_IDENTITY_SNAPSHOT = r'''import fcntl,hashlib,json,os,pathlib,stat,struct
p=pathlib.Path
size=100663296
def require(ok,message):
 if not ok:raise RuntimeError(message)
def read(path,limit=65536):
 try:
  with open(path,'rb') as f:return f.read(limit).decode('utf-8','strict').strip('\x00\r\n ')
 except OSError:return None
def readb(path,limit=1048576):
 try:
  with open(path,'rb') as f:return f.read(limit)
 except OSError:return None
def build_id(notes):
 if notes is None:return None
 pos=0
 while pos+12<=len(notes):
  namesz,descsz,kind=struct.unpack_from('<III',notes,pos);pos+=12
  name_end=pos+namesz;name_pad=pos+((namesz+3)&~3)
  desc_end=name_pad+descsz;next_pos=name_pad+((descsz+3)&~3)
  if name_end>len(notes) or desc_end>len(notes) or next_pos>len(notes):return None
  if notes[pos:name_end].rstrip(b'\x00')==b'GNU' and kind==3:return notes[name_pad:desc_end].hex()
  pos=next_pos
 return None
''' + CAMERA_PROC_VALIDATION + r'''
alias=p('/dev/block/by-name/recovery')
resolved=str(alias.resolve(strict=True))
node=p('/dev/sda16')
require(resolved=='/dev/sda16','RECOVERY alias target mismatch')
require(not node.is_symlink(),'RECOVERY block node became a symlink')
expected_rdev=os.makedev(259,0)
fd=os.open(node,os.O_RDONLY|os.O_CLOEXEC|os.O_NOFOLLOW)
try:
 info=os.fstat(fd)
 require(stat.S_ISBLK(info.st_mode) and info.st_rdev==expected_rdev,
         'RECOVERY block device identity mismatch')
 capacity=struct.unpack('<Q',fcntl.ioctl(fd,0x80081272,b'\x00'*8))[0]
 sysfs_sectors=int(read('/sys/class/block/sda16/size'))
 uevent=(read('/sys/class/block/sda16/uevent',4096) or '').splitlines()
 mount_text=read_complete_proc_text('/proc/self/mountinfo',262144,
                                    '/proc/self/mountinfo')
 mounted=mountinfo_has_recovery(mount_text)
 require(capacity==size and sysfs_sectors==size//512,
         'RECOVERY capacity mismatch')
 require('PARTNAME=recovery' in uevent and not mounted,
         'RECOVERY partition name/mount state mismatch')
 h=hashlib.sha256()
 offset=0
 while offset<size:
   block=os.pread(fd,min(1048576,size-offset),offset)
   require(bool(block),'short RECOVERY identity read')
   h.update(block)
   offset+=len(block)
 require(offset==size,'RECOVERY identity read exceeded the pinned size')
 recovery_hash=h.hexdigest()
finally:os.close(fd)
modules=read_complete_proc_text('/proc/modules',1048576,'/proc/modules',allow_empty=True)
loaded=fimc_is_live_in_modules(modules)
module_note='/sys/module/fimc_is/notes/.note.gnu.build-id'
state={'schema':'camera-recovery-identity/v1',
 'boot_id':read('/proc/sys/kernel/random/boot_id'),
 'pid1':read('/proc/1/comm'),
 'native_ready':os.path.exists('/run/native-ready'),
 'kernel_release':read('/proc/sys/kernel/osrelease'),
 'kernel_gnu_build_id':build_id(readb('/sys/kernel/notes')),
 'recovery_sha256':recovery_hash,
 'recovery_target':{'alias_target':resolved,'rdev':'259:0',
  'capacity_bytes':capacity,'sysfs_sectors':sysfs_sectors,
  'partition_name':'recovery','mounted':mounted},
 'camera_module_loaded':loaded,
 'camera_module_gnu_build_id':build_id(readb(module_note)) if loaded else None}
print(json.dumps(state,separators=(',',':')))
'''


def render_boot_bound_flash(rendered_shared_body: str, expected_boot_id: str) -> str:
    """Check the captured boot ID before shared flash code can mutate state."""
    uuid_pattern = r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}'
    if not isinstance(expected_boot_id, str) or not re.fullmatch(uuid_pattern, expected_boot_id):
        raise ValueError('pre-write boot ID must be a canonical UUID')
    prelude = (
        'import pathlib\n'
        f"expected_boot_id={expected_boot_id!r}\n"
        "actual_boot_id=pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip()\n"
        "if actual_boot_id!=expected_boot_id: raise RuntimeError('boot ID changed before RECOVERY write; no retry')\n"
    )
    return prelude + rendered_shared_body


def validate_camera_identity(snapshot: dict, *, expected_recovery_sha: str,
                             require_baseline_camera_module: bool) -> dict:
    if not isinstance(snapshot, dict) or snapshot.get('schema') != 'camera-recovery-identity/v1':
        raise ValueError('camera identity snapshot has an invalid schema')
    boot_id = snapshot.get('boot_id')
    if not isinstance(boot_id, str) or not re.fullmatch(
            r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}', boot_id):
        raise ValueError('camera identity snapshot has no canonical boot ID')
    if snapshot.get('recovery_sha256') != expected_recovery_sha:
        raise ValueError('camera identity snapshot RECOVERY hash mismatch')
    target = snapshot.get('recovery_target')
    if not isinstance(target, dict):
        raise ValueError('camera identity snapshot has no RECOVERY target proof')
    for key, expected in {
        'alias_target': '/dev/sda16',
        'rdev': '259:0',
        'capacity_bytes': SIZE,
        'sysfs_sectors': SIZE // 512,
        'partition_name': 'recovery',
        'mounted': False,
    }.items():
        if target.get(key) != expected:
            raise ValueError(f'camera identity snapshot RECOVERY target mismatch for {key}')
    if snapshot.get('pid1') != 'native-guardian' or snapshot.get('native_ready') is not True:
        raise ValueError('native recovery context is not ready')
    if snapshot.get('kernel_gnu_build_id') != RUNNING_KERNEL_BUILD_ID:
        raise ValueError('running kernel GNU build ID is not the pinned HCI kernel')
    if require_baseline_camera_module and (
            snapshot.get('camera_module_loaded') is not True or
            snapshot.get('camera_module_gnu_build_id') != BASELINE_MODULE_BUILD_ID):
        raise ValueError('pre-write fimc_is is not the expected loaded HCI module')
    return snapshot

PROFILES = {
    'camera-forward': {
        'before_role': 'baseline', 'write_role': 'camera',
        'staging_directory': '/srv/s22/camera-recovery-forward-20260927',
        'rollback_filename': 'hci-recovery-rollback.img',
        'receipt_prefix': 'camera-recovery-forward',
    },
    'camera-reverse': {
        'before_role': 'camera', 'write_role': 'baseline',
        'staging_directory': '/srv/s22/camera-recovery-reverse-20260927',
        'rollback_filename': 'camera-recovery-rollback.img',
        'receipt_prefix': 'camera-recovery-reverse',
    },
}
TRIAL_IDENTITIES = {
    ('camera-forward', 'stage'): 'camera-recovery-20260927-forward-stage',
    ('camera-forward', 'flash'): 'camera-recovery-20260927-forward-flash',
    ('camera-reverse', 'stage'): 'camera-recovery-20260927-reverse-stage',
    ('camera-reverse', 'flash'): 'camera-recovery-20260927-reverse-flash',
}


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f'cannot load shared deployment helper: {path}')
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _load_shared(project_root: Path):
    shared = _load_module('s22_camera_shared_recovery', project_root / 'tools/hardware/deploy-audio-recovery.py')
    guard = _load_module('s22_camera_device_trial_guard', project_root / 'tools/hardware/device-trial-guard.py')
    return shared, guard


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def resolve_profile(profile_name: str) -> dict:
    if profile_name not in PROFILES:
        raise ValueError(f'unknown camera RECOVERY profile: {profile_name}')
    roles = {
        'baseline': {'image': BASELINE_IMAGE, 'sha256': BASELINE_IMAGE_SHA256,
                     'module_build_id': BASELINE_MODULE_BUILD_ID},
        'camera': {'image': CAMERA_IMAGE, 'sha256': CAMERA_IMAGE_SHA256,
                   'module_build_id': CAMERA_MODULE_BUILD_ID},
    }
    resolved = dict(PROFILES[profile_name])
    resolved.update(profile=profile_name,
                    before=roles[resolved['before_role']],
                    write=roles[resolved['write_role']])
    resolved.update(before_sha256=resolved['before']['sha256'],
                    new_sha256=resolved['write']['sha256'],
                    image=resolved['write']['image'])
    return resolved


def _require_equal(value, expected, label):
    if value != expected:
        raise ValueError(f'{label} does not match the pinned camera recovery evidence')


def validate_artifacts(profile_name: str, *, project_root=ROOT,
                       artifact_root=ARTIFACT_ROOT, shared=None) -> tuple[dict, bytes, bytes]:
    """Verify pinned evidence, private manifest, and both complete image files."""
    project_root = Path(project_root)
    artifact_root = Path(artifact_root)
    if shared is None:
        shared, _ = _load_shared(project_root)
    profile = resolve_profile(profile_name)
    evidence_bytes = shared.read_host_artifact(project_root / PACKAGE_EVIDENCE, 'camera package evidence')
    _require_equal(_sha256(evidence_bytes), PACKAGE_EVIDENCE_SHA256, 'camera package evidence SHA-256')
    try:
        evidence = json.loads(evidence_bytes)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ValueError(f'camera package evidence is not valid JSON: {error}') from error
    if not isinstance(evidence, dict):
        raise ValueError('camera package evidence must be a JSON object')
    for key, expected in {
        'artifact_relative_path': CAMERA_IMAGE,
        'artifact_sha256': CAMERA_IMAGE_SHA256,
        'artifact_bytes': SIZE,
        'private_manifest_sha256': CAMERA_MANIFEST_SHA256,
        'base_image_sha256': BASELINE_IMAGE_SHA256,
        'partition_writes': 0,
        'reboots': 0,
        'camera_open_or_capture_attempted': False,
        'bootability_verified': False,
        'independent_hardware_rescue_demonstrated': False,
        'phone_access_by_packager': False,
    }.items():
        _require_equal(evidence.get(key), expected, f'package evidence {key}')

    manifest_bytes = shared.read_host_artifact(artifact_root / CAMERA_MANIFEST, 'private camera package manifest')
    _require_equal(_sha256(manifest_bytes), CAMERA_MANIFEST_SHA256, 'private camera manifest SHA-256')
    try:
        manifest = json.loads(manifest_bytes)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ValueError(f'private camera manifest is not valid JSON: {error}') from error
    if not isinstance(manifest, dict):
        raise ValueError('private camera manifest must be a JSON object')
    for key, expected in {
        'candidate_image_sha256': CAMERA_IMAGE_SHA256,
        'candidate_image_bytes': SIZE,
        'base_image_sha256': BASELINE_IMAGE_SHA256,
        'partition_size_bytes': SIZE,
        'phone_access': False,
        'deployed': False,
        'candidate_avb_footer_verified': True,
        'avb_algorithm': 'NONE',
    }.items():
        _require_equal(manifest.get(key), expected, f'private manifest {key}')
    replacement = manifest.get('replacement_module')
    if not isinstance(replacement, dict):
        raise ValueError('private manifest has no camera replacement module record')
    for key, expected in {
        'path': 'lib/modules/fimc-is.ko',
        'build_id_sha1': CAMERA_MODULE_BUILD_ID,
        'versions_section_present': True,
    }.items():
        _require_equal(replacement.get(key), expected, f'camera module record {key}')
    baseline_module = manifest.get('baseline_module')
    if not isinstance(baseline_module, dict):
        raise ValueError('private manifest has no baseline camera module record')
    _require_equal(baseline_module.get('build_id_sha1'), BASELINE_MODULE_BUILD_ID,
                   'baseline camera module GNU build ID')

    baseline = shared.read_host_artifact(artifact_root / BASELINE_IMAGE, 'HCI rollback recovery image')
    camera = shared.read_host_artifact(artifact_root / CAMERA_IMAGE, 'camera recovery image')
    for label, content, digest in (
        ('HCI rollback recovery image', baseline, BASELINE_IMAGE_SHA256),
        ('camera recovery image', camera, CAMERA_IMAGE_SHA256),
    ):
        if len(content) != SIZE or _sha256(content) != digest:
            raise ValueError(f'{label} has incorrect size or SHA-256')
    images = {'baseline': baseline, 'camera': camera}
    return profile, images[profile['before_role']], images[profile['write_role']]


def validate_cli_execution(profile_name: str, mode: str, trial_identity: str | None):
    expected = TRIAL_IDENTITIES.get((profile_name, mode))
    if expected is None or trial_identity != expected:
        raise ValueError('camera execution requires this direction/mode one-use trial identity')
    return expected


def camera_identity_command():
    return 'python3 -c ' + shlex.quote(CAMERA_IDENTITY_SNAPSHOT)


def _run_transport(shared, artifact_root, command, *, input_data, timeout, transport):
    if transport is None:
        return shared.run_approved_ssh_wrapper(
            artifact_root / 'tools/s22-ssh', command, input_data=input_data,
            timeout=timeout, project_root=artifact_root)
    return transport(artifact_root / 'tools/s22-ssh', command,
                     input_data=input_data, timeout=timeout,
                     project_root=artifact_root)


def _capture_camera_identity(shared, artifact_root, *, transport):
    result = _run_transport(shared, artifact_root, camera_identity_command(),
                            input_data=b'', timeout=60, transport=transport)
    if result.returncode:
        raise RuntimeError('read-only camera identity capture failed; operation outcome may be unknown')
    try:
        value = json.loads(result.stdout)
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise RuntimeError('read-only camera identity capture returned invalid JSON') from error
    if not isinstance(value, dict):
        raise RuntimeError('read-only camera identity capture returned a non-object')
    return value


def _run_operation(profile, mode, image, *, project_root, receipt_dir,
                   artifact_root, state_root, shared, guard, trial_identity,
                   transport=None):
    if mode not in ('stage', 'flash'):
        raise ValueError(f'unsupported camera RECOVERY operation: {mode}')
    operation_kind = f'camera-recovery-{profile["profile"].removeprefix("camera-")}-{mode}'
    if receipt_dir is None:
        receipt_dir = (Path.home() / '.local/state/s22-camera-trial-20260927/receipts' /
                       trial_identity)
    receipt_dir = Path(receipt_dir)
    if receipt_dir.name != trial_identity:
        raise ValueError('camera receipt directory must be namespaced by the one-use trial identity')
    try:
        receipt_dir = shared.prepare_private_receipt_directory(receipt_dir)
        receipt_dir_fd = shared.open_private_receipt_directory(receipt_dir)
    except (OSError, ValueError) as error:
        raise RuntimeError(f'private camera receipt directory unavailable: {error}') from error
    out = receipt_dir / f'{profile["receipt_prefix"]}-{mode}.json'
    prewrite_out = receipt_dir / f'{profile["receipt_prefix"]}-flash-prewrite.json'
    raw_write_out = receipt_dir / f'{profile["receipt_prefix"]}-flash-write-readback.json'
    try:
        shared.ensure_new_receipt(out, directory_fd=receipt_dir_fd)
        if mode == 'flash':
            shared.ensure_new_receipt(prewrite_out, directory_fd=receipt_dir_fd)
            shared.ensure_new_receipt(raw_write_out, directory_fd=receipt_dir_fd)
        # Validate transport identity before the durable pending marker. The
        # actual wrapper is opened/hash-pinned again by run_approved_ssh_wrapper.
        if transport is None:
            wrapper_preflight_fd = shared.validate_approved_ssh_wrapper(artifact_root / 'tools/s22-ssh')
            os.close(wrapper_preflight_fd)
        code = shared.render_remote(
            base_sha=profile['before_sha256'], new_sha=profile['new_sha256'],
            staging_directory=profile['staging_directory'],
            rollback_filename=profile['rollback_filename'])
        command = 'python3 -c ' + shlex.quote(code) + ' ' + mode
        with guard.acquire_operation_lock(
                project_root, trial_identity, operation_kind,
                state_root=state_root) as operation:
            operation.begin(project_root=project_root)
            try:
                prewrite = None
                prewrite_sha = None
                if mode == 'flash':
                    prewrite = _capture_camera_identity(
                        shared, artifact_root, transport=transport)
                    validate_camera_identity(
                        prewrite, expected_recovery_sha=profile['before_sha256'],
                        require_baseline_camera_module=profile['profile'] == 'camera-forward')
                    prewrite_record = {
                        'schema': 'camera-recovery-prewrite/v1',
                        'profile': profile['profile'],
                        'trial_identity': trial_identity,
                        'partition': PARTITION,
                        'expected_write_sha256': profile['new_sha256'],
                        'identity': prewrite,
                    }
                    shared.persist_receipt(prewrite_out, prewrite_record,
                                           directory_fd=receipt_dir_fd)
                    prewrite_sha = _sha256(shared.read_host_artifact(
                        prewrite_out, 'durable camera pre-write identity receipt'))
                    code = render_boot_bound_flash(code, prewrite['boot_id'])
                    command = 'python3 -c ' + shlex.quote(code) + ' flash'

                result = _run_transport(
                    shared, artifact_root, command,
                    input_data=image if mode == 'stage' else b'',
                    timeout=100 if mode == 'stage' else None,
                    transport=transport)
                if result.returncode:
                    detail=result.stderr.decode(errors='replace')
                    raise RuntimeError(
                        mode+' transport/remote operation failed; outcome may be unknown; '
                        'do not retry: '+detail)
                try:
                    receipt = json.loads(result.stdout)
                    shared.validate_remote_receipt(
                        receipt, mode=mode, before_sha=profile['before_sha256'],
                        candidate_sha=profile['new_sha256'], size=SIZE)
                except (TypeError, ValueError, json.JSONDecodeError) as error:
                    raise RuntimeError(
                        mode+' returned an invalid receipt; outcome may be unknown; '
                        'do not retry: '+str(error)) from error
                receipt.update({
                    'profile': profile['profile'],
                    'partition': PARTITION,
                    'trial_identity': trial_identity,
                    'written_image_sha256': profile['new_sha256'],
                    'expected_before_module_build_id': profile['before']['module_build_id'],
                    'expected_write_module_build_id': profile['write']['module_build_id'],
                    'explicit_execute_flag_present': True,
                })
                if mode == 'flash':
                    receipt.update({
                        'schema': 'camera-recovery-write-readback/v1',
                        'prewrite_identity_receipt_sha256': prewrite_sha,
                        'prewrite_boot_id': prewrite['boot_id'],
                        'prewrite_kernel_gnu_build_id': prewrite['kernel_gnu_build_id'],
                        'prewrite_camera_module_loaded': prewrite['camera_module_loaded'],
                        'prewrite_camera_module_gnu_build_id': prewrite['camera_module_gnu_build_id'],
                    })
                    # Preserve full write/readback proof before another remote
                    # query. If the return identity check fails, this receipt
                    # remains while the global marker stays unresolved.
                    shared.persist_receipt(raw_write_out, receipt,
                                           directory_fd=receipt_dir_fd)
                    postwrite = _capture_camera_identity(
                        shared, artifact_root, transport=transport)
                    validate_camera_identity(
                        postwrite, expected_recovery_sha=profile['new_sha256'],
                        require_baseline_camera_module=profile['profile'] == 'camera-forward')
                    if postwrite['boot_id'] != prewrite['boot_id']:
                        raise RuntimeError(
                            'boot ID changed across RECOVERY write/readback; do not reboot or retry')
                    receipt = dict(receipt)
                    receipt.update({
                        'schema': 'camera-recovery-flash-bound/v1',
                        'write_readback_receipt_sha256': _sha256(shared.read_host_artifact(
                            raw_write_out, 'durable camera write/readback receipt')),
                        'postwrite_identity': postwrite,
                        'prewrite_postwrite_same_boot': True,
                    })
                    shared.persist_receipt(out, receipt, directory_fd=receipt_dir_fd)
                else:
                    shared.persist_receipt(out, receipt, directory_fd=receipt_dir_fd)
                # The SSH subprocess has exited and the operation receipt is
                # durable; retained stage files are intentional, not cleanup.
                operation.complete(out, outcome='success', cleanup_confirmed=True)
            except (OSError, subprocess.TimeoutExpired) as error:
                raise RuntimeError(
                    mode+' transport failed/timed out; operation outcome may be unknown; '
                    'inspect before any retry: '+str(error)) from error
        print(json.dumps(receipt, indent=2))
        return 0
    finally:
        os.close(receipt_dir_fd)


def main_camera_profile(profile_name, mode=None, *, project_root=ROOT,
                         artifact_root=ARTIFACT_ROOT, receipt_dir=None,
                         state_root=None, trial_identity=None, execute=False,
                         shared=None, guard=None, transport=None):
    project_root = Path(project_root)
    artifact_root = Path(artifact_root)
    if mode is None and (execute or trial_identity is not None):
        raise ValueError('execution flags require exactly one --stage or --flash operation')
    if mode is not None:
        if not execute:
            raise ValueError('camera --stage/--flash require explicit --execute intent')
        validate_cli_execution(profile_name, mode, trial_identity)
    if shared is None or (mode is not None and guard is None):
        loaded_shared, loaded_guard = _load_shared(project_root)
        shared = loaded_shared if shared is None else shared
        guard = loaded_guard if guard is None else guard
    try:
        profile, before_image, write_image = validate_artifacts(
            profile_name, project_root=project_root,
            artifact_root=artifact_root, shared=shared)
    except (OSError, ValueError) as error:
        raise RuntimeError(f'camera RECOVERY artifact validation failed; no device operation attempted: {error}') from error
    plan = {
        'profile': profile_name,
        'partition': PARTITION,
        'before_sha256': profile['before_sha256'],
        'expected_before_module_build_id': profile['before']['module_build_id'],
        'write_sha256': profile['new_sha256'],
        'expected_write_module_build_id': profile['write']['module_build_id'],
        'write_image': profile['image'],
        'staging_directory': profile['staging_directory'],
        'operation': mode,
        'execution': mode is not None,
        'host_only_plan': mode is None,
        'explicit_execute_requested': False if mode is None else True,
        'independent_rescue_demonstrated': False,
        'camera_unattended_acceptance_recorded': False,
        'reboot_performed': False,
    }
    if mode is None:
        print(json.dumps(plan, indent=2))
        return 0
    if state_root is None:
        state_root = guard.STATE_ROOT
    return _run_operation(profile, mode, write_image,
                          project_root=project_root, receipt_dir=receipt_dir,
                          artifact_root=artifact_root, state_root=state_root,
                          shared=shared, guard=guard,
                          trial_identity=trial_identity, transport=transport)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    choice = parser.add_mutually_exclusive_group()
    choice.add_argument('--stage', action='store_true')
    choice.add_argument('--flash', action='store_true')
    parser.add_argument('--profile', choices=tuple(PROFILES), default='camera-forward')
    parser.add_argument('--receipt-dir', type=Path)
    parser.add_argument('--execute', action='store_true',
                        help='explicitly request the selected host/RECOVERY operation')
    parser.add_argument('--trial-identity',
                        help='must be the fresh fixed identity for this direction and mode')
    args = parser.parse_args(argv)
    mode = 'stage' if args.stage else 'flash' if args.flash else None
    if mode is not None and not args.execute:
        parser.error('--stage/--flash require --execute; default invocations are host-only plans')
    if mode is None and (args.execute or args.trial_identity is not None or args.receipt_dir is not None):
        parser.error('--execute/--trial-identity/--receipt-dir require --stage or --flash')
    if mode is not None:
        try:
            validate_cli_execution(args.profile, mode, args.trial_identity)
        except ValueError as error:
            parser.error(str(error))
    try:
        return main_camera_profile(
            args.profile, mode,
            artifact_root=ARTIFACT_ROOT, receipt_dir=args.receipt_dir,
            trial_identity=args.trial_identity,
            execute=args.execute)
    except (OSError, RuntimeError, ValueError) as error:
        raise SystemExit(str(error)) from error


if __name__ == '__main__':
    raise SystemExit(main())
