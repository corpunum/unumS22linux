#!/usr/bin/env python3
"""Build a reviewed six-patch or native-eight NPU profile in a fresh output.

This host-only builder keeps the six-patch profile as its default and exposes
the HCI/camera-preserving eight-patch profile only through an explicit switch.
It verifies the exact source commit, ordered patch tree, preserved config, and
local Android Clang 21 toolchain before invoking Kbuild. It never packages or
deploys the result.
"""
from __future__ import annotations

import argparse
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
import errno
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
BASE_COMMIT = "4e5c5ad7d950e4de0688b5663965f2075654b2ad"
PATCHES = (
    ("npu-session-lifecycle-fix.patch",
     "1554436cb6624c542f9e04ac22a3b3545e55f94c59d3025ee6bdc1ec43168251"),
    ("npu-refcount-lifecycle-profile.patch",
     "8385e4210a807f96f972757cd6ca74a8077b0127ab112d8b877d012ccdc3cb7b"),
    ("npu-default-boot-callback-fix.patch",
     "f5ce216e34df11d8c6adee4a99c36d63f73593cf379e29de3a9de828ec2ee1e7"),
    ("npu-probe-unwind-fix.patch",
     "d3e2e590d4d3c956b10c724a15db996dacd07def204332f50a1c0513f56b4948"),
    ("npu-shutdown-lifecycle-profile.patch",
     "b986e1896305fda55f1d702ed6f12dde646e4a84b3ce91009203b9d77b7a00e7"),
    ("npu-shutdown-error-propagation.patch",
     "08374e96792f24d1e0e4fbca594bfce35537af8acace9296b66f27b531564e43"),
)
FROZEN_OWNERSHIP_PATCH = (
    "npu-shutdown-ownership-fix.patch",
    "a8af77122b4049fd38e21adfd01a8577d3e8f9bef1f68d0cfa091a5884a4d9f3",
)
LOCAL_S22_ROOT = Path.home() / "s22-linux"
CONFIG_PATH = (
    LOCAL_S22_ROOT / "builds/close-range-clang21-llvm1-O-20260922/.config"
)
CONFIG_SHA256 = "a147841a53f5b10c366a759d0e83525996a0ec5d8227a103b020cf2111400f9e"
TOOLCHAIN_BIN = Path(
    LOCAL_S22_ROOT / "builds/toolchain-clang-r563880c-20260922/"
    "repo/clang-r563880c/bin"
)
CLANG_SHA256 = "af0f25ca6818aed54c1cab03dc591acd549f385b8447148326a413e3e59c22b7"
LD_LLD_SHA256 = "784146955ed87545385bf5c89b3b920ca7fe3ac83e034c3e6c53783ce544adf1"
BUILDS_ROOT = LOCAL_S22_ROOT / "builds"
DEFAULT_SOURCE = BUILDS_ROOT / "npu-six-patch-kernel-20261002"
DEFAULT_OUTPUT = BUILDS_ROOT / "npu-six-patch-out-20261002"
NATIVE_EIGHT_SOURCE_BASE = "3fca50941422439b2019db2e4a3dc1016b2138a1"
NATIVE_EIGHT_PATCHES = (
    *PATCHES,
    ("npu-mailbox-missing-callback-reclaim.patch",
     "f109b57381b3f2afcf2638b518f50db59ef8c9f784debd949488c74f8ea5c39b"),
    ("npu-mailbox-debug-walk-bounds.patch",
     "20c700bfa11f13836c76c88cca28a4f8dfa459e5cf146292a814880cd5850b29"),
)
NATIVE_EIGHT_SOURCE = BUILDS_ROOT / "npu-native-eight-kernel-20261002"
NATIVE_EIGHT_OUTPUT = BUILDS_ROOT / "npu-native-eight-out-20261002"
NATIVE_EIGHT_CONFIG_PATH = Path(
    "/home/corpunum/s22-linux/builds/native-config-export-20261002.config"
)
NATIVE_EIGHT_CONFIG_SHA256 = (
    "d762d5fc71e369013ee36657d007063ce1f0faca9707d5f9ccfba2597b7fcd16"
)
NATIVE_EIGHT_REQUIRED_CONFIG = (
    'CONFIG_LOCALVERSION="-g4e5c5ad7d950"',
    "# CONFIG_LOCALVERSION_AUTO is not set",
    "CONFIG_SHADOW_CALL_STACK=y",
    "CONFIG_LTO_NONE=y",
    "CONFIG_CFI_CLANG=y",
    "CONFIG_MODVERSIONS=y",
    "CONFIG_BT=y",
    "CONFIG_BT_HCIUART=y",
    "CONFIG_BT_HCIUART_QCA=y",
    "CONFIG_VIDEO_EXYNOS_PABLO_ISP=m",
    "CONFIG_EXYNOS_NPU=m",
    "CONFIG_NPU_USE_HW_DEVICE=y",
    "CONFIG_NPU_USE_BOOT_IOCTL=y",
)
NATIVE_EIGHT_LLVM_TOOL_SHA256 = {
    "clang": CLANG_SHA256,
    "ld.lld": LD_LLD_SHA256,
    "llvm-ar": "9833ebe9c5cb6be4711e667959cb70bf30434c8045dc135667c9f87b0d531b26",
    "llvm-nm": "96bc0865c29acfe30b45d84b4acca27ac14340657ffc6dbea58797f3853be6f1",
    "llvm-objcopy": "1cdde2768f3c94aa5db19361f6857ffe80a1c3c7f87574b64df5da256c4ac959",
    "llvm-objdump": "68736b054c3d7035474e10b827908417b4d92e22b25bb1aa773f0add7f324b1f",
    "llvm-readelf": "5104576a3518575cf1887c2afa9249bbd0dc175cb9dc0f2af0d430fe0cb20bbe",
    "llvm-strip": "1cdde2768f3c94aa5db19361f6857ffe80a1c3c7f87574b64df5da256c4ac959",
    "llvm-ranlib": "9833ebe9c5cb6be4711e667959cb70bf30434c8045dc135667c9f87b0d531b26",
    "llvm-size": "50ac8d28bd266f5117de9c8199c84b8ddbaf6994a063f7a38d619fa373a750dd",
    "clang-21": "7202556a0ecae7ab00c67c1221e502692c7a46cf1531262fc62f597820078eef",
}
NATIVE_EIGHT_GNU_TOOL_SHA256 = {
    "aarch64-linux-gnu-gcc": "cd90adc7801f4595267f61a5d25bd3a0c6beb2f9f1f107ab919a97a12972dc9a",
    "aarch64-linux-gnu-ld": "7c903ac277dd1f5c4397277db865f12d8239fe45782f71afbeb3d42180a4b1ae",
    "aarch64-linux-gnu-nm": "96dbed79b11f6cc13b060dd5ca705a277bb5bdecd714df1c470ffaafb2513727",
}
GIB = 1024 ** 3
MIN_START_MEM = 12 * GIB
MIN_REMAINING_MEM = 8 * GIB
MIN_START_DISK = 32 * GIB
MIN_REMAINING_DISK = 16 * GIB
CLEANUP_STAGE_TIMEOUTS = (30.0, 15.0, 5.0)
CLEANUP_POLL_INTERVAL = 0.1
REQUIRED_CONFIG = (
    "CONFIG_EXYNOS_NPU=m",
    "CONFIG_NPU_USE_HW_DEVICE=y",
    "CONFIG_NPU_USE_BOOT_IOCTL=y",
    "CONFIG_LTO_CLANG_THIN=y",
    "CONFIG_CFI_CLANG=y",
    "CONFIG_MODVERSIONS=y",
    "CONFIG_SHADOW_CALL_STACK=y",
)
LLVM_TOOLS = (
    "clang", "ld.lld", "llvm-ar", "llvm-nm", "llvm-objcopy",
    "llvm-objdump", "llvm-readelf", "llvm-strip", "llvm-ranlib",
    "llvm-size", "clang-21",
)


