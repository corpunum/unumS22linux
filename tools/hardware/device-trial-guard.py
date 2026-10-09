#!/usr/bin/env python3
"""Host-global serialization and durable replay guard for S22 device trials.

This intentionally small helper is shared by the Bluetooth and audio trial
runners. It is not a recovery service: a pending marker survives interruption
and blocks further trials until an operator records explicit reconciliation.
"""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import time


STATE_ROOT = Path('/home/corpunum/.local/state/s22-device-trial-guard')
_TRIAL_RE = re.compile(r'[a-z0-9][a-z0-9-]{0,95}\Z')
_TERMINAL = {'complete', 'reconciled'}
_O_NOFOLLOW = getattr(os, 'O_NOFOLLOW', 0)


class TrialGuardError(RuntimeError):
    """Fail-closed host serialization or durable-state error."""


def _require(condition, message):
    if not condition:
        raise TrialGuardError(message)


def _lstat(path, label):
    try:
        return path.lstat()
    except OSError as error:
        raise TrialGuardError(f'{label} cannot be inspected: {path}: {error}') from error


def _private_dir(path, *, uid=None):
    info = _lstat(path, 'private directory')
    expected_uid = os.geteuid() if uid is None else uid
    _require(stat.S_ISDIR(info.st_mode) and info.st_uid == expected_uid and
             stat.S_IMODE(info.st_mode) == 0o700,
             f'directory must be a real owner-owned mode-0700 directory: {path}')
    return info


def _state_directory(state_root):
    root = Path(state_root)
    _require(root.is_absolute(), 'state directory path must be absolute')
    parent = _private_dir(root.parent)
    try:
        root.mkdir(mode=0o700)
    except FileExistsError:
        pass
    except OSError as error:
        raise TrialGuardError(f'cannot create private state directory: {error}') from error
    info = _private_dir(root)
    now_parent = _lstat(root.parent, 'state parent')
    _require((parent.st_dev, parent.st_ino) == (now_parent.st_dev, now_parent.st_ino),
             'state parent identity changed during initialization')
    return root, info


def _read_marker(path):
    info = _lstat(path, 'trial marker')
    _require(stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid() and
             stat.S_IMODE(info.st_mode) == 0o600 and info.st_nlink == 1,
             f'trial marker is not a private regular file: {path}')
    try:
        with path.open('r', encoding='utf-8') as stream:
            value = json.load(stream)
    except (OSError, ValueError) as error:
        raise TrialGuardError(f'existing trial marker is unreadable; manual reconciliation required: {path}') from error
    _require(isinstance(value, dict) and isinstance(value.get('status'), str),
             f'existing trial marker is malformed; manual reconciliation required: {path}')
    return value


def _write_all(fd, value):
    payload = (json.dumps(value, sort_keys=True, separators=(',', ':')) + '\n').encode()
    offset = 0
    while offset < len(payload):
        offset += os.write(fd, payload[offset:])
    os.ftruncate(fd, len(payload))
    os.fsync(fd)


def _fsync_dir(path):
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0) | _O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _receipt_digest(receipt_path):
    path = Path(receipt_path)
    parent_info = _private_dir(path.parent)
    try:
        fd = os.open(path, os.O_RDWR | os.O_CLOEXEC | _O_NOFOLLOW)
    except OSError as error:
        raise TrialGuardError(f'completion receipt cannot be opened safely: {error}') from error
    try:
        info = os.fstat(fd)
        path_info = _lstat(path, 'completion receipt')
        _require(stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid() and
                 not (stat.S_IMODE(info.st_mode) & 0o077) and info.st_nlink == 1 and
                 (info.st_dev, info.st_ino) == (path_info.st_dev, path_info.st_ino),
                 'completion receipt must be an owner-only regular file')
        digest = hashlib.sha256()
        for block in iter(lambda: os.read(fd, 1024 * 1024), b''):
            digest.update(block)
        # The board runner historically used write_text without fsync. Flush
        # the receipt itself and its directory before the terminal marker.
        os.fsync(fd)
        _fsync_dir(path.parent)
        current_parent = _lstat(path.parent, 'completion receipt parent')
        _require((parent_info.st_dev, parent_info.st_ino) ==
                 (current_parent.st_dev, current_parent.st_ino),
                 'completion receipt parent identity changed')
    finally:
        os.close(fd)
    return str(path), digest.hexdigest()


