#!/usr/bin/env python3
"""Build a host-only recovery image replacing exactly three audio modules.

This narrowly pinned packager changes only the ABOX5, Rainbow Prince, and USB
audio offloading CPIO payloads in the preserved camera recovery image. It does
not install, load, deploy, or communicate with a device.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import struct
import subprocess
import sys
import tempfile
from collections import Counter


ROOT = Path(__file__).resolve().parents[2]
S22_ROOT = Path("/home/corpunum/s22-linux")

BASE_IMAGE = S22_ROOT / "builds/camera-module-recovery-20260927/recovery.img"
BASE_IMAGE_SHA256 = "b10412715756da3cc8ee221368b49f179cc0c64ab7bd2802976480905e6d8d2f"
BASE_IMAGE_BYTES = 100663296
BASE_CPIO_SHA256 = "f280aa4c1bee4545a281602d2f9f3551decfac47b7285c4fa5f9166ec85747ee"
BASE_CPIO_RECORDS = 963
BASE_RAMDISK_SHA256 = "a7769123c67ae909e29b2d89e73261fd58b7bde7ba193241a4b21a3ec2ddf89a"
BASE_KERNEL_SHA256 = "7738564db77e4a6183ffaa875fc12f8168a58e8135a3bdc86e27b127b47fc05c"
BASE_DTB_SHA256 = "f5a00d80dd1800c934c6baed4ff4f5b7ac0bdc19acbc52a8e6092f03e5f7b2b8"
BASE_RECOVERY_DTBO_SHA256 = "dd2acb7f8e02a58bba6b9ee9da09790286ab19c887ef7feabc8a8be10e0e8f98"
BASE_KERNEL_BUILD_ID_REFERENCE = "b2dda820b18d410d9bf12f1bd2584567d545991d"
PARTITION_SIZE = 100663296
BOOT_V2_ID_OFFSET = 576
BOOT_ID_BYTES = 32

TARGET_MODULES = {
    "lib/modules/snd-soc-samsung-abox.ko": {
        "name": "snd_soc_samsung_abox",
        "base_bytes": 1909256,
        "base_sha256": "7d61a65a617e1e0c500b7aa437a21d0cdb9a583d2d1277b5c8a42e2392028975",
        "base_build_id": "34a5354a75980688ee7dbeb6a848e04a7d54558f",
        "source_bytes": 9596112,
        "source_sha256": "55bae9f12135a2134337d7d520ddfadc85cdd049cedefd2a3c41f871fdd329bf",
        "source_build_id": "26347c3373e155fa6badf7883ff162f1d9f6723f",
        "candidate_bytes": 1735880,
        "candidate_sha256": "61d846d2bb13d5ff48261f21efadcd8b378bdc586c28b3e288ddccf145488265",
        "candidate_build_id": "26347c3373e155fa6badf7883ff162f1d9f6723f",
        "stripped_debug_sections": [
            ".debug_abbrev", ".debug_frame", ".debug_info", ".debug_line",
            ".debug_loc", ".debug_ranges", ".debug_str", ".rela.debug_frame",
            ".rela.debug_info", ".rela.debug_line", ".rela.debug_loc",
            ".rela.debug_ranges",
        ],
        "source": S22_ROOT / "builds/audio-native-five-out-20261002/sound/soc/samsung/abox/snd-soc-samsung-abox.ko",
    },
    "lib/modules/rainbow_prince.ko": {
        "name": "rainbow_prince",
        "base_bytes": 70400,
        "base_sha256": "7cca3e04a7a414b755a9f8ca01abfb5d3d78d490b2ef2f405be480e2d9f506eb",
        "base_build_id": "510b984887b640ad4ad3c1e9a556ef16c30b38c9",
        "source_bytes": 522472,
        "source_sha256": "6461073beee9e1fdc4f7c92b250bbb773a18cbd766e0c9331e77ec01e5e45170",
        "source_build_id": "8a7227b58cb7f4faf73ea92974781d34870bbfac",
        "candidate_bytes": 70792,
        "candidate_sha256": "b896200b333be6d518b9eb4b218abefe8c115a3162e5fb3b1a7016a6b7175c7a",
        "candidate_build_id": "8a7227b58cb7f4faf73ea92974781d34870bbfac",
        "stripped_debug_sections": [
            ".debug_abbrev", ".debug_frame", ".debug_info", ".debug_line",
            ".debug_loc", ".debug_ranges", ".debug_str", ".rela.debug_frame",
            ".rela.debug_info", ".rela.debug_line", ".rela.debug_loc",
            ".rela.debug_ranges",
        ],
        "source": S22_ROOT / "builds/audio-native-five-consumers-out-20261002/sound/soc/samsung/rainbow_prince.ko",
    },
    "lib/modules/exynos-usb-audio-offloading.ko": {
        "name": "exynos_usb_audio_offloading",
        "base_bytes": 47288,
        "base_sha256": "ca62486424aa41961242812e93340b2a4b9d8e8265cfcc1f42b4ce85e2c48466",
        "base_build_id": "4f35c50b0eca0d22b2060f7b8d84f03359feacd1",
        "source_bytes": 401656,
        "source_sha256": "92116d85c21c9c3969e00746fb0299a5cb7725edfe9c344d5645417686416ec2",
        "source_build_id": "8c9b0d4787ea32eae7de0086a4d662b0f351675d",
        "candidate_bytes": 42704,
        "candidate_sha256": "3b732ada44c0a6812b6aaeb38d7de8757ad54e7f843c6761b97ff4e5d63e8392",
        "candidate_build_id": "8c9b0d4787ea32eae7de0086a4d662b0f351675d",
        "stripped_debug_sections": [
            ".debug_abbrev", ".debug_frame", ".debug_info", ".debug_line",
            ".debug_loc", ".debug_ranges", ".debug_str", ".rela.debug_frame",
            ".rela.debug_info", ".rela.debug_line",
        ],
        "source": S22_ROOT / "builds/audio-native-five-consumers-out-20261002/sound/usb/exynos-usb-audio-offloading.ko",
    },
}
EXPECTED_RAMDISK_MODULES = 324
EXPECTED_VERMAGIC = "5.10.260-g4e5c5ad7d950 SMP preempt mod_unload modversions aarch64"
MODULE_LAYOUT_CRC = "0x0e3c515c"

BASE_SYMVERS = S22_ROOT / "builds/npu-native-eight-out-clang18-recipe-20261002/Module.symvers"
BASE_SYMVERS_BYTES = 1108525
BASE_SYMVERS_SHA256 = "15fc69e815cb4da6cb3372f4b5141005ab2e770b0414b67f230f1b185bf03df7"
BASE_ABOX_OWNER = b"sound/soc/samsung/abox/snd-soc-samsung-abox"
BASE_OFFLOADER_OWNER = b"sound/usb/exynos-usb-audio-offloading"
BASE_ABOX_ROWS = 28
BASE_ABOX_ROWS_SHA256 = "7b7e8246796a1ea9965a31c4eaebd9b1af3cf19dfdb564984732c4f3ba79be37"
BASE_OFFLOADER_ROWS = 5
BASE_OFFLOADER_ROWS_SHA256 = "715a469f62a95ab8813c866cb0656f756730475fb80322b1afb4dfcbcfd527ad"
ABOX_SYMVERS = S22_ROOT / "builds/audio-native-five-out-20261002/modules-only.symvers"
ABOX_SYMVERS_BYTES = 2585
ABOX_SYMVERS_SHA256 = "e73d89c9637e3cea289c4bc61d999967d9cd20ae33a73bf71bfd13c7439f67ac"
OFFLOADER_SYMVERS = S22_ROOT / "builds/audio-native-five-consumers-out-20261002/modules-only.symvers"
OFFLOADER_SYMVERS_BYTES = 429
OFFLOADER_SYMVERS_SHA256 = "715a469f62a95ab8813c866cb0656f756730475fb80322b1afb4dfcbcfd527ad"
UPDATED_SYMVERS_ROWS = 17283
UPDATED_SYMVERS_SHA256 = "0c8e481225fe3ab071ba9be1d14faae06ce8a556fa7e89b07f3dcefb761bd470"

WLAN_MODULE = S22_ROOT / "rootfs/wifi-vendor-assets/lineage-23.2-20260915/vendor_dlkm/lib/modules/wlan.ko"
WLAN_MODULE_SHA256 = "cbf8932d079e97006a5b7aae0e1b5acfe65b3e8b5113ab8fa095daa36796738d"
WLAN_MODULE_BYTES = 15466952
WLAN_MODULE_NAME = "wlan"
WLAN_IMPORTS = 495

BASE_METADATA_RECORDS = {
    "lib/modules/modules.dep": "fe40d3926aa809acfcd17bbcf1cfe6c865f518af819bd27bf40d6562ca4dba22",
    "lib/modules/modules.alias": "c4437cdbbb7ed6e4af6b404a9175a5dcad7580b64013d65baf728c522b950ffb",
    "lib/modules/modules.softdep": "56c5b007ba4d737529861aca3a559d939d2f906d4752bc4307d08acb6ece9db0",
}
CURRENT_FIMC = {
    "path": "lib/modules/fimc-is.ko",
    "bytes": 7164248,
    "sha256": "256926d8a1499d9fd8fcef22fc9ad677998decb1317b2037045c8dcc44b35ecf",
    "build_id": "59e54c032c545fff3ba52156f226fb6d69aadf64",
}

CAMERA_BUILDER_SHA256 = "5c5a5a530dea7d217c81aa4e96365fda876e1d1d81b45155b74d74ac6f8a83f6"
CPIO_PARSER_SHA256 = "08e97e336e796108a850847687b7e3dad2b85f349353182ecce570601a7addb0"
IMAGE_HELPER_SHA256 = "c480336f5ccd7f2631d8fb0a332f8d6c3cbe8390857ed0b0449079f1089b929f"
PREFLIGHT_SHA256 = "715383e85d4c5b426f8b6fa43961da2481b57a8fc52dcd54c335faf68d5c4155"
AVBTOOL = S22_ROOT / "tools/avb/avbtool.py"
AVBTOOL_SHA256 = "5698656733ef5077d62ee30395b5ad34295a0f170fb1ba570026c760ead83782"
MKBOOTIMG = S22_ROOT / "tools/mkbootimg/mkbootimg.py"
MKBOOTIMG_SHA256 = "37d84b3d162e0bc62e36c1f4e1c63c85ea0caa9f29be023eb2f8efe006ad948c"
MKBOOTIMG_IMPORT_ROOT = S22_ROOT / "tools/mkbootimg"
GKI_CERT_HELPER = MKBOOTIMG_IMPORT_ROOT / "gki/generate_gki_certificate.py"
GKI_CERT_HELPER_SHA256 = "1bb1feec68a13da18d581aa2c631798f86f6bc10b55d587b2dd31446a0f8a203"
UNPACK_BOOTIMG = S22_ROOT / "tools/mkbootimg/unpack_bootimg.py"
UNPACK_BOOTIMG_SHA256 = "a9d260978a63bd06a24b6347e7dee8a28ff96639793caea15dff6aa491316308"
LZ4 = Path("/usr/bin/lz4")
LZ4_SHA256 = "87c0d5d060fd36c685b98a38611b7bd6c4614d40b36d29be84edcd6c173f8160"
MODINFO = Path("/usr/sbin/modinfo")
MODPROBE = Path("/usr/sbin/modprobe")
KMOD_REALPATH = Path("/usr/bin/kmod")
KMOD_SHA256 = "5abf732e561c8be9ccd1794940b5d43025b041d4bed1db34782d979e0accc36f"
READELF = Path("/usr/bin/readelf")
READELF_REALPATH = Path("/usr/bin/x86_64-linux-gnu-readelf")
READELF_SHA256 = "64c58e15274bbbb5153f31078e455e9e77ee5f51489e709bba5bb788ce9df2b0"
OBJCOPY = Path("/usr/bin/llvm-objcopy-18")
OBJCOPY_REALPATH = Path("/usr/lib/llvm-18/bin/llvm-objcopy")
OBJCOPY_SHA256 = "f52b9997b3c5019b4b3043e12b1ae2e821df67996ca344921c234c89c4d23e34"

ELF64_HEADER = struct.Struct("<16sHHIQQQIHHHHHH")
ELF64_SECTION = struct.Struct("<IIQQQQIIQQ")
ELF64_SYMBOL = struct.Struct("<IBBHQQ")
ELF64_REL = struct.Struct("<QQ")
ELF64_RELA = struct.Struct("<QQq")
ELF_SHT_SYMTAB = 2
ELF_SHT_STRTAB = 3
ELF_SHT_RELA = 4
ELF_SHT_NOBITS = 8
ELF_SHT_REL = 9
ELF_SHT_DYNSYM = 11
ELF_SHF_ALLOC = 0x2
ELF_SHN_XINDEX = 0xFFFF
ELF_STT_FILE = 4
ELF_STB_LOCAL = 0

AVB_SALT = "708474da33de9afafcd1835e6f4cf6f9b6e9451ad37748e95d322678250b69b7"
FINGERPRINT = "samsung/lineage_r0s/r0s:16/BP4A.251205.006/4a67c928b4:userdebug/release-keys"
UNMODIFIED_IMAGE_COMPONENTS = ("kernel", "second", "dtb", "recovery_dtbo")


class BuildError(RuntimeError):
    """A pinned input or exact-three-module package validation failed."""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _safe_read(path: Path, label: str, expected_sha256: str | None = None,
               expected_bytes: int | None = None) -> bytes:
    path = Path(path)
    try:
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode):
            raise BuildError(f"{label} must be a non-symlink regular file: {path}")
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0))
    except OSError as error:
        raise BuildError(f"{label} is unavailable: {path}: {error}") from error
    try:
        opened = os.fstat(fd)
        if (not stat.S_ISREG(opened.st_mode)
                or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)):
            raise BuildError(f"{label} identity changed while opening: {path}")
        chunks = []
        digest = hashlib.sha256()
        size = 0
        while True:
            block = os.read(fd, 1024 * 1024)
            if not block:
                break
            chunks.append(block)
            digest.update(block)
            size += len(block)
        after = path.lstat()
        if ((after.st_dev, after.st_ino) != (opened.st_dev, opened.st_ino)
                or size != opened.st_size or size != after.st_size):
            raise BuildError(f"{label} changed while being read: {path}")
        actual = digest.hexdigest()
        if expected_sha256 is not None and actual != expected_sha256:
            raise BuildError(f"{label} SHA-256 mismatch: expected {expected_sha256}, got {actual}")
        if expected_bytes is not None and size != expected_bytes:
            raise BuildError(f"{label} size mismatch: expected {expected_bytes}, got {size}")
        return b"".join(chunks)
    finally:
        os.close(fd)


def _verify_file(path: Path, label: str, expected_sha256: str,
                 expected_bytes: int | None = None) -> dict[str, object]:
    path = Path(path)
    try:
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode):
            raise BuildError(f"{label} must be a non-symlink regular file: {path}")
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0))
    except OSError as error:
        raise BuildError(f"{label} is unavailable: {path}: {error}") from error
    try:
        opened = os.fstat(fd)
        if (not stat.S_ISREG(opened.st_mode)
                or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)):
            raise BuildError(f"{label} identity changed while opening: {path}")
        digest = hashlib.sha256()
        size = 0
        while True:
            block = os.read(fd, 1024 * 1024)
            if not block:
                break
            digest.update(block)
            size += len(block)
        after = path.lstat()
        if ((after.st_dev, after.st_ino) != (opened.st_dev, opened.st_ino)
                or size != opened.st_size or size != after.st_size):
            raise BuildError(f"{label} changed while being hashed: {path}")
        actual = digest.hexdigest()
        if actual != expected_sha256:
            raise BuildError(f"{label} SHA-256 mismatch: expected {expected_sha256}, got {actual}")
        if expected_bytes is not None and size != expected_bytes:
            raise BuildError(f"{label} size mismatch: expected {expected_bytes}, got {size}")
        return {"path": str(path), "bytes": size, "sha256": actual}
    finally:
        os.close(fd)


def _verify_executable(path: Path, expected_sha256: str, label: str) -> dict[str, object]:
    metadata = _verify_file(path, label, expected_sha256)
    if not os.access(path, os.X_OK):
        raise BuildError(f"{label} is not executable: {path}")
    return metadata


def _verify_tool_alias(path: Path, target: Path, expected_sha256: str,
                       label: str) -> dict[str, object]:
    try:
        alias = Path(path)
        info = alias.lstat()
        resolved = alias.resolve(strict=True)
    except OSError as error:
        raise BuildError(f"{label} is unavailable: {path}: {error}") from error
    if not stat.S_ISLNK(info.st_mode) or resolved != Path(target):
        raise BuildError(f"{label} alias must resolve exactly to {target}: {path}")
    metadata = _verify_executable(resolved, expected_sha256, label)
    return {"invocation": str(alias), "resolved": str(resolved), **metadata}


def _load_pinned_source(path: Path, name: str, expected_sha256: str):
    source = _safe_read(path, f"{name} helper", expected_sha256)
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise BuildError(f"cannot load pinned {name} helper: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    exec(compile(source, str(path), "exec"), module.__dict__)
    return module


def load_helpers() -> dict[str, object]:
    """Load only the project-pinned pure CPIO, image, ABI, and camera helpers."""
    return {
        "camera": _load_pinned_source(
            ROOT / "tools/hardware/build-camera-module-recovery.py",
            "audio_coherent_camera_helper", CAMERA_BUILDER_SHA256,
        ),
        "cpio": _load_pinned_source(
            ROOT / "tools/headless-recovery/build_native_handoff.py",
            "audio_coherent_cpio_helper", CPIO_PARSER_SHA256,
        ),
        "image": _load_pinned_source(
            ROOT / "tools/hardware/build-bt-hci-recovery.py",
            "audio_coherent_image_helper", IMAGE_HELPER_SHA256,
        ),
        "preflight": _load_pinned_source(
            ROOT / "tools/hardware/s22-hci-candidate-preflight-20260924.py",
            "audio_coherent_preflight_helper", PREFLIGHT_SHA256,
        ),
    }


def _parse_symvers(data: bytes, label: str) -> list[bytes]:
    if not data.endswith(b"\n"):
        raise BuildError(f"{label} must end with one complete LF-terminated row")
    rows = data.splitlines()
    if not rows or len(rows) != len(set(rows)):
        raise BuildError(f"{label} is empty or contains duplicate rows")
    for number, row in enumerate(rows, 1):
        fields = row.split()
        if (len(fields) < 4
                or re.fullmatch(rb"0x[0-9a-fA-F]{8}", fields[0]) is None):
            raise BuildError(f"{label} has a malformed row {number}")
    return rows


def build_updated_provider_map(baseline_data: bytes, abox_data: bytes,
                               offloader_data: bytes, *,
                               expected_baseline_rows: int = 17283,
                               expected_old_abox_rows: int = BASE_ABOX_ROWS,
                               expected_old_offloader_rows: int = BASE_OFFLOADER_ROWS,
                               expected_abox_rows: int = 28,
                               expected_offloader_rows: int = 5) -> tuple[bytes, dict[str, object], dict[str, set[str]]]:
    """Replace the two provider owners and return canonical rows plus exports."""
    baseline = _parse_symvers(baseline_data, "native-eight Module.symvers")
    abox = _parse_symvers(abox_data, "candidate ABOX5 modules-only.symvers")
    offloader = _parse_symvers(offloader_data, "candidate offloader modules-only.symvers")
    if len(baseline) != expected_baseline_rows:
        raise BuildError(f"native-eight provider map row count is {len(baseline)}, expected {expected_baseline_rows}")
    old_abox = [row for row in baseline if row.split()[2] == BASE_ABOX_OWNER]
    old_offloader = [row for row in baseline if row.split()[2] == BASE_OFFLOADER_OWNER]
    if len(old_abox) != expected_old_abox_rows or len(old_offloader) != expected_old_offloader_rows:
        raise BuildError("native-eight ABOX/offloader provider-owner rows differ from the pinned counts")
    if len(abox) != expected_abox_rows or len(offloader) != expected_offloader_rows:
        raise BuildError("candidate ABOX/offloader provider row count differs from the pinned count")
    if any(row.split()[2] != BASE_ABOX_OWNER for row in abox):
        raise BuildError("candidate ABOX5 symvers contains a row owned by another module")
    if any(row.split()[2] != BASE_OFFLOADER_OWNER for row in offloader):
        raise BuildError("candidate offloader symvers contains a row owned by another module")

    merged = sorted([row for row in baseline if row not in old_abox and row not in old_offloader] + abox + offloader)
    canonical = b"".join(row + b"\n" for row in merged)
    exports: dict[str, set[str]] = {}
    for row in merged:
        fields = row.decode("ascii", errors="strict").split()
        crc, symbol = fields[0].lower(), fields[1]
        exports.setdefault(symbol, set()).add(crc)
    duplicate_crc_symbols = sorted(symbol for symbol, crcs in exports.items() if len(crcs) > 1)
    summary = {
        "baseline_rows": len(baseline),
        "old_abox_rows_removed": len(old_abox),
        "old_abox_rows_sha256": sha256_bytes(b"\n".join(old_abox) + b"\n"),
        "old_offloader_rows_removed": len(old_offloader),
        "old_offloader_rows_sha256": sha256_bytes(b"\n".join(old_offloader) + b"\n"),
        "candidate_abox_rows_added": len(abox),
        "candidate_abox_rows_sha256": sha256_bytes(b"\n".join(abox) + b"\n"),
        "candidate_offloader_rows_added": len(offloader),
        "candidate_offloader_rows_sha256": sha256_bytes(b"\n".join(offloader) + b"\n"),
        "final_rows": len(merged),
        "final_sorted_rows_sha256": sha256_bytes(canonical),
        "distinct_crc_duplicate_provider_symbols": len(duplicate_crc_symbols),
        "distinct_crc_duplicate_provider_examples": duplicate_crc_symbols[:8],
    }
    return canonical, summary, exports


def _target_paths() -> tuple[str, ...]:
    return tuple(TARGET_MODULES)


def _validate_cpio_inventory(cpio_api, cpio_data: bytes,
                             *, expected_module_count: int | None = EXPECTED_RAMDISK_MODULES) -> tuple[list[object], list[object]]:
    try:
        records = cpio_api.parse_cpio(cpio_data)
    except (ValueError, UnicodeError) as error:
        raise BuildError(f"base CPIO is invalid: {error}") from error
    if len({record.name for record in records}) != len(records):
        raise BuildError("CPIO contains duplicate record names")
    modules = []
    for record in records:
        if not (record.name.startswith("lib/modules/") and record.name.endswith(".ko")):
            continue
        path = PurePosixPath(record.name)
        if (path.is_absolute() or len(path.parts) != 3
                or path.parts[0] != "lib" or path.parts[1] != "modules"
                or re.fullmatch(r"[A-Za-z0-9_.+-]+\.ko", path.name) is None):
            raise BuildError(f"unsafe or unsupported CPIO module path: {record.name!r}")
        if not stat.S_ISREG(record.fields[1]):
            raise BuildError(f"CPIO module is not a regular file: {record.name}")
        modules.append(record)
    if expected_module_count is not None and len(modules) != expected_module_count:
        raise BuildError(f"expected {expected_module_count} ramdisk modules, found {len(modules)}")
    return records, modules


def _target_pin_maps() -> tuple[dict[str, tuple[str, int]], dict[str, str]]:
    base = {path: (identity["base_sha256"], identity["base_bytes"])
            for path, identity in TARGET_MODULES.items()}
    candidates = {path: identity["candidate_sha256"] for path, identity in TARGET_MODULES.items()}
    return base, candidates


def verify_three_targets_only_changed(cpio_api, base_cpio: bytes,
                                      candidate_cpio: bytes,
                                      replacements: dict[str, bytes]) -> dict[str, object]:
    """Require exact CPIO order and raw bytes except for the three module payloads."""
    if set(replacements) != set(_target_paths()):
        raise BuildError("replacement set must contain exactly the three pinned audio modules")
    base, _base_modules = _validate_cpio_inventory(cpio_api, base_cpio, expected_module_count=None)
    candidate, _candidate_modules = _validate_cpio_inventory(cpio_api, candidate_cpio, expected_module_count=None)
    if [item.name for item in base] != [item.name for item in candidate]:
        raise BuildError("candidate CPIO record names or ordering changed")
    changed = []
    target_details = {}
    for before, after in zip(base, candidate, strict=True):
        if before.name not in replacements:
            if before.raw != after.raw:
                raise BuildError(f"unexpected non-target CPIO record changed: {before.name}")
            continue
        if after.payload != replacements[before.name]:
            raise BuildError(f"candidate CPIO payload differs from pinned replacement: {before.name}")
        old_fields = list(before.fields)
        new_fields = list(after.fields)
        old_fields[6] = new_fields[6]
        if old_fields != new_fields:
            raise BuildError(f"target CPIO identity or metadata changed: {before.name}")
        changed.append(before.name)
        target_details[before.name] = {
            "base_bytes": len(before.payload),
            "candidate_bytes": len(after.payload),
            "mode": f"{after.fields[1]:07o}",
            "uid_gid": f"{after.fields[2]}:{after.fields[3]}",
        }
    if len(changed) != 3 or set(changed) != set(_target_paths()):
        raise BuildError("candidate CPIO must change exactly the three pinned audio module payloads")
    return {"record_count": len(base), "changed_records": changed, "targets": target_details}


def replace_three_module_cpio(cpio_api, camera_helper, base_cpio: bytes,
                              replacement_modules: dict[str, bytes], *,
                              expected_base: dict[str, tuple[str, int]] | None = None,
                              expected_candidates: dict[str, str] | None = None,
                              expected_module_count: int | None = EXPECTED_RAMDISK_MODULES) -> tuple[bytes, dict[str, object]]:
    """Replace exactly the three pinned module payloads, preserving other raw records."""
    paths = _target_paths()
    if set(replacement_modules) != set(paths):
        raise BuildError("replacement set must contain exactly the three pinned audio modules")
    base_pins, candidate_pins = _target_pin_maps()
    expected_base = base_pins if expected_base is None else expected_base
    expected_candidates = candidate_pins if expected_candidates is None else expected_candidates
    if set(expected_base) != set(paths) or set(expected_candidates) != set(paths):
        raise BuildError("three-module replacement pins must name exactly the three target paths")
    records, modules = _validate_cpio_inventory(
        cpio_api, base_cpio, expected_module_count=expected_module_count,
    )
    if len(records) != BASE_CPIO_RECORDS and expected_module_count == EXPECTED_RAMDISK_MODULES:
        raise BuildError(f"base CPIO record count differs from the pin: {len(records)}")
    if (expected_module_count == EXPECTED_RAMDISK_MODULES
            and sha256_bytes(base_cpio) != BASE_CPIO_SHA256):
        raise BuildError("base CPIO SHA-256 differs from the pin")
    module_by_path = {record.name: record for record in modules}
    for path in paths:
        target = module_by_path.get(path)
        if target is None:
            raise BuildError(f"base CPIO must contain exactly one {path}")
        expected_sha, expected_size = expected_base[path]
        if (len(target.payload) != expected_size or sha256_bytes(target.payload) != expected_sha
                or target.fields[1] != 0o100644 or target.fields[2:4] != (0, 0)):
            raise BuildError(f"base CPIO target identity or ownership differs from the pin: {path}")
        replacement = replacement_modules[path]
        if not replacement:
            raise BuildError(f"replacement module is empty: {path}")
        if sha256_bytes(replacement) != expected_candidates[path]:
            raise BuildError(f"replacement module SHA-256 differs from the pin: {path}")

    output = bytearray()
    for record in records:
        replacement = replacement_modules.get(record.name)
        output += (camera_helper._newc_record(record.name, record.fields, replacement)
                   if replacement is not None else record.raw)
    candidate_cpio = bytes(output)
    summary = verify_three_targets_only_changed(
        cpio_api, base_cpio, candidate_cpio, replacement_modules,
    )
    return candidate_cpio, summary


def _depends_list(value: str, label: str) -> list[str]:
    value = value.strip()
    if not value:
        return []
    fields = [item.strip() for item in value.split(",")]
    if any(not item for item in fields) or len(fields) != len(set(fields)):
        raise BuildError(f"{label} dependency metadata is malformed or duplicated")
    return fields


def compare_module_discovery_metadata(camera_helper, old_depends: str,
                                      new_depends: str, old_aliases: list[str],
                                      new_aliases: list[str], label: str) -> dict[str, object]:
    """Allow ordering-only changes, but reject any dependency or alias set change."""
    old_order = _depends_list(old_depends, f"base {label}")
    new_order = _depends_list(new_depends, f"candidate {label}")
    if set(old_order) != set(new_order):
        missing = sorted(set(old_order) - set(new_order))
        added = sorted(set(new_order) - set(old_order))
        raise BuildError(f"candidate {label} dependency set differs: removed={missing}, added={added}")
    old_alias_hash = camera_helper._canonical_lines_hash(old_aliases)
    new_alias_hash = camera_helper._canonical_lines_hash(new_aliases)
    if old_alias_hash != new_alias_hash:
        raise BuildError(f"candidate {label} alias set differs from the preserved module")
    return {
        "base_depends_order": old_order,
        "candidate_depends_order": new_order,
        "dependency_set_unchanged": True,
        "dependency_order_changed": old_order != new_order,
        "aliases_unchanged": True,
        "alias_count": len(old_aliases),
        "alias_set_sha256": old_alias_hash,
    }


def _run(command: list[str], label: str) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(command, capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as error:
        details = (error.stderr or error.stdout or "command failed").strip()
        tail = details.splitlines()[-1] if details else "command failed"
        raise BuildError(f"{label} failed (exit {error.returncode}): {tail}") from error


def _modinfo_value(modinfo: Path, path: Path, field: str) -> str:
    return _run([str(modinfo), "-F", field, str(path)], f"modinfo {field}").stdout.strip()


def _module_build_id(readelf: Path, path: Path) -> str:
    notes = _run([str(readelf), "-n", str(path)], "readelf build ID").stdout
    matches = re.findall(r"Build ID:\s*([0-9a-fA-F]{40})", notes)
    if len(matches) != 1:
        raise BuildError(f"module must contain exactly one 40-hex SHA-1 GNU build ID: {path}")
    return matches[0].lower()


def _is_debug_section(name: str) -> bool:
    return (name.startswith((".debug", ".zdebug", ".rela.debug", ".rela.zdebug"))
            or name in {".stab", ".stabstr", ".gdb_index"})


def _elf_string(data: bytes, offset: int, label: str) -> str:
    if offset < 0 or offset >= len(data):
        raise BuildError(f"malformed ELF string offset in {label}")
    end = data.find(b"\0", offset)
    if end < 0:
        raise BuildError(f"unterminated ELF string in {label}")
    try:
        return data[offset:end].decode("ascii")
    except UnicodeDecodeError as error:
        raise BuildError(f"non-ASCII ELF identifier in {label}") from error


def _parse_elf64(data: bytes, label: str) -> dict[str, object]:
    if len(data) < ELF64_HEADER.size:
        raise BuildError(f"truncated ELF header: {label}")
    header = ELF64_HEADER.unpack_from(data)
    ident = header[0]
    if (ident[:4] != b"\x7fELF" or ident[4] != 2 or ident[5] != 1
            or ident[6] != 1 or header[11] != ELF64_SECTION.size):
        raise BuildError(f"unsupported or malformed ELF64 little-endian input: {label}")
    shoff, shentsize, shnum, shstrndx = header[6], header[11], header[12], header[13]
    if (shnum == 0 or shstrndx == 0xFFFF or shstrndx >= shnum
            or shoff < ELF64_HEADER.size
            or shoff + shentsize * shnum > len(data)):
        raise BuildError(f"unsupported extended or out-of-bounds ELF section table: {label}")
    raw_sections = [
        ELF64_SECTION.unpack_from(data, shoff + index * shentsize)
        for index in range(shnum)
    ]
    name_header = raw_sections[shstrndx]
    if name_header[1] != ELF_SHT_STRTAB:
        raise BuildError(f"ELF section-name table is not a string table: {label}")
    name_offset, name_size = name_header[4], name_header[5]
    if name_offset + name_size > len(data):
        raise BuildError(f"ELF section-name table exceeds file bounds: {label}")
    name_table = data[name_offset:name_offset + name_size]
    sections = []
    names_seen = set()
    for index, raw in enumerate(raw_sections):
        name = _elf_string(name_table, raw[0], label)
        section_type, flags, address, offset, size = raw[1:6]
        link, info, alignment, entry_size = raw[6:10]
        if section_type != ELF_SHT_NOBITS and offset + size > len(data):
            raise BuildError(f"ELF section exceeds file bounds: {label}:{name}")
        if name and name in names_seen:
            raise BuildError(f"duplicate ELF section name: {label}:{name}")
        if name:
            names_seen.add(name)
        content = b"" if section_type == ELF_SHT_NOBITS else data[offset:offset + size]
        sections.append({
            "index": index, "name": name, "type": section_type, "flags": flags,
            "address": address, "offset": offset, "size": size, "link": link,
            "info": info, "alignment": alignment, "entry_size": entry_size,
            "content": content,
        })
    return {"data": data, "header": header, "sections": sections}


def _elf_section_reference(sections: list[dict[str, object]], index: int,
                           label: str) -> str | None:
    if index == 0:
        return None
    if index < 0 or index >= len(sections):
        raise BuildError(f"ELF section reference is out of range: {label}")
    return str(sections[index]["name"])


def _elf_symbol_section(sections: list[dict[str, object]], index: int,
                        label: str) -> str:
    if index == 0:
        return "UND"
    if index == 0xFFF1:
        return "ABS"
    if index == 0xFFF2:
        return "COMMON"
    if index == ELF_SHN_XINDEX:
        raise BuildError(f"extended ELF symbol section index is unsupported: {label}")
    if index >= 0xFF00:
        return f"SPECIAL:{index:#x}"
    if index >= len(sections):
        raise BuildError(f"ELF symbol section index is out of range: {label}")
    return str(sections[index]["name"])


def _parse_elf_symbols(parsed: dict[str, object], label: str) -> dict[int, list[tuple]]:
    sections = parsed["sections"]
    data = parsed["data"]
    symbols_by_section = {}
    for section in sections:
        if section["type"] not in (ELF_SHT_SYMTAB, ELF_SHT_DYNSYM):
            continue
        if section["entry_size"] != ELF64_SYMBOL.size or section["size"] % ELF64_SYMBOL.size:
            raise BuildError(f"malformed ELF symbol table: {label}:{section['name']}")
        string_index = section["link"]
        if string_index >= len(sections) or sections[string_index]["type"] != ELF_SHT_STRTAB:
            raise BuildError(f"ELF symbol table has an invalid string-table link: {label}")
        string_table = sections[string_index]["content"]
        records = []
        count = section["size"] // ELF64_SYMBOL.size
        first_nonlocal = section["info"]
        if first_nonlocal > count:
            raise BuildError(f"ELF symbol-table local boundary is out of range: {label}")
        for index in range(count):
            offset = section["offset"] + index * ELF64_SYMBOL.size
            name_offset, info, other, shndx, value, size = ELF64_SYMBOL.unpack_from(data, offset)
            name = _elf_string(string_table, name_offset, label) if name_offset else ""
            section_name = _elf_symbol_section(sections, shndx, label)
            binding = info >> 4
            if (index < first_nonlocal) != (binding == ELF_STB_LOCAL):
                raise BuildError(f"ELF symbol-table local boundary is malformed: {label}")
            records.append((name, info, other, section_name, value, size))
        symbols_by_section[section["index"]] = records
    return symbols_by_section


def _canonical_runtime_symbols(parsed: dict[str, object], label: str) -> dict[str, list[tuple]]:
    sections = parsed["sections"]
    tables = _parse_elf_symbols(parsed, label)
    result = {}
    for index, records in tables.items():
        table_name = str(sections[index]["name"])
        retained = [
            record for record in records
            if ((record[1] & 0xF) != ELF_STT_FILE and not _is_debug_section(record[3]))
        ]
        result[table_name] = sorted(Counter(retained).items())
    return result


def _canonical_runtime_relocations(parsed: dict[str, object], label: str) -> dict[str, list[tuple]]:
    sections = parsed["sections"]
    data = parsed["data"]
    symbol_tables = _parse_elf_symbols(parsed, label)
    result = {}
    for section in sections:
        section_type = section["type"]
        if section_type not in (ELF_SHT_REL, ELF_SHT_RELA):
            continue
        name = str(section["name"])
        if _is_debug_section(name):
            continue
        table_index = section["link"]
        if table_index not in symbol_tables:
            raise BuildError(f"runtime relocation has no linked symbol table: {label}:{name}")
        target = _elf_section_reference(sections, section["info"], f"{label}:{name}")
        if target is None or _is_debug_section(target):
            raise BuildError(f"unexpected non-debug relocation against a debug section: {label}:{name}")
        entry_struct = ELF64_RELA if section_type == ELF_SHT_RELA else ELF64_REL
        if section["entry_size"] != entry_struct.size or section["size"] % entry_struct.size:
            raise BuildError(f"malformed runtime relocation section: {label}:{name}")
        symbols = symbol_tables[table_index]
        records = []
        count = section["size"] // entry_struct.size
        for entry_index in range(count):
            offset = section["offset"] + entry_index * entry_struct.size
            fields = entry_struct.unpack_from(data, offset)
            relocation_offset, info = fields[:2]
            symbol_index, relocation_type = info >> 32, info & 0xFFFFFFFF
            if symbol_index >= len(symbols):
                raise BuildError(f"runtime relocation symbol index is out of range: {label}:{name}")
            canonical = (relocation_offset, relocation_type, symbols[symbol_index])
            if section_type == ELF_SHT_RELA:
                canonical += (fields[2],)
            records.append(canonical)
        result[name] = records
    return result


def _section_link_name(sections: list[dict[str, object]], section: dict[str, object],
                       label: str) -> str | None:
    link = section["link"]
    if link == 0:
        return None
    return _elf_section_reference(sections, link, label)


def _section_metadata(parsed: dict[str, object], section: dict[str, object],
                      label: str) -> tuple:
    sections = parsed["sections"]
    section_type = section["type"]
    name = str(section["name"])
    if section_type in (ELF_SHT_REL, ELF_SHT_RELA):
        info_value = _elf_section_reference(sections, section["info"], label)
    elif section_type in (ELF_SHT_SYMTAB, ELF_SHT_DYNSYM):
        # sh_info is the first non-local symbol index, which may shift when
        # debug-only local symbols are stripped; symbol semantics are checked
        # separately by canonical identity.
        info_value = "symbol-table-local-boundary"
    else:
        info_value = section["info"]
    return (
        section_type, section["flags"], section["address"], section["alignment"],
        section["entry_size"], _section_link_name(sections, section, f"{label}:{name}"),
        info_value,
    )


def validate_debug_strip_transform(source: bytes, stripped: bytes,
                                   expected_removed_sections: list[str], *,
                                   require_aarch64_module: bool = False) -> dict[str, object]:
    """Prove --strip-debug removed only pinned debug sections and preserved runtime ELF semantics."""
    signature_trailer = b"~Module signature appended~\n"
    if source.endswith(signature_trailer) or stripped.endswith(signature_trailer):
        raise BuildError("signed module signature trailer is unsupported; refusing to invalidate it")
    original = _parse_elf64(source, "unstripped module")
    output = _parse_elf64(stripped, "stripped module")
    old_header, new_header = original["header"], output["header"]
    if require_aarch64_module and (old_header[1] != 1 or old_header[2] != 183):
        raise BuildError("candidate module must be an AArch64 ELF relocatable object")
    stable_header_fields = (0, 1, 2, 3, 4, 5, 7, 8, 9, 10, 11)
    if any(old_header[index] != new_header[index] for index in stable_header_fields):
        raise BuildError("ELF identity or load header changed during debug stripping")

    old_sections, new_sections = original["sections"], output["sections"]
    old_by_name = {str(section["name"]): section for section in old_sections if section["name"]}
    new_by_name = {str(section["name"]): section for section in new_sections if section["name"]}
    removed = sorted(set(old_by_name) - set(new_by_name))
    added = sorted(set(new_by_name) - set(old_by_name))
    expected_removed = sorted(expected_removed_sections)
    if removed != expected_removed:
        raise BuildError(f"debug strip removed an unexpected section set: {removed}")
    if added:
        raise BuildError(f"debug strip added unexpected section(s): {added}")
    if not removed or any(not _is_debug_section(name) for name in removed):
        raise BuildError("debug strip did not remove only the pinned debug sections")
    old_order = [str(section["name"]) for section in old_sections if section["name"] and not _is_debug_section(str(section["name"]))]
    new_order = [str(section["name"]) for section in new_sections if section["name"]]
    if old_order != new_order:
        raise BuildError("non-debug ELF section inventory or order changed")

    old_symbol_tables = _canonical_runtime_symbols(original, "unstripped module")
    new_symbol_tables = _canonical_runtime_symbols(output, "stripped module")
    if old_symbol_tables != new_symbol_tables:
        raise BuildError("runtime ELF symbol inventory changed during debug stripping")
    old_relocations = _canonical_runtime_relocations(original, "unstripped module")
    new_relocations = _canonical_runtime_relocations(output, "stripped module")
    if old_relocations != new_relocations:
        raise BuildError("runtime relocation semantics changed during debug stripping")

    allowed_variable_sections = {".symtab", ".strtab", ".shstrtab"}
    old_alloc_digest = hashlib.sha256()
    alloc_count = 0
    protected_names = []
    for name in old_order:
        before, after = old_by_name[name], new_by_name[name]
        old_metadata = _section_metadata(original, before, f"unstripped:{name}")
        new_metadata = _section_metadata(output, after, f"stripped:{name}")
        if old_metadata != new_metadata:
            raise BuildError(f"non-debug ELF section metadata changed: {name}")
        if before["flags"] & ELF_SHF_ALLOC:
            if before["size"] != after["size"] or before["content"] != after["content"]:
                raise BuildError(f"SHF_ALLOC section contents changed: {name}")
            alloc_count += 1
            record = {
                "name": name, "metadata": old_metadata,
                "size": before["size"],
                "content_sha256": hashlib.sha256(before["content"]).hexdigest(),
            }
            old_alloc_digest.update(json.dumps(record, sort_keys=True, separators=(",", ":")).encode())
            old_alloc_digest.update(b"\n")
        if name not in allowed_variable_sections and before["type"] not in (ELF_SHT_REL, ELF_SHT_RELA):
            if before["size"] != after["size"] or before["content"] != after["content"]:
                raise BuildError(f"non-debug ELF section contents changed: {name}")
        if any(token in name.lower() for token in (
                "kcfi", "shadow", "sks", "__versions", "__ksymtab", "__kcrctab", ".modinfo")):
            if (before["type"] not in (ELF_SHT_REL, ELF_SHT_RELA)
                    and (before["size"] != after["size"]
                         or before["content"] != after["content"])):
                raise BuildError(f"protected module metadata section changed: {name}")
            protected_names.append(name)

    old_crc_symbols = []
    for records in _parse_elf_symbols(original, "unstripped module").values():
        old_crc_symbols.extend(record for record in records if record[0].startswith("__crc_"))
    old_crc_symbols.sort()
    crc_digest = hashlib.sha256(json.dumps(old_crc_symbols, separators=(",", ":")).encode()).hexdigest()
    old_relocation_entries = sum(len(entries) for entries in old_relocations.values())
    relocation_digest = hashlib.sha256(json.dumps(old_relocations, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    symbol_digest = hashlib.sha256(json.dumps(old_symbol_tables, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    runtime_symbol_count = sum(
        multiplicity
        for records in old_symbol_tables.values()
        for _record, multiplicity in records
    )
    return {
        "removed_debug_sections": removed,
        "added_sections": added,
        "non_debug_section_count": len(old_order),
        "allocated_section_count": alloc_count,
        "allocated_section_inventory_sha256": old_alloc_digest.hexdigest(),
        "runtime_relocation_section_count": len(old_relocations),
        "runtime_relocation_entry_count": old_relocation_entries,
        "runtime_relocation_semantics_sha256": relocation_digest,
        "runtime_symbol_table_count": len(old_symbol_tables),
        "runtime_symbol_count": runtime_symbol_count,
        "runtime_symbol_inventory_sha256": symbol_digest,
        "export_crc_symbol_count": len(old_crc_symbols),
        "export_crc_symbols_sha256": crc_digest,
        "protected_module_sections": sorted(protected_names),
        "source_bytes": len(source),
        "stripped_bytes": len(stripped),
        "source_sha256": sha256_bytes(source),
        "stripped_sha256": sha256_bytes(stripped),
    }


def _modinfo_metadata(modinfo: Path, module_path: Path) -> list[tuple[str, str]]:
    output = _run([str(modinfo), "-0", str(module_path)], "modinfo metadata").stdout
    fields = []
    for record in output.split("\0"):
        if not record:
            continue
        key, separator, value = record.partition("=")
        if not separator:
            key, separator, value = record.partition(":")
        if not separator:
            raise BuildError(f"malformed modinfo metadata record: {record[:80]!r}")
        key = key.strip()
        if key != "filename":
            fields.append((key, value.lstrip()))
    return fields


def _modprobe_versions(modprobe: Path, module_path: Path) -> list[str]:
    output = _run([str(modprobe), "--dump-modversions", str(module_path)], "modprobe module versions").stdout
    return output.splitlines()


def strip_debug_candidate(source_path: Path, output_path: Path,
                          identity: dict[str, object],
                          source_bytes: bytes | None = None) -> tuple[bytes, dict[str, object]]:
    """Create and verify an isolated --strip-debug derivative of one pinned source module."""
    if source_bytes is None:
        source_bytes = _safe_read(
            source_path, "unstripped candidate module source",
            identity["source_sha256"], identity["source_bytes"],
        )
    elif (len(source_bytes) != identity["source_bytes"]
          or sha256_bytes(source_bytes) != identity["source_sha256"]):
        raise BuildError(f"in-memory unstripped candidate differs from its pin: {source_path.name}")
    _verify_file(source_path, "unstripped candidate module source",
                 identity["source_sha256"], identity["source_bytes"])
    source_build_id = _module_build_id(READELF, source_path)
    if source_build_id != identity["source_build_id"]:
        raise BuildError(f"unstripped candidate build ID differs from its pin: {source_path.name}")
    source_elf = _parse_elf64(source_bytes, "unstripped candidate module")
    if source_elf["header"][1] != 1 or source_elf["header"][2] != 183:
        raise BuildError(f"candidate module is not AArch64 ELF relocatable input: {source_path.name}")
    _verify_tool_alias(OBJCOPY, OBJCOPY_REALPATH, OBJCOPY_SHA256, "pinned llvm-objcopy-18")
    output_path = Path(output_path)
    if output_path.exists() or output_path.is_symlink():
        raise BuildError(f"refusing existing temporary stripped module: {output_path.name}")
    try:
        with output_path.open("xb") as stream:
            stream.write(source_bytes)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError as error:
        raise BuildError(f"cannot create isolated stripped module: {error}") from error
    _run([str(OBJCOPY), "--strip-debug", str(output_path)], "llvm-objcopy --strip-debug")
    stripped_bytes = _safe_read(
        output_path, "stripped candidate module",
        identity["candidate_sha256"], identity["candidate_bytes"],
    )
    transform = validate_debug_strip_transform(
        source_bytes, stripped_bytes, identity["stripped_debug_sections"],
        require_aarch64_module=True,
    )
    source_fields = _modinfo_metadata(MODINFO, source_path)
    stripped_fields = _modinfo_metadata(MODINFO, output_path)
    if source_fields != stripped_fields:
        raise BuildError(f"modinfo metadata changed during debug stripping: {source_path.name}")
    source_versions = _modprobe_versions(MODPROBE, source_path)
    stripped_versions = _modprobe_versions(MODPROBE, output_path)
    if source_versions != stripped_versions:
        raise BuildError(f"imported symbol CRCs changed during debug stripping: {source_path.name}")
    stripped_build_id = _module_build_id(READELF, output_path)
    if stripped_build_id != identity["candidate_build_id"] or stripped_build_id != source_build_id:
        raise BuildError(f"stripped candidate GNU build ID changed: {source_path.name}")
    _verify_file(
        source_path, "unchanged unstripped candidate source",
        identity["source_sha256"], identity["source_bytes"],
    )
    transform.update({
        "source_build_id_sha1": source_build_id,
        "stripped_build_id_sha1": stripped_build_id,
        "modinfo_field_count": len(source_fields),
        "modinfo_metadata_sha256": hashlib.sha256(
            json.dumps(source_fields, separators=(",", ":")).encode()
        ).hexdigest(),
        "imported_crc_record_count": len(source_versions),
        "imported_crc_records_sha256": hashlib.sha256(
            ("\n".join(source_versions) + "\n").encode()
        ).hexdigest() if source_versions else hashlib.sha256(b"").hexdigest(),
    })
    return stripped_bytes, transform


def validate_internal_module_name(modinfo: Path, module_path: Path,
                                  expected_name: str, label: str) -> str:
    """Read the module's internal ELF name; an inspection label is not identity."""
    actual_name = _modinfo_value(modinfo, module_path, "name")
    if actual_name != expected_name:
        raise BuildError(
            f"{label} internal module name differs from pin: "
            f"expected {expected_name!r}, got {actual_name!r}"
        )
    return actual_name


