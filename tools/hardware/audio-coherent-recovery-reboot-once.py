#!/usr/bin/env python3
"""Request one guarded RECOVERY reboot, or take one read-only boot snapshot.

The reserved audio trial currently has no owner authorization.  The default
CLI is a host-only plan, and no argument can enable a request while the pinned
authorization constant/allowlist are false and empty.
"""
from __future__ import annotations

import argparse
from contextlib import nullcontext
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shlex
import stat
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
ARTIFACT_ROOT = Path('/home/corpunum/s22-linux')
RECEIPTS_ROOT = Path.home() / '.local/state/s22-audio-coherent-trial/receipts'
STATE_ROOT = Path('/home/corpunum/.local/state/s22-device-trial-guard')
TRIAL_IDENTITY = 'audio-coherent-20261003-first'

# Set to the frozen deployment-adapter source digest when this two-file unit is
# frozen. Empty is development-only and is never accepted by the CLI's source
# gate after commit.
DEPLOY_ADAPTER_SHA256 = '859047a5018d697e510ef77855c5f841b1ef2e8890fb9890d47ba8dcb2a4aa66'


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


ADAPTER_PATH = ROOT / 'tools/hardware/deploy-audio-coherent-recovery.py'
ADAPTER = _load_module('s22_audio_coherent_trial_adapter', ADAPTER_PATH)
SHARED = ADAPTER.SHARED
GUARD = ADAPTER.GUARD
PROFILE = ADAPTER.PROFILE
AUDIO = ADAPTER.AUDIO_OBSERVER
CAMERA = ADAPTER.CAMERA_OBSERVER


def verify_source_pins(project_root: Path = ROOT) -> dict[str, str]:
    actual = ADAPTER.verify_source_pins(project_root)
    if not DEPLOY_ADAPTER_SHA256:
        raise RuntimeError('audio execution adapter digest is not frozen')
    adapter_path = Path(project_root) / 'tools/hardware/deploy-audio-coherent-recovery.py'
    digest = hashlib.sha256(adapter_path.read_bytes()).hexdigest()
    if digest != DEPLOY_ADAPTER_SHA256:
        raise RuntimeError('audio execution adapter changed after observer review')
    actual[str(adapter_path.relative_to(project_root))] = digest
    return actual


def _direction(profile_name: str) -> str:
    _require(profile_name in PROFILE.PROFILES, 'unknown audio recovery profile')
    return profile_name.removeprefix('audio-')


def _reboot_marker_id(profile_name: str) -> str:
    profile = PROFILE.resolve_profile(profile_name)
    return f'{profile["trial_identity"]}-{_direction(profile_name)}-reboot'


def _observation_marker_id(profile_name: str) -> str:
    profile = PROFILE.resolve_profile(profile_name)
    return f'{profile["trial_identity"]}-{_direction(profile_name)}-observe'


def _receipt_paths(profile_name: str, receipts_root: Path = RECEIPTS_ROOT) -> dict[str, Path]:
    trial = PROFILE.resolve_profile(profile_name)['trial_identity']
    directory = Path(receipts_root) / trial
    return {
        'directory': directory,
        'stage': directory / f'{profile_name}-stage.json',
        'flash': directory / f'{profile_name}-flash.json',
        'reboot_attempt': directory / f'{profile_name}-reboot-attempt.json',
        'reboot_result': directory / f'{profile_name}-reboot-result.json',
        'observation': directory / f'{profile_name}-initial-observation.json',
    }


def _validate_receipt_marker(profile_name: str, mode: str, receipt_path: Path,
                             state_root: Path) -> dict:
    marker_id = (ADAPTER.operation_id(profile_name, mode) if mode in ('stage', 'flash')
                 else _reboot_marker_id(profile_name) if mode == 'reboot'
                 else _observation_marker_id(profile_name))
    kind = (f'audio-coherent-{_direction(profile_name)}-{mode}')
    return ADAPTER._validate_operation_marker(
        Path(state_root), marker_id, operation_kind=kind, receipt_path=Path(receipt_path))