class Operation:
    def __init__(self, state_root, trial_id, operation_kind, parent_identity):
        self.state_root = state_root
        self.trial_id = trial_id
        self.operation_kind = operation_kind
        self.parent_identity = parent_identity
        self.marker_path = state_root / f'{trial_id}.json'
        self.marker_fd = None
        self.started = False
        self.finished = False

    def begin(self, *, project_root):
        """Persist pending state before the first remote write/side effect."""
        _require(not self.started, 'operation marker already started')
        _require(_TRIAL_RE.fullmatch(self.trial_id) is not None,
                 'trial identity must be a lowercase stable identifier')
        before = _lstat(self.state_root.parent, 'state parent')
        _require((before.st_dev, before.st_ino) == self.parent_identity,
                 'state parent identity changed before begin')
        try:
            fd = os.open(self.marker_path,
                         os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | _O_NOFOLLOW,
                         0o600)
        except FileExistsError as error:
            raise TrialGuardError('trial identity already has a durable marker; never reuse it') from error
        try:
            info = os.fstat(fd)
            path_info = _lstat(self.marker_path, 'new trial marker')
            _require(stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid() and
                     stat.S_IMODE(info.st_mode) == 0o600 and info.st_nlink == 1 and
                     (info.st_dev, info.st_ino) == (path_info.st_dev, path_info.st_ino),
                     'new trial marker identity or permissions are unsafe')
            self.marker_fd = fd
            self.started = True
            _write_all(fd, {
                'trial_id': self.trial_id,
                'operation_kind': self.operation_kind,
                'project_root': str(Path(project_root).resolve()),
                'pid': os.getpid(),
                'started_unix_ns': time.time_ns(),
                'status': 'pending',
            })
            _fsync_dir(self.state_root)
            self._verify_marker_identity()
        except BaseException:
            if not self.started:
                os.close(fd)
            raise

    def _verify_marker_identity(self):
        current = _lstat(self.marker_path, 'trial marker')
        opened = os.fstat(self.marker_fd)
        _require((current.st_dev, current.st_ino) == (opened.st_dev, opened.st_ino) and
                 stat.S_ISREG(current.st_mode) and current.st_uid == os.geteuid() and
                 stat.S_IMODE(current.st_mode) == 0o600 and current.st_nlink == 1,
                 'trial marker path identity changed')

    def _update(self, fields):
        _require(self.started and self.marker_fd is not None,
                 'cannot update an operation before its pending marker')
        self._verify_marker_identity()
        try:
            os.lseek(self.marker_fd, 0, os.SEEK_SET)
            _write_all(self.marker_fd, fields)
            _fsync_dir(self.state_root)
        except OSError as error:
            raise TrialGuardError(f'could not durably update trial marker: {error}') from error

    def complete(self, receipt_path, *, outcome='success', cleanup_confirmed=False):
        """Write a terminal marker only after durable receipt and confirmed cleanup."""
        allowed = {'success', 'failed-cleanup-confirmed', 'preflight-rejected-no-mutation'}
        _require(outcome in allowed, 'unsupported terminal trial outcome')
        _require(cleanup_confirmed is True,
                 'completion requires confirmed cleanup or no-mutation disposition')
        path, digest = _receipt_digest(receipt_path)
        self._update({
            'trial_id': self.trial_id,
            'operation_kind': self.operation_kind,
            'status': 'complete',
            'outcome': outcome,
            'receipt_path': path,
            'receipt_sha256': digest,
            'completed_unix_ns': time.time_ns(),
        })
        self.finished = True

    def _mark_unknown(self, error):
        if not self.started or self.finished:
            return
        # Failure to record UNKNOWN leaves the durable pending marker in place,
        # which is still unresolved and blocks future operations.
        try:
            self._update({
                'trial_id': self.trial_id,
                'operation_kind': self.operation_kind,
                'status': 'unknown',
                'reason': f'{type(error).__name__}: {error}'[:512],
                'updated_unix_ns': time.time_ns(),
            })
        except Exception:
            pass