class BuildError(RuntimeError):
    pass


@dataclass(frozen=True)
class BuildProfile:
    name: str
    source_base_commit: str
    patches: tuple[tuple[str, str], ...]
    default_source: Path
    default_output: Path
    output_prefix: str
    stack_label: str
    config_path: Path
    config_sha256: str
    required_config: tuple[str, ...]


PROFILES = {
    "six": BuildProfile(
        name="six",
        source_base_commit=BASE_COMMIT,
        patches=PATCHES,
        default_source=DEFAULT_SOURCE,
        default_output=DEFAULT_OUTPUT,
        output_prefix="npu-six-patch-out-",
        stack_label="six-patch",
        config_path=CONFIG_PATH,
        config_sha256=CONFIG_SHA256,
        required_config=REQUIRED_CONFIG,
    ),
    "native-eight": BuildProfile(
        name="native-eight",
        source_base_commit=NATIVE_EIGHT_SOURCE_BASE,
        patches=NATIVE_EIGHT_PATCHES,
        default_source=NATIVE_EIGHT_SOURCE,
        default_output=NATIVE_EIGHT_OUTPUT,
        output_prefix="npu-native-eight-out-",
        stack_label="native-eight NPU patch",
        config_path=NATIVE_EIGHT_CONFIG_PATH,
        config_sha256=NATIVE_EIGHT_CONFIG_SHA256,
        required_config=NATIVE_EIGHT_REQUIRED_CONFIG,
    ),
}


