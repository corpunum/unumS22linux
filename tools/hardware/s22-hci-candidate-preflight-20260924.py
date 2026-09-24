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
LINEAGE_WLAN_MODULE = (
    "rootfs/wifi-vendor-assets/lineage-23.2-20260915/"
    "vendor_dlkm/lib/modules/wlan.ko"
)
STOCK_WLAN_ALTERNATE = (
    "rootfs/wifi-vendor-assets/vendor_dlkm/lib/modules/wlan.ko"
)
WLAN_SOURCE_PLAN = (
    {
        "path": LINEAGE_WLAN_MODULE,
        "role": "selected Lineage 23.2 module; required in candidate runtime inventory",
        "candidate_required": True,
        "expected_sha256": "cbf8932d079e97006a5b7aae0e1b5acfe65b3e8b5113ab8fa095daa36796738d",
        "activation_evidence": (
            "docs/WIFI_NATIVE.md selects this SHA; tools/hardware/wifi-stage.sh stages it; "
            "tools/hardware/wifi-bringup-once.py inserts PAYLOAD/modules/wlan.ko"
        ),
    },
    {
        "path": STOCK_WLAN_ALTERNATE,
        "role": "stock FYI3 module; known alternate, not selected as active runtime source",
        "candidate_required": False,
        "activation_evidence": (
            "docs/WIFI_NATIVE.md identifies the stock module as ABI-incompatible and says it must not be loaded"
        ),
    },
)
MODULE_LAYOUT_SYMBOL = "module_layout"
PINNED_LOADER_REFERENCE = {
    "repository": "LineageOS/android_kernel_samsung_s5e9925",
    "commit": "4e5c5ad7d950e4de0688b5663965f2075654b2ad",
    "file": "kernel/module.c",
    "functions": {
        "setup_load_info": 3228,
        "check_version": 1316,
        "check_modstruct_version": 1365,
        "same_magic": 1384,
        "check_modinfo": 3283,
        "resolve_symbol": 1469,
    },
    "model_note": (
        "Source-traced host model for the ordinary non-forced loader path, not execution of the kernel loader. "
        "setup_load_info records __versions in info->index.vers; check_modinfo "
        "passes that index as same_magic's has_crcs; with CONFIG_MODVERSIONS, "
        "same_magic compares from the first space onward only when the section "
        "exists. check_version rejects a found CRC mismatch, but warns and "
        "accepts a missing symbol record when a versions section exists. This "
        "model does not enable MODULE_INIT_IGNORE_MODVERSIONS, "
        "MODULE_INIT_IGNORE_VERMAGIC, or force-load behavior."
    ),
}
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


def _after_release(vermagic: str) -> str:
    """Return the C same_magic() suffix beginning at strcspn(" ")."""
    separator = vermagic.find(" ")
    return vermagic[separator:] if separator >= 0 else ""


def pinned_same_magic(module_vermagic: str, kernel_vermagic: str,
                      has_versions: bool, config_modversions: bool) -> bool:
    """Model same_magic() from the pinned kernel/module.c implementation."""
    if config_modversions and has_versions:
        module_vermagic = _after_release(module_vermagic)
        kernel_vermagic = _after_release(kernel_vermagic)
    return module_vermagic == kernel_vermagic


def module_vermagic_check(expected_kernel_vermagic: str, module_vermagic_value: str,
                          versions_section_present: bool | None,
                          config_modversions: bool) -> dict:
    if versions_section_present is None:
        return {
            "loader_compatible": None,
            "comparison_mode": "unknown_versions_section_presence",
            "versions_section_present": None,
            "module_release": module_vermagic_value.split(" ", 1)[0],
            "kernel_release": expected_kernel_vermagic.split(" ", 1)[0],
            "release_prefix_differs": (
                module_vermagic_value.split(" ", 1)[0]
                != expected_kernel_vermagic.split(" ", 1)[0]
            ),
        }
    compare_flags_only = config_modversions and versions_section_present
    return {
        "loader_compatible": pinned_same_magic(
            module_vermagic_value, expected_kernel_vermagic,
            versions_section_present, config_modversions,
        ),
        "comparison_mode": "flags_after_release" if compare_flags_only else "full_vermagic",
        "versions_section_present": versions_section_present,
        "module_release": module_vermagic_value.split(" ", 1)[0],
        "kernel_release": expected_kernel_vermagic.split(" ", 1)[0],
        "release_prefix_differs": (
            module_vermagic_value.split(" ", 1)[0]
            != expected_kernel_vermagic.split(" ", 1)[0]
        ),
        "module_flags": _after_release(module_vermagic_value),
        "kernel_flags": _after_release(expected_kernel_vermagic),
    }


