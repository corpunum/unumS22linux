#!/usr/bin/env python3
"""One explicitly authorized H4/IBS controller-registration trial.

The default path refuses execution. Live execution additionally requires a
separate owner-authorization acknowledgement, exact trial name, verified local
artifact provenance, a valid completed RECOVERY observer receipt, and fresh
same-boot health/hash/controller-absence checks. This script has no retry.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import stat
import struct
import subprocess
import sys
import types

ROOT = Path(__file__).resolve().parents[2]
TRUSTED_ROOT = Path('/home/corpunum/s22-linux')
TRIAL_DIR = '/srv/s22/bt-trial-20260927/bt-hci-registration-20260926'
TRIAL_TRACE_DIR = Path(TRIAL_DIR)
TRIAL_ID = 'bt-hci-registration-20260926'
OBSERVER_TRIAL_ID = 'hci-candidate-20260924-second'
EXPECTED_RECOVERY_SHA256 = '42da267f3dd9f94f30f62a95fb2ac13f91d4cf98f1a2307f7cc14e45d9c49be5'
EXPECTED_GNU_BUILD_ID = 'b2dda820b18d410d9bf12f1bd2584567d545991d'
EXPECTED_ARTIFACT_SHA256 = 'c28307985bdad6404f0fecc82860eaa92a2c150a5d870d9297ce0301f44bac0a'
EXPECTED_ARTIFACT_SIZE = 1042008
EXPECTED_ARTIFACT_BUILD_ID = 'eb47c232ddb7fc4b477bb145bbe9bf2832972276'
EXPECTED_SSH_WRAPPER_SHA256 = '7e9d31035762de50ccc6c5614d8348532fd912bf59c41c9d410a4c7bfe49dd1d'
EXPECTED_TRUSTED_RUN_TRIAL_SHA256 = '165a16566f2dedcb23506d427425ddb030acc53876710a61bb06a2f1b039a5fe'
EXPECTED_TRUSTED_DEPLOYER_SHA256 = 'd0c54cc306e3a8a80cb79d79958fceedf6bb5945dabc7606e02b12d27dc00fd3'
EXPECTED_TZ_SOURCE_COMMIT = '4e5c5ad7d950e4de0688b5663965f2075654b2ad'
EXPECTED_TZ_CLASSIFIER_SHA256 = '8faa2cd46d16818ebdd61d7a577b046489083f5c96a6a398ddea769ebbb6619a'
ARTIFACT = Path('/home/corpunum/s22-workers/bt-next-20260926/builds/bt-next-20260926/bt-qca6490-hci-bridge-probe')
SOURCE = ROOT / 'tools/hardware/bt-qca6490-hci-bridge-probe.c'
DEST = TRIAL_DIR + '/bt-qca6490-hci-bridge-probe'
TRACE_NAME = TRIAL_ID + '.strace'
EXPECTED_S22_DEVICE_ID = 66324
MIN_S22_FREE_BYTES = 1024 * 1024 * 1024
MIN_S22_FREE_INODES = 10000
S22_PERSISTENT_ROOT = '/srv/s22'
S22_NAMESPACE_NAME = 'bt-trial-20260927'
PRIVATE_BUILD_HEADERS = (
    Path('/home/corpunum/s22-linux/builds/bt-patch-20260922/qca-patch-private.h'),
    Path('/home/corpunum/s22-linux/builds/bt-runtime-nvm-20260922/s22_nvm_payload.h'),
)
BUILD_INPUT_SHA256 = {
    'tools/hardware/bt-qca6490-hci-bridge-probe.c':
        'ceabf4635b6512397cd473f67df7c313ccf12ca13379fbccf0786b94f13b8f3d',
    'tools/hardware/bt-h4-ibs-bridge.c':
        '646cd02b84c8cd580b40a0266b63dd5399c95371f1e7d3e33d42d9fc1c85fbae',
    'tools/hardware/bt-qca6490-runtime-reset.c':
        '337fb3b576a6dccce0a520daf9628a02a9842c5ffa97f16c1f2230351b516b5b',
    'tools/hardware/bt-qca6490-nvm-capture.c':
        '16e4759f22a5932d29a892e3f32f4339d0ab53807aa9f6f314b3e59afdbe8202',
    'tools/hardware/bt-qca6490-patch-capture.c':
        'ac24c9334a4371ecf3ba6c47d80499ec40da5081b8f70a311389c931c99e4bc1',
    'tools/hardware/bt-qca6490-baud-probe.c':
        '655a097731f50417c8012a7ebd0ce1ad9da5a76d6180c38d05f5b3d4f6af843b',
    'tools/hardware/bt-qca6490-patch-version-probe.c':
        'a32ed1bddc52000a7d36ceee032d55c45a978788868f47c3f0aa3a23ca305984',
    'tools/hardware/bt-version-transport-probe.c':
        'b3a9a346facc24845655fa63c020687efc30ffd44c2a0a9ae7bf8ed149ec58a9',
}
REVIEWED_RUNNER_SHA256 = {
    'tools/hardware/audio-recovery-reboot-once.py':
        'ffc4fc5a8d24f3f5b903f2c0009035e127d90dda402d775e160868304ceff6aa',
    'tools/hardware/deploy-audio-recovery.py':
        EXPECTED_TRUSTED_DEPLOYER_SHA256,
    'tools/hardware/run-bt-board-once.py':
        'bf3de2008a9667fdd0de8993cc4035037be93d61701d68613ae89eec6db60b04',
    'tools/hardware/run-bt-version-once.py':
        '93a84da810bccb517694b6cbeb44aa85850289919caec71aa7638c90d49c4653',
    'tools/hardware/trustzone_log_classifier.py': EXPECTED_TZ_CLASSIFIER_SHA256,
}
LIVE_TRIAL_GATE = (
    'Controller registration remains disabled unless the exact trial is invoked '
    'with --execute and --ack-separate-controller-authorization after separate '
    'owner authorization. The prior raw-HCI socket authorization did not cover '
    'firmware initialization, HCI registration, or the kernel-queued automatic '
    'controller initialization/power-on. The 20-second userspace bridge loop '
    'does not bound kernel detach; a supervisor timeout means outcome unknown, '
    'not confirmed power-off or safe retry.'
)
CONTROLLER_STATE_SCRIPT = r'''import json,pathlib
p=pathlib.Path
root=p('/sys/class/bluetooth')
if not root.is_dir(): raise SystemExit('Bluetooth class directory unavailable')
print(json.dumps({'boot_id':p('/proc/sys/kernel/random/boot_id').read_text().strip(),
                  'controllers':sorted(x.name for x in root.glob('hci*'))}))
'''

def render_target_fs_preflight_script(*, root=S22_PERSISTENT_ROOT,
                                      expected_device=EXPECTED_S22_DEVICE_ID,
                                      expected_uid=0, min_free_bytes=MIN_S22_FREE_BYTES,
                                      min_free_inodes=MIN_S22_FREE_INODES,
                                      namespace=S22_NAMESPACE_NAME,
                                      trial_name=TRIAL_ID):
    root_path = Path(root)
    return f'''import json,os,pathlib,stat
root=pathlib.Path({str(root_path)!r})
parent=root.parent
parent_info=parent.lstat()
if not stat.S_ISDIR(parent_info.st_mode) or parent_info.st_uid!={expected_uid!r} or stat.S_IMODE(parent_info.st_mode)&0o022:
 raise SystemExit('persistent root parent is symlinked, writable, or not owner-controlled')
info=root.lstat()
need_bytes={min_free_bytes!r}
need_inodes={min_free_inodes!r}
expected_dev={expected_device!r}
expected_uid={expected_uid!r}
if not stat.S_ISDIR(info.st_mode) or info.st_uid!=expected_uid or stat.S_IMODE(info.st_mode)!=0o700:
 raise SystemExit('persistent trial root ownership/mode/type mismatch')
if info.st_dev!=expected_dev: raise SystemExit('persistent trial root device identity mismatch')
space=os.statvfs(root)
free_bytes=space.f_bavail*space.f_frsize
free_inodes=space.f_favail
if free_bytes<need_bytes or free_inodes<need_inodes:
 raise SystemExit('persistent trial root lacks reserved bytes/inodes')
base=root/{namespace!r}
trial=base/{trial_name!r}
try: base_info=base.lstat()
except FileNotFoundError: base_info=None
if base_info is not None and (not stat.S_ISDIR(base_info.st_mode) or base_info.st_uid!=expected_uid or stat.S_IMODE(base_info.st_mode)!=0o700 or base_info.st_dev!=expected_dev):
 raise SystemExit('trial namespace parent is not a private directory on the persistent filesystem')
if trial.exists() or trial.is_symlink(): raise SystemExit('unique trial directory already exists; never reuse')
print(json.dumps({{'device_id':info.st_dev,'free_bytes':free_bytes,'free_inodes':free_inodes,'trial_absent':True}}))
'''


def render_reserve_target_dir_script(*, root=S22_PERSISTENT_ROOT,
                                     expected_device=EXPECTED_S22_DEVICE_ID,
                                     expected_uid=0, min_free_bytes=MIN_S22_FREE_BYTES,
                                     min_free_inodes=MIN_S22_FREE_INODES,
                                     namespace=S22_NAMESPACE_NAME,
                                     trial_name=TRIAL_ID,
                                     staged_name='bt-qca6490-hci-bridge-probe',
                                     trace_name=TRACE_NAME):
    root_path = Path(root)
    return f'''import json,os,pathlib,stat
root=pathlib.Path({str(root_path)!r})
expected_dev={expected_device!r}
expected_uid={expected_uid!r}
need_bytes={min_free_bytes!r}
need_inodes={min_free_inodes!r}
def check_root():
 parent_info=root.parent.lstat()
 if not stat.S_ISDIR(parent_info.st_mode) or parent_info.st_uid!=expected_uid or stat.S_IMODE(parent_info.st_mode)&0o022:
  raise SystemExit('persistent root parent is unsafe')
 info=root.lstat()
 if not stat.S_ISDIR(info.st_mode) or info.st_uid!=expected_uid or stat.S_IMODE(info.st_mode)!=0o700 or info.st_dev!=expected_dev:
  raise SystemExit('persistent trial root identity changed')
 space=os.statvfs(root)
 if space.f_bavail*space.f_frsize<need_bytes or space.f_favail<need_inodes:
  raise SystemExit('persistent trial root no longer has reserved space')
 return info
root_info=check_root()
base=root/{namespace!r}
trial=base/{trial_name!r}
try: base.mkdir(mode=0o700)
except FileExistsError:
 pass
base_info=base.lstat()
if not stat.S_ISDIR(base_info.st_mode) or base_info.st_uid!=expected_uid or stat.S_IMODE(base_info.st_mode)!=0o700 or base_info.st_dev!=expected_dev:
 raise SystemExit('trial namespace parent is unsafe')
trial.mkdir(mode=0o700)
fd=os.open(base,os.O_RDONLY|getattr(os,'O_DIRECTORY',0))
try: os.fsync(fd)
finally: os.close(fd)
root_after=root.lstat()
if (root_after.st_dev,root_after.st_ino)!=(root_info.st_dev,root_info.st_ino):
 raise SystemExit('persistent trial root identity changed during reservation')
trial_info=trial.lstat()
if not stat.S_ISDIR(trial_info.st_mode) or trial_info.st_uid!=expected_uid or stat.S_IMODE(trial_info.st_mode)!=0o700 or trial_info.st_dev!=expected_dev:
 raise SystemExit('exclusive trial reservation failed validation')
if (trial/{staged_name!r}).exists() or (trial/{staged_name!r}).is_symlink() or (trial/{trace_name!r}).exists() or (trial/{trace_name!r}).is_symlink():
 raise SystemExit('staging or trace target unexpectedly exists in fresh reservation')
print(json.dumps({{'reserved':True,'device_id':trial_info.st_dev,'mode':stat.S_IMODE(trial_info.st_mode)}}))
'''


TARGET_FS_PREFLIGHT_SCRIPT = render_target_fs_preflight_script()
RESERVE_TARGET_DIR_SCRIPT = render_reserve_target_dir_script()


class GateError(RuntimeError):
    """A fail-closed, pre-board-main readiness rejection."""


def require(condition, message):
    if not condition:
        raise GateError(message)


def _regular_file(path, label):
    try:
        info = path.lstat()
    except OSError as error:
        raise GateError(f'{label} is unavailable: {path}') from error
    require(stat.S_ISREG(info.st_mode), f'{label} must be a regular non-symlink file')
    return info


def _sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def load_pinned_kernel_classifier(root=ROOT):
    """Load the exact classifier source used by the recovery observer."""
    path = root / 'tools/hardware/trustzone_log_classifier.py'
    _regular_file(path, 'pinned TrustZone log classifier')
    source = path.read_bytes()
    require(hashlib.sha256(source).hexdigest() == EXPECTED_TZ_CLASSIFIER_SHA256,
            'pinned TrustZone log classifier fingerprint changed')
    name = '_s22_bt_pinned_trustzone_log_classifier'
    module = types.ModuleType(name)
    module.__file__ = str(path)
    sys.modules[name] = module
    try:
        exec(compile(source, str(path), 'exec'), module.__dict__)
    except Exception:
        sys.modules.pop(name, None)
        raise
    require(getattr(module, 'PINNED_KERNEL_SOURCE_COMMIT', None) ==
            EXPECTED_TZ_SOURCE_COMMIT,
            'TrustZone classifier kernel source pin differs from the observer')
    return module


def classify_kernel_delta(data, *, classifier=None):
    """Keep trace-only evidence distinct; reject fatal, hung, or incomplete logs."""
    require(isinstance(data, bytes), 'post-trial kernel delta is not bytes')
    if classifier is None:
        classifier = load_pinned_kernel_classifier()
    if not data.strip():
        return {
            'assessment': 'no_new_records', 'fatal_indicators': [],
            'hung_task_warning_count': 0, 'call_trace_count': 0,
            'liveness_unresolved': False, 'coverage_complete': True,
        }
    classified = classifier.classify_kernel_log(data, capture_complete=True)
    require(classified.coverage_complete,
            'post-trial kernel delta classification is incomplete')
    fatal = [indicator.kind for indicator in classified.fatal_indicators]
    hung_count = len(classified.hung_task_warnings)
    require(not fatal and hung_count == 0 and
            classified.material_liveness_unresolved is False,
            'post-trial kernel delta contains a fatal or hung-task indicator')
    return {
        'assessment': classified.assessment,
        'fatal_indicators': fatal,
        'hung_task_warning_count': hung_count,
        'call_trace_count': len(classified.call_trace_lines),
        'liveness_unresolved': classified.material_liveness_unresolved,
        'coverage_complete': classified.coverage_complete,
    }


def _elf_build_id(data):
    require(data[:6] == b'\x7fELF\x02\x01', 'probe must be a little-endian ELF64')
    require(struct.unpack_from('<H', data, 18)[0] == 183,
            'probe must be an AArch64 ELF')
    section_offset = struct.unpack_from('<Q', data, 40)[0]
    section_size, section_count = struct.unpack_from('<HH', data, 58)
    require(section_size >= 64 and section_count > 0 and
            section_offset + section_size * section_count <= len(data),
            'probe ELF section table is malformed')
    for index in range(section_count):
        offset = section_offset + index * section_size
        if struct.unpack_from('<I', data, offset + 4)[0] != 7:  # SHT_NOTE
            continue
        notes_offset, notes_size = struct.unpack_from('<QQ', data, offset + 24)
        end = notes_offset + notes_size
        if end > len(data):
            raise GateError('probe ELF note section is out of bounds')
        cursor = notes_offset
        while cursor + 12 <= end:
            name_size, desc_size, note_type = struct.unpack_from('<III', data, cursor)
            cursor += 12
            name_end = cursor + name_size
            name_padded_end = cursor + ((name_size + 3) & ~3)
            desc_end = name_padded_end + desc_size
            next_cursor = name_padded_end + ((desc_size + 3) & ~3)
            if next_cursor > end:
                break
            if data[cursor:name_end].rstrip(b'\0') == b'GNU' and note_type == 3:
                return data[name_padded_end:desc_end].hex()
            cursor = next_cursor
    return None


def validate_local_provenance(root=ROOT, artifact_path=None,
                              private_headers=PRIVATE_BUILD_HEADERS):
    """Pin the existing compiled artifact to reviewed source and ELF identity."""
    for relative, expected in BUILD_INPUT_SHA256.items():
        source = root / relative
        _regular_file(source, 'build source')
        require(_sha256(source) == expected,
                f'build source fingerprint changed: {relative}')
    for relative, expected in REVIEWED_RUNNER_SHA256.items():
        source = root / relative
        _regular_file(source, 'reviewed trial runner')
        require(_sha256(source) == expected,
                f'reviewed trial runner fingerprint changed: {relative}')
    for header in private_headers:
        _regular_file(header, 'private build dependency')
    artifact = artifact_path or ARTIFACT
    info = _regular_file(artifact, 'prebuilt bridge artifact')
    require(info.st_uid == os.geteuid() and (info.st_mode & 0o777) == 0o700,
            'prebuilt bridge artifact must be owner-owned mode 0700')
    data = artifact.read_bytes()
    require(len(data) == EXPECTED_ARTIFACT_SIZE,
            'prebuilt bridge artifact size mismatch')
    require(hashlib.sha256(data).hexdigest() == EXPECTED_ARTIFACT_SHA256,
            'prebuilt bridge artifact SHA-256 mismatch')
    require(_elf_build_id(data) == EXPECTED_ARTIFACT_BUILD_ID,
            'prebuilt bridge artifact GNU build ID mismatch')
    return hashlib.sha256(data).hexdigest()


def trial_receipt_directory(root=ROOT, name=TRIAL_ID):
    require(name == TRIAL_ID, 'only the exact controller-registration trial identity is allowed')
    return root / 'rootfs/hardware-reuse-20260921/bt-trials' / name


def require_unused_receipt_path(path):
    cursor = path
    while True:
        try:
            info = cursor.lstat()
        except FileNotFoundError:
            parent = cursor.parent
            if parent == cursor:
                break
            cursor = parent
            continue
        except OSError as error:
            raise GateError(f'cannot inspect one-shot receipt path: {error}') from error
        require(not stat.S_ISLNK(info.st_mode),
                'one-shot receipt path contains a symlink')
        if cursor != path:
            require(stat.S_ISDIR(info.st_mode),
                    'one-shot receipt parent is not a directory')
        break
    require(not path.exists() and not path.is_symlink(),
            'one-shot trial receipt path already exists; never retry')


def load_observer():
    path = ROOT / 'tools/hardware/audio-recovery-reboot-once.py'
    spec = importlib.util.spec_from_file_location('bt_registration_observer', path)
    if spec is None or spec.loader is None:
        raise GateError('reviewed HCI observer module is unavailable')
    observer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(observer)
    # The pinned helper source stays in this worktree. Calls below pass the
    # original repository root explicitly so its private known_hosts file and
    # existing SSH wrapper are used without copying credentials.
    return observer


def load_board():
    path = ROOT / 'tools/hardware/run-bt-board-once.py'
    spec = importlib.util.spec_from_file_location('bt_registration_board_runner', path)
    if spec is None or spec.loader is None:
        raise GateError('existing named board runner is unavailable')
    board = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(board)
    return board


def load_device_trial_guard():
    path = ROOT / 'tools/hardware/device-trial-guard.py'
    spec = importlib.util.spec_from_file_location('s22_device_trial_guard', path)
    if spec is None or spec.loader is None:
        raise GateError('shared device-operation guard is unavailable')
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)
    return guard


def validate_trusted_transport(observer, trusted_root=TRUSTED_ROOT):
    wrapper = trusted_root / 'tools/s22-ssh'
    wrapper_info = _regular_file(wrapper, 'trusted USB SSH wrapper')
    require(wrapper_info.st_uid == os.geteuid(),
            'trusted USB SSH wrapper is not owned by this user')
    require(_sha256(wrapper) == EXPECTED_SSH_WRAPPER_SHA256,
            'trusted USB SSH wrapper differs from the approved original')
    trial_runner = trusted_root / 'tools/gpu-compat/run-trial.py'
    _regular_file(trial_runner, 'trusted board trial helper')
    require(_sha256(trial_runner) == EXPECTED_TRUSTED_RUN_TRIAL_SHA256,
            'trusted board trial helper differs from the reviewed version')
    helper = observer.ROOT / 'tools/hardware/deploy-audio-recovery.py'
    _regular_file(helper, 'reviewed SSH helper')
    require(_sha256(helper) == EXPECTED_TRUSTED_DEPLOYER_SHA256,
            'reviewed SSH helper differs from the approved source')
    observer.pinned_usb_host_key_alias(project_root=trusted_root)


def read_live_controller_state(observer, trusted_root=TRUSTED_ROOT):
    result = observer.run_trusted_remote(
        'usb', None, 'python3 -c ' + shlex.quote(CONTROLLER_STATE_SCRIPT),
        timeout=10, project_root=trusted_root)
    require(result.returncode == 0, 'read-only controller-presence check failed')
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise GateError('controller-presence check returned invalid JSON') from error
    require(isinstance(value, dict) and isinstance(value.get('boot_id'), str) and
            isinstance(value.get('controllers'), list) and
            all(isinstance(item, str) for item in value['controllers']),
            'controller-presence check returned malformed state')
    return value


def validate_live_preflight(observer, *, receipt=None, snapshot_reader=None,
                            candidate_hasher=None, controller_reader=None,
                            trusted_root=TRUSTED_ROOT):
    """Check exact observer, live kernel/image, health, and absent controller."""
    require(observer.TRIAL_ID == OBSERVER_TRIAL_ID,
            'observer trial identity changed')
    require(observer.EXPECTED_GNU_BUILD_ID == EXPECTED_GNU_BUILD_ID and
            observer.EXPECTED_FLASH_SHA256 == EXPECTED_RECOVERY_SHA256,
            'observer candidate pins differ from the reviewed RECOVERY candidate')
    if receipt is None:
        receipt_path = observer.observer_receipt_path(observer.OUT, OBSERVER_TRIAL_ID)
        receipt = observer.read_json(receipt_path)
    require(observer.observer_receipt_valid(receipt),
            'completed hci-candidate-20260924-second observer receipt is invalid')

    if snapshot_reader is None:
        snapshot_reader = lambda: observer.snapshot_over(
            'usb', None, project_root=trusted_root)
    current = snapshot_reader()
    observer.validate_snapshot(current, post_reboot=True)
    require(observer.network_state_valid(current.get('network_state')),
            'current WLAN baseline is not ready')
    require(current.get('gnu_build_id') == EXPECTED_GNU_BUILD_ID,
            'live GNU build ID differs from the reviewed candidate')
    require(current.get('boot_id') == receipt.get('boot_id'),
            'live boot ID differs from the completed observer receipt')

    if candidate_hasher is None:
        candidate_hasher = lambda: observer.candidate_hash(
            'usb', None, project_root=trusted_root)
    recovery_hash = candidate_hasher()
    require(recovery_hash == EXPECTED_RECOVERY_SHA256,
            'live full RECOVERY hash differs from the reviewed candidate')

    if controller_reader is None:
        controller_reader = lambda: read_live_controller_state(
            observer, trusted_root=trusted_root)
    controller_state = controller_reader()
    require(controller_state.get('boot_id') == current.get('boot_id') ==
            receipt.get('boot_id'),
            'controller-presence read is not from the observer boot')
    require(controller_state.get('controllers') == [],
            'a Bluetooth controller is already registered; do not attach or retry')
    return {'boot_id': current['boot_id'], 'gnu_build_id': current['gnu_build_id'],
            'recovery_sha256': recovery_hash, 'trial_identity': TRIAL_ID}


def _trusted_remote_command(observer, trusted_root, script, *, timeout):
    command = shlex.join(('python3', '-I', '-c', script))
    result = observer.run_trusted_remote('usb', None, command,
                                         timeout=timeout, project_root=trusted_root)
    if result.returncode:
        detail = result.stderr.decode(errors='replace') if isinstance(result.stderr, bytes) else result.stderr
        raise GateError('persistent trial storage check failed: ' + str(detail).strip()[:512])
    return result


def validate_remote_target_fs(observer, *, trusted_root=TRUSTED_ROOT):
    """Read-only check for the exact persistent filesystem and fresh target."""
    result = _trusted_remote_command(observer, trusted_root,
                                     TARGET_FS_PREFLIGHT_SCRIPT, timeout=15)
    output = result.stdout.decode(errors='replace') if isinstance(result.stdout, bytes) else result.stdout
    try:
        value = json.loads(output)
    except (json.JSONDecodeError, TypeError) as error:
        raise GateError('persistent trial storage preflight returned invalid JSON') from error
    return validate_target_storage_summary(value)


def validate_target_storage_summary(value):
    require(isinstance(value, dict) and value.get('device_id') == EXPECTED_S22_DEVICE_ID and
            value.get('trial_absent') is True and
            isinstance(value.get('free_bytes'), int) and
            value['free_bytes'] >= MIN_S22_FREE_BYTES and
            isinstance(value.get('free_inodes'), int) and
            value['free_inodes'] >= MIN_S22_FREE_INODES,
            'persistent trial storage is not the reviewed private target')
    return value


def reserve_remote_trial_dir(observer, *, trusted_root=TRUSTED_ROOT):
    """Atomically reserve a new private stage/trace directory after begin()."""
    deployer = observer._trusted_deployer()
    wrapper = trusted_root / 'tools/s22-ssh'
    command = shlex.join(('python3', '-I', '-c', RESERVE_TARGET_DIR_SCRIPT))
    result = deployer.run_approved_ssh_wrapper(
        wrapper, command, input_data=b'', timeout=20, project_root=trusted_root)
    if result.returncode:
        detail = result.stderr.decode(errors='replace') if isinstance(result.stderr, bytes) else result.stderr
        raise GateError('exclusive persistent trial directory reservation failed: ' + str(detail).strip()[:512])
    output = result.stdout.decode(errors='replace') if isinstance(result.stdout, bytes) else result.stdout
    try:
        value = json.loads(output)
    except (json.JSONDecodeError, TypeError) as error:
        raise GateError('exclusive trial directory reservation returned invalid JSON') from error
    require(value == {'reserved': True, 'device_id': EXPECTED_S22_DEVICE_ID, 'mode': 0o700},
            'exclusive trial directory reservation returned unexpected identity')
    return value


def validate_completed_trial_receipt(workspace=ROOT, name=TRIAL_ID, *, require_live=True):
    """Require independently verified postflight and durable cleanup evidence."""
    path = trial_receipt_directory(workspace, name) / 'receipt.json'
    info = _regular_file(path, 'completed controller-trial receipt')
    require(info.st_uid == os.geteuid() and not (info.st_mode & 0o077) and
            info.st_nlink == 1,
            'controller-trial receipt is not owner-private')
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as error:
        raise GateError('controller-trial receipt is unreadable') from error
    require(isinstance(value, dict) and value.get('same_boot') is True and
            value.get('returncode') == 0 and value.get('kernel_capture_exit') == 0 and
            value.get('after_metadata_exit') == 0 and value.get('after_vote_check') == 0 and
            value.get('strace_capture_exit') == 0,
            'controller-trial receipt lacks successful same-boot postflight')
    before = value.get('before')
    after = value.get('after')
    require(isinstance(before, dict) and isinstance(after, dict) and
            before.get('boot_id') == after.get('boot_id') and
            after.get('pid1') == 'native-guardian' and
            after.get('profile') == 'qwen4b' and after.get('model') == 'ok' and
            type(after.get('temperature')) in (int, float) and after['temperature'] < 42,
            'controller-trial health receipt is not the same healthy boot')
    try:
        metadata = json.loads(value.get('after_metadata', ''))
    except (TypeError, json.JSONDecodeError) as error:
        raise GateError('post-trial metadata receipt is invalid') from error
    require(isinstance(metadata, dict) and metadata.get('boot_id') == after['boot_id'] and
            metadata.get('device_fds') == [] and metadata.get('independent_usb') is True,
            'post-trial metadata does not confirm same-boot idle USB/no device FDs')
    require(value.get('after_vote_check_stdout', '').strip() ==
            'live_wlan_metadata_only=0',
            'post-trial C check did not confirm the independent WLAN vote')
    output = value.get('uart_output')
    require(isinstance(output, str) and
            re.search(r'(?m)^bridge_registered_hci=\d+\s*$', output) and
            re.search(r'(?m)^bridge_result=0\b', output) and
            re.search(r'(?m)^pty_cleanup_ioctl_result=0\s*$', output),
            'controller-trial receipt does not confirm HCI registration and TTY detach')
    uart_stderr = value.get('uart_stderr')
    require(isinstance(uart_stderr, str) and
            re.search(r'(?m)^stage=power_off_and_vote_restored\s*$', uart_stderr),
            'controller-trial receipt does not confirm BT power-off and WLAN vote restoration')
    require(value.get('strace_capture_path') == TRIAL_DIR + '/' + TRACE_NAME,
            'controller-trial trace path differs from the exclusive reservation')
    trace_path = path.parent / 'strace.txt'
    trace_bytes = read_durable_private_file(trace_path, 'private syscall trace receipt')
    require(bool(trace_bytes) and value.get('strace_capture_bytes') == len(trace_bytes) and
            value.get('strace_capture_sha256') == hashlib.sha256(trace_bytes).hexdigest(),
            'private syscall trace is empty or differs from its receipt hash')
    delta_path = path.parent / 'kernel-delta.txt'
    delta_classification = classify_kernel_delta(
        read_durable_private_file(delta_path, 'post-trial kernel delta'))
    if require_live:
        live = value.get('live_postflight')
        require(isinstance(live, dict) and live.get('boot_id') == after['boot_id'] and
                live.get('gnu_build_id') == EXPECTED_GNU_BUILD_ID and
                live.get('recovery_sha256') == EXPECTED_RECOVERY_SHA256 and
                live.get('controllers') == [] and live.get('network_ready') is True and
                live.get('native_model_ready') is True and live.get('power_safe') is True and
                live.get('serious_fault') is False and live.get('kernel_capture_complete') is True and
                live.get('kernel_coverage_complete') is True and
                live.get('kernel_assessment') in ('no_indicators', 'trace_only') and
                live.get('fatal_indicators') == [] and
                type(live.get('hung_task_warning_count')) is int and
                live.get('hung_task_warning_count') == 0 and
                live.get('liveness_unresolved') is False and
                delta_classification.get('fatal_indicators') == [] and
                delta_classification.get('hung_task_warning_count') == 0 and
                delta_classification.get('liveness_unresolved') is False,
                'independent postflight does not prove exact candidate and healthy controller-free boot')
    return path


def validate_live_postflight(observer, before, *, snapshot_reader=None,
                             candidate_hasher=None, controller_reader=None,
                             trusted_root=TRUSTED_ROOT):
    """Re-read the pinned running candidate and health after confirmed detach."""
    if snapshot_reader is None:
        snapshot_reader = lambda: observer.snapshot_over(
            'usb', None, project_root=trusted_root)
    current = snapshot_reader()
    observer.validate_snapshot(current, post_reboot=True)
    require(current.get('boot_id') == before.get('boot_id'),
            'post-trial snapshot is not from the original candidate boot')
    require(current.get('gnu_build_id') == EXPECTED_GNU_BUILD_ID,
            'post-trial GNU build ID differs from the reviewed candidate')
    require(observer.network_state_valid(current.get('network_state')),
            'post-trial WLAN baseline is not healthy')
    if candidate_hasher is None:
        candidate_hasher = lambda: observer.candidate_hash(
            'usb', None, project_root=trusted_root)
    recovery_hash = candidate_hasher()
    require(recovery_hash == EXPECTED_RECOVERY_SHA256,
            'post-trial full RECOVERY hash differs from the reviewed candidate')
    if controller_reader is None:
        controller_reader = lambda: read_live_controller_state(
            observer, trusted_root=trusted_root)
    state = controller_reader()
    require(state.get('boot_id') == current.get('boot_id') and
            state.get('controllers') == [],
            'post-trial controller is not detached on the same boot')
    kernel = current.get('kernel_log_classification')
    require(isinstance(kernel, dict) and
            isinstance(kernel.get('fatal_indicators'), list) and
            type(kernel.get('hung_task_warning_count')) is int and
            type(kernel.get('liveness_unresolved')) is bool,
            'post-trial kernel diagnostic fields are unavailable')
    require(kernel['fatal_indicators'] == [] and
            kernel['hung_task_warning_count'] == 0 and
            kernel['liveness_unresolved'] is False and
            kernel.get('assessment') in ('no_indicators', 'trace_only'),
            'post-trial kernel log reports fatal/hung indicators or unresolved liveness')
    readiness = current.get('readiness')
    return {
        'boot_id': current['boot_id'],
        'gnu_build_id': current['gnu_build_id'],
        'recovery_sha256': recovery_hash,
        'controllers': [],
        'network_ready': True,
        'native_model_ready': True,
        'power_safe': True,
        'serious_fault': current.get('serious_fault'),
        'assistant_idle': current.get('assistant_idle'),
        'readiness': readiness,
        'kernel_capture_complete': kernel.get('capture_complete') is True,
        'kernel_coverage_complete': kernel.get('coverage_complete') is True,
        'kernel_assessment': kernel.get('assessment'),
        'hung_task_warning_count': kernel.get('hung_task_warning_count'),
        'fatal_indicators': kernel.get('fatal_indicators'),
        'liveness_unresolved': kernel.get('liveness_unresolved'),
    }


def persist_live_postflight(receipt_path, live_postflight):
    """Durably add independently checked postflight to the private receipt."""
    path = Path(receipt_path)
    parent_info = path.parent.lstat()
    require(stat.S_ISDIR(parent_info.st_mode) and
            parent_info.st_uid == os.geteuid() and stat.S_IMODE(parent_info.st_mode) == 0o700,
            'controller-trial receipt directory is not private')
    info = _regular_file(path, 'controller-trial receipt')
    require(info.st_uid == os.geteuid() and not (info.st_mode & 0o077) and info.st_nlink == 1,
            'controller-trial receipt is not owner-private')
    fd = os.open(path, os.O_RDWR | os.O_CLOEXEC | getattr(os, 'O_NOFOLLOW', 0))
    try:
        current = os.fstat(fd)
        named = path.lstat()
        require((current.st_dev, current.st_ino) == (named.st_dev, named.st_ino),
                'controller-trial receipt identity changed')
        with os.fdopen(os.dup(fd), 'r', encoding='utf-8') as stream:
            value = json.load(stream)
        require(isinstance(value, dict), 'controller-trial receipt is not an object')
        value['live_postflight'] = live_postflight
        data = (json.dumps(value, indent=2) + '\n').encode()
        os.lseek(fd, 0, os.SEEK_SET)
        offset = 0
        while offset < len(data):
            offset += os.write(fd, data[offset:])
        os.ftruncate(fd, len(data))
        os.fsync(fd)
        adapter_parent = path.parent.lstat()
        require((parent_info.st_dev, parent_info.st_ino) ==
                (adapter_parent.st_dev, adapter_parent.st_ino),
                'controller-trial receipt directory identity changed')
        directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0) |
                               getattr(os, 'O_NOFOLLOW', 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        os.close(fd)
    return path


def read_durable_private_file(path, label):
    """Read and fsync an owner-only evidence file before terminalization."""
    path = Path(path)
    info = _regular_file(path, label)
    require(info.st_uid == os.geteuid() and not (info.st_mode & 0o077) and
            info.st_nlink == 1,
            f'{label} is not an owner-private regular file')
    parent_info = path.parent.lstat()
    require(stat.S_ISDIR(parent_info.st_mode) and parent_info.st_uid == os.geteuid() and
            stat.S_IMODE(parent_info.st_mode) == 0o700,
            f'{label} parent directory is not private')
    fd = os.open(path, os.O_RDWR | os.O_CLOEXEC | getattr(os, 'O_NOFOLLOW', 0))
    try:
        current = os.fstat(fd)
        named = path.lstat()
        require((current.st_dev, current.st_ino) == (named.st_dev, named.st_ino),
                f'{label} identity changed')
        data = bytearray()
        while True:
            block = os.read(fd, 1024 * 1024)
            if not block:
                break
            data.extend(block)
        os.fsync(fd)
        directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0) |
                               getattr(os, 'O_NOFOLLOW', 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        return bytes(data)
    finally:
        os.close(fd)


def configure_board(board):
    board.SOURCE = SOURCE
    board.BINARY = ARTIFACT
    board.DEST = DEST
    board.TRACE_DIR = TRIAL_TRACE_DIR
    board.PHONE_TIMEOUT = 35
    board.HOST_TIMEOUT = 45
    board.EXTRA_SOURCES = [
        ROOT / 'tools/hardware/bt-h4-ibs-bridge.c',
        ROOT / 'tools/hardware/bt-qca6490-runtime-reset.c',
        ROOT / 'tools/hardware/bt-qca6490-nvm-capture.c',
        ROOT / 'tools/hardware/bt-qca6490-patch-capture.c',
        ROOT / 'tools/hardware/bt-qca6490-baud-probe.c',
        ROOT / 'tools/hardware/bt-qca6490-patch-version-probe.c',
        ROOT / 'tools/hardware/bt-version-transport-probe.c',
        Path(__file__).resolve(),
    ]
    board.NOTE = (
        'One controller-registration trial. The verified QCA patch/NVM/'
        'reset path is followed by N_HCI registration; pinned hci_register_dev '
        'queues kernel automatic HCI initialization/power-on. No explicit '
        'HCIDEVUP, scan, advertising, connection, or pairing command. Detach '
        'follows a 20-second userspace H4/IBS bridge loop, but TIOCSETD teardown '
        'synchronously waits for kernel work cancellation/close with no internal '
        'overall deadline. An outer timeout is an UNKNOWN outcome, not proof of '
        'detach or power-off. Preserve the independent WLAN vote. Stop on error; '
        'never retry or infer safe recovery from timeout.'
    )


def isolated_stage_command(command):
    """Run only the board's Python staging script without PYTHON* overrides."""
    try:
        words = shlex.split(command)
    except ValueError as error:
        raise GateError('board staging command is malformed') from error
    require(len(words) == 3 and words[0] == 'python3' and words[1] == '-c',
            'board staging requested an unexpected remote command')
    return shlex.join(('python3', '-I', '-c', words[2]))