def run(command: list[str], *, cwd: Path, env: dict[str, str] | None = None,
        check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, cwd=cwd, env=env, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            check=False)
    if check and result.returncode != 0:
        raise BuildError(
            f"command failed ({result.returncode}): {' '.join(command)}\n"
            f"{result.stdout[-12000:]}"
        )
    return result


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def git(source: Path, *args: str, env: dict[str, str] | None = None) -> str:
    return run(["git", *args], cwd=source, env=env).stdout.strip()


def verify_patch_inputs(
        patches: tuple[tuple[str, str], ...] = PATCHES) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for name, expected in (*patches, FROZEN_OWNERSHIP_PATCH):
        path = ROOT / "tools/hardware" / name
        if not path.is_file():
            raise BuildError(f"required reviewed patch is missing: {name}")
        actual = sha256(path)
        if actual != expected:
            raise BuildError(f"patch SHA-256 mismatch for {name}: {actual}")
        result.append({"name": name, "sha256": actual})
    return result


def patch_receipt_fields(profile: BuildProfile,
                         patch_info: list[dict[str, str]]) -> dict[str, object]:
    """Keep applied patches distinct from hash-only exclusions in receipts."""
    if profile.name == "six":
        return {"patches": patch_info}
    return {
        "patches": patch_info[:-1],
        "excluded_patch_inputs": [patch_info[-1]],
    }


def verify_config(path: Path = CONFIG_PATH, expected_sha256: str = CONFIG_SHA256,
                  required: tuple[str, ...] = REQUIRED_CONFIG) -> dict[str, str]:
    if not path.is_file():
        raise BuildError(f"preserved config is missing: {path}")
    actual = sha256(path)
    if actual != expected_sha256:
        raise BuildError(f"preserved config SHA-256 mismatch: {actual}")
    lines = path.read_text(encoding="utf-8").splitlines()
    missing = [line for line in required if line not in lines]
    if missing:
        raise BuildError(f"preserved config lacks required enabled options: {missing}")
    return {"path": str(path), "sha256": actual}