def _validate_target_metadata(camera_helper, cpio_api, base_cpio: bytes,
                              replacement_paths: dict[str, Path],
                              replacement_bytes: dict[str, bytes],
                              helpers: dict[str, object], temp_dir: Path) -> dict[str, object]:
    records, _modules = _validate_cpio_inventory(cpio_api, base_cpio)
    by_name = {record.name: record for record in records}
    details = {}
    for index, (path, identity) in enumerate(TARGET_MODULES.items()):
        old_record = by_name.get(path)
        if old_record is None:
            raise BuildError(f"base CPIO is missing target module {path}")
        old_path = temp_dir / f"base-{index}.ko"
        old_path.write_bytes(old_record.payload)
        candidate_path = replacement_paths[path]
        candidate_bytes = replacement_bytes[path]
        old_name = _modinfo_value(MODINFO, old_path, "name")
        new_name = _modinfo_value(MODINFO, candidate_path, "name")
        old_vermagic = _modinfo_value(MODINFO, old_path, "vermagic")
        new_vermagic = _modinfo_value(MODINFO, candidate_path, "vermagic")
        if old_name != identity["name"] or new_name != identity["name"]:
            raise BuildError(f"base/candidate internal module name differs from the pin: {path}")
        if old_vermagic != EXPECTED_VERMAGIC or new_vermagic != EXPECTED_VERMAGIC:
            raise BuildError(f"base/candidate vermagic differs from the pinned kernel: {path}")
        old_depends = _modinfo_value(MODINFO, old_path, "depends")
        new_depends = _modinfo_value(MODINFO, candidate_path, "depends")
        old_aliases = _run([str(MODINFO), "-F", "alias", str(old_path)], "base modinfo aliases").stdout.splitlines()
        new_aliases = _run([str(MODINFO), "-F", "alias", str(candidate_path)], "candidate modinfo aliases").stdout.splitlines()
        discovery = compare_module_discovery_metadata(
            camera_helper, old_depends, new_depends, old_aliases, new_aliases, path,
        )
        old_build_id = _module_build_id(READELF, old_path)
        new_build_id = _module_build_id(READELF, candidate_path)
        if old_build_id != identity["base_build_id"] or new_build_id != identity["candidate_build_id"]:
            raise BuildError(f"base/candidate GNU build ID differs from the pin: {path}")
        if sha256_bytes(old_record.payload) != identity["base_sha256"]:
            raise BuildError(f"base CPIO target SHA-256 differs from the pin: {path}")
        if sha256_bytes(candidate_bytes) != identity["candidate_sha256"]:
            raise BuildError(f"candidate module bytes changed after initial pin check: {path}")
        details[path] = {
            "base": {
                "bytes": len(old_record.payload), "sha256": identity["base_sha256"],
                "build_id_sha1": old_build_id, "name": old_name,
                "vermagic": old_vermagic, "depends_order": _depends_list(old_depends, path),
            },
            "candidate": {
                "bytes": len(candidate_bytes), "sha256": identity["candidate_sha256"],
                "build_id_sha1": new_build_id, "name": new_name,
                "vermagic": new_vermagic, "depends_order": _depends_list(new_depends, path),
            },
            "discovery_metadata": discovery,
        }
    return details