def validate_stage_input(data):
    """Pin the bytes just read by board.main before they cross SSH."""
    require(isinstance(data, bytes) and len(data) == EXPECTED_ARTIFACT_SIZE,
            'staged bridge artifact size mismatch; refusing remote stage')
    require(hashlib.sha256(data).hexdigest() == EXPECTED_ARTIFACT_SHA256,
            'staged bridge artifact SHA-256 mismatch; refusing remote stage')


def run_board_main(board, name, observer, *, trusted_root=TRUSTED_ROOT):
    """Reuse board.main while pinning its transport to the original SSH root."""
    deployer = observer._trusted_deployer()
    original_spec = importlib.util.spec_from_file_location
    original_subprocess = board.subprocess
    original_argv = sys.argv

    class TrustedStageSubprocess:
        TimeoutExpired = subprocess.TimeoutExpired

        @staticmethod
        def run(argv, *, input=None, capture_output=False, timeout=None, **kwargs):
            require(not kwargs and capture_output and input is not None and
                    timeout is not None,
                    'board runner requested an unexpected subprocess operation')
            validate_stage_input(input)
            require(isinstance(argv, list) and len(argv) == 2 and
                    argv[0] == str(Path(board.ROOT) / 'tools/s22-ssh'),
                    'board staging did not use the expected wrapper command')
            return deployer.run_approved_ssh_wrapper(
                trusted_root / 'tools/s22-ssh', isolated_stage_command(argv[1]),
                input_data=input,
                timeout=timeout, project_root=trusted_root)

    def trusted_trial_spec(name_value, location, *args, **kwargs):
        if name_value == 'hardware_trial':
            location = trusted_root / 'tools/gpu-compat/run-trial.py'
        return original_spec(name_value, location, *args, **kwargs)

    try:
        board.subprocess = TrustedStageSubprocess
        importlib.util.spec_from_file_location = trusted_trial_spec
        sys.argv = [str(Path(board.__file__).resolve()), name, '--execute']
        return board.main()
    finally:
        board.subprocess = original_subprocess
        importlib.util.spec_from_file_location = original_spec
        sys.argv = original_argv


