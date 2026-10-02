#!/usr/bin/env python3
"""Compile pinned NPU report/profile and validator bodies with host lock shims."""
from __future__ import annotations

import hashlib
import importlib.util
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
HELPER_PATH = ROOT / "tools/hardware/test-npu-candidate-stack.py"
PATCH_PATH = ROOT / "tools/hardware/npu-fw-report-lock-unwind.patch"
MSGID_PATCH_PATH = ROOT / "tools/hardware/npu-mailbox-msgid-validation.patch"
HARNESS_PATH = ROOT / "tools/hardware/npu-fw-report-lock-unwind-harness.c"
SOURCE_TREE_ENV = "S22_NPU_PROBE_SOURCE_TREE"
PINNED_BASE = "4e5c5ad7d950e4de0688b5663965f2075654b2ad"
SOURCE_COMMIT = "3fca50941422439b2019db2e4a3dc1016b2138a1"
LOG_C = "drivers/vision/npu/core/npu-log.c"
INTERFACE_C = "drivers/vision/npu/core/interface/hardware/npu-interface.c"
MSGID_C = "drivers/vision/npu/core/npu-util-msgidgen.c"
SOURCE_SHA256 = {
    LOG_C: "e98c22dc4d005b350d9c82bf067ada5b01a0059f766f13bce39594cc44172d5c",
    INTERFACE_C: "c2deaa0abd990184b64373bb13983f048b925e0f5f421f6623de7de85f667108",
    MSGID_C: "271dfe4f0b591a9a6d5a3f996e5fd2fe7a05d9b839513ce8691863bae79d3e2e",
}
MAX_PATCH_BYTES = 512 * 1024