def _verify_base_metadata_records(cpio_api, base_cpio: bytes) -> dict[str, str]:
    records, modules = _validate_cpio_inventory(cpio_api, base_cpio)
    if len(records) != BASE_CPIO_RECORDS:
        raise BuildError(f"base CPIO record count differs from the pin: {len(records)}")
    by_name: dict[str, list[object]] = {}
    for record in records:
        by_name.setdefault(record.name, []).append(record)
    result = {}
    for name, expected_sha256 in BASE_METADATA_RECORDS.items():
        matches = by_name.get(name, [])
        if len(matches) != 1 or sha256_bytes(matches[0].payload) != expected_sha256:
            raise BuildError(f"base CPIO {name} bytes differ from the pinned metadata record")
        result[name] = expected_sha256
    fimc = by_name.get(CURRENT_FIMC["path"], [])
    if (len(fimc) != 1 or len(fimc[0].payload) != CURRENT_FIMC["bytes"]
            or sha256_bytes(fimc[0].payload) != CURRENT_FIMC["sha256"]):
        raise BuildError("base CPIO current fimc-is.ko differs from the preserved camera candidate")
    with tempfile.TemporaryDirectory(prefix="audio-preserved-fimc-check-") as temporary:
        fimc_path = Path(temporary) / "fimc-is.ko"
        fimc_path.write_bytes(fimc[0].payload)
        if _module_build_id(READELF, fimc_path) != CURRENT_FIMC["build_id"]:
            raise BuildError("base CPIO current fimc-is.ko GNU build ID differs from the camera pin")
    if len(modules) != EXPECTED_RAMDISK_MODULES:
        raise BuildError("base CPIO ramdisk module count differs from the pin")
    return result


