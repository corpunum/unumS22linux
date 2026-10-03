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
import math
import os
from pathlib import Path
import shlex
import stat
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
ARTIFACT_ROOT = Path('/home/corpunum/s22-linux')
RECEIPTS_ROOT = Path.home() / '.local/state/s22-audio-coherent-trial/receipts'
STATE_ROOT = Path('/home/corpunum/.local/state/s22-device-trial-guard')
TRIAL_IDENTITY = 'audio-coherent-20261003-first'

# The stability follow-up is intentionally shorter than a ten-minute trial:
# one fully ready sample starts the interval, then two fresh samples are taken
# at 90-second offsets. The final sample starts no earlier than 195 seconds so
# its remote capture still covers 180 seconds with up to 15 seconds of capture
# skew. A bounded 180-second uptime gate (13 probes at 15s offsets), two
# full-readiness startup polls, and one read-only reconnect total fit inside
# the hard deadline.
STABILITY_SAMPLE_OFFSETS_SECONDS = (0, 90, 180)
STABILITY_MIN_SECONDS = 180
STABILITY_MIN_SAMPLE_GAP_SECONDS = 60
STABILITY_TOTAL_DEADLINE_SECONDS = 600
STABILITY_STARTUP_UPTIME_SECONDS = 180
STABILITY_STARTUP_POLL_GAP_SECONDS = 15
STABILITY_STARTUP_POLLS = STABILITY_STARTUP_UPTIME_SECONDS // STABILITY_STARTUP_POLL_GAP_SECONDS + 1
STABILITY_READINESS_STARTUP_POLLS = 2
STABILITY_READINESS_QUERY_CAP_SECONDS = 15
STABILITY_REMOTE_SAMPLE_START_SKEW_SECONDS = STABILITY_READINESS_QUERY_CAP_SECONDS
STABILITY_REMOTE_FINAL_SAMPLE_OFFSET_SECONDS = (
    STABILITY_MIN_SECONDS + STABILITY_REMOTE_SAMPLE_START_SKEW_SECONDS)
STABILITY_REMOTE_UPTIME_MAX_SECONDS = 10_000_000
STABILITY_IDENTITY_QUERY_CAP_SECONDS = 30
STABILITY_MAX_RECONNECTS_TOTAL = 1
STABILITY_RECONNECT_BACKOFF_SECONDS = 2
STABILITY_STARTUP_QUERY_CAP_SECONDS = 3
STABILITY_STARTUP_READINESS_GAP_SECONDS = 15

# Set to the frozen deployment-adapter source digest when this two-file unit is
# frozen. Empty is development-only and is never accepted by the CLI's source
# gate after commit.
DEPLOY_ADAPTER_SHA256 = '859047a5018d697e510ef77855c5f841b1ef2e8890fb9890d47ba8dcb2a4aa66'


class ReadOnlyTransportGap(RuntimeError):
    """A bounded read-only query could not reach or receive the target."""


class TerminalObservationError(RuntimeError):
    """An identity, boot, serious-fault, or marker contradiction was observed."""

    def __init__(self, category: str, message: str):
        super().__init__(message)
        self.category = category


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
    _require(isinstance(logs, dict) and
             type(logs.get('input_available')) is bool and logs['input_available'] is True and
             type(logs.get('capture_complete')) is bool and logs['capture_complete'] is True and
             type(logs.get('coverage_complete')) is bool and logs['coverage_complete'] is True,
             'kernel diagnostic input/capture/coverage is unavailable or incomplete')
    diagnostic_bytes = logs.get('bytes')
    diagnostic_sha256 = logs.get('sha256')
    _require(type(diagnostic_bytes) is int and 0 < diagnostic_bytes <= 4_194_304 and
             type(diagnostic_sha256) is str and len(diagnostic_sha256) == 64 and
             all(character in '0123456789abcdef' for character in diagnostic_sha256),
             'kernel diagnostic byte count or fingerprint is unavailable or malformed')
    fatal = logs.get('fatal_indicators')
    hung_count = logs.get('hung_task_warning_count')
    trace_count = logs.get('call_trace_count')
    _require(isinstance(fatal, list) and not fatal and
             type(hung_count) is int and hung_count == 0 and
             type(trace_count) is int and trace_count == 0 and
             type(logs.get('liveness_unresolved')) is bool and
             logs['liveness_unresolved'] is False and
             type(logs.get('assessment')) is str and
             logs['assessment'] == 'no_indicators',
             'kernel diagnostics do not meet the no-indicators readiness assessment')
    _require(type(logs.get('full_boot_log_coverage')) is bool,
             'kernel diagnostic full-boot coverage status is malformed')
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
    marker = 'print(json.dumps(state))'
    _require(script.count(marker) == 1,
             'pinned readiness collector has an ambiguous JSON output boundary')
    # The pinned collector uses serious_fault=None when the classifier had no
    # input, but does not otherwise serialize input_available. Export the
    # classifier's exact value at the existing output boundary; do not infer
    # availability from a nonempty ring or from a successful dmesg exit.
    script = script.replace(
        marker,
        "state['kernel_log_classification']['input_available']=log_classification.input_available\n" +
        "state['kernel_log_classification']['sha256']=(hashlib.sha256(log.encode('utf-8','replace')).hexdigest() if isinstance(log,str) else None)\n" +
        marker)
    compile(script, 'audio-coherent-native-readiness', 'exec')
    return script


def capture_readiness_snapshot(*, project_root: Path = ROOT, transport=None,
                               timeout: float = 20) -> dict:
    command = 'python3 -I -B -c ' + shlex.quote(_readiness_script())
    try:
        result = ADAPTER._remote_json(command, input_data=b'', timeout=timeout,
                                      project_root=project_root, transport=transport)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ReadOnlyTransportGap('native readiness query outcome is unknown') from error
    if result.returncode != 0:
        raise ReadOnlyTransportGap('native readiness query failed')
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
        'available': value.get('input_available'),
        'assessment': value.get('assessment'),
        'fatal_indicator_count': len(fatal) if isinstance(fatal, list) else None,
        'fatal_indicators': fatal if isinstance(fatal, list) else None,
        'hung_task_warning_count': value.get('hung_task_warning_count'),
        'call_trace_count': value.get('call_trace_count'),
        'liveness_unresolved': value.get('liveness_unresolved'),
        'capture_complete': value.get('capture_complete'),
        'coverage_complete': value.get('coverage_complete'),
        'full_boot_log_coverage': value.get('full_boot_log_coverage'),
        'bytes': value.get('bytes'),
        'sha256': value.get('sha256'),
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


def _stability_marker_id(profile_name: str) -> str:
    profile = PROFILE.resolve_profile(profile_name)
    return f'{profile["trial_identity"]}-{_direction(profile_name)}-stability'