def run_trial(name, *, observer, board, local_validator=None,
              transport_validator=None, snapshot_reader=None, candidate_hasher=None,
              controller_reader=None, board_invoker=None, workspace=ROOT,
              trusted_root=TRUSTED_ROOT, receipt=None,
              target_fs_checker=None, remote_reserver=None,
              operation_lock_factory=None):
    require(name == TRIAL_ID, 'only the exact controller-registration trial identity is allowed')
    if operation_lock_factory is None:
        guard = load_device_trial_guard()
        operation_lock_factory = guard.acquire_operation_lock
    with operation_lock_factory(workspace, name, 'bluetooth-controller-registration') as operation:
        path = trial_receipt_directory(workspace, name)
        require_unused_receipt_path(path)
        if local_validator is None:
            local_validator = lambda: validate_local_provenance(workspace)
        artifact_digest = local_validator()
        require(artifact_digest == EXPECTED_ARTIFACT_SHA256,
                'local artifact preflight did not return the reviewed SHA-256')
        if transport_validator is None:
            transport_validator = lambda: validate_trusted_transport(observer, trusted_root)
        transport_validator()
        result = validate_live_preflight(
            observer, receipt=receipt, snapshot_reader=snapshot_reader,
            candidate_hasher=candidate_hasher, controller_reader=controller_reader,
            trusted_root=trusted_root)
        if target_fs_checker is None:
            target_fs_checker = lambda: validate_remote_target_fs(
                observer, trusted_root=trusted_root)
        target_storage = target_fs_checker()
        validate_target_storage_summary(target_storage)
        configure_board(board)
        if remote_reserver is None:
            remote_reserver = lambda: reserve_remote_trial_dir(
                observer, trusted_root=trusted_root)
        if board_invoker is None:
            board_invoker = lambda module, trial_name: run_board_main(
                module, trial_name, observer, trusted_root=trusted_root)

        # This durable marker must precede the first remote write (the private
        # staging/trace-directory reservation). Any later timeout stays UNKNOWN.
        operation.begin(project_root=workspace)
        result['remote_trial_reservation'] = remote_reserver()
        result['target_storage'] = target_storage
        result['board_runner_result'] = board_invoker(board, name)
        receipt_path = validate_completed_trial_receipt(workspace, name, require_live=False)
        live_postflight = validate_live_postflight(
            observer, result, snapshot_reader=snapshot_reader,
            candidate_hasher=candidate_hasher, controller_reader=controller_reader,
            trusted_root=trusted_root)
        persist_live_postflight(receipt_path, live_postflight)
        validate_completed_trial_receipt(workspace, name)
        result['live_postflight'] = live_postflight
        operation.complete(receipt_path, outcome='success', cleanup_confirmed=True)
        result['cleanup_confirmed'] = True
        return result


def main(argv=None, *, dependencies=None):
    if not __debug__:
        print('optimized Python is refused for this one-shot trial runner', file=sys.stderr)
        return 2
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('name')
    parser.add_argument('--execute', action='store_true',
                        help='request the one live controller-registration trial')
    parser.add_argument('--ack-separate-controller-authorization', action='store_true',
                        help=('acknowledge separate owner authorization for this exact '
                              'trial, including kernel-queued automatic HCI init/power-on '
                              'and teardown with no internal overall deadline'))
    args = parser.parse_args(argv)
    if args.name != TRIAL_ID:
        print('refusing: trial identity must be exactly ' + TRIAL_ID, file=sys.stderr)
        return 2
    if not args.execute:
        print(LIVE_TRIAL_GATE, file=sys.stderr)
        return 1
    if not args.ack_separate_controller_authorization:
        print(LIVE_TRIAL_GATE, file=sys.stderr)
        return 1
    try:
        if dependencies is None:
            observer = load_observer()
            board = load_board()
            result = run_trial(args.name, observer=observer, board=board)
        else:
            result = run_trial(args.name, **dependencies)
    except (GateError, OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f'refusing controller-registration trial: {error}', file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
