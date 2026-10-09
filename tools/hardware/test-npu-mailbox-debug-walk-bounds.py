#!/usr/bin/env python3
"""Compile pinned mailbox C and bound the v9 diagnostic ring walker.

This is a host-only extracted-C regression. The actual mailbox print/copy/put
and MARK helper bodies are compiled against a deliberately small byte-ring
shim; this is not kernel-locking, MMIO, firmware, or device evidence.
"""
from __future__ import annotations

import hashlib
import importlib.util
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
STACK_PATH = ROOT / "tools/hardware/test-npu-candidate-stack.py"
PATCH_PATH = ROOT / "tools/hardware/npu-mailbox-debug-walk-bounds.patch"
SOURCE_TREE_ENV = "S22_NPU_PROBE_SOURCE_TREE"
PINNED_BASE = "4e5c5ad7d950e4de0688b5663965f2075654b2ad"
SOURCE_COMMIT = "3fca50941422439b2019db2e4a3dc1016b2138a1"

MAILBOX_C = "drivers/vision/npu/core/interface/hardware/mailbox_ipc.c"
MAILBOX_MSG_DISPATCH_H = "drivers/vision/npu/core/interface/hardware/mailbox_msg.h"
MAILBOX_MSG_H = "drivers/vision/npu/core/interface/hardware/mailbox_msg_v10.h"
MAILBOX_V9_H = "drivers/vision/npu/core/interface/hardware/mailbox_v9.h"
INTERFACE_C = "drivers/vision/npu/core/interface/hardware/npu-interface.c"
PROTO_MBOX_C = "drivers/vision/npu/core/npu-if-protodrv-mbox2.c"
DEFCONFIG = "arch/arm64/configs/s5e9925_defconfig"
SOURCE_SHA256 = {
    MAILBOX_C: "171b039aeedd95edfb8029e572df9dc7cc335c9f9156c22b841dc0bfc6bf30d8",
    MAILBOX_MSG_DISPATCH_H: "5985d2b5ebb34eecd02c2506dbd4dbb51dbe0cf720ad450c9077a3fb50edf0ef",
    MAILBOX_MSG_H: "ca18870a661dce7c1bebe0c008f5c70f14eba93c63539b8cde010294df9c56f0",
    MAILBOX_V9_H: "5719acfe4ac6f366466b390a9e24cbbed0af5461ab7dcff2e1a6e41f44457650",
    INTERFACE_C: "c2deaa0abd990184b64373bb13983f048b925e0f5f421f6623de7de85f667108",
    PROTO_MBOX_C: "d13247bd90ab27dd58cfe9241ec1a07460ab3b353d86a268f8a6fadefc6ff755",
    DEFCONFIG: "de87dbdff5a4082b2aa6fd511a69b9766ddfb0369738c76885c9766178f2b4f5",
}
# Updated after the patch is frozen; a mismatch is intentionally fail-closed.
PATCH_SHA256 = "20c700bfa11f13836c76c88cca28a4f8dfa459e5cf146292a814880cd5850b29"
RING_BYTES = 256  # Scaled power-of-two host ring, not the target mailbox size.


