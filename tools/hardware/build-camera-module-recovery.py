#!/usr/bin/env python3
"""Build a host-only recovery candidate replacing only ramdisk fimc-is.ko.

This is a private artifact builder, not a deployment tool. It pins the current
HCI recovery image and both module inputs, validates module versions against
the matching kernel export set, and byte-compares every CPIO record other than
the target before and after repacking.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[2]
BASE_IMAGE_SHA256 = "42da267f3dd9f94f30f62a95fb2ac13f91d4cf98f1a2307f7cc14e45d9c49be5"
BASE_IMAGE_BYTES = 100663296
BASE_KERNEL_SHA256 = "7738564db77e4a6183ffaa875fc12f8168a58e8135a3bdc86e27b127b47fc05c"
BASE_RAMDISK_SHA256 = "0dd9dda696c26ccf4c99d77f9d24f334f0c4baece5e19312bcc12f2963841e5d"
BASE_DTB_SHA256 = "f5a00d80dd1800c934c6baed4ff4f5b7ac0bdc19acbc52a8e6092f03e5f7b2b8"
BASE_RECOVERY_DTBO_SHA256 = "dd2acb7f8e02a58bba6b9ee9da09790286ab19c887ef7feabc8a8be10e0e8f98"
BASE_CPIO_SHA256 = "69b545e83c1eccdb26e06cb9226d17a6db514f04d7fd5091fd2e7328c3b95599"
BASE_MODULE_PATH = "lib/modules/fimc-is.ko"
BASE_MODULE_SHA256 = "ba492fccbf1814372f568b230a07f278e22fbee7e7b22f77f97a75eeb1d085d3"
BASE_MODULE_BYTES = 8239848
BASE_MODULES_DEP_SHA256 = "fe40d3926aa809acfcd17bbcf1cfe6c865f518af819bd27bf40d6562ca4dba22"
BASE_MODULES_ALIAS_SHA256 = "c4437cdbbb7ed6e4af6b404a9175a5dcad7580b64013d65baf728c522b950ffb"
BASE_MODULES_SOFTDEP_SHA256 = "56c5b007ba4d737529861aca3a559d939d2f906d4752bc4307d08acb6ece9db0"
BASE_MODULE_ALIAS_ROWS = 28
BASE_MODULE_BUILD_ID = "8286071582b5efedff0e0c6169ba1a23018fb814"
KERNEL_SOURCE_BASE = "f52cbbd7e2783d529e1e5742d94e0fd64889bbdf"
CAMERA_PATCH_COMMITS = (
    "104e6b98153f9f00d571ec780687398c7a263057",
    "3fca50941422439b2019db2e4a3dc1016b2138a1",
)
KERNEL_SOURCE_HEAD = "3fca50941422439b2019db2e4a3dc1016b2138a1"
KERNEL_CONFIG_SHA256 = "d762d5fc71e369013ee36657d007063ce1f0faca9707d5f9ccfba2597b7fcd16"
STRIPPED_MODULE_SHA256 = "256926d8a1499d9fd8fcef22fc9ad677998decb1317b2037045c8dcc44b35ecf"
STRIPPED_MODULE_BYTES = 7164248
STRIPPED_MODULE_BUILD_ID = "59e54c032c545fff3ba52156f226fb6d69aadf64"
UNSTRIPPED_MODULE_SHA256 = "d3b6eda506023c1fea5a25e01a400ef9aa24fda6f3447dc4bb40b0780628f38e"
UNSTRIPPED_MODULE_BYTES = 56384552
STRIP_TOOL_SHA256 = "f52b9997b3c5019b4b3043e12b1ae2e821df67996ca344921c234c89c4d23e34"
BASE_SYMVERS_SHA256 = "15fc69e815cb4da6cb3372f4b5141005ab2e770b0414b67f230f1b185bf03df7"
BASE_VMLINUX_SYMVERS_SHA256 = "555fd150d1753ede989e80b383754bf9392defc6a55f6fba375c63616be7f8c2"
CANDIDATE_MODULE_SYMVERS_SHA256 = "e8d93fa5d84b654aa9a417f2af6dd4655d7f8969a9121aaf88749c617eb7e85e"
DEPENDENCY_SET_SHA256 = "d106216009e69b0145b4cc291bc31e703ec03fce2aaf61db140e39b0d7e91a04"
ALIAS_SET_SHA256 = "3afe78136be34abb6d1be0cccaaff1a48fa3fabaf8c803bd1eeacafdc4030656"
IMPORT_SET_SHA256 = "124a8dcda7bcc4412de8afda57b55676241c3747f26bd8c95d305516a12df6d5"
IMPORT_RECORDS_SHA256 = "c6ad6bbc9d7c6ff9c4cc261f083d21a9c1cc1dcd274df0b0ab91d2deb3c25e97"
KERNEL_RELEASE = "5.10.260-g4e5c5ad7d950"
KERNEL_VERMAGIC = "5.10.260-g4e5c5ad7d950 SMP preempt mod_unload modversions aarch64"
MODULE_LAYOUT_CRC = "0x0e3c515c"
MODULE_OWNER = "drivers/media/platform/exynos/camera/fimc-is"
PARTITION_SIZE = 100663296
AVBTOOL_SHA256 = "5698656733ef5077d62ee30395b5ad34295a0f170fb1ba570026c760ead83782"
MKBOOTIMG_SHA256 = "37d84b3d162e0bc62e36c1f4e1c63c85ea0caa9f29be023eb2f8efe006ad948c"
UNPACK_BOOTIMG_SHA256 = "a9d260978a63bd06a24b6347e7dee8a28ff96639793caea15dff6aa491316308"
LZ4_SHA256 = "87c0d5d060fd36c685b98a38611b7bd6c4614d40b36d29be84edcd6c173f8160"
CPIO_PARSER_SHA256 = "08e97e336e796108a850847687b7e3dad2b85f349353182ecce570601a7addb0"
IMAGE_HELPER_SHA256 = "c480336f5ccd7f2631d8fb0a332f8d6c3cbe8390857ed0b0449079f1089b929f"
PREFLIGHT_SHA256 = "715383e85d4c5b426f8b6fa43961da2481b57a8fc52dcd54c335faf68d5c4155"
AVB_SALT = "708474da33de9afafcd1835e6f4cf6f9b6e9451ad37748e95d322678250b69b7"
FINGERPRINT = "samsung/lineage_r0s/r0s:16/BP4A.251205.006/4a67c928b4:userdebug/release-keys"
TARGET_RECORD = "lib/modules/fimc-is.ko"
BOOT_V2_ID_OFFSET = 576
BOOT_ID_BYTES = 32
UNMODIFIED_HEADER_FIELDS = (
    "boot_magic", "header_version", "kernel_size", "kernel_load_address",
    "ramdisk_load_address", "second_size", "second_load_address",
    "tags_load_address", "page_size", "os_version", "os_patch_level",
    "product_name", "cmdline", "extra_cmdline", "recovery_dtbo_size",
    "boot_header_size", "dtb_size", "dtb_load_address", "boot_signature_size",
)
UNMODIFIED_IMAGE_COMPONENTS = ("kernel", "second", "dtb", "recovery_dtbo")


class BuildError(RuntimeError):
    """A pinned camera package input or validation failed."""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def require_pinned_file(path: Path, expected_sha256: str, label: str,
                        *, executable: bool = False) -> Path:
    path = Path(path)
    try:
        metadata = path.lstat()
    except OSError as error:
        raise BuildError(f"{label} is unavailable: {path}: {error}") from error
    if not stat.S_ISREG(metadata.st_mode):
        raise BuildError(f"{label} must be a non-symlink regular file: {path}")
    if executable and not os.access(path, os.X_OK):
        raise BuildError(f"{label} is not executable: {path}")
    actual = sha256_file(path)
    if actual != expected_sha256:
        raise BuildError(f"{label} SHA-256 mismatch: expected {expected_sha256}, got {actual}")
    return path


def validate_new_output_directory(path: Path) -> Path:
    """Return an absolute absent output path with safe existing parents."""
    output = Path(os.path.abspath(path))
    if output.exists() or output.is_symlink():
        raise BuildError(f"refusing existing output path: {output}")
    parent = output.parent
    while True:
        try:
            metadata = parent.lstat()
        except OSError as error:
            raise BuildError(f"output parent is unavailable: {parent}: {error}") from error
        if not stat.S_ISDIR(metadata.st_mode):
            raise BuildError(f"output parent must be a non-symlink directory: {parent}")
        if parent == parent.parent:
            break
        parent = parent.parent
    return output


def _load_module(source: Path, name: str, expected_sha256: str):
    require_pinned_file(source, expected_sha256, name)
    spec = importlib.util.spec_from_file_location(name, source)
    if spec is None or spec.loader is None:
        raise BuildError(f"cannot load pinned helper: {source}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _run(command: list[str], label: str) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(command, capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as error:
        detail = (error.stderr or error.stdout or "command failed").strip()
        tail = detail.splitlines()[-1] if detail else "command failed"
        raise BuildError(f"{label} failed (exit {error.returncode}): {tail}") from error


def _modinfo_value(modinfo: str, module_path: Path, field: str) -> str:
    result = _run([modinfo, "-F", field, str(module_path)], f"modinfo {field}")
    return result.stdout.strip()


def _canonical_lines_hash(lines: list[str]) -> str:
    ordered = sorted(line for line in lines if line)
    data = "".join(line + "\n" for line in ordered).encode("utf-8")
    return sha256_bytes(data)


def _canonical_import_hash(lines: list[str]) -> str:
    records = []
    for line in lines:
        fields = line.split()
        if len(fields) < 2 or re.fullmatch(r"0x[0-9a-fA-F]{8}", fields[0]) is None:
            raise BuildError("malformed modprobe imported-version output")
        records.append((fields[1], line))
    data = "".join(line + "\n" for _symbol, line in sorted(records)).encode("utf-8")
    return sha256_bytes(data)


def _module_build_id(readelf: str, module_path: Path) -> str:
    result = _run([readelf, "-n", str(module_path)], "readelf build ID")
    matches = re.findall(r"Build ID:\s*([0-9a-fA-F]{40})", result.stdout)
    if len(matches) != 1:
        raise BuildError("candidate module must have exactly one SHA-1 GNU build ID")
    return matches[0].lower()


def validate_module_inputs(module_path: Path, baseline_symvers_path: Path,
                           candidate_symvers_path: Path) -> dict[str, object]:
    """Validate the exact stripped .ko and its matching symbol records."""
    module_path = require_pinned_file(
        module_path, STRIPPED_MODULE_SHA256, "stripped fimc-is.ko",
    )
    if module_path.stat().st_size != STRIPPED_MODULE_BYTES:
        raise BuildError("stripped fimc-is.ko size differs from the pinned value")
    baseline_symvers_path = require_pinned_file(
        baseline_symvers_path, BASE_SYMVERS_SHA256, "baseline Module.symvers",
    )
    candidate_symvers_path = require_pinned_file(
        candidate_symvers_path, CANDIDATE_MODULE_SYMVERS_SHA256,
        "candidate modules-only.symvers",
    )

    preflight_path = ROOT / "tools/hardware/s22-hci-candidate-preflight-20260924.py"
    preflight = _load_module(preflight_path, "camera_module_preflight", PREFLIGHT_SHA256)
    baseline_rows = baseline_symvers_path.read_bytes().splitlines(keepends=True)
    candidate_exports = candidate_symvers_path.read_bytes()
    owned_rows = []
    for number, line in enumerate(baseline_rows, 1):
        columns = line.split()
        if len(columns) < 4:
            raise BuildError(f"malformed baseline Module.symvers row {number}")
        if columns[2].decode("ascii", errors="strict") == MODULE_OWNER:
            owned_rows.append(line)
    if (len(owned_rows) != 75 or sha256_bytes(b"".join(owned_rows)) != CANDIDATE_MODULE_SYMVERS_SHA256
            or b"".join(owned_rows) != candidate_exports):
        raise BuildError("candidate exports differ from the pinned baseline fimc-is exports")

    exports = preflight.parse_symvers(baseline_symvers_path)
    module = preflight.inspect_module_file(
        module_path, TARGET_RECORD, exports, True,
        shutil.which("modinfo") or "modinfo", shutil.which("modprobe") or "modprobe",
    )
    if module["name"] != "fimc-is.ko" or module["sha256"] != STRIPPED_MODULE_SHA256:
        raise BuildError("candidate module identity differs from the pinned receipt")
    if module["vermagic"] != KERNEL_VERMAGIC:
        raise BuildError("candidate module vermagic differs from the exact HCI kernel release")
    if (not module["versions_section_present"]
            or module["symbol_version_record_count"] != 378
            or module["symbol_version_records_sha256"] != IMPORT_RECORDS_SHA256):
        raise BuildError("candidate module version records differ from the pinned 378-record set")
    verdict = module["symbol_versions"]
    if not verdict["evidence_complete"] or not verdict["loader_compatible"]:
        raise BuildError("candidate imported symbol CRCs are not completely compatible")
    if (verdict["missing_symbol_count"] or verdict["crc_mismatch_count"]
            or verdict["unknown_candidate_crc_count"] or verdict["ambiguous_candidate_crc_count"]
            or verdict["module_layout_candidate_crcs"] != [MODULE_LAYOUT_CRC]):
        raise BuildError("candidate module symbol verdict differs from the pinned ABI")

    modinfo = shutil.which("modinfo")
    modprobe = shutil.which("modprobe")
    readelf = shutil.which("readelf")
    if not modinfo or not modprobe or not readelf:
        raise BuildError("host modinfo, modprobe, and readelf are required for static validation")
    if _modinfo_value(modinfo, module_path, "name") != "fimc_is":
        raise BuildError("candidate module internal name is not fimc_is")

    import_output = _run(
        [modprobe, "--dump-modversions", str(module_path)], "modprobe version extraction",
    ).stdout
    import_lines = import_output.splitlines()
    if _canonical_import_hash(import_lines) != IMPORT_SET_SHA256:
        raise BuildError("candidate canonical imported CRC set differs from the pinned set")

    aliases = _run([modinfo, "-F", "alias", str(module_path)], "modinfo alias").stdout.splitlines()
    if _canonical_lines_hash(aliases) != ALIAS_SET_SHA256:
        raise BuildError("candidate module alias set differs from the baseline")
    raw_depends = _modinfo_value(modinfo, module_path, "depends")
    depends = raw_depends.split(",") if raw_depends else []
    if (not depends or len(depends) != len(set(depends))
            or _canonical_lines_hash(depends) != DEPENDENCY_SET_SHA256):
        raise BuildError("candidate module direct dependency set differs from the baseline")
    signature = {
        field: _modinfo_value(modinfo, module_path, field)
        for field in ("sig_id", "signer", "sig_key", "sig_hashalgo")
    }
    if any(signature.values()):
        raise BuildError("candidate module unexpectedly has signature metadata")

    return {
        "path": TARGET_RECORD,
        "sha256": module["sha256"],
        "bytes": STRIPPED_MODULE_BYTES,
        "build_id_sha1": _module_build_id(readelf, module_path),
        "vermagic": module["vermagic"],
        "versions_section_present": module["versions_section_present"],
        "import_records": module["symbol_version_record_count"],
        "import_records_sha256": module["symbol_version_records_sha256"],
        "sorted_import_set_sha256": IMPORT_SET_SHA256,
        "symbol_version_verdict": verdict,
        "exports": {
            "rows": len(owned_rows),
            "sha256": sha256_bytes(candidate_exports),
            "matches_baseline": True,
        },
        "alias_count": len(aliases),
        "alias_set_sha256": ALIAS_SET_SHA256,
        "direct_dependency_count": len(depends),
        "direct_dependency_set_sha256": DEPENDENCY_SET_SHA256,
        "signature_fields": signature,
    }


def _newc_record(name: str, fields: tuple[int, ...], payload: bytes) -> bytes:
    if len(fields) != 13:
        raise BuildError("newc CPIO record must have exactly 13 fields")
    name_bytes = name.encode("utf-8", errors="strict") + b"\0"
    values = list(fields)
    values[6] = len(payload)
    values[11] = len(name_bytes)
    if any(value < 0 or value > 0xFFFFFFFF for value in values):
        raise BuildError("newc CPIO field is outside its 32-bit range")
    record = bytearray(b"070701")
    record += b"".join(f"{value:08x}".encode("ascii") for value in values)
    record += name_bytes
    record += b"\0" * ((-len(record)) & 3)
    record += payload
    record += b"\0" * ((-len(record)) & 3)
    return bytes(record)


def verify_only_target_record_changed(cpio_api, base_cpio: bytes,
                                      candidate_cpio: bytes,
                                      replacement_module: bytes) -> dict[str, object]:
    """Require identical CPIO ordering/raw records except for fimc-is.ko."""
    base = cpio_api.parse_cpio(base_cpio)
    candidate = cpio_api.parse_cpio(candidate_cpio)
    if len({record.name for record in base}) != len(base):
        raise BuildError("baseline CPIO contains duplicate record names")
    if len({record.name for record in candidate}) != len(candidate):
        raise BuildError("candidate CPIO contains duplicate record names")
    if [record.name for record in candidate] != [record.name for record in base]:
        raise BuildError("candidate CPIO record names or ordering changed")
    base_targets = [record for record in base if record.name == TARGET_RECORD]
    candidate_targets = [record for record in candidate if record.name == TARGET_RECORD]
    if len(base_targets) != 1 or len(candidate_targets) != 1:
        raise BuildError("CPIO must contain exactly one fimc-is.ko entry")
    if candidate_targets[0].payload != replacement_module:
        raise BuildError("candidate CPIO target payload differs from the stripped module")
    changed = []
    for before, after in zip(base, candidate, strict=True):
        if before.name != TARGET_RECORD:
            if before.raw != after.raw:
                raise BuildError(f"unexpected CPIO record changed: {before.name}")
            continue
        old_fields = list(before.fields)
        new_fields = list(after.fields)
        old_fields[6] = new_fields[6]
        if old_fields != new_fields:
            raise BuildError("fimc-is.ko CPIO ownership, mode, or other metadata changed")
        changed.append(before.name)
    if changed != [TARGET_RECORD]:
        raise BuildError("candidate CPIO must change only the fimc-is.ko payload")
    return {
        "record_count": len(base),
        "changed_records": changed,
        "base_target_bytes": len(base_targets[0].payload),
        "candidate_target_bytes": len(candidate_targets[0].payload),
        "target_mode": f"{candidate_targets[0].fields[1]:07o}",
        "target_uid_gid": f"{candidate_targets[0].fields[2]}:{candidate_targets[0].fields[3]}",
    }


def replace_module_cpio(cpio_api, base_cpio: bytes, replacement_module: bytes,
                        *, expected_base_sha256: str = BASE_MODULE_SHA256,
                        expected_module_sha256: str = STRIPPED_MODULE_SHA256,
                        expected_base_bytes: int = BASE_MODULE_BYTES) -> tuple[bytes, dict[str, object]]:
    """Replace the single pinned module while preserving every other raw record."""
    if not replacement_module:
        raise BuildError("replacement fimc-is.ko is empty")
    if sha256_bytes(replacement_module) != expected_module_sha256:
        raise BuildError("replacement fimc-is.ko SHA-256 differs from the pinned candidate")
    records = cpio_api.parse_cpio(base_cpio)
    if len({record.name for record in records}) != len(records):
        raise BuildError("baseline CPIO contains duplicate record names")
    targets = [record for record in records if record.name == TARGET_RECORD]
    if len(targets) != 1:
        raise BuildError("baseline CPIO must contain exactly one fimc-is.ko entry")
    target = targets[0]
    if (sha256_bytes(target.payload) != expected_base_sha256
            or len(target.payload) != expected_base_bytes
            or target.fields[1] != 0o100644
            or target.fields[2:4] != (0, 0)):
        raise BuildError("baseline CPIO fimc-is.ko identity or metadata differs from the pinned entry")

    result = bytearray()
    for record in records:
        if record.name == TARGET_RECORD:
            result += _newc_record(record.name, record.fields, replacement_module)
        else:
            result += record.raw
    candidate = bytes(result)
    summary = verify_only_target_record_changed(
        cpio_api, base_cpio, candidate, replacement_module,
    )
    return candidate, summary


def _boot_image_id(image: Path, header_version: int) -> str:
    if header_version != 2:
        raise BuildError("pinned recovery image must use Android boot header v2")
    with image.open("rb") as stream:
        stream.seek(BOOT_V2_ID_OFFSET)
        value = stream.read(BOOT_ID_BYTES)
    if len(value) != BOOT_ID_BYTES:
        raise BuildError("Android boot image ID field is truncated")
    return value.hex()


def _header_values(info) -> dict[str, object]:
    missing = [field for field in UNMODIFIED_HEADER_FIELDS if not hasattr(info, field)]
    if missing:
        raise BuildError(f"pinned unpacker omitted required header fields: {missing}")
    return {field: getattr(info, field) for field in UNMODIFIED_HEADER_FIELDS}


def _copy_new(source: Path, destination: Path, expected_sha256: str) -> str:
    """Copy a verified temp artifact into the new output and independently hash it."""
    source = Path(source)
    destination = Path(destination)
    try:
        before = source.lstat()
        if not stat.S_ISREG(before.st_mode):
            raise BuildError(f"copy source must be a non-symlink regular file: {source}")
        source_fd = os.open(
            source, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
    except OSError as error:
        raise BuildError(f"cannot open copy source safely: {source}: {error}") from error
    destination_fd = None
    try:
        opened = os.fstat(source_fd)
        if (not stat.S_ISREG(opened.st_mode)
                or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)):
            raise BuildError(f"copy source changed during identity check: {source}")
        destination_fd = os.open(
            destination,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        digest = hashlib.sha256()
        while True:
            block = os.read(source_fd, 1024 * 1024)
            if not block:
                break
            digest.update(block)
            offset = 0
            while offset < len(block):
                written = os.write(destination_fd, block[offset:])
                if written <= 0:
                    raise BuildError(f"short write while copying candidate artifact: {destination}")
                offset += written
        os.fsync(destination_fd)
        actual = digest.hexdigest()
        if actual != expected_sha256:
            raise BuildError(
                f"copy source bytes changed: expected {expected_sha256}, got {actual}"
            )
        os.close(destination_fd)
        destination_fd = None
        copied_hash = sha256_file(destination)
        if copied_hash != expected_sha256:
            raise BuildError(
                f"copied artifact hash mismatch: expected {expected_sha256}, got {copied_hash}"
            )
        return copied_hash
    finally:
        os.close(source_fd)
        if destination_fd is not None:
            os.close(destination_fd)


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0)
    fd = os.open(path, flags)
    try:
        if not stat.S_ISDIR(os.fstat(fd).st_mode):
            raise BuildError(f"expected directory for fsync: {path}")
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_new(path: Path, data: bytes) -> None:
    fd = os.open(
        path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        with os.fdopen(fd, "wb", closefd=False) as output:
            output.write(data)
            output.flush()
            os.fsync(fd)
    finally:
        os.close(fd)


def build_candidate(args) -> dict[str, object]:
    base_image = require_pinned_file(args.base_image, BASE_IMAGE_SHA256, "current HCI recovery image")
    if base_image.stat().st_size != BASE_IMAGE_BYTES:
        raise BuildError("current HCI recovery image size differs from the pinned RECOVERY partition")
    module_info = validate_module_inputs(
        args.module, args.baseline_symvers, args.candidate_module_symvers,
    )
    module_path = Path(args.module)
    module_bytes = module_path.read_bytes()

    for path, digest, label in (
        (args.mkbootimg, MKBOOTIMG_SHA256, "mkbootimg.py"),
        (args.unpack_bootimg, UNPACK_BOOTIMG_SHA256, "unpack_bootimg.py"),
        (args.avbtool, AVBTOOL_SHA256, "avbtool.py"),
    ):
        require_pinned_file(path, digest, label)
    lz4 = require_pinned_file(args.lz4, LZ4_SHA256, "lz4", executable=True)
    out = validate_new_output_directory(args.out_dir)

    cpio_api = _load_module(
        ROOT / "tools/headless-recovery/build_native_handoff.py",
        "camera_recovery_cpio", CPIO_PARSER_SHA256,
    )
    image_helper = _load_module(
        ROOT / "tools/hardware/build-bt-hci-recovery.py",
        "camera_recovery_image_helper", IMAGE_HELPER_SHA256,
    )
    unpacker = image_helper.load_unpacker(args.unpack_bootimg)

    with tempfile.TemporaryDirectory(prefix="camera-module-recovery-") as temporary:
        work = Path(temporary)
        base_dir = work / "base"
        base_info = image_helper.unpack(unpacker, base_image, base_dir)
        if base_info.header_version != 2:
            raise BuildError("current HCI recovery image is not Android header v2")
        if base_info.ramdisk_size != (base_dir / "ramdisk").stat().st_size:
            raise BuildError("unpacked HCI ramdisk size differs from its header")
        base_payload_hashes = {}
        for name, expected in (
            ("kernel", BASE_KERNEL_SHA256),
            ("ramdisk", BASE_RAMDISK_SHA256),
            ("dtb", BASE_DTB_SHA256),
            ("recovery_dtbo", BASE_RECOVERY_DTBO_SHA256),
        ):
            payload = base_dir / name
            if not payload.is_file() or payload.is_symlink() or sha256_file(payload) != expected:
                raise BuildError(f"current HCI image {name} differs from its pinned payload")
            base_payload_hashes[name] = expected

        image_helper.verify_image(args.avbtool, base_image)
        base_cpio_path = work / "base.ramdisk.cpio"
        _run([str(lz4), "-d", str(base_dir / "ramdisk"), str(base_cpio_path)], "lz4 base ramdisk decode")
        base_cpio = base_cpio_path.read_bytes()
        if sha256_bytes(base_cpio) != BASE_CPIO_SHA256:
            raise BuildError("decompressed current HCI ramdisk differs from the pinned CPIO")
        base_records = cpio_api.parse_cpio(base_cpio)
        required_records = {
            "lib/modules/modules.dep": BASE_MODULES_DEP_SHA256,
            "lib/modules/modules.alias": BASE_MODULES_ALIAS_SHA256,
            "lib/modules/modules.softdep": BASE_MODULES_SOFTDEP_SHA256,
        }
        for name, expected in required_records.items():
            matches = [record for record in base_records if record.name == name]
            if len(matches) != 1 or sha256_bytes(matches[0].payload) != expected:
                raise BuildError(f"current HCI CPIO metadata differs from its pinned {name} record")
        alias_text = next(record.payload for record in base_records
                          if record.name == "lib/modules/modules.alias")
        alias_lines = alias_text.decode("utf-8", errors="strict").splitlines()
        if sum(line.endswith(" fimc_is") for line in alias_lines) != BASE_MODULE_ALIAS_ROWS:
            raise BuildError("current HCI modules.alias fimc_is row count differs from the pinned value")

        candidate_cpio, cpio_summary = replace_module_cpio(cpio_api, base_cpio, module_bytes)
        candidate_cpio_path = work / "candidate.ramdisk.cpio"
        _write_new(candidate_cpio_path, candidate_cpio)
        candidate_ramdisk = work / "candidate.ramdisk.lz4"
        _run([str(lz4), "-l", "-12", str(candidate_cpio_path), str(candidate_ramdisk)],
             "lz4 candidate ramdisk encode")

        candidate_image = work / "candidate.recovery.img"
        boot_args = base_info.format_mkbootimg_argument()
        try:
            ramdisk_index = boot_args.index("--ramdisk")
            boot_args[ramdisk_index + 1] = str(candidate_ramdisk)
        except (ValueError, IndexError) as error:
            raise BuildError("pinned unpacker did not provide a replaceable ramdisk argument") from error
        _run(
            [sys.executable, str(args.mkbootimg), *boot_args, "--output", str(candidate_image)],
            "mkbootimg",
        )
        image_helper.run_trusted_avbtool(
            args.avbtool, "add_hash_footer",
            "--image", candidate_image,
            "--partition_size", str(PARTITION_SIZE),
            "--partition_name", "recovery",
            "--algorithm", "NONE",
            "--rollback_index", "0",
            "--salt", AVB_SALT,
            "--prop", f"com.android.build.recovery.fingerprint:{FINGERPRINT}",
        )
        if candidate_image.stat().st_size != PARTITION_SIZE:
            raise BuildError("candidate recovery image size differs from the RECOVERY partition")
        candidate_image_sha256 = sha256_file(candidate_image)
        image_helper.verify_image(args.avbtool, candidate_image)
        if sha256_file(candidate_image) != candidate_image_sha256:
            raise BuildError("candidate recovery image changed during AVB verification")

        candidate_dir = work / "candidate"
        candidate_info = image_helper.unpack(unpacker, candidate_image, candidate_dir)
        base_header = _header_values(base_info)
        candidate_header = _header_values(candidate_info)
        if base_header != candidate_header:
            changed = sorted(name for name in base_header if base_header[name] != candidate_header[name])
            raise BuildError(f"unexpected boot header fields changed: {changed}")
        if candidate_info.ramdisk_size != candidate_ramdisk.stat().st_size:
            raise BuildError("candidate header ramdisk size differs from the compressed replacement")
        base_id = _boot_image_id(base_image, base_info.header_version)
        candidate_id = _boot_image_id(candidate_image, candidate_info.header_version)
        if base_id == candidate_id:
            raise BuildError("repacked boot image ID did not change with the ramdisk payload")

        candidate_payload_hashes = {}
        for name in UNMODIFIED_IMAGE_COMPONENTS:
            original = base_dir / name
            repacked = candidate_dir / name
            if not original.exists() and not repacked.exists():
                continue
            if (not original.is_file() or original.is_symlink()
                    or not repacked.is_file() or repacked.is_symlink()):
                raise BuildError(f"boot payload presence/type changed: {name}")
            old_hash, new_hash = sha256_file(original), sha256_file(repacked)
            if old_hash != new_hash or old_hash != base_payload_hashes.get(name, old_hash):
                raise BuildError(f"repacked boot image changed an unmodified payload: {name}")
            candidate_payload_hashes[name] = new_hash
        if sha256_file(candidate_dir / "ramdisk") != sha256_file(candidate_ramdisk):
            raise BuildError("unpacked candidate ramdisk differs from the compressed CPIO replacement")
        candidate_cpio_check = work / "candidate.verify.cpio"
        _run([str(lz4), "-d", str(candidate_dir / "ramdisk"), str(candidate_cpio_check)],
             "lz4 candidate ramdisk verification")
        final_cpio = candidate_cpio_check.read_bytes()
        if final_cpio != candidate_cpio:
            raise BuildError("candidate image CPIO differs from the validated replacement CPIO")
        cpio_summary = verify_only_target_record_changed(
            cpio_api, base_cpio, final_cpio, module_bytes,
        )

        if sha256_file(base_image) != BASE_IMAGE_SHA256:
            raise BuildError("pinned current HCI recovery image changed during packaging")
        if sha256_file(candidate_image) != candidate_image_sha256:
            raise BuildError("candidate recovery image changed after payload verification")
        output_sources = {
            "recovery.img": candidate_image,
            "ramdisk.cpio": candidate_cpio_path,
            "ramdisk.lz4": candidate_ramdisk,
            "fimc-is.ko": module_path,
        }
        expected_output_hashes = {
            "recovery.img": candidate_image_sha256,
            "ramdisk.cpio": sha256_bytes(final_cpio),
            "ramdisk.lz4": sha256_file(candidate_ramdisk),
            "fimc-is.ko": module_info["sha256"],
        }
        if expected_output_hashes["fimc-is.ko"] != STRIPPED_MODULE_SHA256:
            raise BuildError("validated stripped module identity changed before durable copy")
        out.mkdir(mode=0o700)
        _fsync_directory(out.parent)
        image_out = out / "recovery.img"
        cpio_out = out / "ramdisk.cpio"
        ramdisk_out = out / "ramdisk.lz4"
        module_out = out / "fimc-is.ko"
        output_paths = {
            "recovery.img": image_out,
            "ramdisk.cpio": cpio_out,
            "ramdisk.lz4": ramdisk_out,
            "fimc-is.ko": module_out,
        }
        output_hashes = {
            name: _copy_new(output_sources[name], path, expected_output_hashes[name])
            for name, path in output_paths.items()
        }
        for name, path in output_paths.items():
            final_hash = sha256_file(path)
            if final_hash != expected_output_hashes[name] or final_hash != output_hashes[name]:
                raise BuildError(f"durable output changed before success receipt: {name}")
        _fsync_directory(out)
        manifest = {
            "phone_access": False,
            "deployed": False,
            "base_image_sha256": BASE_IMAGE_SHA256,
            "candidate_image_sha256": output_hashes["recovery.img"],
            "candidate_image_bytes": image_out.stat().st_size,
            "partition_size_bytes": PARTITION_SIZE,
            "base_avb_footer_verified": True,
            "candidate_avb_footer_verified": True,
            "avb_algorithm": "NONE",
            "avb_note": "footer/hash verification is not Samsung authentication or proof of bootability",
            "base_header": {
                **base_header,
                "ramdisk_size": base_info.ramdisk_size,
                "recovery_dtbo_offset": base_info.recovery_dtbo_offset,
                "image_id": base_id,
            },
            "candidate_header": {
                **candidate_header,
                "ramdisk_size": candidate_info.ramdisk_size,
                "recovery_dtbo_offset": candidate_info.recovery_dtbo_offset,
                "image_id": candidate_id,
            },
            "derived_header_changes_allowed": ["ramdisk_size", "recovery_dtbo_offset", "image_id"],
            "base_payload_sha256": base_payload_hashes,
            "candidate_unchanged_payload_sha256": candidate_payload_hashes,
            "base_ramdisk_sha256": BASE_RAMDISK_SHA256,
            "base_cpio_sha256": BASE_CPIO_SHA256,
            "candidate_cpio_sha256": sha256_bytes(final_cpio),
            "candidate_ramdisk_sha256": sha256_file(ramdisk_out),
            "cpio_record_validation": cpio_summary,
            "baseline_module": {
                "path": TARGET_RECORD,
                "bytes": BASE_MODULE_BYTES,
                "sha256": BASE_MODULE_SHA256,
                "build_id_sha1": BASE_MODULE_BUILD_ID,
                "mode": "0100644",
                "uid_gid": "0:0",
            },
            "replacement_module": module_info,
            "preserved_module_metadata_records": {
                "modules.dep_sha256": BASE_MODULES_DEP_SHA256,
                "modules.alias_sha256": BASE_MODULES_ALIAS_SHA256,
                "modules.alias_fimc_is_rows": BASE_MODULE_ALIAS_ROWS,
                "modules.softdep_sha256": BASE_MODULES_SOFTDEP_SHA256,
            },
            "tools": {
                "mkbootimg_sha256": MKBOOTIMG_SHA256,
                "unpack_bootimg_sha256": UNPACK_BOOTIMG_SHA256,
                "avbtool_sha256": AVBTOOL_SHA256,
                "lz4_sha256": LZ4_SHA256,
                "module_stripper_sha256": STRIP_TOOL_SHA256,
            },
            "kernel_build_identity": {
                "source_base_commit": KERNEL_SOURCE_BASE,
                "camera_patch_commits": list(CAMERA_PATCH_COMMITS),
                "source_head": KERNEL_SOURCE_HEAD,
                "source_clean_and_committed": True,
                "kernel_release": KERNEL_RELEASE,
                "config_sha256": KERNEL_CONFIG_SHA256,
                "baseline_module_symvers_sha256": BASE_SYMVERS_SHA256,
                "baseline_vmlinux_symvers_sha256": BASE_VMLINUX_SYMVERS_SHA256,
                "candidate_module_symvers_sha256": CANDIDATE_MODULE_SYMVERS_SHA256,
                "unstripped_module_sha256": UNSTRIPPED_MODULE_SHA256,
            },
            "output_files": output_hashes,
        }
        _write_new(out / "manifest.json", (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode())
        _fsync_directory(out)
        return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-image", type=Path, required=True,
                        help="exact current HCI recovery image (SHA-256 pinned)")
    parser.add_argument("--module", type=Path, required=True,
                        help="separately stripped fimc-is.ko candidate (SHA-256 pinned)")
    parser.add_argument("--baseline-symvers", type=Path, required=True,
                        help="matching complete baseline kernel Module.symvers")
    parser.add_argument("--candidate-module-symvers", type=Path, required=True,
                        help="candidate single-module modules-only.symvers")
    parser.add_argument("--mkbootimg", type=Path, required=True)
    parser.add_argument("--unpack-bootimg", type=Path, required=True)
    parser.add_argument("--avbtool", type=Path, required=True)
    parser.add_argument("--lz4", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True,
                        help="new private output directory; existing paths are refused")
    args = parser.parse_args()
    try:
        result = build_candidate(args)
    except (RuntimeError, OSError, UnicodeError, ValueError, subprocess.SubprocessError) as error:
        print(f"camera recovery package: {error}", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
