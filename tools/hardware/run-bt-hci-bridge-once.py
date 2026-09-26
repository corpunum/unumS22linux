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
import shlex
import stat
import struct
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
TRUSTED_ROOT = Path('/home/corpunum/s22-linux')
TRIAL_ID = 'bt-hci-registration-20260926'
OBSERVER_TRIAL_ID = 'hci-candidate-20260924-second'
EXPECTED_RECOVERY_SHA256 = '42da267f3dd9f94f30f62a95fb2ac13f91d4cf98f1a2307f7cc14e45d9c49be5'
EXPECTED_GNU_BUILD_ID = 'b2dda820b18d410d9bf12f1bd2584567d545991d'
EXPECTED_ARTIFACT_SHA256 = 'c28307985bdad6404f0fecc82860eaa92a2c150a5d870d9297ce0301f44bac0a'
EXPECTED_ARTIFACT_BUILD_ID = 'eb47c232ddb7fc4b477bb145bbe9bf2832972276'
EXPECTED_SSH_WRAPPER_SHA256 = '7e9d31035762de50ccc6c5614d8348532fd912bf59c41c9d410a4c7bfe49dd1d'
EXPECTED_TRUSTED_RUN_TRIAL_SHA256 = '165a16566f2dedcb23506d427425ddb030acc53876710a61bb06a2f1b039a5fe'
EXPECTED_TRUSTED_DEPLOYER_SHA256 = 'd0c54cc306e3a8a80cb79d79958fceedf6bb5945dabc7606e02b12d27dc00fd3'
ARTIFACT = ROOT / 'builds/bt-next-20260926/bt-qca6490-hci-bridge-probe'
SOURCE = ROOT / 'tools/hardware/bt-qca6490-hci-bridge-probe.c'
DEST = '/srv/s22/bt-next-20260926/bt-qca6490-hci-bridge-probe'
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
        'f399575a3fa96bc8881b71391bc7e7ab91c931e89ce8cb5785ddea695e0ec537',
    'tools/hardware/run-bt-version-once.py':
        '93a84da810bccb517694b6cbeb44aa85850289919caec71aa7638c90d49c4653',
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
    artifact = artifact_path or (root / ARTIFACT.relative_to(ROOT))
    info = _regular_file(artifact, 'prebuilt bridge artifact')
    require(info.st_uid == os.geteuid() and (info.st_mode & 0o777) == 0o700,
            'prebuilt bridge artifact must be owner-owned mode 0700')
    data = artifact.read_bytes()
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


def configure_board(board):
    board.SOURCE = SOURCE
    board.BINARY = ARTIFACT
    board.DEST = DEST
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
              trusted_root=TRUSTED_ROOT, receipt=None):
    require(name == TRIAL_ID, 'only the exact controller-registration trial identity is allowed')
    path = trial_receipt_directory(workspace, name)
    require_unused_receipt_path(path)
    if local_validator is None:
        local_validator = lambda: validate_local_provenance(workspace)
    local_validator()
    if transport_validator is None:
        transport_validator = lambda: validate_trusted_transport(observer, trusted_root)
    transport_validator()
    result = validate_live_preflight(
        observer, receipt=receipt, snapshot_reader=snapshot_reader,
        candidate_hasher=candidate_hasher, controller_reader=controller_reader,
        trusted_root=trusted_root)
    configure_board(board)
    if board_invoker is None:
        board_invoker = lambda module, trial_name: run_board_main(
            module, trial_name, observer, trusted_root=trusted_root)
    result['board_runner_result'] = board_invoker(board, name)
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