class _LockedOperation:
    def __init__(self, project_root, trial_id, operation_kind, state_root):
        self.project_root = Path(project_root)
        self.trial_id = trial_id
        self.operation_kind = operation_kind
        self.state_root = Path(state_root)
        self.fd = None
        self.operation = None
        self.parent_identity = None

    def __enter__(self):
        _require(_TRIAL_RE.fullmatch(self.trial_id) is not None,
                 'trial identity must be a lowercase stable identifier')
        _require(_TRIAL_RE.fullmatch(self.operation_kind) is not None,
                 'operation kind must be a lowercase stable identifier')
        root, root_info = _state_directory(self.state_root)
        parent_info = _lstat(root.parent, 'state parent')
        lock_path = root / 'operation.lock'
        try:
            fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | _O_NOFOLLOW, 0o600)
        except OSError as error:
            raise TrialGuardError(f'cannot open host operation lock: {error}') from error
        try:
            info = os.fstat(fd)
            path_info = _lstat(lock_path, 'operation lock')
            _require(stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid() and
                     stat.S_IMODE(info.st_mode) == 0o600 and info.st_nlink == 1 and
                     (info.st_dev, info.st_ino) == (path_info.st_dev, path_info.st_ino),
                     'operation lock is not a private regular file')
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise TrialGuardError('another S22 device operation holds the host lock') from error
            final_root = _private_dir(root)
            final_parent = _lstat(root.parent, 'state parent')
            final_lock = _lstat(lock_path, 'operation lock')
            _require((root_info.st_dev, root_info.st_ino) ==
                     (final_root.st_dev, final_root.st_ino) and
                     (parent_info.st_dev, parent_info.st_ino) ==
                     (final_parent.st_dev, final_parent.st_ino) and
                     (info.st_dev, info.st_ino) == (final_lock.st_dev, final_lock.st_ino),
                     'host lock/state path identity changed')
            for marker in root.glob('*.json'):
                state = _read_marker(marker)
                if state.get('status') not in _TERMINAL:
                    raise TrialGuardError(
                        f'unresolved trial marker {marker.name}; explicit reconciliation required')
            marker = root / f'{self.trial_id}.json'
            if marker.exists() or marker.is_symlink():
                raise TrialGuardError('trial identity already has a durable marker; never reuse it')
            self.fd = fd
            self.parent_identity = (final_parent.st_dev, final_parent.st_ino)
            self.operation = Operation(root, self.trial_id, self.operation_kind,
                                        self.parent_identity)
            return self.operation
        except BaseException:
            if self.fd is None:
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                except OSError:
                    pass
                os.close(fd)
            raise

    def __exit__(self, exc_type, exc, traceback):
        try:
            if self.operation is not None and self.operation.started and not self.operation.finished:
                reason = exc if exc is not None else RuntimeError(
                    'operation exited without a terminal receipt')
                self.operation._mark_unknown(reason)
        finally:
            if self.operation is not None and self.operation.marker_fd is not None:
                os.close(self.operation.marker_fd)
            if self.fd is not None:
                fcntl.flock(self.fd, fcntl.LOCK_UN)
                os.close(self.fd)
        return False


def acquire_operation_lock(project_root, trial_id, operation_kind, *, state_root=STATE_ROOT):
    """Acquire the single nonblocking host-global S22 operation lock."""
    return _LockedOperation(project_root, trial_id, operation_kind, state_root)


def reconcile_operation(trial_id, evidence, *, state_root=STATE_ROOT):
    """Explicitly terminalize one unresolved marker without deleting it."""
    _require(_TRIAL_RE.fullmatch(trial_id) is not None,
             'trial identity must be a lowercase stable identifier')
    _require(isinstance(evidence, str) and evidence.strip(),
             'reconciliation requires explicit evidence text')
    root, _ = _state_directory(state_root)
    lock_path = root / 'operation.lock'
    fd = os.open(lock_path, os.O_RDWR | os.O_CLOEXEC | _O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        path_info = _lstat(lock_path, 'operation lock')
        _require(stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid() and
                 stat.S_IMODE(info.st_mode) == 0o600 and info.st_nlink == 1 and
                 (info.st_dev, info.st_ino) == (path_info.st_dev, path_info.st_ino),
                 'operation lock is not a private regular file')
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise TrialGuardError('another S22 device operation holds the host lock') from error
        marker = root / f'{trial_id}.json'
        value = _read_marker(marker)
        _require(value.get('status') not in _TERMINAL,
                 'trial marker is already terminal; preserve it')
        _require(value.get('status') in {'pending', 'unknown'},
                 'trial marker status is not reconcilable')
        value.update(status='reconciled', reconciliation=evidence.strip()[:2048],
                     reconciled_unix_ns=time.time_ns())
        marker_fd = os.open(marker, os.O_RDWR | os.O_CLOEXEC | _O_NOFOLLOW)
        try:
            current = os.fstat(marker_fd)
            current_path = _lstat(marker, 'trial marker')
            _require((current.st_dev, current.st_ino) ==
                     (current_path.st_dev, current_path.st_ino) and
                     current.st_uid == os.geteuid() and stat.S_IMODE(current.st_mode) == 0o600,
                     'trial marker identity changed during reconciliation')
            os.lseek(marker_fd, 0, os.SEEK_SET)
            _write_all(marker_fd, value)
            _fsync_dir(root)
        finally:
            os.close(marker_fd)
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
