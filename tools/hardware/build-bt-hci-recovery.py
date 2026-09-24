#!/usr/bin/env python3
"""Package a host-built kernel into the pinned native RECOVERY image.

Host-only: this script never talks to the phone. It refuses to overwrite an
output directory and verifies that only the kernel payload changed from the
current audio-extras recovery image before writing the candidate and manifest.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
BASE_IMAGE = ROOT / "builds/audio-extra-v2-20260922/recovery.img"
DEFAULT_AVBTOOL = ROOT / "tools/avb/avbtool.py"
# Trusted AOSP avbtool 1.4.0 bytes in the project owner's existing local tool
# store. `--avbtool` may relocate these exact bytes, but cannot select a shim.
TRUSTED_AVBTOOL_SHA256 = "5698656733ef5077d62ee30395b5ad34295a0f170fb1ba570026c760ead83782"
MKBOOTIMG_SHA256 = "37d84b3d162e0bc62e36c1f4e1c63c85ea0caa9f29be023eb2f8efe006ad948c"
UNPACK_BOOTIMG_SHA256 = "a9d260978a63bd06a24b6347e7dee8a28ff96639793caea15dff6aa491316308"
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


def git_output(source: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(source), *args],
        capture_output=True, text=True, check=False,
    )
    if result.returncode:
        raise RuntimeError(f"cannot read kernel source identity: {result.stderr.strip()}")
    return result.stdout.strip()


def kernel_build_provenance(source_tree: Path | None, build_output: Path | None,
                            toolchain_tools: list[Path], build_command: str | None) -> dict:
    supplied = (source_tree is not None, build_output is not None, bool(toolchain_tools))
    if any(supplied) and not all(supplied):
        raise RuntimeError("source tree, build output, and at least one toolchain tool must be supplied together")
    if not any(supplied):
        return {"complete": False, "reason": "kernel build provenance arguments were not supplied"}

    source = source_tree.resolve(strict=True)
    output = build_output.resolve(strict=True)
    status = git_output(source, "status", "--porcelain", "--untracked-files=all")
    if status:
        raise RuntimeError("kernel source tree must be clean and committed before packaging")
    source_commit = git_output(source, "rev-parse", "HEAD")
    release_path = output / "include/config/kernel.release"
    config_path = output / ".config"
    symvers_path = output / "Module.symvers"
    built_image = output / "arch/arm64/boot/Image"
    required = (release_path, config_path, symvers_path, built_image)
    if any(not path.is_file() or path.is_symlink() for path in required):
        raise RuntimeError("build output must contain regular kernel.release, .config, Module.symvers, and arm64 Image files")

    tools = []
    for path in toolchain_tools:
        invocation = path.absolute()
        resolved = path.resolve(strict=True)
        if not resolved.is_file():
            raise RuntimeError(f"toolchain input must resolve to a regular executable: {path}")
        version = subprocess.run(
            [str(invocation), "--version"], capture_output=True, text=True, check=False,
        )
        if version.returncode:
            raise RuntimeError(f"cannot record toolchain version for {resolved}")
        tools.append({
            "name": invocation.name,
            "sha256": sha(resolved),
            "version_first_line": (version.stdout or version.stderr).splitlines()[0],
        })
    return {
        "complete": True,
        "source_commit": source_commit,
        "source_dirty": False,
        "kernel_release": release_path.read_text().strip(),
        "config_sha256": sha(config_path),
        "module_symvers_sha256": sha(symvers_path),
        "kernel_image_sha256": sha(built_image),
        "toolchain_tools": tools,
        "build_command": build_command,
    }


def open_trusted_avbtool(avbtool: Path) -> int:
    """Snapshot and seal the verifier bytes, returning an immutable memfd."""
    avbtool = Path(avbtool)
    try:
        before = avbtool.lstat()
        if not stat.S_ISREG(before.st_mode):
            raise RuntimeError(f"trusted avbtool must be a non-symlink regular file: {avbtool}")
        source_fd = os.open(avbtool, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0))
    except OSError as error:
        raise RuntimeError(f"trusted avbtool cannot be opened safely: {avbtool}: {error}") from error
    snapshot_fd = None
    try:
        opened = os.fstat(source_fd)
        if (not stat.S_ISREG(opened.st_mode)
                or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)):
            raise RuntimeError("avbtool path changed during trusted-identity check")
        seal_names = ("F_ADD_SEALS", "F_GET_SEALS", "F_SEAL_SEAL", "F_SEAL_SHRINK",
                      "F_SEAL_GROW", "F_SEAL_WRITE")
        if (not hasattr(os, "memfd_create") or not hasattr(os, "MFD_ALLOW_SEALING")
                or any(not hasattr(fcntl, name) for name in seal_names)):
            raise RuntimeError("this host cannot create a sealed verifier snapshot")
        snapshot_fd = os.memfd_create(
            "s22-trusted-avbtool",
            getattr(os, "MFD_CLOEXEC", 0) | getattr(os, "MFD_ALLOW_SEALING", 0),
        )
        digest = hashlib.sha256()
        while True:
            block = os.read(source_fd, 1024 * 1024)
            if not block:
                break
            digest.update(block)
            offset = 0
            while offset < len(block):
                count = os.write(snapshot_fd, block[offset:])
                if count <= 0:
                    raise RuntimeError("could not complete trusted avbtool snapshot")
                offset += count
        actual = digest.hexdigest()
        if actual != TRUSTED_AVBTOOL_SHA256:
            raise RuntimeError(
                f"avbtool identity mismatch: expected trusted SHA-256 {TRUSTED_AVBTOOL_SHA256}, got {actual}"
            )
        seals = (fcntl.F_SEAL_SEAL | fcntl.F_SEAL_SHRINK
                 | fcntl.F_SEAL_GROW | fcntl.F_SEAL_WRITE)
        fcntl.fcntl(snapshot_fd, fcntl.F_ADD_SEALS, seals)
        if fcntl.fcntl(snapshot_fd, fcntl.F_GET_SEALS) & seals != seals:
            raise RuntimeError("trusted avbtool snapshot could not be made immutable")
        os.lseek(snapshot_fd, 0, os.SEEK_SET)
        os.close(source_fd)
        return snapshot_fd
    except Exception:
        os.close(source_fd)
        if snapshot_fd is not None:
            os.close(snapshot_fd)
        raise


def run_trusted_avbtool(avbtool: Path, *arguments: str, runner=None) -> subprocess.CompletedProcess:
    """Run only the pinned verifier bytes; runner injection is a test seam."""
    fd = open_trusted_avbtool(avbtool)
    try:
        command = [sys.executable, f"/proc/self/fd/{fd}", *(str(arg) for arg in arguments)]
        execute = subprocess.run if runner is None else runner
        return execute(command, check=True, capture_output=True, text=True, pass_fds=(fd,))
    except subprocess.CalledProcessError as error:
        detail = (error.stderr or error.stdout or "avbtool returned nonzero").strip()
        raise RuntimeError(f"trusted avbtool {arguments[0] if arguments else 'command'} failed: {detail}") from error
    finally:
        os.close(fd)


def verify_image(avbtool: Path, image: Path, *, runner=None) -> str:
    """Require the pinned AVB verifier to validate the candidate footer/hash."""
    image = Path(image)
    try:
        metadata = image.lstat()
    except OSError as error:
        raise RuntimeError(f"candidate image is missing or unreadable: {image}: {error}") from error
    if not stat.S_ISREG(metadata.st_mode):
        raise RuntimeError(f"candidate image must be a non-symlink regular file: {image}")
    result = run_trusted_avbtool(avbtool, "verify_image", "--image", image, runner=runner)
    return result.stdout.strip()


def load_unpacker(source: Path):
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
    parser.add_argument("--base-image", type=Path, default=BASE_IMAGE,
                        help="pinned audio-extras recovery base image")
    parser.add_argument("--mkbootimg", type=Path, default=ROOT / "tools/mkbootimg/mkbootimg.py",
                        help="trusted project mkbootimg.py tool")
    parser.add_argument("--unpack-bootimg", type=Path, default=ROOT / "tools/mkbootimg/unpack_bootimg.py",
                        help="trusted project unpack_bootimg.py tool")
    parser.add_argument("--source-tree", type=Path,
                        help="clean committed kernel source tree used for the build")
    parser.add_argument("--build-output", type=Path,
                        help="kernel O-tree containing .config, Module.symvers, and Image")
    parser.add_argument("--toolchain-tool", type=Path, action="append", default=[],
                        help="compiler/linker executable to hash and record; repeat as needed")
    parser.add_argument("--build-command",
                        help="human-readable exact build invocation and environment")
    parser.add_argument(
        "--avbtool", type=Path, default=DEFAULT_AVBTOOL,
        help="pinned avbtool.py path (defaults to tools/avb/avbtool.py)",
    )
    args = parser.parse_args()

    if args.kernel.is_symlink():
        raise SystemExit("kernel input must not be a symlink")
    kernel = args.kernel.resolve(strict=True)
    if args.out_dir.is_symlink():
        raise SystemExit("output directory must not be a symlink")
    out = args.out_dir.resolve(strict=False)
    if not kernel.is_file() or kernel.stat().st_size == 0:
        raise SystemExit("kernel must be a nonempty regular file")
    base_image = args.base_image
    if base_image.is_symlink() or not base_image.is_file() or sha(base_image) != BASE_SHA256:
        raise SystemExit("pinned audio-extras base image is missing or has the wrong hash")
    if not args.avbtool.is_file() or args.avbtool.is_symlink():
        raise SystemExit(f"pinned avbtool.py is missing or is not a regular file: {args.avbtool}")
    for tool_path, expected_hash, label in (
        (args.mkbootimg, MKBOOTIMG_SHA256, "mkbootimg.py"),
        (args.unpack_bootimg, UNPACK_BOOTIMG_SHA256, "unpack_bootimg.py"),
    ):
        if tool_path.is_symlink() or not tool_path.is_file() or sha(tool_path) != expected_hash:
            raise SystemExit(f"trusted {label} is missing or has an unexpected hash: {tool_path}")
    if out.exists():
        raise SystemExit(f"refusing existing output directory: {out}")
    try:
        out.relative_to(ROOT / "builds")
    except ValueError as error:
        raise SystemExit("output directory must be a new child of the repository builds directory") from error
    try:
        provenance = kernel_build_provenance(
            args.source_tree, args.build_output, args.toolchain_tool, args.build_command,
        )
    except (OSError, RuntimeError) as error:
        raise SystemExit(str(error)) from error
    if provenance.get("complete") and provenance["kernel_image_sha256"] != sha(kernel):
        raise SystemExit("build O-tree Image hash differs from the requested kernel payload")

    unpacker = load_unpacker(args.unpack_bootimg)
    with tempfile.TemporaryDirectory(prefix="bt-hci-recovery-") as temp:
        work = Path(temp)
        base_dir = work / "base"
        if not base_image.is_file() or sha(base_image) != BASE_SHA256:
            raise SystemExit("pinned audio-extras base image changed during candidate packaging")
        base_info = unpack(unpacker, base_image, base_dir)
        candidate = work / "recovery.img"
        run(
            sys.executable, str(args.mkbootimg),
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
        run_trusted_avbtool(
            args.avbtool, "add_hash_footer",
            "--image", str(candidate), "--partition_size", str(PARTITION_SIZE),
            "--partition_name", "recovery", "--algorithm", "NONE",
            "--rollback_index", "0", "--salt", SALT,
            "--prop", f"com.android.build.recovery.fingerprint:{FINGERPRINT}",
        )
        if candidate.stat().st_size != PARTITION_SIZE:
            raise SystemExit("candidate size does not exactly match RECOVERY partition")

        # A successful footer creation/info dump is not verification.  Require
        # avbtool's full image verifier before header/payload review or output.
        avb_verification = verify_image(args.avbtool, candidate)

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

        avb_report = run_trusted_avbtool(
            args.avbtool, "info_image", "--image", candidate,
        ).stdout
        out.mkdir()
        image_path = out / "recovery.img"
        kernel_copy = out / "kernel.Image"
        shutil.copyfile(candidate, image_path)
        shutil.copyfile(kernel, kernel_copy)
        base_name = (
            str(base_image.resolve().relative_to(ROOT))
            if base_image.resolve().is_relative_to(ROOT)
            else "builds/audio-extra-v2-20260922/recovery.img"
        )
        report = {
            "phone_access": False,
            "base_image": base_name,
            "base_image_sha256": BASE_SHA256,
            "kernel": str(kernel_copy.relative_to(ROOT)),
            "kernel_sha256": sha(kernel),
            "image": str(image_path.relative_to(ROOT)),
            "image_sha256": sha(image_path),
            "partition_size_bytes": PARTITION_SIZE,
            "header": {field: getattr(candidate_info, field) for field in HEADER_FIELDS},
            "unchanged_payloads": {
                name: sha(candidate_dir / name) for name in ("ramdisk", "dtb", "recovery_dtbo")
            },
            "avb_verify_image": avb_verification,
            "avb_info_image": avb_report,
            "avb_verifier_sha256": TRUSTED_AVBTOOL_SHA256,
            "mkbootimg_sha256": MKBOOTIMG_SHA256,
            "unpack_bootimg_sha256": UNPACK_BOOTIMG_SHA256,
            "avb_note": "algorithm NONE verifies the AVB footer/hash only; it does not establish Samsung authentication or bootability",
            "kernel_build_provenance": provenance,
        }
        (out / "manifest.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
