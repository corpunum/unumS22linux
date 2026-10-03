#!/usr/bin/env python3
"""Extract and failure-test the pinned r0s v10.1 sensor clock path."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest
import urllib.error
import urllib.request


ROOT = Path(__file__).resolve().parents[2]
PATCH = ROOT / "tools/hardware/camera-sensor-clock-unwind.patch"
HARNESS = ROOT / "tools/hardware/camera-sensor-clock-unwind-harness.c"
SOURCE_RELATIVE = Path(
    "drivers/media/platform/exynos/camera/ischain/is-v10_1_0/setup-is-sensor.c")
CALLER_RELATIVE = Path(
    "drivers/media/platform/exynos/camera/is-device-sensor_v2.c")
HEADER_RELATIVE = Path(
    "drivers/media/platform/exynos/camera/is-device-sensor.h")
VIDEO_RELATIVE = Path(
    "drivers/media/platform/exynos/camera/is-video.c")
SETUP_RELATIVE = Path(
    "drivers/media/platform/exynos/camera/ischain/is-v10_1_0/setup-is.c")
VFS_RELATIVE = Path("fs/file_table.c")
VB2_CORE_RELATIVE = Path("drivers/media/common/videobuf2/videobuf2-core.c")
VB2_V4L2_RELATIVE = Path("drivers/media/common/videobuf2/videobuf2-v4l2.c")
VIDEO_HEADER_RELATIVE = Path(
    "drivers/media/platform/exynos/camera/include/is-video.h")
VB2_HEADER_RELATIVE = Path("include/media/videobuf2-core.h")
CAMERA_KBUILD_RELATIVE = Path("drivers/media/platform/exynos/camera/Makefile")
S5E9925_CONFIG_RELATIVE = Path("arch/arm64/configs/s5e9925_defconfig")
ISCHAIN_HEADER_RELATIVE = Path(
    "drivers/media/platform/exynos/camera/is-device-ischain.h")
RESOURCEMGR_RELATIVE = Path(
    "drivers/media/platform/exynos/camera/is-resourcemgr.c")
V4L2_DEV_RELATIVE = Path("drivers/media/v4l2-core/v4l2-dev.c")
CORE_RELATIVE = Path("drivers/media/platform/exynos/camera/is-core.c")
CORE_HEADER_RELATIVE = Path("drivers/media/platform/exynos/camera/is-core.h")
SENSOR_VIDEO_NODE_RELATIVE = Path(
    "drivers/media/platform/exynos/camera/is-video-sensor.c")
ISCHAIN_VIDEO_NODE_RELATIVE = Path(
    "drivers/media/platform/exynos/camera/is-video-byrp.c")
IS_CONFIG_RELATIVE = Path(
    "drivers/media/platform/exynos/camera/ischain/is-v10_1_0/is-config.h")
COMMON_CONFIG_RELATIVE = Path(
    "drivers/media/platform/exynos/camera/include/is-common-config.h")
PINNED_SOURCE = Path(
    "/home/corpunum/s22-linux/lineage/android_kernel_samsung_s5e9925")
PINNED_COMMIT = "4e5c5ad7d950e4de0688b5663965f2075654b2ad"
PINNED_TREE = "5c46cbe12dadbcdb64eec4344c9e8ff0f8a75dee"
PINNED_DERIVED = Path(os.environ.get(
    "CAMERA_CLOCK_DERIVED_ROOT",
    "/home/corpunum/s22-workers/camera-kernel-build-20260927",
)).expanduser()
DERIVED_COMMIT = "3fca50941422439b2019db2e4a3dc1016b2138a1"
DERIVED_TREE = "5aad5cf1dbaa0f430377737141f0547e971b0a2d"
SOURCE_SHA256 = "be9ea9769e2b1678cea68f1a86514aa0ccd559bfa9316840a1a83268bfef1182"
CALLER_SHA256 = "b88e203fa7ca7fdcf109f969d516601a9cba4702c98a3a0b338b3a6dbcf55265"
SETUP_SHA256 = "0e3668ca54f6d60abb8a22dc3a18d15b441fcf3c5154b2f0ee61e481d37b88b2"
HEADER_SHA256 = "ee236f792d25ccd0ef63519b461878d933c29dc376627e6e3861fe816ce10d7e"
VIDEO_SHA256 = "66eceae9b3b6d6b8e3e6e600814adfad26d313b304ea09497755e04fd977f232"
VFS_SHA256 = "d74699023b0062aefbbb35462e155c7485dd3264fd2476aecef0e4fc48f3cc0f"
VB2_CORE_SHA256 = "f67ef215d28df95911b8b0568d13b0edcc42c992e2d16f39d63b0f2f7c3e9583"
VB2_V4L2_SHA256 = "9ec6c8e61acb1df5259fb9023aa0236a31c80ba792cc7e84d63b3907fbb5d065"
VIDEO_HEADER_SHA256 = "8028c9208dd2e6cf1c15b2005bf49e4f28fc2ea8bbc6228a89fa615a60c6765c"
VB2_HEADER_SHA256 = "a6c56a319eb5e70ca1f6333a6ca9906a90807b812e3c03a21d76f66f45135627"
CAMERA_KBUILD_SHA256 = "3ca13acb7cda8795f5abe5f31af455b506a982353ea4fae9afbf6eab79589024"
S5E9925_CONFIG_SHA256 = "de87dbdff5a4082b2aa6fd511a69b9766ddfb0369738c76885c9766178f2b4f5"
ISCHAIN_HEADER_SHA256 = "c51564c10e835b44352930156714d1dfb2fc69de887242e32de7046a55e788a9"
RESOURCEMGR_SHA256 = "79dea9151d3294c082856d47be30bc3ffc7e165df8109be5fb62b35b338bcd7d"
V4L2_DEV_SHA256 = "93290477b04503bd2da5334a9c795a87669948bde00c3cb82430d16271a1e737"
CORE_SHA256 = "2f81e7342a74098e284cef0679afce4ef7e8be34bd81bac9c215bf7f695e1594"
CORE_HEADER_SHA256 = "3b7183a980cd053510726011f3d73eba5ef03622f1657f55ecefc5a70f483518"
SENSOR_VIDEO_NODE_SHA256 = "62ad8e2eddea8bccf5e18b91d14dc90c49ee452d0ff8bea90f69a2b18db31a40"
ISCHAIN_VIDEO_NODE_SHA256 = "4f8f21136765d3368804340e8559fc129c8fd4180684df35d87e2b75a68d3f0e"
IS_CONFIG_SHA256 = "5f06bf13d9f626c084a88cf3f1e045e7d709e26d86efcd6cf74ef7806ee52738"
COMMON_CONFIG_SHA256 = "88bc87df5cc2050a6342952ecfe6ebdae5724980544572ba17f0fda6a1973726"
PINNED_FILES = {
    SOURCE_RELATIVE: SOURCE_SHA256,
    CALLER_RELATIVE: CALLER_SHA256,
    SETUP_RELATIVE: SETUP_SHA256,
    HEADER_RELATIVE: HEADER_SHA256,
    VIDEO_RELATIVE: VIDEO_SHA256,
    VFS_RELATIVE: VFS_SHA256,
    VB2_CORE_RELATIVE: VB2_CORE_SHA256,
    VB2_V4L2_RELATIVE: VB2_V4L2_SHA256,
    VIDEO_HEADER_RELATIVE: VIDEO_HEADER_SHA256,
    VB2_HEADER_RELATIVE: VB2_HEADER_SHA256,
    CAMERA_KBUILD_RELATIVE: CAMERA_KBUILD_SHA256,
    S5E9925_CONFIG_RELATIVE: S5E9925_CONFIG_SHA256,
    ISCHAIN_HEADER_RELATIVE: ISCHAIN_HEADER_SHA256,
    RESOURCEMGR_RELATIVE: RESOURCEMGR_SHA256,
    V4L2_DEV_RELATIVE: V4L2_DEV_SHA256,
    CORE_RELATIVE: CORE_SHA256,
    CORE_HEADER_RELATIVE: CORE_HEADER_SHA256,
    SENSOR_VIDEO_NODE_RELATIVE: SENSOR_VIDEO_NODE_SHA256,
    ISCHAIN_VIDEO_NODE_RELATIVE: ISCHAIN_VIDEO_NODE_SHA256,
    IS_CONFIG_RELATIVE: IS_CONFIG_SHA256,
    COMMON_CONFIG_RELATIVE: COMMON_CONFIG_SHA256,
}
SOURCE_URL = "https://raw.githubusercontent.com/LineageOS/android_kernel_samsung_s5e9925"
MAX_SOURCE_BYTES = 512 * 1024
SOURCE_FETCH_TIMEOUT = 5
PATCHED_FUNCTIONS = (
    "exynos9925_is_csi_gate",
    "exynos9925_is_sensor_iclk_cfg",
    "exynos9925_is_sensor_iclk_on",
    "exynos9925_is_sensor_iclk_off",
    "exynos_is_sensor_iclk_cfg",
    "exynos_is_sensor_iclk_on",
    "exynos_is_sensor_iclk_off",
)
CALLER_FUNCTIONS = (
    "is_sensor_iclk_on", "is_sensor_iclk_off", "is_sensor_suspend",
    "is_sensor_runtime_suspend", "is_sensor_runtime_resume",
)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def checked_revision(repository: Path, commit: str, tree: str) -> None:
    result = subprocess.run(
        ["git", "-C", str(repository), "rev-parse", "HEAD", "HEAD^{tree}"],
        capture_output=True, text=True, check=False,
    )
    if result.returncode:
        raise AssertionError(f"cannot identify pinned camera source {repository}: {result.stderr}")
    actual = result.stdout.splitlines()
    if actual != [commit, tree]:
        raise AssertionError(f"camera source identity differs at {repository}: {actual}")
    status = subprocess.run(
        ["git", "-C", str(repository), "status", "--porcelain"],
        capture_output=True, text=True, check=False,
    )
    if status.returncode or status.stdout:
        raise AssertionError(f"camera source worktree is not clean: {repository}")


def pinned_blob(repository: Path, relative: Path, expected_sha256: str) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(repository), "show", f"HEAD:{relative.as_posix()}"],
        capture_output=True, check=False,
    )
    if result.returncode:
        raise AssertionError(f"cannot read pinned source blob {relative}: {result.stderr.decode(errors='replace')}")
    if len(result.stdout) > MAX_SOURCE_BYTES or sha256(result.stdout) != expected_sha256:
        raise AssertionError(f"pinned source blob hash/size mismatch: {relative}")
    working = repository / relative
    if not working.is_file() or working.is_symlink():
        raise AssertionError(f"pinned working source is not a regular file: {working}")
    if working.stat().st_size > MAX_SOURCE_BYTES or sha256(working.read_bytes()) != expected_sha256:
        raise AssertionError(f"working source differs from pinned blob: {relative}")
    return result.stdout


def fetch_pinned_source(relative: Path) -> bytes:
    expected_sha256 = PINNED_FILES[relative]
    url = f"{SOURCE_URL}/{PINNED_COMMIT}/{relative.as_posix()}"
    request = urllib.request.Request(
        url, headers={"User-Agent": "S22-camera-clock-ownership-host-test/1"},
    )
    try:
        with urllib.request.urlopen(request, timeout=SOURCE_FETCH_TIMEOUT) as response:
            if response.geturl() != url:
                raise AssertionError(f"pinned source redirected unexpectedly: {response.geturl()}")
            length = response.headers.get("Content-Length")
            if length is not None:
                if not re.fullmatch(r"[0-9]+", length):
                    raise AssertionError(f"malformed pinned source Content-Length: {relative}")
                if int(length) > MAX_SOURCE_BYTES:
                    raise AssertionError(f"pinned source Content-Length exceeds cap: {relative}")
                expected_length = int(length)
            else:
                expected_length = None
            data = response.read(MAX_SOURCE_BYTES + 1)
    except urllib.error.HTTPError as error:
        raise AssertionError(f"pinned source request failed (HTTP {error.code}): {relative}") from error
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise AssertionError(f"pinned source fetch unavailable for {relative}: {error}") from error
    if len(data) > MAX_SOURCE_BYTES:
        raise AssertionError(f"pinned source body exceeds cap: {relative}")
    if expected_length is not None and len(data) != expected_length:
        raise AssertionError(f"pinned source Content-Length mismatch: {relative}")
    if sha256(data) != expected_sha256:
        raise AssertionError(f"pinned public source hash mismatch: {relative}")
    return data


def load_pinned_sources() -> tuple[dict[Path, bytes], str]:
    configured = os.environ.get("CAMERA_CLOCK_SOURCE_TREE")
    force_public = os.environ.get("CAMERA_CLOCK_FORCE_PUBLIC") == "1"
    if configured and not force_public:
        repository = Path(configured).expanduser()
        checked_revision(repository, PINNED_COMMIT, PINNED_TREE)
        return {
            relative: pinned_blob(repository, relative, digest)
            for relative, digest in PINNED_FILES.items()
        }, f"verified configured source {PINNED_COMMIT}"
    if PINNED_SOURCE.is_dir() and not force_public:
        checked_revision(PINNED_SOURCE, PINNED_COMMIT, PINNED_TREE)
        return {
            relative: pinned_blob(PINNED_SOURCE, relative, digest)
            for relative, digest in PINNED_FILES.items()
        }, f"verified clean private source {PINNED_COMMIT}"
    return (
        {relative: fetch_pinned_source(relative) for relative in PINNED_FILES},
        f"public source fixtures pinned at {PINNED_COMMIT}",
    )


def extract_function(source: str, name: str) -> str:
    """Extract one complete C function body from the exact pinned source."""
    pattern = re.compile(
        r"^(?:static\s+)?(?:inline\s+)?int\s+" + re.escape(name) + r"\s*\(",
        re.MULTILINE,
    )
    match = None
    for candidate in pattern.finditer(source):
        brace_candidate = source.find("{", candidate.end())
        semicolon_candidate = source.find(";", candidate.end())
        if (brace_candidate >= 0 and
                (semicolon_candidate < 0 or brace_candidate < semicolon_candidate)):
            match = candidate
            break
    if match is None:
        raise ValueError(f"function not found: {name}")
    start = match.start()
    brace = source.find("{", match.end())
    if brace < 0:
        raise ValueError(f"function body missing: {name}")
    depth = 0
    in_string = False
    escaped = False
    for index in range(brace, len(source)):
        char = source[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[start:index + 1] + "\n"
    raise ValueError(f"unbalanced C body: {name}")


def extract_enum(source: str, name: str) -> str:
    start = source.find(name)
    if start < 0:
        raise ValueError(f"enum not found: {name}")
    brace = source.find("{", start)
    depth = 0
    for index in range(brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                end = source.find(";", index)
                if end < 0:
                    raise ValueError(f"enum terminator missing: {name}")
                return source[start:end + 1] + "\n"
    raise ValueError(f"unbalanced enum: {name}")


def extract_void_or_int_function(source: str, declaration: str) -> str:
    start = source.find(declaration)
    if start < 0:
        raise ValueError(f"function declaration not found: {declaration}")
    brace = source.find("{", start + len(declaration))
    if brace < 0:
        raise ValueError(f"function body missing: {declaration}")
    depth = 0
    in_string = False
    escaped = False
    for index in range(brace, len(source)):
        char = source[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[start:index + 1]
    raise ValueError(f"unbalanced function body: {declaration}")


PATCHED_SOURCE_FILES = (
    SOURCE_RELATIVE, HEADER_RELATIVE, CALLER_RELATIVE, VIDEO_HEADER_RELATIVE,
    VIDEO_RELATIVE, CORE_RELATIVE,
)


def apply_patch_to_temporary_source(sources: dict[Path, bytes], temp_root: Path) -> dict[Path, bytes]:
    for relative in PATCHED_SOURCE_FILES:
        target = temp_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(sources[relative])
    check = subprocess.run(
        ["git", "apply", "--check", str(PATCH)], cwd=temp_root,
        capture_output=True, text=True, check=False,
    )
    if check.returncode:
        raise AssertionError("clock patch does not apply to temporary pinned source: " + check.stderr)
    applied = subprocess.run(
        ["git", "apply", str(PATCH)], cwd=temp_root,
        capture_output=True, text=True, check=False,
    )
    if applied.returncode:
        raise AssertionError("cannot apply clock patch to temporary source: " + applied.stderr)
    return {relative: (temp_root / relative).read_bytes() for relative in PATCHED_SOURCE_FILES}


def harness_source(sources: dict[Path, bytes], *, patched: bool) -> str:
    current = sources
    if patched:
        with tempfile.TemporaryDirectory(prefix="camera-clock-patch-source-") as temporary:
            current = apply_patch_to_temporary_source(sources, Path(temporary))
    functions = [
        extract_function(current[SOURCE_RELATIVE].decode("utf-8"), name)
        for name in PATCHED_FUNCTIONS
    ]
    functions.extend(
        extract_function(current[CALLER_RELATIVE].decode("utf-8"), name)
        for name in CALLER_FUNCTIONS
    )
    functions.append(
        extract_function(current[VIDEO_RELATIVE].decode("utf-8"), "is_video_close")
    )
    template = HARNESS.read_text(encoding="utf-8")
    marker = "/* CAMERA_SENSOR_CLOCK_FUNCTIONS */"
    if template.count(marker) != 1:
        raise AssertionError("C harness extraction marker must appear exactly once")
    enum_marker = "/* CAMERA_SENSOR_STATE_ENUM */"
    if template.count(enum_marker) != 1:
        raise AssertionError("C harness enum marker must appear exactly once")
    state_enum = extract_enum(current[HEADER_RELATIVE].decode("utf-8"), "enum is_sensor_state")
    template = template.replace(enum_marker, state_enum)
    return template.replace(marker, "\n\n".join(functions))


def shutdown_harness_source(sources: dict[Path, bytes], *, patched: bool) -> str:
    with tempfile.TemporaryDirectory(prefix="camera-clock-shutdown-source-") as temporary:
        proposed = apply_patch_to_temporary_source(sources, Path(temporary))
    current = proposed if patched else sources
    core = current[CORE_RELATIVE].decode("utf-8")
    functions = []
    if patched:
        functions.append(extract_void_or_int_function(
            core, "static bool is_sensor_clock_ownership_unknown(struct is_core *core)"))
        caller = current[CALLER_RELATIVE].decode("utf-8")
        functions.append(extract_void_or_int_function(
            caller,
            "static bool is_sensor_core_clock_ownership_unknown(struct is_core *core,\n\tstruct is_device_sensor *device)"))
    functions.extend((
        extract_void_or_int_function(core, "void is_cleanup(struct is_core *core)"),
        extract_void_or_int_function(core,
                                     "static void is_shutdown(struct platform_device *pdev)"),
        extract_function(sources[RESOURCEMGR_RELATIVE].decode("utf-8"),
                         "is_reboot_handler"),
        extract_void_or_int_function(
            current[CALLER_RELATIVE].decode("utf-8"),
            "static void is_sensor_instanton(struct work_struct *data)"),
    ))
    template = HARNESS.read_text(encoding="utf-8")
    marker = "/* CAMERA_SHUTDOWN_FUNCTIONS */"
    if template.count(marker) != 1:
        raise AssertionError("shutdown harness extraction marker must appear exactly once")
    enum_marker = "/* CAMERA_SHUTDOWN_SENSOR_ENUM */"
    if template.count(enum_marker) != 1:
        raise AssertionError("shutdown sensor enum marker must appear exactly once")
    state_enum = extract_enum(proposed[HEADER_RELATIVE].decode("utf-8"),
                              "enum is_sensor_state")
    template = template.replace(enum_marker, state_enum)
    return template.replace(marker, "\n\n".join(functions))


def compile_and_run(source: str, compiler: str, temp_root: Path,
                    *, baseline: bool, optimization: str) -> str:
    c_source = temp_root / ("camera-clock-baseline.c" if baseline else "camera-clock-patched.c")
    c_source.write_text(source, encoding="utf-8")
    binary = temp_root / (("clock-baseline-" if baseline else "clock-patched-") + optimization[2:])
    command = [
        compiler, "-std=gnu89", "-Wall", "-Wextra", "-Werror",
        "-Wno-unused-parameter", "-Wno-unused-function",
        "-Wno-unused-but-set-variable", optimization,
    ]
    if baseline:
        command.append("-DCAMERA_EXPECT_BASELINE")
    command.extend([str(c_source), "-o", str(binary)])
    build = subprocess.run(command, capture_output=True, text=True, check=False)
    if build.returncode:
        raise AssertionError(f"extracted C compile failed ({optimization}): {build.stderr}")
    run = subprocess.run([str(binary)], capture_output=True, text=True, check=False)
    expected = "BASELINE_REPRODUCED" if baseline else "PASS: extracted clock quarantine lifecycle"
    if run.returncode or expected not in run.stdout:
        raise AssertionError(
            f"extracted C run failed ({optimization}, baseline={baseline}): "
            f"stdout={run.stdout!r}, stderr={run.stderr!r}"
        )
    return run.stdout.strip()


def compile_shutdown_and_run(source: str, compiler: str, temp_root: Path,
                              *, baseline: bool, optimization: str) -> str:
    c_source = temp_root / "camera-shutdown-quarantine.c"
    c_source.write_text(source, encoding="utf-8")
    binary = temp_root / (("shutdown-baseline-" if baseline else "shutdown-patched-")
                          + optimization[2:])
    command = [
        compiler, "-std=gnu89", "-Wall", "-Wextra", "-Werror",
        "-Wno-unused-parameter", "-Wno-unused-function",
        "-Wno-unused-but-set-variable", optimization,
        "-DCAMERA_SHUTDOWN_ONLY",
    ]
    if baseline:
        command.append("-DCAMERA_EXPECT_SHUTDOWN_BASELINE")
    command.extend([str(c_source), "-o", str(binary)])
    build = subprocess.run(command, capture_output=True, text=True, check=False)
    if build.returncode:
        raise AssertionError(f"shutdown extracted C compile failed ({optimization}): {build.stderr}")
    run = subprocess.run([str(binary)], capture_output=True, text=True, check=False)
    expected = ("BASELINE_SHUTDOWN_REPRODUCED" if baseline
                else "PASS: extracted terminal shutdown quarantine")
    if run.returncode or expected not in run.stdout:
        raise AssertionError(
            f"shutdown extracted C run failed ({optimization}, baseline={baseline}): "
            f"stdout={run.stdout!r}, stderr={run.stderr!r}"
        )
    return run.stdout.strip()


class CameraSensorClockUnwindTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.compiler = shutil.which("cc")
        if not cls.compiler:
            raise RuntimeError("host C compiler is required for extracted-C regressions")
        cls.sources, cls.source_description = load_pinned_sources()
        cls.baseline_setup = cls.sources[SOURCE_RELATIVE]
        cls.caller = cls.sources[CALLER_RELATIVE]
        cls.setup_helpers = cls.sources[SETUP_RELATIVE]
        cls.video = cls.sources[VIDEO_RELATIVE]
        cls.baseline_text = cls.baseline_setup.decode("utf-8")
        cls.caller_text = cls.caller.decode("utf-8")
        cls.setup_helpers_text = cls.setup_helpers.decode("utf-8")
        cls.video_text = cls.video.decode("utf-8")

    def test_pinned_source_and_patch_apply(self) -> None:
        self.assertIn(PINNED_COMMIT, self.source_description)
        if os.environ.get("CAMERA_CLOCK_FORCE_PUBLIC") == "1":
            self.assertIn("public source fixtures", self.source_description)
        patch_headers = re.findall(
            r"^diff --git (.+)$", PATCH.read_text(encoding="utf-8"), re.MULTILINE,
        )
        expected_headers = [
            f"a/{relative.as_posix()} b/{relative.as_posix()}"
            for relative in PATCHED_SOURCE_FILES
        ]
        self.assertEqual(patch_headers, expected_headers)
        with tempfile.TemporaryDirectory(prefix="camera-clock-patch-base-") as temporary:
            patched = apply_patch_to_temporary_source(self.sources, Path(temporary))
            self.assertNotEqual(patched[SOURCE_RELATIVE], self.baseline_setup)
            text = patched[SOURCE_RELATIVE].decode("utf-8")
            for function in PATCHED_FUNCTIONS:
                self.assertIn(function, text)
            self.assertEqual(
                extract_function(text, "exynos9925_is_sensor_mclk_on"),
                extract_function(self.baseline_text, "exynos9925_is_sensor_mclk_on"),
            )
            self.assertEqual(
                extract_function(text, "exynos9925_is_sensor_mclk_off"),
                extract_function(self.baseline_text, "exynos9925_is_sensor_mclk_off"),
            )

    def test_optional_private_derived_tree_apply_check(self) -> None:
        if not PINNED_DERIVED.is_dir():
            self.skipTest("private derived source tree unavailable; public pinned fixtures remain tested")
        checked_revision(PINNED_DERIVED, DERIVED_COMMIT, DERIVED_TREE)
        derived_relevant = PATCHED_SOURCE_FILES + (
            SETUP_RELATIVE, VIDEO_HEADER_RELATIVE, ISCHAIN_HEADER_RELATIVE,
            CAMERA_KBUILD_RELATIVE, S5E9925_CONFIG_RELATIVE,
        )
        derived = {
            relative: pinned_blob(PINNED_DERIVED, relative, PINNED_FILES[relative])
            for relative in derived_relevant
        }
        for relative in derived_relevant:
            self.assertEqual(derived[relative], self.sources[relative])
        with tempfile.TemporaryDirectory(prefix="camera-clock-patch-derived-") as temporary:
            patched = apply_patch_to_temporary_source(derived, Path(temporary))
            self.assertNotEqual(patched[SOURCE_RELATIVE], derived[SOURCE_RELATIVE])

    def test_pinned_clock_helper_contract_and_actual_caller_order(self) -> None:
        enable = extract_function(self.setup_helpers_text, "is_enable")
        disable = extract_function(self.setup_helpers_text, "is_disable")
        self.assertIn("ret = clk_prepare_enable(clk);", enable)
        self.assertIn("return ret;", enable)
        self.assertIn("clk_disable_unprepare(clk);", disable)
        self.assertIn("return 0;", disable)
        missing_clock = disable.index("if (IS_ERR_OR_NULL(clk))")
        disable_call = disable.index("clk_disable_unprepare(clk);")
        self.assertLess(missing_clock, disable_call)
        self.assertIn("return -EINVAL;", disable[missing_clock:disable_call])
        self.assertNotIn("ret = clk_disable_unprepare", disable)
        caller = extract_function(self.caller_text, "is_sensor_iclk_on")
        self.assertLess(caller.index("pdata->iclk_cfg("), caller.index("pdata->iclk_on("))
        self.assertLess(caller.index("pdata->iclk_on("), caller.index("set_bit(IS_SENSOR_ICLK_ON"))

    def test_quarantine_guards_cover_sensor_lifecycle_before_side_effects(self) -> None:
        with tempfile.TemporaryDirectory(prefix="camera-clock-lifecycle-source-") as temporary:
            patched = apply_patch_to_temporary_source(self.sources, Path(temporary))
        caller = patched[CALLER_RELATIVE].decode("utf-8")
        video = patched[VIDEO_RELATIVE].decode("utf-8")

        on = extract_function(caller, "is_sensor_iclk_on")
        off = extract_function(caller, "is_sensor_iclk_off")
        self.assertLess(on.index("IS_SENSOR_ICLK_UNKNOWN"), on.index("pdata->iclk_cfg("))
        self.assertLess(off.index("IS_SENSOR_ICLK_UNKNOWN"), off.index("pdata->iclk_off("))
        self.assertIn("if (ret == -EUCLEAN)\n\t\t\tset_bit(IS_SENSOR_ICLK_UNKNOWN", on)
        self.assertIn("if (ret == -EUCLEAN)\n\t\t\tset_bit(IS_SENSOR_ICLK_UNKNOWN", off)
        self.assertNotIn("mutex_lock(&device->mutex_reboot)", off)

        probe = extract_function(caller, "is_sensor_probe")
        opened = extract_function(caller, "is_sensor_open")
        closed = extract_function(caller, "is_sensor_close")
        suspend = extract_function(caller, "is_sensor_suspend")
        runtime_suspend = extract_function(caller, "is_sensor_runtime_suspend")
        runtime_resume = extract_function(caller, "is_sensor_runtime_resume")
        sensor_start_worker = extract_void_or_int_function(
            caller, "static void is_sensor_instanton(struct work_struct *data)")
        self.assertLess(probe.index("IS_SENSOR_ICLK_UNKNOWN"), probe.index("memset(&device->v4l2_dev"))
        self.assertLess(opened.index("IS_SENSOR_ICLK_UNKNOWN"), opened.index("mutex_lock("))
        self.assertLess(closed.index("IS_SENSOR_ICLK_UNKNOWN"), closed.index("is_sensor_front_stop("))
        self.assertLess(closed.index("IS_SENSOR_ICLK_UNKNOWN"), closed.index("is_resource_put("))
        self.assertLess(suspend.index("IS_SENSOR_ICLK_UNKNOWN"), suspend.index("is_vendor_sensor_suspend("))
        self.assertLess(runtime_suspend.index("IS_SENSOR_ICLK_UNKNOWN"),
                        runtime_suspend.index("is_sensor_runtime_suspend_pre("))
        self.assertLess(runtime_suspend.index("return ret;"),
                        runtime_suspend.index("v4l2_device_unregister_subdev("))
        self.assertLess(runtime_resume.index("IS_SENSOR_ICLK_UNKNOWN"),
                        runtime_resume.index("is_sensor_runtime_resume_pre("))
        self.assertLess(sensor_start_worker.index("mutex_lock(&device->mutex_reboot)"),
                        sensor_start_worker.index("if (device->reboot)"))
        worker_quarantine = sensor_start_worker.index(
            "is_sensor_core_clock_ownership_unknown(core, device)")
        self.assertLess(sensor_start_worker.index("mutex_lock(&device->mutex_reboot)"),
                        worker_quarantine)
        self.assertLess(worker_quarantine,
                        sensor_start_worker.index("if (device->reboot)"))
        self.assertLess(worker_quarantine,
                        sensor_start_worker.index("v4l2_subdev_call(device->subdev_csi"))
        core_worker_guard = extract_void_or_int_function(
            caller,
            "static bool is_sensor_core_clock_ownership_unknown(struct is_core *core,\n\tstruct is_device_sensor *device)")
        self.assertIn("for (i = 0; i < IS_SENSOR_COUNT; i++)", core_worker_guard)
        self.assertIn("&core->sensor[i].state", core_worker_guard)

        close_video = extract_function(video, "is_video_close")
        quarantine = close_video.index("IS_SENSOR_ICLK_UNKNOWN")
        for cleanup in ("is_sensor_close(", "__is_video_close(", "is_vctx_close("):
            self.assertLess(quarantine, close_video.index(cleanup))
        self.assertLess(close_video.index("ivc->iclk_quarantine_retained = true;"),
                        close_video.index("get_device(&iv->vd.dev)"))
        self.assertLess(close_video.index("get_device(&iv->vd.dev)"),
                        close_video.index("__module_get(THIS_MODULE)"))
        module_pin = close_video.index("__module_get(THIS_MODULE)")
        self.assertLess(module_pin, close_video.index("return -EUCLEAN;", module_pin))
        self.assertLess(close_video.index("if (ivc->iclk_quarantine_retained)"),
                        close_video.index("get_device(&iv->vd.dev)"))
        video_header = patched[VIDEO_HEADER_RELATIVE].decode("utf-8")
        self.assertIn("bool\t\t\t\ticlk_quarantine_retained;", video_header)
        video_open = extract_function(video, "is_video_open")
        self.assertIn("ids = idi->sensor;", video_open)
        self.assertLess(video_open.index("IS_SENSOR_ICLK_UNKNOWN"),
                        video_open.index("is_vctx_open("))
        resource_open = extract_function(
            self.sources[RESOURCEMGR_RELATIVE].decode("utf-8"), "is_resource_open")
        self.assertIn("*device = result;", resource_open)
        self.assertNotIn("is_resource_get(", resource_open)

    def test_pinned_vfs_and_vb2_lifetimes_support_only_terminal_retention(self) -> None:
        vfs = self.sources[VFS_RELATIVE].decode("utf-8")
        vfs_close = extract_void_or_int_function(vfs, "static void __fput(struct file *file)")
        release = vfs_close.index("file->f_op->release(inode, file);")
        fops_drop = vfs_close.index("fops_put(file->f_op);")
        file_free = vfs_close.index("\n" + "\t" + "file_free(file);")
        self.assertLess(release, fops_drop)
        self.assertLess(fops_drop, file_free)
        self.assertNotIn("ret = file->f_op->release", vfs_close)

        v4l2 = self.sources[V4L2_DEV_RELATIVE].decode("utf-8")
        v4l2_release = extract_void_or_int_function(
            v4l2, "static int v4l2_release(struct inode *inode, struct file *filp)")
        driver_release = v4l2_release.index("ret = vdev->fops->release(filp);")
        vdev_put = v4l2_release.index("video_put(vdev);")
        self.assertLess(driver_release, vdev_put)
        self.assertIn("/* decrease the refcount unconditionally", v4l2_release)
        vdev_release = extract_void_or_int_function(
            v4l2, "static void v4l2_device_release(struct device *cd)")
        self.assertIn("vdev->release(vdev);", vdev_release)
        self.assertIn("void video_device_release(struct video_device *vdev)", v4l2)
        self.assertIn("kfree(vdev);", extract_void_or_int_function(
            v4l2, "void video_device_release(struct video_device *vdev)"))

        video = self.video_text
        ctx_def = self.sources[VIDEO_HEADER_RELATIVE].decode("utf-8")
        ctx_start = ctx_def.index("struct is_video_ctx {")
        ctx_end = ctx_def.index("\n};", ctx_start)
        ctx_struct = ctx_def[ctx_start:ctx_end]
        self.assertNotIn("struct file", ctx_struct)
        ctx_open_start = video.index("static struct is_video_ctx *is_vctx_open(")
        ctx_close_start = video.index("static int is_vctx_close(", ctx_open_start)
        ctx_open = video[ctx_open_start:ctx_close_start]
        self.assertIn("file->private_data = ivc;", ctx_open)
        queue_init = extract_function(video, "queue_init")
        self.assertIn("vbq->drv_priv\t\t= vctx;", queue_init)

        vb2_v4l2 = self.sources[VB2_V4L2_RELATIVE].decode("utf-8")
        reqbufs = extract_function(vb2_v4l2, "vb2_ioctl_reqbufs")
        self.assertIn("vdev->queue->owner = p->count ? file->private_data : NULL;", reqbufs)
        queue_release = extract_void_or_int_function(vb2_v4l2,
                                                     "void vb2_queue_release(struct vb2_queue *q)")
        self.assertIn("vb2_core_queue_release(q);", queue_release)
        vb2_core = self.sources[VB2_CORE_RELATIVE].decode("utf-8")
        core_release = extract_void_or_int_function(vb2_core,
            "void vb2_core_queue_release(struct vb2_queue *q)")
        for cleanup in ("__vb2_cleanup_fileio(q);", "__vb2_queue_cancel(q);",
                        "__vb2_queue_free(q, q->num_buffers);"):
            self.assertIn(cleanup, core_release)

        video_close = extract_function(video, "__is_video_close")
        self.assertLess(video_close.index("vb2_queue_release(iq->vbq)"),
                        video_close.index("pablo_free(iq->vbq)"))
        vctx_close = extract_function(video, "is_vctx_close")
        self.assertIn("pablo_free(vctx);", vctx_close)
        self.assertEqual(video.count("pablo_free(vctx);"), 1)

        fops = video[video.index("static struct v4l2_file_operations is_default_v4l2_file_ops"):
                     video.index("};", video.index("static struct v4l2_file_operations is_default_v4l2_file_ops"))]
        self.assertIn(".owner\t\t= THIS_MODULE", fops)
        self.assertIn(".release\t= is_video_close", fops)
        self.assertIn("video->vd.release\t= video_device_release;", video)
        kbuild = self.sources[CAMERA_KBUILD_RELATIVE].decode("utf-8")
        self.assertIn("\tis-video.o \\", kbuild)
        self.assertIn("\tis-device-sensor_v2.o \\", kbuild)
        self.assertIn("obj-$(CONFIG_VIDEO_EXYNOS_PABLO_ISP) += fimc-is.o", kbuild)
        config = self.sources[S5E9925_CONFIG_RELATIVE].decode("utf-8")
        self.assertIn("CONFIG_VIDEO_EXYNOS_PABLO_ISP=m\n", config)
        ischain_header = self.sources[ISCHAIN_HEADER_RELATIVE].decode("utf-8")
        self.assertIn("struct is_device_sensor\t\t\t*sensor;", ischain_header)

        core = self.sources[CORE_RELATIVE].decode("utf-8")
        probe = extract_function(core, "is_probe")
        success_return = probe.index("return 0;")
        core_free = probe.index("pablo_free(core);")
        self.assertLess(success_return, core_free)
        self.assertLess(probe.index("core = pablo_zalloc(sizeof(struct is_core)"),
                        success_return)
        drivers = re.findall(
            r"static struct platform_driver is_driver = \{.*?\n\};", core, re.S)
        self.assertTrue(drivers)
        self.assertTrue(all(".remove" not in driver for driver in drivers))
        with tempfile.TemporaryDirectory(prefix="camera-clock-shutdown-source-") as temporary:
            patched = apply_patch_to_temporary_source(self.sources, Path(temporary))
        patched_core = patched[CORE_RELATIVE].decode("utf-8")
        ownership_unknown = extract_void_or_int_function(
            patched_core,
            "static bool is_sensor_clock_ownership_unknown(struct is_core *core)")
        self.assertIn("for (i = 0; i < IS_SENSOR_COUNT; i++)", ownership_unknown)
        self.assertIn("test_bit(IS_SENSOR_ICLK_UNKNOWN, &device->state)", ownership_unknown)
        cleanup = extract_void_or_int_function(
            patched_core, "void is_cleanup(struct is_core *core)")
        self.assertLess(cleanup.index("is_sensor_clock_ownership_unknown(core)"),
                        cleanup.index("is_sensor_front_stop(device, true);"))
        self.assertIn("device->reboot = true;", cleanup)
        self.assertIn("retain all shared sensor resources", cleanup)
        shutdown = extract_void_or_int_function(
            patched_core, "static void is_shutdown(struct platform_device *pdev)")
        self.assertIn("is_cleanup(core);", shutdown)
        self.assertLess(shutdown.index("is_sensor_clock_ownership_unknown(core)"),
                        shutdown.index("is_sensor_deinit_sensor_thread(sensor_peri);"))
        self.assertLess(shutdown.index("is_sensor_clock_ownership_unknown(core)"),
                        shutdown.index("cancel_work_sync("))
        config_switches = self.sources[COMMON_CONFIG_RELATIVE].decode("utf-8")
        self.assertIn("#define ENABLE_REBOOT_HANDLER", config_switches)
        resource_source = self.sources[RESOURCEMGR_RELATIVE].decode("utf-8")
        reboot_handler = extract_function(
            resource_source, "is_reboot_handler")
        self.assertIn("is_cleanup(core);", reboot_handler)
        self.assertIn("register_reboot_notifier(&notify_reboot_block);", resource_source)
        is_config = self.sources[IS_CONFIG_RELATIVE].decode("utf-8")
        self.assertIn("#define IS_SENSOR_COUNT\t\t6", is_config)
        core_header = self.sources[CORE_HEADER_RELATIVE].decode("utf-8")
        self.assertIn("struct is_device_sensor\t\tsensor[IS_SENSOR_COUNT];", core_header)
        self.assertIn("struct is_video\t\t\tvideo_byrp;", core_header)
        sensor_video_node = extract_function(
            self.sources[SENSOR_VIDEO_NODE_RELATIVE].decode("utf-8"),
            "is_ssx_video_probe")
        self.assertIn("video = &device->video;", sensor_video_node)
        ischain_video_node = extract_function(
            self.sources[ISCHAIN_VIDEO_NODE_RELATIVE].decode("utf-8"),
            "is_byrp_video_probe")
        self.assertIn("video = &core->video_byrp;", ischain_video_node)

    def test_actual_pinned_baseline_reproduces_hidden_failure_and_state_bug(self) -> None:
        compiler_version = subprocess.run(
            [self.compiler, "--version"], capture_output=True, text=True, check=False,
        )
        self.assertEqual(compiler_version.returncode, 0, compiler_version.stderr)
        for optimization in ("-O0", "-O2"):
            with self.subTest(optimization=optimization):
                source = harness_source(self.sources, patched=False)
                with tempfile.TemporaryDirectory(prefix="camera-clock-baseline-") as temporary:
                    output = compile_and_run(
                        source, self.compiler, Path(temporary), baseline=True,
                        optimization=optimization,
                    )
                self.assertIn("BASELINE_REPRODUCED", output)

    def test_actual_patched_c_propagates_and_unwinds_each_owned_vote(self) -> None:
        with tempfile.TemporaryDirectory(prefix="camera-clock-patched-source-") as temporary:
            patched = apply_patch_to_temporary_source(self.sources, Path(temporary))
        source = harness_source(patched, patched=False)
        for optimization in ("-O0", "-O2"):
            with self.subTest(optimization=optimization):
                with tempfile.TemporaryDirectory(prefix="camera-clock-patched-c-") as temporary:
                    output = compile_and_run(
                        source, self.compiler, Path(temporary), baseline=False,
                        optimization=optimization,
                    )
                self.assertIn("PASS: extracted clock quarantine lifecycle", output)

    def test_actual_cleanup_reboot_and_shutdown_guards_at_O0_O2(self) -> None:
        baseline = shutdown_harness_source(self.sources, patched=False)
        patched = shutdown_harness_source(self.sources, patched=True)
        for optimization in ("-O0", "-O2"):
            with self.subTest(optimization=optimization, baseline=True):
                with tempfile.TemporaryDirectory(prefix="camera-shutdown-baseline-") as temporary:
                    output = compile_shutdown_and_run(
                        baseline, self.compiler, Path(temporary), baseline=True,
                        optimization=optimization,
                    )
                self.assertIn("BASELINE_SHUTDOWN_REPRODUCED", output)
            with self.subTest(optimization=optimization, baseline=False):
                with tempfile.TemporaryDirectory(prefix="camera-shutdown-patched-") as temporary:
                    output = compile_shutdown_and_run(
                        patched, self.compiler, Path(temporary), baseline=False,
                        optimization=optimization,
                    )
                self.assertIn("PASS: extracted terminal shutdown quarantine", output)

    def test_effective_python_optimization_is_reported(self) -> None:
        expected = os.environ.get("CAMERA_EXPECT_PYTHONOPTIMIZE")
        if expected is not None:
            self.assertEqual(sys_flags_optimize(), int(expected))
        if os.environ.get("PYTHONOPTIMIZE") == "1":
            self.assertEqual(sys_flags_optimize(), 1)


def sys_flags_optimize() -> int:
    import sys
    return sys.flags.optimize


if __name__ == "__main__":
    import sys

    print(
        f"PYTHON_TEST_FLAGS isolated={sys.flags.isolated} "
        f"no_site={sys.flags.no_site} optimize={sys.flags.optimize} "
        f"PYTHONOPTIMIZE={os.environ.get('PYTHONOPTIMIZE', '<unset>')}"
    )
    unittest.main(verbosity=2)