def toolchain_identities(toolchain_bin: Path = TOOLCHAIN_BIN) -> list[dict[str, str | int]]:
    identities: list[dict[str, str | int]] = []
    for name in LLVM_TOOLS:
        path = toolchain_bin / name
        if not path.is_file() or not os.access(path, os.X_OK):
            raise BuildError(f"pinned LLVM helper is missing or not executable: {path}")
        resolved = path.resolve(strict=True)
        identities.append({
            "name": name,
            "path": str(path),
            "resolved_path": str(resolved),
            "bytes": resolved.stat().st_size,
            "sha256": sha256(resolved),
        })
    return identities


def cross_tool_identities() -> list[dict[str, str | int]]:
    identities: list[dict[str, str | int]] = []
    for name in NATIVE_EIGHT_GNU_TOOL_SHA256:
        found = shutil.which(name)
        if found is None:
            raise BuildError(f"required GNU cross tool is missing from PATH: {name}")
        path = Path(found)
        resolved = path.resolve(strict=True)
        identities.append({
            "name": name,
            "path": str(path),
            "resolved_path": str(resolved),
            "bytes": resolved.stat().st_size,
            "sha256": sha256(resolved),
        })
    return identities


def verify_native_toolchain_identities(
        llvm_tools: list[dict[str, str | int]],
        cross_tools: list[dict[str, str | int]]) -> None:
    for tools, expected, label in (
            (llvm_tools, NATIVE_EIGHT_LLVM_TOOL_SHA256, "LLVM"),
            (cross_tools, NATIVE_EIGHT_GNU_TOOL_SHA256, "GNU cross")):
        actual = {str(item["name"]): str(item["sha256"]) for item in tools}
        if set(actual) != set(expected):
            raise BuildError(f"native-eight {label} tool inventory mismatch")
        mismatches = {
            name: actual[name] for name, digest in expected.items()
            if actual[name] != digest
        }
        if mismatches:
            raise BuildError(
                f"native-eight pinned {label} tool hash mismatch: {mismatches}"
            )


def validate_output_path(output: Path, builds_root: Path = BUILDS_ROOT,
                         output_prefix: str = "npu-six-patch-out-") -> None:
    if output.is_symlink():
        raise BuildError(f"refusing a symbolic-link build output path: {output}")
    resolved = output.resolve()
    root = builds_root.resolve()
    if resolved.parent != root or not resolved.name.startswith(output_prefix):
        raise BuildError(
            f"output must be a fresh {output_prefix} directory under {root}"
        )
    if resolved.exists() or os.path.lexists(str(output)):
        raise BuildError(f"refusing to reuse an existing build output: {resolved}")


def verify_patch_tree(
        source: Path, *, source_base_commit: str = BASE_COMMIT,
        patches: tuple[tuple[str, str], ...] = PATCHES,
        stack_label: str = "six-patch") -> None:
    """Replay the selected diffs into a temporary index and compare its tree."""
    head_tree = git(source, "rev-parse", "HEAD^{tree}")
    with tempfile.TemporaryDirectory(prefix="npu-six-index-") as directory:
        index = Path(directory) / "index"
        env = os.environ.copy()
        env["GIT_INDEX_FILE"] = str(index)
        run(["git", "read-tree", source_base_commit], cwd=source, env=env)
        for name, _expected in patches:
            patch = ROOT / "tools/hardware" / name
            apply_args = ["git", "apply", "--cached", "--whitespace=error-all"]
            run([*apply_args, "--check", str(patch)], cwd=source, env=env)
            run([*apply_args, str(patch)], cwd=source, env=env)
        replayed_tree = git(source, "write-tree", env=env)
    if replayed_tree != head_tree:
        raise BuildError(
            f"kernel source tree is not exactly the ordered {stack_label} stack: "
            f"replayed={replayed_tree}, HEAD={head_tree}"
        )