def _stability_marker_kind(profile_name: str) -> str:
    return f'audio-coherent-{_direction(profile_name)}-stability'


def _read_stability_operation_marker(operation, *, profile_name: str,
                                     state_root: Path, project_root: Path) -> dict:
    marker_id = _stability_marker_id(profile_name)
    marker_kind = _stability_marker_kind(profile_name)
    expected_path = Path(state_root) / f'{marker_id}.json'
    expected_root = Path(state_root)
    expected_project = str(Path(project_root).resolve())

    def refuse(message: str, error: BaseException | None = None):
        # Do not let the context manager rewrite a marker whose identity could
        # not be proved against this exact in-flight operation.
        if operation is not None and getattr(operation, 'started', False):
            operation.finished = True
        raise TerminalObservationError('ambiguous_marker', message) from error

    if (operation is None or operation.trial_id != marker_id or
            operation.operation_kind != marker_kind or
            Path(operation.state_root) != expected_root or
            Path(operation.marker_path) != expected_path or
            operation.started is not True or operation.marker_fd is None):
        refuse('active stability operation does not match the expected guard identity')
    try:
        operation._verify_marker_identity()
        marker = GUARD._read_marker(expected_path)
        operation._verify_marker_identity()
    except Exception as error:
        refuse('active stability guard marker identity cannot be verified', error)
    if (marker.get('trial_id') != marker_id or
            marker.get('operation_kind') != marker_kind):
        refuse('active stability guard marker fields do not match this operation')
    if marker.get('status') in ('pending', 'unknown') and (
            marker.get('project_root') != expected_project or
            marker.get('pid') != os.getpid()):
        refuse('active stability guard marker origin does not match this process')
    return marker


def _require_stability_marker_phase(marker: dict, *, phase: str,
                                    result_path: Path | None = None,
                                    result_sha256: str | None = None,
                                    operation=None) -> None:
    if phase == 'pending':
        valid = (marker.get('status') == 'pending' and
                 'outcome' not in marker and 'receipt_path' not in marker and
                 'receipt_sha256' not in marker)
    elif phase == 'complete':
        valid = (marker.get('status') == 'complete' and
                 marker.get('outcome') == 'success' and
                 marker.get('receipt_path') == str(result_path) and
                 marker.get('receipt_sha256') == result_sha256 and
                 type(marker.get('completed_unix_ns')) is int)
    else:
        raise ValueError('unsupported stability marker phase')
    if not valid:
        if operation is not None and getattr(operation, 'started', False):
            operation.finished = True
        raise TerminalObservationError(
            'ambiguous_marker',
            f'active stability guard marker does not match the {phase} phase')


def _demote_late_stability_completion(operation, *, profile_name: str,
                                      state_root: Path, project_root: Path,
                                      result_path: Path, result_sha256: str,
                                      completed_marker: dict,
                                      timeout_error: TimeoutError) -> None:
    """Preserve this operation's late completion, but leave its guard unresolved."""
    marker_id = _stability_marker_id(profile_name)
    marker_kind = _stability_marker_kind(profile_name)
    marker_path = Path(state_root) / f'{marker_id}.json'
    provisional_receipt = {
        'path': str(result_path),
        'sha256': result_sha256,
        'status': 'provisional-bounded-stability-observed',
        'accepted': False,
    }
    unknown_marker = {
        'trial_id': marker_id,
        'operation_kind': marker_kind,
        'project_root': str(Path(project_root).resolve()),
        'pid': os.getpid(),
        'status': 'unknown',
        'outcome': 'UNKNOWN',
        'reason': (f'{type(timeout_error).__name__}: stability finalization exceeded '
                   f'{STABILITY_TOTAL_DEADLINE_SECONDS} seconds')[:512],
        'late_success_completion': completed_marker,
        'provisional_result_receipt': provisional_receipt,
        'updated_unix_ns': time.time_ns(),
    }

    # The lock remains held. Verify the same open marker, operation IDs, and
    # result digest immediately before the only permitted late-state update.
    current = _read_stability_operation_marker(
        operation, profile_name=profile_name, state_root=state_root,
        project_root=project_root)
    _require_stability_marker_phase(current, phase='complete',
                                    result_path=result_path,
                                    result_sha256=result_sha256)
    if current != completed_marker:
        operation.finished = True
        raise TerminalObservationError(
            'ambiguous_marker',
            'late stability completion changed before the unresolved-state update')

    last_error = None
    for _attempt in range(2):
        try:
            operation._verify_marker_identity()
            current = GUARD._read_marker(marker_path)
            if current not in (completed_marker, unknown_marker):
                operation.finished = True
                raise TerminalObservationError(
                    'ambiguous_marker',
                    'refusing to alter a guard marker that is no longer this completion')
            # Reapply and fsync even if the prior attempt made UNKNOWN visible
            # before reporting an I/O error; visibility alone is not durability.
            operation._update(unknown_marker)
            operation._verify_marker_identity()
            persisted = GUARD._read_marker(marker_path)
            if persisted != unknown_marker:
                raise RuntimeError('late completion UNKNOWN marker did not verify after update')
            operation.finished = True
            return
        except TerminalObservationError:
            raise
        except Exception as error:
            last_error = error

    # Give the guard context one final best-effort UNKNOWN update on unwind.
    # This path always raises; an uncertain local marker update is never success.
    operation.finished = False
    raise RuntimeError(
        'late stability success marker could not be durably changed to UNKNOWN') from last_error


def _stability_receipt_paths(profile_name: str,
                             receipts_root: Path = RECEIPTS_ROOT) -> dict[str, object]:
    directory = _receipt_paths(profile_name, receipts_root)['directory']
    return {
        'directory': directory,
        'started': directory / f'{profile_name}-stability-started.json',
        'result': directory / f'{profile_name}-stability-result.json',
        'failure': directory / f'{profile_name}-stability-failure.json',
        'uptime_probes': [directory / f'{profile_name}-stability-uptime-{index:02d}.json'
                          for index in range(STABILITY_STARTUP_POLLS)],
        'startup': [directory / f'{profile_name}-stability-startup-{index:02d}.json'
                    for index in range(STABILITY_READINESS_STARTUP_POLLS)],
        'samples': [directory / f'{profile_name}-stability-sample-{index:02d}.json'
                    for index in range(len(STABILITY_SAMPLE_OFFSETS_SECONDS))],
    }