def check(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    check(spec is not None and spec.loader is not None,
          f"cannot load existing bounded pinned-source helper: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


STACK = load_module(HELPER_PATH, "s22_npu_report_lock_fixture_loader")


def function_body(source: str, marker: str) -> str:
    start = source.find(marker)
    while start >= 0:
        brace = source.find("{", start)
        semicolon = source.find(";", start)
        if semicolon >= 0 and (brace < 0 or semicolon < brace):
            start = source.find(marker, semicolon + 1)
            continue
        check(brace >= 0, f"pinned C function has no body: {marker}")
        depth = 0
        for end in range(brace, len(source)):
            if source[end] == "{":
                depth += 1
            elif source[end] == "}":
                depth -= 1
                if depth == 0:
                    return source[start:end + 1]
        raise RuntimeError(f"pinned C function body is incomplete: {marker}")
    raise RuntimeError(f"pinned source lacks function: {marker}")


def extract(source: str, candidates: tuple[str, ...]) -> str:
    errors = []
    for marker in candidates:
        try:
            return function_body(source, marker)
        except RuntimeError as error:
            errors.append(str(error))
    raise RuntimeError("; ".join(errors))


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_sources() -> tuple[dict[str, bytes], str]:
    configured_root = os.environ.get(SOURCE_TREE_ENV)
    if configured_root:
        source_root = Path(configured_root).expanduser().resolve()
        head = subprocess.run(
            ["git", "-C", str(source_root), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=False, timeout=10,
        )
        check(head.returncode == 0 and head.stdout.strip() == SOURCE_COMMIT,
              f"fixture is not at verified derived source HEAD {SOURCE_COMMIT}")
        status = subprocess.run(
            ["git", "-C", str(source_root), "status", "--porcelain"],
            capture_output=True, text=True, check=False, timeout=10,
        )
        check(status.returncode == 0 and not status.stdout.strip(),
              "configured pinned-derived source fixture must be clean")
        identity = f"clean derived fixture {source_root}@{SOURCE_COMMIT}"
    else:
        identity = f"public pinned source {PINNED_BASE} via bounded SHA loader"

    sources: dict[str, bytes] = {}
    for relative, expected in SOURCE_SHA256.items():
        data = STACK.load_extra_fixture(relative, expected)
        check(len(data) <= STACK.HELPERS.MAX_SOURCE_BYTES,
              f"source exceeds existing per-file bound: {relative}")
        check(digest(data) == expected,
              f"pinned source hash mismatch for {relative}: {digest(data)}")
        sources[relative] = data
    return sources, identity


def verify_source_contract(sources: dict[str, bytes]) -> None:
    log = sources[LOG_C].decode("utf-8")
    interface = sources[INTERFACE_C].decode("utf-8")
    msgid = sources[MSGID_C].decode("utf-8")
    sites = (
        ("int npu_fw_report_store(", "fw_report_lock"),
        ("int npu_fw_profile_store(", "fw_profile_lock"),
        ("int fw_will_note_to_kernel(", "fw_report_lock"),
        ("int fw_will_note(", "fw_report_lock"),
    )
    for marker, lock in sites:
        body = function_body(log, marker)
        check(f"spin_lock_irqsave(&{lock}, intr_flags)" in body,
              f"target function no longer acquires its expected lock: {marker}")
        check("if (" in body and "st_buf == NULL" in body and
              "return -ENOMEM;" in body,
              f"target function no longer has the expected NULL-buffer error path: {marker}")
    gather = function_body(interface, "void fw_rprt_gather(")
    check("mutex_lock(&interface.lock)" in gather and
          "npu_fw_report_store(buf, nSize)" in gather and
          "mutex_unlock(&interface.lock)" in gather,
          "pinned actual report gather no longer calls store under interface.lock")
    for marker in ("int nw_rslt_manager(", "int fr_rslt_manager("):
        result_manager = function_body(interface, marker)
        type_check = result_manager.find("interface.msgid_get_type(msg.mid)")
        dequeue = result_manager.find("mbx_ipc_get_msg(")
        check(type_check >= 0 and dequeue > type_check,
              f"actual result path must validate type before dequeue: {marker}")
    check("fw_will_note(FW_LOGSIZE)" in
          function_body(msgid, "static inline void __validate_handle_msgid("),
          "pinned high-ID validator no longer routes through fw_will_note")


def apply_patch_to_source(source: bytes, relative: str, patch_path: Path) -> bytes:
    check(patch_path.is_file(), f"missing required patch: {patch_path}")
    patch_bytes = patch_path.read_bytes()
    check(len(patch_bytes) <= MAX_PATCH_BYTES,
          f"patch exceeds bounded input limit: {patch_path.name}")
    with tempfile.TemporaryDirectory(prefix="npu-report-unwind-") as temporary:
        root = Path(temporary)
        target = root / relative
        target.parent.mkdir(parents=True)
        target.write_bytes(source)
        checked = subprocess.run(
            ["git", "apply", "--check", "--whitespace=error-all", str(patch_path)],
            cwd=root, capture_output=True, text=True, check=False, timeout=10,
        )
        check(checked.returncode == 0,
              f"ordinary git apply --check failed for {patch_path.name}:\n{checked.stderr}")
        applied = subprocess.run(
            ["git", "apply", "--whitespace=error-all", str(patch_path)],
            cwd=root, capture_output=True, text=True, check=False, timeout=10,
        )
        check(applied.returncode == 0,
              f"ordinary git apply failed for {patch_path.name}:\n{applied.stderr}")
        return target.read_bytes()


LOG_FUNCTIONS = (
    "void npu_fw_report_init(",
    "void npu_fw_report_deinit(",
    "void npu_fw_profile_init(",
    "void npu_fw_profile_deinit(",
    "int npu_fw_report_store(",
    "int npu_fw_profile_store(",
    "int fw_will_note_to_kernel(",
    "int fw_will_note(",
)


def render_harness(template: str, log_source: bytes, interface_source: bytes,
                   msgid_source: bytes) -> str:
    log_text = log_source.decode("utf-8")
    interface_text = interface_source.decode("utf-8")
    msgid_text = msgid_source.decode("utf-8")
    actual_log = "\n\n".join(function_body(log_text, marker)
                               for marker in LOG_FUNCTIONS)
    actual_gather = function_body(interface_text, "void fw_rprt_gather(")
    actual_msgid = "\n\n".join((
        extract(msgid_text, (
            "static inline void __validate_handle_msgid(",
            "static inline int __validate_handle_msgid(",
        )),
        function_body(msgid_text, "int msgid_get_pt_type("),
    ))
    injections = {
        "/* ACTUAL_LOG_FUNCTIONS */": actual_log,
        "/* ACTUAL_GATHER_FUNCTION */": actual_gather,
        "/* ACTUAL_MSGID_FUNCTIONS */": actual_msgid,
    }
    for marker, body in injections.items():
        check(template.count(marker) == 1,
              f"harness source injection marker is not unique: {marker}")
        template = template.replace(marker, body, 1)
    return template


def compile_and_run(cc: list[str], level: str, source: str,
                    directory: Path, baseline: bool) -> str:
    source_file = directory / f"npu-fw-report-unwind-{level[2:]}.c"
    binary = directory / f"npu-fw-report-unwind-{level[2:]}"
    source_file.write_text(source, encoding="utf-8")
    command = [*cc, "-std=c11", "-Wall", "-Wextra", "-Werror",
               "-Wno-unused-parameter", level]
    if baseline:
        command.append("-DEXPECT_BASELINE=1")
    command.extend((str(source_file), "-o", str(binary)))
    built = subprocess.run(command, capture_output=True, text=True,
                           check=False, timeout=20)
    check(built.returncode == 0,
          f"actual extracted C did not compile at {level}:\n{built.stderr}")
    result = subprocess.run([str(binary)], capture_output=True, text=True,
                            check=False, timeout=10)
    check(result.returncode == 0,
          f"extracted C {'baseline' if baseline else 'patched'} failed at {level}:\n"
          f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}")
    if baseline:
        expected = (
            "REPRO: baseline report store returns with fw_report_lock held",
            "REPRO: baseline profile store returns with fw_profile_lock held",
            "REPRO: baseline fw_will_note_to_kernel leaks fw_report_lock",
            "REPRO: baseline fw_will_note leaks fw_report_lock",
            "REPRO: baseline gather ignores NULL-store error and leaks report lock",
            "REPRO: pinned high invalid ID calls fw_will_note, leaks lock, then BUGs",
        )
        for fragment in expected:
            check(fragment in result.stdout,
                  f"baseline C did not reproduce required behavior at {level}: {fragment}")
    else:
        check("PASS: actual high-ID validator diagnostic unwinds report lock" in
              result.stdout,
              f"patched C did not exercise combined validator/report path at {level}")
        check("PASS: actual gather wrap and newline paths completed" in result.stdout,
              f"patched C did not exercise actual gather/newline path at {level}")
        check("PASS: gather preserves its existing read-pointer advance" in result.stdout,
              f"patched C did not exercise gather's retained store-error behavior at {level}")
    return result.stdout


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--cc", default=os.environ.get("CC", "cc"))
    args = parser.parse_args()
    sources, identity = load_sources()
    verify_source_contract(sources)

    original_log = sources[LOG_C]
    original_msgid = sources[MSGID_C]
    fixed_log = apply_patch_to_source(original_log, LOG_C, PATCH_PATH)
    fixed_msgid = apply_patch_to_source(original_msgid, MSGID_C, MSGID_PATCH_PATH)

    harness_template = HARNESS_PATH.read_text(encoding="utf-8")
    baseline_harness = render_harness(
        harness_template, original_log, sources[INTERFACE_C], original_msgid)
    fixed_harness = render_harness(
        harness_template, fixed_log, sources[INTERFACE_C], fixed_msgid)
    compiler = shlex.split(args.cc)
    check(compiler, "C compiler command must not be empty")

    with tempfile.TemporaryDirectory(prefix="npu-fw-report-lock-build-") as temporary:
        directory = Path(temporary)
        for level in ("-O0", "-O2"):
            compile_and_run(compiler, level, baseline_harness, directory, True)
            compile_and_run(compiler, level, fixed_harness, directory, False)

    print(f"PASS: {identity}")
    print("PASS: ordinary git apply --check/apply for report-lock and existing msgid patches")
    print("PASS: extracted actual C at -O0 and -O2, baseline reproduced and patched paths passed")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"FAIL: {error}", file=sys.stderr)
        raise SystemExit(1)
