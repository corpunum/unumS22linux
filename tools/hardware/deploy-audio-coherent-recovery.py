#!/usr/bin/env python3
"""Host plan and guarded stage/flash adapter for the pinned audio profiles.

This adapter is deliberately inactive until a separate reviewed change records
owner authorization.  The reserved trial identity is descriptive data only.
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
import stat
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
ARTIFACT_ROOT = Path('/home/corpunum/s22-linux')
STATE_ROOT = Path('/home/corpunum/.local/state/s22-device-trial-guard')
RECEIPTS_ROOT = Path.home() / '.local/state/s22-audio-coherent-trial/receipts'
TRIAL_IDENTITY = 'audio-coherent-20261003-first'

# No authorization record exists.  Never add a CLI acknowledgement as a
# substitute; activation requires a separately reviewed source change after
# the coordinator records the actual owner instruction.
CURRENT_AUDIO_EXECUTION_AUTHORIZED = False
EXECUTION_AUTHORIZED_TRIALS = frozenset()

PINNED_SOURCES = {
    'tools/hardware/audio-coherent-recovery-profile.py':
        '7a24b600d8e8054ce4266a084b7d227a108928eb4319b548a1bc34bd88e407d4',
    'tools/hardware/deploy-audio-recovery.py':
        '4138413c16b9c90c70a46da6ed3b4f60b0f41f1c6c69b4b2047421895903a47f',
    'tools/hardware/device-trial-guard.py':
        '6380fca8137a14f3574fe7bb29d644f12c522ec5e45e7915a38a48d81b4e6d30',
    'tools/hardware/deploy-camera-recovery.py':
        '2c8554a5d17324b52765fa348fa047814f78116cfe3bcf5cb506aba1c6ee97de',
    'tools/hardware/camera-recovery-reboot-once.py':
        'c97f0f161ec31149ce9e8ec989e895adef4802379e5888e3f1fabf31c092fe95',
    'tools/hardware/audio-recovery-reboot-once.py':
        '5ec8fec300d514ef9e586dfe5635ca38995718acd0437caefe50c34358bd1ac4',
    'tools/pi-web/pi_readiness.py':
        '61cb4f4640409d013ca1d65d5c52959d62610c71f5d76cc2d2e117b1be228e4f',
    'tools/hardware/trustzone_log_classifier.py':
        '8faa2cd46d16818ebdd61d7a577b046489083f5c96a6a398ddea769ebbb6619a',
    'tools/s22-ssh':
        '7e9d31035762de50ccc6c5614d8348532fd912bf59c41c9d410a4c7bfe49dd1d',
}

RUNNING_KERNEL_BUILD_ID = 'b2dda820b18d410d9bf12f1bd2584567d545991d'
AUDIO_MODULES = {
    'snd_soc_samsung_abox': {
        'baseline': '34a5354a75980688ee7dbeb6a848e04a7d54558f',
        'candidate': '26347c3373e155fa6badf7883ff162f1d9f6723f',
    },
    'rainbow_prince': {
        'baseline': '510b984887b640ad4ad3c1e9a556ef16c30b38c9',
        'candidate': '8a7227b58cb7f4faf73ea92974781d34870bbfac',
    },
    'exynos_usb_audio_offloading': {
        'baseline': '4f35c50b0eca0d22b2060f7b8d84f03359feacd1',
        'candidate': '8c9b0d4787ea32eae7de0086a4d662b0f351675d',
    },
}


def expected_profile_modules(profile_name: str, *, before: bool) -> dict[str, str]:
    """Return the exact pinned loaded IDs for one side of a profile change."""
    profile = PROFILE.resolve_profile(profile_name)
    role_key = 'before_role' if before else 'target_role'
    role = profile.get(role_key)
    _require(role in ('camera_recovery_baseline', 'audio_candidate'),
             f'audio profile has an unknown {role_key}')
    module_role = 'candidate' if role == 'audio_candidate' else 'baseline'
    return {name: values[module_role] for name, values in AUDIO_MODULES.items()}


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f'cannot load pinned helper: {path}')
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def verify_source_pins(project_root: Path = ROOT) -> dict[str, str]:
    """Recheck every helper used for dispatch or observation before use."""
    actual = {}
    for relative, expected in PINNED_SOURCES.items():
        path = Path(project_root) / relative
        try:
            data = path.read_bytes()
        except OSError as error:
            raise RuntimeError(f'pinned helper is unavailable: {relative}: {error}') from error
        digest = _sha256(data)
        if digest != expected:
            raise RuntimeError(f'pinned helper changed: {relative}: {digest}')
        actual[relative] = digest
    return actual


PROFILE = _load_module(
    's22_audio_coherent_execution_profile',
    ROOT / 'tools/hardware/audio-coherent-recovery-profile.py')
SHARED = PROFILE.DEPLOY
GUARD = _load_module(
    's22_audio_coherent_execution_guard', ROOT / 'tools/hardware/device-trial-guard.py')
CAMERA_DEPLOY = _load_module(
    's22_audio_coherent_camera_identity_contract',
    ROOT / 'tools/hardware/deploy-camera-recovery.py')
CAMERA_OBSERVER = _load_module(
    's22_audio_coherent_reboot_contract',
    ROOT / 'tools/hardware/camera-recovery-reboot-once.py')
AUDIO_OBSERVER = _load_module(
    's22_audio_coherent_native_readiness_contract',
    ROOT / 'tools/hardware/audio-recovery-reboot-once.py')


def require_execution_authorized(trial_identity: str) -> None:
    if CURRENT_AUDIO_EXECUTION_AUTHORIZED is not True:
        raise PermissionError('audio RECOVERY execution is not currently owner-authorized')
    if trial_identity not in EXECUTION_AUTHORIZED_TRIALS:
        raise PermissionError('trial identity is not in the reviewed audio execution allowlist')
    if trial_identity != PROFILE.TRIAL_IDENTITY or trial_identity != TRIAL_IDENTITY:
        raise PermissionError('audio execution trial identity does not match the pinned profile')


def operation_id(profile_name: str, mode: str) -> str:
    profile = PROFILE.resolve_profile(profile_name)
    _require(mode in ('stage', 'flash'), 'unsupported operation marker kind')
    direction = profile_name.removeprefix('audio-')
    return f'{profile["trial_identity"]}-{direction}-{mode}'


def identity_snapshot_script() -> str:
    """Extend the reviewed read-only recovery identity capture with audio IDs."""
    marker = "print(json.dumps(state,separators=(',',':')))"
    source = CAMERA_DEPLOY.CAMERA_IDENTITY_SNAPSHOT
    _require(source.count(marker) == 1,
             'pinned identity template has an ambiguous output boundary')
    extra = r'''
loaded_audio_names={line.split()[0] for line in modules.splitlines() if line.split()}
audio_modules={}
for name in ('snd_soc_samsung_abox','rainbow_prince','exynos_usb_audio_offloading'):
 loaded=name in loaded_audio_names
 audio_modules[name]={'loaded':loaded,
  'gnu_build_id':build_id(readb('/sys/module/'+name+'/notes/.note.gnu.build-id')) if loaded else None}
state['schema']='s22-audio-coherent-recovery-identity/v1'
state['audio_modules']=audio_modules
'''
    # The camera identity template has already read /proc/modules into `modules`
    # as part of its pinned FIMC-IS validation block.
    _require('modules=read_complete_proc_text' in source,
             'pinned identity template no longer exposes its module inventory')
    rendered = source.replace(marker, extra + '\n' + marker)
    compile(rendered, 'audio-coherent-recovery-identity', 'exec')
    return rendered


def validate_audio_identity(snapshot: object, *, expected_recovery_sha: str,
                            expected_modules: dict[str, str] | None = None) -> dict:
    _require(isinstance(snapshot, dict) and
             snapshot.get('schema') == 's22-audio-coherent-recovery-identity/v1',
             'audio identity snapshot has an invalid schema')
    _require(set(snapshot) == {
        'schema', 'boot_id', 'pid1', 'native_ready', 'kernel_release',
        'kernel_gnu_build_id', 'recovery_sha256', 'recovery_target',
        'camera_module_loaded', 'camera_module_gnu_build_id', 'audio_modules',
    }, 'audio identity snapshot has unexpected fields')
    boot_id = snapshot.get('boot_id')
    _require(type(boot_id) is str and re.fullmatch(
        r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}', boot_id) is not None,
        'audio identity snapshot has no canonical boot ID')
    _require(snapshot.get('recovery_sha256') == expected_recovery_sha,
             'audio identity RECOVERY image hash mismatch')
    target = snapshot.get('recovery_target')
    _require(isinstance(target, dict), 'audio identity has no RECOVERY target proof')
    for key, expected in {
        'alias_target': '/dev/sda16', 'rdev': '259:0',
        'capacity_bytes': PROFILE.PARTITION_SIZE,
        'sysfs_sectors': PROFILE.PARTITION_SIZE // 512,
        'partition_name': 'recovery', 'mounted': False,
    }.items():
        _require(type(target.get(key)) is type(expected) and target.get(key) == expected,
                 f'audio RECOVERY target mismatch for {key}')
    _require(snapshot.get('pid1') == 'native-guardian' and
             snapshot.get('native_ready') is True,
             'native recovery context is not ready')
    _require(snapshot.get('kernel_gnu_build_id') == RUNNING_KERNEL_BUILD_ID,
             'running kernel GNU build ID is not the pinned recovery kernel')
    _require(type(snapshot.get('kernel_release')) is str and
             bool(snapshot['kernel_release'].strip()),
             'audio identity kernel release is unavailable')
    _require(type(snapshot.get('camera_module_loaded')) is bool,
             'preserved camera module state is malformed')
    camera_build_id = snapshot.get('camera_module_gnu_build_id')
    _require((snapshot['camera_module_loaded'] and type(camera_build_id) is str and
              re.fullmatch(r'[0-9a-f]{40}', camera_build_id) is not None) or
             (not snapshot['camera_module_loaded'] and camera_build_id is None),
             'preserved camera module GNU build ID is malformed')
    modules = snapshot.get('audio_modules')
    _require(isinstance(modules, dict) and set(modules) == set(AUDIO_MODULES),
             'audio module identity inventory is incomplete')
    for name, record in modules.items():
        _require(isinstance(record, dict) and set(record) == {'loaded', 'gnu_build_id'} and
                 type(record.get('loaded')) is bool,
                 f'audio module state is malformed: {name}')
        build_id = record.get('gnu_build_id')
        if record['loaded']:
            _require(type(build_id) is str and re.fullmatch(r'[0-9a-f]{40}', build_id) is not None,
                     f'loaded audio module has no GNU build ID: {name}')
        else:
            _require(build_id is None, f'unloaded audio module unexpectedly has a build ID: {name}')
    if expected_modules is not None:
        _require(set(expected_modules) == set(AUDIO_MODULES),
                 'expected audio module set is incomplete')
        for name, expected in expected_modules.items():
            record = modules[name]
            _require(record['loaded'] is True and record['gnu_build_id'] == expected,
                     f'loaded module GNU build ID mismatch: {name}')
    return snapshot


def _read_private_json(path: Path, *, max_bytes: int = 1_048_576) -> tuple[dict, bytes]:
    path = Path(path)
    if not path.is_absolute() or path.name in ('', '.', '..'):
        raise ValueError('audio receipt path must be absolute with a safe basename')
    directory_fd = SHARED.open_private_receipt_directory(path.parent)
    try:
        parent_open = os.fstat(directory_fd)
        parent_now = path.parent.lstat()
        _require((parent_open.st_dev, parent_open.st_ino) ==
                 (parent_now.st_dev, parent_now.st_ino),
                 'audio receipt directory changed while opening')
        before = os.stat(path.name, dir_fd=directory_fd, follow_symlinks=False)
        _require(stat.S_ISREG(before.st_mode) and before.st_uid == os.geteuid() and
                 stat.S_IMODE(before.st_mode) == 0o600 and before.st_nlink == 1 and
                 before.st_size <= max_bytes,
                 'audio receipt must be a bounded private regular file')
        fd = os.open(path.name, os.O_RDONLY | os.O_CLOEXEC | getattr(os, 'O_NOFOLLOW', 0),
                     dir_fd=directory_fd)
        try:
            opened = os.fstat(fd)
            _require(stat.S_ISREG(opened.st_mode) and opened.st_uid == os.geteuid() and
                     stat.S_IMODE(opened.st_mode) == 0o600 and opened.st_nlink == 1 and
                     (opened.st_dev, opened.st_ino) == (before.st_dev, before.st_ino),
                     'audio receipt identity changed while opening')
            content = bytearray()
            while len(content) <= max_bytes:
                block = os.read(fd, min(65536, max_bytes + 1 - len(content)))
                if not block:
                    break
                content.extend(block)
            after = os.fstat(fd)
            current = os.stat(path.name, dir_fd=directory_fd, follow_symlinks=False)
            _require(len(content) <= max_bytes and len(content) == opened.st_size == after.st_size and
                     (opened.st_dev, opened.st_ino, opened.st_mtime_ns, opened.st_ctime_ns) ==
                     (after.st_dev, after.st_ino, after.st_mtime_ns, after.st_ctime_ns) and
                     (opened.st_dev, opened.st_ino) == (current.st_dev, current.st_ino),
                     'audio receipt changed while being read')
        finally:
            os.close(fd)
    finally:
        os.close(directory_fd)
    try:
        value = json.loads(content, object_pairs_hook=PROFILE._unique_json_object)
    except (TypeError, ValueError, UnicodeDecodeError) as error:
        raise ValueError('audio receipt is invalid or has duplicate JSON fields') from error
    _require(isinstance(value, dict), 'audio receipt must be a JSON object')
    return value, bytes(content)


def _validate_operation_marker(state_root: Path, marker_id: str, *, operation_kind: str,
                               receipt_path: Path) -> dict:
    marker = GUARD._read_marker(Path(state_root) / f'{marker_id}.json')
    receipt_bytes = SHARED.read_host_artifact(receipt_path, 'bound audio operation receipt')
    _require(marker.get('trial_id') == marker_id and
             marker.get('operation_kind') == operation_kind and
             marker.get('status') == 'complete' and marker.get('outcome') == 'success' and
             marker.get('receipt_sha256') == _sha256(receipt_bytes) and
             marker.get('receipt_path') == str(receipt_path),
             'audio operation receipt is not bound to its terminal guard marker')
    return marker


def validate_stage_receipt(profile_name: str, receipt_dir: Path, state_root: Path) -> tuple[dict, bytes]:
    profile = PROFILE.resolve_profile(profile_name)
    path = Path(receipt_dir) / f'{profile_name}-stage.json'
    receipt, content = _read_private_json(path)
    PROFILE.validate_receipt(profile_name, receipt)
    _require(receipt.get('mode') == 'stage', 'stage receipt mode mismatch')
    _validate_operation_marker(
        state_root, operation_id(profile_name, 'stage'),
        operation_kind=f'audio-coherent-{profile_name.removeprefix("audio-")}-stage',
        receipt_path=path)
    return receipt, content


def validate_flash_receipt(profile_name: str, receipt: object, *, state_root: Path,
                           receipt_path: Path | None = None,
                           validate_guard_markers: bool = True) -> dict:
    profile = PROFILE.resolve_profile(profile_name)
    _require(isinstance(receipt, dict) and
             receipt.get('schema') == 's22-audio-coherent-flash-bound/v1',
             'bound audio flash receipt schema mismatch')
    _require(receipt_path is not None and
             Path(receipt_path).name == f'{profile_name}-flash.json' and
             Path(receipt_path).parent.name == profile['trial_identity'],
             'bound audio flash receipt path is not the exact trial/profile path')
    _require(set(receipt) == {
        'schema', 'profile', 'trial_identity', 'mode', 'partition',
        'partition_written', 'bytes', 'before_sha256', 'readback_sha256',
        'reboot_performed', 'remote_receipt', 'remote_receipt_sha256',
        'stage_receipt_sha256', 'prewrite_identity_receipt_sha256',
        'prewrite_boot_id', 'prewrite_identity', 'postwrite_identity',
        'prewrite_postwrite_same_boot', 'reboot_authorized', 'audio_acceptance',
    }, 'bound audio flash receipt has missing or unexpected fields')
    expected = {
        'profile': profile_name,
        'trial_identity': profile['trial_identity'],
        'mode': 'flash',
        'partition': 'recovery',
        'partition_written': 'recovery',
        'bytes': PROFILE.PARTITION_SIZE,
        'before_sha256': profile['before_sha256'],
        'readback_sha256': profile['target_sha256'],
        'reboot_performed': False,
        'prewrite_postwrite_same_boot': True,
        'reboot_authorized': False,
        'audio_acceptance': False,
    }
    for key, value in expected.items():
        _require(type(receipt.get(key)) is type(value) and receipt.get(key) == value,
                 f'bound audio flash receipt mismatch for {key}')
    remote = receipt.get('remote_receipt')
    PROFILE.validate_receipt(profile_name, remote)
    _require(remote.get('mode') == 'flash' and
             type(receipt.get('remote_receipt_sha256')) is str and
             re.fullmatch(r'[0-9a-f]{64}', receipt['remote_receipt_sha256']) is not None,
             'raw audio flash receipt binding mismatch')
    _require(type(receipt.get('stage_receipt_sha256')) is str and
             re.fullmatch(r'[0-9a-f]{64}', receipt['stage_receipt_sha256']) is not None,
             'audio flash receipt stage binding is missing or malformed')
    pre = receipt.get('prewrite_identity')
    post = receipt.get('postwrite_identity')
    validate_audio_identity(
        pre, expected_recovery_sha=profile['before_sha256'],
        expected_modules=expected_profile_modules(profile_name, before=True))
    validate_audio_identity(post, expected_recovery_sha=profile['target_sha256'])
    _require(pre['boot_id'] == post['boot_id'] == receipt.get('prewrite_boot_id') and
             pre['kernel_gnu_build_id'] == post['kernel_gnu_build_id'] == RUNNING_KERNEL_BUILD_ID and
             pre['audio_modules'] == post['audio_modules'] and
             pre['camera_module_loaded'] == post['camera_module_loaded'] and
             pre['camera_module_gnu_build_id'] == post['camera_module_gnu_build_id'],
             'flash receipt does not prove a same-boot unchanged loaded-module window')
    if receipt_path is not None:
        stage_path = Path(receipt_path).parent / f'{profile_name}-stage.json'
        stage_value, stage_bytes = _read_private_json(stage_path)
        PROFILE.validate_receipt(profile_name, stage_value)
        _require(_sha256(stage_bytes) == receipt['stage_receipt_sha256'],
                 'flash receipt is not linked to its exact stage receipt')
        _validate_operation_marker(
            state_root, operation_id(profile_name, 'stage'),
            operation_kind=f'audio-coherent-{profile_name.removeprefix("audio-")}-stage',
            receipt_path=stage_path)
        prewrite_path = Path(receipt_path).parent / f'{profile_name}-flash-prewrite.json'
        pre_record, pre_bytes = _read_private_json(prewrite_path)
        _require(set(pre_record) == {
            'schema', 'profile', 'trial_identity', 'expected_write_sha256', 'identity',
        } and pre_record.get('schema') == 's22-audio-coherent-prewrite/v1' and
                pre_record.get('profile') == profile_name and
                pre_record.get('trial_identity') == profile['trial_identity'] and
                pre_record.get('expected_write_sha256') == profile['target_sha256'] and
                pre_record.get('identity') == pre,
                'prewrite identity receipt is not bound to the flash receipt')
        _require(_sha256(pre_bytes) == receipt.get('prewrite_identity_receipt_sha256'),
                 'flash receipt does not hash the exact prewrite identity receipt')
        raw_path = Path(receipt_path).parent / f'{profile_name}-flash-remote.json'
        raw_value, raw_bytes = _read_private_json(raw_path)
        _require(raw_value == remote and _sha256(raw_bytes) == receipt['remote_receipt_sha256'],
                 'flash receipt does not hash the exact remote write/readback receipt')
        if validate_guard_markers:
            _validate_operation_marker(
                state_root, operation_id(profile_name, 'flash'),
                operation_kind=f'audio-coherent-{profile_name.removeprefix("audio-")}-flash',
                receipt_path=receipt_path)
    return receipt


def _profile_image(profile_name: str, *, artifact_root: Path) -> tuple[dict, bytes]:
    metadata = PROFILE.validate_profile_artifacts(profile_name, artifact_root=artifact_root)
    profile = PROFILE.resolve_profile(profile_name)
    target = Path(artifact_root) / profile['target_image']
    data = SHARED.read_host_artifact(target, 'pinned audio target recovery image')
    _require(len(data) == PROFILE.PARTITION_SIZE and
             _sha256(data) == profile['target_sha256'],
             'audio target image changed after manifest validation')
    return {**profile, 'artifact_validation': metadata}, data


def _remote_json(command: str, *, input_data: bytes, timeout: int | None,
                 project_root: Path, transport=None):
    if transport is not None:
        return transport(ARTIFACT_ROOT / 'tools/s22-ssh', command,
                         input_data=input_data, timeout=timeout,
                         project_root=project_root)
    return SHARED.run_approved_ssh_wrapper(
        ARTIFACT_ROOT / 'tools/s22-ssh', command, input_data=input_data,
        timeout=timeout, project_root=project_root)


def capture_audio_identity(*, expected_recovery_sha: str, project_root: Path = ROOT,
                           transport=None) -> dict:
    command = 'python3 -I -B -c ' + shlex.quote(identity_snapshot_script())
    try:
        result = _remote_json(command, input_data=b'', timeout=60,
                              project_root=project_root, transport=transport)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RuntimeError('read-only audio identity query outcome is unknown') from error
    if result.returncode != 0:
        raise RuntimeError('read-only audio identity query failed; do not retry a write')
    try:
        snapshot = json.loads(result.stdout, object_pairs_hook=PROFILE._unique_json_object)
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise RuntimeError('read-only audio identity query returned invalid JSON') from error
    return validate_audio_identity(snapshot, expected_recovery_sha=expected_recovery_sha)


def _require_stage_marker(profile_name: str, receipt_dir: Path, state_root: Path):
    return validate_stage_receipt(profile_name, receipt_dir, state_root)


def _run_profile_operation(profile_name: str, mode: str, *, project_root: Path = ROOT,
                           artifact_root: Path = ARTIFACT_ROOT,
                           receipt_dir: Path | None = None,
                           state_root: Path = STATE_ROOT,
                           transport=None, identity_reader=None) -> dict:
    profile = PROFILE.resolve_profile(profile_name)
    trial_identity = profile['trial_identity']
    require_execution_authorized(trial_identity)
    verify_source_pins(project_root)
    _require(mode in ('stage', 'flash'), 'audio operation must be stage or flash')
    profile, image = _profile_image(profile_name, artifact_root=artifact_root)
    if receipt_dir is None:
        receipt_dir = RECEIPTS_ROOT / trial_identity
    receipt_dir = Path(receipt_dir)
    _require(receipt_dir.name == trial_identity,
             'audio receipt directory must be namespaced by the reserved trial identity')
    receipt_dir = SHARED.prepare_private_receipt_directory(receipt_dir)
    receipt_fd = SHARED.open_private_receipt_directory(receipt_dir)
    output = receipt_dir / f'{profile_name}-{mode}.json'
    prewrite_path = receipt_dir / f'{profile_name}-flash-prewrite.json'
    raw_path = receipt_dir / f'{profile_name}-flash-remote.json'
    try:
        SHARED.ensure_new_receipt(output, directory_fd=receipt_fd)
        if mode == 'flash':
            SHARED.ensure_new_receipt(prewrite_path, directory_fd=receipt_fd)
            SHARED.ensure_new_receipt(raw_path, directory_fd=receipt_fd)
            stage_receipt, stage_bytes = _require_stage_marker(
                profile_name, receipt_dir, Path(state_root))
            stage_sha = _sha256(stage_bytes)
        else:
            stage_receipt = None
            stage_sha = None

        ssh_path = ARTIFACT_ROOT / 'tools/s22-ssh'
        if transport is None:
            wrapper_fd = SHARED.validate_approved_ssh_wrapper(ssh_path)
            os.close(wrapper_fd)
        code = PROFILE.render_remote(profile_name)
        kind = f'audio-coherent-{profile_name.removeprefix("audio-")}-{mode}'
        with GUARD.acquire_operation_lock(
                project_root, operation_id(profile_name, mode), kind,
                state_root=state_root) as operation:
            operation.begin(project_root=project_root)
            try:
                prewrite = None
                command_code = code
                if mode == 'flash':
                    reader = identity_reader or (lambda expected: capture_audio_identity(
                        expected_recovery_sha=expected, project_root=project_root,
                        transport=transport))
                    prewrite = reader(profile['before_sha256'])
                    validate_audio_identity(
                        prewrite, expected_recovery_sha=profile['before_sha256'],
                        expected_modules=expected_profile_modules(profile_name, before=True))
                    pre_record = {
                        'schema': 's22-audio-coherent-prewrite/v1',
                        'profile': profile_name, 'trial_identity': trial_identity,
                        'expected_write_sha256': profile['target_sha256'],
                        'identity': prewrite,
                    }
                    SHARED.persist_receipt(prewrite_path, pre_record, directory_fd=receipt_fd)
                    command_code = CAMERA_DEPLOY.render_boot_bound_flash(code, prewrite['boot_id'])
                command = 'python3 -I -B -c ' + shlex.quote(command_code) + ' ' + mode
                result = _remote_json(
                    command, input_data=image if mode == 'stage' else b'',
                    timeout=100 if mode == 'stage' else None,
                    project_root=project_root, transport=transport)
                if result.returncode != 0:
                    detail = result.stderr.decode(errors='replace') if isinstance(result.stderr, bytes) else str(result.stderr)
                    raise RuntimeError(f'{mode} failed; remote outcome may be unknown; never retry: {detail[:512]}')
                try:
                    raw = json.loads(result.stdout, object_pairs_hook=PROFILE._unique_json_object)
                    SHARED.validate_remote_receipt(
                        raw, mode=mode, before_sha=profile['before_sha256'],
                        candidate_sha=profile['target_sha256'],
                        size=PROFILE.PARTITION_SIZE)
                    raw['profile'] = profile_name
                    raw['trial_identity'] = trial_identity
                    PROFILE.validate_receipt(profile_name, raw)
                    _require(raw.get('mode') == mode, 'remote audio operation mode mismatch')
                except (TypeError, ValueError, json.JSONDecodeError) as error:
                    raise RuntimeError(f'{mode} returned an invalid receipt; outcome may be unknown; never retry: {error}') from error

                if mode == 'stage':
                    SHARED.persist_receipt(output, raw, directory_fd=receipt_fd)
                    operation.complete(output, outcome='success', cleanup_confirmed=True)
                    return raw

                SHARED.persist_receipt(raw_path, raw, directory_fd=receipt_fd)
                raw_bytes = SHARED.read_host_artifact(raw_path, 'durable raw audio readback receipt')
                reader = identity_reader or (lambda expected: capture_audio_identity(
                    expected_recovery_sha=expected, project_root=project_root,
                    transport=transport))
                postwrite = reader(profile['target_sha256'])
                validate_audio_identity(postwrite, expected_recovery_sha=profile['target_sha256'])
                _require(postwrite['boot_id'] == prewrite['boot_id'] and
                         postwrite['kernel_gnu_build_id'] == prewrite['kernel_gnu_build_id'] and
                         postwrite['audio_modules'] == prewrite['audio_modules'],
                         'RECOVERY write/readback crossed a boot or loaded-module change')
                bound = {
                    'schema': 's22-audio-coherent-flash-bound/v1',
                    'profile': profile_name,
                    'trial_identity': trial_identity,
                    'mode': 'flash',
                    'partition': 'recovery',
                    'partition_written': raw['partition_written'],
                    'bytes': raw['bytes'],
                    'before_sha256': raw['before_sha256'],
                    'readback_sha256': raw['readback_sha256'],
                    'reboot_performed': raw['reboot_performed'],
                    'remote_receipt': raw,
                    'remote_receipt_sha256': _sha256(raw_bytes),
                    'stage_receipt_sha256': stage_sha,
                    'prewrite_identity_receipt_sha256': _sha256(
                        SHARED.read_host_artifact(prewrite_path,
                                                  'durable audio prewrite identity receipt')),
                    'prewrite_boot_id': prewrite['boot_id'],
                    'prewrite_identity': prewrite,
                    'postwrite_identity': postwrite,
                    'prewrite_postwrite_same_boot': True,
                    'reboot_authorized': False,
                    'audio_acceptance': False,
                }
                # Bind stage bytes in the receipt itself (not an implicit
                # private validator-only field) before making the guard terminal.
                bound['stage_receipt_sha256'] = stage_sha
                SHARED.persist_receipt(output, bound, directory_fd=receipt_fd)
                validate_flash_receipt(
                    profile_name, bound, state_root=Path(state_root),
                    receipt_path=output, validate_guard_markers=False)
                operation.complete(output, outcome='success', cleanup_confirmed=True)
                validate_flash_receipt(profile_name, bound, state_root=Path(state_root),
                                       receipt_path=output)
                return bound
            except (OSError, subprocess.TimeoutExpired) as error:
                raise RuntimeError(f'{mode} transport timed out/failed; outcome may be unknown; never retry: {error}') from error
    finally:
        os.close(receipt_fd)


def build_plan(profile_name: str, *, artifact_root: Path = ARTIFACT_ROOT) -> dict:
    plan = PROFILE.build_plan(profile_name, artifact_root=artifact_root)
    plan.update({
        'schema': 's22-audio-coherent-recovery-execution-plan/v1',
        'current_deployment_authorized': CURRENT_AUDIO_EXECUTION_AUTHORIZED,
        'execution_authorized_trial_allowlist': sorted(EXECUTION_AUTHORIZED_TRIALS),
        'execution_adapter_available': True,
        'execution': False,
        'operation_marker_created': False,
        'partition_written': False,
        'reboot_performed': False,
        'audio_acceptance': False,
    })
    return plan


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    choice = parser.add_mutually_exclusive_group()
    choice.add_argument('--stage', action='store_true')
    choice.add_argument('--flash', action='store_true')
    parser.add_argument('--profile', required=True, choices=tuple(PROFILE.PROFILES))
    parser.add_argument('--execute', action='store_true',
                        help='request the selected operation; this is not authorization')
    parser.add_argument('--trial-identity')
    parser.add_argument('--receipt-dir', type=Path)
    args = parser.parse_args(argv)
    mode = 'stage' if args.stage else 'flash' if args.flash else None
    if mode is None:
        if args.execute or args.trial_identity is not None or args.receipt_dir is not None:
            parser.error('execution arguments require --stage or --flash')
        print(json.dumps(build_plan(args.profile, artifact_root=ARTIFACT_ROOT),
                          indent=2, sort_keys=True))
        return 0
    if not args.execute:
        parser.error('--stage/--flash require explicit --execute intent')
    if args.trial_identity != PROFILE.TRIAL_IDENTITY:
        parser.error('audio operation trial identity does not match the reserved profile identity')
    # Refuse before artifact/receipt/lock setup while this trial is not
    # separately authorized. No CLI flag can alter this constant or allowlist.
    require_execution_authorized(args.trial_identity)
    result = _run_profile_operation(args.profile, mode, receipt_dir=args.receipt_dir)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError, PermissionError) as error:
        raise SystemExit(f'refused: {error}') from error