def capture_stability_identity(profile_name: str, *, timeout: float,
                               project_root: Path = ROOT, transport=None) -> dict:
    """Read the full recovery hash and exact profile module IDs with a deadline."""
    profile = PROFILE.resolve_profile(profile_name)
    command = 'python3 -I -B -c ' + shlex.quote(ADAPTER.identity_snapshot_script())
    try:
        result = ADAPTER._remote_json(command, input_data=b'', timeout=timeout,
                                      project_root=project_root, transport=transport)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ReadOnlyTransportGap('read-only recovery identity query is unavailable') from error
    if result.returncode != 0:
        raise ReadOnlyTransportGap('read-only recovery identity query failed')
    try:
        value = json.loads(result.stdout, object_pairs_hook=PROFILE._unique_json_object)
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise RuntimeError('read-only recovery identity response is malformed') from error
    try:
        return ADAPTER.validate_audio_identity(
            value, expected_recovery_sha=profile['target_sha256'],
            expected_modules=_expected_profile_modules(profile_name))
    except (TypeError, ValueError) as error:
        raise TerminalObservationError('wrong_identity',
                                       'recovery image or loaded audio module identity mismatch') from error


def capture_stability_startup_probe(*, timeout: float, project_root: Path = ROOT,
                                    transport=None) -> dict:
    """Read only boot ID and uptime while waiting for the normal 180s gate."""
    script = r'''from pathlib import Path
import json
boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
uptime=float(Path('/proc/uptime').read_text().split()[0])
print(json.dumps({'boot_id':boot_id,'uptime_seconds':uptime}))
'''
    command = 'python3 -I -B -c ' + shlex.quote(script)
    try:
        result = ADAPTER._remote_json(command, input_data=b'', timeout=timeout,
                                      project_root=project_root, transport=transport)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ReadOnlyTransportGap('read-only startup probe is unavailable') from error
    if result.returncode != 0:
        raise ReadOnlyTransportGap('read-only startup probe failed')
    try:
        value = json.loads(result.stdout, object_pairs_hook=PROFILE._unique_json_object)
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise RuntimeError('read-only startup probe response is malformed') from error
    if (not isinstance(value, dict) or type(value.get('boot_id')) is not str or
            not value['boot_id'] or type(value.get('uptime_seconds')) not in (float, int) or
            not 0 <= value['uptime_seconds'] < 10_000_000):
        raise RuntimeError('read-only startup probe fields are malformed')
    return value


def _stability_initial_observation(profile_name: str, request: dict, flash: dict,
                                   initial: object) -> dict:
    """Revalidate the earlier one-shot receipt before treating it as interval t=0."""
    profile = PROFILE.resolve_profile(profile_name)
    message = 'initial observation receipt or marker is ambiguous'
    try:
        _require(isinstance(initial, dict) and
                 initial.get('schema') == 's22-audio-coherent-initial-observation/v1' and
                 initial.get('profile') == profile_name and
                 initial.get('trial_identity') == profile['trial_identity'] and
                 initial.get('status') == 'initial-observation-complete' and
                 initial.get('reboot_request_outcome') == request['outcome'] and
                 type(initial.get('reboot_requests')) is int and
                 initial['reboot_requests'] == 1 and
                 initial.get('retry_allowed') is False and
                 initial.get('baseline_boot_id') == flash['prewrite_identity']['boot_id'] and
                 type(initial.get('observed_boot_id')) is str and
                 bool(initial['observed_boot_id']) and
                 initial['observed_boot_id'] != initial['baseline_boot_id'] and
                 initial.get('mode') == 'RECOVERY' and
                 initial.get('recovery_sha256') == profile['target_sha256'] and
                 initial.get('kernel_gnu_build_id') == ADAPTER.RUNNING_KERNEL_BUILD_ID and
                 initial.get('audio_module_gnu_build_ids') ==
                 _expected_profile_modules(profile_name), message)
        readiness = {
            'model_api_health': initial.get('model_api_health'),
            'model_idle': initial.get('model_idle'),
            'desktop_environment': initial.get('desktop_environment'),
        }
        _require(initial.get('native_ready') is True and
                 AUDIO.ready_value(initial.get('persistent_ready')) and
                 readiness['model_api_health'] is True and
                 readiness['model_idle'] is True and
                 readiness['desktop_environment'] is True and
                 AUDIO.network_state_valid(initial.get('network_state')) and
                 AUDIO.power_state_valid(initial.get('power_state')),
                 message)
        diagnostics = initial.get('kernel_diagnostics')
        _require(isinstance(diagnostics, dict) and
                 diagnostics.get('available') is True and
                 diagnostics.get('assessment') == 'no_indicators' and
                 diagnostics.get('fatal_indicators') == [] and
                 type(diagnostics.get('hung_task_warning_count')) is int and
                 diagnostics['hung_task_warning_count'] == 0 and
                 type(diagnostics.get('call_trace_count')) is int and
                 diagnostics['call_trace_count'] == 0 and
                 diagnostics.get('liveness_unresolved') is False and
                 diagnostics.get('capture_complete') is True and
                 diagnostics.get('coverage_complete') is True and
                 type(diagnostics.get('full_boot_log_coverage')) is bool and
                 type(diagnostics.get('bytes')) is int and diagnostics['bytes'] > 0 and
                 type(diagnostics.get('sha256')) is str and
                 len(diagnostics['sha256']) == 64 and
                 all(character in '0123456789abcdef' for character in diagnostics['sha256']),
                 message)
        return initial
    except TerminalObservationError:
        raise
    except (TypeError, ValueError, KeyError) as error:
        raise TerminalObservationError('ambiguous_marker', message) from error


def _safe_sample_readiness(state: object) -> dict:
    if not isinstance(state, dict):
        return {'boot_id': None}
    ready = state.get('readiness')
    ready = ready if isinstance(ready, dict) else {}
    persistent = state.get('persistent_ready')
    persistent = persistent if isinstance(persistent, dict) else {}
    health = state.get('health')
    health = health if isinstance(health, dict) else {}
    slots = state.get('slots')
    slots = slots if isinstance(slots, dict) else {}
    return {
        'boot_id': state.get('boot_id') if type(state.get('boot_id')) is str else None,
        'native_ready': state.get('native_ready') if type(state.get('native_ready')) is bool else None,
        'persistent_ready': persistent.get('ready') if type(persistent.get('ready')) is bool else None,
        'persistent_mount_ready': persistent.get('mount_ready') if type(persistent.get('mount_ready')) is bool else None,
        'health_status': health.get('status') if type(health.get('status')) is str else None,
        'slot_count': slots.get('count') if type(slots.get('count')) is int else None,
        'assistant_idle': state.get('assistant_idle') if type(state.get('assistant_idle')) is bool else None,
        'model_api_health': ready.get('model_api_health') if type(ready.get('model_api_health')) is bool else None,
        'model_idle': ready.get('model_idle') if type(ready.get('model_idle')) is bool else None,
        'desktop_environment': ready.get('desktop_environment') if type(ready.get('desktop_environment')) is bool else None,
    }