def module_vermagic_verdict(expected: str, modules: list[dict],
                            expected_count: int = EXPECTED_RAMDISK_MODULE_COUNT,
                            config_modversions: bool = True) -> dict:
    by_name = {item["name"]: item for item in modules}
    missing = [name for name in REQUIRED_RAMDISK_MODULES if name not in by_name]
    checks = []
    for item in modules:
        check = module_vermagic_check(
            expected, item["vermagic"], item.get("versions_section_present"),
            config_modversions,
        )
        checks.append({"name": item["name"], **check})
    mismatches = [item for item in checks if item["loader_compatible"] is False]
    unverified = [item for item in checks if item["loader_compatible"] is None]
    return {
        "compatible": (len(modules) == expected_count and not missing
                       and not mismatches and not unverified),
        "loader_reference": PINNED_LOADER_REFERENCE,
        "config_modversions": config_modversions,
        "expected_full_vermagic": expected,
        "module_count": len(modules),
        "expected_module_count": expected_count,
        "missing_required_modules": missing,
        "vermagic_mismatch_count": len(mismatches),
        "vermagic_mismatch_examples": mismatches[:12],
        "versions_presence_unverified_count": len(unverified),
        "versions_presence_unverified_examples": unverified[:12],
        "module_checks": checks,
        "required_modules": {
            name: ({"vermagic": by_name[name]["vermagic"],
                    **module_vermagic_check(
                        expected, by_name[name]["vermagic"],
                        by_name[name].get("versions_section_present"),
                        config_modversions,
                    )}
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
    unknown_crcs = []
    ambiguous_crcs = []
    for crc, symbol in imports:
        known = exports.get(symbol)
        if not known:
            missing.append(symbol)
            continue
        versioned = sorted(value.lower() for value in known if value.lower() != "0x00000000")
        if not versioned:
            # Module.symvers records the export but cannot prove that the
            # runtime kernel has a usable CRC pointer for this symbol.
            unknown_crcs.append({"symbol": symbol, "module_crc": crc,
                                 "candidate_crcs": sorted(known)})
        elif len(set(versioned)) > 1:
            # The loader resolves one provider; Module.symvers alone does not
            # establish which distinct CRC will be returned by find_symbol().
            ambiguous_crcs.append({"symbol": symbol, "module_crc": crc,
                                   "candidate_crcs": sorted(set(versioned))})
        elif crc.lower() != versioned[0]:
            mismatches.append({"symbol": symbol, "module_crc": crc,
                               "candidate_crcs": versioned})
    if missing or mismatches:
        loader_compatible = False
    elif unknown_crcs or ambiguous_crcs:
        loader_compatible = None
    else:
        loader_compatible = True
    return {
        "import_count": len(imports),
        "missing_symbol_count": len(missing),
        "crc_mismatch_count": len(mismatches),
        "unknown_candidate_crc_count": len(unknown_crcs),
        "ambiguous_candidate_crc_count": len(ambiguous_crcs),
        "missing_symbol_examples": missing[:12],
        "crc_mismatch_examples": mismatches[:12],
        "unknown_candidate_crc_examples": unknown_crcs[:12],
        "ambiguous_candidate_crc_examples": ambiguous_crcs[:12],
        "loader_compatible": loader_compatible,
        "evidence_complete": loader_compatible is True,
        "compatible": loader_compatible is True,
    }


def module_symbol_version_verdict(imports: list[tuple[str, str]],
                                  exports: dict[str, set[str]],
                                  versions_section_present: bool,
                                  config_modversions: bool = True) -> dict:
    """Compare one module's recorded imports using the pinned check_version()."""
    if not config_modversions:
        return {
            "loader_compatible": True,
            "evidence_complete": True,
            "mode": "CONFIG_MODVERSIONS_disabled",
            "module_layout_record_present": None,
            "module_layout_candidate_crc_present": None,
            "module_layout_crc_mismatch": False,
            "module_layout_module_crcs": [],
            "module_layout_candidate_crcs": [],
            "versions_section_present": versions_section_present,
            "import_count": len(imports),
            "missing_symbol_count": 0,
            "crc_mismatch_count": 0,
            "unknown_candidate_crc_count": 0,
            "ambiguous_candidate_crc_count": 0,
            "missing_symbol_examples": [],
            "crc_mismatch_examples": [],
            "unknown_candidate_crc_examples": [],
            "ambiguous_candidate_crc_examples": [],
            "missing_module_layout_version_record": False,
            "module_layout_crc_unverified": False,
        }

    layout_records = [(crc, name) for crc, name in imports
                      if name == MODULE_LAYOUT_SYMBOL]
    layout_exported = bool(exports.get(MODULE_LAYOUT_SYMBOL))
    if not versions_section_present:
        # check_version() calls try_to_force_load() when versindex is zero.
        # This preflight never uses the module force-load path.
        return {
            "loader_compatible": False,
            "evidence_complete": False,
            "mode": "versions_section_missing",
            "versions_section_present": False,
            "module_layout_record_present": False,
            "module_layout_candidate_crc_present": layout_exported,
            "module_layout_crc_mismatch": False,
            "module_layout_module_crcs": [],
            "module_layout_candidate_crcs": sorted(exports.get(MODULE_LAYOUT_SYMBOL, set())),
            "import_count": len(imports),
            "missing_symbol_count": 0,
            "crc_mismatch_count": 0,
            "unknown_candidate_crc_count": 0,
            "ambiguous_candidate_crc_count": 0,
            "missing_symbol_examples": [],
            "crc_mismatch_examples": [],
            "unknown_candidate_crc_examples": [],
            "ambiguous_candidate_crc_examples": [],
            "missing_module_layout_version_record": True,
            "module_layout_crc_unverified": False,
        }

    compared = compare_symbol_versions(imports, exports)
    layout_missing = not layout_records
    # check_modstruct_version() BUGs if module_layout is not exported. When it
    # is exported but absent from __versions, check_version() warns and accepts;
    # expose that as loader-compatible but not fully verified evidence.
    loader_compatible = (False if not layout_exported
                         else compared["loader_compatible"])
    evidence_complete = loader_compatible and not layout_missing
    layout_exports = exports.get(MODULE_LAYOUT_SYMBOL, set())
    layout_versioned_exports = {
        value.lower() for value in layout_exports if value.lower() != "0x00000000"
    }
    layout_mismatch = (
        len(layout_versioned_exports) == 1
        and any(crc.lower() not in layout_versioned_exports for crc, _ in layout_records)
    )
    layout_crc_unverified = bool(layout_records) and (
        not layout_versioned_exports or len(layout_versioned_exports) > 1
    )
    return {
        "loader_compatible": loader_compatible,
        "evidence_complete": evidence_complete,
        "mode": "pinned_check_version",
        "versions_section_present": True,
        "module_layout_record_present": not layout_missing,
        "module_layout_candidate_crc_present": layout_exported,
        "module_layout_crc_mismatch": layout_mismatch,
        "module_layout_module_crcs": sorted({crc.lower() for crc, _ in layout_records}),
        "module_layout_candidate_crcs": sorted(layout_exports),
        "module_layout_crc_unverified": layout_crc_unverified,
        "import_count": compared["import_count"],
        "missing_symbol_count": compared["missing_symbol_count"],
        "crc_mismatch_count": compared["crc_mismatch_count"],
        "unknown_candidate_crc_count": compared["unknown_candidate_crc_count"],
        "ambiguous_candidate_crc_count": compared["ambiguous_candidate_crc_count"],
        "missing_symbol_examples": compared["missing_symbol_examples"],
        "crc_mismatch_examples": compared["crc_mismatch_examples"],
        "unknown_candidate_crc_examples": compared["unknown_candidate_crc_examples"],
        "ambiguous_candidate_crc_examples": compared["ambiguous_candidate_crc_examples"],
        "missing_module_layout_version_record": layout_missing,
    }


def _read_exact(stream, offset: int, size: int, file_size: int, path: Path) -> bytes:
    if offset < 0 or size < 0 or offset + size > file_size:
        raise GateError(f"invalid ELF section bounds in module {path}")
    stream.seek(offset)
    data = stream.read(size)
    if len(data) != size:
        raise GateError(f"truncated ELF section data in module {path}")
    return data


def elf_section_names(path: Path) -> list[str]:
    """Read ELF section names without depending on host readelf formatting."""
    file_size = path.stat().st_size
    with path.open("rb") as stream:
        ident = _read_exact(stream, 0, 16, file_size, path)
        if ident[:4] != b"\x7fELF" or ident[4] not in (1, 2) or ident[5] not in (1, 2):
            raise GateError(f"unsupported or malformed ELF module: {path}")
        elf_class = ident[4]
        endian = "<" if ident[5] == 1 else ">"
        header_format = "HHIQQQIHHHHHH" if elf_class == 2 else "HHIIIIIHHHHHH"
        header_size = struct.calcsize(endian + header_format)
        header = struct.unpack(endian + header_format,
                               _read_exact(stream, 16, header_size, file_size, path))
        section_offset = header[5]
        section_entry_size = header[10]
        section_count = header[11]
        string_section_index = header[12]
        section_format = "IIQQQQIIQQ" if elf_class == 2 else "IIIIIIIIII"
        minimum_entry_size = struct.calcsize(endian + section_format)
        if section_entry_size < minimum_entry_size:
            raise GateError(f"invalid ELF section header size in module {path}")

        def read_section(index: int) -> tuple[int, int, int, int]:
            if index < 0 or (section_count and index >= section_count):
                raise GateError(f"invalid ELF section index in module {path}")
            raw = _read_exact(
                stream, section_offset + index * section_entry_size,
                section_entry_size, file_size, path,
            )
            fields = struct.unpack(endian + section_format, raw[:minimum_entry_size])
            return fields[0], fields[4], fields[5], fields[6]

        if section_count == 0:
            # ELF extended section numbering stores e_shnum in section zero.
            section_zero = read_section(0)
            section_count = section_zero[2]
        if string_section_index == 0xFFFF:
            string_section_index = read_section(0)[3]
        if section_count <= 0 or string_section_index >= section_count:
            raise GateError(f"missing ELF section-name table in module {path}")

        # The local reader closes over the adjusted count for extended tables.
        raw = _read_exact(
            stream, section_offset + string_section_index * section_entry_size,
            section_entry_size, file_size, path,
        )
        fields = struct.unpack(endian + section_format, raw[:minimum_entry_size])
        strings = _read_exact(stream, fields[4], fields[5], file_size, path)
        names = []
        for index in range(section_count):
            raw = _read_exact(
                stream, section_offset + index * section_entry_size,
                section_entry_size, file_size, path,
            )
            fields = struct.unpack(endian + section_format, raw[:minimum_entry_size])
            name_offset = fields[0]
            if name_offset >= len(strings):
                raise GateError(f"invalid ELF section-name offset in module {path}")
            end = strings.find(b"\0", name_offset)
            if end < 0:
                raise GateError(f"unterminated ELF section name in module {path}")
            names.append(strings[name_offset:end].decode("ascii", errors="strict"))
        return names


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


def inspect_module_file(module_path: Path, relative: str,
                        exports: dict[str, set[str]],
                        config_modversions: bool,
                        modinfo: str, modprobe: str) -> dict:
    vermagic = module_vermagic(module_path, modinfo)
    versions_section_present = "__versions" in elf_section_names(module_path)
    result = subprocess.run(
        [modprobe, "--dump-modversions", str(module_path)],
        capture_output=True, text=True, check=False,
    )
    if result.returncode:
        detail = (result.stderr or result.stdout or "modprobe failed").strip()
        raise GateError(f"cannot read imported symbol versions from {relative}: {detail}")
    imports = []
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) < 2 or not re.fullmatch(r"0x[0-9a-fA-F]{8}", fields[0]):
            raise GateError(f"malformed imported symbol version for {relative}: {line!r}")
        imports.append((fields[0].lower(), fields[1]))
    imports_digest = hashlib.sha256()
    for crc, symbol in imports:
        imports_digest.update(f"{crc}\t{symbol}\n".encode())
    return {
        "name": PurePosixPath(relative).name,
        "path": relative,
        "vermagic": vermagic,
        "versions_section_present": versions_section_present,
        "symbol_version_record_count": len(imports),
        "symbol_version_records_sha256": imports_digest.hexdigest(),
        "sha256": sha256_file(module_path),
        "symbol_versions": module_symbol_version_verdict(
            imports, exports, versions_section_present, config_modversions,
        ),
    }


def inspect_required_and_alternate_wlan_sources(
        artifact_root: Path, expected_vermagic: str, symvers_path: Path,
        config_modversions: bool) -> dict:
    modinfo = shutil.which("modinfo")
    modprobe = shutil.which("modprobe")
    if not modinfo or not modprobe:
        raise GateError("host modinfo and modprobe tools are required for module inspection")
    exports = parse_symvers(symvers_path)

    def inspect(relative: str, role: str, required: bool,
                expected_sha256: str | None, activation_evidence: str) -> dict:
        source_path = artifact_root / relative
        if not source_path.exists():
            if required:
                raise GateError(f"required selected Lineage WLAN runtime module is missing: {relative}")
            return {
                "available": False,
                "role": role,
                "path": relative,
                "candidate_required_runtime_module": False,
                "loaded_membership": "not queried",
                "expected_sha256": expected_sha256,
                "activation_evidence": activation_evidence,
            }
        if source_path.is_symlink():
            raise GateError(f"WLAN module source must not be a symlink: {relative}")
        resolved = source_path.resolve(strict=True)
        if not resolved.is_relative_to(artifact_root) or not resolved.is_file():
            raise GateError(f"WLAN module source is not a contained regular file: {relative}")
        module = inspect_module_file(
            resolved, relative, exports, config_modversions, modinfo, modprobe,
        )
        vermagic_check = module_vermagic_check(
            expected_vermagic, module["vermagic"],
            module["versions_section_present"], config_modversions,
        )
        symbol_compatible = module["symbol_versions"]["loader_compatible"]
        if vermagic_check["loader_compatible"] is False or symbol_compatible is False:
            loader_compatible = False
        elif (vermagic_check["loader_compatible"] is None
              or symbol_compatible is None):
            loader_compatible = None
        else:
            loader_compatible = True
        return {
            "available": True,
            "role": role,
            "path": relative,
            "candidate_required_runtime_module": required,
            "loaded_membership": "unknown without a live device query",
            "expected_sha256": expected_sha256,
            "sha256_matches_expected": (
                module["sha256"] == expected_sha256 if expected_sha256 else None
            ),
            "activation_evidence": activation_evidence,
            "candidate_vermagic_check": vermagic_check,
            "candidate_loader_compatible": loader_compatible,
            "candidate_evidence_complete": (
                vermagic_check["loader_compatible"] is True
                and module["symbol_versions"]["evidence_complete"]
            ),
            "module": module,
        }

    records = [
        inspect(
            source["path"], source["role"], source["candidate_required"],
            source.get("expected_sha256"), source["activation_evidence"],
        )
        for source in WLAN_SOURCE_PLAN
    ]
    selected = next(item for item in records if item["candidate_required_runtime_module"])
    stock = next(item for item in records if not item["candidate_required_runtime_module"])
    return {
        "candidate_required_runtime_sources": [selected],
        "known_alternate_sources": [stock],
        "loaded_membership_complete": False,
        "note": (
            "The selected Lineage WLAN module is included in the candidate-required "
            "runtime inventory but is staged outside the 324-module ramdisk. The stock "
            "FYI3 copy is recorded as an inactive alternate, not counted as active. "
            "Neither record establishes current /proc/modules membership."
        ),
    }


def inspect_ramdisk_modules(ramdisk: Path, expected_vermagic: str,
                            symvers_path: Path,
                            config_modversions: bool = True) -> dict:
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
        version_checks = []
        exports = parse_symvers(symvers_path)
        inventory = hashlib.sha256()
        for relative in paths:
            module_path = module_root / relative
            if module_path.is_symlink() or not module_path.is_file():
                raise GateError(f"ramdisk module was not safely extracted: {relative}")
            record = inspect_module_file(
                module_path, relative, exports, config_modversions, modinfo, modprobe,
            )
            inventory.update(
                f"{relative}\t{record['sha256']}\t{record['vermagic']}\n".encode()
            )
            modules.append(record)
            version_checks.append({"name": record["name"], **record["symbol_versions"]})
        vermagic = module_vermagic_verdict(
            expected_vermagic, modules, config_modversions=config_modversions,
        )
        missing_symbols = sum(item["missing_symbol_count"] for item in version_checks)
        crc_mismatches = sum(item["crc_mismatch_count"] for item in version_checks)
        missing_versions = sum(not item["versions_section_present"]
                               for item in version_checks)
        missing_layout_records = sum(item["missing_module_layout_version_record"]
                                     for item in version_checks)
        missing_examples = [
            {"module": item["name"], "symbol": symbol}
            for item in version_checks for symbol in item["missing_symbol_examples"]
        ][:12]
        mismatch_examples = [
            {"module": item["name"], **example}
            for item in version_checks for example in item["crc_mismatch_examples"]
        ][:12]
        unknown_crc_examples = [
            {"module": item["name"], **example}
            for item in version_checks for example in item["unknown_candidate_crc_examples"]
        ][:12]
        ambiguous_crc_examples = [
            {"module": item["name"], **example}
            for item in version_checks for example in item["ambiguous_candidate_crc_examples"]
        ][:12]
        version_statuses = [item["loader_compatible"] for item in version_checks]
        if any(status is False for status in version_statuses):
            version_compatible = False
        elif any(status is None for status in version_statuses):
            version_compatible = None
        else:
            version_compatible = True
        abi = {
            "loader_reference": PINNED_LOADER_REFERENCE,
            "config_modversions": config_modversions,
            "compatible": version_compatible,
            "evidence_complete": all(item["evidence_complete"] for item in version_checks),
            "import_count": sum(item["import_count"] for item in version_checks),
            "missing_symbol_count": missing_symbols,
            "crc_mismatch_count": crc_mismatches,
            "unknown_candidate_crc_count": sum(
                item["unknown_candidate_crc_count"] for item in version_checks
            ),
            "ambiguous_candidate_crc_count": sum(
                item["ambiguous_candidate_crc_count"] for item in version_checks
            ),
            "missing_symbol_examples": missing_examples,
            "crc_mismatch_examples": mismatch_examples,
            "unknown_candidate_crc_examples": unknown_crc_examples,
            "ambiguous_candidate_crc_examples": ambiguous_crc_examples,
            "missing_versions_section_count": missing_versions,
            "missing_module_layout_version_record_count": missing_layout_records,
            "module_layout_crc_mismatch_count": sum(
                item["module_layout_crc_mismatch"] for item in version_checks
            ),
            "module_layout_crc_unverified_count": sum(
                item["module_layout_crc_unverified"] for item in version_checks
            ),
            "module_checks": version_checks,
        }
        return {
            "module_count": len(modules),
            "module_inventory_sha256": inventory.hexdigest(),
            "vermagic": vermagic,
            "module_abi": abi,
            "source_completeness": {
                "ramdisk_modules_available": True,
                "ramdisk_module_count": len(modules),
                "ramdisk_set_complete": True,
                "loaded_module_set_complete": False,
                "loaded_module_count": None,
                "other_boot_or_runtime_module_sources": "unknown/not inventoried by this host preflight",
                "note": (
                    "This is the complete .ko set found in the candidate ramdisk only. "
                    "It does not prove the full boot/runtime module source closure or "
                    "equal the separate 325-entry live /proc/modules observation."
                ),
            },
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


def manifest_source_commit_issue(manifest: dict, source_head: str) -> str | None:
    build_provenance = manifest.get("kernel_build_provenance")
    if not isinstance(build_provenance, dict):
        return None
    manifest_commit = build_provenance.get("source_commit")
    if manifest_commit is None:
        if build_provenance.get("complete") is True:
            return "candidate manifest claims complete build provenance without a source commit"
        return None
    if not isinstance(manifest_commit, str) or not manifest_commit:
        return "candidate manifest kernel build source commit is malformed"
    if manifest_commit != source_head:
        return (
            "candidate manifest kernel build source commit "
            f"{manifest_commit} differs from O-tree source HEAD {source_head}"
        )
    return None


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
    provenance_issues = []
    loader_issues = []
    if release != uts:
        provenance_issues.append(f"kernel.release {release!r} differs from UTS_RELEASE {uts!r}")
    for key, expected in REQUIRED_CONFIG.items():
        if config.get(key) != expected:
            provenance_issues.append(f"kernel config {key} must be {expected}, got {config.get(key)!r}")

    source_link = o_tree / "source"
    if not source_link.exists():
        raise GateError("O-tree source link is missing; build source provenance is unknown")
    source = source_link.resolve(strict=True)
    source_head = git_value(source, "rev-parse", "HEAD")
    source_description = git_value(source, "describe", "--always", "--dirty")
    source_status = git_value(source, "status", "--porcelain", "--untracked-files=all")
    dirty_files = [line[2:].strip() for line in source_status.splitlines() if line]
    manifest_source_issue = manifest_source_commit_issue(manifest, source_head)
    if manifest_source_issue:
        provenance_issues.append(manifest_source_issue)
    if dirty_files:
        provenance_issues.append(
            "kernel build source tree is dirty; source revision is not a complete provenance key"
        )

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
        provenance_issues.append("candidate O-tree btpower/exynos_tty module vermagics disagree")
    expected_vermagic = sorted(expected_vermagics)[0]
    if expected_vermagic.split()[0] != release:
        provenance_issues.append("candidate O-tree module vermagic release differs from kernel.release")

    with tempfile.TemporaryDirectory(prefix="s22-hci-image-audit-") as temporary:
        ramdisk_path = Path(temporary) / "candidate-ramdisk.lz4"
        copy_region(image_path, image_layout["ramdisk_offset"],
                    image_layout["ramdisk_size"], ramdisk_path)
        modules = inspect_ramdisk_modules(
            ramdisk_path, expected_vermagic, symvers_path,
            config_modversions=config.get("CONFIG_MODVERSIONS") == "y",
        )
    wlan_sources = inspect_required_and_alternate_wlan_sources(
        artifact_root, expected_vermagic, symvers_path,
        config_modversions=config.get("CONFIG_MODVERSIONS") == "y",
    )
    modules["wlan_runtime_sources"] = wlan_sources
    selected_wlan = wlan_sources["candidate_required_runtime_sources"][0]
    if selected_wlan["sha256_matches_expected"] is not True:
        provenance_issues.append(
            "selected Lineage WLAN runtime module SHA-256 differs from the recorded asset identity"
        )
    candidate_runtime_inventory_count = (
        modules["module_count"] + int(selected_wlan["available"])
    )
    modules["source_completeness"] = {
        "ramdisk_modules_available": True,
        "ramdisk_module_count": modules["module_count"],
        "ramdisk_set_complete": True,
        "candidate_required_wlan_module_available": selected_wlan["available"],
        "candidate_required_runtime_module_count": candidate_runtime_inventory_count,
        "known_alternate_module_source_count": len(wlan_sources["known_alternate_sources"]),
        "declared_candidate_required_sources_enumerated": True,
        "candidate_runtime_source_closure_verified": False,
        "loaded_module_set_complete": False,
        "loaded_module_count": None,
        "actual_loaded_membership": "unknown; no device query in this host preflight",
        "other_boot_or_runtime_module_sources": "unknown/not independently inventoried",
        "coverage_issues": [
            "candidate boot /proc/modules membership is not established by host artifacts",
            "other boot/runtime sources beyond the ramdisk and selected WLAN payload remain unknown",
        ],
        "note": (
            "The ramdisk contributes 324 known .ko files. The selected Lineage "
            "20260915 wlan.ko is a separate candidate-required runtime source, so "
            "the known candidate inventory is 325 modules when available. The stock "
            "FYI3 wlan.ko is a known alternate and is not counted active. This does "
            "not prove which modules are loaded by a candidate boot."
        ),
    }
    if not modules["vermagic"]["compatible"]:
        loader_issues.append(
            "one or more ramdisk module vermagic checks fail or lack version-section evidence"
        )
    if not modules["module_abi"]["compatible"]:
        loader_issues.append(
            "one or more ramdisk modules fail or cannot be verified against pinned module_layout/imported-symbol rules"
        )
    if not modules["module_abi"]["evidence_complete"]:
        loader_issues.append(
            "one or more ramdisk modules lack complete __versions/module_layout CRC evidence"
        )
    if selected_wlan["candidate_loader_compatible"] is False:
        loader_issues.append(
            "selected Lineage WLAN runtime module fails the pinned loader compatibility checks"
        )
    elif selected_wlan["candidate_loader_compatible"] is None:
        loader_issues.append(
            "selected Lineage WLAN runtime module compatibility cannot be established"
        )
    if not selected_wlan["candidate_evidence_complete"]:
        loader_issues.append(
            "selected Lineage WLAN runtime module lacks complete version CRC evidence"
        )
    issues = provenance_issues + loader_issues
    version_compatible = modules["module_abi"]["compatible"]
    if not modules["vermagic"]["compatible"] or version_compatible is False:
        loader_compatible = False
    elif version_compatible is None:
        loader_compatible = None
    else:
        loader_compatible = True
    selected_wlan_compatible = selected_wlan["candidate_loader_compatible"]
    if loader_compatible is False or selected_wlan_compatible is False:
        loader_compatible = False
    elif loader_compatible is None or selected_wlan_compatible is None:
        loader_compatible = None
    loader_evidence_complete = (
        modules["vermagic"]["versions_presence_unverified_count"] == 0
        and modules["module_abi"]["evidence_complete"]
        and selected_wlan["candidate_evidence_complete"]
    )
    modules["candidate_required_runtime_summary"] = {
        "module_count": candidate_runtime_inventory_count,
        "ramdisk_module_count": modules["module_count"],
        "selected_external_module_count": 1,
        "symbol_version_record_count": (
            modules["module_abi"]["import_count"]
            + selected_wlan["module"]["symbol_version_record_count"]
        ),
        "module_layout_version_records_present_count": (
            sum(item["module_layout_record_present"] is True
                for item in modules["module_abi"]["module_checks"])
            + int(selected_wlan["module"]["symbol_versions"]["module_layout_record_present"] is True)
        ),
        "loader_compatible": loader_compatible,
        "version_evidence_complete": loader_evidence_complete,
        "live_loaded_membership": "unknown; no device query",
    }

    report = {
        "schema": "s22-hci-candidate-preflight-20260924/v2",
        "candidate_compatible": not issues,
        "issues": issues,
        "verdicts": {
            "loader_compatibility": {
                "compatible": loader_compatible,
                "evidence_complete": loader_evidence_complete,
                "scope": "324 candidate ramdisk modules plus the selected candidate-required Lineage WLAN module",
                "complete_loaded_module_set": False,
                "loaded_module_membership": "unknown; no live device query",
                "issues": loader_issues,
                "reference": PINNED_LOADER_REFERENCE,
            },
            "source_artifact_provenance": {
                "compatible": not provenance_issues,
                "issues": provenance_issues,
            },
            "operational_release_assumptions": {
                "status": "not_evaluated_by_module_preflight",
                "compatible": None,
                "note": (
                    "Boot/recovery helper release-name assumptions and rollback behavior "
                    "require the separate operational helper audit."
                ),
            },
        },
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
        "module_inventory": modules["source_completeness"],
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
        error_text = str(error)
        report = {
            "schema": "s22-hci-candidate-preflight-20260924/v2",
            "candidate_compatible": False,
            "issues": [error_text],
            "verdicts": {
                "loader_compatibility": {
                    "compatible": None,
                    "evidence_complete": False,
                    "issues": ["not evaluated because artifact inspection did not complete"],
                    "reference": PINNED_LOADER_REFERENCE,
                },
                "source_artifact_provenance": {
                    "compatible": False,
                    "issues": [error_text],
                },
                "operational_release_assumptions": {
                    "status": "not_evaluated_by_module_preflight",
                    "compatible": None,
                },
            },
            "module_inventory": {
                "ramdisk_modules_available": False,
                "ramdisk_set_complete": False,
                "loaded_module_set_complete": False,
                "loaded_module_count": None,
                "other_boot_or_runtime_module_sources": "unknown",
            },
            "device_access": False,
            "hardware_evidence": "host artifacts only; no live module query or HCI/controller access",
        }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["candidate_compatible"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
