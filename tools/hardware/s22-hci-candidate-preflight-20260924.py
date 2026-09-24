#!/usr/bin/env python3
"""Host-only provenance and external-module compatibility gate for HCI images.

The gate reads an Android v2 recovery image, its build manifest, and the exact
kernel O-tree. It checks the kernel and unchanged ramdisk payloads, inspects
every ramdisk module, and compares module vermagic and symbol versions with the
candidate kernel build. It never accesses a phone or changes an image.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import struct
import subprocess
import tempfile

EXPECTED_RAMDISK_MODULE_COUNT = 324
REQUIRED_RAMDISK_MODULES = ("btpower.ko", "exynos_tty.ko")
REQUIRED_CONFIG = {
    "CONFIG_BT": "y",
    "CONFIG_BT_HCIUART": "y",
    "CONFIG_BT_HCIUART_QCA": "y",
    "CONFIG_MODVERSIONS": "y",
}
HEADER_MANIFEST_FIELDS = (
    "header_version", "kernel_size", "kernel_load_address",
    "ramdisk_load_address", "tags_load_address", "page_size",
    "recovery_dtbo_size", "recovery_dtbo_offset", "boot_header_size",
    "dtb_size", "dtb_load_address",
)


class GateError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_region(path: Path, offset: int, size: int) -> str:
    if offset < 0 or size < 0:
        raise GateError("negative Android boot image payload offset or size")
    digest = hashlib.sha256()
    remaining = size
    with path.open("rb") as stream:
        stream.seek(offset)
        while remaining:
            block = stream.read(min(1024 * 1024, remaining))
            if not block:
                raise GateError("truncated Android boot image payload")
            digest.update(block)
            remaining -= len(block)
    return digest.hexdigest()


def copy_region(path: Path, offset: int, size: int, destination: Path) -> None:
    remaining = size
    with path.open("rb") as source, destination.open("xb") as target:
        source.seek(offset)
        while remaining:
            block = source.read(min(1024 * 1024, remaining))
            if not block:
                raise GateError("truncated Android boot image payload")
            target.write(block)
            remaining -= len(block)


def align(value: int, page_size: int) -> int:
    return ((value + page_size - 1) // page_size) * page_size


def boot_image_layout(path: Path) -> dict:
    """Parse the Android boot header v2 fields used to locate payloads."""
    size = path.stat().st_size
    with path.open("rb") as stream:
        header = stream.read(1660)
    if len(header) < 1660 or header[:8] != b"ANDROID!":
        raise GateError(f"not a complete Android v2 boot image: {path}")
    values = struct.unpack_from("<9I", header, 8)
    (kernel_size, kernel_addr, ramdisk_size, ramdisk_addr, second_size,
     _second_addr, tags_addr, page_size, header_version) = values
    if header_version != 2:
        raise GateError(f"expected Android boot header v2, got v{header_version}")
    if page_size < 512 or page_size > 65536 or page_size & (page_size - 1):
        raise GateError(f"invalid Android boot page size: {page_size}")
    recovery_dtbo_size, recovery_dtbo_offset, boot_header_size = struct.unpack_from(
        "<IQI", header, 1632
    )
    dtb_size, dtb_load_address = struct.unpack_from("<IQ", header, 1648)
    kernel_offset = page_size
    ramdisk_offset = page_size + align(kernel_size, page_size)
    if kernel_offset + kernel_size > size or ramdisk_offset + ramdisk_size > size:
        raise GateError("kernel or ramdisk extends beyond Android boot image")
    return {
        "boot_magic": "ANDROID!",
        "header_version": header_version,
        "kernel_size": kernel_size,
        "kernel_load_address": kernel_addr,
        "ramdisk_size": ramdisk_size,
        "ramdisk_load_address": ramdisk_addr,
        "second_size": second_size,
        "tags_load_address": tags_addr,
        "page_size": page_size,
        "recovery_dtbo_size": recovery_dtbo_size,
        "recovery_dtbo_offset": recovery_dtbo_offset,
        "boot_header_size": boot_header_size,
        "dtb_size": dtb_size,
        "dtb_load_address": dtb_load_address,
        "kernel_offset": kernel_offset,
        "ramdisk_offset": ramdisk_offset,
    }


def parse_kernel_config(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(errors="strict").splitlines():
        if line.startswith("CONFIG_") and "=" in line:
            key, value = line.split("=", 1)
            if value.startswith('"'):
                try:
                    value = json.loads(value)
                except json.JSONDecodeError as error:
                    raise GateError(f"invalid string in kernel config for {key}") from error
            values[key] = value
        elif line.startswith("# CONFIG_") and line.endswith(" is not set"):
            values[line[2:-11]] = "n"
    return values


def module_vermagic(path: Path, modinfo: str) -> str:
    result = subprocess.run(
        [modinfo, "-F", "vermagic", str(path)],
        capture_output=True, text=True, check=False,
    )
    value = result.stdout.strip()
    if result.returncode or not value:
        detail = (result.stderr or result.stdout or "empty vermagic").strip()
        raise GateError(f"cannot read vermagic from host module {path}: {detail}")
    return value


def module_vermagic_verdict(expected: str, modules: list[dict],
                            expected_count: int = EXPECTED_RAMDISK_MODULE_COUNT) -> dict:
    by_name = {item["name"]: item for item in modules}
    missing = [name for name in REQUIRED_RAMDISK_MODULES if name not in by_name]
    mismatches = [
        {"name": item["name"], "vermagic": item["vermagic"]}
        for item in modules if item["vermagic"] != expected
    ]
    return {
        "compatible": (len(modules) == expected_count and not missing and not mismatches),
        "expected_full_vermagic": expected,
        "module_count": len(modules),
        "expected_module_count": expected_count,
        "missing_required_modules": missing,
        "vermagic_mismatch_count": len(mismatches),
        "vermagic_mismatch_examples": mismatches[:12],
        "required_modules": {
            name: ({"vermagic": by_name[name]["vermagic"],
                    "matches_candidate_kernel": by_name[name]["vermagic"] == expected}
                   if name in by_name else None)
            for name in REQUIRED_RAMDISK_MODULES
        },
    }


def parse_symvers(path: Path) -> dict[str, set[str]]:
    exports: dict[str, set[str]] = defaultdict(set)
    for line_number, line in enumerate(path.read_text(errors="strict").splitlines(), 1):
        columns = line.split()
        if not columns:
            continue
        if len(columns) < 2 or not re.fullmatch(r"0x[0-9a-fA-F]{8}", columns[0]):
            raise GateError(f"malformed Module.symvers row {line_number}")
        exports[columns[1]].add(columns[0].lower())
    if not exports:
        raise GateError(f"no exported symbol versions in {path}")
    return exports


def compare_symbol_versions(imports: list[tuple[str, str]],
                             exports: dict[str, set[str]]) -> dict:
    missing = []
    mismatches = []
    for crc, symbol in imports:
        known = exports.get(symbol)
        if not known:
            missing.append(symbol)
        elif crc.lower() not in known:
            mismatches.append({"symbol": symbol, "module_crc": crc,
                               "candidate_crcs": sorted(known)})
    return {
        "import_count": len(imports),
        "missing_symbol_count": len(missing),
        "crc_mismatch_count": len(mismatches),
        "missing_symbol_examples": missing[:12],
        "crc_mismatch_examples": mismatches[:12],
        "compatible": not missing and not mismatches,
    }


def run_lz4_cpio(ramdisk: Path, cpio_args: list[str], cwd: Path | None = None):
    lz4 = shutil.which("lz4")
    cpio = shutil.which("cpio")
    if not lz4 or not cpio:
        raise GateError("host lz4 and cpio tools are required for ramdisk inspection")
    decoder = subprocess.Popen(
        [lz4, "-dc", str(ramdisk)], stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    assert decoder.stdout is not None
    try:
        result = subprocess.run(
            [cpio, *cpio_args], stdin=decoder.stdout,
            capture_output=True, cwd=cwd, check=False,
        )
    finally:
        decoder.stdout.close()
    decoder_status = decoder.wait()
    if decoder_status or result.returncode:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise GateError(f"cannot inspect compressed ramdisk with lz4/cpio: {detail}")
    return result.stdout


def ramdisk_module_paths(ramdisk: Path) -> list[str]:
    output = run_lz4_cpio(ramdisk, ["-it", "--quiet"])
    try:
        names = output.decode("utf-8", errors="strict").splitlines()
    except UnicodeDecodeError as error:
        raise GateError("ramdisk cpio listing is not UTF-8") from error
    modules = []
    for name in names:
        if not name.startswith("lib/modules/") or not name.endswith(".ko"):
            continue
        path = PurePosixPath(name)
        if (path.is_absolute() or len(path.parts) != 3
                or path.parts[0] != "lib" or path.parts[1] != "modules"
                or not re.fullmatch(r"[A-Za-z0-9_.+-]+\.ko", path.name)):
            raise GateError(f"unsafe or unsupported module path in ramdisk: {name!r}")
        modules.append(name)
    if len(modules) != len(set(modules)):
        raise GateError("duplicate .ko path in ramdisk module inventory")
    return sorted(modules)


def inspect_ramdisk_modules(ramdisk: Path, expected_vermagic: str,
                            symvers_path: Path) -> dict:
    modinfo = shutil.which("modinfo")
    modprobe = shutil.which("modprobe")
    if not modinfo or not modprobe:
        raise GateError("host modinfo and modprobe tools are required for module inspection")
    paths = ramdisk_module_paths(ramdisk)
    if len(paths) != EXPECTED_RAMDISK_MODULE_COUNT:
        raise GateError(
            f"expected {EXPECTED_RAMDISK_MODULE_COUNT} ramdisk modules, found {len(paths)}"
        )
    with tempfile.TemporaryDirectory(prefix="s22-hci-module-audit-") as temporary:
        module_root = Path(temporary)
        run_lz4_cpio(ramdisk, [
            "-id", "--quiet", "--no-absolute-filenames", *paths,
        ], cwd=module_root)
        modules = []
        imports = []
        inventory = hashlib.sha256()
        for relative in paths:
            module_path = module_root / relative
            if module_path.is_symlink() or not module_path.is_file():
                raise GateError(f"ramdisk module was not safely extracted: {relative}")
            vermagic = module_vermagic(module_path, modinfo)
            module_hash = sha256_file(module_path)
            modules.append({"name": PurePosixPath(relative).name,
                            "vermagic": vermagic, "sha256": module_hash})
            inventory.update(f"{relative}\t{module_hash}\t{vermagic}\n".encode())
            result = subprocess.run(
                [modprobe, "--dump-modversions", str(module_path)],
                capture_output=True, text=True, check=False,
            )
            if result.returncode:
                detail = (result.stderr or result.stdout or "modprobe failed").strip()
                raise GateError(f"cannot read imported symbol versions from {relative}: {detail}")
            for line in result.stdout.splitlines():
                fields = line.split()
                if len(fields) < 2 or not re.fullmatch(r"0x[0-9a-fA-F]{8}", fields[0]):
                    raise GateError(f"malformed imported symbol version for {relative}: {line!r}")
                imports.append((fields[0], fields[1]))
        vermagic = module_vermagic_verdict(expected_vermagic, modules)
        abi = compare_symbol_versions(imports, parse_symvers(symvers_path))
        return {
            "module_count": len(modules),
            "module_inventory_sha256": inventory.hexdigest(),
            "vermagic": vermagic,
            "module_abi": abi,
        }


def config_compile_metadata(path: Path) -> dict:
    result = {}
    if not path.is_file():
        return result
    for line in path.read_text(errors="strict").splitlines():
        match = re.match(r'^#define (LINUX_COMPILE_BY|LINUX_COMPILE_HOST|LINUX_COMPILER|UTS_VERSION) "(.*)"$', line)
        if match:
            try:
                result[match[1]] = json.loads('"' + match[2] + '"')
            except json.JSONDecodeError:
                result[match[1]] = match[2]
    return result


def git_value(source: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(source), *arguments],
        capture_output=True, text=True, check=False,
    )
    if result.returncode:
        raise GateError(f"cannot read source provenance from {source}: {result.stderr.strip()}")
    return result.stdout.strip()


def manifest_artifact(root: Path, value: object, field: str) -> Path:
    if not isinstance(value, str) or not value:
        raise GateError(f"manifest {field} path is missing")
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise GateError(f"manifest {field} path must be relative and contained")
    source = root / relative
    if source.is_symlink():
        raise GateError(f"manifest {field} must not be a symlink")
    resolved = source.resolve(strict=True)
    if not resolved.is_relative_to(root):
        raise GateError(f"manifest {field} path escapes artifact root")
    if resolved.is_symlink() or not resolved.is_file():
        raise GateError(f"manifest {field} must resolve to a regular file")
    return resolved


def uts_release(path: Path) -> str:
    match = re.search(r'^#define UTS_RELEASE "([^"\n]+)"$',
                      path.read_text(errors="strict"), re.M)
    if not match:
        raise GateError(f"cannot parse UTS_RELEASE from {path}")
    return match[1]


def inspect_candidate(artifact_root: Path, image_path: Path,
                      manifest_path: Path, o_tree: Path) -> dict:
    artifact_root = artifact_root.resolve(strict=True)
    if image_path.is_symlink() or manifest_path.is_symlink():
        raise GateError("image and manifest must not be symlinks")
    image_path = image_path.resolve(strict=True)
    manifest_path = manifest_path.resolve(strict=True)
    o_tree = o_tree.resolve(strict=True)
    manifest = json.loads(manifest_path.read_text(errors="strict"))
    if manifest.get("phone_access") is not False:
        raise GateError("candidate manifest does not explicitly record phone_access=false")
    manifest_image = manifest_artifact(artifact_root, manifest.get("image"), "image")
    kernel_path = manifest_artifact(artifact_root, manifest.get("kernel"), "kernel")
    base_image = manifest_artifact(artifact_root, manifest.get("base_image"), "base_image")
    if manifest_image != image_path:
        raise GateError("provided candidate image differs from manifest image path")

    image_hash = sha256_file(image_path)
    if image_hash != manifest.get("image_sha256"):
        raise GateError("candidate image SHA-256 differs from manifest")
    base_hash = sha256_file(base_image)
    if base_hash != manifest.get("base_image_sha256"):
        raise GateError("pinned base image SHA-256 differs from manifest")

    image_layout = boot_image_layout(image_path)
    base_layout = boot_image_layout(base_image)
    manifest_header = manifest.get("header")
    if not isinstance(manifest_header, dict):
        raise GateError("manifest boot header record is missing")
    for field in HEADER_MANIFEST_FIELDS:
        if image_layout[field] != manifest_header.get(field):
            raise GateError(f"candidate boot header differs from manifest field {field}")
    kernel_hash = sha256_region(image_path, image_layout["kernel_offset"],
                               image_layout["kernel_size"])
    ramdisk_hash = sha256_region(image_path, image_layout["ramdisk_offset"],
                                 image_layout["ramdisk_size"])
    base_ramdisk_hash = sha256_region(base_image, base_layout["ramdisk_offset"],
                                      base_layout["ramdisk_size"])
    if kernel_hash != manifest.get("kernel_sha256") or kernel_hash != sha256_file(kernel_path):
        raise GateError("embedded candidate kernel does not match manifest/O-tree Image")
    unchanged = manifest.get("unchanged_payloads")
    if not isinstance(unchanged, dict) or ramdisk_hash != unchanged.get("ramdisk"):
        raise GateError("embedded candidate ramdisk differs from manifest payload hash")
    if ramdisk_hash != base_ramdisk_hash:
        raise GateError("candidate ramdisk payload differs from its pinned base image")

    config_path = o_tree / ".config"
    release_path = o_tree / "include/config/kernel.release"
    uts_path = o_tree / "include/generated/utsrelease.h"
    image_o_path = o_tree / "arch/arm64/boot/Image"
    symvers_path = o_tree / "Module.symvers"
    for required in (config_path, release_path, uts_path, image_o_path, symvers_path):
        if not required.is_file():
            raise GateError(f"required O-tree provenance file is missing: {required}")
    if sha256_file(image_o_path) != manifest.get("kernel_sha256"):
        raise GateError("O-tree Image hash differs from manifest kernel hash")

    config = parse_kernel_config(config_path)
    release = release_path.read_text(errors="strict").strip()
    uts = uts_release(uts_path)
    issues = []
    if release != uts:
        issues.append(f"kernel.release {release!r} differs from UTS_RELEASE {uts!r}")
    for key, expected in REQUIRED_CONFIG.items():
        if config.get(key) != expected:
            issues.append(f"kernel config {key} must be {expected}, got {config.get(key)!r}")

    source_link = o_tree / "source"
    if not source_link.exists():
        raise GateError("O-tree source link is missing; build source provenance is unknown")
    source = source_link.resolve(strict=True)
    source_head = git_value(source, "rev-parse", "HEAD")
    source_description = git_value(source, "describe", "--always", "--dirty")
    source_status = git_value(source, "status", "--porcelain", "--untracked-files=all")
    dirty_files = [line[2:].strip() for line in source_status.splitlines() if line]
    if dirty_files:
        issues.append("kernel build source tree is dirty; source revision is not a complete provenance key")

    expected_modules = (
        o_tree / "drivers/bluetooth/btpower.ko",
        o_tree / "drivers/tty/serial/exynos_tty.ko",
    )
    for path in expected_modules:
        if not path.is_file() or path.is_symlink():
            raise GateError(f"candidate O-tree vermagic reference module is missing: {path}")
    modinfo = shutil.which("modinfo")
    if not modinfo:
        raise GateError("host modinfo is required to identify candidate kernel vermagic")
    expected_vermagics = {module_vermagic(path, modinfo) for path in expected_modules}
    if len(expected_vermagics) != 1:
        issues.append("candidate O-tree btpower/exynos_tty module vermagics disagree")
    expected_vermagic = sorted(expected_vermagics)[0]
    if expected_vermagic.split()[0] != release:
        issues.append("candidate O-tree module vermagic release differs from kernel.release")

    with tempfile.TemporaryDirectory(prefix="s22-hci-image-audit-") as temporary:
        ramdisk_path = Path(temporary) / "candidate-ramdisk.lz4"
        copy_region(image_path, image_layout["ramdisk_offset"],
                    image_layout["ramdisk_size"], ramdisk_path)
        modules = inspect_ramdisk_modules(ramdisk_path, expected_vermagic, symvers_path)
    if not modules["vermagic"]["compatible"]:
        issues.append(
            "candidate kernel full vermagic does not match one or more external modules in the unchanged 324-module ramdisk"
        )
    if not modules["module_abi"]["compatible"]:
        issues.append("one or more ramdisk module symbol CRCs do not match candidate Module.symvers")

    report = {
        "schema": "s22-hci-candidate-preflight-20260924/v1",
        "candidate_compatible": not issues,
        "issues": issues,
        "device_access": False,
        "hardware_evidence": "host artifacts only; no live module query or HCI/controller access",
        "image": {
            "path": str(image_path),
            "sha256": image_hash,
            "embedded_kernel_sha256": kernel_hash,
            "embedded_ramdisk_sha256": ramdisk_hash,
            "base_image_sha256": base_hash,
            "base_ramdisk_sha256": base_ramdisk_hash,
            "ramdisk_matches_pinned_base": ramdisk_hash == base_ramdisk_hash,
        },
        "kernel_build": {
            "source_head": source_head,
            "source_description": source_description,
            "source_dirty_files": dirty_files,
            "kernel_release": release,
            "uts_release": uts,
            "config_sha256": sha256_file(config_path),
            "config": {key: config.get(key) for key in (
                "CONFIG_LOCALVERSION", "CONFIG_LOCALVERSION_AUTO", "CONFIG_MODVERSIONS",
                "CONFIG_BT", "CONFIG_BT_HCIUART", "CONFIG_BT_HCIUART_QCA",
            )},
            "compiler": config_compile_metadata(o_tree / "include/generated/compile.h"),
            "image_sha256": sha256_file(image_o_path),
            "module_symvers_sha256": sha256_file(symvers_path),
            "expected_full_vermagic": expected_vermagic,
        },
        "ramdisk_modules": modules,
    }
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-root", type=Path, required=True,
                        help="repository/build artifact root used by relative manifest paths")
    parser.add_argument("--image", type=Path, required=True,
                        help="candidate Android recovery image")
    parser.add_argument("--manifest", type=Path, required=True,
                        help="candidate image manifest")
    parser.add_argument("--o-tree", type=Path, required=True,
                        help="exact kernel output tree used for this image")
    args = parser.parse_args()
    try:
        report = inspect_candidate(args.artifact_root, args.image, args.manifest, args.o_tree)
    except (GateError, OSError, ValueError, json.JSONDecodeError) as error:
        report = {
            "schema": "s22-hci-candidate-preflight-20260924/v1",
            "candidate_compatible": False,
            "issues": [str(error)],
            "device_access": False,
            "hardware_evidence": "host artifacts only; no live module query or HCI/controller access",
        }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["candidate_compatible"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