def _safe_sample_identity(identity: object) -> dict:
    if not isinstance(identity, dict):
        return {}
    modules = identity.get('audio_modules')
    module_ids = {}
    if isinstance(modules, dict):
        for name, item in modules.items():
            if (type(name) is str and isinstance(item, dict) and
                    type(item.get('gnu_build_id')) is str):
                module_ids[name] = item['gnu_build_id']
    return {
        'boot_id': identity.get('boot_id') if type(identity.get('boot_id')) is str else None,
        'recovery_sha256': identity.get('recovery_sha256') if type(identity.get('recovery_sha256')) is str else None,
        'kernel_gnu_build_id': identity.get('kernel_gnu_build_id') if type(identity.get('kernel_gnu_build_id')) is str else None,
        'audio_module_gnu_build_ids': module_ids,
    }


def _safe_sample_power(state: object) -> dict:
    value = state.get('power_state') if isinstance(state, dict) else None
    if not isinstance(value, dict):
        return {}
    allowed = ('battery_status', 'battery_capacity_percent',
               'battery_temperature_celsius', 'thermal_all_readable',
               'thermal_zone_count', 'thermal_max_temperature_celsius')
    return {key: value[key] for key in allowed if key in value and
            (type(value[key]) in (str, int, float, bool) or value[key] is None)}


def _safe_sample_remote_uptime(state: object) -> int | float | None:
    value = state.get('uptime_seconds') if isinstance(state, dict) else None
    if type(value) not in (int, float):
        return None
    try:
        return value if math.isfinite(value) else None
    except OverflowError:
        return None


def _validated_remote_uptime(state: object) -> int | float:
    value = state.get('uptime_seconds') if isinstance(state, dict) else None
    try:
        valid = (type(value) in (int, float) and
                 0 <= value < STABILITY_REMOTE_UPTIME_MAX_SECONDS and
                 math.isfinite(value))
    except (OverflowError, TypeError):
        valid = False
    if not valid:
        raise TerminalObservationError(
            'ambiguous_response',
            'readiness response uptime must be a finite exact numeric value')
    return value


def _sample_record(*, profile_name: str, kind: str, index: int,
                   operation_started: float, sampled_started: float,
                   state: object, identity: object, status: str,
                   category: str | None,
                   remote_interval_seconds: float | None = None,
                   host_interval_seconds: float | None = None) -> dict:
    logs = state.get('kernel_log_classification') if isinstance(state, dict) else None
    raw_uptime = state.get('uptime_seconds') if isinstance(state, dict) else None
    remote_uptime = _safe_sample_remote_uptime(state)
    return {
        'schema': 's22-audio-coherent-stability-sample/v1',
        'profile': profile_name,
        'trial_identity': PROFILE.resolve_profile(profile_name)['trial_identity'],
        'kind': kind,
        'index': index,
        'sampled_unix_ns': time.time_ns(),
        'elapsed_seconds': round(max(0.0, sampled_started - operation_started), 3),
        'remote_uptime_seconds': remote_uptime,
        'remote_uptime_input_type': (
            type(raw_uptime).__name__ if isinstance(state, dict) and
            'uptime_seconds' in state else 'missing'),
        'remote_uptime_valid': remote_uptime is not None,
        'remote_interval_seconds': remote_interval_seconds,
        'host_interval_seconds': (round(host_interval_seconds, 3)
                                  if host_interval_seconds is not None else None),
        'status': status,
        'category': category,
        'readiness': _safe_sample_readiness(state),
        'identity': _safe_sample_identity(identity),
        'network_state': _redacted_network(
            state.get('network_state') if isinstance(state, dict) else {}),
        'power_state': _safe_sample_power(state),
        'kernel_diagnostics': _redacted_kernel_diagnostics(logs),
    }


def _assess_stability_sample(profile_name: str, expected_boot_id: str,
                             state: object, identity: object) -> tuple[str, str | None]:
    if not isinstance(state, dict):
        raise TerminalObservationError('ambiguous_response',
                                       'readiness response is not an object')
    boot_id = state.get('boot_id')
    if type(boot_id) is not str or not boot_id:
        raise TerminalObservationError('wrong_identity',
                                       'readiness response has no boot identity')
    if boot_id != expected_boot_id:
        raise TerminalObservationError('changed_boot',
                                       'boot ID changed during bounded observation')
    if not isinstance(identity, dict):
        raise TerminalObservationError('ambiguous_response',
                                       'recovery identity response is not an object')
    if identity.get('boot_id') != expected_boot_id:
        raise TerminalObservationError('changed_boot',
                                       'readiness and recovery identity boot IDs disagree')
    if state.get('serious_fault') is True:
        raise TerminalObservationError('serious_fault',
                                       'serious kernel fault was reported')
    logs = state.get('kernel_log_classification')
    if isinstance(logs, dict):
        fatal = logs.get('fatal_indicators')
        assessment = logs.get('assessment')
        hung_count = logs.get('hung_task_warning_count')
        if ((isinstance(fatal, list) and fatal) or
                assessment in ('fatal', 'hung_task_warning') or
                (type(hung_count) is int and hung_count > 0)):
            raise TerminalObservationError('serious_fault',
                                           'fatal or hung-task kernel diagnostic was reported')
    profile = PROFILE.resolve_profile(profile_name)
    try:
        identity = ADAPTER.validate_audio_identity(
            identity, expected_recovery_sha=profile['target_sha256'],
            expected_modules=_expected_profile_modules(profile_name))
    except (TypeError, ValueError) as error:
        raise TerminalObservationError('wrong_identity',
                                       'recovery image or loaded audio module identity mismatch') from error
    if identity['boot_id'] != boot_id or state.get('gnu_build_id') != identity['kernel_gnu_build_id']:
        raise TerminalObservationError('changed_boot',
                                       'readiness and recovery identity came from different boots')
    if (not AUDIO.target_identity_valid(state) or
            state.get('gnu_build_id') != ADAPTER.RUNNING_KERNEL_BUILD_ID or
            type(state.get('kernel_release')) is not str or
            not state['kernel_release'].strip() or
            not AUDIO.recovery_record(state.get('boot_reset_first_record'))):
        raise TerminalObservationError('wrong_identity',
                                       'device, kernel, or RECOVERY identity is not pinned')
    try:
        validate_readiness_snapshot(state, post_reboot=True,
                                    expected_boot_id=expected_boot_id)
    except ValueError:
        # Incomplete startup, services, network, power, current-ring coverage,
        # and trace-only classifications may wait at startup. They cannot pass
        # a later stability sample.
        return 'unready', 'readiness_gate_not_satisfied'
    return 'ready', None


def _stability_capture_identity(profile_name: str, *, project_root: Path,
                                transport, remaining: float) -> dict:
    return capture_stability_identity(
        profile_name, timeout=min(STABILITY_IDENTITY_QUERY_CAP_SECONDS, remaining),
        project_root=project_root, transport=transport)


