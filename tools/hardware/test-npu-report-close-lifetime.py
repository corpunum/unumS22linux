#!/usr/bin/env python3
"""Pinned extracted-C race regression for NPU report-owner close lifetime."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import resource
import shlex
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
PATCH_PATH = ROOT / "tools/hardware/npu-report-close-lifetime.patch"
HARNESS_PATH = ROOT / "tools/hardware/npu-report-close-lifetime-harness.c"
BUILD_PROFILE_PATH = ROOT / "tools/hardware/build-npu-six-profile.py"
PINNED_BASE = "4e5c5ad7d950e4de0688b5663965f2075654b2ad"
SOURCE_URL = "https://raw.githubusercontent.com/LineageOS/android_kernel_samsung_s5e9925"
MAX_SOURCE_BYTES = 512 * 1024
MAX_PATCH_BYTES = 512 * 1024
FETCH_TIMEOUT = 5
COMPOSED_TREE_ENV = "S22_NPU_REPORT_COMPOSED_SOURCE_TREE"
COMPOSED12_COMMIT = "e9c3016233a72ceccb13e537f0b7ef72426582b9"

INTERFACE_C = "drivers/vision/npu/core/interface/hardware/npu-interface.c"
LOG_C = "drivers/vision/npu/core/npu-log.c"
MSGID_C = "drivers/vision/npu/core/npu-util-msgidgen.c"
SYSTEM_C = "drivers/vision/npu/core/npu-system.c"
DEVICE_C = "drivers/vision/npu/core/npu-device.c"
HWDEV_C = "drivers/vision/npu/core/npu-hw-device.c"
REFCOUNT_H = "include/linux/refcount.h"
REFCOUNT_C = "lib/refcount.c"
SOURCE_SHA256 = {
    INTERFACE_C: "c2deaa0abd990184b64373bb13983f048b925e0f5f421f6623de7de85f667108",
    LOG_C: "e98c22dc4d005b350d9c82bf067ada5b01a0059f766f13bce39594cc44172d5c",
    MSGID_C: "271dfe4f0b591a9a6d5a3f996e5fd2fe7a05d9b839513ce8691863bae79d3e2e",
    SYSTEM_C: "96eaa6bf1511f3e6414e3e376d62592229454a2ea1687d760b7bb8e5952b1a05",
    DEVICE_C: "98be21e422ca864cc971dfe6a78e71b292d7691cb625c1f029bf4502100644ab",
    HWDEV_C: "14617a6f8e5b08e1bb169618daa8544f2680ad6709cb9f3b9730919d4dc8e16f",
    REFCOUNT_H: "82f75597f6899f61e9a2b4097ad6f4bf1ac53d8ace1113426e708f0da6316692",
    REFCOUNT_C: "b8d08fc1f8a678a54587149ff084b3ef15e9173df1dde1a0282027ac5b999d56",
}
NPU12_SHA256 = {
    SYSTEM_C: SOURCE_SHA256[SYSTEM_C],
    DEVICE_C: "a281fd35f2311951328d797824b8dbb165639bfc7977da30044bb764688cdc11",
    HWDEV_C: "b052462aa4919458a2b86c7ba0aed78bfdb938690cd58d5dcd01bdf0d75a312b",
    INTERFACE_C: SOURCE_SHA256[INTERFACE_C],
}
NPU13_INTERFACE_SHA256 = "3d142e00fc77148f5c215c1c105ea6e7a6d036ece82e4872f2556dd97132e6ba"
REPORT_INTERFACE_SHA256 = "3e1c9198f8317beb05f0e978f08c270fbc6e25cb73224784ccd92e959a0c4ef7"
NPU13_PATCH_SHA256 = "95e63b45d60e0a2611c1f2dcab4428e5658a03ff9954b8197d175ac6fec139cb"
NPU12_MSGID_PATCH_SHA256 = "c8366edfab42090ac09a6c366ad3c535a62e13384dd494ed685bbfe524a64d14"
NPU12_REPORT_PATCH_SHA256 = "71c2fa44f0fe42bd94ee416fb09453185dd418b408ba15937ec3c787a5e9bf5d"
NPU14_PATCH_SHA256 = "f1af656f9e1b8ca2bf15e934031e0adf828c442267a17761ba64f7d7c611729a"
NPU14_PREPATCH_SHA256 = "464a78b43f7ef0cc7e26b5f69075980460211f9c789d0a418b36d44be548968e"
REPORT_PATCH_SHA256 = "9bf6eef33be3b8f2c49744e362dada21613a0969576ef78a725d356cdebb0060"
SELECTED_C = (SYSTEM_C, DEVICE_C, HWDEV_C, INTERFACE_C)


def check(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    check(spec is not None and spec.loader is not None,
          f"cannot load pinned composition helper: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


BUILD_PROFILE = load_module(BUILD_PROFILE_PATH, "s22_npu_report_close_build_profile")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def verify_digest(label: str, data: bytes, expected: str) -> None:
    check(digest(data) == expected,
          f"SHA-256 mismatch for {label}: expected {expected}, got {digest(data)}")


def fetch_public(relative: str) -> bytes:
    url = f"{SOURCE_URL}/{PINNED_BASE}/{relative}"
    request = urllib.request.Request(
        url, headers={"User-Agent": "S22-NPU-report-close-host-test/1"})
    try:
        with urllib.request.urlopen(request, timeout=FETCH_TIMEOUT) as response:
            check(response.geturl() == url,
                  f"pinned source redirected unexpectedly: {relative}")
            content_length = response.headers.get("Content-Length")
            if content_length is not None:
                check(int(content_length) <= MAX_SOURCE_BYTES,
                      f"pinned source exceeds bound: {relative}")
            data = response.read(MAX_SOURCE_BYTES + 1)
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise RuntimeError(f"bounded public source fetch failed for {relative}: {error}") from error
    check(len(data) <= MAX_SOURCE_BYTES,
          f"pinned source exceeds {MAX_SOURCE_BYTES} bytes: {relative}")
    verify_digest(relative, data, SOURCE_SHA256[relative])
    return data


def git_apply(root: Path, patch: bytes, label: str,
              selected: tuple[str, ...] | None = None) -> None:
    check(len(patch) <= MAX_PATCH_BYTES,
          f"patch exceeds {MAX_PATCH_BYTES} bytes: {label}")
    command = ["git", "apply", "--whitespace=error-all"]
    if selected is not None:
        touched = patch_paths(patch).intersection(selected)
        if not touched:
            return
        for relative in sorted(touched):
            command.extend(("--include", relative))
    for operation in ("--check", None):
        actual = [*command]
        if operation:
            actual.append(operation)
        result = subprocess.run(
            actual + ["-"], cwd=root, input=patch, capture_output=True,
            text=False, check=False, timeout=10)
        check(result.returncode == 0,
              f"ordinary git apply {operation or 'apply'} failed for {label}:\n"
              f"{result.stderr.decode('utf-8', errors='replace')}")


def patch_paths(patch: bytes) -> set[str]:
    paths = set()
    for line in patch.decode("utf-8", errors="strict").splitlines():
        if line.startswith("diff --git a/"):
            paths.add(line[len("diff --git a/"):].split(" b/", 1)[0])
    return paths


def write_sources(root: Path, sources: dict[str, bytes]) -> None:
    for relative, data in sources.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


def read_sources(root: Path, paths: tuple[str, ...]) -> dict[str, bytes]:
    return {relative: (root / relative).read_bytes() for relative in paths}


def verify_tracked_patch(name: str, expected: str) -> bytes:
    path = ROOT / "tools/hardware" / name
    check(not path.is_symlink() and path.is_file(),
          f"required tracked patch missing or symlinked: {name}")
    data = path.read_bytes()
    check(len(data) <= MAX_PATCH_BYTES, f"tracked patch too large: {name}")
    verify_digest(name, data, expected)
    return data


def compose_sources(raw: dict[str, bytes]) -> tuple[dict[str, bytes], dict[str, bytes]]:
    for name, expected in BUILD_PROFILE.NATIVE_EIGHT_PATCHES:
        verify_tracked_patch(name, expected)
    npu13_patch = verify_tracked_patch("npu-interface-open-unwind.patch",
                                       NPU13_PATCH_SHA256)
    npu12_msgid = verify_tracked_patch("npu-mailbox-msgid-validation.patch",
                                       NPU12_MSGID_PATCH_SHA256)
    npu12_report = verify_tracked_patch("npu-fw-report-lock-unwind.patch",
                                        NPU12_REPORT_PATCH_SHA256)
    npu14_patch = verify_tracked_patch("npu-system-resume-error-unwind.patch",
                                       NPU14_PATCH_SHA256)
    verify_tracked_patch("fixtures/npu14-pre-correction.patch",
                         NPU14_PREPATCH_SHA256)

    with tempfile.TemporaryDirectory(prefix="npu-report-close-compose-") as temp:
        root = Path(temp)
        write_sources(root, raw)
        for name, expected in BUILD_PROFILE.NATIVE_EIGHT_PATCHES:
            patch = verify_tracked_patch(name, expected)
            git_apply(root, patch, name, SELECTED_C)
        npu12 = read_sources(root, SELECTED_C)
        for relative, expected in NPU12_SHA256.items():
            verify_digest(f"NPU12 composed {relative}", npu12[relative], expected)
        print("SOURCE: native-eight ordered patches reproduce selected NPU12 public SHA set")

        git_apply(root, npu12_msgid, "NPU12 msgid validation",
                  (MSGID_C,))
        git_apply(root, npu12_report, "NPU12 firmware report lock unwind",
                  (LOG_C,))
        print("SOURCE: NPU12 patches in documented order: msgid validation, firmware report lock unwind")
        git_apply(root, npu13_patch, "NPU13 interface lifecycle fence",
                  (INTERFACE_C,))
        npu13_interface = (root / INTERFACE_C).read_bytes()
        verify_digest("NPU13 composed interface", npu13_interface,
                      NPU13_INTERFACE_SHA256)
        print(f"SOURCE: NPU13 composed interface SHA-256 {NPU13_INTERFACE_SHA256}")

        touched_npu14 = patch_paths(npu14_patch).intersection(SELECTED_C)
        check(touched_npu14 == {SYSTEM_C, DEVICE_C},
              f"current NPU14 selected paths changed unexpectedly: {sorted(touched_npu14)}")
        git_apply(root, npu14_patch, "NPU14 system-resume close-error correction",
                  SELECTED_C)
        print(f"SOURCE: NPU14 current correction patch SHA-256 {NPU14_PATCH_SHA256}")
        before_our_patch = read_sources(
            root, SELECTED_C + (LOG_C, MSGID_C, REFCOUNT_H, REFCOUNT_C))

        ours = verify_tracked_patch(PATCH_PATH.name, REPORT_PATCH_SHA256)
        check(patch_paths(ours) == {INTERFACE_C},
              "report-owner patch must touch only npu-interface.c")
        git_apply(root, ours, PATCH_PATH.name)
        fixed = read_sources(
            root, SELECTED_C + (LOG_C, MSGID_C, REFCOUNT_H, REFCOUNT_C))
        check((root / INTERFACE_C).read_bytes() != npu13_interface,
              "report-owner patch did not alter composed interface source")
        verify_digest("final report-owner interface", fixed[INTERFACE_C],
                      REPORT_INTERFACE_SHA256)
        print(f"SOURCE: final report-owner interface SHA-256 {REPORT_INTERFACE_SHA256}")
    return before_our_patch, fixed


def git_output(root: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(root), *args],
                            capture_output=True, text=True, check=False, timeout=10)
    check(result.returncode == 0,
          f"git {' '.join(args)} failed in optional composed fixture: {result.stderr.strip()}")
    return result.stdout.strip()


def validate_optional_composed_tree(path: Path) -> str:
    check(not path.is_symlink(), "optional composed source fixture must not be a symlink")
    check(path.is_dir(), f"supplied composed source fixture is absent: {path}")
    root = path.resolve()
    head = git_output(root, "rev-parse", "HEAD")
    check(head == COMPOSED12_COMMIT,
          f"optional composed fixture must be at {COMPOSED12_COMMIT}, found {head}")
    check(not git_output(root, "status", "--porcelain"),
          "optional composed NPU12 source fixture must be clean")
    for relative, expected in NPU12_SHA256.items():
        target = root / relative
        check(not target.is_symlink() and target.is_file(),
              f"optional composed fixture path missing or symlinked: {relative}")
        check(target.stat().st_size <= MAX_SOURCE_BYTES,
              f"optional fixture file exceeds source bound: {relative}")
        data = target.read_bytes()
        check(len(data) <= MAX_SOURCE_BYTES,
              f"optional fixture file exceeds source bound: {relative}")
        verify_digest(f"optional composed NPU12 {relative}", data, expected)
    return f"verified optional clean NPU12 fixture @{COMPOSED12_COMMIT}"


def function_body(source: str, marker: str) -> str:
    start = source.find(marker)
    while start >= 0:
        brace = source.find("{", start)
        semicolon = source.find(";", start)
        if semicolon >= 0 and (brace < 0 or semicolon < brace):
            start = source.find(marker, semicolon + 1)
            continue
        check(brace >= 0, f"function body not found for {marker}")
        depth = 0
        for end in range(brace, len(source)):
            if source[end] == "{":
                depth += 1
            elif source[end] == "}":
                depth -= 1
                if depth == 0:
                    return source[start:end + 1]
        raise RuntimeError(f"unterminated function body: {marker}")
    raise RuntimeError(f"pinned source function missing: {marker}")


def extract_report_state(source: str) -> str:
    start = source.find("static DEFINE_SPINLOCK(report_owner_lock);")
    if start < 0:
        start = source.find("static DEFINE_SPINLOCK(report_wq_lock);")
    end = source.find("/* Modify strArr", start)
    check(start >= 0 and end > start,
          "NPU report queue/owner state block is incomplete")
    return source[start:end]


def verify_contract(before_bytes: dict[str, bytes], fixed_bytes: dict[str, bytes]) -> None:
    before = before_bytes[INTERFACE_C].decode("utf-8")
    fixed = fixed_bytes[INTERFACE_C].decode("utf-8")
    log = before_bytes[LOG_C].decode("utf-8")
    fixed_gather = function_body(fixed, "void fw_rprt_gather(")
    fixed_debug = function_body(fixed, "void dbg_print_error(")
    fixed_profiler = function_body(fixed, "int npu_check_unposted_mbox(")
    baseline_gather = function_body(before, "void fw_rprt_gather(")
    check(baseline_gather.find("if (interface.mbox_hdr == NULL)") <
          baseline_gather.find("mutex_lock(&interface.lock)"),
          "baseline does not retain the expected check-before-lock race")
    for marker, body in (("fw_rprt_gather", fixed_gather),
                         ("dbg_print_error", fixed_debug),
                         ("npu_check_unposted_mbox", fixed_profiler)):
        check("npu_report_owner_get()" in body and
              "npu_report_owner_put()" in body,
              f"fixed {marker} lacks a balanced report-owner pin")
        check("interface.mbox_hdr == NULL" not in body and
              "interface.mbox_hdr->" not in body,
              f"fixed {marker} still dereferences the mutable global mailbox pointer")
    check(fixed_gather.find("npu_report_owner_get()") <
          fixed_gather.find("mutex_lock(&interface.lock)"),
          "report gather must pin its local owner before taking interface.lock")
    check(fixed_debug.find("npu_report_owner_get()") <
          fixed_debug.find("mutex_lock(&interface.lock)"),
          "debug report reader must pin before interface.lock")
    check(fixed_profiler.find("npu_report_owner_get()") <
          fixed_profiler.find("if (!in_interrupt())\n\t\tmutex_lock(&interface.lock)"),
          "profiler reader must pin before preserving its conditional interface mutex")
    check("if (!in_interrupt())\n\t\tmutex_lock(&interface.lock);" in fixed_profiler and
          "if (!in_interrupt())\n\t\tmutex_unlock(&interface.lock);" in fixed_profiler,
          "profiler path changed its non-interrupt conditional mutex policy")
    refcount_header = fixed_bytes[REFCOUNT_H].decode("utf-8")
    refcount_library = fixed_bytes[REFCOUNT_C].decode("utf-8")
    exact_dec = function_body(refcount_header,
                              "static inline void __refcount_dec(")
    exact_warn = function_body(refcount_library,
                               "void refcount_warn_saturate(")
    exact_sub_test = function_body(
        refcount_header,
        "static inline __must_check bool __refcount_sub_and_test(")
    check("#define REFCOUNT_SATURATED\t(INT_MIN / 2)" in refcount_header and
          "atomic_fetch_sub_release(1, &r->refs)" in exact_dec and
          "if (unlikely(old <= 1))" in exact_dec and
          "refcount_warn_saturate(r, REFCOUNT_DEC_LEAK)" in exact_dec and
          "refcount_set(r, REFCOUNT_SATURATED)" in exact_warn and
          "case REFCOUNT_DEC_LEAK:" in exact_warn and
          "if (old == i)" in exact_sub_test,
          "pinned refcount helper semantics changed; no-reader decrement control is invalid")
    check("atomic_t report_owner_readers" not in fixed and
          "refcount_t report_owner_refs = REFCOUNT_INIT(0)" in fixed and
          "refcount_inc_not_zero" in fixed and
          "refcount_dec_and_test" in fixed and
          "refcount_dec(&report_owner_refs)" not in fixed,
          "owner pins must use fail-closed refcount decrement/test semantics")
    detach = function_body(fixed, "static volatile struct mailbox_hdr *npu_report_owner_detach(")
    check("wake_readers = refcount_dec_and_test(&report_owner_refs)" in detach and
          detach.find("spin_unlock_irqrestore") < detach.find("wake_up_all") <
          detach.find("wait_event(report_owner_wait"),
          "publication sentinel must drop with dec_and_test and wake outside owner spinlock")
    check(detach.find("report_owner = NULL") < detach.find("spin_unlock_irqrestore") <
          detach.find("wait_event(report_owner_wait"),
          "detach must withdraw publication and release owner spin before waiting")
    probe = function_body(fixed, "int npu_interface_probe(")
    opening = function_body(fixed, "int npu_interface_open(")
    closing = function_body(fixed, "int npu_interface_close(")
    check(probe.find("mutex_lock(&report_wq_lifecycle_lock)") <
          probe.find("report_interface_probed || report_interface_open") <
          probe.find("mutex_init(&interface.lock)"),
          "probe/reprobe must be serialized and rejected before reinitializing interface.lock")
    check("report_interface_probed = true" in probe,
          "probe/reprobe guard no longer commits its one-time initialized state")
    check("mutex_init(&report_wq_lifecycle_lock)" not in fixed and
          "mutex_init(&report_owner_lock)" not in fixed,
          "static report locks must never be reset by probe")
    check(opening.find("mailbox_init(interface.mbox_hdr, system)") <
          opening.find("npu_report_owner_publish(interface.mbox_hdr)"),
          "report owner may be published only after mailbox initialization succeeds")
    publish_at = opening.find("npu_report_owner_publish(interface.mbox_hdr)")
    publish_error_end = opening.find("report_irq_count = requested_irqs", publish_at)
    check(publish_at >= 0 and publish_error_end > publish_at and
          "if (ret)" in opening[publish_at:publish_error_end] and
          "mailbox_deinit(" not in opening[publish_at:publish_error_end],
          "publication invariant failure must not free a possibly pinned owner")
    detach_at = closing.find("npu_report_owner_detach()")
    deinit_at = closing.find("mailbox_deinit(report_mbox, system)")
    clear_at = closing.find("interface.mbox_hdr = NULL")
    check(detach_at >= 0 and detach_at < deinit_at < clear_at,
          "close must detach/drain readers before deinit and global pointer clear")
    check(closing.find("npu_report_workqueue_detach()") <
          closing.find("devm_free_irq") <
          closing.find("npu_report_workqueue_destroy(report_wq)") < detach_at,
          "close must detach producers, synchronize IRQs, and drain work before direct readers")
    for function_name in ("int fw_will_note_to_kernel(", "int fw_will_note("):
        caller = function_body(log, function_name)
        gather_at = caller.find("npu_log.log_ops->fw_rprt_gather();")
        report_lock_at = caller.find("spin_lock_irqsave(&fw_report_lock")
        check(0 <= gather_at < report_lock_at,
              f"direct log caller lock ordering changed: {function_name}")
    check("npu_report_queue_work()" not in
          function_body(log, "int fw_will_note_to_kernel("),
          "direct note caller unexpectedly became queued-only")
    lifecycle_independent = (
        "static void npu_report_queue_work(",
        "static irqreturn_t mailbox_isr0(",
        "static irqreturn_t mailbox_isr1(",
        "static irqreturn_t mailbox_isr2(",
        "static void __rprt_manager(",
    )
    for marker in lifecycle_independent:
        body = function_body(fixed, marker)
        check("report_wq_lifecycle_lock" not in body,
              f"close/open lifecycle serialization must not be acquired by {marker}")
    print("PASS contract: report pins, close/reprobe ordering, caller lock order, lifecycle-independent drainers")


def render_harness(template: str, before: dict[str, bytes],
                   fixed: dict[str, bytes], baseline: bool) -> str:
    interface_bytes = before[INTERFACE_C] if baseline else fixed[INTERFACE_C]
    interface = interface_bytes.decode("utf-8")
    log = before[LOG_C].decode("utf-8")
    functions = "\n\n".join(function_body(interface, marker) for marker in (
        "int npu_interface_probe(",
        "int npu_interface_open(",
        "int npu_interface_close(",
    ))
    readers = "\n\n".join(function_body(interface, marker) for marker in (
        "void fw_rprt_gather(",
        "void dbg_print_error(",
        "int npu_check_unposted_mbox(",
    ))
    callers = "\n\n".join(function_body(log, marker) for marker in (
        "int fw_will_note_to_kernel(",
        "int fw_will_note(",
    ))
    refcount_header = fixed[REFCOUNT_H].decode("utf-8")
    refcount_library = fixed[REFCOUNT_C].decode("utf-8")
    pinned_refcount_helpers = "\n\n".join(function_body(source, marker)
        for source, marker in (
            (refcount_library, "void refcount_warn_saturate("),
            (refcount_header,
             "static inline __must_check bool __refcount_sub_and_test("),
            (refcount_header,
             "static inline __must_check bool __refcount_dec_and_test("),
            (refcount_header,
             "static inline __must_check bool refcount_dec_and_test("),
            (refcount_header, "static inline void __refcount_dec("),
            (refcount_header, "static inline void refcount_dec("),
        ))
    injections = {
        "/* PINNED_REFCOUNT_HELPERS */": pinned_refcount_helpers,
        "/* ACTUAL_REPORT_STATE */": extract_report_state(interface),
        "/* ACTUAL_PROBE_OPEN_CLOSE */": functions,
        "/* ACTUAL_REPORT_READERS */": readers,
        "/* ACTUAL_LOG_CALLERS */": callers,
    }
    for marker, code in injections.items():
        check(template.count(marker) == 1,
              f"harness extraction marker is not unique: {marker}")
        template = template.replace(marker, code, 1)
    return template


def compile_run(cc: list[str], level: str, source: str, directory: Path,
                scenario: str) -> str:
    tag = scenario
    source_file = directory / f"report-close-{tag}-{level[2:]}.c"
    binary = directory / f"report-close-{tag}-{level[2:]}"
    source_file.write_text(source, encoding="utf-8")
    command = [*cc, "-std=c11", "-Wall", "-Wextra", "-Werror",
               "-Wno-unused-parameter", "-Wno-unused-function",
               "-Wno-unused-but-set-variable", "-pthread", level]
    if scenario == "baseline":
        command.append("-DEXPECT_BASELINE=1")
    elif scenario == "pre-fix-refcount":
        command.append("-DEXPECT_BAD_REFCOUNT=1")
    command.extend((str(source_file), "-o", str(binary)))
    built = subprocess.run(command, capture_output=True, text=True,
                           check=False, timeout=20)
    check(built.returncode == 0,
          f"actual extracted C failed to compile at {level}:\n{built.stderr}")
    result = subprocess.run(
        [str(binary)], capture_output=True, text=True, check=False, timeout=20,
        preexec_fn=lambda: resource.setrlimit(resource.RLIMIT_CORE, (0, 0)))
    check(result.returncode == 0,
          f"extracted C {tag} failed at {level}:\nstdout:\n{result.stdout}\n"
          f"stderr:\n{result.stderr}")
    expected = (("PASS: pre-probe report readers refuse without touching uninitialized mutex",
                 "REPRO: baseline direct reader passed pre-lock check; close deinitialized/cleared before stale dereference",
                 "REPRO: baseline profiler direct reader escaped close after pre-lock header check")
                if scenario == "baseline" else
                (("REPRO: pre-fix refcount_dec at one saturates; wait condition remains unsatisfied",)
                 if scenario == "pre-fix-refcount" else
                 ("PASS: before-open/open-error readers refuse unpublished owner; probe/reprobe guarded",
                 "PASS: direct caller close waits for pinned reader; post-detach callers refuse",
                 "PASS: debug and interrupt-context profiler races drain; reopen/repeat-close progress",
                 "PASS: no-reader close drops the publication sentinel with pinned refcount_dec_and_test",
                 "PASS: report-owner counter overflow/underflow fail closed rather than wrap to free")))
    for fragment in expected:
        check(fragment in result.stdout,
              f"{tag} C did not report expected scenario at {level}: {fragment}")
    print(f"C_RESULT {tag} {level}: {result.stdout.strip().replace(chr(10), ' | ')}")
    return result.stdout


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cc", default=os.environ.get("CC", "cc"))
    parser.add_argument("--expect-opt", type=int, choices=(0, 1))
    parser.add_argument("--composed-source-tree", type=Path,
                        help="optional clean preserved NPU12 tree for hash corroboration")
    args = parser.parse_args()
    check(sys.flags.optimize in (0, 1), "unexpected Python optimization level")
    if args.expect_opt is not None:
        check(sys.flags.optimize == args.expect_opt,
              f"expected Python optimize={args.expect_opt}, got {sys.flags.optimize}")
    raw = {relative: fetch_public(relative) for relative in SOURCE_SHA256}
    before, fixed = compose_sources(raw)
    verify_contract(before, fixed)
    configured = args.composed_source_tree or os.environ.get(COMPOSED_TREE_ENV)
    optional_identity = "absent (public composition remains mandatory)"
    if configured:
        optional_identity = validate_optional_composed_tree(Path(configured).expanduser())
    else:
        print("SOURCE: optional private NPU12 tree absent; public composition is the only source route")

    template = HARNESS_PATH.read_text(encoding="utf-8")
    base_harness = render_harness(template, before, fixed, True)
    fixed_harness = render_harness(template, before, fixed, False)
    broken_refcount_call = "wake_readers = refcount_dec_and_test(&report_owner_refs);"
    check(fixed_harness.count(broken_refcount_call) == 1,
          "cannot build pre-fix refcount negative control from extracted source")
    pre_fix_refcount_harness = fixed_harness.replace(
        broken_refcount_call, "refcount_dec(&report_owner_refs);", 1)
    compiler = shlex.split(args.cc)
    check(bool(compiler), "C compiler command is empty")
    with tempfile.TemporaryDirectory(prefix="npu-report-close-build-") as temp:
        directory = Path(temp)
        for level in ("-O0", "-O2"):
            compile_run(compiler, level, base_harness, directory, "baseline")
            compile_run(compiler, level, pre_fix_refcount_harness, directory,
                        "pre-fix-refcount")
            compile_run(compiler, level, fixed_harness, directory, "fixed")
    print(f"PASS: Python optimize={sys.flags.optimize}")
    print(f"PASS: public exact-SHA stack {PINNED_BASE} -> NPU12 -> NPU13 -> NPU14 -> report-owner")
    print(f"PASS: optional fixture {optional_identity}")
    print("PASS: extracted kernel C and pinned refcount helpers at -O0/-O2; baseline races and pre-fix no-reader refcount leak reproduced; fixed close drained")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"FAIL: {error}", file=sys.stderr)
        raise SystemExit(1)