def validate_static_abi_report(report: dict[str, object], label: str) -> dict[str, object]:
    """Reject incomplete or incompatible module reports from the pinned preflight."""
    versions = report.get("symbol_versions")
    if not isinstance(versions, dict):
        raise BuildError(f"static ABI inspection did not return a version report: {label}")
    required_zero_counts = (
        "missing_symbol_count", "crc_mismatch_count", "unknown_candidate_crc_count",
        "ambiguous_candidate_crc_count",
    )
    if (report.get("vermagic") != EXPECTED_VERMAGIC
            or report.get("versions_section_present") is not True
            or versions.get("evidence_complete") is not True
            or versions.get("loader_compatible") is not True
            or versions.get("missing_module_layout_version_record") is not False
            or versions.get("module_layout_crc_unverified") is not False
            or versions.get("module_layout_crc_mismatch") is not False
            or versions.get("module_layout_module_crcs") != [MODULE_LAYOUT_CRC]
            or versions.get("module_layout_candidate_crcs") != [MODULE_LAYOUT_CRC]
            or any(versions.get(key) != 0 for key in required_zero_counts)):
        raise BuildError(f"module is stale, incompatible, or incompletely versioned: {label}")
    return versions


def inspect_static_abi(cpio_api, preflight, candidate_cpio: bytes,
                       exports: dict[str, set[str]],
                       wlan_path: Path) -> dict[str, object]:
    """Check every final ramdisk module plus the selected external WLAN module."""
    _records, modules = _validate_cpio_inventory(cpio_api, candidate_cpio)
    inventory = hashlib.sha256()
    counts = {
        "module_count": 0, "import_count": 0, "missing_symbol_count": 0,
        "crc_mismatch_count": 0, "unknown_crc_count": 0,
        "ambiguous_crc_count": 0, "missing_versions_section_count": 0,
        "missing_module_layout_count": 0, "vermagic_mismatch_count": 0,
    }
    module_layouts = set()
    with tempfile.TemporaryDirectory(prefix="audio-coherent-module-check-") as temporary:
        work = Path(temporary)
        for index, record in enumerate(sorted(modules, key=lambda item: item.name)):
            module_path = work / f"ramdisk-{index:03d}.ko"
            module_path.write_bytes(record.payload)
            try:
                report = preflight.inspect_module_file(
                    module_path, record.name, exports, True, str(MODINFO), str(MODPROBE),
                )
            except (OSError, RuntimeError, ValueError) as error:
                raise BuildError(f"static ABI inspection failed for {record.name}: {error}") from error
            versions = validate_static_abi_report(report, record.name)
            counts["module_count"] += 1
            counts["import_count"] += versions["import_count"]
            counts["missing_symbol_count"] += versions["missing_symbol_count"]
            counts["crc_mismatch_count"] += versions["crc_mismatch_count"]
            counts["unknown_crc_count"] += versions["unknown_candidate_crc_count"]
            counts["ambiguous_crc_count"] += versions["ambiguous_candidate_crc_count"]
            counts["missing_versions_section_count"] += not report["versions_section_present"]
            counts["missing_module_layout_count"] += bool(
                versions["missing_module_layout_version_record"]
                or versions["module_layout_crc_unverified"]
                or versions["module_layout_crc_mismatch"]
                or versions["module_layout_module_crcs"] != [MODULE_LAYOUT_CRC]
                or versions["module_layout_candidate_crcs"] != [MODULE_LAYOUT_CRC]
            )
            counts["vermagic_mismatch_count"] += report["vermagic"] != EXPECTED_VERMAGIC
            module_layouts.update(versions["module_layout_module_crcs"])
            inventory.update(
                (record.name + "\t" + report["sha256"] + "\t" + report["vermagic"] + "\n").encode()
            )
            if (counts["missing_symbol_count"]
                    or counts["crc_mismatch_count"]
                    or counts["unknown_crc_count"]
                    or counts["ambiguous_crc_count"]):
                raise BuildError(f"ramdisk module fails complete ordinary-loader ABI checks: {record.name}")

        if counts["module_count"] != EXPECTED_RAMDISK_MODULES:
            raise BuildError(f"expected {EXPECTED_RAMDISK_MODULES} final ramdisk modules, found {counts['module_count']}")
        if any(counts[key] for key in (
            "missing_symbol_count", "crc_mismatch_count", "unknown_crc_count",
            "ambiguous_crc_count", "missing_versions_section_count",
            "missing_module_layout_count", "vermagic_mismatch_count",
        )):
            raise BuildError("final ramdisk ABI inventory contains an incompatibility or incomplete record")

        try:
            wlan = preflight.inspect_module_file(
                wlan_path, "selected external Lineage WLAN", exports, True,
                str(MODINFO), str(MODPROBE),
            )
        except (OSError, RuntimeError, ValueError) as error:
            raise BuildError(f"selected external WLAN static ABI inspection failed: {error}") from error
        wlan_versions = validate_static_abi_report(wlan, "selected external WLAN")
        wlan_internal_name = validate_internal_module_name(
            MODINFO, wlan_path, WLAN_MODULE_NAME, "selected external WLAN",
        )
        if (wlan["sha256"] != WLAN_MODULE_SHA256
                or wlan_path.stat().st_size != WLAN_MODULE_BYTES
                or wlan["vermagic"] != EXPECTED_VERMAGIC
                or not wlan["versions_section_present"]
                or wlan_versions["import_count"] != WLAN_IMPORTS
                or not wlan_versions["evidence_complete"]
                or wlan_versions["loader_compatible"] is not True
                or wlan_versions["missing_symbol_count"]
                or wlan_versions["crc_mismatch_count"]
                or wlan_versions["unknown_candidate_crc_count"]
                or wlan_versions["ambiguous_candidate_crc_count"]
                or wlan_versions["module_layout_module_crcs"] != [MODULE_LAYOUT_CRC]
                or wlan_versions["module_layout_candidate_crcs"] != [MODULE_LAYOUT_CRC]):
            raise BuildError("selected external WLAN is not completely compatible with the updated provider map")
    return {
        "scope": "324 preserved-camera ramdisk module records after exactly three replacements, plus selected external WLAN",
        "ramdisk": {
            **counts,
            "module_inventory_sha256_sorted_path_hash_vermagic": inventory.hexdigest(),
            "module_layout_crc": MODULE_LAYOUT_CRC,
            "module_layout_module_records": len(module_layouts),
            "all_vermagic_exact": True,
            "all_versions_sections_present": True,
            "all_imports_complete_and_compatible": True,
        },
        "selected_external_wlan": {
            "path": str(wlan_path), "bytes": WLAN_MODULE_BYTES,
            "sha256": wlan["sha256"], "inspection_label": wlan["name"],
            "module_name": wlan_internal_name,
            "imports": wlan_versions["import_count"],
            "module_layout_crc": MODULE_LAYOUT_CRC,
            "all_imports_complete_and_compatible": True,
            "included_in_ramdisk": False,
        },
        "limitations": [
            "Static host inspection does not run the kernel module loader or establish loaded membership.",
            "The 324-record CPIO inventory is not the current phone's 325-module runtime inventory.",
        ],
    }


