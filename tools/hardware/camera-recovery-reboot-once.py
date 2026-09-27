#!/usr/bin/env python3
"""Camera-specific, one-shot RECOVERY reboot and read-only observer.

The default is a host-only plan. A reboot requires a direction-specific owner
acknowledgement and a validated boot-bound flash receipt. Observation is a
separate mode and contains no reboot/request path.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import shlex
import stat
import subprocess
import sys
import time
from datetime import datetime, timezone


ROOT = Path(__file__).resolve().parents[2]
TRIAL_HOME = Path.home() / ".local/state/s22-camera-trial-20260927"
RECEIPTS_ROOT = TRIAL_HOME / "receipts"
OBSERVER_ROOT = TRIAL_HOME / "observer"
RUNNING_KERNEL_BUILD_ID = "b2dda820b18d410d9bf12f1bd2584567d545991d"
S22_REBOOT_SHA256 = "25df3dafd81a0fb8ff0b89055977918e386bd83959f23e5723018feda82f2c42"
S22_RESTART2_SHA256 = "8a602f6182eff2501af57569b8f5499f78a0a0a73fd5dabea1e55a82bb683eb1"
S22_REBOOT_TRIAL_IDS = {
    "camera-forward": "camera-recovery-20260927-forward-reboot",
    "camera-reverse": "camera-recovery-20260927-reverse-reboot",
}
OWNER_ACK = {
    "camera-forward": "CAMERA-FORWARD-20260927-ONE-RECOVERY-REBOOT",
    "camera-reverse": "CAMERA-REVERSE-20260927-ONE-RECOVERY-REBOOT",
}
REQUEST_COMMAND_LABEL = "boot-id-guarded exec /usr/local/sbin/s22-reboot recovery"
OBSERVATION_SECONDS = 600
SAMPLE_INTERVAL_SECONDS = 5
MIN_STABLE_SECONDS = 180
_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")


class CameraObserverError(RuntimeError):
    """Fail-closed local evidence or observation error."""


class ReadOnlyTransportGap(RuntimeError):
    """A bounded read-only query could not reach/return from the device."""


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise CameraObserverError(f"cannot load host helper {path.name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# Reuse only the existing read-only observer helpers and the exact camera
# receipt/identity contract. No audio request, marker, or HCI smoke code runs.
AUDIO = _load_module("s22_camera_audio_readonly_helpers",
                     ROOT / "tools/hardware/audio-recovery-reboot-once.py")
DEPLOY = _load_module("s22_camera_deploy_receipt_contract",
                      ROOT / "tools/hardware/deploy-camera-recovery.py")
SHARED, GUARD = DEPLOY._load_shared(ROOT)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise CameraObserverError(message)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _private_json(path: Path, *, max_bytes: int = 1_048_576) -> tuple[dict, bytes]:
    """Read one owner-private receipt without following a leaf symlink."""
    try:
        parent = path.parent.lstat()
        before = path.lstat()
        _require(stat.S_ISDIR(parent.st_mode) and not path.parent.is_symlink() and
                 parent.st_uid == os.geteuid() and stat.S_IMODE(parent.st_mode) == 0o700,
                 "receipt directory must be owner-private mode 0700")
        _require(stat.S_ISREG(before.st_mode) and before.st_uid == os.geteuid() and
                 stat.S_IMODE(before.st_mode) == 0o600 and before.st_nlink == 1 and
                 before.st_size <= max_bytes,
                 "receipt must be an owner-private bounded regular file")
        fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0))
    except OSError as error:
        raise CameraObserverError(f"required private receipt unavailable: {path.name}") from error
    try:
        opened = os.fstat(fd)
        _require(stat.S_ISREG(opened.st_mode) and opened.st_uid == os.geteuid() and
                 stat.S_IMODE(opened.st_mode) == 0o600 and opened.st_nlink == 1 and
                 (opened.st_dev, opened.st_ino) == (before.st_dev, before.st_ino),
                 "receipt identity changed while opening")
        content = bytearray()
        while len(content) <= max_bytes:
            chunk = os.read(fd, min(65536, max_bytes + 1 - len(content)))
            if not chunk:
                break
            content.extend(chunk)
        _require(len(content) <= max_bytes, "receipt exceeds the local size bound")
    finally:
        os.close(fd)
    try:
        value = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CameraObserverError("private receipt is not valid JSON") from error
    _require(isinstance(value, dict), "private receipt must be a JSON object")
    return value, bytes(content)


def _profile(profile_name: str) -> dict:
    try:
        return DEPLOY.resolve_profile(profile_name)
    except ValueError as error:
        raise CameraObserverError(str(error)) from error


def flash_receipt_directory(profile_name: str, receipts_root: Path = RECEIPTS_ROOT) -> Path:
    identity = DEPLOY.TRIAL_IDENTITIES[(profile_name, "flash")]
    return Path(receipts_root) / identity


def validate_flash_bundle(profile_name: str, *, receipts_root: Path = RECEIPTS_ROOT) -> dict:
    """Validate final flash receipt and both private, hash-linked predecessors."""
    profile = _profile(profile_name)
    directory = flash_receipt_directory(profile_name, receipts_root)
    prefix = profile["receipt_prefix"]
    pre_path = directory / f"{prefix}-flash-prewrite.json"
    raw_path = directory / f"{prefix}-flash-write-readback.json"
    final_path = directory / f"{prefix}-flash.json"
    pre, pre_bytes = _private_json(pre_path)
    raw, raw_bytes = _private_json(raw_path)
    final, final_bytes = _private_json(final_path)
    trial_identity = DEPLOY.TRIAL_IDENTITIES[(profile_name, "flash")]
    for value, schema, label in (
        (pre, "camera-recovery-prewrite/v1", "pre-write"),
        (raw, "camera-recovery-write-readback/v1", "raw write/readback"),
        (final, "camera-recovery-flash-bound/v1", "flash-bound"),
    ):
        _require(value.get("schema") == schema, f"{label} receipt schema mismatch")
        _require(value.get("profile") == profile_name and
                 value.get("trial_identity") == trial_identity and
                 value.get("partition") == "recovery",
                 f"{label} receipt profile/trial/partition mismatch")

    pre_identity = pre.get("identity")
    try:
        DEPLOY.validate_camera_identity(
            pre_identity, expected_recovery_sha=profile["before_sha256"],
            require_baseline_camera_module=(profile_name == "camera-forward"))
    except (TypeError, ValueError) as error:
        raise CameraObserverError(f"pre-write identity is not bound: {error}") from error
    _require(pre.get("expected_write_sha256") == profile["new_sha256"],
             "pre-write receipt expected-image hash mismatch")
    _require(raw.get("mode") == "flash" and
             raw.get("partition_written") == "recovery" and
             raw.get("before_sha256") == profile["before_sha256"] and
             raw.get("readback_sha256") == profile["new_sha256"] and
             raw.get("written_image_sha256") == profile["new_sha256"] and
             raw.get("bytes") == DEPLOY.SIZE and
             raw.get("reboot_performed") is False and
             raw.get("explicit_execute_flag_present") is True,
             "raw write/readback receipt image/mode fields mismatch")
    _require(raw.get("prewrite_identity_receipt_sha256") == _sha256(pre_bytes) and
             raw.get("prewrite_boot_id") == pre_identity.get("boot_id") and
             raw.get("prewrite_kernel_gnu_build_id") == pre_identity.get("kernel_gnu_build_id") and
             raw.get("prewrite_camera_module_loaded") == pre_identity.get("camera_module_loaded") and
             raw.get("prewrite_camera_module_gnu_build_id") ==
             pre_identity.get("camera_module_gnu_build_id"),
             "raw receipt is not linked to the exact pre-write identity receipt")
    _require(raw.get("expected_before_module_build_id") == profile["before"]["module_build_id"] and
             raw.get("expected_write_module_build_id") == profile["write"]["module_build_id"],
             "raw receipt expected module identities mismatch")
    try:
        SHARED.validate_remote_receipt(
            raw, mode="flash", before_sha=profile["before_sha256"],
            candidate_sha=profile["new_sha256"], size=DEPLOY.SIZE)
    except ValueError as error:
        raise CameraObserverError(f"raw receipt readback proof mismatch: {error}") from error

    post_identity = final.get("postwrite_identity")
    try:
        DEPLOY.validate_camera_identity(
            post_identity, expected_recovery_sha=profile["new_sha256"],
            require_baseline_camera_module=(profile_name == "camera-forward"))
    except (TypeError, ValueError) as error:
        raise CameraObserverError(f"post-write identity is not bound: {error}") from error
    _require(_sha256(raw_bytes) == final.get("write_readback_receipt_sha256"),
             "final receipt does not hash the exact raw readback receipt")
    _require(final.get("prewrite_identity_receipt_sha256") == _sha256(pre_bytes) and
             final.get("prewrite_boot_id") == pre_identity.get("boot_id") and
             final.get("prewrite_postwrite_same_boot") is True and
             post_identity.get("boot_id") == pre_identity.get("boot_id") and
             post_identity.get("kernel_gnu_build_id") == pre_identity.get("kernel_gnu_build_id") and
             post_identity.get("camera_module_loaded") == pre_identity.get("camera_module_loaded") and
             post_identity.get("camera_module_gnu_build_id") ==
             pre_identity.get("camera_module_gnu_build_id"),
             "flash receipt does not prove a same-boot write/readback window")
    for key in ("mode", "partition_written", "before_sha256", "readback_sha256",
                "written_image_sha256", "bytes", "reboot_performed", "profile",
                "trial_identity", "partition", "expected_before_module_build_id",
                "expected_write_module_build_id", "prewrite_identity_receipt_sha256",
                "prewrite_boot_id", "prewrite_kernel_gnu_build_id",
                "prewrite_camera_module_loaded", "prewrite_camera_module_gnu_build_id"):
        _require(final.get(key) == raw.get(key), f"final receipt changed raw field {key}")
    return {
        "profile": profile,
        "trial_identity": trial_identity,
        "prewrite_identity": pre_identity,
        "postwrite_identity": post_identity,
        "flash_receipt_sha256": _sha256(final_bytes),
        "prewrite_bore": None,
        "directory": directory,
    }


HELPER_SNAPSHOT = r'''import hashlib,json,os,stat
items={}
for key,path in [('reboot','/usr/local/sbin/s22-reboot'),('restart2','/usr/local/libexec/s22-restart2')]:
 try:
  before=os.lstat(path)
  if not stat.S_ISREG(before.st_mode): raise ValueError('not regular')
  if before.st_size>2097152: raise ValueError('helper exceeds 2 MiB bound')
  fd=os.open(path,os.O_RDONLY|os.O_CLOEXEC|os.O_NOFOLLOW)
  try:
   after=os.fstat(fd)
   if (before.st_dev,before.st_ino)!=(after.st_dev,after.st_ino) or after.st_size>2097152: raise ValueError('identity changed or oversized')
   h=hashlib.sha256()
   total=0
   while True:
    b=os.read(fd,min(65536,2097153-total))
    if not b: break
    total+=len(b)
    if total>2097152: raise ValueError('helper grew beyond 2 MiB bound')
    h.update(b)
   items[key]={'sha256':h.hexdigest(),'uid':after.st_uid,'mode':stat.S_IMODE(after.st_mode),
               'regular':stat.S_ISREG(after.st_mode),'symlink':False}
  finally: os.close(fd)
 except Exception: items[key]=None
print(json.dumps({'schema':'camera-reboot-helpers/v1','helpers':items},separators=(',',':')))
'''


def render_reboot_prelude(expected_boot_id: str) -> str:
    """Return an immediate same-boot/native guard that execs the helper once."""
    _require(isinstance(expected_boot_id, str) and
             re.fullmatch(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}",
                          expected_boot_id) is not None,
             "reboot prelude requires a canonical captured boot ID")
    return f'''import hashlib,os,stat
expected={expected_boot_id!r}
with open('/proc/sys/kernel/random/boot_id','r',encoding='ascii') as stream:
 current=stream.read(128).strip()
if current!=expected: raise SystemExit(42)
if os.geteuid()!=0 or os.readlink('/proc/1/exe')!='/system/bin/native-guardian': raise SystemExit(43)
items=[('/usr/local/sbin/s22-reboot','{S22_REBOOT_SHA256}'),('/usr/local/libexec/s22-restart2','{S22_RESTART2_SHA256}')]
for path,digest in items:
 before=os.lstat(path)
 if not stat.S_ISREG(before.st_mode) or before.st_uid!=0 or stat.S_IMODE(before.st_mode)!=0o755 or before.st_size>2097152: raise SystemExit(44)
 fd=os.open(path,os.O_RDONLY|os.O_CLOEXEC|os.O_NOFOLLOW)
 try:
  after=os.fstat(fd)
  if (before.st_dev,before.st_ino)!=(after.st_dev,after.st_ino) or after.st_size>2097152: raise SystemExit(44)
  h=hashlib.sha256(); total=0
  while True:
   block=os.read(fd,min(65536,2097153-total))
   if not block: break
   total+=len(block)
   if total>2097152: raise SystemExit(44)
   h.update(block)
  if h.hexdigest()!=digest: raise SystemExit(44)
 finally: os.close(fd)
os.execve('/usr/local/sbin/s22-reboot',['/usr/local/sbin/s22-reboot','recovery'],{{'PATH':'/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin'}})
'''


def reboot_exec_command(expected_boot_id: str) -> str:
    return "python3 -I -B -c " + shlex.quote(render_reboot_prelude(expected_boot_id))


def validate_helper_snapshot(value: dict) -> None:
    _require(isinstance(value, dict) and value.get("schema") == "camera-reboot-helpers/v1",
             "installed recovery helper snapshot schema is unavailable")
    helpers = value.get("helpers")
    _require(isinstance(helpers, dict), "installed recovery helper inventory is unavailable")
    for key, expected in (("reboot", S22_REBOOT_SHA256), ("restart2", S22_RESTART2_SHA256)):
        row = helpers.get(key)
        _require(isinstance(row, dict) and row.get("sha256") == expected and
                 row.get("uid") == 0 and row.get("mode") == 0o755 and
                 row.get("regular") is True and row.get("symlink") is False,
                 f"installed {key} helper identity/permissions mismatch")


def validate_flash_guard_marker(profile_name: str, bundle: dict, *, guard_state_root=None,
                                 guard=GUARD) -> dict:
    """Require durable terminal proof for the exact camera flash receipt."""
    state_root = guard.STATE_ROOT if guard_state_root is None else Path(guard_state_root)
    trial_id = bundle["trial_identity"]
    marker, _ = _private_json(state_root / f"{trial_id}.json", max_bytes=65536)
    expected_kind = f"camera-recovery-{profile_name.removeprefix('camera-')}-flash"
    expected_path = str(bundle["directory"] /
                        f"{bundle['profile']['receipt_prefix']}-flash.json")
    _require(marker.get("trial_id") == trial_id and
             marker.get("operation_kind") == expected_kind and
             marker.get("status") == "complete" and marker.get("outcome") == "success" and
             marker.get("receipt_path") == expected_path and
             marker.get("receipt_sha256") == bundle["flash_receipt_sha256"],
             "camera flash guard marker does not prove this exact terminal receipt")
    return marker


def _native_power_preflight(state: dict) -> None:
    _require(isinstance(state, dict) and state.get("pid1") == "native-guardian" and
             state.get("native_ready") is True and AUDIO.ready_value(state.get("persistent_ready")),
             "usable native shell/persistent state is unavailable")
    _require(AUDIO.target_identity_valid(state), "device target identity is not the expected r0s")
    _require(AUDIO.power_state_valid(state.get("power_state")),
             "battery/thermal power safety is not established")


def _identity_for_remote(remote, timeout: int = 90) -> dict:
    result = remote(DEPLOY.camera_identity_command(), timeout)
    if result.returncode != 0:
        raise ReadOnlyTransportGap("read-only RECOVERY identity query unavailable")
    try:
        value = json.loads(result.stdout)
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise CameraObserverError("camera identity capture returned invalid JSON") from error
    _require(isinstance(value, dict), "camera identity capture is not an object")
    return value


def _helpers_for_remote(remote) -> dict:
    result = remote("python3 -I -B -c " + shlex.quote(HELPER_SNAPSHOT), 15)
    if result.returncode != 0:
        raise ReadOnlyTransportGap("read-only reboot-helper query unavailable")
    try:
        return json.loads(result.stdout)
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise CameraObserverError("reboot-helper capture returned invalid JSON") from error


def _default_remote(command: str, timeout: int):
    return AUDIO.run_trusted_remote("usb", None, command, timeout=timeout,
                                    project_root=DEPLOY.ARTIFACT_ROOT)


def _default_snapshotter():
    return AUDIO.snapshot_over("usb", project_root=DEPLOY.ARTIFACT_ROOT)


def _request_local_paths(profile_name: str, local_root: Path = OBSERVER_ROOT):
    identity = S22_REBOOT_TRIAL_IDS[profile_name]
    directory = Path(local_root) / identity
    return directory, directory / "request-started.json", directory / "request-result.json"


def request_recovery_once(profile_name: str, owner_ack: str, *,
                          receipts_root: Path = RECEIPTS_ROOT,
                          local_root: Path = OBSERVER_ROOT,
                          guard=GUARD, guard_state_root=None,
                          snapshotter=None, remote=None,
                          identity_reader=None, helper_reader=None) -> dict:
    """Request exactly once; any uncertain remote result is durable UNKNOWN."""
    profile = _profile(profile_name)
    _require(owner_ack == OWNER_ACK[profile_name],
             "request requires the exact direction-specific owner acknowledgement")
    bundle = validate_flash_bundle(profile_name, receipts_root=receipts_root)
    guard_state_root = GUARD.STATE_ROOT if guard_state_root is None else Path(guard_state_root)
    validate_flash_guard_marker(profile_name, bundle,
                                guard_state_root=guard_state_root, guard=guard)
    try:
        DEPLOY.validate_artifacts(profile_name, project_root=ROOT,
                                  artifact_root=DEPLOY.ARTIFACT_ROOT, shared=SHARED)
    except (OSError, TypeError, ValueError, RuntimeError) as error:
        raise CameraObserverError(
            "pinned host candidate/rollback artifacts failed current verification"
        ) from error
    directory, attempt_path, result_path = _request_local_paths(profile_name, local_root)
    AUDIO.ensure_private_directory(directory)
    for path in (attempt_path, result_path):
        _require(not path.exists() and not path.is_symlink(),
                 "camera reboot request identity already has local evidence; never reuse it")
    snapshotter = snapshotter or _default_snapshotter
    remote = remote or _default_remote
    identity_reader = identity_reader or (lambda: _identity_for_remote(remote))
    helper_reader = helper_reader or (lambda: _helpers_for_remote(remote))
    trial_id = S22_REBOOT_TRIAL_IDS[profile_name]
    op_kind = f"camera-recovery-{profile_name.removeprefix('camera-')}-reboot"

    with guard.acquire_operation_lock(ROOT, trial_id, op_kind,
                                      state_root=guard_state_root) as operation:
        state = snapshotter()
        current = identity_reader()
        helpers = helper_reader()
        _native_power_preflight(state)
        if profile_name == "camera-forward":
            try:
                _validate_forward_health(state)
            except (CameraObserverError, RuntimeError, ValueError) as error:
                raise CameraObserverError(
                    "forward reboot requires full native readiness, model idleness, and resolved liveness"
                ) from error
        _require(state.get("boot_id") == bundle["prewrite_identity"].get("boot_id") ==
                 current.get("boot_id"), "current boot ID differs from bound pre-write boot")
        _require(state.get("gnu_build_id") == RUNNING_KERNEL_BUILD_ID and
                 current.get("kernel_gnu_build_id") == RUNNING_KERNEL_BUILD_ID,
                 "running kernel GNU build ID is not the pinned recovery kernel")
        try:
            DEPLOY.validate_camera_identity(
                current, expected_recovery_sha=profile["new_sha256"],
                require_baseline_camera_module=(profile_name == "camera-forward"))
        except ValueError as error:
            raise CameraObserverError(f"current RECOVERY identity failed: {error}") from error
        validate_helper_snapshot(helpers)

        operation.begin(project_root=ROOT)
        started = {
            "schema": "camera-reboot-request/v1", "profile": profile_name,
            "trial_identity": trial_id, "flash_trial_identity": bundle["trial_identity"],
            "flash_receipt_sha256": bundle["flash_receipt_sha256"],
            "prewrite_boot_id": bundle["prewrite_identity"]["boot_id"],
            "prewrite_recovery_sha256": profile["before_sha256"],
            "current_recovery_sha256": current["recovery_sha256"],
            "baseline_boot_reset_record": state.get("boot_reset_first_record"),
            "owner_acknowledgement_recorded": True,
            "owner_acknowledgement_is_security_evidence": False,
            "request_command": REQUEST_COMMAND_LABEL,
            "retry_allowed": False,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        AUDIO.durable_json(attempt_path, started)
        try:
            result = remote(reboot_exec_command(current["boot_id"]), 15)
            outcome = "ACKNOWLEDGED" if result.returncode == 0 else "UNKNOWN"
            request_result = {
                "schema": "camera-reboot-request-result/v1", "profile": profile_name,
                "trial_identity": trial_id, "outcome": outcome,
                "returncode": result.returncode, "retry_allowed": False,
            }
        except Exception as error:
            outcome = "UNKNOWN"
            request_result = {
                "schema": "camera-reboot-request-result/v1", "profile": profile_name,
                "trial_identity": trial_id, "outcome": outcome,
                "error_type": type(error).__name__, "retry_allowed": False,
            }
        AUDIO.durable_json(result_path, request_result)
        # Even an acknowledged command is not proof that a changed boot and
        # RECOVERY image were observed. Keep the global operation unresolved;
        # only explicit evidence-based coordinator reconciliation may close it.
        return request_result


def _validate_forward_health(state: dict) -> None:
    AUDIO.validate_snapshot(state, post_reboot=True)
    logs = state.get("kernel_log_classification")
    _require(isinstance(logs, dict) and logs.get("liveness_unresolved") is False,
             "forward acceptance requires resolved kernel liveness")


def _read_request_context(profile_name: str, *, local_root: Path, guard=GUARD,
                          guard_state_root=None) -> tuple[dict, dict]:
    trial_id = S22_REBOOT_TRIAL_IDS[profile_name]
    _, attempt_path, result_path = _request_local_paths(profile_name, local_root)
    attempt, _ = _private_json(attempt_path)
    _require(attempt.get("schema") == "camera-reboot-request/v1" and
             attempt.get("profile") == profile_name and attempt.get("trial_identity") == trial_id and
             attempt.get("request_command") == REQUEST_COMMAND_LABEL and
             attempt.get("retry_allowed") is False,
             "no valid durable one-shot request record exists")
    _require(attempt.get("prewrite_boot_id") and
             attempt.get("flash_trial_identity") == DEPLOY.TRIAL_IDENTITIES[(profile_name, "flash")],
             "request record is not linked to this flash/profile")
    if result_path.exists():
        request_result, _ = _private_json(result_path)
        _require(request_result.get("schema") == "camera-reboot-request-result/v1" and
                 request_result.get("trial_identity") == trial_id and
                 request_result.get("profile") == profile_name and
                 request_result.get("outcome") in ("ACKNOWLEDGED", "UNKNOWN") and
                 request_result.get("retry_allowed") is False,
                 "request result identity is invalid")
    else:
        request_result = {"outcome": "UNKNOWN", "status": "no durable return receipt"}
    guard_state_root = guard.STATE_ROOT if guard_state_root is None else Path(guard_state_root)
    marker_path = guard_state_root / f"{trial_id}.json"
    marker, _ = _private_json(marker_path, max_bytes=65536)
    _require(marker.get("trial_id") == trial_id and marker.get("operation_kind") ==
             f"camera-recovery-{profile_name.removeprefix('camera-')}-reboot" and
             marker.get("status") in ("pending", "unknown", "complete", "reconciled"),
             "global one-shot guard marker is missing or mismatched")
    return attempt, request_result


def _refuse_prior_observation_receipt(profile_name: str, local_root: Path) -> None:
    """Do not let a fresh ring-buffer window replace this one-shot result."""
    trial_id = S22_REBOOT_TRIAL_IDS[profile_name]
    root_dir = Path(local_root)
    trial_dir = root_dir / trial_id
    for directory in (root_dir, trial_dir):
        try:
            directory_info = directory.lstat()
        except FileNotFoundError:
            return
        except OSError as error:
            raise CameraObserverError("prior observation receipt path is unreadable") from error
        _require(stat.S_ISDIR(directory_info.st_mode)
                 and directory_info.st_uid == os.geteuid()
                 and stat.S_IMODE(directory_info.st_mode) & 0o077 == 0,
                 "prior observation receipt path is not owner-private")
    observation_dir = trial_dir / "observations"
    try:
        info = observation_dir.lstat()
    except FileNotFoundError:
        return
    except OSError as error:
        raise CameraObserverError("prior observation receipt directory is unreadable") from error
    _require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid() and
             stat.S_IMODE(info.st_mode) == 0o700,
             "prior observation receipt directory is not owner-private")
    try:
        entries = sorted(observation_dir.iterdir(), key=lambda path: path.name)
    except OSError as error:
        raise CameraObserverError("prior observation receipt directory cannot be listed") from error
    _require(len(entries) <= 256,
             "too many prior observation entries; refusing a fresh observation window")
    if not entries:
        return

    expected_flash_id = DEPLOY.TRIAL_IDENTITIES[(profile_name, "flash")]
    expected_hash = _profile(profile_name)["new_sha256"]
    allowed_statuses = ({"accepted", "not_accepted"} if profile_name == "camera-forward"
                        else {"restore_unverified", "image_restored_health_unaccepted",
                              "image_restored_health_accepted"})
    saw_complete = False
    for path in entries:
        _require(re.fullmatch(r"observe-[0-9]{1,32}\.json", path.name) is not None,
                 "unexpected prior observation entry; refusing a fresh observation window")
        value, _ = _private_json(path)
        valid_identity = (
            value.get("schema") == "camera-recovery-reboot-observer/v1"
            and value.get("profile") == profile_name
            and value.get("trial_identity") == trial_id
            and value.get("flash_trial_identity") == expected_flash_id
            and isinstance(value.get("flash_receipt_sha256"), str)
            and _SHA256_HEX.fullmatch(value["flash_receipt_sha256"])
            and value.get("expected_recovery_sha256") == expected_hash
            and value.get("request_outcome") in ("ACKNOWLEDGED", "UNKNOWN")
            and value.get("retry_allowed") is False
            and value.get("transport_scope") == "usb_only"
        )
        _require(valid_identity,
                 "prior observation receipt identity/schema is malformed; refusing a fresh window")
        status = value.get("status")
        sample_count = value.get("sample_count")
        duration = value.get("observation_bound_seconds")
        elapsed = value.get("observed_seconds")
        _require(isinstance(status, str) and status in allowed_statuses
                 and type(sample_count) is int and sample_count >= 0
                 and type(duration) is int and 0 < duration <= OBSERVATION_SECONDS
                 and type(elapsed) in (int, float) and math.isfinite(elapsed) and elapsed >= 0
                 and type(value.get("observation_within_bound")) is bool
                 and type(value.get("full_health_accepted")) is bool
                 and type(value.get("recovery_image_restored")) is bool,
                 "prior observation receipt is incomplete or malformed; refusing a fresh window")
        if profile_name == "camera-forward":
            _require((status == "accepted") == value["full_health_accepted"],
                     "prior observation receipt has inconsistent acceptance fields")
        else:
            restored_full_health = value.get("restored_full_health")
            _require(type(restored_full_health) is bool,
                     "prior reverse observation receipt has invalid health field")
            expected_restore, expected_health = {
                "restore_unverified": (False, False),
                "image_restored_health_unaccepted": (True, False),
                "image_restored_health_accepted": (True, True),
            }[status]
            _require(value["recovery_image_restored"] is expected_restore and
                     restored_full_health is expected_health,
                     "prior reverse observation receipt has inconsistent health result")
        saw_complete = True
    _require(not saw_complete,
             "a complete observation receipt already exists for this one-shot ID; refusing a fresh window")


def _sample_camera_state(snapshotter, identity_reader, helper_reader):
    state = snapshotter()
    return state, identity_reader(), helper_reader()


def observe_only(profile_name: str, *, receipts_root: Path = RECEIPTS_ROOT,
                 local_root: Path = OBSERVER_ROOT, guard=GUARD,
                 guard_state_root=None, snapshotter=None, remote=None,
                 identity_reader=None, helper_reader=None,
                 observation_seconds: int = OBSERVATION_SECONDS,
                 sample_interval: int = SAMPLE_INTERVAL_SECONDS,
                 clock=time.monotonic, sleep=time.sleep) -> dict:
    """Resume by observing only; this function has no reboot command path."""
    _require(0 < observation_seconds <= OBSERVATION_SECONDS,
             "observation bound must be between 1 and 600 seconds")
    _require(sample_interval > 0, "sample interval must be positive")
    profile = _profile(profile_name)
    _refuse_prior_observation_receipt(profile_name, local_root)
    bundle = validate_flash_bundle(profile_name, receipts_root=receipts_root)
    attempt, request_result = _read_request_context(
        profile_name, local_root=local_root, guard=guard,
        guard_state_root=guard_state_root)
    _require(attempt["prewrite_boot_id"] == bundle["prewrite_identity"].get("boot_id"),
             "request record boot ID differs from boot-bound flash receipt")
    _require(attempt.get("flash_receipt_sha256") == bundle["flash_receipt_sha256"],
             "request record does not bind the exact validated flash receipt")
    started = clock()
    deadline = started + observation_seconds
    base_remote = remote or _default_remote

    def remaining_timeout(requested: int) -> int:
        remaining = deadline - clock()
        if remaining < 1:
            raise subprocess.TimeoutExpired("read-only observation", requested)
        return min(requested, int(remaining))

    def bounded_remote(command: str, timeout: int):
        return base_remote(command, remaining_timeout(timeout))

    if snapshotter is None:
        def bounded_runner(argv, **kwargs):
            kwargs["timeout"] = remaining_timeout(int(kwargs.get("timeout", 15)))
            return subprocess.run(argv, **kwargs)
        snapshotter = lambda: AUDIO.snapshot_over(
            "usb", runner=bounded_runner, project_root=DEPLOY.ARTIFACT_ROOT)
    else:
        injected_snapshotter = snapshotter

        def snapshotter():
            if clock() >= deadline:
                raise subprocess.TimeoutExpired("read-only snapshot", 0)
            return injected_snapshotter()

    remote = bounded_remote
    identity_reader = identity_reader or (lambda: _identity_for_remote(remote))
    helper_reader = helper_reader or (lambda: _helpers_for_remote(remote))
    baseline_boot = attempt["prewrite_boot_id"]
    baseline_bore = attempt.get("baseline_boot_reset_record")
    new_boot = None
    stable_start = None
    last_good = None
    samples = 0
    serious_fault_seen = False
    serious_status_unknown_seen = False
    unresolved_liveness_seen = False
    liveness_status_unknown_seen = False
    boot_changed_again = False
    new_bore_seen = False
    last_state = None
    latest_identity = None
    latest_helpers = None
    reverse_native_stable_start = None
    reverse_native_last_good = None
    transport_gaps = 0
    identity_transport_gaps = 0
    identity_errors = []
    identity_failure_seen = False
    helper_mismatch_seen = False
    module_mismatch_seen = False
    module_pending = False
    identity_valid_now = False
    identity_capture_count = 0
    initial_identity_capture_attempted = False
    identity_retry_pending = False
    last_identity_capture_elapsed = None
    # Reserve the maximum reviewed identity+helper query time and one bounded
    # final snapshot, keeping every remote call inside the 600-second bound.
    final_capture_reserve = min(120, max(1, observation_seconds - MIN_STABLE_SECONDS))
    sampling_deadline = deadline - final_capture_reserve

    def clear_stability() -> None:
        nonlocal stable_start, last_good
        nonlocal reverse_native_stable_start, reverse_native_last_good
        stable_start = last_good = None
        reverse_native_stable_start = reverse_native_last_good = None

    def capture_postboot_identity() -> bool:
        nonlocal latest_identity, latest_helpers, identity_transport_gaps
        nonlocal identity_failure_seen, helper_mismatch_seen, module_mismatch_seen
        nonlocal identity_valid_now, identity_capture_count, module_pending
        nonlocal identity_retry_pending, last_identity_capture_elapsed
        if clock() >= deadline:
            identity_valid_now = False
            return False
        identity_capture_count += 1
        last_identity_capture_elapsed = clock() - started
        identity_retry_pending = False
        try:
            state_id = identity_reader()
            if clock() >= deadline:
                identity_transport_gaps += 1
                identity_valid_now = False
                clear_stability()
                return False
            helper_state = helper_reader()
            if clock() >= deadline:
                identity_transport_gaps += 1
                identity_valid_now = False
                clear_stability()
                return False
        except (ReadOnlyTransportGap, subprocess.TimeoutExpired, OSError):
            identity_transport_gaps += 1
            identity_retry_pending = True
            identity_valid_now = False
            clear_stability()
            return False
        try:
            DEPLOY.validate_camera_identity(
                state_id, expected_recovery_sha=profile["new_sha256"],
                require_baseline_camera_module=False)
            _require(state_id.get("boot_id") == new_boot,
                     "camera identity boot ID changed during observation")
            _require(state_id.get("kernel_gnu_build_id") == RUNNING_KERNEL_BUILD_ID,
                     "camera identity kernel GNU build ID changed")
        except Exception as error:
            identity_failure_seen = True
            identity_errors.append(type(error).__name__)
            identity_valid_now = False
            clear_stability()
            return False
        try:
            validate_helper_snapshot(helper_state)
        except Exception as error:
            helper_mismatch_seen = True
            identity_errors.append(type(error).__name__)
            identity_valid_now = False
            clear_stability()
            return False
        latest_identity, latest_helpers = state_id, helper_state
        actual_loaded = state_id.get("camera_module_loaded") is True
        actual_build = state_id.get("camera_module_gnu_build_id") if actual_loaded else None
        if actual_loaded and actual_build != profile["write"]["module_build_id"]:
            module_mismatch_seen = True
        module_pending = profile_name == "camera-forward" and not actual_loaded
        identity_retry_pending = module_pending
        identity_valid_now = not (profile_name == "camera-forward" and
                                  (module_pending or module_mismatch_seen))
        return True

    while clock() < sampling_deadline:
        samples += 1
        sample_started = clock()
        try:
            state = snapshotter()
        except Exception:
            state = None
        if not isinstance(state, dict):
            # A lost USB snapshot is a transport gap, not evidence that the
            # remote kernel reported a fault. It breaks continuity but does
            # not become a sticky diagnostic unknown.
            transport_gaps += 1
            clear_stability()
        else:
            last_state = state
            boot_id = state.get("boot_id")
            if not isinstance(boot_id, str) or not boot_id:
                serious_status_unknown_seen = True
            elif boot_id != baseline_boot:
                if new_boot is None:
                    new_boot = boot_id
                elif boot_id != new_boot:
                    boot_changed_again = True
            elapsed_before_identity = clock() - started
            retry_due = (identity_retry_pending and last_identity_capture_elapsed is not None and
                         elapsed_before_identity - last_identity_capture_elapsed >= 60)
            if (new_boot is not None and boot_id == new_boot and
                    (not initial_identity_capture_attempted or retry_due) and
                    not identity_failure_seen and not helper_mismatch_seen and
                    not module_mismatch_seen):
                initial_identity_capture_attempted = True
                capture_postboot_identity()
            logs = state.get("kernel_log_classification")
            if state.get("serious_fault") is True:
                serious_fault_seen = True
            elif state.get("serious_fault") is not False:
                serious_status_unknown_seen = True
            if isinstance(logs, dict) and type(logs.get("liveness_unresolved")) is bool:
                if logs["liveness_unresolved"]:
                    unresolved_liveness_seen = True
            else:
                liveness_status_unknown_seen = True

            new_bore = (AUDIO.recovery_record(state.get("boot_reset_first_record")) and
                        state.get("boot_reset_first_record") != baseline_bore)
            new_bore_seen = new_bore_seen or new_bore
            try:
                _native_power_preflight(state)
                native_ok = (boot_id == new_boot and new_bore and not boot_changed_again)
            except CameraObserverError:
                native_ok = False

            elapsed = clock() - started
            if profile_name == "camera-forward":
                full_ok = False
                if native_ok and identity_valid_now:
                    try:
                        _validate_forward_health(state)
                        full_ok = True
                    except (CameraObserverError, RuntimeError, ValueError):
                        full_ok = False
                if not full_ok or serious_fault_seen or serious_status_unknown_seen or \
                        unresolved_liveness_seen or liveness_status_unknown_seen:
                    stable_start = last_good = None
                else:
                    if last_good is None or elapsed - last_good > sample_interval * 2:
                        stable_start = elapsed
                    last_good = elapsed
            else:
                # Reverse restoration does not require a healthy candidate
                # camera/Desktop; expose full health separately below.
                if not native_ok or not identity_valid_now or serious_fault_seen or serious_status_unknown_seen or \
                        unresolved_liveness_seen or liveness_status_unknown_seen:
                    reverse_native_stable_start = reverse_native_last_good = None
                else:
                    if (reverse_native_last_good is None or
                            elapsed - reverse_native_last_good > sample_interval * 2):
                        reverse_native_stable_start = elapsed
                    reverse_native_last_good = elapsed
        remaining = sampling_deadline - clock()
        if remaining > 0:
            sleep(min(sample_interval, remaining))

    if new_boot is not None and not boot_changed_again:
        capture_postboot_identity()
    # A fresh end-of-window snapshot is reserved after both identity reads.
    # Acceptance uses this current health/native state but does not count the
    # unobserved query tail as part of the stable interval.
    final_native_ok = False
    final_state_ok = False
    final_bore_ok = False
    final_snapshot_skipped_deadline = clock() >= deadline
    if not final_snapshot_skipped_deadline:
        samples += 1
        try:
            final_state = snapshotter()
        except Exception:
            final_state = None
    else:
        final_state = None
    if isinstance(final_state, dict):
        last_state = final_state
        final_boot = final_state.get("boot_id")
        if isinstance(final_boot, str) and final_boot and final_boot != baseline_boot:
            if new_boot is None:
                new_boot = final_boot
            elif final_boot != new_boot:
                boot_changed_again = True
        elif not isinstance(final_boot, str) or not final_boot:
            serious_status_unknown_seen = True
        if final_state.get("serious_fault") is True:
            serious_fault_seen = True
        elif final_state.get("serious_fault") is not False:
            serious_status_unknown_seen = True
        final_logs = final_state.get("kernel_log_classification")
        if isinstance(final_logs, dict) and type(final_logs.get("liveness_unresolved")) is bool:
            if final_logs["liveness_unresolved"]:
                unresolved_liveness_seen = True
        else:
            liveness_status_unknown_seen = True
        new_bore_seen = new_bore_seen or (
            AUDIO.recovery_record(final_state.get("boot_reset_first_record")) and
            final_state.get("boot_reset_first_record") != baseline_bore)
        final_bore_ok = (
            AUDIO.recovery_record(final_state.get("boot_reset_first_record")) and
            final_state.get("boot_reset_first_record") != baseline_bore)
        try:
            _native_power_preflight(final_state)
            final_native_ok = (final_boot == new_boot and new_boot != baseline_boot and
                               final_bore_ok and not boot_changed_again)
            final_state_ok = (final_native_ok and not serious_fault_seen and
                              not serious_status_unknown_seen and
                              not unresolved_liveness_seen and not liveness_status_unknown_seen)
            if profile_name == "camera-forward":
                _validate_forward_health(final_state)
            elif profile_name == "camera-reverse":
                _validate_forward_health(final_state)
        except (CameraObserverError, RuntimeError, ValueError):
            final_state_ok = False
    else:
        if not final_snapshot_skipped_deadline:
            transport_gaps += 1
        clear_stability()
    observed = max(0.0, clock() - started)
    observation_within_bound = observed <= observation_seconds

    actual_module = None
    if isinstance(latest_identity, dict) and latest_identity.get("camera_module_loaded") is True:
        actual_module = latest_identity.get("camera_module_gnu_build_id")
    expected_module = profile["write"]["module_build_id"]
    module_matches = actual_module == expected_module and not module_mismatch_seen
    expected_recovery = profile["new_sha256"]
    actual_recovery = (latest_identity.get("recovery_sha256")
                       if isinstance(latest_identity, dict) else None)
    new_identity_ok = bool(latest_identity and not identity_failure_seen and
                           latest_identity.get("boot_id") == new_boot and
                           latest_identity.get("boot_id") != baseline_boot and
                           latest_identity.get("kernel_gnu_build_id") == RUNNING_KERNEL_BUILD_ID and
                           actual_recovery == expected_recovery)
    helper_ok = bool(latest_helpers) and not helper_mismatch_seen
    if helper_ok:
        try:
            validate_helper_snapshot(latest_helpers)
        except CameraObserverError:
            helper_ok = False
    bore_ok = final_bore_ok
    if profile_name == "camera-forward":
        stable_seconds = (max(0.0, last_good - stable_start)
                          if stable_start is not None and last_good is not None and
                          sampling_deadline - started - last_good <= sample_interval * 2 else 0.0)
        full_health = bool(observation_within_bound and final_state_ok and
                           stable_seconds >= MIN_STABLE_SECONDS and last_state and
                           not serious_fault_seen and not serious_status_unknown_seen and
                           not unresolved_liveness_seen and not liveness_status_unknown_seen and
                           new_identity_ok and not identity_errors and module_matches and
                           helper_ok and bore_ok and
                           not boot_changed_again)
        restored = False
        native_stable_seconds = 0.0
    else:
        native_stable_seconds = (
            max(0.0, reverse_native_last_good - reverse_native_stable_start)
            if reverse_native_stable_start is not None and reverse_native_last_good is not None and
            sampling_deadline - started - reverse_native_last_good <= sample_interval * 2
            else 0.0
        )
        restored = bool(observation_within_bound and final_native_ok and
                        native_stable_seconds >= MIN_STABLE_SECONDS and new_identity_ok and
                        helper_ok and bore_ok and not boot_changed_again and
                        not identity_errors and
                        not serious_fault_seen and not serious_status_unknown_seen and
                        not unresolved_liveness_seen and not liveness_status_unknown_seen)
        try:
            if last_state:
                _validate_forward_health(last_state)
                full_health = bool(observation_within_bound and final_state_ok and
                                   native_stable_seconds >= MIN_STABLE_SECONDS and
                                   module_matches and new_identity_ok and not identity_errors and
                                   helper_ok and not serious_fault_seen and
                                   not serious_status_unknown_seen and
                                   not unresolved_liveness_seen and
                                   not liveness_status_unknown_seen and bore_ok and
                                   not boot_changed_again)
            else:
                full_health = False
        except (CameraObserverError, RuntimeError, ValueError):
            full_health = False
        stable_seconds = native_stable_seconds

    result = {
        "schema": "camera-recovery-reboot-observer/v1",
        "status": (("accepted" if full_health else "not_accepted")
                   if profile_name == "camera-forward" else
                   (("image_restored_health_accepted" if full_health else
                     "image_restored_health_unaccepted") if restored else "restore_unverified")),
        "profile": profile_name,
        "trial_identity": S22_REBOOT_TRIAL_IDS[profile_name],
        "flash_trial_identity": bundle["trial_identity"],
        "flash_receipt_sha256": bundle["flash_receipt_sha256"],
        "request_outcome": request_result.get("outcome", "UNKNOWN"),
        "request_returncode": request_result.get("returncode"),
        "baseline_boot_id": baseline_boot,
        "observed_boot_id": new_boot,
        "transport_scope": "usb_only",
        "transport_gap_count": transport_gaps,
        "identity_transport_gap_count": identity_transport_gaps,
        "boot_changed_again": boot_changed_again,
        "actual_bore_recovery_new_record": bore_ok,
        "new_bore_seen_during_observation": new_bore_seen,
        "expected_recovery_sha256": expected_recovery,
        "observed_recovery_sha256": actual_recovery,
        "expected_kernel_gnu_build_id": RUNNING_KERNEL_BUILD_ID,
        "observed_kernel_gnu_build_id": (latest_identity.get("kernel_gnu_build_id")
                                           if isinstance(latest_identity, dict) else None),
        "expected_camera_module_gnu_build_id": expected_module,
        "observed_camera_module_loaded": (latest_identity.get("camera_module_loaded")
                                          if isinstance(latest_identity, dict) else None),
        "observed_camera_module_gnu_build_id": actual_module,
        "camera_module_matches_expected": module_matches,
        "reboot_helper_identity_verified": helper_ok,
        "reboot_helper_expected_sha256": {"s22-reboot": S22_REBOOT_SHA256,
                                          "s22-restart2": S22_RESTART2_SHA256},
        "reboot_helper_observed": (latest_helpers.get("helpers")
                                   if isinstance(latest_helpers, dict) else None),
        "serious_fault_seen": serious_fault_seen,
        "serious_fault_status_unknown_seen": serious_status_unknown_seen,
        "unresolved_liveness_seen": unresolved_liveness_seen,
        "liveness_status_unknown_seen": liveness_status_unknown_seen,
        "continuous_stable_seconds": round(stable_seconds, 2),
        "native_restore_stable_seconds": round(native_stable_seconds, 2),
        "observed_seconds": round(observed, 2),
        "observation_bound_seconds": observation_seconds,
        "observation_within_bound": observation_within_bound,
        "sample_count": samples,
        "identity_errors": identity_errors,
        "identity_mismatch_seen": identity_failure_seen,
        "helper_mismatch_seen": helper_mismatch_seen,
        "camera_module_mismatch_seen": module_mismatch_seen,
        "camera_module_pending": module_pending,
        "identity_capture_count": identity_capture_count,
        "recovery_image_restored": restored if profile_name == "camera-reverse" else False,
        "full_health_accepted": full_health if profile_name == "camera-forward" else False,
        "restored_full_health": full_health if profile_name == "camera-reverse" else None,
        "retry_allowed": False,
    }
    output_dir = Path(local_root) / S22_REBOOT_TRIAL_IDS[profile_name] / "observations"
    AUDIO.ensure_private_directory(output_dir)
    output_path = output_dir / f"observe-{time.time_ns()}.json"
    AUDIO.durable_json(output_path, result)
    result["receipt_path"] = str(output_path)
    # Deliberately do not call device-trial-guard reconciliation or modify the
    # one-shot request marker. Root may use this receipt for explicit review.
    return result


def host_plan(profile_name: str, *, receipts_root: Path = RECEIPTS_ROOT) -> dict:
    profile = _profile(profile_name)
    bundle_ok = False
    error_type = None
    try:
        bundle = validate_flash_bundle(profile_name, receipts_root=receipts_root)
        bundle_ok = True
    except (CameraObserverError, OSError, ValueError) as error:
        bundle = None
        error_type = type(error).__name__
    return {
        "schema": "camera-recovery-reboot-plan/v1", "profile": profile_name,
        "trial_identity": S22_REBOOT_TRIAL_IDS[profile_name],
        "flash_trial_identity": DEPLOY.TRIAL_IDENTITIES[(profile_name, "flash")],
        "expected_current_recovery_sha256": profile["new_sha256"],
        "expected_postboot_module_gnu_build_id": profile["write"]["module_build_id"],
        "bound_receipt_bundle_valid": bundle_ok,
        "receipt_validation_error_type": error_type,
        "host_only_plan": True, "phone_accessed": False,
        "request_authorized": False, "independent_rescue_proven": False,
        "camera_unattended_acceptance_proven": False,
        "one_shot_acknowledgement_required": True,
        "observation_bound_seconds": OBSERVATION_SECONDS,
        "retry_allowed": False,
        "bound_flash_receipt_sha256": bundle["flash_receipt_sha256"] if bundle else None,
    }


STDOUT_SUMMARY_FIELDS = (
    "schema", "status", "profile", "trial_identity", "flash_trial_identity",
    "flash_receipt_sha256", "request_outcome", "request_returncode", "returncode", "retry_allowed",
    "bound_receipt_bundle_valid", "receipt_validation_error_type", "host_only_plan",
    "phone_accessed", "request_authorized", "independent_rescue_proven",
    "camera_unattended_acceptance_proven", "one_shot_acknowledgement_required",
    "observation_bound_seconds", "observation_within_bound", "transport_scope", "transport_gap_count",
    "identity_transport_gap_count", "actual_bore_recovery_new_record",
    "expected_recovery_sha256", "observed_recovery_sha256",
    "expected_kernel_gnu_build_id", "observed_kernel_gnu_build_id",
    "expected_camera_module_gnu_build_id", "observed_camera_module_loaded",
    "observed_camera_module_gnu_build_id", "camera_module_matches_expected",
    "reboot_helper_identity_verified", "reboot_helper_expected_sha256",
    "reboot_helper_observed", "serious_fault_seen", "serious_fault_status_unknown_seen",
    "unresolved_liveness_seen", "liveness_status_unknown_seen",
    "continuous_stable_seconds", "native_restore_stable_seconds", "observed_seconds",
    "sample_count", "identity_mismatch_seen", "helper_mismatch_seen",
    "camera_module_mismatch_seen", "camera_module_pending", "identity_capture_count",
    "new_bore_seen_during_observation", "recovery_image_restored",
    "full_health_accepted", "restored_full_health",
)


def stdout_summary(result: dict) -> dict:
    """Expose only the small approved summary; raw IDs/records stay private."""
    return {key: result[key] for key in STDOUT_SUMMARY_FIELDS if key in result}


def observer_exit_code(result: dict, *, action_requested: bool) -> int:
    if not action_requested:
        return 0
    return 0 if result.get("status") in ("accepted", "image_restored_health_accepted") else 3


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=tuple(S22_REBOOT_TRIAL_IDS), default="camera-forward")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--request-recovery", action="store_true",
                        help="request the single direction-specific native RECOVERY reboot")
    action.add_argument("--observe-only", action="store_true",
                        help="resume bounded read-only observation; never requests reboot")
    parser.add_argument("--owner-acknowledgement",
                        help="exact direction-specific operator assertion; not security evidence")
    args = parser.parse_args(argv)
    try:
        action_requested = bool(args.request_recovery or args.observe_only)
        if args.request_recovery:
            if args.owner_acknowledgement is None:
                parser.error("--request-recovery requires --owner-acknowledgement")
            # Keep request and bounded observer in this process even when the
            # reboot command disconnects/returns UNKNOWN. This never retries.
            request_recovery_once(args.profile, args.owner_acknowledgement)
            result = observe_only(args.profile)
        elif args.observe_only:
            if args.owner_acknowledgement is not None:
                parser.error("--owner-acknowledgement is only valid with --request-recovery")
            result = observe_only(args.profile)
        else:
            if args.owner_acknowledgement is not None:
                parser.error("owner acknowledgement cannot be supplied to a host-only plan")
            result = host_plan(args.profile)
    except (CameraObserverError, AUDIO.ObserverError, OSError, RuntimeError, ValueError) as error:
        print(f"camera-recovery-reboot-once: {type(error).__name__}: {error}", file=sys.stderr)
        return 2
    print(json.dumps(stdout_summary(result), sort_keys=True, indent=2))
    return observer_exit_code(result, action_requested=action_requested)


if __name__ == "__main__":
    raise SystemExit(main())