def check(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    check(spec is not None and spec.loader is not None,
          f"cannot load pinned-source helper {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


STACK = load_module(STACK_PATH, "s22_npu_mailbox_walk_source_loader")


def function_declaration(source: str, marker: str) -> str:
    start = source.find(marker)
    while start >= 0:
        brace = source.find("{", start)
        semicolon = source.find(";", start)
        if semicolon >= 0 and (brace < 0 or semicolon < brace):
            start = source.find(marker, semicolon + 1)
            continue
        check(brace >= 0, f"pinned C declaration has no body: {marker}")
        depth = 0
        for end in range(brace, len(source)):
            if source[end] == "{":
                depth += 1
            elif source[end] == "}":
                depth -= 1
                if depth == 0:
                    return source[start:end + 1]
        raise RuntimeError(f"pinned C declaration is incomplete: {marker}")
    raise RuntimeError(f"pinned C declaration is incomplete: {marker}")


def source_block(source: str, start_marker: str, end_marker: str) -> str:
    start = source.find(start_marker)
    end = source.find(end_marker, start + len(start_marker)) if start >= 0 else -1
    check(start >= 0 and end > start,
          f"pinned source block boundaries changed: {start_marker!r}, {end_marker!r}")
    return source[start:end]


def source_sha256(label: str, data: bytes, expected: str) -> None:
    actual = hashlib.sha256(data).hexdigest()
    check(actual == expected,
          f"SHA-256 mismatch for {label}: expected {expected}, got {actual}")


def load_sources() -> tuple[dict[str, bytes], str]:
    configured_root = os.environ.get(SOURCE_TREE_ENV)
    if configured_root:
        source_root = Path(configured_root).expanduser().resolve()
        head = subprocess.run(
            ["git", "-C", str(source_root), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=False, timeout=10,
        )
        check(head.returncode == 0 and head.stdout.strip() == SOURCE_COMMIT,
              f"explicit source tree is not pinned at {SOURCE_COMMIT}: {source_root}")
        status = subprocess.run(
            ["git", "-C", str(source_root), "status", "--porcelain"],
            capture_output=True, text=True, check=False, timeout=10,
        )
        check(status.returncode == 0 and not status.stdout.strip(),
              f"explicit source tree is not clean: {source_root}")
        identity = f"explicit clean derived fixture {source_root}@{SOURCE_COMMIT}"
    else:
        source_root = None
        identity = f"public pinned source at {PINNED_BASE} via bounded SHA loader"

    sources: dict[str, bytes] = {}
    for relative, expected in SOURCE_SHA256.items():
        data = STACK.load_extra_fixture(relative, expected)
        check(len(data) <= STACK.HELPERS.MAX_SOURCE_BYTES,
              f"pinned source exceeds per-file bound: {relative}")
        sources[relative] = data
    total = sum(len(data) for data in sources.values())
    check(total <= len(sources) * STACK.HELPERS.MAX_SOURCE_BYTES,
          "pinned source union exceeds its explicit aggregate bound")
    return sources, identity


def check_target_config(sources: dict[str, bytes]) -> None:
    config = sources[DEFCONFIG].decode("utf-8")
    dispatcher = sources[MAILBOX_MSG_DISPATCH_H].decode("utf-8")
    mailbox_v9 = sources[MAILBOX_V9_H].decode("utf-8")
    required = (
        "CONFIG_NPU_MAILBOX_VERSION=9",
        "CONFIG_NPU_COMMAND_VERSION=10",
        "# CONFIG_NPU_USE_MAILBOX_GROUP is not set",
        "CONFIG_NPU_USE_BOOT_IOCTL=y",
        "CONFIG_DSP_USE_VS4L=y",
    )
    for item in required:
        check(item in config, f"pinned s5e9925 defconfig changed: missing {item}")
    check('#elif (CONFIG_NPU_COMMAND_VERSION == 10)\n#include "mailbox_msg_v10.h"'
          in dispatcher,
          "pinned command-version dispatcher no longer selects the v10 message ABI")
    check("CONFIG_NPU_USE_MAILBOX_GROUP=y" not in config,
          "pinned s5e9925 defconfig unexpectedly enables mailbox groups")
    check(mailbox_v9.count("SZ_128K") == 4 and mailbox_v9.count("SZ_256K") == 2,
          "pinned mailbox v9 segment lengths changed")


def check_callback_path(sources: dict[str, bytes]) -> None:
    interface = sources[INTERFACE_C].decode("utf-8")
    proto_mbox = sources[PROTO_MBOX_C].decode("utf-8")
    mailbox = sources[MAILBOX_C].decode("utf-8")
    registration = "\t.nw_post_request = nw_req_manager,"
    check(registration in interface,
          "actual interface ops no longer register nw_req_manager")
    post = function_declaration(proto_mbox, "int npu_nw_mbox_ops_put(")
    check("protodrv_mbox.npu_if_protodrv_mbox_ops->nw_post_request(msgid, &src->nw)"
          in post, "actual mailbox adapter no longer synchronously invokes nw_post_request")

    manager = function_declaration(interface, "int nw_req_manager(")
    set_cmd = function_declaration(interface, "static int npu_set_cmd(")
    send = source_block(interface, "static int __send_interrupt(",
                        "\nstatic irqreturn_t mailbox_isr0")
    dump = function_declaration(interface, "void dbg_dump_mbox(void)")
    order = (
        manager.find("ret = npu_set_cmd(&msg, &cmd, NPU_MBOX_REQUEST_LOW)"),
        manager.find("if (ret)\n\t\tgoto nw_req_err"),
        manager.find("mbx_ipc_print_dbg("),
        manager.find("return TRUE;"),
    )
    check(all(position >= 0 for position in order) and
          order == tuple(sorted(order)),
          "actual nw_req_manager publication/debug/return order changed")
    check(set_cmd.find("mbx_ipc_put(") < set_cmd.find("__send_interrupt(") and
          "if (ret)\n\t\tgoto I_ERR;" in set_cmd,
          "actual npu_set_cmd write/interrupt failure order changed")
    check("timeout = MAILBOX_CLEAR_CHECK_TIMEOUT;" in send and
          "timeout--;" in send and "dbg_dump_mbox();" in send and
          "-EWOULDBLOCK" in send,
          "actual interrupt timeout and diagnostic dump path changed")
    check("mbx_ipc_print(" in dump and "interface.lock" not in dump,
          "actual timeout dump no longer calls the mailbox walker as reviewed")
    check("interface.lock" not in manager and "interface.lock" not in set_cmd and
          "interface.lock" not in send,
          "a direct interface lock was added to the reviewed callback path")
    check("static void __mbx_ipc_print(" in mailbox,
          "actual mailbox diagnostic implementation is missing")


def extract_mailbox_declarations(sources: dict[str, bytes]) -> tuple[str, str, str]:
    mailbox = sources[MAILBOX_C].decode("utf-8")
    msg_dispatch = sources[MAILBOX_MSG_DISPATCH_H].decode("utf-8")
    msg_header = sources[MAILBOX_MSG_H].decode("utf-8")
    v9_header = sources[MAILBOX_V9_H].decode("utf-8")
    start = msg_header.find("#define MESSAGE_MAX_CNT")
    end_marker = "#endif /* MAILBOX_MSG_H_ */"
    end = msg_header.find(end_marker, start)
    check(start >= 0 and end > start,
          "pinned command-v10 message declarations changed shape")
    check('#include "mailbox_msg_v10.h"' in msg_dispatch,
          "pinned message dispatcher no longer selects the compiled header")
    declarations = msg_header[start:end]
    ctrl = function_declaration(v9_header, "struct mailbox_ctrl") + ";"
    line_match = re.search(r"^#define LINE_TO_SGMT[^\n]*$", mailbox, re.MULTILINE)
    ctrl_match = re.search(r"^#define NPU_MAILBOX_GET_CTRL\(x, y\)[^\n]*$",
                           v9_header, re.MULTILINE)
    check(line_match is not None and ctrl_match is not None,
          "pinned ring address macros changed shape")
    return declarations, ctrl, line_match.group(0) + "\n" + ctrl_match.group(0)


def apply_patch_to_fixture(source: bytes, patch_data: bytes) -> bytes:
    with tempfile.TemporaryDirectory(prefix="npu-mailbox-patch-") as temp_name:
        temp_root = Path(temp_name)
        target = temp_root / MAILBOX_C
        target.parent.mkdir(parents=True)
        target.write_bytes(source)
        patch_file = temp_root / "debug-walk.patch"
        patch_file.write_bytes(patch_data)
        checked = subprocess.run(
            ["git", "apply", "--check", "--whitespace=error-all", str(patch_file)],
            cwd=temp_root, capture_output=True, text=True, check=False, timeout=10,
        )
        check(checked.returncode == 0,
              f"ordinary git apply --check failed: {checked.stderr.strip()}")
        applied = subprocess.run(
            ["git", "apply", "--whitespace=error-all", str(patch_file)],
            cwd=temp_root, capture_output=True, text=True, check=False, timeout=10,
        )
        check(applied.returncode == 0,
              f"ordinary git apply failed: {applied.stderr.strip()}")
        return target.read_bytes()


def rename_function(declaration: str, old: str, new: str) -> str:
    before = declaration.count(old)
    check(before == 1, f"expected exactly one function name {old}, found {before}")
    return declaration.replace(old, new, 1)


C_PRELUDE = r"""
#include <errno.h>
#include <inttypes.h>
#include <setjmp.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef uint32_t u32;
#define EPARAM 41
#define ERESOURCE 43
#define EALIGN 44
#define NPU_LOG_DBG 1
#define NPU_LOG_INFO 2
#define NPU_LOG_ERR 3
#define dmb(order) ((void)0)
#define dsb(order) ((void)0)

struct message;
struct command;
struct mailbox_ctrl;
static unsigned int dbg_rows;
static unsigned int error_count;
static unsigned int message_copy_count;
static unsigned int command_copy_count;
static u32 last_command_offset;
static char last_error[128];
static bool watchdog_active;
static unsigned int watchdog_limit;
static jmp_buf watchdog_env;

static void host_npu_dbg(const char *format, ...)
{
    if (strstr(format, "0x%08X %8d %8d"))
        dbg_rows++;
}

static void host_npu_info(const char *format, ...)
{
    (void)format;
}

static void host_npu_err(const char *format, ...)
{
    va_list args;
    va_start(args, format);
    vsnprintf(last_error, sizeof(last_error), format, args);
    va_end(args);
    error_count++;
}

#define npu_dbg host_npu_dbg
#define npu_info host_npu_info
#define npu_err host_npu_err

static void dbg_print_msg(struct message *message, struct command *command)
{
    (void)message;
    (void)command;
}

static void dbg_print_ctrl(volatile struct mailbox_ctrl *ctrl)
{
    (void)ctrl;
}
"""

C_MAIN = r"""
static char mailbox_arena[1024] __attribute__((aligned(8)));
static struct mailbox_ctrl control;

static void fail(const char *message)
{
    fprintf(stderr, "npu-mailbox-debug-walk-bounds: %s\n", message);
    exit(1);
}

static void expect(bool condition, const char *message)
{
    if (!condition)
        fail(message);
}

static char *ring_base(void)
{
    return NPU_MAILBOX_GET_CTRL(mailbox_arena, control.sgmt_ofs);
}

static void reset_case(u32 segment_length)
{
    memset(mailbox_arena, 0, sizeof(mailbox_arena));
    control.sgmt_ofs = 64;
    control.sgmt_len = segment_length;
    control.rptr = 0;
    control.wptr = 0;
    dbg_rows = 0;
    error_count = 0;
    message_copy_count = 0;
    command_copy_count = 0;
    last_command_offset = 0;
    last_error[0] = '\0';
}

static int put_message(u32 mid)
{
    struct message message = { 0 };
    struct command command = { 0 };
    message.mid = mid;
    message.command = COMMAND_LOAD;
    message.length = sizeof(struct command);
    command.c.load.oid = mid;
    command.payload = mid + 0x100;
    return mbx_ipc_put(mailbox_arena, &control, &message, &command);
}

static void write_ring_word(u32 logical_offset, u32 value)
{
    char *base = ring_base();
    memcpy(base + (logical_offset & (control.sgmt_len - 1)), &value, sizeof(value));
}

static void prepare_cycle_record(void)
{
    struct message message = { 0 };
    struct command command = { 0 };
    reset_case(RING_BYTES);
    control.rptr = 16;
    control.wptr = 64;
    message.magic = MESSAGE_MAGIC;
    message.mid = 5;
    message.command = COMMAND_LOAD;
    message.length = sizeof(struct command);
    message.data = UINT32_MAX - 7;
    command.c.load.oid = 5;
    __copy_message_to_line(ring_base(), control.sgmt_len, control.rptr, &message);
    memcpy(ring_base() + (message.data & (control.sgmt_len - 1)),
           &command, sizeof(command));
}

static void test_baseline_stall(void)
{
    prepare_cycle_record();
    watchdog_active = true;
    watchdog_limit = 6;
    if (setjmp(watchdog_env) == 0) {
        __mbx_ipc_print_baseline(mailbox_arena, &control, NPU_LOG_DBG);
        watchdog_active = false;
        fail("pinned baseline unexpectedly returned from non-progressing ring");
    }
    watchdog_active = false;
    expect(message_copy_count == watchdog_limit,
           "baseline watchdog did not observe repeated actual C header reads");
    expect(command_copy_count == watchdog_limit - 1,
           "baseline did not repeat the actual command-copy path before bound");
    expect(last_command_offset == UINT32_MAX - 7,
           "baseline did not repeatedly read from the malformed stale pointer");
    puts("BASELINE REPRO actual mailbox C: next_rptr == rptr repeated until host read watchdog");
}

static void test_valid_queue_and_mark(void)
{
    struct message previous = { 0 }, next = { 0 };
    reset_case(RING_BYTES);
    expect(put_message(10) == 0 && put_message(11) == 0,
           "actual mbx_ipc_put did not produce two normal records");
    __copy_message_from_line(ring_base(), control.sgmt_len, control.rptr, &previous);
    expect(mbx_ipc_ref_msg(mailbox_arena, &control, &previous, &next) == 0,
           "actual mbx_ipc_ref_msg could not locate the second record");
    expect(mbx_ipc_clr_msg(mailbox_arena, &control, &next) == 0,
           "actual mbx_ipc_clr_msg could not mark the second record");
    dbg_rows = error_count = 0;
    __mbx_ipc_print_patched(mailbox_arena, &control, NPU_LOG_DBG);
    expect(error_count == 0 && dbg_rows == 1,
           "valid normal record plus MESSAGE_MARK did not print/skip exactly once");
    puts("PASS actual mailbox C: normal message, intervening MESSAGE_MARK, producer layout");
}

static void test_empty_and_budget_edge(void)
{
    unsigned int i;
    reset_case(RING_BYTES);
    __mbx_ipc_print_patched(mailbox_arena, &control, NPU_LOG_DBG);
    expect(error_count == 0 && dbg_rows == 0,
           "empty mailbox should complete without rows or errors");

    reset_case(RING_BYTES);
    for (i = 0; i < RING_BYTES / (sizeof(struct message) + sizeof(struct command)); i++)
        expect(put_message(i + 1) == 0, "scaled producer could not fill edge queue");
    expect(control.wptr == RING_BYTES - (RING_BYTES % 48),
           "scaled producer edge pointer changed unexpectedly");
    dbg_rows = error_count = 0;
    __mbx_ipc_print_patched(mailbox_arena, &control, NPU_LOG_DBG);
    expect(error_count == 0 && dbg_rows == 5,
           "maximum valid records for the scaled ring did not complete");
    expect(put_message(99) < 0,
           "scaled ring accepted one record past its producer capacity");
    puts("PASS actual mailbox C: empty queue and maximum valid scaled-ring record count");
}

static void test_wrapped_header_and_padding(void)
{
    reset_case(RING_BYTES);
    control.rptr = 240;
    control.wptr = 240;
    expect(put_message(20) == 0 && control.wptr == 288,
           "actual producer could not write header-wrapped record");
    dbg_rows = error_count = 0;
    __mbx_ipc_print_patched(mailbox_arena, &control, NPU_LOG_DBG);
    expect(error_count == 0 && dbg_rows == 1,
           "wrapped message header was not read through the production ring mask");

    reset_case(RING_BYTES);
    control.rptr = 216;
    control.wptr = 216;
    expect(put_message(21) == 0 && control.wptr == 280,
           "actual producer did not insert the expected wrap padding");
    {
        struct message message = { 0 };
        __copy_message_from_line(ring_base(), control.sgmt_len, control.rptr, &message);
        expect(message.data == 256,
               "actual producer's command pointer did not skip the wrap padding");
    }
    dbg_rows = error_count = 0;
    __mbx_ipc_print_patched(mailbox_arena, &control, NPU_LOG_DBG);
    expect(error_count == 0 && dbg_rows == 1,
           "valid command after producer padding was rejected or stalled");
    puts("PASS actual mailbox C: wrapped header and mbx_ipc_put command padding");
}

static void expect_one_rejection(const char *what)
{
    dbg_rows = error_count = 0;
    message_copy_count = command_copy_count = 0;
    last_error[0] = '\0';
    __mbx_ipc_print_patched(mailbox_arena, &control, NPU_LOG_DBG);
    if (error_count != 1 || dbg_rows != 0) {
        fprintf(stderr, "npu-mailbox-debug-walk-bounds: %s: errors=%u rows=%u\n",
                what, error_count, dbg_rows);
        exit(1);
    }
}

static void test_corrupt_ring_boundaries(void)
{
    u32 value;

    reset_case(RING_BYTES);
    control.wptr = 20;
    expect_one_rejection("incomplete message header");
    expect(message_copy_count == 0,
           "truncated header was copied before readable-byte validation");

    reset_case(250);
    expect_one_rejection("non-power-of-two segment");

    reset_case(RING_BYTES);
    control.wptr = RING_BYTES + 1;
    expect_one_rejection("occupancy exceeds ring length");

    reset_case(RING_BYTES);
    control.rptr = 4;
    control.wptr = 3;
    expect_one_rejection("backwards logical pointers");

    reset_case(RING_BYTES);
    control.rptr = 1;
    control.wptr = 49;
    expect_one_rejection("unaligned ring pointers");
    expect(message_copy_count == 0,
           "unaligned message header was read through volatile u32 accesses");

    reset_case(RING_BYTES);
    expect(put_message(30) == 0, "failed to prepare corrupt-length case");
    value = sizeof(struct command) - 4;
    write_ring_word(control.rptr + 12, value);
    expect_one_rejection("bad message length");

    reset_case(RING_BYTES);
    expect(put_message(31) == 0, "failed to prepare short-data-pointer case");
    write_ring_word(control.rptr + 20, control.rptr + 4);
    expect_one_rejection("data pointer overlaps header");
    expect(command_copy_count == 0,
           "command was copied before its data pointer was validated");

    reset_case(RING_BYTES);
    expect(put_message(34) == 0, "failed to prepare unaligned-data case");
    write_ring_word(control.rptr + 20, control.rptr + sizeof(struct message) + 1);
    expect_one_rejection("unaligned command data pointer");
    expect(command_copy_count == 0,
           "unaligned command pointer reached the copy helper");

    reset_case(RING_BYTES);
    expect(put_message(32) == 0, "failed to prepare out-of-range case");
    write_ring_word(control.rptr + 20, control.wptr + 4);
    expect_one_rejection("data pointer past wptr");
    expect(command_copy_count == 0,
           "out-of-range command pointer reached the actual copy helper");

    reset_case(RING_BYTES);
    expect(put_message(33) == 0, "failed to prepare truncated-command case");
    write_ring_word(control.rptr + 20, control.wptr - 8);
    expect_one_rejection("command extends beyond wptr");
    expect(command_copy_count == 0,
           "truncated command reached the actual copy helper");

    prepare_cycle_record();
    expect_one_rejection("non-progressing modular pointer");
    expect(command_copy_count == 0,
           "non-progressing record reached the command copy helper");
    reset_case(RING_BYTES);
    control.rptr = UINT32_MAX - 15;
    control.wptr = 32;
    expect_one_rejection("32-bit monotonic counter wrapped");
    expect(message_copy_count == 0,
           "wrapped 32-bit pointer counters were read as a valid queue");

    puts("PASS patched actual mailbox C: malformed length/alignment/ring/header/data/non-progress and u32-wrap bounds");
}

static void test_explicit_walk_budget(void)
{
    struct message message = { 0 };
    struct command command = { 0 };
    u32 rptr;
    reset_case(RING_BYTES);
    control.wptr = RING_BYTES;
    command.c.load.oid = 1;
    for (rptr = 0; rptr < 5 * 48; rptr += 48) {
        message.magic = MESSAGE_MAGIC;
        message.mid = rptr / 48 + 1;
        message.command = COMMAND_LOAD;
        message.length = sizeof(struct command);
        message.data = rptr + sizeof(struct message);
        __copy_message_to_line(ring_base(), control.sgmt_len, rptr, &message);
        __copy_command_to_line(ring_base(), control.sgmt_len, message.data,
                               &command, sizeof(command));
    }
    dbg_rows = error_count = 0;
    __mbx_ipc_print_patched(mailbox_arena, &control, NPU_LOG_DBG);
    expect(error_count == 1 && dbg_rows == 5,
           "walk-record budget did not stop the valid-prefix-plus-trailing-bytes case");
    puts("PASS patched actual mailbox C: explicit floor(ring/min-record) walk budget");
}

int main(void)
{
    _Static_assert(sizeof(struct message) == 24, "pinned v9 message layout changed");
    _Static_assert(sizeof(struct command) == 24, "pinned v9 command layout changed");
    test_baseline_stall();
    test_valid_queue_and_mark();
    test_empty_and_budget_edge();
    test_wrapped_header_and_padding();
    test_corrupt_ring_boundaries();
    test_explicit_walk_budget();
    return 0;
}
"""


def make_translation_unit(before: str, after: str,
                          declarations: str, ctrl_decl: str,
                          ring_macros: str) -> str:
    exact_helpers = []
    for marker, renamed in (
        ("static inline u32 __get_readable_size(", None),
        ("static inline u32 __get_writable_size(", None),
        ("static inline u32 __copy_message_from_line(",
         "__copy_message_from_line_exact"),
        ("static inline u32 __copy_message_to_line(", None),
        ("static inline u32 __copy_command_from_line(",
         "__copy_command_from_line_exact"),
        ("static inline u32 __copy_command_to_line(", None),
    ):
        item = function_declaration(before, marker)
        if renamed:
            match = re.search(r"\b(__copy_(?:message|command)_from_line)\(", marker)
            check(match is not None, f"helper name parser failed for {marker}")
            item = rename_function(item, match.group(1), renamed)
        exact_helpers.append(item)

    # The source declaration marker includes its parameter list; normalize only
    # the two helper names to leave room for the bounded test instrumentation.
    helper_text = "\n\n".join(exact_helpers)
    # The replacement above is intentionally guarded by exact name checks.
    helper_text = helper_text.replace(
        "__copy_message_from_line(", "__copy_message_from_line_exact(")
    helper_text = helper_text.replace(
        "__copy_command_from_line(", "__copy_command_from_line_exact(")
    wrappers = r"""
static inline u32 __copy_message_from_line(char *base, u32 segment_length,
                                           u32 pointer, struct message *message)
{
    message_copy_count++;
    if (watchdog_active && message_copy_count >= watchdog_limit)
        longjmp(watchdog_env, 1);
    return __copy_message_from_line_exact(base, segment_length, pointer, message);
}

static inline u32 __copy_command_from_line(char *base, u32 segment_length,
                                           u32 pointer, void *command, u32 size)
{
    command_copy_count++;
    last_command_offset = pointer;
    return __copy_command_from_line_exact(base, segment_length, pointer, command, size);
}
"""

    producer = function_declaration(before, "int mbx_ipc_put(")
    ref = function_declaration(before, "int mbx_ipc_ref_msg(")
    clear = function_declaration(before, "int mbx_ipc_clr_msg(")
    baseline = rename_function(
        function_declaration(before, "static void __mbx_ipc_print("),
        "__mbx_ipc_print", "__mbx_ipc_print_baseline")
    patched = rename_function(
        function_declaration(after, "static void __mbx_ipc_print("),
        "__mbx_ipc_print", "__mbx_ipc_print_patched")
    return "\n".join((
        C_PRELUDE,
        declarations,
        ctrl_decl,
        ring_macros,
        "#define RING_BYTES 256",
        "_Static_assert(sizeof(struct message) == 24, \"message layout\");",
        "_Static_assert(sizeof(struct command) == 24, \"command layout\");",
        helper_text,
        wrappers,
        producer,
        ref,
        clear,
        baseline,
        patched,
        C_MAIN,
    ))


def compile_and_run(code: str, optimization: int, compiler: list[str]) -> None:
    with tempfile.TemporaryDirectory(prefix="npu-mailbox-debug-c-") as temp_name:
        temp_root = Path(temp_name)
        source_path = temp_root / "mailbox_debug_walk.c"
        binary_path = temp_root / "mailbox_debug_walk"
        source_path.write_text(code, encoding="utf-8")
        command = compiler + [
            "-std=gnu11", "-Wall", "-Wextra", "-Werror",
            "-Wno-unused-parameter",
            f"-O{optimization}", str(source_path), "-o", str(binary_path),
        ]
        built = subprocess.run(command, capture_output=True, text=True,
                               check=False, timeout=20)
        check(built.returncode == 0,
              f"host C -O{optimization} compile failed:\n{built.stderr}")
        result = subprocess.run([str(binary_path)], capture_output=True,
                                text=True, check=False, timeout=5)
        check(result.returncode == 0,
              f"host C -O{optimization} regression failed:\n"
              f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}")
        print(result.stdout, end="")
        print(f"PASS extracted mailbox C -O{optimization} with -Wall -Wextra -Werror")


def run() -> None:
    patch_data = PATCH_PATH.read_bytes()
    source_sha256(PATCH_PATH.name, patch_data, PATCH_SHA256)
    sources, identity = load_sources()
    check_target_config(sources)
    check_callback_path(sources)

    before = sources[MAILBOX_C].decode("utf-8")
    after_bytes = apply_patch_to_fixture(sources[MAILBOX_C], patch_data)
    after = after_bytes.decode("utf-8")
    check(function_declaration(before, "int mbx_ipc_put(") ==
          function_declaration(after, "int mbx_ipc_put("),
          "debug-only patch unexpectedly changes mailbox publication writes")
    declarations, ctrl_decl, ring_macros = extract_mailbox_declarations(sources)
    translation_unit = make_translation_unit(before, after, declarations,
                                             ctrl_decl, ring_macros)
    compiler = shlex.split(os.environ.get("CC", "cc"))
    check(bool(compiler), "CC resolved to an empty compiler command")
    print(f"Pinned source: {identity}")
    print("Source boundary: exact SHA-verified mailbox-v9 / command-v10 ABI and target defconfig")
    for optimization in (0, 2):
        compile_and_run(translation_unit, optimization, compiler)


if __name__ == "__main__":
    try:
        run()
    except STACK.SourceFixtureUnavailable as error:
        print(f"SKIP pinned public source unavailable: {error}", file=sys.stderr)
        sys.exit(77)