def _isolated_avb_runner(command, **kwargs):
    command = list(command)
    if not command or Path(command[0]).resolve() != Path(sys.executable).resolve():
        raise BuildError("trusted AVB helper requested an unexpected interpreter")
    isolated = [command[0], "-I", "-S", "-B", *command[1:]]
    return subprocess.run(isolated, **kwargs)


def _verify_mkbootimg_sources(mkbootimg: Path) -> dict[str, dict[str, object]]:
    """Require both pinned sources imported by mkbootimg before starting a child."""
    return {
        "mkbootimg": _verify_file(mkbootimg, "pinned mkbootimg.py", MKBOOTIMG_SHA256),
        "mkbootimg_gki_helper": _verify_file(
            GKI_CERT_HELPER, "pinned mkbootimg GKI certificate helper",
            GKI_CERT_HELPER_SHA256,
        ),
    }


def _mkbootimg_command(python: Path, mkbootimg: Path, args: list[str],
                       import_root: Path | None = None) -> list[str]:
    """Use an isolated bootstrap with one explicit, pinned Android tool directory."""
    import_root = MKBOOTIMG_IMPORT_ROOT if import_root is None else Path(import_root)
    bootstrap = (
        "import runpy, sys\n"
        "import_root, script, *script_args = sys.argv[1:]\n"
        "sys.path.insert(0, import_root)\n"
        "sys.argv = [script, *script_args]\n"
        "runpy.run_path(script, run_name='__main__')\n"
    )
    return [
        str(python), "-I", "-S", "-B", "-c", bootstrap,
        str(import_root), str(mkbootimg), *args,
    ]