def _validate_reboot_marker_for_observation(profile_name: str, result: dict,
                                            result_path: Path, state_root: Path) -> dict:
    marker_id = _reboot_marker_id(profile_name)
    marker = GUARD._read_marker(Path(state_root) / f'{marker_id}.json')
    kind = f'audio-coherent-{_direction(profile_name)}-reboot'
    _require(marker.get('trial_id') == marker_id and marker.get('operation_kind') == kind,
             'reboot guard marker belongs to another operation')
    if result.get('outcome') == 'ACKNOWLEDGED':
        _require(marker.get('status') == 'complete' and marker.get('outcome') == 'success' and
                 marker.get('receipt_path') == str(result_path) and
                 marker.get('receipt_sha256') == hashlib.sha256(
                     SHARED.read_host_artifact(result_path, 'one-shot reboot result')).hexdigest(),
                 'acknowledged reboot result is not bound to its completed guard marker')
    else:
        _require(result.get('outcome') == 'UNKNOWN' and marker.get('status') == 'unknown',
                 'unknown reboot outcome is not preserved by an unresolved guard marker')
    return marker


def _load_flash(profile_name: str, *, receipts_root: Path, state_root: Path) -> tuple[dict, bytes]:
    paths = _receipt_paths(profile_name, receipts_root)
    flash, content = ADAPTER._read_private_json(paths['flash'])
    ADAPTER.validate_flash_receipt(profile_name, flash, state_root=state_root,
                                   receipt_path=paths['flash'])
    _validate_receipt_marker(profile_name, 'flash', paths['flash'], state_root)
    return flash, content


def _expected_profile_modules(profile_name: str) -> dict[str, str]:
    return ADAPTER.expected_profile_modules(profile_name, before=False)


def validate_readiness_snapshot(state: object, *, post_reboot: bool,
                                expected_boot_id: str | None = None) -> dict:
    """Require generic native readiness; Pi/tmux/browser are recorded only."""
    _require(isinstance(state, dict), 'native readiness snapshot is not an object')
    _require(type(state.get('boot_id')) is str and bool(state['boot_id']),
             'native readiness snapshot has no boot ID')
    if expected_boot_id is not None:
        _require(state['boot_id'] == expected_boot_id,
                 'readiness and RECOVERY identity came from different boots')
    _require(state.get('pid1') == 'native-guardian' and state.get('native_ready') is True,
             'native PID1/readiness is not established')
    _require(AUDIO.ready_value(state.get('persistent_ready')),
             'persistent /srv/s22 readiness/mount is not established')
    _require(isinstance(state.get('health'), dict) and state['health'].get('status') == 'ok',
             'local model health endpoint is not healthy')
    slots = state.get('slots')
    _require(isinstance(slots, dict) and type(slots.get('count')) is int and
             slots['count'] > 0 and isinstance(slots.get('processing'), list) and
             len(slots['processing']) == slots['count'] and
             all(type(value) is bool and value is False for value in slots['processing']) and
             state.get('assistant_idle') is True,
             'model idleness is not proven by a nonempty idle slot sample')
    readiness = state.get('readiness')
    _require(isinstance(readiness, dict) and
             readiness.get('kernel_remote_control') is True and
             readiness.get('model_api_health') is True and
             readiness.get('model_idle') is True and
             readiness.get('desktop_environment') is True,
             'native/model/desktop readiness is not established')
    _require(state.get('serious_fault') is False,
             'serious-fault status is unknown or a fault was recorded')
    logs = state.get('kernel_log_classification')
    _require(isinstance(logs, dict) and isinstance(logs.get('fatal_indicators'), list) and
             not logs['fatal_indicators'] and type(logs.get('liveness_unresolved')) is bool and
             logs['liveness_unresolved'] is False,
             'available kernel diagnostics report a fatal or unresolved liveness condition')
    _require(AUDIO.network_state_valid(state.get('network_state')),
             'network readiness (wlan0 up/carrier) is not established')
    _require(AUDIO.power_state_valid(state.get('power_state')),
             'battery/thermal limits or thermal sensor coverage are not satisfied')
    _require(AUDIO.target_identity_valid(state),
             'device-tree and boot properties do not identify the pinned Samsung r0s')
    _require(type(state.get('gnu_build_id')) is str and
             state.get('gnu_build_id') == ADAPTER.RUNNING_KERNEL_BUILD_ID,
             'running kernel GNU build ID mismatch')
    _require(type(state.get('kernel_release')) is str and bool(state['kernel_release'].strip()),
             'running kernel release is unavailable')
    _require(AUDIO.recovery_record(state.get('boot_reset_first_record')),
             'latest boot record does not prove entry into RECOVERY')
    # dedicated_pi_session_status, browser_terminal_status and tmux are not
    # universal gates; preserve their observed values in the receipt if present.
    return state


def _readiness_script() -> str:
    script = AUDIO.render_snapshot_script()
    compile(script, 'audio-coherent-native-readiness', 'exec')
    return script


