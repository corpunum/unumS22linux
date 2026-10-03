#!/usr/bin/env python3
"""Validate and render, but never execute, the pinned audio RECOVERY profiles.

This host-only tool has no stage, flash, SSH, reboot, or marker-creation path.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys

ROOT = Path(__file__).resolve().parents[2]
SHARED_DEPLOYER = ROOT / "tools/hardware/deploy-audio-recovery.py"
SHARED_DEPLOYER_SHA256 = "d0c54cc306e3a8a80cb79d79958fceedf6bb5945dabc7606e02b12d27dc00fd3"

PARTITION_SIZE = 100663296
TRIAL_IDENTITY = "audio-coherent-20261003-first"
CANDIDATE_IMAGE = "builds/audio-coherent-recovery-stripped-host-20261003/recovery.img"
CANDIDATE_MANIFEST = "builds/audio-coherent-recovery-stripped-host-20261003/manifest.json"
BASELINE_IMAGE = "builds/camera-module-recovery-20260927/recovery.img"
LINEAGE_IMAGE = "lineage/build-20260915/recovery.img"

CANDIDATE_SHA256 = "6b788b23f54b9b8e84212187544064949b72af20c167cbb02392cbe53ee5a6ab"
CANDIDATE_MANIFEST_SHA256 = "f78bbaddf94ef6d1ae37b059b7455b24a15cbb7c2fab1ec9bbc4e1df5fd1edf8"
BASELINE_SHA256 = "b10412715756da3cc8ee221368b49f179cc0c64ab7bd2802976480905e6d8d2f"
MAX_JSON_BYTES = 1024 * 1024

EXPECTED_MODULES = {
    "lib/modules/snd-soc-samsung-abox.ko": "61d846d2bb13d5ff48261f21efadcd8b378bdc586c28b3e288ddccf145488265",
    "lib/modules/rainbow_prince.ko": "b896200b333be6d518b9eb4b218abefe8c115a3162e5fb3b1a7016a6b7175c7a",
    "lib/modules/exynos-usb-audio-offloading.ko": "3b732ada44c0a6812b6aaeb38d7de8757ad54e7f843c6761b97ff4e5d63e8392",
}

PROFILES = {
    "audio-forward": {
        "before_role": "camera_recovery_baseline",
        "before_image": BASELINE_IMAGE,
        "before_sha256": BASELINE_SHA256,
        "target_role": "audio_candidate",
        "target_image": CANDIDATE_IMAGE,
        "target_sha256": CANDIDATE_SHA256,
        "staging_directory": "/srv/s22/audio-coherent-forward-20261003",
        "rollback_filename": "camera-baseline-rollback.img",
    },
    "audio-reverse": {
        "before_role": "audio_candidate",
        "before_image": CANDIDATE_IMAGE,
        "before_sha256": CANDIDATE_SHA256,
        "target_role": "camera_recovery_baseline",
        "target_image": BASELINE_IMAGE,
        "target_sha256": BASELINE_SHA256,
        "staging_directory": "/srv/s22/audio-coherent-reverse-20261003",
        "rollback_filename": "audio-candidate-rollback.img",
    },
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _unique_json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON field: {key}")
        result[key] = value
    return result


def _load_shared_deployer():
    try:
        helper_bytes = SHARED_DEPLOYER.read_bytes()
    except OSError as error:
        raise RuntimeError(f"shared RECOVERY helper unavailable: {error}") from error
    actual = sha256(helper_bytes)
    if actual != SHARED_DEPLOYER_SHA256:
        raise RuntimeError(
            "shared RECOVERY helper changed; independent review is required: "
            f"expected {SHARED_DEPLOYER_SHA256}, got {actual}"
        )
    spec = importlib.util.spec_from_file_location("s22_audio_profile_shared_deployer", SHARED_DEPLOYER)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot import pinned shared RECOVERY helper")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


DEPLOY = _load_shared_deployer()


def _read_bounded_regular_file(path: Path, label: str, maximum: int) -> bytes:
    path = Path(path)
    try:
        before = path.lstat()
    except OSError as error:
        raise ValueError(f"{label} is missing or unreadable: {error}") from error
    require(stat.S_ISREG(before.st_mode), f"{label} must be a non-symlink regular file")
    require(before.st_size <= maximum, f"{label} exceeds the {maximum}-byte input bound")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as error:
        raise ValueError(f"{label} could not be opened safely: {error}") from error
    try:
        opened = os.fstat(fd)
        require(stat.S_ISREG(opened.st_mode), f"{label} changed to a non-regular file")
        require((opened.st_dev, opened.st_ino) == (before.st_dev, before.st_ino),
                f"{label} changed while opening")
        require(opened.st_size <= maximum, f"{label} exceeds the {maximum}-byte input bound")
        result = bytearray()
        while len(result) <= maximum:
            chunk = os.read(fd, min(65536, maximum + 1 - len(result)))
            if not chunk:
                break
            result.extend(chunk)
        after = os.fstat(fd)
        require(len(result) <= maximum, f"{label} exceeds the {maximum}-byte input bound")
        require(len(result) == opened.st_size == after.st_size,
                f"{label} changed size while being read")
        require((opened.st_dev, opened.st_ino, opened.st_mtime_ns) ==
                (after.st_dev, after.st_ino, after.st_mtime_ns),
                f"{label} changed while being read")
        return bytes(result)
    finally:
        os.close(fd)


def _require_image_file(path: Path, label: str) -> None:
    try:
        metadata = Path(path).lstat()
    except OSError as error:
        raise ValueError(f"{label} is missing or unreadable: {error}") from error
    require(stat.S_ISREG(metadata.st_mode), f"{label} must be a non-symlink regular file")
    require(metadata.st_size == PARTITION_SIZE,
            f"{label} must be exactly {PARTITION_SIZE} bytes")


def resolve_profile(profile_name: str) -> dict[str, str]:
    if profile_name not in PROFILES:
        raise ValueError(f"unknown audio recovery profile: {profile_name}")
    return {
        "name": profile_name,
        "trial_identity": TRIAL_IDENTITY,
        **PROFILES[profile_name],
    }


def _validate_package_manifest(root: Path) -> dict[str, object]:
    manifest_path = Path(root) / CANDIDATE_MANIFEST
    data = _read_bounded_regular_file(manifest_path, "audio package manifest", MAX_JSON_BYTES)
    actual = sha256(data)
    require(actual == CANDIDATE_MANIFEST_SHA256,
            f"audio package manifest SHA-256 mismatch: {actual}")
    try:
        manifest = json.loads(data, object_pairs_hook=_unique_json_object)
    except (ValueError, UnicodeDecodeError) as error:
        raise ValueError(f"audio package manifest is invalid JSON: {error}") from error
    require(isinstance(manifest, dict), "audio package manifest must be an object")
    expected_scalars = {
        "schema": "s22-audio-coherent-recovery-package/v1",
        "candidate_image_bytes": PARTITION_SIZE,
        "candidate_image_sha256": CANDIDATE_SHA256,
        "partition_size_bytes": PARTITION_SIZE,
        "candidate_avb_footer_verified": True,
        "base_image_avb_footer_verified": True,
        "avb_algorithm": "NONE",
        "phone_access": False,
        "deployed": False,
        "host_only": True,
        "module_loaded": False,
        "boot_authorized": False,
    }
    for key, expected in expected_scalars.items():
        require(manifest.get(key) == expected,
                f"audio package manifest has incorrect {key}")
    base = manifest.get("base_image")
    require(isinstance(base, dict), "audio package manifest base_image is malformed")
    require(base.get("bytes") == PARTITION_SIZE and
            base.get("sha256") == BASELINE_SHA256,
            "audio package manifest baseline identity mismatch")
    base_path = base.get("path")
    require(isinstance(base_path, str) and
            base_path.replace("\\", "/").endswith("/" + BASELINE_IMAGE),
            "audio package manifest baseline path mismatch")
    replacements = manifest.get("replacements")
    require(isinstance(replacements, dict) and set(replacements) == set(EXPECTED_MODULES),
            "audio package manifest replacement membership mismatch")
    for module_path, expected_sha in EXPECTED_MODULES.items():
        record = replacements[module_path]
        require(isinstance(record, dict) and isinstance(record.get("candidate"), dict),
                f"audio package manifest candidate module record malformed: {module_path}")
        require(record["candidate"].get("sha256") == expected_sha,
                f"audio package manifest replacement mismatch: {module_path}")
    abi = manifest.get("static_abi")
    require(isinstance(abi, dict), "audio package manifest static_abi is malformed")
    ramdisk = abi.get("ramdisk")
    wlan = abi.get("selected_external_wlan")
    require(isinstance(ramdisk, dict) and ramdisk.get("module_count") == 324 and
            ramdisk.get("import_count") == 16569 and
            ramdisk.get("all_imports_complete_and_compatible") is True and
            ramdisk.get("missing_symbol_count") == 0 and
            ramdisk.get("crc_mismatch_count") == 0 and
            ramdisk.get("unknown_crc_count") == 0,
            "audio package manifest ramdisk static ABI evidence mismatch")
    require(isinstance(wlan, dict) and wlan.get("imports") == 495 and
            wlan.get("included_in_ramdisk") is False and
            wlan.get("all_imports_complete_and_compatible") is True,
            "audio package manifest selected WLAN static ABI evidence mismatch")
    return manifest


def validate_profile_artifacts(profile_name: str, *, artifact_root: Path = ROOT) -> dict[str, object]:
    profile = resolve_profile(profile_name)
    root = Path(artifact_root)
    package_manifest = _validate_package_manifest(root)
    before_path = root / profile["before_image"]
    target_path = root / profile["target_image"]
    lineage_path = root / LINEAGE_IMAGE
    _require_image_file(before_path, "profile before-image")
    _require_image_file(target_path, "profile target image")
    _require_image_file(lineage_path, "known-good lineage image")
    target_bytes = DEPLOY.validate_host_artifacts(
        target_path,
        before_path,
        lineage_path,
        size=PARTITION_SIZE,
        candidate_sha=profile["target_sha256"],
        base_sha=profile["before_sha256"],
        lineage_sha=DEPLOY.LINEAGE_SHA,
    )
    return {
        "profile": profile_name,
        "before_sha256": profile["before_sha256"],
        "target_sha256": profile["target_sha256"],
        "partition_size_bytes": PARTITION_SIZE,
        "candidate_package_manifest_sha256": CANDIDATE_MANIFEST_SHA256,
        "candidate_package_manifest_schema": package_manifest["schema"],
        "target_bytes_validated": len(target_bytes),
        "lineage_sha256_validated": DEPLOY.LINEAGE_SHA,
    }


def render_remote(profile_name: str) -> str:
    profile = resolve_profile(profile_name)
    return DEPLOY.render_remote(
        base_sha=profile["before_sha256"],
        new_sha=profile["target_sha256"],
        staging_directory=profile["staging_directory"],
        rollback_filename=profile["rollback_filename"],
    )


def validate_receipt(profile_name: str, receipt: object) -> dict[str, object]:
    profile = resolve_profile(profile_name)
    require(isinstance(receipt, dict), "operation receipt must be a JSON object")
    mode = receipt.get("mode")
    if mode == "stage":
        expected_keys = {
            "mode", "partition_written", "backup_sha256", "candidate_sha256",
            "profile", "trial_identity",
        }
    elif mode == "flash":
        expected_keys = {
            "mode", "partition_written", "bytes", "before_sha256",
            "readback_sha256", "reboot_performed", "profile", "trial_identity",
        }
    else:
        raise ValueError("operation receipt mode is unknown or ambiguous")
    require(set(receipt) == expected_keys,
            "operation receipt has missing or unrecognized fields; outcome is ambiguous")
    require(receipt.get("profile") == profile_name,
            "operation receipt profile identity mismatch")
    require(receipt.get("trial_identity") == TRIAL_IDENTITY,
            "operation receipt trial identity mismatch")
    DEPLOY.validate_remote_receipt(
        receipt,
        mode=mode,
        before_sha=profile["before_sha256"],
        candidate_sha=profile["target_sha256"],
        size=PARTITION_SIZE,
    )
    return {
        "profile": profile_name,
        "trial_identity": TRIAL_IDENTITY,
        "mode": mode,
        "receipt_validated": True,
        "partition_written": receipt["partition_written"],
        "before_sha256": profile["before_sha256"],
        "target_sha256": profile["target_sha256"],
        "reboot_performed": False,
    }


def read_receipt(path: Path) -> dict[str, object]:
    data = _read_bounded_regular_file(Path(path), "operation receipt", 65536)
    try:
        receipt = json.loads(data, object_pairs_hook=_unique_json_object)
    except (ValueError, UnicodeDecodeError) as error:
        raise ValueError(f"operation receipt is invalid JSON: {error}") from error
    require(isinstance(receipt, dict), "operation receipt must be a JSON object")
    return receipt


def build_plan(profile_name: str, *, artifact_root: Path = ROOT) -> dict[str, object]:
    profile = resolve_profile(profile_name)
    validation = validate_profile_artifacts(profile_name, artifact_root=artifact_root)
    remote = render_remote(profile_name)
    return {
        "schema": "s22-audio-coherent-recovery-profile/v1",
        "profile": profile_name,
        "reserved_future_trial_identity": TRIAL_IDENTITY,
        "partition": "recovery",
        "before_role": profile["before_role"],
        "before_image": profile["before_image"],
        "before_sha256": profile["before_sha256"],
        "target_role": profile["target_role"],
        "target_image": profile["target_image"],
        "target_sha256": profile["target_sha256"],
        "partition_size_bytes": PARTITION_SIZE,
        "staging_directory": profile["staging_directory"],
        "rollback_filename": profile["rollback_filename"],
        "rendered_remote_sha256": sha256(remote.encode("utf-8")),
        "artifact_validation": validation,
        "host_only": True,
        "execution": False,
        "stage_flash_cli_available": False,
        "current_deployment_authorized": False,
        "independent_hardware_rescue_demonstrated": False,
        "operation_marker_created": False,
        "partition_written": False,
        "reboot_performed": False,
        "bootability_or_audio_acceptance": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True, choices=tuple(PROFILES))
    parser.add_argument("--artifact-root", type=Path, default=ROOT,
                        help="read-only root containing the pinned host artifacts")
    parser.add_argument("--receipt", type=Path,
                        help="read-only local operation receipt to validate; does not execute")
    args = parser.parse_args(argv)
    try:
        result = build_plan(args.profile, artifact_root=args.artifact_root)
        if args.receipt is not None:
            result["receipt_validation"] = validate_receipt(
                args.profile, read_receipt(args.receipt))
    except (OSError, ValueError, RuntimeError) as error:
        raise SystemExit(f"refused: {error}") from error
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