def _run_mkbootimg(python: Path, mkbootimg: Path,
                   args: list[str]) -> subprocess.CompletedProcess:
    _verify_mkbootimg_sources(mkbootimg)
    command = _mkbootimg_command(python, mkbootimg, args)
    return _run(command, "mkbootimg")


def _prepare_inputs(args, helpers: dict[str, object]) -> tuple[dict[str, bytes], dict[str, object]]:
    input_bytes = {}
    mkbootimg_sources = _verify_mkbootimg_sources(args.mkbootimg)
    input_files = [
        ("base_symvers", args.base_symvers, BASE_SYMVERS_SHA256, BASE_SYMVERS_BYTES),
        ("abox_symvers", args.abox_symvers, ABOX_SYMVERS_SHA256, ABOX_SYMVERS_BYTES),
        ("offloader_symvers", args.offloader_symvers, OFFLOADER_SYMVERS_SHA256, OFFLOADER_SYMVERS_BYTES),
    ]
    for name, path, digest, size in input_files:
        input_bytes[name] = _safe_read(path, name, digest, size)
    summary_bytes = {
        "base_image": _verify_file(args.base_image, "preserved camera recovery image", BASE_IMAGE_SHA256, BASE_IMAGE_BYTES),
        "base_symvers": {"path": str(args.base_symvers), "bytes": BASE_SYMVERS_BYTES, "sha256": BASE_SYMVERS_SHA256},
        "abox_symvers": {"path": str(args.abox_symvers), "bytes": ABOX_SYMVERS_BYTES, "sha256": ABOX_SYMVERS_SHA256},
        "offloader_symvers": {"path": str(args.offloader_symvers), "bytes": OFFLOADER_SYMVERS_BYTES, "sha256": OFFLOADER_SYMVERS_SHA256},
        "wlan": _verify_file(args.wlan, "selected external Lineage WLAN module", WLAN_MODULE_SHA256, WLAN_MODULE_BYTES),
        "avbtool": _verify_file(args.avbtool, "pinned public avbtool", AVBTOOL_SHA256),
        "mkbootimg": mkbootimg_sources["mkbootimg"],
        "mkbootimg_gki_helper": mkbootimg_sources["mkbootimg_gki_helper"],
        "unpack_bootimg": _verify_file(args.unpack_bootimg, "pinned unpack_bootimg.py", UNPACK_BOOTIMG_SHA256),
        "llvm_objcopy": _verify_tool_alias(
            OBJCOPY, OBJCOPY_REALPATH, OBJCOPY_SHA256, "pinned llvm-objcopy-18",
        ),
        "lz4": _verify_executable(args.lz4, LZ4_SHA256, "pinned lz4"),
        "modinfo": _verify_tool_alias(MODINFO, KMOD_REALPATH, KMOD_SHA256, "pinned modinfo/kmod"),
        "modprobe": _verify_tool_alias(MODPROBE, KMOD_REALPATH, KMOD_SHA256, "pinned modprobe/kmod"),
        "readelf": _verify_tool_alias(READELF, READELF_REALPATH, READELF_SHA256, "pinned readelf"),
        "python": {
            "executable": str(Path(sys.executable).resolve()),
            "version": sys.version.splitlines()[0],
            "isolated": bool(sys.flags.isolated),
            "no_site": bool(sys.flags.no_site),
            "optimize": sys.flags.optimize,
            "sha256": sha256_bytes(Path(sys.executable).resolve().read_bytes()),
        },
    }
    candidate_paths = getattr(args, "candidate_modules", {
        path: identity["source"] for path, identity in TARGET_MODULES.items()
    })
    for path, identity in TARGET_MODULES.items():
        source = Path(candidate_paths[path])
        data = _safe_read(source, f"unstripped candidate source {path}",
                          identity["source_sha256"], identity["source_bytes"])
        _verify_file(source, f"unstripped candidate source {path}",
                     identity["source_sha256"], identity["source_bytes"])
        input_bytes[path] = data
        summary_bytes[path] = {
            "path": str(source), "bytes": identity["source_bytes"],
            "sha256": identity["source_sha256"], "build_id_sha1": identity["source_build_id"],
        }
    summary_bytes["base_cpio"] = {"sha256": BASE_CPIO_SHA256, "record_count": BASE_CPIO_RECORDS}
    summary_bytes["updated_provider_map_sha256"] = UPDATED_SYMVERS_SHA256
    return input_bytes, summary_bytes


