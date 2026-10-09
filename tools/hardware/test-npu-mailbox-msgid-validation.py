#!/usr/bin/env python3
"""Compile extracted pinned NPU message-ID and mailbox caller C at -O0/-O2.

Host shims catch the old BUG_ON behavior and guard ownership/dequeue effects.
They do not run Linux, firmware, a kernel build, or NPU hardware.
"""
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
PATCH_PATH = ROOT / "tools/hardware/npu-mailbox-msgid-validation.patch"
HARNESS_PATH = ROOT / "tools/hardware/npu-mailbox-msgid-validation-harness.c"
SOURCE_TREE_ENV = "S22_NPU_PROBE_SOURCE_TREE"
PINNED_BASE = "4e5c5ad7d950e4de0688b5663965f2075654b2ad"
SOURCE_COMMIT = "3fca50941422439b2019db2e4a3dc1016b2138a1"
SOURCE_SHA256 = {
    "drivers/vision/npu/core/npu-util-msgidgen.c":
        "271dfe4f0b591a9a6d5a3f996e5fd2fe7a05d9b839513ce8691863bae79d3e2e",
    "drivers/vision/npu/core/npu-if-protodrv-mbox2.c":
        "d13247bd90ab27dd58cfe9241ec1a07460ab3b353d86a268f8a6fadefc6ff755",
    "drivers/vision/npu/core/npu-protodrv.c":
        "246952fc5b29985f88277b76dce6fcf12c1694695e0f6111cffa249c0f38f5a9",
    "drivers/vision/npu/core/interface/hardware/npu-interface.c":
        "c2deaa0abd990184b64373bb13983f048b925e0f5f421f6623de7de85f667108",
    "drivers/vision/npu/core/interface/hardware/mailbox_msg.h":
        "5985d2b5ebb34eecd02c2506dbd4dbb51dbe0cf720ad450c9077a3fb50edf0ef",
    "drivers/vision/npu/core/interface/hardware/mailbox_msg_v10.h":
        "ca18870a661dce7c1bebe0c008f5c70f14eba93c63539b8cde010294df9c56f0",
    "drivers/vision/npu/core/include/npu-config.h":
        "0aea42ae72a0083ab57c918b5b90bad4d87f1e4c213552c14d728bf427e5073f",
    "arch/arm64/configs/s5e9925_defconfig":
        "de87dbdff5a4082b2aa6fd511a69b9766ddfb0369738c76885c9766178f2b4f5",
}
PATCH_SHA256 = "c8366edfab42090ac09a6c366ad3c535a62e13384dd494ed685bbfe524a64d14"