def observe_stability(profile_name: str, *, receipts_root: Path = RECEIPTS_ROOT,
                      state_root: Path = STATE_ROOT, project_root: Path = ROOT,
                      transport=None, snapshotter=None, identity_reader=None,
                      clock=time.monotonic, sleeper=time.sleep) -> dict:
    """Observe three fresh same-boot samples over at least 180 seconds.

    Only the approved read-only readiness and full-hash identity queries run.
    A successful ACK or unresolved UNKNOWN reboot result is accepted as the
    prior one-shot context; neither path can send or repeat a reboot.
    """
    operation_started = clock()
    deadline = operation_started + STABILITY_TOTAL_DEADLINE_SECONDS
    profile = PROFILE.resolve_profile(profile_name)
    ADAPTER.require_execution_authorized(profile['trial_identity'])
    verify_source_pins(project_root)
    paths = _stability_receipt_paths(profile_name, receipts_root)
    directory, directory_fd = _ensure_receipt_directory(paths['directory'])
    startup_paths = paths['startup']
    uptime_probe_paths = paths['uptime_probes']
    sample_paths = paths['samples']
    all_paths = [paths['started'], paths['result'], paths['failure'],
                 *uptime_probe_paths, *startup_paths, *sample_paths]
    saved_samples: list[dict] = []
    accepted_remote_uptimes: list[int | float] = []
    accepted_sample_starts: list[float] = []
    final_result_persist_started = False
    final_result_persist_returned = False
    final_result_sha256: str | None = None
    late_completed_marker: dict | None = None
    try:
        for path in all_paths:
            SHARED.ensure_new_receipt(path, directory_fd=directory_fd)
        started_receipt = {
            'schema': 's22-audio-coherent-stability-started/v1',
            'profile': profile_name,
            'trial_identity': profile['trial_identity'],
            'total_deadline_seconds': STABILITY_TOTAL_DEADLINE_SECONDS,
            'startup_uptime_gate_seconds': STABILITY_STARTUP_UPTIME_SECONDS,
            'startup_uptime_poll_offsets_seconds': [
                index * STABILITY_STARTUP_POLL_GAP_SECONDS
                for index in range(STABILITY_STARTUP_POLLS)],
            'startup_readiness_max_polls': STABILITY_READINESS_STARTUP_POLLS,
            'startup_readiness_poll_gap_seconds': STABILITY_STARTUP_READINESS_GAP_SECONDS,
            'sample_offsets_seconds': list(STABILITY_SAMPLE_OFFSETS_SECONDS),
            'remote_uptime_required': True,
            'remote_minimum_interval_seconds': STABILITY_MIN_SECONDS,
            'remote_sample_start_skew_allowance_seconds':
                STABILITY_REMOTE_SAMPLE_START_SKEW_SECONDS,
            'remote_final_sample_start_offset_seconds_minimum':
                STABILITY_REMOTE_FINAL_SAMPLE_OFFSET_SECONDS,
            'maximum_read_only_reconnects': STABILITY_MAX_RECONNECTS_TOTAL,
            'retry_allowed': False,
        }
        SHARED.persist_receipt(paths['started'], started_receipt,
                               directory_fd=directory_fd)

        reconnect_count = 0
        attempt_count = 0
        startup_readiness_poll_count = 0
        active_category = 'ambiguous_marker'
        operation = None
        try:
            flash, _ = _load_flash(profile_name, receipts_root=receipts_root,
                                   state_root=Path(state_root))
            reboot_path = _receipt_paths(profile_name, receipts_root)['reboot_result']
            request, _ = ADAPTER._read_private_json(reboot_path)
            _validate_request_outcome(request, profile_name)
            _validate_reboot_marker_for_observation(
                profile_name, request, reboot_path, Path(state_root))
            observation_paths = _receipt_paths(profile_name, receipts_root)
            initial, _ = ADAPTER._read_private_json(observation_paths['observation'])
            initial = _stability_initial_observation(profile_name, request, flash, initial)
            observe_marker = Path(state_root) / f'{_observation_marker_id(profile_name)}.json'
            if request['outcome'] == 'ACKNOWLEDGED':
                _validate_receipt_marker(profile_name, 'observe',
                                         observation_paths['observation'], Path(state_root))
            elif observe_marker.exists() or observe_marker.is_symlink():
                raise TerminalObservationError(
                    'ambiguous_marker',
                    'UNKNOWN reboot must retain an unmarked read-only initial observation')

            stability_marker = Path(state_root) / f'{_stability_marker_id(profile_name)}.json'
            if stability_marker.exists() or stability_marker.is_symlink():
                raise TerminalObservationError('ambiguous_marker',
                                               'stability marker was already consumed')
            if request['outcome'] == 'ACKNOWLEDGED':
                context = GUARD.acquire_operation_lock(
                    project_root, _stability_marker_id(profile_name),
                    _stability_marker_kind(profile_name),
                    state_root=state_root)
            else:
                # The unresolved reboot marker already blocks every guarded
                # write. Keep it unknown and do not attempt the global lock.
                context = nullcontext(None)
            active_category = 'ambiguous_response'

            def remaining_time() -> float:
                remaining = deadline - clock()
                if remaining <= 0:
                    raise TimeoutError('bounded observation deadline exhausted')
                return remaining

            if snapshotter is None:
                read_state = lambda cap: capture_readiness_snapshot(
                    project_root=project_root, transport=transport,
                    timeout=min(STABILITY_READINESS_QUERY_CAP_SECONDS, cap))
            else:
                read_state = lambda _cap: snapshotter()
            if identity_reader is None:
                read_identity = lambda cap: _stability_capture_identity(
                    profile_name, project_root=project_root, transport=transport,
                    remaining=cap)
            else:
                read_identity = lambda cap: identity_reader(profile['target_sha256'])
            if transport is None:
                read_startup_probe = lambda cap: capture_stability_startup_probe(
                    timeout=min(STABILITY_STARTUP_QUERY_CAP_SECONDS, cap),
                    project_root=project_root)
            else:
                read_startup_probe = lambda cap: capture_stability_startup_probe(
                    timeout=min(STABILITY_STARTUP_QUERY_CAP_SECONDS, cap),
                    project_root=project_root, transport=transport)

            def bounded_query(label: str, reader) -> object:
                nonlocal reconnect_count, attempt_count
                cap = {
                    'startup': STABILITY_STARTUP_QUERY_CAP_SECONDS,
                    'readiness': STABILITY_READINESS_QUERY_CAP_SECONDS,
                    'identity': STABILITY_IDENTITY_QUERY_CAP_SECONDS,
                }[label]
                while True:
                    timeout = min(float(cap), remaining_time())
                    attempt_count += 1
                    try:
                        value = reader(timeout)
                    except (ReadOnlyTransportGap, OSError,
                            subprocess.TimeoutExpired) as error:
                        if reconnect_count >= STABILITY_MAX_RECONNECTS_TOTAL:
                            raise ReadOnlyTransportGap(
                                f'{label} query failed after its bounded reconnect') from error
                        reconnect_count += 1
                        pause = min(float(STABILITY_RECONNECT_BACKOFF_SECONDS),
                                    remaining_time())
                        if pause > 0:
                            sleeper(pause)
                        remaining_time()
                        continue
                    if clock() > deadline:
                        raise TimeoutError('bounded observation deadline exhausted during query')
                    return value

            def save_sample(path: Path, *, kind: str, index: int,
                            sample_start: float, state: object, identity: object,
                            status: str, category: str | None,
                            remote_interval_seconds: float | None = None,
                            host_interval_seconds: float | None = None) -> dict:
                record = _sample_record(
                    profile_name=profile_name, kind=kind, index=index,
                    operation_started=operation_started, sampled_started=sample_start,
                    state=state, identity=identity, status=status, category=category,
                    remote_interval_seconds=remote_interval_seconds,
                    host_interval_seconds=host_interval_seconds)
                SHARED.persist_receipt(path, record, directory_fd=directory_fd)
                digest = hashlib.sha256(SHARED.read_host_artifact(
                    path, 'durable stability sample receipt')).hexdigest()
                saved = {'path': path.name, 'sha256': digest, 'status': status,
                         'elapsed_seconds': record['elapsed_seconds'],
                         'remote_uptime_seconds': record['remote_uptime_seconds'],
                         'remote_uptime_valid': record['remote_uptime_valid'],
                         'remote_interval_seconds': record['remote_interval_seconds'],
                         'host_interval_seconds': record['host_interval_seconds']}
                saved_samples.append(saved)
                return saved

            def collect_pair(*, kind: str, index: int,
                             unready_path: Path | None = None
                             ) -> tuple[dict, str, str | None, float]:
                sample_start = clock()
                state = bounded_query('readiness', read_state)
                identity = bounded_query('identity', read_identity)
                remote_uptime = _safe_sample_remote_uptime(state)
                remote_interval = None
                host_interval = None
                try:
                    status, category = _assess_stability_sample(
                        profile_name, initial['observed_boot_id'], state, identity)
                    remote_uptime = _validated_remote_uptime(state)
                    if status == 'ready' and remote_uptime < STABILITY_STARTUP_UPTIME_SECONDS:
                        status, category = 'unready', 'uptime_gate_not_satisfied'
                    elif status == 'ready' and accepted_remote_uptimes:
                        remote_interval = remote_uptime - accepted_remote_uptimes[-1]
                        host_interval = sample_start - accepted_sample_starts[-1]
                        if remote_interval <= 0:
                            raise TerminalObservationError(
                                'remote_uptime_not_advancing',
                                'same-boot readiness uptime did not advance between accepted samples')
                        minimum_remote_gap = max(
                            0.0, host_interval - STABILITY_REMOTE_SAMPLE_START_SKEW_SECONDS)
                        maximum_remote_gap = (
                            host_interval + STABILITY_REMOTE_SAMPLE_START_SKEW_SECONDS)
                        if not minimum_remote_gap <= remote_interval <= maximum_remote_gap:
                            raise TerminalObservationError(
                                'remote_uptime_progression_mismatch',
                                'remote uptime progression is inconsistent with the host sample interval')
                except TerminalObservationError as error:
                    save_sample(sample_paths[index] if kind == 'stability' else
                                (unready_path or startup_paths[index]),
                                kind=kind, index=index, sample_start=sample_start,
                                state=state, identity=identity, status='terminal',
                                category=error.category,
                                remote_interval_seconds=remote_interval,
                                host_interval_seconds=host_interval)
                    raise
                stable_sample = kind == 'stability' or status == 'ready'
                sample_path = (sample_paths[index] if kind == 'stability' else
                               sample_paths[0] if status == 'ready' else
                               (unready_path or startup_paths[index]))
                receipt_kind = 'stability' if stable_sample else 'startup'
                receipt_index = index if kind == 'stability' else 0 if status == 'ready' else index
                saved = save_sample(sample_path, kind=receipt_kind, index=receipt_index,
                                    sample_start=sample_start, state=state,
                                    identity=identity, status=status, category=category,
                                    remote_interval_seconds=remote_interval,
                                    host_interval_seconds=host_interval)
                if status == 'ready':
                    accepted_remote_uptimes.append(remote_uptime)
                    accepted_sample_starts.append(sample_start)
                return saved, status, category, sample_start

            with context as operation:
                if operation is not None:
                    operation.begin(project_root=project_root)
                try:
                    uptime_probe_origin = clock()
                    startup_probe_count = 0
                    boot_id = initial['observed_boot_id']
                    for probe_index in range(STABILITY_STARTUP_POLLS):
                        target = (uptime_probe_origin +
                                  probe_index * STABILITY_STARTUP_POLL_GAP_SECONDS)
                        pause = target - clock()
                        if pause > 0:
                            sleeper(min(pause, remaining_time()))
                        probe = bounded_query('startup', read_startup_probe)
                        if probe.get('boot_id') != boot_id:
                            raise TerminalObservationError(
                                'changed_boot',
                                'startup probe boot ID differs from the bound initial observation')
                        probe_uptime = _validated_remote_uptime(probe)
                        probe_record = {
                            'schema': 's22-audio-coherent-stability-uptime/v1',
                            'profile': profile_name,
                            'trial_identity': profile['trial_identity'],
                            'index': probe_index,
                            'sampled_unix_ns': time.time_ns(),
                            'elapsed_seconds': round(max(0.0, clock() - operation_started), 3),
                            'boot_id': boot_id,
                            'uptime_seconds': probe_uptime,
                            'status': ('ready' if probe_uptime >=
                                       STABILITY_STARTUP_UPTIME_SECONDS else 'waiting'),
                        }
                        SHARED.persist_receipt(uptime_probe_paths[probe_index], probe_record,
                                               directory_fd=directory_fd)
                        startup_probe_count += 1
                        if probe_uptime >= STABILITY_STARTUP_UPTIME_SECONDS:
                            break
                    else:
                        active_category = 'unready_startup'
                        raise RuntimeError(
                            'boot uptime did not reach 180 seconds within the bounded startup phase')

                    stable_start = None
                    first_sample = None
                    for poll in range(STABILITY_READINESS_STARTUP_POLLS):
                        startup_readiness_poll_count += 1
                        if poll:
                            target = clock() + STABILITY_STARTUP_READINESS_GAP_SECONDS
                            pause = target - clock()
                            if pause > 0:
                                sleeper(min(pause, remaining_time()))
                        try:
                            sample, status, category, sample_start = collect_pair(
                                kind='startup', index=poll,
                                unready_path=startup_paths[poll])
                        except ReadOnlyTransportGap as error:
                            active_category = 'transport_gap'
                            raise error
                        if status == 'ready':
                            first_sample = sample
                            stable_start = sample_start
                            break
                        if poll + 1 == STABILITY_READINESS_STARTUP_POLLS:
                            active_category = 'unready_startup'
                            raise RuntimeError(
                                'native/model/network/log readiness remained incomplete after a bounded startup poll')

                    if stable_start is None or first_sample is None:
                        active_category = 'unready_startup'
                        raise RuntimeError('no complete startup sample started the stability interval')
                    stable_receipts = [first_sample]
                    prior_sample_start = stable_start
                    for index in range(1, len(STABILITY_SAMPLE_OFFSETS_SECONDS)):
                        target = max(
                            stable_start + STABILITY_SAMPLE_OFFSETS_SECONDS[index],
                            prior_sample_start + STABILITY_MIN_SAMPLE_GAP_SECONDS)
                        if index == len(STABILITY_SAMPLE_OFFSETS_SECONDS) - 1:
                            target = max(
                                target,
                                stable_start + STABILITY_REMOTE_FINAL_SAMPLE_OFFSET_SECONDS)
                        pause = target - clock()
                        if pause > 0:
                            sleeper(min(pause, remaining_time()))
                        try:
                            sample, status, category, sample_start = collect_pair(
                                kind='stability', index=index)
                        except ReadOnlyTransportGap as error:
                            active_category = 'transport_gap'
                            raise error
                        if status != 'ready':
                            active_category = 'later_gate_failure'
                            raise RuntimeError(
                                'a later stability sample did not meet every readiness gate')
                        gap = sample_start - prior_sample_start
                        if gap < STABILITY_MIN_SAMPLE_GAP_SECONDS:
                            active_category = 'sample_gap'
                            raise RuntimeError('fresh stability samples were not sufficiently spaced')
                        stable_receipts.append(sample)
                        prior_sample_start = sample_start

                    remaining_time()
                    observed_seconds = prior_sample_start - stable_start
                    if (len(stable_receipts) != 3 or
                            observed_seconds < STABILITY_MIN_SECONDS):
                        active_category = 'sample_gap'
                        raise RuntimeError('bounded stability interval was shorter than 180 seconds')
                    remote_observed_seconds = (
                        accepted_remote_uptimes[-1] - accepted_remote_uptimes[0]
                        if len(accepted_remote_uptimes) == len(stable_receipts) else -1)
                    if remote_observed_seconds < STABILITY_MIN_SECONDS:
                        active_category = 'remote_interval_short'
                        raise RuntimeError(
                            'remote uptime interval was shorter than 180 seconds')
                    receipt = {
                        'schema': 's22-audio-coherent-stability-observation/v1',
                        'profile': profile_name,
                        'trial_identity': profile['trial_identity'],
                        # This durable file is only a candidate. Its digest is
                        # bound into the exact timely global marker below; the
                        # file alone must never be treated as acceptance.
                        'status': 'provisional-bounded-stability-observed',
                        'reboot_request_outcome': request['outcome'],
                        'reboot_requests': 1,
                        'retry_allowed': False,
                        'acceptance_binding': {
                            'requires_matching_global_guard_marker': True,
                            'marker_id': _stability_marker_id(profile_name),
                            'operation_kind': _stability_marker_kind(profile_name),
                            'receipt_alone_is_acceptance': False,
                        },
                        'baseline_boot_id': flash['prewrite_identity']['boot_id'],
                        'observed_boot_id': initial['observed_boot_id'],
                        'recovery_sha256': profile['target_sha256'],
                        'kernel_gnu_build_id': ADAPTER.RUNNING_KERNEL_BUILD_ID,
                        'audio_module_gnu_build_ids': _expected_profile_modules(profile_name),
                        'stability_seconds': round(observed_seconds, 3),
                        'sample_offsets_seconds': [round(start - stable_start, 3)
                                                   for start in accepted_sample_starts],
                        'remote_sample_uptimes_seconds': accepted_remote_uptimes,
                        'remote_sample_intervals_seconds': [
                            None, *(current - previous
                                    for previous, current in zip(
                                        accepted_remote_uptimes,
                                        accepted_remote_uptimes[1:]))],
                        'remote_stability_seconds': remote_observed_seconds,
                        'sample_count': len(stable_receipts),
                        'startup_readiness_polls': startup_readiness_poll_count,
                        'startup_uptime_probes': startup_probe_count,
                        'read_only_reconnect_attempts': reconnect_count,
                        'remote_query_attempts': attempt_count,
                        'samples': stable_receipts,
                        'audio_hardware_acceptance': False,
                        'bootability_claim': False,
                        'full_boot_log_coverage_claimed': False,
                    }
                    final_result_persist_started = True
                    SHARED.persist_receipt(paths['result'], receipt,
                                           directory_fd=directory_fd)
                    final_result_persist_returned = True
                    remaining_time()
                    final_result_sha256 = hashlib.sha256(SHARED.read_host_artifact(
                        paths['result'], 'durable stability result receipt')).hexdigest()
                    remaining_time()
                    if operation is not None:
                        pending_marker = _read_stability_operation_marker(
                            operation, profile_name=profile_name, state_root=state_root,
                            project_root=project_root)
                        _require_stability_marker_phase(
                            pending_marker, phase='pending', operation=operation)
                        remaining_time()
                        operation.complete(paths['result'], outcome='success',
                                           cleanup_confirmed=True)
                        try:
                            remaining_time()
                        except TimeoutError as timeout_error:
                            active_category = 'timeout'
                            completed_marker = _read_stability_operation_marker(
                                operation, profile_name=profile_name,
                                state_root=state_root, project_root=project_root)
                            _require_stability_marker_phase(
                                completed_marker, phase='complete',
                                result_path=paths['result'],
                                result_sha256=final_result_sha256)
                            late_completed_marker = completed_marker
                            _demote_late_stability_completion(
                                operation, profile_name=profile_name, state_root=state_root,
                                project_root=project_root, result_path=paths['result'],
                                result_sha256=final_result_sha256,
                                completed_marker=completed_marker,
                                timeout_error=timeout_error)
                            raise
                        completed_marker = _read_stability_operation_marker(
                            operation, profile_name=profile_name, state_root=state_root,
                            project_root=project_root)
                        _require_stability_marker_phase(
                            completed_marker, phase='complete',
                            result_path=paths['result'],
                            result_sha256=final_result_sha256)
                        try:
                            remaining_time()
                        except TimeoutError as timeout_error:
                            active_category = 'timeout'
                            late_completed_marker = completed_marker
                            _demote_late_stability_completion(
                                operation, profile_name=profile_name, state_root=state_root,
                                project_root=project_root, result_path=paths['result'],
                                result_sha256=final_result_sha256,
                                completed_marker=completed_marker,
                                timeout_error=timeout_error)
                            raise
                        # Return a finalization view only after the exact
                        # receipt/marker binding and deadline have both been
                        # read back successfully. The on-disk receipt remains
                        # the provisional candidate whose digest is guarded.
                        return {
                            **receipt,
                            'status': 'bounded-stability-observed',
                            'persisted_receipt_status': receipt['status'],
                            'finalization': {
                                'accepted': True,
                                'basis': 'timely-matching-global-guard-marker',
                                'marker_id': _stability_marker_id(profile_name),
                                'operation_kind': _stability_marker_kind(profile_name),
                                'receipt_path': str(paths['result']),
                                'receipt_sha256': final_result_sha256,
                            },
                        }
                    # An UNKNOWN reboot request deliberately has no stability
                    # operation marker to complete. Keep its read-only result
                    # provisional and let the CLI report non-acceptance.
                    return {
                        **receipt,
                        'finalization': {
                            'accepted': False,
                            'basis': 'unresolved-reboot-marker-no-stability-guard',
                            'receipt_path': str(paths['result']),
                            'receipt_sha256': final_result_sha256,
                        },
                    }
                except Exception as error:
                    if isinstance(error, ReadOnlyTransportGap):
                        active_category = 'transport_gap'
                    elif isinstance(error, TimeoutError):
                        active_category = 'timeout'
                    elif isinstance(error, TerminalObservationError):
                        active_category = error.category
                    failure = {
                        'schema': 's22-audio-coherent-stability-failure/v1',
                        'profile': profile_name,
                        'trial_identity': profile['trial_identity'],
                        'status': 'UNKNOWN',
                        'outcome': 'UNKNOWN',
                        'failure_category': active_category,
                        'retry_allowed': False,
                        'provisional_result_receipt': ({
                            'path': str(paths['result']),
                            'sha256': final_result_sha256,
                            'persist_started': final_result_persist_started,
                            'persist_returned': final_result_persist_returned,
                            'accepted': False,
                        } if final_result_persist_started else None),
                        'late_success_completion': late_completed_marker,
                        'sample_receipts': saved_samples,
                        'remote_sample_uptimes_seconds': accepted_remote_uptimes,
                        'remote_sample_intervals_seconds': [
                            None, *(current - previous
                                    for previous, current in zip(
                                        accepted_remote_uptimes,
                                        accepted_remote_uptimes[1:]))],
                        'remote_stability_seconds': (
                            accepted_remote_uptimes[-1] - accepted_remote_uptimes[0]
                            if len(accepted_remote_uptimes) >= 2 else None),
                        'host_sample_offsets_seconds': [
                            round(start - accepted_sample_starts[0], 3)
                            for start in accepted_sample_starts],
                        'read_only_reconnect_attempts': reconnect_count,
                        'remote_query_attempts': attempt_count,
                        'elapsed_seconds': round(max(0.0, clock() - operation_started), 3),
                        'error_type': type(error).__name__,
                    }
                    SHARED.persist_receipt(paths['failure'], failure,
                                           directory_fd=directory_fd)
                    raise
        except Exception as error:
            if not Path(paths['failure']).exists():
                failure = {
                    'schema': 's22-audio-coherent-stability-failure/v1',
                    'profile': profile_name,
                    'trial_identity': profile['trial_identity'],
                    'status': 'UNKNOWN',
                    'outcome': 'UNKNOWN',
                    'failure_category': (error.category if isinstance(
                        error, TerminalObservationError) else active_category),
                    'retry_allowed': False,
                    'provisional_result_receipt': ({
                        'path': str(paths['result']),
                        'sha256': final_result_sha256,
                        'persist_started': final_result_persist_started,
                        'persist_returned': final_result_persist_returned,
                        'accepted': False,
                    } if final_result_persist_started else None),
                    'late_success_completion': late_completed_marker,
                    'sample_receipts': saved_samples,
                    'remote_sample_uptimes_seconds': accepted_remote_uptimes,
                    'remote_sample_intervals_seconds': [
                        None, *(current - previous
                                for previous, current in zip(
                                    accepted_remote_uptimes,
                                    accepted_remote_uptimes[1:]))],
                    'remote_stability_seconds': (
                        accepted_remote_uptimes[-1] - accepted_remote_uptimes[0]
                        if len(accepted_remote_uptimes) >= 2 else None),
                    'host_sample_offsets_seconds': [
                        round(start - accepted_sample_starts[0], 3)
                        for start in accepted_sample_starts],
                    'read_only_reconnect_attempts': reconnect_count,
                    'remote_query_attempts': attempt_count,
                    'elapsed_seconds': round(max(0.0, clock() - operation_started), 3),
                    'error_type': type(error).__name__,
                }
                try:
                    SHARED.persist_receipt(paths['failure'], failure,
                                           directory_fd=directory_fd)
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
        'stability_observation_execution': False,
        'stability_observation_total_deadline_seconds': STABILITY_TOTAL_DEADLINE_SECONDS,
        'stability_observation_sample_offsets_seconds': list(STABILITY_SAMPLE_OFFSETS_SECONDS),
        'stability_observation_minimum_remote_interval_seconds': STABILITY_MIN_SECONDS,
        'stability_observation_remote_final_sample_start_minimum_seconds':
            STABILITY_REMOTE_FINAL_SAMPLE_OFFSET_SECONDS,
        'audio_hardware_acceptance': False,
    })
    return plan


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    choice = parser.add_mutually_exclusive_group()
    choice.add_argument('--request-reboot', action='store_true')
    choice.add_argument('--observe-once', action='store_true')
    choice.add_argument('--observe-stability', action='store_true')
    parser.add_argument('--profile', required=True, choices=tuple(PROFILE.PROFILES))
    parser.add_argument('--execute', action='store_true',
                        help='request operation intent; this is not owner authorization')
    parser.add_argument('--trial-identity')
    args = parser.parse_args(argv)
    operation = ('reboot' if args.request_reboot else
                 'observe' if args.observe_once else
                 'stability' if args.observe_stability else None)
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
    if operation == 'reboot':
        result = request_recovery_once(args.profile)
    elif operation == 'observe':
        result = observe_reboot_once(args.profile)
    else:
        result = observe_stability(args.profile)
    print(json.dumps(result, indent=2, sort_keys=True))
    if operation == 'stability' and result.get('finalization', {}).get('accepted') is not True:
        return 2
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError, PermissionError) as error:
        raise SystemExit(f'refused: {error}') from error