def build_candidate(args) -> dict[str, object]:
    if sys.flags.isolated != 1 or sys.flags.no_site != 1:
        raise BuildError("invoke the package builder with python3 -I -S; -B is recommended")
    helpers = load_helpers()
    camera, cpio_api, image_helper, preflight = (
        helpers["camera"], helpers["cpio"], helpers["image"], helpers["preflight"],
    )
    # The absent path is checked before expensive work and again immediately
    # before the exclusive directory creation at the final publication step.
    output = camera.validate_new_output_directory(args.out_dir)
    input_bytes, input_summary = _prepare_inputs(args, helpers)
    candidate_paths = getattr(args, "candidate_modules", {
        path: identity["source"] for path, identity in TARGET_MODULES.items()
    })
    for path, identity in TARGET_MODULES.items():
        if _module_build_id(READELF, candidate_paths[path]) != identity["source_build_id"]:
            raise BuildError(f"unstripped candidate GNU build ID differs from the source pin: {path}")

    canonical_symvers, provider_summary, exports = build_updated_provider_map(
        input_bytes["base_symvers"], input_bytes["abox_symvers"], input_bytes["offloader_symvers"],
    )
    if (provider_summary["final_rows"] != UPDATED_SYMVERS_ROWS
            or provider_summary["final_sorted_rows_sha256"] != UPDATED_SYMVERS_SHA256
            or provider_summary["old_abox_rows_sha256"] != BASE_ABOX_ROWS_SHA256
            or provider_summary["old_offloader_rows_sha256"] != BASE_OFFLOADER_ROWS_SHA256
            or provider_summary["candidate_abox_rows_sha256"] != ABOX_SYMVERS_SHA256
            or provider_summary["candidate_offloader_rows_sha256"] != OFFLOADER_SYMVERS_SHA256
            or provider_summary["distinct_crc_duplicate_provider_symbols"] != 0):
        raise BuildError("constructed updated provider map differs from the pinned 17,283-row map")

    python_path = Path(sys.executable).resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix="audio-coherent-recovery-") as temporary:
        work = Path(temporary)
        stripped_dir = work / "stripped-modules"
        stripped_dir.mkdir()
        stripped_paths = {}
        replacement_bytes = {}
        debug_strip_summaries = {}
        for path, identity in TARGET_MODULES.items():
            stripped_path = stripped_dir / PurePosixPath(path).name
            stripped_bytes, strip_summary = strip_debug_candidate(
                Path(candidate_paths[path]), stripped_path, identity, input_bytes[path],
            )
            stripped_paths[path] = stripped_path
            replacement_bytes[path] = stripped_bytes
            debug_strip_summaries[path] = strip_summary
        base_dir = work / "base"
        unpacker = _load_pinned_source(
            args.unpack_bootimg, "audio_coherent_unpack_bootimg", UNPACK_BOOTIMG_SHA256,
        )
        base_info = image_helper.unpack(unpacker, args.base_image, base_dir)
        if base_info.header_version != 2 or args.base_image.stat().st_size != PARTITION_SIZE:
            raise BuildError("preserved base is not the pinned Android v2 RECOVERY partition")
        if base_info.ramdisk_size != (base_dir / "ramdisk").stat().st_size:
            raise BuildError("unpacked base ramdisk size differs from its header")
        base_payload_hashes = {}
        for name, expected in (
            ("kernel", BASE_KERNEL_SHA256), ("ramdisk", BASE_RAMDISK_SHA256),
            ("dtb", BASE_DTB_SHA256), ("recovery_dtbo", BASE_RECOVERY_DTBO_SHA256),
        ):
            payload = base_dir / name
            if not payload.is_file() or payload.is_symlink() or camera.sha256_file(payload) != expected:
                raise BuildError(f"preserved base {name} payload differs from the exact camera image pin")
            base_payload_hashes[name] = expected
        image_helper.verify_image(args.avbtool, args.base_image, runner=_isolated_avb_runner)

        base_cpio_path = work / "base.ramdisk.cpio"
        _run([str(args.lz4), "-d", str(base_dir / "ramdisk"), str(base_cpio_path)], "lz4 base ramdisk decode")
        base_cpio = base_cpio_path.read_bytes()
        if sha256_bytes(base_cpio) != BASE_CPIO_SHA256:
            raise BuildError("decompressed base ramdisk differs from pinned 963-record CPIO")
        preserved_metadata = _verify_base_metadata_records(cpio_api, base_cpio)

        replacement_paths = stripped_paths
        (work / "old-targets").mkdir()
        target_metadata = _validate_target_metadata(
            camera, cpio_api, base_cpio, replacement_paths, replacement_bytes,
            helpers, work / "old-targets",
        )
        candidate_cpio, cpio_summary = replace_three_module_cpio(
            cpio_api, camera, base_cpio, replacement_bytes,
        )
        candidate_abi = inspect_static_abi(cpio_api, preflight, candidate_cpio, exports, args.wlan)

        candidate_cpio_path = work / "candidate.ramdisk.cpio"
        camera._write_new(candidate_cpio_path, candidate_cpio)
        candidate_ramdisk = work / "candidate.ramdisk.lz4"
        _run([str(args.lz4), "-l", "-12", str(candidate_cpio_path), str(candidate_ramdisk)],
             "lz4 candidate ramdisk encode")
        if camera.sha256_file(candidate_ramdisk) == BASE_RAMDISK_SHA256:
            raise BuildError("candidate ramdisk bytes did not change after module replacement")

        candidate_image = camera.candidate_recovery_image_path(work)
        boot_args = base_info.format_mkbootimg_argument()
        try:
            ramdisk_index = boot_args.index("--ramdisk")
            boot_args[ramdisk_index + 1] = str(candidate_ramdisk)
        except (ValueError, IndexError) as error:
            raise BuildError("pinned unpacker did not provide a replaceable ramdisk argument") from error
        _run_mkbootimg(python_path, args.mkbootimg, [*boot_args, "--output", str(candidate_image)])
        image_helper.run_trusted_avbtool(
            args.avbtool, "add_hash_footer", "--image", candidate_image,
            "--partition_size", str(PARTITION_SIZE), "--partition_name", "recovery",
            "--algorithm", "NONE", "--rollback_index", "0", "--salt", AVB_SALT,
            "--prop", f"com.android.build.recovery.fingerprint:{FINGERPRINT}",
            runner=_isolated_avb_runner,
        )
        if candidate_image.stat().st_size != PARTITION_SIZE:
            raise BuildError("candidate image size differs from the pinned RECOVERY partition")
        candidate_image_sha256 = camera.sha256_file(candidate_image)
        image_helper.verify_image(args.avbtool, candidate_image, runner=_isolated_avb_runner)
        if camera.sha256_file(candidate_image) != candidate_image_sha256:
            raise BuildError("candidate image changed during AVB verification")

        candidate_dir = work / "candidate-unpacked"
        candidate_info = image_helper.unpack(unpacker, candidate_image, candidate_dir)
        base_header = camera._header_values(base_info)
        candidate_header = camera._header_values(candidate_info)
        if base_header != candidate_header:
            changed = sorted(name for name in base_header if base_header[name] != candidate_header[name])
            raise BuildError(f"unexpected non-derived boot header fields changed: {changed}")
        if candidate_info.ramdisk_size != candidate_ramdisk.stat().st_size:
            raise BuildError("candidate boot header ramdisk size differs from the compressed CPIO")
        base_id = camera._boot_image_id(args.base_image, base_info.header_version)
        candidate_id = camera._boot_image_id(candidate_image, candidate_info.header_version)
        if base_id == candidate_id:
            raise BuildError("repacked recovery image ID did not change with the ramdisk")

        candidate_payload_hashes = {}
        for name in UNMODIFIED_IMAGE_COMPONENTS:
            original, repacked = base_dir / name, candidate_dir / name
            if not original.exists() and not repacked.exists():
                continue
            if (not original.is_file() or original.is_symlink()
                    or not repacked.is_file() or repacked.is_symlink()):
                raise BuildError(f"boot payload presence/type changed: {name}")
            before_hash, after_hash = camera.sha256_file(original), camera.sha256_file(repacked)
            if before_hash != after_hash or before_hash != base_payload_hashes.get(name, before_hash):
                raise BuildError(f"repacked image changed an unmodified boot payload: {name}")
            candidate_payload_hashes[name] = after_hash

        if camera.sha256_file(candidate_dir / "ramdisk") != camera.sha256_file(candidate_ramdisk):
            raise BuildError("unpacked candidate ramdisk differs from its compressed output")
        final_cpio_path = work / "candidate.verify.cpio"
        _run([str(args.lz4), "-d", str(candidate_dir / "ramdisk"), str(final_cpio_path)],
             "lz4 final ramdisk verification")
        final_cpio = final_cpio_path.read_bytes()
        if final_cpio != candidate_cpio:
            raise BuildError("final recovery image CPIO differs from the validated three-module replacement")
        cpio_summary = verify_three_targets_only_changed(cpio_api, base_cpio, final_cpio, replacement_bytes)
        candidate_abi = inspect_static_abi(cpio_api, preflight, final_cpio, exports, args.wlan)
        if camera.sha256_file(args.base_image) != BASE_IMAGE_SHA256:
            raise BuildError("pinned camera recovery image changed during packaging")
        if _verify_file(args.wlan, "selected external Lineage WLAN module", WLAN_MODULE_SHA256,
                        WLAN_MODULE_BYTES)["sha256"] != WLAN_MODULE_SHA256:
            raise BuildError("selected external WLAN changed during packaging")
        if camera.sha256_file(candidate_image) != candidate_image_sha256:
            raise BuildError("candidate image changed after byte-preservation checks")

        output_files = {
            "recovery.img": (candidate_image, candidate_image_sha256),
            "ramdisk.cpio": (candidate_cpio_path, sha256_bytes(final_cpio)),
            "ramdisk.lz4": (candidate_ramdisk, camera.sha256_file(candidate_ramdisk)),
        }
        for relative, identity in TARGET_MODULES.items():
            output_files[PurePosixPath(relative).name] = (
                stripped_paths[relative], identity["candidate_sha256"],
            )

        for relative, identity in TARGET_MODULES.items():
            _verify_file(
                Path(candidate_paths[relative]),
                f"unchanged unstripped candidate source {relative}",
                identity["source_sha256"], identity["source_bytes"],
            )

        output = camera.validate_new_output_directory(args.out_dir)
        try:
            os.mkdir(output, 0o700)
        except FileExistsError as error:
            raise BuildError(f"refusing existing output path: {output}") from error
        camera._fsync_directory(output.parent)
        copied_hashes = {}
        for name, (source, expected_sha256) in output_files.items():
            copied_hashes[name] = camera._copy_new(source, output / name, expected_sha256)
        for name, (_source, expected_sha256) in output_files.items():
            final_hash = camera.sha256_file(output / name)
            if final_hash != expected_sha256 or final_hash != copied_hashes[name]:
                raise BuildError(f"durable output changed before receipt publication: {name}")
        camera._fsync_directory(output)

        manifest = {
            "schema": "s22-audio-coherent-recovery-package/v1",
            "host_only": True,
            "phone_access": False,
            "module_loaded": False,
            "deployed": False,
            "boot_authorized": False,
            "base_image": input_summary["base_image"],
            "base_image_avb_footer_verified": True,
            "base_cpio_sha256": BASE_CPIO_SHA256,
            "base_cpio_record_count": BASE_CPIO_RECORDS,
            "base_kernel_build_id_reference": BASE_KERNEL_BUILD_ID_REFERENCE,
            "base_payload_sha256": base_payload_hashes,
            "candidate_image_sha256": copied_hashes["recovery.img"],
            "candidate_image_bytes": PARTITION_SIZE,
            "partition_size_bytes": PARTITION_SIZE,
            "candidate_avb_footer_verified": True,
            "pinned_input_files": {
                name: input_summary[name]
                for name in (
                    "base_symvers", "abox_symvers", "offloader_symvers", "wlan",
                    "mkbootimg_gki_helper",
                    *TARGET_MODULES.keys(),
                )
            },
            "avb_algorithm": "NONE",
            "avb_verification_note": "Footer/hash verification is not Samsung authentication, bootability, or device acceptance.",
            "base_header": {
                **base_header, "ramdisk_size": base_info.ramdisk_size,
                "recovery_dtbo_offset": base_info.recovery_dtbo_offset, "image_id": base_id,
            },
            "candidate_header": {
                **candidate_header, "ramdisk_size": candidate_info.ramdisk_size,
                "recovery_dtbo_offset": candidate_info.recovery_dtbo_offset,
                "image_id": candidate_id,
            },
            "derived_header_fields": ["ramdisk_size", "recovery_dtbo_offset", "image_id"],
            "candidate_unchanged_payload_sha256": candidate_payload_hashes,
            "replacements": target_metadata,
            "cpio_validation": {
                **cpio_summary,
                "candidate_cpio_sha256": sha256_bytes(final_cpio),
                "candidate_ramdisk_sha256": camera.sha256_file(output / "ramdisk.lz4"),
                "non_target_raw_records_identical": True,
                "modules_dep_alias_softdep_identical": True,
            },
            "preserved_module_metadata_record_sha256": preserved_metadata,
            "updated_provider_map": provider_summary,
            "static_abi": candidate_abi,
            "debug_stripping": {
                "operation": "pinned llvm-objcopy-18 --strip-debug",
                "tool": input_summary["llvm_objcopy"],
                "original_sources_preserved": True,
                "per_module": debug_strip_summaries,
            },
            "artifacts": {
                "candidates": {
                    name: {
                        "source_bytes": TARGET_MODULES[path]["source_bytes"],
                        "source_sha256": TARGET_MODULES[path]["source_sha256"],
                        "source_build_id_sha1": TARGET_MODULES[path]["source_build_id"],
                        "packaged_bytes": TARGET_MODULES[path]["candidate_bytes"],
                        "packaged_sha256": TARGET_MODULES[path]["candidate_sha256"],
                        "packaged_build_id_sha1": TARGET_MODULES[path]["candidate_build_id"],
                    }
                    for path, name in ((path, TARGET_MODULES[path]["name"]) for path in TARGET_MODULES)
                },
                "output_files_sha256": copied_hashes,
            },
            "tools": {
                **{key: value for key, value in input_summary.items()
                   if key in {"avbtool", "mkbootimg", "mkbootimg_gki_helper", "unpack_bootimg", "llvm_objcopy", "lz4", "modinfo", "modprobe", "readelf", "python"}},
                "camera_builder_sha256": CAMERA_BUILDER_SHA256,
                "cpio_parser_sha256": CPIO_PARSER_SHA256,
                "boot_image_helper_sha256": IMAGE_HELPER_SHA256,
                "static_preflight_sha256": PREFLIGHT_SHA256,
            },
            "scope_limits": [
                "324 ramdisk modules plus the selected external WLAN are static host ELF/MODVERSIONS checks only.",
                "No kernel module was loaded; no phone, SSH, ADB, firmware, deployment, or power action occurred.",
                "Static 324-module inventory is not the current phone's 325-module runtime inventory.",
                "Successful AVB footer verification does not prove Samsung authentication or bootability.",
            ],
        }
        camera._publish_success_manifest(
            output / "manifest.json",
            (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode(),
        )
        return manifest


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-image", type=Path, default=BASE_IMAGE,
                        help="exact preserved camera RECOVERY image")
    parser.add_argument("--abox5", type=Path, default=TARGET_MODULES["lib/modules/snd-soc-samsung-abox.ko"]["source"],
                        help="exact pinned ABOX5 provider module")
    parser.add_argument("--rainbow", type=Path, default=TARGET_MODULES["lib/modules/rainbow_prince.ko"]["source"],
                        help="exact pinned Rainbow Prince module")
    parser.add_argument("--offloader", type=Path, default=TARGET_MODULES["lib/modules/exynos-usb-audio-offloading.ko"]["source"],
                        help="exact pinned USB offloading module")
    parser.add_argument("--base-symvers", type=Path, default=BASE_SYMVERS,
                        help="exact native-eight 17,283-row Module.symvers")
    parser.add_argument("--abox-symvers", type=Path, default=ABOX_SYMVERS,
                        help="exact candidate ABOX5 28-row modules-only.symvers")
    parser.add_argument("--offloader-symvers", type=Path, default=OFFLOADER_SYMVERS,
                        help="exact rebuilt offloader 5-row modules-only.symvers")
    parser.add_argument("--wlan", type=Path, default=WLAN_MODULE,
                        help="exact selected external Lineage WLAN module")
    parser.add_argument("--mkbootimg", type=Path, default=MKBOOTIMG)
    parser.add_argument("--unpack-bootimg", type=Path, default=UNPACK_BOOTIMG)
    parser.add_argument("--avbtool", type=Path, default=AVBTOOL)
    parser.add_argument("--lz4", type=Path, default=LZ4)
    parser.add_argument("--out-dir", type=Path, required=True,
                        help="new output directory; existing paths are refused")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _argument_parser()
    args = parser.parse_args(argv)
    if sys.flags.isolated != 1 or sys.flags.no_site != 1:
        print("audio coherent recovery package: invoke with python3 -I -S; -B is recommended", file=sys.stderr)
        return 2
    args.candidate_modules = {
        "lib/modules/snd-soc-samsung-abox.ko": args.abox5,
        "lib/modules/rainbow_prince.ko": args.rainbow,
        "lib/modules/exynos-usb-audio-offloading.ko": args.offloader,
    }
    try:
        result = build_candidate(args)
    except (RuntimeError, OSError, UnicodeError, ValueError, subprocess.SubprocessError) as error:
        print(f"audio coherent recovery package: {error}", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
