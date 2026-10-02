#!/usr/bin/env python3
"""Build a distinct, host-only AArch64 QCA bridge candidate.

This helper never stages, uploads, executes, or authorizes the artifact. The
previous trial runner and its consumed trial identity remain separately pinned
to their original source and artifact.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import stat
import struct
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[2]
PINNED_SOURCE_COMMIT = "79c539ea55a1663e946ed881606232519873fd93"
TRUSTED_ROOT = Path("/home/corpunum/s22-linux")
BUILD_ROOT = TRUSTED_ROOT / "builds"
OUTPUT_DIR = BUILD_ROOT / "bt-native-transport-candidate-20261002"
ARTIFACT_NAME = "bt-qca6490-hci-bridge-probe-native-20261002"
ARTIFACT_PATH = OUTPUT_DIR / ARTIFACT_NAME
LEGACY_ARTIFACT_PATH = (
    TRUSTED_ROOT / "builds/bt-plain-h4-20260927/bt-qca6490-hci-bridge-probe"
)

COMPILER = Path("/usr/bin/aarch64-linux-gnu-gcc-13")
COMPILER_SHA256 = "cd90adc7801f4595267f61a5d25bd3a0c6beb2f9f1f107ab919a97a12972dc9a"
COMPILER_VERSION = "aarch64-linux-gnu-gcc-13 (Ubuntu 13.3.0-6ubuntu2~24.04.1) 13.3.0"
COMPILER_TARGET = "aarch64-linux-gnu"
COMPILER_MAJOR = "13"

# The existing static-link configuration is pinned, as are the runtime inputs
# selected by that compiler. No output, source, or private header is copied to
# the repository.
STATIC_RUNTIME_SHA256 = {
    "crt1.o": "332a510b4afece51376517d3c9b3d5a210e27c38a73cf946bf0daac3dfc4e724",
    "crti.o": "93bb05d2d87f3464fd89a70d26d8ed29f0797e4b97a60786a2724a7e3ffbeba5",
    "crtn.o": "1c741c1f9499461b6f1e531e21fc5f86a216cc5d0c49d18bdcf97d7d922342d5",
    "crtbeginT.o": "772152265b97c58d4d0df6ec4ecbf27265b1ec62bfdcb6edcef7400854cc9927",
    "crtend.o": "3da9c42040eca63e3432014ee4b0a98a24ea1f7c4a33e1b40c2b02cc7a15c208",
    "libc.a": "84d7ae225e8eafaee13344c74ff19bc42189f68a6c704a979ccb0924902fc996",
    "libgcc.a": "4bc354241987926aa0489ba1d925e8b10356556323307944f08209f370a28ad6",
    "libgcc_eh.a": "ba85797b21e8eb241afccce977b0b2ca607a9764e3ae15bb966415824a8ee8a4",
}
COMPILER_COMPONENTS = {
    "/usr/libexec/gcc-cross/aarch64-linux-gnu/13/cc1":
        "a7f69616cf8ddbf5c52a5b61bc4c11f504ed9421e282e9083f0a7a22a3268dad",
    "/usr/libexec/gcc-cross/aarch64-linux-gnu/13/collect2":
        "9cf2eeb7b62183cd18107e7e2645dea6d3545e803a386569f24e98265ba1d79c",
    "/usr/bin/aarch64-linux-gnu-as":
        "a315904827ba6f80ad0b58ac3a4672f56a76e639cec2d6833f80b475a28445a3",
    "/usr/bin/aarch64-linux-gnu-ld.bfd":
        "7c903ac277dd1f5c4397277db865f12d8239fe45782f71afbeb3d42180a4b1ae",
}
COMPILER_PROGRAMS = {
    "cc1": "/usr/libexec/gcc-cross/aarch64-linux-gnu/13/cc1",
    "collect2": "/usr/libexec/gcc-cross/aarch64-linux-gnu/13/collect2",
    "as": "/usr/bin/aarch64-linux-gnu-as",
    "ld": "/usr/bin/aarch64-linux-gnu-ld.bfd",
}

PUBLIC_INPUT_SHA256 = {
    "tools/hardware/bt-qca6490-hci-bridge-probe.c":
        "3324cba16c421254c6e0f64d1f26b422e72ba31189a25d8372957248672410b3",
    "tools/hardware/bt-h4-ibs-bridge.c":
        "32bfebfc96864b6f5150195518fc7da996f62b7b21311d79c9bfea2725f9b1a1",
    "tools/hardware/bt-qca6490-runtime-reset.c":
        "337fb3b576a6dccce0a520daf9628a02a9842c5ffa97f16c1f2230351b516b5b",
    "tools/hardware/bt-qca6490-nvm-capture.c":
        "16e4759f22a5932d29a892e3f32f4339d0ab53807aa9f6f314b3e59afdbe8202",
    "tools/hardware/bt-qca6490-patch-capture.c":
        "ac24c9334a4371ecf3ba6c47d80499ec40da5081b8f70a311389c931c99e4bc1",
    "tools/hardware/bt-qca6490-baud-probe.c":
        "655a097731f50417c8012a7ebd0ce1ad9da5a76d6180c38d05f5b3d4f6af843b",
    "tools/hardware/bt-qca6490-patch-version-probe.c":
        "a32ed1bddc52000a7d36ceee032d55c45a978788868f47c3f0aa3a23ca305984",
    "tools/hardware/bt-version-transport-probe.c":
        "b3a9a346facc24845655fa63c020687efc30ffd44c2a0a9ae7bf8ed149ec58a9",
}

PRIVATE_INPUTS = {
    "qca_patch_profile": (
        TRUSTED_ROOT / "builds/bt-patch-20260922/qca-patch-private.h",
        "de1ef56689bbc1f0da3dabe41786561d6949f1c3fb8d5f4deb02f3b9c99e5824",
    ),
    "runtime_nvm_profile": (
        TRUSTED_ROOT / "builds/bt-runtime-nvm-20260922/s22_nvm_payload.h",
        "989eddb0975569255ae9096255b083dac5a6447137b3adfefa1b54b88416b215",
    ),
}

LEGACY_RUNNER = "tools/hardware/run-bt-hci-bridge-once.py"
LEGACY_RUNNER_SHA256 = "26bbe8bdb5ff687de29d5ce67f17d58cfa460a48fe9fc5be9043dccd54cf9253"
LEGACY_BRIDGE_SHA256 = "476f148246dfac8330f7ade2790627b64a38ee7b1cb4f713934b6d438864a7a4"
LEGACY_ARTIFACT_SHA256 = "f4ba76613e1339314898ebbf067338d846ed9ee231907a44306b82f54f2f1684"
LEGACY_ARTIFACT_BUILD_ID = "a5be9451d95335ae2a5d292d721a766208f55a87"
TRIAL_EVIDENCE = "evidence/s22-hardware-continuation-20260927.json"
TRIAL_EVIDENCE_SHA256 = "eb8f1963049cb4cdbca7236169bbdc91f2c929227ea995bdbb310972399518dc"
TRIAL_ID = "bt-hci-plain-h4-20260927"

MIN_AVAILABLE_MEMORY = 8 * 1024**3
MIN_FREE_DISK = 16 * 1024**3
MAX_ARTIFACT_BYTES = 32 * 1024**2
BUILD_TIMEOUT_SECONDS = 180
MAIN_SOURCE = "tools/hardware/bt-qca6490-hci-bridge-probe.c"
PRIVATE_INCLUDE_ROOT = TRUSTED_ROOT / "tools/hardware"

BLOCKED_ENV_NAMES = {
    "AS", "AR", "CC", "CFLAGS", "C_INCLUDE_PATH", "CPLUS_INCLUDE_PATH",
    "COMPILER_PATH", "CPATH", "CPP", "CPPFLAGS", "DEPENDENCIES_OUTPUT",
    "GCC_COMPARE_DEBUG", "GCC_DRIVER_SELF_SPECS", "GCC_EXEC_PREFIX", "GNUMAKEFLAGS", "LD",
    "LD_LIBRARY_PATH", "LD_PRELOAD", "LDFLAGS", "LIBRARY_PATH", "MAKEFLAGS",
    "MAKEOVERRIDES", "MFLAGS", "NM", "OBJCOPY", "OBJDUMP", "RANLIB",
    "SUNPRO_DEPENDENCIES", "TMPDIR",
}
SAFE_CHILD_ENV = {
    "PATH": "/usr/bin:/bin",
    "LANG": "C",
    "LC_ALL": "C",
}


class GateError(RuntimeError):
    """A bounded input or artifact gate refused the requested host build."""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise GateError(message)


def clean_git_env() -> dict[str, str]:
    return dict(SAFE_CHILD_ENV)


def git_output(*args: str) -> subprocess.CompletedProcess[str]:
    command = ["/usr/bin/git", "--no-pager", "-C", str(ROOT), *args]
    return subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        env=clean_git_env(),
        timeout=10,
    )


def check_environment(environ: dict[str, str] | None = None) -> None:
    environ = os.environ if environ is None else environ
    blocked = sorted(
        name for name in environ
        if name in BLOCKED_ENV_NAMES
        or name.startswith(("KCONFIG_", "KBUILD_"))
        or (name.startswith("GIT_") and name != "GIT_PAGER")
    )
    require(not blocked, "unsafe environment variable set: " + ",".join(blocked))


def regular_file(path: Path, label: str) -> os.stat_result:
    try:
        info = path.lstat()
    except OSError as exc:
        raise GateError(label + " missing or unreadable") from exc
    require(stat.S_ISREG(info.st_mode), label + " must be a regular non-symlink file")
    return info


def verify_hash_file(path: Path, expected: str, label: str) -> str:
    regular_file(path, label)
    actual = sha256_file(path)
    require(actual == expected, label + " fingerprint mismatch")
    return actual


def verify_bytes_hash(data: bytes, expected: str, label: str) -> str:
    actual = sha256_bytes(data)
    require(actual == expected, label + " fingerprint mismatch")
    return actual


def verify_git_source_tree() -> str:
    head = git_output("rev-parse", "HEAD")
    require(head.returncode == 0, "cannot identify source worktree HEAD")
    commit = head.stdout.strip()
    ancestor = git_output("merge-base", "--is-ancestor", PINNED_SOURCE_COMMIT, "HEAD")
    require(ancestor.returncode == 0, "pinned source commit is not an ancestor")

    paths = list(PUBLIC_INPUT_SHA256)
    diff = git_output("diff", "--quiet", PINNED_SOURCE_COMMIT, "HEAD", "--", *paths)
    require(diff.returncode == 0, "pinned public sources changed after source commit")
    working = git_output("diff", "--quiet", "--", *paths)
    staged = git_output("diff", "--cached", "--quiet", "--", *paths)
    require(working.returncode == 0 and staged.returncode == 0,
            "public source worktree has uncommitted changes")
    tracked = git_output("ls-files", "--error-unmatch", *paths)
    require(tracked.returncode == 0, "a pinned public source is not tracked")
    for relative, expected in PUBLIC_INPUT_SHA256.items():
        verify_hash_file(ROOT / relative, expected, relative)
    return commit


def verify_legacy_trial_guards() -> dict:
    runner_path = ROOT / LEGACY_RUNNER
    runner_sha = verify_hash_file(runner_path, LEGACY_RUNNER_SHA256, "legacy runner")
    runner_text = runner_path.read_text(encoding="utf-8")
    require(LEGACY_BRIDGE_SHA256 in runner_text and
            LEGACY_ARTIFACT_SHA256 in runner_text and
            LEGACY_ARTIFACT_BUILD_ID in runner_text,
            "legacy source/artifact pins are not intact")

    evidence_path = ROOT / TRIAL_EVIDENCE
    evidence_sha = verify_hash_file(evidence_path, TRIAL_EVIDENCE_SHA256,
                                    "consumed trial evidence")
    try:
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        trial = evidence["bluetooth_after_transport_fix"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise GateError("consumed trial evidence cannot be validated") from exc
    require(trial.get("trial_id") == TRIAL_ID and
            trial.get("attempts_for_this_identity") == 1 and
            trial.get("durable_guard_outcome") == "success" and
            trial.get("artifact_sha256") == LEGACY_ARTIFACT_SHA256 and
            trial.get("artifact_gnu_build_id") == LEGACY_ARTIFACT_BUILD_ID,
            "consumed trial identity/receipt differs from its frozen record")
    require(ARTIFACT_PATH != LEGACY_ARTIFACT_PATH and
            OUTPUT_DIR != LEGACY_ARTIFACT_PATH.parent,
            "candidate output collides with the old trial artifact")
    return {
        "legacy_runner_sha256": runner_sha,
        "legacy_source_pin_sha256": LEGACY_BRIDGE_SHA256,
        "legacy_artifact_sha256": LEGACY_ARTIFACT_SHA256,
        "legacy_artifact_build_id": LEGACY_ARTIFACT_BUILD_ID,
        "trial_identity": TRIAL_ID,
        "trial_attempts_preserved": 1,
        "trial_receipt_sha256": evidence_sha,
        "trial_receipt_unchanged": True,
        "candidate_artifact_is_distinct": True,
    }


def verify_private_inputs() -> list[dict[str, str]]:
    result = []
    for name, (path, expected) in PRIVATE_INPUTS.items():
        info = regular_file(path, name)
        require(info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o600,
                name + " ownership or permissions changed")
        result.append({"id": name, "sha256": verify_hash_file(path, expected, name)})
    return result


def run_compiler_query(*args: str) -> str:
    result = subprocess.run(
        [str(COMPILER), *args],
        check=False,
        capture_output=True,
        text=True,
        env=clean_git_env(),
        timeout=10,
    )
    require(result.returncode == 0, "pinned cross-compiler query failed")
    return result.stdout.strip()


def compiler_identity() -> dict:
    verify_hash_file(COMPILER, COMPILER_SHA256, "cross-compiler")
    version = run_compiler_query("--version").splitlines()[0]
    target = run_compiler_query("-dumpmachine")
    major = run_compiler_query("-dumpversion")
    require(version == COMPILER_VERSION and target == COMPILER_TARGET and
            major == COMPILER_MAJOR, "cross-compiler version or target mismatch")
    for program, expected_path in COMPILER_PROGRAMS.items():
        selected = Path(run_compiler_query("-print-prog-name=" + program))
        try:
            selected_path = selected.resolve(strict=True)
        except OSError as exc:
            raise GateError("cross-compiler selected component is unavailable") from exc
        require(selected_path == Path(expected_path),
                "cross-compiler selected component path mismatch")

    runtime = []
    for name, expected in STATIC_RUNTIME_SHA256.items():
        printed = run_compiler_query("-print-file-name=" + name)
        require(printed != name, "cross-compiler cannot resolve static runtime input")
        path = Path(printed).resolve(strict=True)
        runtime.append({"id": name, "sha256": verify_hash_file(path, expected, name)})
    components = []
    for path_text, expected in COMPILER_COMPONENTS.items():
        path = Path(path_text)
        components.append({
            "id": path.name,
            "sha256": verify_hash_file(path, expected, "compiler component"),
        })
    return {
        "compiler_id": "aarch64-linux-gnu-gcc-13",
        "sha256": COMPILER_SHA256,
        "version": version,
        "target": target,
        "major": major,
        "sysroot": run_compiler_query("-print-sysroot"),
        "static_runtime_inputs": runtime,
        "compiler_components": components,
    }


def check_resources() -> dict[str, int]:
    available = None
    try:
        for line in Path("/proc/meminfo").read_text(encoding="ascii").splitlines():
            if line.startswith("MemAvailable:"):
                available = int(line.split()[1]) * 1024
                break
    except (OSError, ValueError, IndexError):
        pass
    require(available is not None and available >= MIN_AVAILABLE_MEMORY,
            "available memory is below the bounded host-build floor")
    try:
        free = shutil.disk_usage(BUILD_ROOT).free
    except OSError as exc:
        raise GateError("candidate build filesystem is unavailable") from exc
    require(free >= MIN_FREE_DISK, "free disk is below the bounded host-build floor")
    return {"available_memory_bytes": available, "free_disk_bytes": free}


def dependency_identity(path: Path) -> str:
    public = { (ROOT / relative).resolve(strict=True): relative
               for relative in PUBLIC_INPUT_SHA256 }
    private = {candidate.resolve(strict=True): name
               for name, (candidate, _digest) in PRIVATE_INPUTS.items()}
    if path in public:
        return "public:" + public[path]
    if path in private:
        return "private:" + private[path]
    usr = Path("/usr").resolve(strict=True)
    require(path == usr or usr in path.parents,
            "compiler dependency resolved outside pinned source/private/system roots")
    # Preserve the exact system-header path identity without publishing it.
    path_id = sha256_bytes(str(path).encode("utf-8"))
    return "system-path-sha256:" + path_id


def hash_dependency_paths(paths: list[str]) -> dict:
    entries = {}
    total_bytes = 0
    public_count = 0
    private_count = 0
    system_count = 0
    for token in paths:
        dependency = Path(token)
        if not dependency.is_absolute():
            dependency = ROOT / dependency
        try:
            resolved = dependency.resolve(strict=True)
        except OSError as exc:
            raise GateError("compiler dependency path is missing") from exc
        info = regular_file(resolved, "compiler dependency")
        total_bytes += info.st_size
        require(total_bytes <= 256 * 1024**2,
                "compiler dependency closure exceeds the bounded input size")
        identity = dependency_identity(resolved)
        digest = sha256_file(resolved)
        require(identity not in entries, "compiler dependency identity is ambiguous")
        entries[identity] = digest
        if identity.startswith("public:"):
            public_count += 1
        elif identity.startswith("private:"):
            private_count += 1
        else:
            system_count += 1

    expected_public = {"public:" + path for path in PUBLIC_INPUT_SHA256}
    expected_private = {"private:" + name for name in PRIVATE_INPUTS}
    require(expected_public.issubset(entries),
            "compiler dependency closure omits a pinned public source")
    require(expected_private.issubset(entries),
            "compiler dependency closure omits a pinned private header")
    manifest = json.dumps(entries, sort_keys=True, separators=(",", ":")).encode()
    return {
        "items": entries,
        "dependency_count": len(entries),
        "dependency_bytes": total_bytes,
        "public_source_count": public_count,
        "private_header_count": private_count,
        "system_header_count": system_count,
        "dependency_content_manifest_sha256": sha256_bytes(manifest),
    }


def discover_dependencies() -> dict:
    command = [
        str(COMPILER), "-M", "-std=c11", "-Wall", "-Wextra", "-Werror", "-O2",
        "-I", str(PRIVATE_INCLUDE_ROOT), str(ROOT / MAIN_SOURCE),
    ]
    result = subprocess.run(
        command,
        cwd=ROOT,
        env=clean_git_env(),
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    require(result.returncode == 0, "cross-compiler dependency scan failed")
    paths = parse_depfile(result.stdout)
    return hash_dependency_paths(paths)


def output_directory_state() -> dict[str, object]:
    try:
        root_resolved = TRUSTED_ROOT.resolve(strict=True)
        build_resolved = BUILD_ROOT.resolve(strict=True)
    except OSError as exc:
        raise GateError("trusted host build root is unavailable") from exc
    require(root_resolved == TRUSTED_ROOT and build_resolved == BUILD_ROOT,
            "trusted host build root contains a symlink or path redirect")
    parent_info = BUILD_ROOT.lstat()
    require(stat.S_ISDIR(parent_info.st_mode), "trusted build root is not a directory")
    require(OUTPUT_DIR.parent == BUILD_ROOT and OUTPUT_DIR.name ==
            "bt-native-transport-candidate-20261002",
            "candidate output path changed")
    present = OUTPUT_DIR.exists() or OUTPUT_DIR.is_symlink()
    if present:
        out_info = OUTPUT_DIR.lstat()
        require(stat.S_ISDIR(out_info.st_mode) and out_info.st_uid == os.getuid() and
                stat.S_IMODE(out_info.st_mode) == 0o700,
                "existing candidate output directory is not private and owner-controlled")
        return {"present": True, "mode": stat.S_IMODE(out_info.st_mode)}
    return {"present": False, "mode": None}


def verify_output_absent(path: Path) -> None:
    require(not path.exists() and not path.is_symlink(),
            "candidate output already exists; refusing overwrite or retry")


def parse_depfile(data: str) -> list[str]:
    logical = data.replace("\\\n", " ")
    _target, separator, payload = logical.partition(":")
    require(bool(separator), "compiler dependency file is malformed")
    try:
        return shlex.split(payload, posix=True)
    except ValueError as exc:
        raise GateError("compiler dependency file cannot be parsed") from exc


def verify_dependency_closure(depfile: Path, expected_items: dict[str, str],
                              private_hashes: list[dict[str, str]]) -> dict:
    require(depfile.is_file() and not depfile.is_symlink(),
            "compiler dependency file is missing")
    actual = hash_dependency_paths(parse_depfile(depfile.read_text(encoding="utf-8")))
    for name, (path, expected) in PRIVATE_INPUTS.items():
        verify_hash_file(path, expected, name)
    require(actual["items"] == expected_items,
            "compiled dependency content differs from pre-build snapshot")
    return {key: value for key, value in actual.items() if key != "items"} | {
        "dependency_file_sha256": sha256_file(depfile),
        "closure_verified": True,
        "private_input_sha256": [item for item in private_hashes if "id" in item],
    }


def elf_identity(data: bytes) -> dict:
    require(len(data) >= 64 and data[:4] == b"\x7fELF",
            "candidate is not an ELF file")
    require(data[4] == 2 and data[5] == 1,
            "candidate must be little-endian ELF64")
    elf_type, machine = struct.unpack_from("<HH", data, 16)
    require(elf_type == 2, "candidate must be a statically linked executable")
    require(machine == 183, "candidate ELF machine is not AArch64")

    phoff = struct.unpack_from("<Q", data, 32)[0]
    phentsize, phnum = struct.unpack_from("<HH", data, 54)
    require(phentsize >= 56 and phoff + phentsize * phnum <= len(data),
            "candidate ELF program headers are malformed")
    program_types = [struct.unpack_from("<I", data, phoff + i * phentsize)[0]
                     for i in range(phnum)]
    require(3 not in program_types and 2 not in program_types,
            "candidate must not contain interpreter or dynamic program segments")

    shoff = struct.unpack_from("<Q", data, 40)[0]
    shentsize, shnum = struct.unpack_from("<HH", data, 58)
    require(shentsize >= 64 and shnum > 0 and
            shoff + shentsize * shnum <= len(data),
            "candidate ELF section table is malformed")
    build_id = None
    for index in range(shnum):
        offset = shoff + index * shentsize
        if struct.unpack_from("<I", data, offset + 4)[0] != 7:
            continue
        note_offset, note_size = struct.unpack_from("<QQ", data, offset + 24)
        end = note_offset + note_size
        require(end <= len(data), "candidate ELF note section is out of bounds")
        cursor = note_offset
        while cursor + 12 <= end:
            namesz, descsz, note_type = struct.unpack_from("<III", data, cursor)
            cursor += 12
            name_end = cursor + namesz
            name_padded_end = cursor + ((namesz + 3) & ~3)
            desc_end = name_padded_end + descsz
            next_cursor = name_padded_end + ((descsz + 3) & ~3)
            require(next_cursor <= end, "candidate ELF note is malformed")
            if data[cursor:name_end].rstrip(b"\0") == b"GNU" and note_type == 3:
                build_id = data[name_padded_end:desc_end].hex()
                break
            cursor = next_cursor
        if build_id:
            break
    require(build_id is not None and len(build_id) >= 16,
            "candidate ELF GNU build ID is missing")
    return {"elf_class": 64, "endianness": "little", "machine": "AArch64",
            "type": "ET_EXEC", "static": True, "gnu_build_id": build_id}


def verify_artifact(data: bytes, expected_sha256: str | None = None) -> dict:
    if expected_sha256 is not None:
        verify_bytes_hash(data, expected_sha256, "candidate artifact")
    require(0 < len(data) <= MAX_ARTIFACT_BYTES,
            "candidate artifact is empty or exceeds the bounded size")
    identity = elf_identity(data)
    return {**identity, "size_bytes": len(data), "sha256": sha256_bytes(data)}


def build_command(output_dir: Path = OUTPUT_DIR) -> list[str]:
    return [
        str(COMPILER), "-static", "-std=c11", "-Wall", "-Wextra", "-Werror", "-O2",
        "-I", str(PRIVATE_INCLUDE_ROOT),
        "-MD", "-MF", str(output_dir / "candidate-dependencies.d"),
        "-Wl,-Map," + str(output_dir / "candidate-link.map"),
        str(ROOT / MAIN_SOURCE), "-o", str(output_dir / ARTIFACT_NAME),
    ]


def run_preflight() -> dict:
    check_environment()
    source_commit = verify_git_source_tree()
    legacy = verify_legacy_trial_guards()
    private_hashes = verify_private_inputs()
    compiler = compiler_identity()
    dependency_snapshot = discover_dependencies()
    resources = check_resources()
    output = output_directory_state()
    return {
        "source_commit": source_commit,
        "source_inputs": [
            {"path": path, "sha256": digest}
            for path, digest in PUBLIC_INPUT_SHA256.items()
        ],
        "private_inputs": private_hashes,
        "compiler": compiler,
        "dependency_snapshot": {
            key: value for key, value in dependency_snapshot.items() if key != "items"
        },
        "_dependency_items": dependency_snapshot["items"],
        "resources": resources,
        "output": {"identity": OUTPUT_DIR.name, **output},
        "legacy_trial": legacy,
        "compile_configuration": {
            "flags": ["-static", "-std=c11", "-Wall", "-Wextra", "-Werror", "-O2"],
            "include_root_id": "existing-s22-tools-hardware-private-input-root",
            "target": ARTIFACT_NAME,
        },
    }


def write_private_file(path: Path, data: bytes, mode: int = 0o600) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    with os.fdopen(fd, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def parse_link_map(path: Path) -> dict:
    text = path.read_text(encoding="utf-8", errors="replace")
    loads = re.findall(r"^\s*LOAD\s+(\S+)\s*$", text, flags=re.MULTILINE)
    basenames = sorted({Path(value).name for value in loads})
    required = {"crt1.o", "crti.o", "crtn.o", "crtbeginT.o", "crtend.o",
                "libc.a", "libgcc.a"}
    require(required.issubset(set(basenames)),
            "static link map omits a pinned runtime input")
    loaded_runtime = {}
    for token in loads:
        path_value = Path(token)
        if not path_value.is_absolute():
            path_value = ROOT / path_value
        name = path_value.name
        if name not in STATIC_RUNTIME_SHA256:
            continue
        try:
            resolved = path_value.resolve(strict=True)
        except OSError as exc:
            raise GateError("pinned static runtime input disappeared after link") from exc
        loaded_runtime[name] = verify_hash_file(
            resolved, STATIC_RUNTIME_SHA256[name], name)
    require(required.issubset(set(loaded_runtime)),
            "static link map runtime bytes differ from the pinned compiler inputs")
    return {
        "load_entry_count": len(loads),
        "load_input_basenames": basenames,
        "linked_runtime_sha256": loaded_runtime,
        "link_map_sha256": sha256_file(path),
    }


def run_build(preflight: dict) -> dict:
    verify_output_absent(OUTPUT_DIR)
    try:
        OUTPUT_DIR.mkdir(mode=0o700, parents=False, exist_ok=False)
    except OSError as exc:
        raise GateError("exclusive candidate output directory creation failed") from exc
    info = OUTPUT_DIR.lstat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid() and
            stat.S_IMODE(info.st_mode) == 0o700,
            "new candidate output directory failed ownership/mode validation")

    command = build_command()
    started = time.time()
    try:
        result = subprocess.run(
            command,
            cwd=ROOT,
            env=clean_git_env(),
            check=False,
            capture_output=True,
            timeout=BUILD_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        combined = (exc.stdout or b"") + b"\n" + (exc.stderr or b"")
        write_private_file(OUTPUT_DIR / "build.log", combined)
        raise GateError("cross-compile timed out; output preserved, do not retry") from exc
    elapsed = time.time() - started
    log_data = result.stdout + b"\n" + result.stderr
    write_private_file(OUTPUT_DIR / "build.log", log_data)
    require(result.returncode == 0,
            "cross-compiler failed; output preserved, do not retry blindly")

    artifact_info = regular_file(ARTIFACT_PATH, "candidate artifact")
    require(artifact_info.st_uid == os.getuid(), "candidate artifact owner mismatch")
    require(artifact_info.st_size <= MAX_ARTIFACT_BYTES,
            "candidate artifact exceeds the bounded size")
    os.chmod(ARTIFACT_PATH, 0o700)
    artifact_bytes = ARTIFACT_PATH.read_bytes()
    artifact = verify_artifact(artifact_bytes)
    private_hashes_after = verify_private_inputs()
    require(private_hashes_after == preflight["private_inputs"],
            "private input fingerprint changed during compilation")
    compiler_after = compiler_identity()
    require(compiler_after == preflight["compiler"],
            "compiler or static runtime fingerprint changed during compilation")
    for relative, expected in PUBLIC_INPUT_SHA256.items():
        verify_hash_file(ROOT / relative, expected, relative)

    dependency = verify_dependency_closure(
        OUTPUT_DIR / "candidate-dependencies.d",
        preflight["_dependency_items"], preflight["private_inputs"],
    )
    link_map = parse_link_map(OUTPUT_DIR / "candidate-link.map")
    build_log = OUTPUT_DIR / "build.log"
    require(build_log.is_file(), "private build log is missing")
    return {
        "schema": "s22-bt-native-transport-candidate/v1",
        "status": "host_candidate_built_not_staged_or_authorized",
        "source_commit": preflight["source_commit"],
        "source_input_sha256": preflight["source_inputs"],
        "private_input_sha256": private_hashes_after,
        "compiler": preflight["compiler"],
        "compile_configuration": preflight["compile_configuration"],
        "artifact": {"name": ARTIFACT_NAME, **artifact,
                     "owner_only_mode": "0700"},
        "dependency_proof": {
            **dependency,
            **link_map,
            "build_log_sha256": sha256_file(build_log),
            "compiler_exit_code": result.returncode,
            "elapsed_seconds": round(elapsed, 3),
        },
        "legacy_trial": preflight["legacy_trial"],
        "device_or_deployment_action": False,
        "candidate_execution": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check-only", action="store_true",
                       help="validate exact source/tool/private input gates without compiling")
    group.add_argument("--build-candidate", action="store_true",
                       help="run exactly one bounded host AArch64 static build")
    args = parser.parse_args(argv)
    try:
        require(bool(sys.flags.isolated) and bool(sys.flags.no_site),
                "CLI requires Python -I -S startup isolation")
        preflight = run_preflight()
        if args.check_only:
            payload = {
                "schema": "s22-bt-native-transport-preflight/v1",
                "status": "preflight_only_no_compile",
                **{key: value for key, value in preflight.items()
                   if not key.startswith("_")},
            }
        else:
            payload = run_build(preflight)
        print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
        return 0
    except (GateError, OSError, subprocess.SubprocessError, ValueError) as exc:
        safe = str(exc)
        print("BT_CANDIDATE_REFUSED: " + safe, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
