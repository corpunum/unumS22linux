#!/usr/bin/env python3
"""Package a host-built kernel into the pinned native RECOVERY image.

Host-only: this script never talks to the phone. It refuses to overwrite an
output directory and verifies that only the kernel payload changed from the
current audio-extras recovery image before writing the candidate and manifest.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
BASE_IMAGE = ROOT / "builds/audio-extra-v2-20260922/recovery.img"
BASE_SHA256 = "758fc9d30491e17b7c829a89d338ba69476efa15a1280deb8a1b9b8009687f4b"
PARTITION_SIZE = 100663296
FINGERPRINT = "samsung/lineage_r0s/r0s:16/BP4A.251205.006/4a67c928b4:userdebug/release-keys"
SALT = "708474da33de9afafcd1835e6f4cf6f9b6e9451ad37748e95d322678250b69b7"
HEADER_FIELDS = (
    "boot_magic", "header_version", "kernel_size", "kernel_load_address",
    "ramdisk_load_address", "tags_load_address", "page_size", "os_version",
    "os_patch_level", "cmdline", "recovery_dtbo_size", "dtb_size",
    "dtb_load_address", "recovery_dtbo_offset", "boot_header_size",
)


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def run(*args: str) -> None:
    subprocess.run(list(args), check=True)


def load_unpacker():
    source = ROOT / "tools/mkbootimg/unpack_bootimg.py"
    spec = importlib.util.spec_from_file_location("bt_hci_unpacker", source)
    if not spec or not spec.loader:
        raise RuntimeError("cannot load pinned boot image unpacker")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def unpack(unpacker, image: Path, out: Path):
    out.mkdir()
    with image.open("rb") as stream:
        return unpacker.unpack_boot_image(stream, str(out))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kernel", required=True, type=Path, help="built arm64 Image")
    parser.add_argument("--out-dir", required=True, type=Path, help="new candidate output directory")
    args = parser.parse_args()

    if args.kernel.is_symlink():
        raise SystemExit("kernel input must not be a symlink")
    kernel = args.kernel.resolve(strict=True)
    out = args.out_dir.resolve(strict=False)
    if not kernel.is_file() or kernel.stat().st_size == 0:
        raise SystemExit("kernel must be a nonempty regular file")
    if not BASE_IMAGE.is_file() or sha(BASE_IMAGE) != BASE_SHA256:
        raise SystemExit("pinned audio-extras base image is missing or has the wrong hash")
    if out.exists():
        raise SystemExit(f"refusing existing output directory: {out}")
    try:
        out.relative_to(ROOT / "builds")
    except ValueError as error:
        raise SystemExit("output directory must be a new child of the repository builds directory") from error

    unpacker = load_unpacker()
    with tempfile.TemporaryDirectory(prefix="bt-hci-recovery-") as temp:
        work = Path(temp)
        base_dir = work / "base"
        base_info = unpack(unpacker, BASE_IMAGE, base_dir)
        candidate = work / "recovery.img"
        run(
            sys.executable, str(ROOT / "tools/mkbootimg/mkbootimg.py"),
            "--kernel", str(kernel),
            "--ramdisk", str(base_dir / "ramdisk"),
            "--dtb", str(base_dir / "dtb"),
            "--recovery_dtbo", str(base_dir / "recovery_dtbo"),
            "--output", str(candidate),
            "--base", "0x0", "--kernel_offset", "0x10008000",
            "--ramdisk_offset", "0x11000000", "--second_offset", "0x0",
            "--tags_offset", "0x10000100", "--dtb_offset", "0x11f00000",
            "--pagesize", "2048", "--header_version", "2",
            "--os_version", "16.0.0", "--os_patch_level", "2026-09",
            "--board", "", "--cmdline", " bootconfig",
        )
        run(
            sys.executable, str(ROOT / "tools/avb/avbtool.py"), "add_hash_footer",
            "--image", str(candidate), "--partition_size", str(PARTITION_SIZE),
            "--partition_name", "recovery", "--algorithm", "NONE",
            "--rollback_index", "0", "--salt", SALT,
            "--prop", f"com.android.build.recovery.fingerprint:{FINGERPRINT}",
        )
        if candidate.stat().st_size != PARTITION_SIZE:
            raise SystemExit("candidate size does not exactly match RECOVERY partition")

        candidate_dir = work / "candidate"
        candidate_info = unpack(unpacker, candidate, candidate_dir)
        for field in HEADER_FIELDS:
            if not hasattr(base_info, field) or not hasattr(candidate_info, field):
                raise SystemExit(f"unpacker omitted required boot header field: {field}")
            if field in ("kernel_size", "recovery_dtbo_offset"):
                continue  # kernel replacement changes these derived fields
            if getattr(base_info, field) != getattr(candidate_info, field):
                raise SystemExit(f"unexpected boot header change: {field}")
        if (candidate_dir / "kernel").read_bytes() != kernel.read_bytes():
            raise SystemExit("unpacked kernel differs from the requested kernel payload")
        for name in ("ramdisk", "dtb", "recovery_dtbo"):
            if sha(base_dir / name) != sha(candidate_dir / name):
                raise SystemExit(f"candidate changed the base {name} payload")

        avb_report = subprocess.run(
            [sys.executable, str(ROOT / "tools/avb/avbtool.py"), "info_image", "--image", str(candidate)],
            check=True, capture_output=True, text=True,
        ).stdout
        out.mkdir()
        image_path = out / "recovery.img"
        shutil.copyfile(candidate, image_path)
        report = {
            "phone_access": False,
            "base_image": str(BASE_IMAGE.relative_to(ROOT)),
            "base_image_sha256": BASE_SHA256,
            "kernel": str(kernel.relative_to(ROOT)) if kernel.is_relative_to(ROOT) else "external input",
            "kernel_sha256": sha(kernel),
            "image": str(image_path.relative_to(ROOT)),
            "image_sha256": sha(image_path),
            "partition_size_bytes": PARTITION_SIZE,
            "header": {field: getattr(candidate_info, field) for field in HEADER_FIELDS},
            "unchanged_payloads": {
                name: sha(candidate_dir / name) for name in ("ramdisk", "dtb", "recovery_dtbo")
            },
            "avb_info": avb_report,
        }
        (out / "manifest.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