def verify_source(source: Path, profile: BuildProfile = PROFILES["six"]
                  ) -> dict[str, str]:
    if not source.is_dir():
        raise BuildError(f"kernel source worktree is missing: {source}")
    head = git(source, "rev-parse", "HEAD")
    parents = git(source, "rev-list", "--parents", "-n", "1", "HEAD").split()
    if len(parents) != 2 or parents[1] != profile.source_base_commit:
        if profile.name == "six":
            raise BuildError(
                "kernel commit must be one clean commit directly on the pinned base"
            )
        raise BuildError(
            "kernel commit must be one clean commit directly on the pinned native "
            "HCI/camera base"
        )
    if git(source, "status", "--porcelain=v1", "--untracked-files=all"):
        raise BuildError("kernel source worktree must be clean before build")
    check = run(["git", "diff", "--check", f"{BASE_COMMIT}..HEAD"], cwd=source)
    if check.stdout:
        raise BuildError(f"kernel patch commit has whitespace findings:\n{check.stdout}")
    verify_patch_tree(
        source, source_base_commit=profile.source_base_commit,
        patches=profile.patches, stack_label=profile.stack_label,
    )
    result = {"base_commit": BASE_COMMIT, "kernel_commit": head,
              "kernel_tree": git(source, "rev-parse", "HEAD^{tree}")}
    if profile.name != "six":
        result["profile_source_base_commit"] = profile.source_base_commit
    return result


def available_memory() -> int:
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) * 1024
    raise BuildError("MemAvailable is missing from /proc/meminfo")