def capture_readiness_snapshot(*, project_root: Path = ROOT, transport=None) -> dict:
    command = 'python3 -I -B -c ' + shlex.quote(_readiness_script())
    try:
        result = ADAPTER._remote_json(command, input_data=b'', timeout=20,
                                      project_root=project_root, transport=transport)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RuntimeError('native readiness query outcome is unknown; no retry') from error
    if result.returncode != 0:
        raise RuntimeError('native readiness query failed; no retry')
    try:
        value = json.loads(result.stdout, object_pairs_hook=PROFILE._unique_json_object)
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise RuntimeError('native readiness query returned invalid JSON') from error
    _require(isinstance(value, dict), 'native readiness response must be an object')
    return value


def _expected_helpers() -> dict:
    return {
        'schema': 'camera-reboot-helpers/v1',
        'helpers': {
            'reboot': {
                'sha256': CAMERA.S22_REBOOT_SHA256, 'uid': 0, 'mode': 0o755,
                'regular': True, 'symlink': False,
            },
            'restart2': {
                'sha256': CAMERA.S22_RESTART2_SHA256, 'uid': 0, 'mode': 0o755,
                'regular': True, 'symlink': False,
            },
        },
    }


def capture_helper_snapshot(*, project_root: Path = ROOT, transport=None) -> dict:
    command = 'python3 -I -B -c ' + shlex.quote(CAMERA.HELPER_SNAPSHOT)
    try:
        result = ADAPTER._remote_json(command, input_data=b'', timeout=15,
                                      project_root=project_root, transport=transport)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RuntimeError('native reboot helper identity query outcome is unknown') from error
    if result.returncode != 0:
        raise RuntimeError('native reboot helper identity query failed')
    try:
        value = json.loads(result.stdout, object_pairs_hook=PROFILE._unique_json_object)
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise RuntimeError('native reboot helper identity returned invalid JSON') from error
    CAMERA.validate_helper_snapshot(value)
    return value


def _ensure_receipt_directory(path: Path) -> tuple[Path, int]:
    path = SHARED.prepare_private_receipt_directory(Path(path))
    return path, SHARED.open_private_receipt_directory(path)


def _persist_unknown(path: Path, *, profile_name: str, operation: str,
                     error: BaseException, directory_fd: int) -> dict:
    result = {
        'schema': 's22-audio-coherent-operation-outcome/v1',
        'profile': profile_name,
        'trial_identity': PROFILE.TRIAL_IDENTITY,
        'operation': operation,
        'outcome': 'UNKNOWN',
        'retry_allowed': False,
        'error_type': type(error).__name__,
    }
    SHARED.persist_receipt(path, result, directory_fd=directory_fd)
    return result


def _validate_request_outcome(value: object, profile_name: str) -> dict:
    profile = PROFILE.resolve_profile(profile_name)
    _require(isinstance(value, dict) and
             value.get('schema') == 's22-audio-coherent-operation-outcome/v1',
             'one-shot operation result schema mismatch')
    common = {'schema', 'profile', 'trial_identity', 'operation', 'outcome', 'retry_allowed'}
    _require(value.get('profile') == profile_name and
             value.get('trial_identity') == profile['trial_identity'] and
             value.get('operation') == 'reboot' and
             value.get('retry_allowed') is False,
             'one-shot reboot result identity/type mismatch')
    if value.get('outcome') == 'ACKNOWLEDGED':
        _require(set(value) == common | {
            'returncode', 'owner_authorization_recorded', 'boot_change_verified',
        } and type(value.get('returncode')) is int and value['returncode'] == 0 and
                 value.get('owner_authorization_recorded') is False and
                 value.get('boot_change_verified') is False,
                 'acknowledged reboot result fields are malformed')
    else:
        _require(value.get('outcome') == 'UNKNOWN' and set(value) == common | {'error_type'} and
                 type(value.get('error_type')) is str and bool(value['error_type']),
                 'unknown reboot result fields are malformed')
    return value


def _redacted_kernel_diagnostics(value: object) -> dict:
    if not isinstance(value, dict):
        return {'available': False}
    fatal = value.get('fatal_indicators')
    return {
        'available': True,
        'fatal_indicator_count': len(fatal) if isinstance(fatal, list) else None,
        'fatal_indicators': fatal if isinstance(fatal, list) else None,
        'hung_task_warning_count': value.get('hung_task_warning_count'),
        'call_trace_count': value.get('call_trace_count'),
        'liveness_unresolved': value.get('liveness_unresolved'),
        'capture_complete': value.get('capture_complete'),
        'coverage_complete': value.get('coverage_complete'),
        'full_boot_log_coverage': value.get('full_boot_log_coverage'),
    }