def check(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    check(spec is not None and spec.loader is not None,
          f"cannot import bounded pinned source loader: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


STACK = load_module(HELPER_PATH, "s22_npu_msgid_fixture_loader")


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


def load_sources() -> tuple[dict[str, bytes], str]:
    configured_root = os.environ.get(SOURCE_TREE_ENV)
    if configured_root:
        source_root = Path(configured_root).expanduser().resolve()
        head = subprocess.run(
            ["git", "-C", str(source_root), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=False, timeout=10,
        )
        check(head.returncode == 0 and head.stdout.strip() == SOURCE_COMMIT,
              f"configured fixture is not at verified derived HEAD {SOURCE_COMMIT}")
        status = subprocess.run(
            ["git", "-C", str(source_root), "status", "--porcelain"],
            capture_output=True, text=True, check=False, timeout=10,
        )
        check(status.returncode == 0 and not status.stdout.strip(),
              "configured derived fixture must be clean")
        identity = f"clean derived fixture {source_root}@{SOURCE_COMMIT}"
    else:
        identity = f"public pinned source {PINNED_BASE} via bounded SHA loader"

    sources = {}
    for relative, expected in SOURCE_SHA256.items():
        data = STACK.load_extra_fixture(relative, expected)
        check(len(data) <= STACK.HELPERS.MAX_SOURCE_BYTES,
              f"pinned source exceeds per-file bound: {relative}")
        sources[relative] = data
    return sources, identity


def verify_call_graph(sources: dict[str, bytes]) -> None:
    proto = sources["drivers/vision/npu/core/npu-protodrv.c"].decode()
    mbox = sources["drivers/vision/npu/core/npu-if-protodrv-mbox2.c"].decode()
    interface = sources[
        "drivers/vision/npu/core/interface/hardware/npu-interface.c"].decode()
    config = sources["arch/arm64/configs/s5e9925_defconfig"].decode()
    dispatcher = sources[
        "drivers/vision/npu/core/interface/hardware/mailbox_msg.h"].decode()
    v10 = sources[
        "drivers/vision/npu/core/interface/hardware/mailbox_msg_v10.h"].decode()
    check("CONFIG_NPU_MAILBOX_VERSION=9" in config and
          "CONFIG_NPU_COMMAND_VERSION=10" in config,
          "pinned S5E9925 config no longer selects mailbox v9 / command v10")
    check('#elif (CONFIG_NPU_COMMAND_VERSION == 10)\n#include "mailbox_msg_v10.h"'
          in dispatcher and "u32\t\t\t\tmid; /* message id */" in v10,
          "selected production firmware message does not expose a u32 msgid")
    getter = function_body(proto, "static int get_msgid_type(")
    check("msgid_get_pt_type(&npu_proto_drv.msgid_pool, msgid)" in getter,
          "protocol-driver type getter no longer calls the actual pool lookup")
    for marker, expected_type, dequeue in (
            ("int nw_rslt_manager(", "PROTO_DRV_REQ_TYPE_NW",
             "mbx_ipc_get_msg("),
            ("int fr_rslt_manager(", "PROTO_DRV_REQ_TYPE_FRAME",
             "mbx_ipc_get_msg(")):
        body = function_body(interface, marker)
        type_check = body.find("interface.msgid_get_type(msg.mid)")
        reject = body.find(f"ret != {expected_type}")
        consume = body.find(dequeue)
        check(type_check >= 0 and reject > type_check and consume > reject,
              f"actual firmware result manager must type-check before dequeue: {marker}")
    nw_get = function_body(mbox, "int npu_nw_mbox_ops_get(")
    frame_get = function_body(mbox, "int npu_frame_mbox_ops_get(")
    check("msgid_claim_get_ref(pool, msgid, PROTO_DRV_REQ_TYPE_NW)" in nw_get and
          nw_get.find("if (likely(*target != NULL))") > nw_get.find("msgid_claim_get_ref"),
          "NW adapter no longer gates request mutation on a successful typed claim")
    check("msgid_claim_get_ref(pool, msgid, PROTO_DRV_REQ_TYPE_FRAME)" in frame_get and
          frame_get.find("if (likely(*target != NULL))") > frame_get.find("msgid_claim_get_ref"),
          "frame adapter no longer gates request mutation on a successful typed claim")


def apply_patch_to_source(source: bytes) -> bytes:
    relative = "drivers/vision/npu/core/npu-util-msgidgen.c"
    with tempfile.TemporaryDirectory(prefix="npu-msgid-patch-") as temporary:
        root = Path(temporary)
        target = root / relative
        target.parent.mkdir(parents=True)
        target.write_bytes(source)
        checked = subprocess.run(
            ["git", "apply", "--check", "--whitespace=error-all", str(PATCH_PATH)],
            cwd=root, capture_output=True, text=True, check=False, timeout=10,
        )
        check(checked.returncode == 0,
              f"ordinary git apply --check failed:\n{checked.stderr}")
        applied = subprocess.run(
            ["git", "apply", "--whitespace=error-all", str(PATCH_PATH)],
            cwd=root, capture_output=True, text=True, check=False, timeout=10,
        )
        check(applied.returncode == 0,
              f"ordinary git apply failed:\n{applied.stderr}")
        return target.read_bytes()


def render_harness(template: str, sources: dict[str, bytes]) -> str:
    msgid = sources["drivers/vision/npu/core/npu-util-msgidgen.c"].decode()
    mbox = sources["drivers/vision/npu/core/npu-if-protodrv-mbox2.c"].decode()
    proto = sources["drivers/vision/npu/core/npu-protodrv.c"].decode()
    interface = sources[
        "drivers/vision/npu/core/interface/hardware/npu-interface.c"].decode()
    functions = {
        "/* ACTUAL_MSGID_FUNCTIONS */": "\n\n".join((
            function_body(msgid, "void msgid_pool_init("),
            function_body(msgid, "int msgid_issue("),
            function_body(msgid, "int msgid_issue_save_ref("),
            function_body(msgid, "static inline int __msgid_claim("),
            extract(msgid, ("static inline int __validate_handle_msgid(",
                           "static inline void __validate_handle_msgid(")),
            function_body(msgid, "void msgid_claim("),
            function_body(msgid, "void *msgid_claim_get_ref("),
            function_body(msgid, "int msgid_get_pt_type("),
        )),
        "/* ACTUAL_GET_MSGID_TYPE */": function_body(proto,
                                                       "static int get_msgid_type("),
        "/* ACTUAL_NW_RESULT_MANAGER */": function_body(interface,
                                                          "int nw_rslt_manager("),
        "/* ACTUAL_FRAME_RESULT_MANAGER */": function_body(interface,
                                                            "int fr_rslt_manager("),
        "/* ACTUAL_NW_MBOX_GET */": function_body(mbox,
                                                  "int npu_nw_mbox_ops_get("),
        "/* ACTUAL_FRAME_MBOX_GET */": function_body(mbox,
                                                     "int npu_frame_mbox_ops_get("),
    }
    for marker, body in functions.items():
        check(template.count(marker) == 1,
              f"harness injection marker is not unique: {marker}")
        template = template.replace(marker, body, 1)
    return template


def compile_and_run(cc: list[str], level: str, source: str,
                    directory: Path, baseline: bool) -> subprocess.CompletedProcess:
    source_file = directory / f"npu-msgid-validation-{level[2:]}.c"
    binary = directory / f"npu-msgid-validation-{level[2:]}"
    source_file.write_text(source)
    # Config-disabled production branches leave some pinned parameters unused.
    command = [*cc, "-std=c11", "-Wall", "-Wextra", "-Werror",
               "-Wno-unused-parameter", level]
    if baseline:
        command.append("-DEXPECT_BASELINE_MSGID_BUG=1")
    command.extend([str(source_file), "-o", str(binary)])
    built = subprocess.run(command, capture_output=True, text=True,
                           check=False, timeout=20)
    check(built.returncode == 0,
          f"actual extracted C did not compile at {level}:\n{built.stderr}")
    result = subprocess.run([str(binary)], capture_output=True, text=True,
                            check=False, timeout=10)
    expected = ("REPRO: pinned validator BUGs for negative and out-of-range IDs" in
                result.stdout and
                "REPRO: baseline wrong-type claim returns the reference and releases ownership" in
                result.stdout and
                "REPRO: actual NW result caller mutates a wrong-type mapped request" in
                result.stdout and
                "REPRO: actual frame result caller mutates a wrong-type mapped request" in
                result.stdout and
                "REPRO: firmware NW result path reaches BUG_ON before dequeue" in
                result.stdout and
                "REPRO: firmware frame result path reaches BUG_ON before dequeue" in
                result.stdout) if baseline else (result.returncode == 0 and
                "FAIL:" not in result.stdout and "FAIL:" not in result.stderr)
    check(result.returncode == 0 and expected and "FAIL:" not in result.stdout and
          "FAIL:" not in result.stderr,
          f"extracted C failed expected {'baseline' if baseline else 'fixed'} behavior at {level}:\n"
          f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}")
    return result


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cc", default=os.environ.get("CC", "cc"),
                        help="C compiler command, default: CC or cc")
    args = parser.parse_args()

    patch_bytes = PATCH_PATH.read_bytes()
    check(hashlib.sha256(patch_bytes).hexdigest() == PATCH_SHA256,
          "msgid validation patch SHA-256 changed after freeze")
    sources, identity = load_sources()
    verify_call_graph(sources)
    before = dict(sources)
    after = dict(sources)
    source_key = "drivers/vision/npu/core/npu-util-msgidgen.c"
    after[source_key] = apply_patch_to_source(before[source_key])
    old_harness = render_harness(HARNESS_PATH.read_text(), before)
    new_harness = render_harness(HARNESS_PATH.read_text(), after)
    cc = shlex.split(args.cc)
    check(cc, "C compiler command is empty")
    with tempfile.TemporaryDirectory(prefix="npu-msgid-c-") as temporary:
        directory = Path(temporary)
        for level in ("-O0", "-O2"):
            baseline = compile_and_run(cc, level, old_harness, directory, True)
            fixed = compile_and_run(cc, level, new_harness, directory, False)
            print(f"{level}: PASS: before source reproduces invalid-ID BUG and wrong-type claims")
            print(f"{level}: {fixed.stdout.strip()}")
    print(f"PASS: extracted source fixture {identity}")
    print("PASS: pinned firmware type-gate, NW/frame adapter, and allocator functions included")
    print("PASS: ordinary git apply --check and git apply against actual validator source")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except STACK.HELPERS.SourceFixtureUnavailable as error:
        print(f"UNAVAILABLE: bounded public pinned-source fetch failed: {error}",
              file=sys.stderr)
        raise SystemExit(2)
    except (RuntimeError, OSError, subprocess.TimeoutExpired) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        raise SystemExit(1)
