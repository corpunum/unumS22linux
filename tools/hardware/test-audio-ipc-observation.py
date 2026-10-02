#!/usr/bin/env python3
"""Host-compile pinned ABOX production paths and the patch's trace payloads.

The test reconstructs only the pinned, public source fixtures in a temporary
tree, applies audio-ipc-observation-fix.patch, extracts the actual production
functions, and compiles/runs them against small kernel API shims. It does not
read device state or interact with a kernel/device.
"""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile


DERIVED_HEAD = "3fca50941422439b2019db2e4a3dc1016b2138a1"
BASE_COMMIT = "4e5c5ad7d950e4de0688b5663965f2075654b2ad"
SOURCE_FILES = {
    "include/sound/samsung/abox_ipc.h": "2bef750b3efdf799543baa6661bdb341069e8678c761ad334718ca5fa3d33342",
    "sound/soc/samsung/abox/abox.h": "d6743614eb05dc8e3fe1b740fbccacb4d8e49028fb009fc5b40b8647a66530d0",
    "sound/soc/samsung/abox/abox.c": "ac9c39c5f2f91cad3a4031e4d592f35bf1391579f29dcee6f73da4919d6ad6f2",
    "sound/soc/samsung/abox/abox_rdma.c": "6c7e7fa10a7517ac15192df6ab5a182685fb3e79dc563dd47bb96aa0c6bab0df",
}
MAX_SOURCE_BYTES = 256 * 1024


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def run(command: list[str], *, cwd: Path | None = None) -> str:
    completed = subprocess.run(
        command,
        cwd=cwd,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    require(
        completed.returncode == 0,
        f"command failed ({completed.returncode}): {shlex.join(command)}\n{completed.stdout}",
    )
    return completed.stdout.strip()


def checked_source(source_root: Path) -> dict[str, bytes]:
    head = run(["git", "-C", str(source_root), "rev-parse", "HEAD"])
    require(head == DERIVED_HEAD, f"source HEAD mismatch: expected {DERIVED_HEAD}, got {head}")
    run(["git", "-C", str(source_root), "cat-file", "-e", f"{BASE_COMMIT}^{{commit}}"])

    fixtures: dict[str, bytes] = {}
    for relpath, expected_hash in SOURCE_FILES.items():
        source_size = int(run(["git", "-C", str(source_root), "cat-file", "-s", f"{BASE_COMMIT}:{relpath}"]))
        require(source_size <= MAX_SOURCE_BYTES, f"source cap exceeded: {relpath} ({source_size} bytes)")
        source = run_bytes(["git", "-C", str(source_root), "show", f"{BASE_COMMIT}:{relpath}"])
        require(len(source) == source_size, f"source size changed while reading: {relpath}")
        digest = hashlib.sha256(source).hexdigest()
        require(digest == expected_hash, f"pinned-source hash mismatch for {relpath}: {digest}")
        worktree_file = source_root / relpath
        require(worktree_file.is_file(), f"missing worktree source: {worktree_file}")
        require(
            hashlib.sha256(worktree_file.read_bytes()).hexdigest() == expected_hash,
            f"worktree source changed from pinned fixture: {relpath}",
        )
        fixtures[relpath] = source
    return fixtures


def run_bytes(command: list[str]) -> bytes:
    completed = subprocess.run(command, check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    require(
        completed.returncode == 0,
        f"command failed ({completed.returncode}): {shlex.join(command)}\n"
        f"{completed.stderr.decode(errors='replace')}",
    )
    return completed.stdout


def c_block(source: str, marker: str, *, kind: str = "function") -> str:
    """Extract one brace-balanced C function or struct from pinned source."""
    if kind == "function":
        matches = list(re.finditer(rf"\b{re.escape(marker)}\s*\(", source))
        require(matches, f"missing production function: {marker}")
        match = None
        brace = -1
        for candidate in matches:
            open_paren = source.find("(", candidate.start())
            depth = 0
            close_paren = -1
            for index in range(open_paren, len(source)):
                if source[index] == "(":
                    depth += 1
                elif source[index] == ")":
                    depth -= 1
                    if depth == 0:
                        close_paren = index
                        break
            if close_paren < 0:
                continue
            candidate_brace = close_paren + 1
            while candidate_brace < len(source) and source[candidate_brace].isspace():
                candidate_brace += 1
            if candidate_brace < len(source) and source[candidate_brace] == "{":
                match = candidate
                brace = candidate_brace
                break
        require(match is not None, f"not a function definition: {marker}")
        start = source.rfind("\n", 0, match.start()) + 1
    elif kind == "struct":
        match = re.search(rf"\bstruct\s+{re.escape(marker)}\s*\{{", source)
        require(match is not None, f"missing production struct: {marker}")
        brace = source.find("{", match.start())
        start = match.start()
    else:
        match = re.search(rf"\benum\s+{re.escape(marker)}\s*\{{", source)
        require(match is not None, f"missing production enum: {marker}")
        brace = source.find("{", match.start())
        start = match.start()

    depth = 0
    state = "code"
    quote = ""
    index = brace
    while index < len(source):
        char = source[index]
        nxt = source[index + 1] if index + 1 < len(source) else ""
        if state == "code":
            if char == "/" and nxt == "*":
                state = "block_comment"
                index += 1
            elif char == "/" and nxt == "/":
                state = "line_comment"
                index += 1
            elif char in ("'", '"'):
                state = "string"
                quote = char
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    end = index + 1
                    if kind in ("struct", "enum"):
                        semicolon = source.find(";", end)
                        require(semicolon >= 0, f"missing declaration semicolon: {marker}")
                        end = semicolon + 1
                    return source[start:end]
        elif state == "block_comment":
            if char == "*" and nxt == "/":
                state = "code"
                index += 1
        elif state == "line_comment":
            if char == "\n":
                state = "code"
        elif state == "string":
            if char == "\\":
                index += 1
            elif char == quote:
                state = "code"
        index += 1
    raise RuntimeError(f"unterminated {kind}: {marker}")


def c_define(source: str, name: str) -> str:
    match = re.search(rf"^#define\s+{re.escape(name)}\b.*$", source, re.MULTILINE)
    require(match is not None, f"missing production definition: {name}")
    return match.group(0)


HOST_PREAMBLE = r"""
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>

typedef uint64_t u64;
typedef int irqreturn_t;
typedef int spinlock_t;
struct device { void *driver_data; };
struct work_struct { int unused; };

/* IPC_TYPES_HERE */
enum { IRQ_NONE = 0, IRQ_HANDLED = 1 };
#define ENODATA 61
#define ARRAY_SIZE(a) ((int)(sizeof(a) / sizeof((a)[0])))
#define unlikely(x) (x)
#define container_of(ptr, type, member) \
    ((type *)((char *)(ptr) - offsetof(type, member)))
#define abox_dbg(...) ((void)0)
#define abox_info(...) ((void)0)
#define abox_err(...) ((void)0)
#define abox_warn(...) ((void)0)
#define dev_warn_ratelimited(...) ((void)0)

static unsigned int clock_calls;
static unsigned int atomic_increment_calls;
static unsigned int allocation_calls;
static unsigned int queue_work_calls;
static unsigned int flush_work_calls;
static unsigned int delay_calls;
static unsigned int send_calls;
static unsigned int failsafe_calls;
static unsigned int period_elapsed_calls;
static u64 fake_clock;
static int send_result;
static bool check_queue_trace_before_work;
static int failures;

#define CHECK(test, label) do { \
    if (!(test)) { \
        fprintf(stderr, "FAIL: %s at %s:%d\n", label, __FILE__, __LINE__); \
        ++failures; \
    } \
} while (0)

static void *host_alloc(size_t size)
{
    ++allocation_calls;
    return calloc(1, size);
}
#define kmalloc(size, flags) ((void)(flags), host_alloc(size))
#define kzalloc(size, flags) ((void)(flags), host_alloc(size))
#define kcalloc(count, size, flags) ((void)(flags), host_alloc((count) * (size)))
#define devm_kzalloc(dev, size, flags) ((void)(dev), (void)(flags), host_alloc(size))

typedef struct { u64 counter; } atomic64_t;
#define ATOMIC64_INIT(value) { .counter = (value) }
static u64 atomic64_inc_return(atomic64_t *value)
{
    ++atomic_increment_calls;
    return ++value->counter;
}
static u64 ktime_get_ns(void)
{
    ++clock_calls;
    fake_clock += 100;
    return fake_clock;
}
static unsigned long long sched_clock(void) { return 1; }
static void host_spin_lock(spinlock_t *lock) { (void)lock; }
static void host_spin_unlock(spinlock_t *lock) { (void)lock; }
#define spin_lock_irqsave(lock, flags) do { (flags) = 0; host_spin_lock(lock); } while (0)
#define spin_unlock_irqrestore(lock, flags) do { (void)(flags); host_spin_unlock(lock); } while (0)
static bool queue_work(void *queue, struct work_struct *work);
static void flush_work(struct work_struct *work)
{ (void)work; ++flush_work_calls; }
static void mdelay(unsigned int delay) { (void)delay; ++delay_calls; }
static void pm_runtime_get_sync(struct device *dev) { (void)dev; }
static void pm_runtime_mark_last_busy(struct device *dev) { (void)dev; }
static void pm_runtime_put_autosuspend(struct device *dev) { (void)dev; }
static void abox_failsafe_report(struct device *dev, bool report)
{ (void)dev; (void)report; ++failsafe_calls; }
static int abox_ipc_send(struct device *dev, const ABOX_IPC_MSG *msg,
                         size_t size, void *reply, size_t reply_size)
{
    (void)dev; (void)msg; (void)size; (void)reply; (void)reply_size;
    ++send_calls;
    return send_result;
}
static void *dev_get_drvdata(struct device *dev) { return dev->driver_data; }
static void snd_pcm_period_elapsed(void *substream)
{ (void)substream; ++period_elapsed_calls; }
static void complete(void *completion) { (void)completion; }

/* Host representation of the kernel trace-event declaration/assignment API.
 * Including the patch's real header below compiles each real TP_fast_assign.
 */
#define TP_PROTO(...) (__VA_ARGS__)
#define TP_ARGS(...)
#define __field(type, name) type name;
#define TP_STRUCT__entry(...) __VA_ARGS__
#define TP_fast_assign(...) __VA_ARGS__
#define TP_printk(...)
#define TRACE_EVENT(name, proto, args, entry, assign, print) \
    struct trace_data_##name { entry }; \
    static struct trace_data_##name last_##name; \
    static bool enable_##name; \
    static unsigned int count_##name; \
    static bool trace_##name##_enabled(void) { return enable_##name; } \
    static void trace_##name proto \
    { struct trace_data_##name *__entry = &last_##name; assign; ++count_##name; }
"""


HOST_HARNESS = r"""
static void reset_observation_counters(void)
{
    clock_calls = atomic_increment_calls = queue_work_calls = 0;
    allocation_calls = 0;
    flush_work_calls = delay_calls = send_calls = failsafe_calls = 0;
    period_elapsed_calls = 0;
    fake_clock = 1000;
    send_result = 0;
    check_queue_trace_before_work = false;
    enable_abox_pcm_trigger_queue = false;
    enable_abox_pcm_trigger_send = false;
    enable_abox_rdma_pointer_handler = false;
    count_abox_pcm_trigger_queue = 0;
    count_abox_pcm_trigger_send = 0;
    count_abox_rdma_pointer_handler = 0;
    abox_pcm_trigger_trace_seq.counter = 0;
}

static ABOX_IPC_MSG trigger_message(int channel)
{
    ABOX_IPC_MSG msg = {0};
    msg.ipcid = IPC_PCMPLAYBACK;
    msg.msg.pcmtask.channel_id = channel;
    msg.msg.pcmtask.msgtype = PCM_PLTDAI_TRIGGER;
    msg.msg.pcmtask.param.trigger = 1;
    return msg;
}

static void test_disabled_default(void)
{
    struct device dev = {0};
    struct abox_data data = {0};
    ABOX_IPC_MSG msg = trigger_message(4);
    reset_observation_counters();
    CHECK(abox_schedule_ipc(&dev, &data, 3, &msg, sizeof(msg), true, false) == 0,
          "disabled trigger queue result preserved");
    CHECK(count_abox_pcm_trigger_queue == 0 && count_abox_pcm_trigger_send == 0,
          "tracepoints disabled by default");
    CHECK(clock_calls == 0 && atomic_increment_calls == 0,
          "disabled path performs no timestamp or sequence work");
    CHECK(allocation_calls == 0,
          "disabled path performs no observation allocation");
    CHECK(queue_work_calls == 1 && data.ipc_queue[0].trace_seq == 0,
          "disabled path queues normally with zero metadata");
    abox_process_ipc(&data.ipc_work);
    CHECK(send_calls == 1 && count_abox_pcm_trigger_send == 0,
          "disabled worker sends normally without a send event");
    CHECK(clock_calls == 0 && atomic_increment_calls == 0,
          "disabled worker adds no timestamp or sequence work");
    CHECK(allocation_calls == 0,
          "disabled worker adds no observation allocation");
    CHECK(failures == 0, "disabled default test assertions");
}

static void test_async_queue_send_success(void)
{
    struct device dev = {0};
    struct abox_data data = {0};
    ABOX_IPC_MSG msg = trigger_message(6);
    reset_observation_counters();
    enable_abox_pcm_trigger_queue = true;
    enable_abox_pcm_trigger_send = true;
    check_queue_trace_before_work = true;
    CHECK(abox_schedule_ipc(&dev, &data, 9, &msg, sizeof(msg), true, false) == 0,
          "async queue insertion reports success");
    CHECK(count_abox_pcm_trigger_queue == 1 && count_abox_pcm_trigger_send == 0,
          "queue insertion is distinct from deferred send result");
    CHECK(last_abox_pcm_trigger_queue.sequence == 1 &&
          last_abox_pcm_trigger_queue.ipc_id == IPC_PCMPLAYBACK &&
          last_abox_pcm_trigger_queue.channel == 6 &&
          last_abox_pcm_trigger_queue.msg_type == PCM_PLTDAI_TRIGGER &&
          last_abox_pcm_trigger_queue.result == 0 &&
          last_abox_pcm_trigger_queue.attempt == 0 &&
          last_abox_pcm_trigger_queue.atomic &&
          !last_abox_pcm_trigger_queue.sync,
          "actual queue TP_fast_assign fields");
    CHECK(data.ipc_queue[0].trace_seq == 1,
          "correlation metadata stored in internal queue slot");
    abox_process_ipc(&data.ipc_work);
    CHECK(send_calls == 1 && count_abox_pcm_trigger_send == 1,
          "async worker reports one local sender result");
    CHECK(last_abox_pcm_trigger_send.sequence == 1 &&
          last_abox_pcm_trigger_send.ipc_id == IPC_PCMPLAYBACK &&
          last_abox_pcm_trigger_send.channel == 6 &&
          last_abox_pcm_trigger_send.msg_type == PCM_PLTDAI_TRIGGER &&
          last_abox_pcm_trigger_send.result == 0 &&
          last_abox_pcm_trigger_send.mono_ns >
              last_abox_pcm_trigger_queue.mono_ns,
          "actual send TP_fast_assign correlation/routing/timestamp");
    CHECK(atomic_increment_calls == 1 && clock_calls == 2,
          "enabled matching trigger uses one sequence and two timestamps");
    CHECK(failures == 0, "async queue/send success assertions");
}

static void test_local_send_failure(void)
{
    struct device dev = {0};
    struct abox_data data = {0};
    ABOX_IPC_MSG msg = trigger_message(1);
    reset_observation_counters();
    enable_abox_pcm_trigger_queue = true;
    enable_abox_pcm_trigger_send = true;
    send_result = -EIO;
    CHECK(abox_schedule_ipc(&dev, &data, 0, &msg, sizeof(msg), true, false) == 0,
          "async API still returns queue insertion result");
    abox_process_ipc(&data.ipc_work);
    CHECK(last_abox_pcm_trigger_send.result == -EIO && send_calls == 1,
          "sender failure recorded without changing sender result");
    CHECK(failsafe_calls == 1, "worker retains sender failure failsafe behavior");
    CHECK(failures == 0, "local sender failure assertions");
}

static void test_sender_only_trace_selection(void)
{
    struct device dev = {0};
    struct abox_data data = {0};
    ABOX_IPC_MSG msg = trigger_message(8);
    reset_observation_counters();
    enable_abox_pcm_trigger_send = true;
    CHECK(abox_schedule_ipc(&dev, &data, 0, &msg, sizeof(msg), true, false) == 0,
          "sender-only trace selection preserves async queue result");
    CHECK(count_abox_pcm_trigger_queue == 0 &&
          count_abox_pcm_trigger_send == 0 && clock_calls == 0 &&
          atomic_increment_calls == 1 && data.ipc_queue[0].trace_seq == 1,
          "sender-only selection creates internal correlation without queue event");
    abox_process_ipc(&data.ipc_work);
    CHECK(count_abox_pcm_trigger_send == 1 &&
          last_abox_pcm_trigger_send.sequence == 1 &&
          last_abox_pcm_trigger_send.channel == 8 && clock_calls == 1,
          "sender-only selection records local send event");
    CHECK(failures == 0, "sender-only trace selection assertions");
}

static void test_queue_full_retries(void)
{
    struct device dev = {0};
    struct abox_data data = {0};
    ABOX_IPC_MSG ordinary = {0};
    ABOX_IPC_MSG msg = trigger_message(3);
    size_t index;
    int ret;
    reset_observation_counters();
    for (index = 0; index < ABOX_IPC_QUEUE_SIZE - 1; ++index) {
        CHECK(abox_ipc_queue_put(&data, &dev, 0, &ordinary,
                                 sizeof(ordinary), 0) == 0,
              "prefill ring for queue-full case");
    }
    enable_abox_pcm_trigger_queue = true;
    check_queue_trace_before_work = true;
    ret = abox_schedule_ipc(&dev, &data, 0, &msg, sizeof(msg), true, false);
    CHECK(ret == -EBUSY, "queue-full result remains negative");
    CHECK(count_abox_pcm_trigger_queue == 11 && queue_work_calls == 11,
          "all original retry attempts are observed and scheduled");
    CHECK(last_abox_pcm_trigger_queue.sequence == 1 &&
          last_abox_pcm_trigger_queue.result == -EBUSY &&
          last_abox_pcm_trigger_queue.attempt == 10,
          "queue failure reports final attempt result");
    CHECK(send_calls == 0 && count_abox_pcm_trigger_send == 0,
          "failed enqueue has no sender result");
    CHECK(failsafe_calls == 1 && delay_calls == 11,
          "overflow failsafe and atomic retry delays are preserved");
    CHECK(clock_calls == 11 && atomic_increment_calls == 1,
          "one trigger correlation ID and per-attempt queue timestamps");
    CHECK(failures == 0, "queue-full retry assertions");
}

static void test_message_filter_and_stale_metadata(void)
{
    struct device dev = {0};
    struct abox_data data = {0};
    ABOX_IPC_MSG msg = trigger_message(5);
    struct abox_ipc queued = {0};
    size_t index;
    reset_observation_counters();
    enable_abox_pcm_trigger_queue = true;
    msg.ipcid = IPC_PCMCAPTURE;
    CHECK(abox_schedule_ipc(&dev, &data, 0, &msg, sizeof(msg), true, false) == 0,
          "capture request preserves queue behavior");
    CHECK(count_abox_pcm_trigger_queue == 0 && atomic_increment_calls == 0 &&
          clock_calls == 0 && data.ipc_queue[0].trace_seq == 0,
          "capture message excluded before correlation/timestamp");
    CHECK(abox_ipc_queue_get(&data, &queued) == 0,
          "filtered message dequeues normally");
    CHECK(queued.trace_seq == 0, "filtered message carries no correlation");

    msg = trigger_message(2);
    CHECK(abox_schedule_ipc(&dev, &data, 0, &msg, sizeof(msg), true, false) == 0,
          "matching trigger gets local queue correlation");
    CHECK(abox_ipc_queue_get(&data, &queued) == 0 && queued.trace_seq == 1,
          "queued matching trigger transfers correlation metadata");
    CHECK(count_abox_pcm_trigger_queue == 1,
          "matching event establishes stale metadata for slot reuse");
    enable_abox_pcm_trigger_queue = false;
    enable_abox_pcm_trigger_send = false;
    for (index = 0; index < ABOX_IPC_QUEUE_SIZE; ++index) {
        CHECK(abox_ipc_queue_put(&data, &dev, 0, &msg, sizeof(msg), 0) == 0,
              "ring wraps while checking stale slot");
        CHECK(abox_ipc_queue_get(&data, &queued) == 0 && queued.trace_seq == 0,
              "untraced ring entries retain zero correlation");
    }
    CHECK(count_abox_pcm_trigger_queue == 1 && atomic_increment_calls == 1 &&
          clock_calls == 1,
          "disabled and filtered traffic has no observation work");
    CHECK(failures == 0, "filter and stale metadata assertions");
}

static void test_other_playback_type_filter(void)
{
    struct device dev = {0};
    struct abox_data data = {0};
    ABOX_IPC_MSG msg = trigger_message(4);
    reset_observation_counters();
    enable_abox_pcm_trigger_queue = true;
    msg.msg.pcmtask.msgtype = PCM_PLTDAI_POINTER;
    CHECK(abox_schedule_ipc(&dev, &data, 0, &msg, sizeof(msg), true, false) == 0,
          "other playback message queues normally");
    CHECK(count_abox_pcm_trigger_queue == 0 && atomic_increment_calls == 0 &&
          clock_calls == 0 && data.ipc_queue[0].trace_seq == 0,
          "non-trigger playback message excluded from trigger events");
    CHECK(failures == 0, "playback message-type filter assertions");
}

static void test_direct_sync_path(void)
{
    struct device dev = {0};
    struct abox_data data = {0};
    ABOX_IPC_MSG msg = trigger_message(7);
    reset_observation_counters();
    dev.driver_data = &data;
    enable_abox_pcm_trigger_queue = true;
    enable_abox_pcm_trigger_send = true;
    send_result = 0;
    CHECK(abox_request_ipc(&dev, 0, &msg, sizeof(msg), 1, 1) == 0,
          "direct atomic/synchronous result remains sender result");
    CHECK(send_calls == 1 && count_abox_pcm_trigger_queue == 0 &&
          count_abox_pcm_trigger_send == 0 && clock_calls == 0 &&
          atomic_increment_calls == 0,
          "direct path remains outside async queue instrumentation");
    CHECK(failures == 0, "direct synchronous path assertions");
}

static void test_pointer_handler_events(void)
{
    struct device rdma_device = {0};
    struct abox_data abox_data = {0};
    struct abox_dma_data dma = {0};
    ABOX_IPC_MSG msg = {0};
    const unsigned int private_pointer_sentinel = UINT32_C(0xabcdef01);
    reset_observation_counters();
    enable_abox_rdma_pointer_handler = true;
    abox_data.dev_rdma[1] = &rdma_device;
    rdma_device.driver_data = &dma;
    msg.msg.pcmtask.channel_id = 1;
    msg.msg.pcmtask.msgtype = PCM_PLTDAI_POINTER;
    msg.msg.pcmtask.param.pointer = private_pointer_sentinel;
    CHECK(abox_rdma_ipc_handler(0, &abox_data, &msg) == IRQ_HANDLED,
          "valid RDMA pointer selects handler");
    CHECK(dma.pointer == private_pointer_sentinel && period_elapsed_calls == 1,
          "pointer handler behavior/callback selection preserved");
    CHECK(count_abox_rdma_pointer_handler == 1 &&
          last_abox_rdma_pointer_handler.channel == 1 &&
          last_abox_rdma_pointer_handler.msg_type == PCM_PLTDAI_POINTER &&
          last_abox_rdma_pointer_handler.mono_ns == 1100,
          "actual pointer TP_fast_assign is channel/type/time only");
    CHECK(sizeof(struct trace_data_abox_rdma_pointer_handler) ==
          sizeof(u64) + 2 * sizeof(int),
          "pointer event payload has no pointer-value field");

    msg.msg.pcmtask.channel_id = ABOX_RDMA_CHANNELS;
    CHECK(abox_rdma_ipc_handler(0, &abox_data, &msg) == IRQ_NONE,
          "out-of-range RDMA channel rejected");
    msg.msg.pcmtask.channel_id = 1;
    dma.backend = true;
    CHECK(abox_rdma_ipc_handler(0, &abox_data, &msg) == IRQ_HANDLED,
          "backend pointer retains original handled result");
    dma.backend = false;
    msg.msg.pcmtask.msgtype = PCM_PLTDAI_ACK;
    msg.msg.pcmtask.param.trigger = 1;
    CHECK(abox_rdma_ipc_handler(0, &abox_data, &msg) == IRQ_HANDLED,
          "other valid RDMA callback retains behavior");
    CHECK(count_abox_rdma_pointer_handler == 1 && period_elapsed_calls == 1,
          "invalid/backend/non-pointer paths emit no pointer event");
    CHECK(clock_calls == 1,
          "pointer event timestamps only the accepted non-backend pointer");
    CHECK(failures == 0, "pointer handler event assertions");
}

int main(void)
{
    test_disabled_default();
    test_async_queue_send_success();
    test_local_send_failure();
    test_sender_only_trace_selection();
    test_queue_full_retries();
    test_message_filter_and_stale_metadata();
    test_other_playback_type_filter();
    test_direct_sync_path();
    test_pointer_handler_events();
    if (failures) {
        fprintf(stderr, "%d host assertions failed\n", failures);
        return 1;
    }
    puts("PASS: 9 compiled production-path scenarios");
    return 0;
}
"""


def build_harness(source_root: Path, cc: str) -> None:
    fixtures = checked_source(source_root)
    patch_path = Path(__file__).with_name("audio-ipc-observation-fix.patch")
    require(patch_path.is_file(), f"missing patch: {patch_path}")
    patch_bytes = patch_path.read_bytes()

    with tempfile.TemporaryDirectory(prefix="abox-ipc-observation-") as temporary:
        temp_root = Path(temporary)
        for relpath, content in fixtures.items():
            target = temp_root / relpath
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        run(["git", "init", "-q", str(temp_root)])
        run(["git", "-C", str(temp_root), "add", *fixtures.keys()])
        patch_file = temp_root / "observation.patch"
        patch_file.write_bytes(patch_bytes)
        run(["git", "-C", str(temp_root), "apply", "--check", str(patch_file)])
        run(["git", "-C", str(temp_root), "apply", str(patch_file)])

        abox_c = (temp_root / "sound/soc/samsung/abox/abox.c").read_text()
        abox_h = (temp_root / "sound/soc/samsung/abox/abox.h").read_text()
        rdma_c = (temp_root / "sound/soc/samsung/abox/abox_rdma.c").read_text()
        ipc_h = (temp_root / "include/sound/samsung/abox_ipc.h").read_text()
        event_h = temp_root / "include/trace/events/samsung_abox.h"
        event_text = event_h.read_text()

        expected_fields = {
            "abox_pcm_trigger_queue": ["sequence", "mono_ns", "ipc_id", "channel", "msg_type", "result", "attempt", "atomic", "sync"],
            "abox_pcm_trigger_send": ["sequence", "mono_ns", "ipc_id", "channel", "msg_type", "result"],
            "abox_rdma_pointer_handler": ["mono_ns", "channel", "msg_type"],
        }
        for event, fields in expected_fields.items():
            require(f"TRACE_EVENT({event}," in event_text, f"missing trace event {event}")
            trace_match = re.search(
                rf"TRACE_EVENT\({event},(.*?)(?=\nTRACE_EVENT\(|\n#endif)",
                event_text,
                re.DOTALL,
            )
            require(trace_match is not None, f"cannot isolate trace event {event}")
            event_body = trace_match.group(1)
            declared = re.findall(r"__field\([^,]+,\s*(\w+)\)", event_body)
            require(declared == fields, f"unexpected fields for {event}: {declared}")
            printable = event_body.split("TP_printk(", 1)[1]
            for forbidden in ("pointer", "payload", "firmware", "device", "dma"):
                require(forbidden not in printable.lower(), f"sensitive field in {event} print format")

        include_dir = temp_root / "host-include"
        (include_dir / "linux").mkdir(parents=True)
        (include_dir / "trace").mkdir(parents=True)
        (include_dir / "linux/tracepoint.h").write_text("/* tracepoint host shim */\n")
        (include_dir / "trace/define_trace.h").write_text("/* trace definition host shim */\n")

        functions = [
            c_block(abox_c, "__abox_ipc_queue_empty"),
            c_block(abox_c, "__abox_ipc_queue_full"),
            c_block(abox_c, "abox_ipc_queue_put"),
            c_block(abox_c, "abox_ipc_queue_get"),
            c_block(abox_c, "__abox_process_ipc"),
            c_block(abox_c, "abox_process_ipc"),
            c_block(abox_c, "abox_schedule_ipc"),
            c_block(abox_c, "abox_request_ipc"),
            c_block(rdma_c, "abox_rdma_ipc_handler"),
        ]
        require(
            re.search(r"\b(?:kmalloc|kzalloc|kcalloc|kmalloc_array|devm_kzalloc|vmalloc)\s*\(",
                      "\n".join(functions)) is None,
            "instrumented production paths unexpectedly allocate",
        )
        ipc_struct = c_block(abox_h, "abox_ipc", kind="struct")
        queue_size = c_define(abox_h, "ABOX_IPC_QUEUE_SIZE")
        retry_count = c_define(abox_c, "IPC_RETRY")
        seq_decl = re.search(
            r"^static atomic64_t abox_pcm_trigger_trace_seq = ATOMIC64_INIT\(0\);$",
            abox_c,
            re.MULTILINE,
        )
        require(seq_decl is not None, "missing actual production trigger sequence declaration")
        ipc_types = "\n".join(
            [
                c_block(ipc_h, "PCMMSG", kind="enum"),
                c_block(ipc_h, "IPC_ID", kind="enum"),
                c_block(ipc_h, "PCMTASK_HW_PARAMS", kind="struct"),
                c_block(ipc_h, "PCMTASK_SET_BUFFER", kind="struct"),
                c_block(ipc_h, "PCMTASK_HARDWARE", kind="struct"),
                c_block(ipc_h, "IPC_PCMTASK_MSG", kind="struct"),
                """typedef struct ABOX_IPC_MSG {
    enum IPC_ID ipcid;
    int task_id;
    union { struct IPC_PCMTASK_MSG pcmtask; u64 opaque[4]; } msg;
} ABOX_IPC_MSG;""",
            ]
        )
        harness_source = "\n".join(
            [
                HOST_PREAMBLE.replace("/* IPC_TYPES_HERE */", ipc_types),
                '#include "trace/events/samsung_abox.h"',
                r"""
static bool queue_work(void *queue, struct work_struct *work)
{
    (void)queue;
    (void)work;
    ++queue_work_calls;
    if (check_queue_trace_before_work) {
        CHECK(count_abox_pcm_trigger_queue == queue_work_calls,
              "queue trace emitted before queue_work");
    }
    return true;
}
""",
                "#define ABOX_RDMA_CHANNELS 8",
                "#define SZ_128 128",
                queue_size,
                retry_count,
                seq_decl.group(0),
                ipc_struct,
                r"""
struct abox_dma_data {
    bool backend;
    u64 pointer;
    void *substream;
    bool ack_enabled;
    int closed;
};
struct abox_data {
    struct abox_ipc ipc_queue[ABOX_IPC_QUEUE_SIZE];
    size_t ipc_queue_start;
    size_t ipc_queue_end;
    spinlock_t ipc_queue_lock;
    struct work_struct ipc_work;
    void *ipc_workqueue;
    struct device *dev;
    struct device *dev_rdma[ABOX_RDMA_CHANNELS];
};
static bool abox_can_calliope_ipc(struct device *dev, struct abox_data *data)
{ (void)dev; (void)data; return true; }
""",
                *functions,
                HOST_HARNESS,
            ]
        )
        source_file = temp_root / "host-test.c"
        source_file.write_text(harness_source)

        for level in ("-O0", "-O2"):
            binary = temp_root / f"host-test-{level[2:]}"
            command = [
                *shlex.split(cc),
                "-std=c11",
                level,
                "-Wall",
                "-Wextra",
                "-Werror",
                "-Wno-unused-parameter",
                "-Wno-unused-function",
                "-I",
                str(include_dir),
                "-I",
                str(temp_root / "include"),
                str(source_file),
                "-o",
                str(binary),
            ]
            compilation = run(command)
            execution = run([str(binary)])
            print(f"{level}: {execution}")
            if compilation:
                print(compilation)
        return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source-tree",
        type=Path,
        default=Path("/home/corpunum/s22-workers/camera-kernel-build-20260927"),
        help="read-only derived source repository at the pinned HEAD",
    )
    parser.add_argument("--cc", default=os.environ.get("CC", "cc"))
    args = parser.parse_args()
    require(args.source_tree.is_dir(), f"source tree not found: {args.source_tree}")
    build_harness(args.source_tree.resolve(), args.cc)
    print(f"PASS: source fixtures {BASE_COMMIT} from derived HEAD {DERIVED_HEAD}")
    print(f"PASS: bounded to {len(SOURCE_FILES)} files (maximum {MAX_SOURCE_BYTES} bytes each)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