def request_recovery_once(profile_name: str, *, receipts_root: Path = RECEIPTS_ROOT,
                          state_root: Path = STATE_ROOT, project_root: Path = ROOT,
                          transport=None, snapshotter=None, identity_reader=None,
                          helper_reader=None) -> dict:
    profile = PROFILE.resolve_profile(profile_name)
    ADAPTER.require_execution_authorized(profile['trial_identity'])
    verify_source_pins(project_root)
    flash, _ = _load_flash(profile_name, receipts_root=receipts_root,
                           state_root=Path(state_root))
    paths = _receipt_paths(profile_name, receipts_root)
    directory, directory_fd = _ensure_receipt_directory(paths['directory'])
    try:
        attempt_path = paths['reboot_attempt']
        result_path = paths['reboot_result']
        SHARED.ensure_new_receipt(attempt_path, directory_fd=directory_fd)
        SHARED.ensure_new_receipt(result_path, directory_fd=directory_fd)
        kind = f'audio-coherent-{_direction(profile_name)}-reboot'
        marker_id = _reboot_marker_id(profile_name)
        prewrite = flash['prewrite_identity']
        reader = identity_reader or (lambda expected: ADAPTER.capture_audio_identity(
            expected_recovery_sha=expected, project_root=project_root, transport=transport))
        readiness_reader = snapshotter or (lambda: capture_readiness_snapshot(
            project_root=project_root, transport=transport))
        helper_reader = helper_reader or (lambda: capture_helper_snapshot(
            project_root=project_root, transport=transport))

        with GUARD.acquire_operation_lock(
                project_root, marker_id, kind, state_root=state_root) as operation:
            operation.begin(project_root=project_root)
            try:
                current = reader(profile['target_sha256'])
                ADAPTER.validate_audio_identity(current,
                                                expected_recovery_sha=profile['target_sha256'],
                                                expected_modules=ADAPTER.expected_profile_modules(
                                                    profile_name, before=True))
                _require(current['boot_id'] == prewrite['boot_id'],
                         'boot ID changed after flash and before reboot request')
                _require(current['audio_modules'] == flash['postwrite_identity']['audio_modules'],
                         'loaded audio modules changed after flash and before reboot request')
                state = validate_readiness_snapshot(
                    readiness_reader(), post_reboot=False,
                    expected_boot_id=prewrite['boot_id'])
                _require(state.get('gnu_build_id') == current.get('kernel_gnu_build_id'),
                         'readiness and recovery identity kernel IDs disagree')
                helpers = helper_reader()
                CAMERA.validate_helper_snapshot(helpers)
                _require(helpers == _expected_helpers(),
                         'native recovery reboot helper bytes/owner/mode changed')
                started = {
                    'schema': 's22-audio-coherent-reboot-attempt/v1',
                    'profile': profile_name,
                    'trial_identity': profile['trial_identity'],
                    'flash_receipt_sha256': hashlib.sha256(
                        SHARED.read_host_artifact(paths['flash'], 'bound audio flash receipt')).hexdigest(),
                    'prewrite_boot_id': prewrite['boot_id'],
                    'current_recovery_sha256': current['recovery_sha256'],
                    'target_recovery_sha256': profile['target_sha256'],
                    'kernel_gnu_build_id': current['kernel_gnu_build_id'],
                    'request_command': 'boot-id-guarded exec /usr/local/sbin/s22-reboot recovery',
                    'retry_allowed': False,
                    'owner_authorization_recorded': False,
                    'audio_acceptance': False,
                }
                SHARED.persist_receipt(attempt_path, started, directory_fd=directory_fd)
                try:
                    command = CAMERA.reboot_exec_command(current['boot_id'])
                    result = ADAPTER._remote_json(command, input_data=b'', timeout=15,
                                                  project_root=project_root,
                                                  transport=transport)
                except Exception as error:
                    outcome = _persist_unknown(result_path, profile_name=profile_name,
                                               operation='reboot', error=error,
                                               directory_fd=directory_fd)
                    raise RuntimeError('reboot request outcome is unknown; never retry') from error
                if result.returncode != 0:
                    error = RuntimeError('native reboot command disconnected or returned nonzero')
                    outcome = _persist_unknown(result_path, profile_name=profile_name,
                                               operation='reboot', error=error,
                                               directory_fd=directory_fd)
                    raise RuntimeError('reboot request outcome is unknown; never retry')
                outcome = {
                    'schema': 's22-audio-coherent-operation-outcome/v1',
                    'profile': profile_name,
                    'trial_identity': profile['trial_identity'],
                    'operation': 'reboot',
                    'outcome': 'ACKNOWLEDGED',
                    'returncode': 0,
                    'retry_allowed': False,
                    'owner_authorization_recorded': False,
                    'boot_change_verified': False,
                }
                _validate_request_outcome(outcome, profile_name)
                SHARED.persist_receipt(result_path, outcome, directory_fd=directory_fd)
                operation.complete(result_path, outcome='success', cleanup_confirmed=True)
                return outcome
            except (OSError, subprocess.TimeoutExpired) as error:
                raise RuntimeError('reboot preflight/transport failed; inspect before any retry') from error
    finally:
        os.close(directory_fd)


