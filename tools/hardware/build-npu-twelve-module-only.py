#!/usr/bin/env python3
"""Prepare or run the pinned NPU-twelve module-only host build.

Default invocation is read-only plan mode. Execution requires both --execute
and the frozen wrapper SHA supplied by the coordinator after review. This
operation has its own 12-GiB available-memory / 24-GiB initial-disk gate; it
does not call the full Image-profile builder or change that builder's gates.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import types
from typing import Any


GIB = 1024 ** 3
MIN_START_MEM = 12 * GIB
MIN_START_DISK = 24 * GIB

REPO = Path(__file__).resolve().parents[2]
S22 = Path("/home/corpunum/s22-linux")
BUILDS = S22 / "builds"
SOURCE = BUILDS / "npu-native-twelve-kernel-20261003"
OUTPUT = BUILDS / "npu-native-twelve-module-only-out-20261003"
FILTERED_SYMVERS = BUILDS / "npu-native-twelve-dependencies-20261003.symvers"
BASELINE_SYMVERS = BUILDS / "npu-native-eight-out-clang18-recipe-20261002/Module.symvers"
CONFIG_EXPORT = BUILDS / "native-config-export-20261002.config"

NPU_WORKER = Path("/home/corpunum/s22-workers/npu-msgid-validation-20261002")
SYMVERS_HELPER = NPU_WORKER / "tools/hardware/prepare-camera-modpost-symvers.py"
MONITOR_HELPER = Path(
    "/home/corpunum/s22-workers/npu-ten-module-build-20261002/"
    "tools/hardware/build-npu-six-profile.py"
)

SOURCE_COMMIT = "e9c3016233a72ceccb13e537f0b7ef72426582b9"
SOURCE_TREE = "f917408e1c3388c87a8ed8f0220e4f9c3c9c6a4e"
SOURCE_PARENT = "709ac38b573d092e300f3787d0d5ba97dd333c0a"
CONFIG_SHA256 = "d762d5fc71e369013ee36657d007063ce1f0faca9707d5f9ccfba2597b7fcd16"
BASELINE_SYMVERS_SHA256 = (
    "15fc69e815cb4da6cb3372f4b5141005ab2e770b0414b67f230f1b185bf03df7"
)
EXCLUDED_NPU_ROW_SHA256 = (
    "d1c251e9556acde53f523b84521ce9d55ce16d531f1e37b54117873852bfc900"
)
FILTERED_SYMVERS_SHA256 = (
    "add620bc3a3732654f23161e4c966c7076b020568683f527402c6253cb59a334"
)
FILTERED_HELPER_SHA256 = (
    "2404fab4a2ed469eab1930a7275c28fd3f5a0bbb1b23c88369338ca23cd26c67"
)
MONITOR_HELPER_SHA256 = (
    "56f39759e4a098562cbafd634a659b007be2a540a4ada6234962711be026e5d5"
)
PYTHON_SHA256 = "e50d468e8b0adfb05733f5b87b3cff34829c4a8c1aea50c865aa8bdfe4bb150f"
MODULE_LAYOUT_CRC = "0x0e3c515c"

PATCHES = (
    (
        REPO / "tools/hardware/npu-mailbox-msgid-validation.patch",
        "c8366edfab42090ac09a6c366ad3c535a62e13384dd494ed685bbfe524a64d14",
    ),
    (
        REPO / "tools/hardware/npu-fw-report-lock-unwind.patch",
        "71c2fa44f0fe42bd94ee416fb09453185dd418b408ba15937ec3c787a5e9bf5d",
    ),
)

CONFIG_LINES = (
    'CONFIG_LOCALVERSION="-g4e5c5ad7d950"',
    "# CONFIG_LOCALVERSION_AUTO is not set",
    "CONFIG_SHADOW_CALL_STACK=y",
    "CONFIG_LTO_NONE=y",
    "CONFIG_CFI_CLANG=y",
    "CONFIG_MODVERSIONS=y",
    "CONFIG_EXYNOS_NPU=m",
    "CONFIG_NPU_USE_HW_DEVICE=y",
    "CONFIG_NPU_USE_BOOT_IOCTL=y",
)

PINNED_TOOLS = (
    ("clang", Path("/usr/lib/llvm-18/bin/clang"), "8ef402d453d1ba4902e4ee0f0f847f6cfa01400c95aa43c24e97818b9c0e3f45"),
    ("ld.lld", Path("/usr/lib/llvm-18/bin/ld.lld"), "7ad9a0e8fe6d0e79b71172d731e33872c0274e49fceb7b516d774876d5a58ade"),
    ("llvm-ar", Path("/usr/lib/llvm-18/bin/llvm-ar"), "eedd2efbdee80acf60e17e10adeddf31be0347226245f935be95efa3ae00ec79"),
    ("llvm-ranlib", Path("/usr/lib/llvm-18/bin/llvm-ranlib"), "eedd2efbdee80acf60e17e10adeddf31be0347226245f935be95efa3ae00ec79"),
    ("llvm-nm", Path("/usr/lib/llvm-18/bin/llvm-nm"), "3f85dd567c2806f2c031e317871e7145f858072fc9595aba4dc33d2e3a671402"),
    ("llvm-objcopy", Path("/usr/lib/llvm-18/bin/llvm-objcopy"), "f52b9997b3c5019b4b3043e12b1ae2e821df67996ca344921c234c89c4d23e34"),
    ("llvm-objdump", Path("/usr/lib/llvm-18/bin/llvm-objdump"), "4f98b86448d23bd1f858c50e93fbfc799f1c3640961a95e3b6c89d229a1b91bb"),
    ("llvm-readelf", Path("/usr/lib/llvm-18/bin/llvm-readelf"), "8ed942a8c33f191480253ff7f236b7e49966c7441d12063d49dce9743aba9a6d"),
    ("llvm-strip", Path("/usr/lib/llvm-18/bin/llvm-strip"), "f52b9997b3c5019b4b3043e12b1ae2e821df67996ca344921c234c89c4d23e34"),
    ("llvm-size", Path("/usr/lib/llvm-18/bin/llvm-size"), "401e6835686b116baeaa8b98a5ebee54fbe66148b4a3fcda5436ef243a3653f3"),
    ("aarch64-linux-gnu-gcc", Path("/usr/bin/aarch64-linux-gnu-gcc"), "cd90adc7801f4595267f61a5d25bd3a0c6beb2f9f1f107ab919a97a12972dc9a"),
    ("aarch64-linux-gnu-ld", Path("/usr/bin/aarch64-linux-gnu-ld"), "7c903ac277dd1f5c4397277db865f12d8239fe45782f71afbeb3d42180a4b1ae"),
    ("aarch64-linux-gnu-nm", Path("/usr/bin/aarch64-linux-gnu-nm"), "96dbed79b11f6cc13b060dd5ca705a277bb5bdecd714df1c470ffaafb2513727"),
)
EXECUTABLES = (
    ("python3", Path("/usr/bin/python3"), PYTHON_SHA256),
    ("make", Path("/usr/bin/make"), "d78b8f1d099fbcfb6f2f49ab87223b9b68fb3956642f92d6ec6de812e8afa965"),
    ("nice", Path("/usr/bin/nice"), "2f92f24856db2e178fd91b02d28a5dbe0b4678de62738edfde0ece66b0f57c5d"),
    ("env", Path("/usr/bin/env"), "886e5fa8be716b03eeda48856e0f04493f2969fc148fb7812594ffac647fa050"),
)

FILTER_ENV = {"PATH": "/usr/bin:/bin", "LC_ALL": "C"}
BUILD_ENV = {
    "PATH": "/usr/lib/llvm-18/bin:/usr/bin:/bin",
    "LC_ALL": "C",
    "LOCALVERSION": "",
    "TMPDIR": "/tmp",
}


class BuildPreparationError(RuntimeError):
    pass


class StopRequested(Exception):
    def __init__(self, signum: int):
        super().__init__(f"received signal {signum}")
        self.signum = signum


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def coordinator_go_token(wrapper_sha256: str) -> str:
    return f"GO:NPU12-MODULE-ONLY:{SOURCE_COMMIT}:{wrapper_sha256}"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_path(path: Path, *, reject_symlink: bool = False) -> str:
    if reject_symlink and path.is_symlink():
        raise BuildPreparationError(f"refusing symlink input: {path}")
    if not path.is_file():
        raise BuildPreparationError(f"required regular file missing: {path}")
    return sha256_bytes(path.read_bytes())


def ensure_absent(path: Path) -> None:
    if path.is_symlink() or path.exists():
        raise BuildPreparationError(f"refusing existing output path: {path}")


def git_value(*args: str) -> str:
    result = subprocess.run(
        ["/usr/bin/git", "-C", str(SOURCE), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
        check=False,
    )
    if result.returncode != 0:
        raise BuildPreparationError(
            f"git {' '.join(args)} failed: {result.stderr.decode(errors='replace').strip()}"
        )
    return result.stdout.decode("utf-8", errors="strict").strip()


def mem_available_bytes() -> int:
    for line in Path("/proc/meminfo").read_text(encoding="ascii").splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) * 1024
    raise BuildPreparationError("MemAvailable is absent from /proc/meminfo")


def resource_snapshot() -> dict[str, int | str | bool]:
    available = mem_available_bytes()
    disk_free = shutil.disk_usage(BUILDS).free
    return {
        "captured_at_utc": utc_now(),
        "builds_path": str(BUILDS),
        "mem_available_bytes": available,
        "disk_free_bytes": disk_free,
        "minimum_start_memory_bytes": MIN_START_MEM,
        "minimum_start_disk_bytes": MIN_START_DISK,
        "memory_gate_met": available >= MIN_START_MEM,
        "disk_gate_met": disk_free >= MIN_START_DISK,
        "launch_resource_gate_met": available >= MIN_START_MEM and disk_free >= MIN_START_DISK,
    }


def verify_expected_hash(path: Path, expected: str, label: str, *, reject_symlink: bool = False) -> str:
    actual = sha256_path(path, reject_symlink=reject_symlink)
    if actual != expected:
        raise BuildPreparationError(f"{label} SHA-256 mismatch: {actual}")
    return actual


def verify_configuration() -> None:
    if CONFIG_EXPORT.is_symlink() or not CONFIG_EXPORT.is_file():
        raise BuildPreparationError("private config export is not a regular non-symlink file")
    data = CONFIG_EXPORT.read_bytes()
    if sha256_bytes(data) != CONFIG_SHA256:
        raise BuildPreparationError("private config export hash differs from pinned SHA-256")
    if len(data) != 236183:
        raise BuildPreparationError("private config export length differs from pinned size")
    lines = set(data.decode("ascii", errors="strict").splitlines())
    missing = [line for line in CONFIG_LINES if line not in lines]
    if missing:
        raise BuildPreparationError("required config line missing: " + "; ".join(missing))


def verify_source() -> dict[str, Any]:
    if SOURCE.is_symlink() or not SOURCE.is_dir():
        raise BuildPreparationError(f"source path is not a non-symlink directory: {SOURCE}")
    top = git_value("rev-parse", "--show-toplevel")
    head = git_value("rev-parse", "HEAD")
    tree = git_value("rev-parse", "HEAD^{tree}")
    parent = git_value("rev-parse", "HEAD^")
    status = git_value("status", "--porcelain=v1", "--untracked-files=all")
    changed = git_value("diff", "--name-only", f"{SOURCE_PARENT}..{SOURCE_COMMIT}").splitlines()
    expected_changed = [
        "drivers/vision/npu/core/npu-log.c",
        "drivers/vision/npu/core/npu-util-msgidgen.c",
    ]
    if Path(top).resolve() != SOURCE.resolve():
        raise BuildPreparationError("git top-level differs from the pinned kernel source path")
    if head != SOURCE_COMMIT or tree != SOURCE_TREE or parent != SOURCE_PARENT:
        raise BuildPreparationError("kernel source commit/tree/parent differs from pinned identity")
    if status:
        raise BuildPreparationError("kernel source worktree is not clean")
    if changed != expected_changed:
        raise BuildPreparationError(f"composed source delta differs from pin: {changed!r}")
    return {
        "worktree": str(SOURCE),
        "head": head,
        "tree": tree,
        "parent": parent,
        "clean": True,
        "changed_source_files": changed,
    }


def load_pinned_monitor() -> types.ModuleType:
    data = MONITOR_HELPER.read_bytes()
    digest = sha256_bytes(data)
    if digest != MONITOR_HELPER_SHA256:
        raise BuildPreparationError(f"monitor helper SHA-256 mismatch: {digest}")
    name = "pinned_npu_build_monitor"
    module = types.ModuleType(name)
    module.__file__ = str(MONITOR_HELPER)
    sys.modules[name] = module
    exec(compile(data, str(MONITOR_HELPER), "exec"), module.__dict__)
    expected = {
        "MIN_START_MEM": 12 * GIB,
        "MIN_START_DISK": 32 * GIB,
        "MIN_REMAINING_MEM": 8 * GIB,
        "MIN_REMAINING_DISK": 16 * GIB,
        "CLEANUP_STAGE_TIMEOUTS": (30.0, 15.0, 5.0),
    }
    for key, value in expected.items():
        if getattr(module, key, None) != value:
            raise BuildPreparationError(f"pinned monitor helper {key} differs from expected value")
    original_stop = module.stop_own_process_group
    cleanup_results: list[dict[str, Any]] = []

    def track_cleanup(process: subprocess.Popen[bytes]) -> bool:
        try:
            confirmed = original_stop(process)
        except BaseException as error:
            cleanup_results.append({
                "pid": process.pid,
                "confirmed": False,
                "error": f"{type(error).__name__}: {error}",
            })
            raise
        cleanup_results.append({"pid": process.pid, "confirmed": confirmed})
        return confirmed

    module.stop_own_process_group = track_cleanup
    module._cleanup_results = cleanup_results
    return module


def verify_toolchain() -> dict[str, Any]:
    tools: list[dict[str, str]] = []
    for name, path, expected in (*PINNED_TOOLS, *EXECUTABLES):
        actual = verify_expected_hash(path, expected, name)
        tools.append({"name": name, "path": str(path), "sha256": actual})
    if Path(sys.executable).resolve() != Path("/usr/bin/python3").resolve():
        raise BuildPreparationError(f"unexpected Python interpreter: {sys.executable}")
    if sha256_path(Path(sys.executable)) != PYTHON_SHA256:
        raise BuildPreparationError("running Python interpreter hash differs from pin")
    for path, expected_version in (
        (Path("/usr/lib/llvm-18/bin/clang"), "Ubuntu clang version 18.1.3 (1ubuntu1)"),
        (Path("/usr/lib/llvm-18/bin/ld.lld"), "Ubuntu LLD 18.1.3 (compatible with GNU linkers)"),
    ):
        result = subprocess.run(
            [str(path), "--version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=FILTER_ENV,
            check=False,
        )
        first_line = result.stdout.decode("utf-8", errors="replace").splitlines()[0]
        if result.returncode != 0 or first_line != expected_version:
            raise BuildPreparationError(f"unexpected version output from {path}: {first_line}")
    return {
        "python_executable": str(Path(sys.executable).resolve()),
        "python_version": sys.version.splitlines()[0],
        "clang_version_first_line": "Ubuntu clang version 18.1.3 (1ubuntu1)",
        "lld_version_first_line": "Ubuntu LLD 18.1.3 (compatible with GNU linkers)",
        "tools": tools,
    }


def verify_reserved_outputs() -> None:
    ensure_absent(OUTPUT)
    ensure_absent(FILTERED_SYMVERS)


def collect_plan() -> dict[str, Any]:
    if Path(__file__).resolve() != (REPO / "tools/hardware/build-npu-twelve-module-only.py").resolve():
        raise BuildPreparationError("wrapper is not at its pinned repository path")
    wrapper_sha = sha256_path(Path(__file__))
    verify_configuration()
    source = verify_source()
    baseline_hash = verify_expected_hash(
        BASELINE_SYMVERS, BASELINE_SYMVERS_SHA256,
        "native-eight full Module.symvers", reject_symlink=True,
    )
    symvers_helper_hash = verify_expected_hash(
        SYMVERS_HELPER, FILTERED_HELPER_SHA256, "Symvers filter helper", reject_symlink=True,
    )
    monitor = load_pinned_monitor()
    toolchain = verify_toolchain()
    patches = []
    for path, expected in PATCHES:
        digest = verify_expected_hash(path, expected, path.name, reject_symlink=True)
        patches.append({"file": str(path), "sha256": digest})
    verify_reserved_outputs()
    resources = resource_snapshot()
    return {
        "status": "plan_only_waiting_for_coordinator_go",
        "prepared_at_utc": utc_now(),
        "wrapper": {"path": str(Path(__file__).resolve()), "sha256": wrapper_sha},
        "coordinator_go_token_required": coordinator_go_token(wrapper_sha),
        "source": source,
        "patches_in_order": patches,
        "config": {"path": str(CONFIG_EXPORT), "sha256": CONFIG_SHA256, "bytes": 236183},
        "symbol_inputs": {
            "baseline_path": str(BASELINE_SYMVERS),
            "baseline_sha256": baseline_hash,
            "filter_helper": str(SYMVERS_HELPER),
            "filter_helper_sha256": symvers_helper_hash,
            "filtered_output": str(FILTERED_SYMVERS),
            "filtered_output_status": "absent",
            "filtered_expected_sha256": FILTERED_SYMVERS_SHA256,
            "module_layout_crc": MODULE_LAYOUT_CRC,
        },
        "monitor": {
            "helper": str(MONITOR_HELPER),
            "helper_sha256": MONITOR_HELPER_SHA256,
            "minimum_remaining_memory_bytes": monitor.MIN_REMAINING_MEM,
            "minimum_remaining_disk_bytes": monitor.MIN_REMAINING_DISK,
            "cleanup_stage_timeouts_seconds": list(monitor.CLEANUP_STAGE_TIMEOUTS),
        },
        "resources": resources,
        "output": {"path": str(OUTPUT), "status": "absent"},
        "execution": "NOT_RUN",
        "coordinator_go_required": True,
    }


def append_phase(stream, name: str, **fields: Any) -> None:
    record = {"time_utc": utc_now(), "phase": name, **fields}
    stream.write((json.dumps(record, sort_keys=True) + "\n").encode("utf-8"))
    stream.flush()
    os.fsync(stream.fileno())


def open_new(path: Path, mode: int = 0o600):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    return os.fdopen(descriptor, "wb")


def open_new_append(path: Path, mode: int = 0o600):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_APPEND, mode)
    return os.fdopen(descriptor, "wb")


def copy_config_exclusive() -> str:
    data = CONFIG_EXPORT.read_bytes()
    if sha256_bytes(data) != CONFIG_SHA256:
        raise BuildPreparationError("config export changed between preflight and copy")
    with open_new(OUTPUT / ".config") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    return sha256_path(OUTPUT / ".config", reject_symlink=True)


def run_owned_logged(command: list[str], cwd: Path, env: dict[str, str], log_path: Path,
                     monitor: types.ModuleType, phase_stream, phase: str) -> int:
    append_phase(phase_stream, f"{phase}_started", argv=command, environment=env,
                 cwd=str(cwd), log_path=str(log_path))
    with open_new(log_path) as log:
        process = subprocess.Popen(
            command, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            result = process.wait()
        except BaseException:
            try:
                stopped = monitor.stop_own_process_group(process)
            except BaseException as cleanup_error:
                append_phase(phase_stream, f"{phase}_cleanup_failed",
                             process_group_cleanup_confirmed=False,
                             detail=f"{type(cleanup_error).__name__}: {cleanup_error}")
                raise
            append_phase(phase_stream, f"{phase}_interrupted",
                         process_group_cleanup_confirmed=stopped)
            raise
        log.flush()
        os.fsync(log.fileno())
    append_phase(phase_stream, f"{phase}_finished", exit_code=result,
                 log_bytes=log_path.stat().st_size, log_sha256=sha256_path(log_path))
    return result


def write_final_monitor_phase(phase_info: dict[str, Any], *, phase: str,
                             exit_code: int | None, resource_abort: bool,
                             detail: str | None = None) -> None:
    phase_info.update({
        "phase": phase,
        "build_exit_code": exit_code,
        "resource_abort": resource_abort,
        "finished_at_utc": utc_now(),
    })
    if detail is not None:
        phase_info["wrapper_result_detail"] = detail
    path = OUTPUT / "build-phase.json"
    data = (json.dumps(phase_info, indent=2, sort_keys=True) + "\n").encode("utf-8")
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def verify_module_only_output() -> dict[str, Any]:
    expected = OUTPUT / "drivers/vision/npu.ko"
    module_paths = sorted(path for path in OUTPUT.rglob("*.ko") if path.is_file())
    if module_paths != [expected]:
        raise BuildPreparationError(f"unexpected module outputs: {[str(p) for p in module_paths]}")
    image = OUTPUT / "arch/arm64/boot/Image"
    if image.exists() or image.is_symlink():
        raise BuildPreparationError("module-only output unexpectedly contains an Image")
    return {"path": str(expected), "bytes": expected.stat().st_size,
            "sha256": sha256_path(expected, reject_symlink=True)}


def execute_build(plan: dict[str, Any], monitor: types.ModuleType) -> int:
    resources = resource_snapshot()
    plan["resources"] = resources
    if not resources["launch_resource_gate_met"]:
        raise BuildPreparationError("operation-specific 12-GiB/24-GiB start gate failed")
    OUTPUT.mkdir(mode=0o700)
    os.chmod(OUTPUT, 0o700)
    journal_path = OUTPUT / "module-only-phase.jsonl"
    with open_new_append(journal_path) as phase_stream:
        append_phase(phase_stream, "operation_started", source=plan["source"],
                     wrapper=plan["wrapper"], resources=plan["resources"])
        phase_info: dict[str, Any] | None = None
        try:
            # The pinned helper writes the filtered file exclusively and validates
            # the full input, excluded row, and module_layout CRC itself.
            filter_command = [
                "/usr/bin/python3", "-I", str(SYMVERS_HELPER),
                "--input", str(BASELINE_SYMVERS),
                "--output", str(FILTERED_SYMVERS),
                "--exclude-owner", "drivers/vision/npu",
                "--expected-input-sha256", BASELINE_SYMVERS_SHA256,
                "--expected-excluded-count", "1",
                "--expected-excluded-sha256", EXCLUDED_NPU_ROW_SHA256,
                "--expected-module-layout-crc", MODULE_LAYOUT_CRC,
            ]
            filter_log = OUTPUT / "symvers-filter.log"
            filter_rc = run_owned_logged(
                filter_command, SYMVERS_HELPER.parent, FILTER_ENV, filter_log,
                monitor, phase_stream, "symvers_filter",
            )
            if filter_rc != 0:
                raise BuildPreparationError(f"Symvers filter failed with exit code {filter_rc}")
            filtered_hash = verify_expected_hash(
                FILTERED_SYMVERS, FILTERED_SYMVERS_SHA256,
                "filtered dependency Symvers", reject_symlink=True,
            )
            append_phase(phase_stream, "symvers_verified", sha256=filtered_hash)

            copied_config_hash = copy_config_exclusive()
            append_phase(phase_stream, "config_copied", sha256=copied_config_hash)
            if copied_config_hash != CONFIG_SHA256:
                raise BuildPreparationError("copied config differs from pinned export")

            olddefconfig = [
                "/usr/bin/make", "-C", str(SOURCE), f"O={OUTPUT}",
                "ARCH=arm64", "LLVM=1", "CROSS_COMPILE=aarch64-linux-gnu-",
                "olddefconfig",
            ]
            olddefconfig_rc = run_owned_logged(
                olddefconfig, SOURCE, BUILD_ENV, OUTPUT / "olddefconfig.log",
                monitor, phase_stream, "olddefconfig",
            )
            if olddefconfig_rc != 0:
                raise BuildPreparationError(
                    f"olddefconfig failed with exit code {olddefconfig_rc}; module build refused"
                )
            config_after_hash = sha256_path(OUTPUT / ".config", reject_symlink=True)
            append_phase(phase_stream, "config_after_olddefconfig",
                         sha256=config_after_hash, expected_sha256=CONFIG_SHA256)
            if config_after_hash != CONFIG_SHA256:
                raise BuildPreparationError(
                    "olddefconfig changed the pinned config; module build refused"
                )
            release_path = OUTPUT / "include/config/kernel.release"
            if not release_path.is_file() or release_path.read_text().strip() != "5.10.260-g4e5c5ad7d950":
                raise BuildPreparationError("generated kernel.release differs from pinned release")

            source_after_prepare = verify_source()
            resources_after_prepare = resource_snapshot()
            append_phase(phase_stream, "pre_build_gate", source=source_after_prepare,
                         resources=resources_after_prepare)
            if not resources_after_prepare["launch_resource_gate_met"]:
                raise BuildPreparationError(
                    "operation-specific 12-GiB/24-GiB start gate failed before module build"
                )
            if source_after_prepare != plan["source"]:
                raise BuildPreparationError("source identity changed before module build")

            module_command = [
                "/usr/bin/nice", "-n", "10", "/usr/bin/env", "-i",
                "PATH=/usr/lib/llvm-18/bin:/usr/bin:/bin", "LC_ALL=C",
                "LOCALVERSION=", "TMPDIR=/tmp", "/usr/bin/make",
                "-C", str(SOURCE), f"O={OUTPUT}", "ARCH=arm64", "LLVM=1",
                "CROSS_COMPILE=aarch64-linux-gnu-",
                f"input-symdump={FILTERED_SYMVERS}", "-j1", "V=1",
                "drivers/vision/npu.ko",
            ]
            phase_info = {
                "operation": "npu_twelve_module_only",
                "executed_builder_sha256": MONITOR_HELPER_SHA256,
                "source": source_after_prepare,
                "config": {
                    "export_sha256": CONFIG_SHA256,
                    "output_sha256_before_build": config_after_hash,
                    "kernel_release": "5.10.260-g4e5c5ad7d950",
                },
                "symbol_inputs": {
                    "baseline_sha256": BASELINE_SYMVERS_SHA256,
                    "filtered_sha256": filtered_hash,
                    "module_layout_crc": MODULE_LAYOUT_CRC,
                },
                "toolchain": plan["toolchain"],
                "command": module_command,
                "resource_abort_thresholds": {
                    "minimum_mem_available_bytes": 8 * GIB,
                    "minimum_disk_free_bytes": 16 * GIB,
                },
                "bootup_ready": False,
                "bootup_authorized": False,
            }
            append_phase(phase_stream, "module_build_started", argv=module_command,
                         child_environment=BUILD_ENV, phase_info=phase_info)
            try:
                module_rc, resource_abort = monitor.run_monitored_build(
                    module_command, SOURCE, OUTPUT, BUILD_ENV, phase_info
                )
            except SystemExit as error:
                # A monitor SystemExit(0) is not a successful build result.
                detail = f"monitor helper raised SystemExit({error.code!r})"
                write_final_monitor_phase(
                    phase_info, phase="wrapper_failure", exit_code=None,
                    resource_abort=False, detail=detail,
                )
                append_phase(phase_stream, "module_build_failed", detail=detail,
                             exit_code=error.code, success=False,
                             cleanup_results=getattr(monitor, "_cleanup_results", []))
                return 2
            except BaseException as error:
                detail = f"monitor helper raised {type(error).__name__}: {error}"
                write_final_monitor_phase(
                    phase_info, phase="wrapper_failure", exit_code=None,
                    resource_abort=False, detail=detail,
                )
                append_phase(phase_stream, "module_build_failed", detail=detail,
                             success=False,
                             cleanup_results=getattr(monitor, "_cleanup_results", []))
                if isinstance(error, StopRequested):
                    return 128 + error.signum
                if isinstance(error, KeyboardInterrupt):
                    return 130
                return 2

            if resource_abort:
                # Check this before exit_code: a race can reap a zero-exit child
                # after the resource threshold fired. Abort must never pass.
                detail = f"resource monitor aborted build; make exit={module_rc}"
                write_final_monitor_phase(
                    phase_info, phase="resource_abort", exit_code=module_rc,
                    resource_abort=True, detail=detail,
                )
                append_phase(phase_stream, "module_build_aborted", exit_code=module_rc,
                             resource_abort=True, success=False,
                             cleanup_results=getattr(monitor, "_cleanup_results", []))
                return 3
            if module_rc != 0:
                detail = f"module make failed with exit code {module_rc}"
                write_final_monitor_phase(
                    phase_info, phase="build_failed", exit_code=module_rc,
                    resource_abort=False, detail=detail,
                )
                append_phase(phase_stream, "module_build_failed", exit_code=module_rc,
                             resource_abort=False, success=False)
                return 2

            module = verify_module_only_output()
            config_final_hash = sha256_path(OUTPUT / ".config", reject_symlink=True)
            if config_final_hash != CONFIG_SHA256:
                raise BuildPreparationError("module build changed the preserved config")
            final_source = verify_source()
            if final_source != plan["source"]:
                raise BuildPreparationError("source identity changed during module build")
            write_final_monitor_phase(
                phase_info, phase="build_finished", exit_code=0,
                resource_abort=False,
            )
            append_phase(phase_stream, "module_build_finished", exit_code=0,
                         resource_abort=False, module=module,
                         final_config_sha256=config_final_hash,
                         source=final_source, success=True)
            return 0
        except StopRequested as error:
            if phase_info is not None:
                write_final_monitor_phase(
                    phase_info, phase="wrapper_interrupted", exit_code=None,
                    resource_abort=False, detail=str(error),
                )
            append_phase(phase_stream, "operation_interrupted", detail=str(error), success=False)
            return 128 + error.signum
        except KeyboardInterrupt:
            if phase_info is not None:
                write_final_monitor_phase(
                    phase_info, phase="wrapper_interrupted", exit_code=None,
                    resource_abort=False, detail="KeyboardInterrupt",
                )
            append_phase(phase_stream, "operation_interrupted", detail="KeyboardInterrupt", success=False)
            return 130
        except BaseException as error:
            if phase_info is not None:
                write_final_monitor_phase(
                    phase_info, phase="wrapper_failure", exit_code=None,
                    resource_abort=False,
                    detail=f"{type(error).__name__}: {error}",
                )
            append_phase(phase_stream, "operation_failed",
                         error_type=type(error).__name__, detail=str(error), success=False)
            return 2


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--plan-only", action="store_true",
                      help="validate pinned inputs and print a read-only plan (default)")
    mode.add_argument("--execute", action="store_true",
                      help="execute only after reviewed coordinator GO")
    parser.add_argument("--expect-wrapper-sha256",
                        help="required frozen wrapper SHA for --execute")
    parser.add_argument("--coordinator-go-token",
                        help="exact source- and wrapper-bound coordinator GO token required for --execute")
    return parser.parse_args(argv)


def install_signal_handlers() -> None:
    def stop(signum: int, _frame: Any) -> None:
        raise StopRequested(signum)

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    install_signal_handlers()
    try:
        plan = collect_plan()
        if args.execute:
            if not args.expect_wrapper_sha256:
                raise BuildPreparationError("--execute requires the frozen --expect-wrapper-sha256")
            if args.expect_wrapper_sha256 != plan["wrapper"]["sha256"]:
                raise BuildPreparationError("running wrapper SHA differs from reviewed frozen SHA")
            expected_go = coordinator_go_token(plan["wrapper"]["sha256"])
            if args.coordinator_go_token != expected_go:
                raise BuildPreparationError("--execute requires the exact coordinator GO token")
            monitor = load_pinned_monitor()
            result = execute_build(plan, monitor)
            if result == 0:
                print(json.dumps({"status": "host_module_build_pass", "output": str(OUTPUT)},
                                 sort_keys=True))
            else:
                print(json.dumps({"status": "host_module_build_failed", "exit_code": result,
                                  "output_preserved": str(OUTPUT)}, sort_keys=True), file=sys.stderr)
            return result
        plan["mode"] = "read_only_plan"
        plan["launch_ready"] = bool(plan["resources"]["launch_resource_gate_met"])
        print(json.dumps(plan, sort_keys=True))
        return 0
    except SystemExit as error:
        # Never turn a helper/wrapper SystemExit(0) into apparent success.
        detail = f"SystemExit({error.code!r}) treated as wrapper failure"
        print(detail, file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("wrapper interrupted before/while preparing a phase", file=sys.stderr)
        return 130
    except BaseException as error:
        print(f"NPU module-only wrapper refused: {type(error).__name__}: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    # A nonzero resource abort or any wrapper exception remains a nonzero exit.
    raise SystemExit(main())
