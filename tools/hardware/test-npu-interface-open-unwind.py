#!/usr/bin/env python3
"""Run pinned extracted-C regressions for NPU interface open/close unwind."""
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
PATCH_PATH = ROOT / "tools/hardware/npu-interface-open-unwind.patch"
HARNESS_PATH = ROOT / "tools/hardware/npu-interface-open-unwind-harness.c"
SOURCE_TREE_ENV = "S22_NPU_PROBE_SOURCE_TREE"
PINNED_BASE = "4e5c5ad7d950e4de0688b5663965f2075654b2ad"
SOURCE_COMMIT = "3fca50941422439b2019db2e4a3dc1016b2138a1"
INTERFACE_C = "drivers/vision/npu/core/interface/hardware/npu-interface.c"
SOURCE_SHA256 = "c2deaa0abd990184b64373bb13983f048b925e0f5f421f6623de7de85f667108"
MAX_PATCH_BYTES = 512 * 1024


def check(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    check(spec is not None and spec.loader is not None,
          f"cannot load existing bounded fixture loader: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


STACK = load_module(HELPER_PATH, "s22_npu_interface_unwind_fixture_loader")


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


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_source() -> tuple[bytes, str]:
    configured_root = os.environ.get(SOURCE_TREE_ENV)
    if configured_root:
        source_root = Path(configured_root).expanduser().resolve()
        head = subprocess.run(
            ["git", "-C", str(source_root), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=False, timeout=10,
        )
        check(head.returncode == 0 and head.stdout.strip() == SOURCE_COMMIT,
              f"fixture must be verified derived HEAD {SOURCE_COMMIT}")
        status = subprocess.run(
            ["git", "-C", str(source_root), "status", "--porcelain"],
            capture_output=True, text=True, check=False, timeout=10,
        )
        check(status.returncode == 0 and not status.stdout.strip(),
              "configured pinned-derived source fixture must be clean")
        identity = f"clean derived fixture {source_root}@{SOURCE_COMMIT}"
    else:
        identity = f"pinned public source {PINNED_BASE} via bounded SHA loader"
    data = STACK.load_extra_fixture(INTERFACE_C, SOURCE_SHA256)
    check(len(data) <= STACK.HELPERS.MAX_SOURCE_BYTES,
          "source exceeds existing per-file size bound")
    check(digest(data) == SOURCE_SHA256,
          f"pinned npu-interface.c SHA-256 mismatch: {digest(data)}")
    return data, identity


def apply_patch(source: bytes) -> bytes:
    check(PATCH_PATH.is_file(), f"missing patch: {PATCH_PATH}")
    patch_bytes = PATCH_PATH.read_bytes()
    check(len(patch_bytes) <= MAX_PATCH_BYTES,
          "patch exceeds bounded input size")
    with tempfile.TemporaryDirectory(prefix="npu-interface-unwind-source-") as temp:
        root = Path(temp)
        target = root / INTERFACE_C
        target.parent.mkdir(parents=True)
        target.write_bytes(source)
        for operation in ("--check",):
            result = subprocess.run(
                ["git", "apply", operation, "--whitespace=error-all", str(PATCH_PATH)],
                cwd=root, capture_output=True, text=True,
                check=False, timeout=10,
            )
            check(result.returncode == 0,
                  f"ordinary git apply {operation} failed:\n{result.stderr}")
        applied = subprocess.run(
            ["git", "apply", "--whitespace=error-all", str(PATCH_PATH)],
            cwd=root, capture_output=True, text=True,
            check=False, timeout=10,
        )
        check(applied.returncode == 0,
              f"ordinary git apply failed:\n{applied.stderr}")
        return target.read_bytes()


def verify_contract(original: bytes, patched: bytes) -> None:
    before = original.decode("utf-8")
    after = patched.decode("utf-8")
    before_probe = function_body(before, "int npu_interface_probe(")
    before_open = function_body(before, "int npu_interface_open(")
    before_close = function_body(before, "int npu_interface_close(")
    after_probe = function_body(after, "int npu_interface_probe(")
    after_open = function_body(after, "int npu_interface_open(")
    after_close = function_body(after, "int npu_interface_close(")
    check(before_probe.find('alloc_workqueue("my work"') <
          before_open.find('alloc_workqueue("rprt_manager"'),
          "baseline probe/open queue replacement topology changed")
    check("goto err_exit;" in before_open and "return ret;" in before_open,
          "baseline allocation failure source path no longer matches expected false-success path")
    check("for (i = 0; i < system->irq_num; i++)" in before_open,
          "baseline broad IRQ cleanup loop no longer present")
    check("ret = -ENOMEM;" in after_open and "report_wq = alloc_workqueue" in after_open,
          "patched open must return an allocation error without overwriting global queue")
    check("requested_irqs++" in after_open and "affinity_irqs++" in after_open,
          "patched open must count only successful per-call resource acquisitions")
    check("report_interface_open" in after_open and "-EBUSY" in after_open,
          "patched open must serialize/reject a duplicate active open")
    check("mutex_lock(&report_wq_lifecycle_lock)" in after_probe and
          "mutex_lock(&report_wq_lifecycle_lock)" in after_open and
          "mutex_lock(&report_wq_lifecycle_lock)" in after_close,
          "probe/open/close lifecycle serialization disappeared")
    check("report_wq = npu_report_workqueue_detach()" in after_close and
          after_close.find("report_wq = npu_report_workqueue_detach()") <
          after_close.find("devm_free_irq") <
          after_close.find("npu_report_workqueue_destroy(report_wq)"),
          "close must detach, synchronize IRQ producers, then drain/destroy queue")
    check("if (interface.mbox_hdr)" in after_close,
          "close diagnostics must guard mailbox-header dereferences")
    helper_start = after.find("static void npu_report_queue_work(")
    helper_end = after.find("/* Modify strArr", helper_start)
    check(helper_start >= 0 and helper_end > helper_start,
          "patched source is missing report-queue publication helpers")
    queue_state = after[helper_start:helper_end]
    check("spin_lock_irqsave(&report_wq_lock" in queue_state and
          "queue_work(wq, &work_report)" in queue_state and
          "spin_unlock_irqrestore(&report_wq_lock" in queue_state,
          "queue publication and queue detach must share the workqueue spinlock")
    for marker in ("static irqreturn_t mailbox_isr1(",
                   "static irqreturn_t mailbox_isr2(",
                   "void fw_rprt_manager("):
        body = function_body(after, marker)
        check("npu_report_queue_work();" in body,
              f"producer no longer uses serialized queue helper: {marker}")
    check("queue_work(wq, &work_report)" not in after.replace(queue_state, ""),
          "raw producer bypasses queue publication lock")
    check("if (work_pending(&work_report))" in before_close,
          "baseline close pending-work branch no longer present")


def render_harness(template: str, source: bytes) -> str:
    text = source.decode("utf-8")
    if "static DEFINE_SPINLOCK(report_wq_lock);" in text:
        start = text.find("static DEFINE_SPINLOCK(report_wq_lock);")
        end = text.find("/* Modify strArr", start)
        check(end > start, "actual report worker state block is incomplete")
        report_state = text[start:end]
    else:
        report_state = ""
    functions = "\n\n".join(function_body(text, marker) for marker in (
        "int npu_interface_probe(",
        "int npu_interface_open(",
        "int npu_interface_close(",
        "void fw_rprt_manager(",
    ))
    for marker, body in (("/* ACTUAL_REPORT_STATE */", report_state),
                         ("/* ACTUAL_PROBE_OPEN_CLOSE */", functions)):
        check(template.count(marker) == 1,
              f"harness injection marker not unique: {marker}")
        template = template.replace(marker, body, 1)
    return template


def compile_and_run(cc: list[str], level: str, source: str,
                    directory: Path, baseline: bool) -> str:
    tag = "base" if baseline else "fixed"
    source_file = directory / f"npu-interface-open-unwind-{tag}-{level[2:]}.c"
    binary = directory / f"npu-interface-open-unwind-{tag}-{level[2:]}"
    source_file.write_text(source, encoding="utf-8")
    command = [*cc, "-std=c11", "-Wall", "-Wextra", "-Werror",
               "-Wno-unused-parameter", "-Wno-unused-function", "-pthread", level]
    if baseline:
        command.append("-DEXPECT_BASELINE=1")
    command.extend((str(source_file), "-o", str(binary)))
    built = subprocess.run(command, capture_output=True, text=True,
                           check=False, timeout=20)
    check(built.returncode == 0,
          f"actual extracted C did not compile at {level}:\n{built.stderr}")
    result = subprocess.run([str(binary)], capture_output=True, text=True,
                            check=False, timeout=15)
    check(result.returncode == 0,
          f"extracted C {'baseline' if baseline else 'patched'} failed at {level}:\n"
          f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}")
    if baseline:
        expected = (
            "REPRO: baseline probe WQ allocation failure returns success",
            "REPRO: baseline active-WQ allocation failure returns success",
            "REPRO: baseline partial IRQ acquisition frees and clears every slot",
        )
    else:
        expected = (
            "PASS: probe allocation failure plus close-before-open/repeated close",
            "PASS: active queue allocation failure returns -ENOMEM with exact cleanup",
            "PASS: partial request and affinity failures release only acquired resources",
            "PASS: mailbox failure cleanup, successful publication, duplicate open, and repeated close",
            "PASS: close-before-open guards NULL mailbox diagnostics with queued work",
            "PASS: producer holding queue-publication lock overlaps close",
            "PASS: explicit producer in queue-swap gap is followed by one post-install catch-up",
        )
    for fragment in expected:
        check(fragment in result.stdout,
              f"required {'baseline repro' if baseline else 'patched case'} missing at {level}: {fragment}")
    return result.stdout


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--cc", default=os.environ.get("CC", "cc"))
    args = parser.parse_args()
    original, identity = load_source()
    patched = apply_patch(original)
    verify_contract(original, patched)
    template = HARNESS_PATH.read_text(encoding="utf-8")
    baseline_harness = render_harness(template, original)
    fixed_harness = render_harness(template, patched)
    compiler = shlex.split(args.cc)
    check(compiler, "C compiler command must not be empty")
    with tempfile.TemporaryDirectory(prefix="npu-interface-open-build-") as temp:
        directory = Path(temp)
        outputs = []
        for level in ("-O0", "-O2"):
            outputs.append(compile_and_run(
                compiler, level, baseline_harness, directory, True))
            outputs.append(compile_and_run(
                compiler, level, fixed_harness, directory, False))
    print(f"PASS: {identity}")
    print("PASS: ordinary git apply --check/apply for the single pinned C target")
    print("PASS: extracted production C at -O0/-O2; baseline defects reproduced")
    for output in outputs:
        for line in output.splitlines():
            if line.startswith(("REPRO:", "PASS:")):
                print(line)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"FAIL: {error}", file=sys.stderr)
        raise SystemExit(1)