def _redacted_network(value: dict) -> dict:
    rows = value.get('interfaces', []) if isinstance(value, dict) else []
    return {
        'ready': value.get('ready') is True if isinstance(value, dict) else False,
        'interfaces': [
            {'name': row.get('name'), 'operstate': row.get('operstate'),
             'carrier': row.get('carrier')}
            for row in rows if isinstance(row, dict)
        ],
    }


def observe_reboot_once(profile_name: str, *, receipts_root: Path = RECEIPTS_ROOT,
                        state_root: Path = STATE_ROOT, project_root: Path = ROOT,
                        transport=None, snapshotter=None, identity_reader=None) -> dict:
    profile = PROFILE.resolve_profile(profile_name)
    ADAPTER.require_execution_authorized(profile['trial_identity'])
    verify_source_pins(project_root)
    flash, _ = _load_flash(profile_name, receipts_root=receipts_root,
                           state_root=Path(state_root))
    paths = _receipt_paths(profile_name, receipts_root)
    request, _ = ADAPTER._read_private_json(paths['reboot_result'])
    _validate_request_outcome(request, profile_name)
    marker = _validate_reboot_marker_for_observation(
        profile_name, request, paths['reboot_result'], Path(state_root))
    directory, directory_fd = _ensure_receipt_directory(paths['directory'])
    try:
        result_path = paths['observation']
        started_path = directory / f'{profile_name}-observation-started.json'
        SHARED.ensure_new_receipt(result_path, directory_fd=directory_fd)
        SHARED.ensure_new_receipt(started_path, directory_fd=directory_fd)
        readiness_reader = snapshotter or (lambda: capture_readiness_snapshot(
            project_root=project_root, transport=transport))
        identity_reader = identity_reader or (lambda expected: ADAPTER.capture_audio_identity(
            expected_recovery_sha=expected, project_root=project_root, transport=transport))
        # The durable exclusive local observation marker makes this read-only
        # snapshot one-shot. An UNKNOWN reboot marker intentionally remains
        # unresolved and blocks all writes; observing after that disconnect
        # must not acquire/clear/replay the reboot operation.
        SHARED.persist_receipt(started_path, {
            'schema': 's22-audio-coherent-observation-started/v1',
            'profile': profile_name, 'trial_identity': profile['trial_identity'],
            'reboot_outcome': request['outcome'], 'retry_allowed': False,
        }, directory_fd=directory_fd)
        operation_context = (nullcontext(None) if request['outcome'] == 'UNKNOWN' else
                             GUARD.acquire_operation_lock(
                                 project_root, _observation_marker_id(profile_name),
                                 f'audio-coherent-{_direction(profile_name)}-observe',
                                 state_root=state_root))
        with operation_context as operation:
            if operation is not None:
                operation.begin(project_root=project_root)
            try:
                state = validate_readiness_snapshot(
                    readiness_reader(), post_reboot=True)
                identity = ADAPTER.validate_audio_identity(
                    identity_reader(profile['target_sha256']),
                    expected_recovery_sha=profile['target_sha256'],
                    expected_modules=_expected_profile_modules(profile_name))
                before_boot = flash['prewrite_identity']['boot_id']
                _require(state['boot_id'] != before_boot and
                         identity['boot_id'] == state['boot_id'],
                         'initial observation did not prove a changed, internally consistent boot ID')
                _require(state.get('gnu_build_id') == identity['kernel_gnu_build_id'] ==
                         ADAPTER.RUNNING_KERNEL_BUILD_ID,
                         'initial observation running kernel GNU build ID mismatch')
                _require(AUDIO.recovery_record(state.get('boot_reset_first_record')),
                         'initial observation does not prove RECOVERY boot mode')
                modules = {name: identity['audio_modules'][name]['gnu_build_id']
                           for name in sorted(ADAPTER.AUDIO_MODULES)}
                readiness = state['readiness']
                receipt = {
                    'schema': 's22-audio-coherent-initial-observation/v1',
                    'profile': profile_name,
                    'trial_identity': profile['trial_identity'],
                    'status': 'initial-observation-complete',
                    'reboot_request_outcome': request['outcome'],
                    'reboot_requests': 1,
                    'retry_allowed': False,
                    'baseline_boot_id': before_boot,
                    'observed_boot_id': state['boot_id'],
                    'mode': 'RECOVERY',
                    'recovery_sha256': identity['recovery_sha256'],
                    'kernel_release': state['kernel_release'],
                    'kernel_gnu_build_id': identity['kernel_gnu_build_id'],
                    'audio_module_gnu_build_ids': modules,
                    'native_ready': state['native_ready'],
                    'persistent_ready': state['persistent_ready'],
                    'model_api_health': readiness['model_api_health'],
                    'model_idle': state['assistant_idle'],
                    'desktop_environment': readiness['desktop_environment'],
                    'network_state': _redacted_network(state['network_state']),
                    'power_state': state['power_state'],
                    'kernel_diagnostics': _redacted_kernel_diagnostics(
                        state['kernel_log_classification']),
                    'dedicated_pi_session_status': readiness.get('dedicated_pi_session_status'),
                    'browser_terminal_status': readiness.get('browser_terminal_status'),
                    'tmux_server_running': state.get('tmux_server_running'),
                    'audio_hardware_acceptance': False,
                    'bootability_claim': False,
                }
                SHARED.persist_receipt(result_path, receipt, directory_fd=directory_fd)
                if operation is not None:
                    operation.complete(result_path, outcome='success', cleanup_confirmed=True)
                return receipt
            except Exception as error:
                failure_path = directory / f'{profile_name}-initial-observation-failure.json'
                try:
                    SHARED.ensure_new_receipt(failure_path, directory_fd=directory_fd)
                    SHARED.persist_receipt(failure_path, {
                        'schema': 's22-audio-coherent-initial-observation-failure/v1',
                        'profile': profile_name,
                        'trial_identity': profile['trial_identity'],
                        'status': 'UNKNOWN', 'retry_allowed': False,
                        'error_type': type(error).__name__,
                    }, directory_fd=directory_fd)
                except Exception:
                    pass
                raise
    finally:
        os.close(directory_fd)