def resource_sample(output: Path, started: float) -> dict[str, int | float | str]:
    sample: dict[str, int | float | str] = {
        "time_utc": datetime.now(timezone.utc).isoformat(),
        "elapsed_seconds": round(time.monotonic() - started, 1),
        "mem_available_bytes": available_memory(),
        "disk_free_bytes": shutil.disk_usage(output).free,
    }
    with (output / "resource-monitor.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(sample, sort_keys=True) + "\n")
        stream.flush()
    return sample


def process_group_exists(process_group_id: int) -> bool | None:
    """Check whole-group presence; None means presence could not be checked."""
    try:
        os.killpg(process_group_id, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError as error:
        return False if error.errno == errno.ESRCH else None
    return True


def wait_for_process_group_exit(process: subprocess.Popen[bytes], timeout: float) -> bool:
    """Boundedly wait for the owned group to disappear, not merely its leader."""
    deadline = time.monotonic() + timeout
    while True:
        group_state = process_group_exists(process.pid)
        if group_state is False:
            try:
                process.wait(timeout=0)
            except subprocess.TimeoutExpired:
                return False
            return True
        if group_state is None:
            return False
        process.poll()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(CLEANUP_POLL_INTERVAL, remaining))


def stop_own_process_group(process: subprocess.Popen[bytes]) -> bool:
    """Stop only the session-created build group; True requires group absence.

    The direct child may already have exited or been reaped while descendants
    remain in the original process group, so every stage checks that group.
    False means group disappearance was not confirmed within bounded waits.
    """
    group_state = process_group_exists(process.pid)
    if group_state is False:
        try:
            process.wait(timeout=0)
        except subprocess.TimeoutExpired:
            return False
        return True
    if group_state is None:
        return False

    for sig, timeout in zip(
            (signal.SIGINT, signal.SIGTERM, signal.SIGKILL),
            CLEANUP_STAGE_TIMEOUTS):
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            pass
        except OSError:
            return False
        if wait_for_process_group_exit(process, timeout):
            return True
    return False


def run_monitored_build(command: list[str], source: Path, output: Path,
                        env: dict[str, str],
                        phase_info: dict[str, object] | None = None) -> tuple[int, bool]:
    log_path = output / "build.log"
    started = time.monotonic()
    interrupted_for_resources = False
    last_heartbeat = started
    with log_path.open("xb") as log:
        process = subprocess.Popen(
            command, cwd=source, env=env, stdout=log, stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            if phase_info is not None:
                phase_info.update({
                    "phase": "build_running",
                    "builder_pid": os.getpid(),
                    "make_pid": process.pid,
                    "make_process_group": process.pid,
                    "build_log": str(log_path),
                    "resource_monitor": str(output / "resource-monitor.jsonl"),
                    "observed_at_utc": datetime.now(timezone.utc).isoformat(),
                })
                (output / "build-phase.json").write_text(
                    json.dumps(phase_info, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
            while process.poll() is None:
                time.sleep(10)
                sample = resource_sample(output, started)
                elapsed = int(sample["elapsed_seconds"])
                if elapsed - (last_heartbeat - started) >= 60:
                    print(
                        "BUILD_MONITOR "
                        f"elapsed={elapsed}s "
                        f"mem_available={sample['mem_available_bytes']} "
                        f"disk_free={sample['disk_free_bytes']}",
                        flush=True,
                    )
                    last_heartbeat = time.monotonic()
                if (int(sample["mem_available_bytes"]) < MIN_REMAINING_MEM or
                        int(sample["disk_free_bytes"]) < MIN_REMAINING_DISK):
                    interrupted_for_resources = True
                    print("BUILD_ABORT resource threshold reached; stopping this build", flush=True)
                    stopped = stop_own_process_group(process)
                    if not stopped:
                        print("BUILD_ABORT owned process-group absence was not confirmed under "
                              "bounded cleanup",
                              flush=True)
                        return -signal.SIGKILL, True
                    break
            return process.poll() if process.poll() is not None else -signal.SIGKILL, interrupted_for_resources
        except BaseException:
            try:
                stopped = stop_own_process_group(process)
            except BaseException as cleanup_error:
                print("BUILD_ABORT monitor failed; cleanup raised "
                      f"{type(cleanup_error).__name__}: {cleanup_error}",
                      file=sys.stderr, flush=True)
            else:
                if not stopped:
                    print("BUILD_ABORT monitor failed; owned process-group absence was not "
                          "confirmed under bounded cleanup",
                          file=sys.stderr, flush=True)
            raise


def tail(path: Path, limit: int = 80) -> str:
    try:
        lines = deque(maxlen=limit)
        with path.open("r", encoding="utf-8", errors="replace") as stream:
            for line in stream:
                lines.append(line.rstrip("\n"))
        return "\n".join(lines)
    except OSError:
        return ""


def artifact_info(path: Path) -> dict[str, int | str]:
    if not path.is_file():
        raise BuildError(f"expected build artifact is missing: {path}")
    return {"path": str(path), "bytes": path.stat().st_size,
            "sha256": sha256(path)}


def builder_hash_from_launch(phase_info: dict[str, object]) -> str:
    """Return the helper bytes captured before the build subprocess launched."""
    digest = phase_info.get("executed_builder_sha256")
    if (not isinstance(digest, str) or len(digest) != 64 or
            any(character not in "0123456789abcdef" for character in digest)):
        raise BuildError("launch phase receipt lacks the builder SHA-256")
    return digest


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=tuple(PROFILES), default="six")
    parser.add_argument("--kernel-source", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--jobs", type=int, choices=(1, 2), default=1)
    return parser.parse_args(argv)


def main() -> int:
    args = parse_args()
    profile = PROFILES[args.profile]
    source = (args.kernel_source or profile.default_source).expanduser().resolve()
    builds_root = BUILDS_ROOT.resolve()
    requested_output = (args.output or profile.default_output).expanduser()
    validate_output_path(requested_output, builds_root, profile.output_prefix)
    output = requested_output.resolve()
    config_path = (args.config or profile.config_path).expanduser()

    source_info = verify_source(source, profile)
    patch_info = verify_patch_inputs(profile.patches)
    config_info = verify_config(
        config_path, profile.config_sha256, profile.required_config
    )

    clang = TOOLCHAIN_BIN / "clang"
    lld = TOOLCHAIN_BIN / "ld.lld"
    if sha256(clang) != CLANG_SHA256 or sha256(lld) != LD_LLD_SHA256:
        raise BuildError("local Android Clang 21 toolchain binary hash mismatch")
    toolchain_tools = toolchain_identities()
    version = run([str(clang), "--version"], cwd=source).stdout.splitlines()[0]
    if "r563880c" not in version or "clang version 21.0.0" not in version:
        raise BuildError(f"unexpected Android Clang version: {version}")
    cross_tools: list[dict[str, str | int]] = []
    if profile.name == "native-eight":
        cross_tools = cross_tool_identities()
        verify_native_toolchain_identities(toolchain_tools, cross_tools)
    else:
        for tool in ("aarch64-linux-gnu-gcc", "aarch64-linux-gnu-ld",
                     "aarch64-linux-gnu-nm"):
            if shutil.which(tool) is None:
                raise BuildError(f"required GNU cross tool is missing from PATH: {tool}")
    make = shutil.which("make")
    if make is None:
        raise BuildError("make is missing from PATH")

    output.parent.mkdir(parents=True, exist_ok=True)
    disk_before = shutil.disk_usage(output.parent).free
    memory_before = available_memory()
    if disk_before < MIN_START_DISK:
        raise BuildError(f"only {disk_before} bytes are free; need at least {MIN_START_DISK}")
    if memory_before < MIN_START_MEM:
        raise BuildError(f"only {memory_before} bytes are available; need at least {MIN_START_MEM}")

    print(f"SOURCE_BASE {source_info['base_commit']}")
    if profile.name != "six":
        print(f"PROFILE {profile.name}")
        print(f"PROFILE_SOURCE_BASE {source_info['profile_source_base_commit']}")
    print(f"SOURCE_COMMIT {source_info['kernel_commit']}")
    print(f"SOURCE_TREE {source_info['kernel_tree']}")
    print("PATCHES " + " -> ".join(item[0] for item in profile.patches))
    print(f"CONFIG_SHA256 {config_info['sha256']}")
    print(f"CLANG {version}")
    print(f"CLANG_SHA256 {CLANG_SHA256}")
    print(f"LD_LLD_SHA256 {LD_LLD_SHA256}")
    print(f"OUTPUT {output}")
    print(f"START_MEM_AVAILABLE {memory_before}")
    print(f"START_DISK_FREE {disk_before}")

    output.mkdir(mode=0o700)
    output_config = output / ".config"
    shutil.copyfile(config_path, output_config)
    if sha256(output_config) != profile.config_sha256:
        raise BuildError("copied fresh-output config does not match the preserved SHA")

    env = os.environ.copy()
    env["PATH"] = str(TOOLCHAIN_BIN) + os.pathsep + env.get("PATH", "")
    env["LC_ALL"] = "C"
    common = [make, "-C", str(source), f"O={output}", "ARCH=arm64", "LLVM=1",
              "LLVM_IAS=1", "CROSS_COMPILE=aarch64-linux-gnu-", f"LD={lld}"]
    prepare = [*common, "olddefconfig"]
    print("PREPARE_COMMAND " + " ".join(prepare), flush=True)
    prepare_result = run(prepare, cwd=source, env=env)
    (output / "olddefconfig.log").write_text(prepare_result.stdout, encoding="utf-8")
    if sha256(output_config) != profile.config_sha256:
        raise BuildError("olddefconfig changed the preserved configuration; build refused")

    build = [*common, f"-j{args.jobs}", "Image", "modules"]
    print("BUILD_COMMAND " + " ".join(build), flush=True)
    phase_info = {
        "executed_builder_sha256": sha256(Path(__file__).resolve()),
        "source": source_info,
        "config": config_info,
        "toolchain": {
            "clang_sha256": CLANG_SHA256,
            "ld_lld_sha256": LD_LLD_SHA256,
            "llvm_tools": toolchain_tools,
        },
        "command": build,
        "resource_abort_thresholds": {
            "minimum_mem_available_bytes": MIN_REMAINING_MEM,
            "minimum_disk_free_bytes": MIN_REMAINING_DISK,
        },
        "bootup_ready": False,
        "bootup_authorized": False,
    }
    if profile.name != "six":
        phase_info["profile"] = profile.name
        phase_info["toolchain"]["gnu_cross_tools"] = cross_tools
    try:
        exit_code, resource_abort = run_monitored_build(build, source, output, env, phase_info)
    except Exception as error:
        phase_info.update({
            "phase": "build_monitor_error",
            "monitor_error": f"{type(error).__name__}: {error}",
            "finished_at_utc": datetime.now(timezone.utc).isoformat(),
        })
        try:
            (output / "build-phase.json").write_text(
                json.dumps(phase_info, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        except OSError as receipt_error:
            print(f"BUILD_PHASE_WRITE_FAILED {receipt_error}", file=sys.stderr, flush=True)
        raise BuildError(
            f"build monitor failed after cleanup was requested: {type(error).__name__}: {error}"
        ) from error
    phase_info.update({
        "phase": "build_finished",
        "build_exit_code": exit_code,
        "resource_abort": resource_abort,
        "finished_at_utc": datetime.now(timezone.utc).isoformat(),
    })
    (output / "build-phase.json").write_text(
        json.dumps(phase_info, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    if exit_code != 0 or resource_abort:
        print(f"BUILD_EXIT {exit_code}; resource_abort={resource_abort}")
        print(f"BUILD_LOG_TAIL_BEGIN\n{tail(output / 'build.log')}\nBUILD_LOG_TAIL_END")
        return exit_code or 1

    if sha256(output_config) != profile.config_sha256:
        raise BuildError("configuration SHA changed during the build")
    artifacts = {
        "vmlinux": artifact_info(output / "vmlinux"),
        "Image": artifact_info(output / "arch/arm64/boot/Image"),
        "npu_module": artifact_info(output / "drivers/vision/npu.ko"),
    }
    modules_order = output / "modules.order"
    module_count = sum(bool(line.strip()) for line in modules_order.read_text().splitlines())
    kernel_release_path = output / "include/config/kernel.release"
    kernel_release = (kernel_release_path.read_text().strip()
                     if kernel_release_path.is_file() else "unknown")
    receipt = {
        "status": "host_build_pass",
        **source_info,
        **patch_receipt_fields(profile, patch_info),
        "config_sha256": profile.config_sha256,
        "toolchain": {
            "clang": version, "clang_sha256": CLANG_SHA256,
            "ld_lld_sha256": LD_LLD_SHA256, "bin": str(TOOLCHAIN_BIN),
            "llvm_tools": toolchain_tools,
        },
        "builder_sha256": builder_hash_from_launch(phase_info),
        "command": build,
        "jobs": args.jobs,
        "kernel_release": kernel_release,
        "module_count": module_count,
        "artifacts": artifacts,
        "bootup_ready": False,
        "bootup_authorized": False,
        "device_or_deployment_action": False,
    }
    if profile.name != "six":
        receipt["profile"] = profile.name
        receipt["source_profile_base_commit"] = profile.source_base_commit
        receipt["config_path"] = config_info["path"]
        receipt["toolchain"]["gnu_cross_tools"] = cross_tools
    (output / "build-receipt.json").write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"KERNEL_RELEASE {kernel_release}")
    print(f"MODULE_COUNT {module_count}")
    for label, item in artifacts.items():
        print(f"ARTIFACT {label} bytes={item['bytes']} sha256={item['sha256']}")
    print("BOOTUP_READY false")
    print("BOOTUP_AUTHORIZED false")
    print(f"BUILD_RECEIPT {output / 'build-receipt.json'}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BuildError as error:
        print(f"BUILD_PREFLIGHT_FAILED {error}", file=sys.stderr)
        sys.exit(2)