def build_plan(profile_name: str, *, artifact_root: Path = ARTIFACT_ROOT) -> dict:
    plan = ADAPTER.build_plan(profile_name, artifact_root=artifact_root)
    plan.update({
        'schema': 's22-audio-coherent-reboot-plan/v1',
        'reboot_or_observation_execution': False,
        'current_deployment_authorized': False,
        'execution_authorized_trial_allowlist': [],
        'reboot_marker_created': False,
        'reboot_performed': False,
        'audio_hardware_acceptance': False,
    })
    return plan


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    choice = parser.add_mutually_exclusive_group()
    choice.add_argument('--request-reboot', action='store_true')
    choice.add_argument('--observe-once', action='store_true')
    parser.add_argument('--profile', required=True, choices=tuple(PROFILE.PROFILES))
    parser.add_argument('--execute', action='store_true',
                        help='request operation intent; this is not owner authorization')
    parser.add_argument('--trial-identity')
    args = parser.parse_args(argv)
    operation = 'reboot' if args.request_reboot else 'observe' if args.observe_once else None
    if operation is None:
        if args.execute or args.trial_identity is not None:
            parser.error('execution arguments require --request-reboot or --observe-once')
        print(json.dumps(build_plan(args.profile, artifact_root=ARTIFACT_ROOT),
                          indent=2, sort_keys=True))
        return 0
    if not args.execute:
        parser.error('reboot/observation dispatch requires --execute intent')
    if args.trial_identity != PROFILE.TRIAL_IDENTITY:
        parser.error('operation trial identity does not match the reserved audio profile')
    ADAPTER.require_execution_authorized(args.trial_identity)
    result = (request_recovery_once(args.profile) if operation == 'reboot'
              else observe_reboot_once(args.profile))
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError, PermissionError) as error:
        raise SystemExit(f'refused: {error}') from error
