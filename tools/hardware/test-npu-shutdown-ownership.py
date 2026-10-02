#!/usr/bin/env python3
"""Host-only exact-C regressions for NPU shutdown/recovery ownership.

This does not build the kernel, load a module, or exercise device hardware.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import textwrap
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
OWN_PATCH = ROOT / "tools/hardware/npu-shutdown-ownership-fix.patch"
REF_PATCH = ROOT / "tools/hardware/npu-refcount-transaction-fix.patch"
PM_PATCH = ROOT / "tools/hardware/npu-default-boot-callback-fix.patch"
PINNED_BASE = "4e5c5ad7d950e4de0688b5663965f2075654b2ad"
DERIVED_SOURCE = "3fca50941422439b2019db2e4a3dc1016b2138a1"
SOURCE_URL = "https://raw.githubusercontent.com/LineageOS/android_kernel_samsung_s5e9925"
MAX_FILE_BYTES = 128 * 1024
MAX_TOTAL_BYTES = 1024 * 1024
FETCH_TIMEOUT = 5

DEVICE_H = "drivers/vision/npu/core/npu-device.h"
DEVICE_C = "drivers/vision/npu/core/npu-device.c"
HW_H = "drivers/vision/npu/core/npu-hw-device.h"
HW_C = "drivers/vision/npu/core/npu-hw-device.c"
VERTEX_C = "drivers/vision/npu/core/npu-vertex.c"
CLOCK_C = "drivers/vision/npu/core/npu-clock.c"
PM_H = "include/linux/pm_runtime.h"
CLK_H = "include/linux/clk.h"
SOURCE_FILES = (DEVICE_H, DEVICE_C, HW_H, HW_C, VERTEX_C, CLOCK_C, PM_H, CLK_H)
SOURCE_SHA256 = {
    DEVICE_H: "62028bc7aab88880c7fb7b11344786eab40358e1d9ae916581086ded7b61b314",
    DEVICE_C: "98be21e422ca864cc971dfe6a78e71b292d7691cb625c1f029bf4502100644ab",
    HW_H: "43165437c7b6a4c50599c2677536376ab31579de0f5866c8b76e33ff7813e9c3",
    HW_C: "14617a6f8e5b08e1bb169618daa8544f2680ad6709cb9f3b9730919d4dc8e16f",
    VERTEX_C: "0e130ccedaebab85b2d6e78453a049610abed431c04ab630e4426a0f7aca077a",
    CLOCK_C: "0e3d87104de1667bf8e3c197d2a85a0d61ba725b7290c3ed9ffaff58630672c5",
    PM_H: "8a5982620fd46a59346c9568f9fcf57790509d81421b610000dd09a25c0daac1",
    CLK_H: "8946bdd3c492f0204d3b6b051e4a9f3690a0f0741894758d3081e8f92cd332ea",
}


class SourceFixtureUnavailable(Exception):
    pass


def check(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def selected_source(source: str, secure_mode: bool = True) -> str:
    enabled = {"CONFIG_NPU_USE_BOOT_IOCTL", "CONFIG_NPU_USE_HW_DEVICE"}
    if secure_mode:
        enabled.add("CONFIG_NPU_SECURE_MODE")
    output: list[str] = []
    stack: list[tuple[bool, bool, bool]] = []
    active = True
    for line in source.splitlines(keepends=True):
        directive = line.strip()
        if directive.startswith("#ifdef ") or directive.startswith("#ifndef "):
            name = directive.split()[1]
            condition = name in enabled
            if directive.startswith("#ifndef "):
                condition = not condition
            stack.append((active, condition, False))
            active = active and condition
        elif directive.startswith("#if "):
            expression = directive[4:].strip()
            if expression in {"0", "0L"}:
                condition = False
                stack.append((active, condition, False))
                active = False
                continue
            match = re.fullmatch(r"!?defined\((\w+)\)", expression)
            if match:
                condition = match.group(1) in enabled
                if expression.startswith("!"):
                    condition = not condition
            else:
                # Keep other feature-selected code in the host extraction. The
                # ownership paths under test use the explicit CONFIG guards above.
                condition = True
            stack.append((active, condition, False))
            active = active and condition
        elif directive == "#else" and stack:
            parent, condition, _ = stack[-1]
            stack[-1] = (parent, condition, True)
            active = parent and not condition
        elif directive.startswith("#elif ") and stack:
            parent, _, _ = stack[-1]
            stack[-1] = (parent, False, True)
            active = parent
        elif directive == "#endif" and stack:
            parent, _, _ = stack.pop()
            active = parent
        elif active and not directive.startswith(("#if", "#else", "#endif")):
            output.append(line)
    return "".join(output)


def function_body(source: str, marker: str, secure_mode: bool = True) -> str:
    source = selected_source(source, secure_mode)
    start = source.find(marker)
    while start >= 0:
        brace = source.find("{", start)
        semicolon = source.find(";", start)
        if semicolon >= 0 and (brace < 0 or semicolon < brace):
            start = source.find(marker, semicolon + 1)
            continue
        if brace < 0:
            return ""
        depth = 0
        for end in range(brace, len(source)):
            if source[end] == "{":
                depth += 1
            elif source[end] == "}":
                depth -= 1
                if depth == 0:
                    return source[start:end + 1]
        return ""
    return ""


def git_output(source_root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(source_root), *args], capture_output=True,
        text=True, check=False, timeout=10,
    )
    check(result.returncode == 0,
          f"git {' '.join(args)} failed for source fixture: {result.stderr}")
    return result.stdout.strip()


def verify_hash(relative: str, data: bytes) -> None:
    actual = hashlib.sha256(data).hexdigest()
    check(actual == SOURCE_SHA256[relative],
          f"pinned fixture hash mismatch for {relative}: {actual}")


def fetch_public_source(relative: str) -> bytes:
    url = f"{SOURCE_URL}/{PINNED_BASE}/{relative}"
    request = urllib.request.Request(
        url, headers={"User-Agent": "S22-NPU-shutdown-host-test/1"})
    try:
        with urllib.request.urlopen(request, timeout=FETCH_TIMEOUT) as response:
            check(response.geturl() == url,
                  f"pinned fixture redirected unexpectedly: {response.geturl()}")
            length = response.headers.get("Content-Length")
            if length is not None:
                check(int(length) <= MAX_FILE_BYTES,
                      f"pinned fixture too large: {relative}")
            data = response.read(MAX_FILE_BYTES + 1)
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise SourceFixtureUnavailable(str(error)) from error
    check(len(data) <= MAX_FILE_BYTES, f"pinned fixture too large: {relative}")
    verify_hash(relative, data)
    return data


def load_sources() -> tuple[dict[str, bytes], str]:
    configured = os.environ.get("S22_NPU_SHUTDOWN_SOURCE_TREE")
    if configured:
        source_root = Path(configured).expanduser()
        check(source_root.is_dir(), f"configured source tree missing: {source_root}")
        actual = git_output(source_root, "rev-parse", "HEAD")
        check(actual == DERIVED_SOURCE,
              f"expected source HEAD {DERIVED_SOURCE}, found {actual}")
        check(not git_output(source_root, "status", "--porcelain"),
              "configured pinned source tree must be clean")
        delta = subprocess.run(
            ["git", "-C", str(source_root), "diff", "--quiet", PINNED_BASE,
             DERIVED_SOURCE, "--", *SOURCE_FILES],
            capture_output=True, text=True, check=False, timeout=10,
        )
        check(delta.returncode == 0,
              "driver fixture differs between pinned and exact derived source")
        sources = {relative: (source_root / relative).read_bytes()
                   for relative in SOURCE_FILES}
        for relative, data in sources.items():
            verify_hash(relative, data)
        return sources, f"local derived {DERIVED_SOURCE}; NPU source equals {PINNED_BASE}"

    try:
        sources = {relative: fetch_public_source(relative)
                   for relative in SOURCE_FILES}
    except SourceFixtureUnavailable as error:
        raise SourceFixtureUnavailable(f"public pinned fixture unavailable: {error}") from error
    total = sum(map(len, sources.values()))
    check(total <= MAX_TOTAL_BYTES, f"aggregate fixture exceeds bound: {total}")
    return sources, f"public pinned source {PINNED_BASE}; {total} bounded bytes"


def write_sources(directory: Path, sources: dict[str, bytes]) -> None:
    for relative, data in sources.items():
        destination = directory / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)


def apply_patch(directory: Path, patch: Path, label: str) -> None:
    checked = subprocess.run(
        ["git", "apply", "--check", str(patch)], cwd=directory,
        capture_output=True, text=True, check=False, timeout=15,
    )
    check(checked.returncode == 0,
          f"{label} git-apply --check failed:\n{checked.stderr}")
    applied = subprocess.run(
        ["git", "apply", str(patch)], cwd=directory,
        capture_output=True, text=True, check=False, timeout=15,
    )
    check(applied.returncode == 0,
          f"{label} plain git apply failed:\n{applied.stderr}")


def extract_functions(sources: dict[str, bytes]) -> str:
    requested = (
        (VERTEX_C, "static inline int check_emergency("),
        (VERTEX_C, "static inline int check_emergency_vctx("),
        (DEVICE_C, "int npu_device_recovery_close("),
        (DEVICE_C, "int npu_device_shutdown("),
        (HW_C, "int npu_hwdev_recovery_shutdown("),
        (VERTEX_C, "int npu_hwdev_secure_bootup("),
        (VERTEX_C, "int npu_hwdev_secure_bootdown("),
        (VERTEX_C, "int npu_hwdev_normal_bootdown("),
        (VERTEX_C, "static int npu_vertex_bootup("),
        (VERTEX_C, "static inline int __vref_put("),
        (VERTEX_C, "static int __vref_shutdown("),
        (VERTEX_C, "static int __npu_vertex_bootup(", False),
        (VERTEX_C, "static int npu_vertex_open("),
        (VERTEX_C, "static int npu_vertex_close(", False),
    )
    parts = []
    for request in requested:
        relative, marker = request[:2]
        secure_mode = request[2] if len(request) == 3 else True
        source = sources[relative].decode("utf-8")
        body = function_body(source, marker, secure_mode)
        check(body, f"exact source missing function {marker} in {relative}")
        parts.append(body)
    return "\n\n".join(parts)


PRELUDE = r"""
#include <errno.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdbool.h>

#define unlikely(x) (x)
#define ERESTARTSYS 512
#define u32 uint32_t
#define CONFIG_NPU_USE_BOOT_IOCTL 1
#define CONFIG_NPU_USE_HW_DEVICE 1
#define NPU_DEVICE_ERR_STATE_EMERGENCY 0
#define NPU_DEVICE_ERR_STATE_SHUTDOWN_UNCERTAIN 1
#define NPU_DEVICE_STATE_OPEN 0
#define NPU_VERTEX_OPEN 0
#define NPU_VERTEX_POWER 1
#define NPU_VERTEX_GRAPH 2
#define NPU_VERTEX_FORMAT 3
#define NPU_VERTEX_STREAMOFF 4
#define NPU_VERTEX_CLOSE 5
#define NPU_HWDEV_STATUS_ERROR 7
#define NPU_HWDEV_STATUS_PWR_CLK_OFF 0
#define NPU_HWDEV_ID_NPU 1
#define NPU_HWDEV_ID_DSP 2
#define NPU_HWDEV_ID_DNC 4
#define NPU_CRITICAL_DRIVER(code) (-2000)
#define NPU_ERR_IN_EMERGENCY 1
#define NPU_SCHEDULER_BOOST_TIMEOUT 1
#define NPU_PBHA_HINT_00 0
#define GFP_KERNEL 0
#define MASK_BIT_UP_DOWN 0x1
#define MASK_BIT_SECURE 0x2
#define BOOT_UP 1
#define BOOT_DOWN 0
#define SECURE 2
#define NON_SECURE 0
#define BIT(n) (1UL << (n))
#define container_of(ptr, type, member) \
    ((type *)((char *)(ptr) - offsetof(type, member)))
#define npu_info(...) ((void)0)
#define npu_err(...) ((void)0)
#define npu_warn(...) ((void)0)
#define npu_dbg(...) ((void)0)
#define npu_ierr(...) ((void)0)
#define npu_iinfo(...) ((void)0)
#define profile_point1(...) ((void)0)
#define BUG_ON(x) do { if (x) abort(); } while (0)

typedef struct { int counter; } atomic_t;
enum npu_vertex_state { NPU_STATE_OPEN, NPU_STATE_POWER, NPU_STATE_GRAPH,
                        NPU_STATE_FORMAT, NPU_STATE_STREAMOFF, NPU_STATE_CLOSE };
struct mutex { int locked; };
struct npu_device;
struct npu_hw_device;
struct npu_memory_buffer { size_t size; uint64_t daddr; };
struct npu_memory { int unused; };
struct npu_hw_refcount { atomic_t refcount; };
struct npu_hw_device {
    const char *name;
    int id;
    int status;
    struct npu_hw_refcount init_cnt;
    struct npu_hw_refcount boot_cnt;
    struct npu_device *device;
};
struct npu_vertex_refcount {
    atomic_t refcount;
    struct npu_vertex *vertex;
    int (*final)(struct npu_vertex *vertex);
};
struct npu_vertex {
    struct mutex lock;
    int normal_count;
    int secure_count;
    struct npu_vertex_refcount open_cnt;
    struct npu_vertex_refcount boot_cnt;
};
struct npu_system {
    struct npu_memory memory;
    int max_npu_core;
    void *dhcp;
    struct { int warm_boot_enable; } *mbox_hdr;
};
struct npu_sessionmgr { int unused; };
struct npu_device {
    struct npu_vertex vertex;
    unsigned long err_state;
    unsigned long state;
    int is_secure;
    int active_non_secure_sessions;
    struct npu_system system;
    struct npu_sessionmgr sessionmgr;
};
struct npu_vertex_ctx {
    int id;
    unsigned int state;
    struct npu_vertex *vertex;
    struct mutex lock;
    struct { int unused; } queue;
};
struct npu_session {
    struct npu_vertex_ctx vctx;
    int hids;
    struct npu_memory *memory;
    struct npu_memory_buffer *sec_mem_buf;
    struct { int max_npu_core; } sched_param;
    int ss_state;
    int uid;
};
struct vs4l_ctrl {
    unsigned int ctrl;
    unsigned int value;
    size_t mem_size;
    unsigned int mem_addr;
    unsigned int mem_addr_h;
};
struct npu_scheduler_info { int unused; };
struct device { void *driver_data; };
struct file { void *private_data; struct device dev; };
struct test_state {
    int sequence;
    int events[128];
    int lock_acquires, lock_releases, lock_depth;
    int suspend_calls, suspend_ret;
    int log_close_calls, debug_close_calls;
    int hw_put_calls, hw_put_fail_at, hw_put_fail_ret;
    int hw_shutdown_calls, hw_shutdown_ret, hw_bootup_calls, hw_bootup_ret;
    int session_close_calls, session_open_calls, session_undo_open_calls;
    int unreg_hw_calls, reg_hw_calls, power_notify_calls, power_notify_ret;
    int suspend_session_calls, suspend_session_ret, soc_suspend_calls;
    int memory_alloc_ret, memory_free_calls, kfree_calls;
    int open_get_calls, boot_get_calls, open_put_calls, boot_put_calls;
    int emergency_after_open_get, emergency_after_boot_get;
    int queue_open_calls, shutdown_order;
};

static struct test_state T;
static struct npu_device D;
static struct npu_session S;
static struct npu_hw_device H;
static struct npu_hw_device *g_hwdev_list[3];
static struct npu_hw_device HA, HB, HDNC;
static int g_hwdev_num;
static void *configs[1];
static struct npu_scheduler_info scheduler_info;
static int secure_normal_bootup_ret;
int npu_device_shutdown(struct npu_device *device);
static int __vref_shutdown(struct npu_vertex *vertex);

static void event(int value) {
    if (T.sequence < (int)(sizeof(T.events) / sizeof(T.events[0])))
        T.events[T.sequence++] = value;
}
static void expect(int ok, const char *what) {
    if (!ok) { fprintf(stderr, "FAIL %s\n", what); exit(2); }
}
static int test_bit(int bit, const unsigned long *value) {
    return !!(*value & (1UL << bit));
}
static void set_bit(int bit, unsigned long *value) { *value |= 1UL << bit; }
static void clear_bit(int bit, unsigned long *value) { *value &= ~(1UL << bit); }
static int atomic_read(const atomic_t *value) { return value->counter; }
static void atomic_set(atomic_t *value, int next) { value->counter = next; }
static int atomic_dec_return(atomic_t *value) {
    value->counter--;
    if (value == &D.vertex.boot_cnt.refcount) {
        T.boot_put_calls++; event(50);
    } else if (value == &D.vertex.open_cnt.refcount) {
        T.open_put_calls++; event(80);
    }
    return value->counter;
}
static int atomic_xchg(atomic_t *value, int next) {
    int old = value->counter; value->counter = next; return old;
}
static int mutex_lock_interruptible(struct mutex *lock) {
    (void)lock; T.lock_acquires++; T.lock_depth++; return 0;
}
static void mutex_lock(struct mutex *lock) {
    (void)lock; T.lock_acquires++; T.lock_depth++;
}
static void mutex_unlock(struct mutex *lock) {
    (void)lock; T.lock_releases++; T.lock_depth--;
}
static void mutex_init(struct mutex *lock) { lock->locked = 0; }
static void npu_device_set_emergency_err(struct npu_device *device) {
    set_bit(NPU_DEVICE_ERR_STATE_EMERGENCY, &device->err_state);
}
static int npu_device_is_emergency_err(struct npu_device *device) {
    return test_bit(NPU_DEVICE_ERR_STATE_EMERGENCY, &device->err_state);
}
static int test_open_final(struct npu_vertex *vertex) { (void)vertex; return 0; }
static int npu_system_suspend(struct npu_system *system) {
    (void)system; T.suspend_calls++; event(10); return T.suspend_ret;
}
static int __npu_device_early_close(struct npu_device *device) {
    (void)device; event(5); return 0;
}
static int proto_drv_close(struct npu_device *device) {
    (void)device; event(6); return 0;
}
static void dsp_dhcp_deinit(void *dhcp) { (void)dhcp; event(7); }
static int npu_log_close(struct npu_device *device) {
    (void)device; T.log_close_calls++; event(30); return 0;
}
static int npu_debug_close(struct npu_device *device) {
    (void)device; T.debug_close_calls++; event(40); return 0;
}
static int npu_hw_ref_put(struct npu_device *device, struct npu_hw_refcount *ref) {
    (void)device; T.hw_put_calls++; event(20);
    if (T.hw_put_fail_at == T.hw_put_calls) return T.hw_put_fail_ret;
    if (ref->refcount.counter <= 0) return -EINVAL;
    ref->refcount.counter--;
    struct npu_hw_device *known[] = { &H, &HA, &HB, &HDNC };
    for (size_t i = 0; i < sizeof(known) / sizeof(known[0]); i++)
        if (ref == &known[i]->boot_cnt && ref->refcount.counter == 0)
            known[i]->status = NPU_HWDEV_STATUS_PWR_CLK_OFF;
    return 0;
}
static int npu_hwdev_shutdown(struct npu_device *device, unsigned int hids) {
    (void)device; (void)hids; T.hw_shutdown_calls++; event(60);
    return T.hw_shutdown_ret;
}
static int npu_hwdev_bootup(struct npu_device *device, unsigned int hids) {
    (void)device; (void)hids; T.hw_bootup_calls++; event(15);
    return T.hw_bootup_ret;
}
static int npu_sessionmgr_unregHW(struct npu_session *session) {
    (void)session; T.unreg_hw_calls++; event(1); return 0;
}
static int npu_sessionmgr_regHW(struct npu_session *session) {
    (void)session; T.reg_hw_calls++; return 0;
}
static int npu_session_NW_CMD_POWER_NOTIFY(struct npu_session *session, int on) {
    (void)session; (void)on; T.power_notify_calls++; event(2);
    return T.power_notify_ret;
}
static int npu_session_NW_CMD_SUSPEND(struct npu_session *session) {
    (void)session; T.suspend_session_calls++; event(3);
    return T.suspend_session_ret;
}
static int npu_session_NW_CMD_UNLOAD(struct npu_session *session) {
    (void)session; return 0;
}
static int npu_session_close(struct npu_session *session) {
    (void)session; T.session_close_calls++; event(70); return 0;
}
static void npu_system_soc_suspend(struct npu_system *system) {
    (void)system; T.soc_suspend_calls++; event(4);
}
static void npu_memory_free(struct npu_memory *memory, struct npu_memory_buffer *buffer) {
    (void)memory; (void)buffer; T.memory_free_calls++; event(50);
}
static int npu_memory_alloc_secure(struct npu_memory *memory,
                                   struct npu_memory_buffer *buffer, void *config) {
    (void)memory; (void)buffer; (void)config; return T.memory_alloc_ret;
}
static void *kzalloc(size_t size, int flags) { (void)flags; return calloc(1, size); }
static void kfree(void *pointer) { if (pointer) T.kfree_calls++; }
static int check_done_state(unsigned int state) { (void)state; return NPU_VERTEX_OPEN; }
static int __force_streamoff(struct file *file) { (void)file; return 0; }
static int npu_session_open(struct npu_session **session,
                            struct npu_sessionmgr *mgr, struct npu_memory *memory) {
    (void)mgr; T.session_open_calls++; S.memory = memory; *session = &S; return 0;
}
static int npu_session_undo_open(struct npu_session *session) {
    (void)session; T.session_undo_open_calls++; return 0;
}
static int npu_queue_open(void *queue, struct npu_memory *memory, struct mutex *lock) {
    (void)queue; (void)memory; (void)lock; T.queue_open_calls++; return 0;
}
static void *dev_get_drvdata(struct device *device) { return device->driver_data; }
#define vision_devdata(file) (file)
static struct npu_scheduler_info *npu_scheduler_get_info(void) { return &scheduler_info; }
static void npu_scheduler_boost_off_timeout(struct npu_scheduler_info *info, int timeout) {
    (void)info; (void)timeout;
}
static int __vref_get(struct npu_vertex_refcount *ref) {
    ref->refcount.counter++;
    if (ref == &D.vertex.open_cnt) {
        T.open_get_calls++;
        if (T.emergency_after_open_get) npu_device_set_emergency_err(&D);
    } else {
        T.boot_get_calls++;
        if (T.emergency_after_boot_get) npu_device_set_emergency_err(&D);
    }
    return 0;
}
static int npu_hwdev_normal_bootup(struct npu_device *device,
                                   struct npu_vertex_ctx *vctx,
                                   struct vs4l_ctrl *ctrl) {
    (void)device; (void)vctx; (void)ctrl; return secure_normal_bootup_ret;
}
static int npu_session_NW_CMD_RESUME(struct npu_session *session) { (void)session; return 0; }
static int npu_session_restore_cnt(struct npu_session *session) { (void)session; return 0; }
static int npu_session_restart(void) { return 0; }
static void mdelay(unsigned int milliseconds) { (void)milliseconds; }
static int npu_stm_enable(struct npu_system *system, unsigned int id) { (void)system; (void)id; return 0; }
static int npu_hwdev_hwacg(struct npu_system *system, unsigned int id, int on) { (void)system; (void)id; (void)on; return 0; }
static struct npu_hw_device *npu_get_hdev_by_id(int id) { (void)id; return &H; }

/* Prototypes needed by the exact driver bodies below. */
int npu_device_recovery_close(struct npu_device *device);
int npu_hwdev_recovery_shutdown(struct npu_device *device);
static inline int __vref_put(struct npu_vertex_refcount *ref);
static int __vref_shutdown(struct npu_vertex *vertex);
int npu_hwdev_secure_bootup(struct npu_device *, struct npu_vertex_ctx *, struct vs4l_ctrl *);
int npu_hwdev_secure_bootdown(struct npu_device *, struct npu_vertex_ctx *, struct vs4l_ctrl *);
int npu_hwdev_normal_bootdown(struct npu_device *, struct npu_vertex_ctx *, struct vs4l_ctrl *);
static int npu_vertex_bootup(struct file *, struct vs4l_ctrl *);
static int npu_vertex_open(struct file *);
static int npu_vertex_close(struct file *);
static int __npu_vertex_bootup(struct file *, struct vs4l_ctrl *);

static void reset_fixture(void) {
    memset(&T, 0, sizeof(T)); memset(&D, 0, sizeof(D));
    memset(&S, 0, sizeof(S)); memset(&H, 0, sizeof(H));
    memset(&HA, 0, sizeof(HA)); memset(&HB, 0, sizeof(HB));
    memset(&HDNC, 0, sizeof(HDNC));
    memset(g_hwdev_list, 0, sizeof(g_hwdev_list)); g_hwdev_num = 0;
    D.system.mbox_hdr = (void *)calloc(1, sizeof(*D.system.mbox_hdr));
    D.vertex.open_cnt.refcount.counter = 1;
    D.vertex.boot_cnt.refcount.counter = 1;
    D.vertex.open_cnt.vertex = &D.vertex;
    D.vertex.open_cnt.final = test_open_final;
    D.vertex.boot_cnt.vertex = &D.vertex;
    D.vertex.boot_cnt.final = __vref_shutdown;
    D.state |= BIT(NPU_DEVICE_STATE_OPEN);
    S.vctx.vertex = &D.vertex; S.vctx.id = 4; S.hids = NPU_HWDEV_ID_NPU;
    S.memory = &D.system.memory;
    H.name = "NPU"; H.id = NPU_HWDEV_ID_NPU; H.device = &D;
    secure_normal_bootup_ret = 0; T.shutdown_order = 0;
}
static void release_fixture(void) { free(D.system.mbox_hdr); D.system.mbox_hdr = NULL; }

/* EXACT_DRIVER_FUNCTIONS */

static void test_recovery_error_and_retry(void) {
    reset_fixture();
    D.state |= BIT(NPU_DEVICE_STATE_OPEN);
    T.suspend_ret = -EIO;
    expect(npu_device_recovery_close(&D) == -EIO, "recovery error propagated");
    expect(T.suspend_calls == 1 && T.hw_put_calls == 0,
           "recovery stops after first failing suspend");
    expect(T.log_close_calls == 0 && T.debug_close_calls == 0,
           "recovery does not close later resources after failure");
    expect(test_bit(NPU_DEVICE_ERR_STATE_SHUTDOWN_UNCERTAIN, &D.err_state),
           "recovery failure latches uncertainty");
    expect(test_bit(NPU_DEVICE_STATE_OPEN, &D.state),
           "recovery failure retains open device state");
    expect(npu_device_recovery_close(&D) == -EALREADY,
           "recovery retry is refused");
    expect(T.suspend_calls == 1 && T.hw_put_calls == 0,
           "recovery retry performs no teardown");
    release_fixture();
}

static void test_recovery_success(void) {
    reset_fixture(); D.state |= BIT(NPU_DEVICE_STATE_OPEN);
    H.status = 1; atomic_set(&H.init_cnt.refcount, 1);
    atomic_set(&H.boot_cnt.refcount, 1);
    g_hwdev_list[0] = &H; g_hwdev_num = 1;
    expect(npu_device_recovery_close(&D) == 0, "ordinary recovery succeeds");
    expect(T.suspend_calls == 1 && T.hw_put_calls == 2 &&
           T.log_close_calls == 1 && T.debug_close_calls == 1,
           "ordinary recovery runs each stage once");
    expect(!test_bit(NPU_DEVICE_STATE_OPEN, &D.state),
           "ordinary recovery closes open-state marker");
    release_fixture();
}

static void test_recovery_unequal_multileaf_and_dnc(void) {
    reset_fixture();
    HA.name = "A"; HA.status = 1; HA.device = &D;
    HB.name = "B"; HB.status = 1; HB.device = &D;
    HDNC.name = "DNC"; HDNC.status = NPU_HWDEV_STATUS_ERROR; HDNC.device = &D;
    atomic_set(&HA.init_cnt.refcount, 1); atomic_set(&HA.boot_cnt.refcount, 1);
    atomic_set(&HB.init_cnt.refcount, 2); atomic_set(&HB.boot_cnt.refcount, 2);
    atomic_set(&HDNC.init_cnt.refcount, 0); atomic_set(&HDNC.boot_cnt.refcount, 0);
    g_hwdev_list[0] = &HA; g_hwdev_list[1] = &HB; g_hwdev_list[2] = &HDNC;
    g_hwdev_num = 3;
    expect(npu_hwdev_recovery_shutdown(&D) == 0,
           "unequal multi-leaf refs drain without revisiting a drained leaf");
    expect(T.hw_put_calls == 6 && atomic_read(&HA.init_cnt.refcount) == 0 &&
           atomic_read(&HB.init_cnt.refcount) == 0 &&
           atomic_read(&HA.boot_cnt.refcount) == 0 &&
           atomic_read(&HB.boot_cnt.refcount) == 0,
           "each owned init and boot ref is put exactly once");
    expect(HDNC.status == NPU_HWDEV_STATUS_ERROR &&
           atomic_read(&HDNC.init_cnt.refcount) == 0 &&
           atomic_read(&HDNC.boot_cnt.refcount) == 0,
           "DNC ERROR zero-ref device remains excluded");
    release_fixture();
}

static void test_error_zero_ref(void) {
    reset_fixture(); H.status = NPU_HWDEV_STATUS_ERROR;
    atomic_set(&H.init_cnt.refcount, 0); atomic_set(&H.boot_cnt.refcount, 0);
    g_hwdev_list[0] = &H; g_hwdev_num = 1;
    expect(npu_hwdev_recovery_shutdown(&D) == -EIO,
           "ERROR hardware state returned without inverse callback");
    expect(T.hw_put_calls == 0 && atomic_read(&H.init_cnt.refcount) == 0 &&
           atomic_read(&H.boot_cnt.refcount) == 0,
           "ERROR zero-ref path does not put or underflow");
    release_fixture();

    reset_fixture(); H.status = NPU_HWDEV_STATUS_ERROR;
    atomic_set(&H.init_cnt.refcount, 1); atomic_set(&H.boot_cnt.refcount, 1);
    g_hwdev_list[0] = &H; g_hwdev_num = 1;
    expect(npu_hwdev_recovery_shutdown(&D) == -EIO && T.hw_put_calls == 0,
           "ERROR nonzero-ref path performs no inverse callbacks");
    expect(atomic_read(&H.init_cnt.refcount) == 1 &&
           atomic_read(&H.boot_cnt.refcount) == 1,
           "ERROR nonzero refs remain owned after refusal");
    release_fixture();
}

static void test_final_poison_no_retry(void) {
    reset_fixture(); D.state |= BIT(NPU_DEVICE_STATE_OPEN);
    H.status = 1; atomic_set(&H.init_cnt.refcount, 2);
    atomic_set(&H.boot_cnt.refcount, 1);
    g_hwdev_list[0] = &H; g_hwdev_num = 1;
    T.hw_put_fail_at = 2; T.hw_put_fail_ret = -EIO;
    expect(npu_device_recovery_close(&D) == -EIO,
           "intermediate then final init put failure propagated");
    expect(T.hw_put_calls == 2 && atomic_read(&H.init_cnt.refcount) == 1,
           "failed final put retains the still-owned ref");
    expect(test_bit(NPU_DEVICE_ERR_STATE_SHUTDOWN_UNCERTAIN, &D.err_state),
           "final put failure poisons device teardown");
    expect(npu_device_recovery_close(&D) == -EALREADY && T.hw_put_calls == 2,
           "poisoned recovery does not autoretry final put");
    release_fixture();
}

static void test_recovery_caller_retains_refs(void) {
    struct file file;
    struct vs4l_ctrl ctrl = { .value = NPU_HWDEV_ID_NPU };
    reset_fixture(); memset(&file, 0, sizeof(file));
    file.private_data = &S.vctx; D.state |= BIT(NPU_DEVICE_STATE_OPEN);
    T.emergency_after_boot_get = 1; T.suspend_ret = -EIO;
    expect(__npu_vertex_bootup(&file, &ctrl) == -EIO,
           "boot caller preserves recovery-close error");
    expect(T.boot_get_calls == 1 && T.boot_put_calls == 1 &&
           D.vertex.boot_cnt.refcount.counter == 1,
           "boot caller drops only its transaction ref before failed recovery");
    expect(T.hw_bootup_calls == 1 && T.session_close_calls == 0 &&
           T.lock_depth == 0 && T.lock_acquires == T.lock_releases,
           "boot recovery error leaves session intact and lock balanced");
    expect(T.events[1] == 50 && T.events[2] == 10,
           "protocol boot-ref shutdown precedes hardware recovery shutdown");
    release_fixture();

    reset_fixture(); memset(&file, 0, sizeof(file));
    file.dev.driver_data = &D.vertex;
    T.emergency_after_open_get = 1; T.suspend_ret = -EIO;
    expect(npu_vertex_open(&file) == -EIO,
           "open caller preserves recovery-close error");
    expect(T.open_get_calls == 1 && T.open_put_calls == 1 &&
           D.vertex.open_cnt.refcount.counter == 1,
           "open caller drops only its transaction ref before failed recovery");
    expect(T.session_open_calls == 0 && T.lock_depth == 0 &&
           T.lock_acquires == T.lock_releases,
           "open recovery error creates no session and balances lock");
    expect(T.events[0] == 80 && T.events[1] == 10,
           "open-device ref release precedes recovery shutdown");
    release_fixture();
}

static void test_sticky_quarantine_refuses_new_work(void) {
    struct file file;
    struct vs4l_ctrl ctrl = { .value = NPU_HWDEV_ID_NPU };
    reset_fixture(); memset(&file, 0, sizeof(file));
    file.dev.driver_data = &D.vertex;
    set_bit(NPU_DEVICE_ERR_STATE_SHUTDOWN_UNCERTAIN, &D.err_state);
    npu_device_set_emergency_err(&D);
    expect(npu_vertex_open(&file) == -ELIBACC,
           "sticky uncertainty refuses a zero-open-count new open");
    expect(T.open_get_calls == 0 && T.session_open_calls == 0 &&
           T.hw_bootup_calls == 0 && T.hw_shutdown_calls == 0 &&
           T.session_close_calls == 0 && T.open_put_calls == 0,
           "refused open does not restart, tear down, or free resources");

    file.private_data = &S.vctx; S.vctx.vertex = &D.vertex;
    expect(__npu_vertex_bootup(&file, &ctrl) != 0,
           "sticky uncertainty refuses a later boot operation");
    expect(T.hw_bootup_calls == 0 && T.hw_shutdown_calls == 0 &&
           T.boot_get_calls == 0 && T.boot_put_calls == 0 &&
           T.lock_depth == 0,
           "refused boot does not retry hardware or touch references");
    release_fixture();
}

static void test_close_error_retains_session(void) {
    struct file file;
    reset_fixture(); memset(&file, 0, sizeof(file));
    file.private_data = &S.vctx; S.vctx.state = BIT(NPU_VERTEX_POWER);
    D.vertex.normal_count = 1; T.hw_shutdown_ret = -EIO;
    expect(npu_vertex_close(&file) == -EIO,
           "close propagates hwdev shutdown error");
    expect(T.boot_put_calls == 1 && T.hw_shutdown_calls == 1,
           "close preserves pinned boot-ref then hwdev shutdown order");
    expect(T.session_close_calls == 0 && T.open_put_calls == 0,
           "failed close does not close session or drop open ref");
    expect(test_bit(NPU_DEVICE_ERR_STATE_SHUTDOWN_UNCERTAIN, &D.err_state),
           "close failure latches uncertainty");
    expect(T.lock_depth == 0 && T.lock_acquires == T.lock_releases,
           "failed close balances vertex lock");
    int locks = T.lock_acquires, shutdowns = T.hw_shutdown_calls;
    expect(npu_vertex_close(&file) == -EIO,
           "second close refuses uncertain session");
    expect(T.lock_acquires == locks && T.hw_shutdown_calls == shutdowns &&
           T.session_close_calls == 0 && T.open_put_calls == 0,
           "second close performs no teardown/free");
    release_fixture();
}

static void test_ordinary_close_and_already_down(void) {
    struct file file;
    reset_fixture(); memset(&file, 0, sizeof(file));
    file.private_data = &S.vctx; S.vctx.state = BIT(NPU_VERTEX_POWER);
    D.vertex.normal_count = 1;
    expect(npu_vertex_close(&file) == 0, "ordinary powered close succeeds");
    expect(T.boot_put_calls == 1 && T.hw_shutdown_calls == 1 &&
           T.session_close_calls == 1 && T.open_put_calls == 1,
           "ordinary close runs teardown once");
    expect(T.events[2] == 50 && T.events[3] == 5 && T.events[4] == 6 &&
           T.events[5] == 7 && T.events[6] == 10 && T.events[7] == 60 &&
           T.events[8] == 70,
           "boot-ref protocol teardown precedes hardware-off and session free");
    expect(T.lock_depth == 0 && T.lock_acquires == T.lock_releases,
           "ordinary close balances lock");
    release_fixture();

    reset_fixture(); memset(&file, 0, sizeof(file));
    file.private_data = &S.vctx; S.vctx.state = BIT(NPU_VERTEX_OPEN);
    D.vertex.normal_count = 1;
    expect(npu_vertex_close(&file) == 0, "close after explicit bootdown succeeds");
    expect(T.hw_shutdown_calls == 0 && T.boot_put_calls == 0,
           "already-down close does not repeat hardware teardown");
    release_fixture();
}

static void test_secure_bootup_cleanup_error(void) {
    struct vs4l_ctrl ctrl = { .value = NPU_HWDEV_ID_NPU, .mem_size = 64 };
    reset_fixture(); S.vctx.vertex = &D.vertex; S.memory = &D.system.memory;
    T.memory_alloc_ret = -ENOMEM; T.hw_shutdown_ret = -EIO;
    expect(npu_hwdev_secure_bootup(&D, &S.vctx, &ctrl) == -EIO,
           "secure bootup returns failed cleanup shutdown");
    expect(T.hw_bootup_calls == 1 && T.hw_shutdown_calls == 1 &&
           S.sec_mem_buf != NULL && T.kfree_calls == 0,
           "failed cleanup retains session buffer and hardware owner");
    expect(D.is_secure == 1 && D.vertex.secure_count == 0 &&
           test_bit(NPU_DEVICE_ERR_STATE_SHUTDOWN_UNCERTAIN, &D.err_state),
           "secure cleanup error retains uncertain secure state");
    expect(T.lock_depth == 0 && T.lock_acquires == T.lock_releases,
           "secure bootup cleanup balances lock");
    struct file file = { .private_data = &S.vctx };
    int shutdowns = T.hw_shutdown_calls;
    expect(npu_vertex_close(&file) == -EIO && T.hw_shutdown_calls == shutdowns &&
           T.session_close_calls == 0,
           "close after failed secure cleanup refuses repeat teardown");
    release_fixture();
}

static void test_secure_bootup_failed_resume_no_inverse(void) {
    struct vs4l_ctrl ctrl = { .value = NPU_HWDEV_ID_NPU, .mem_size = 0 };
    reset_fixture(); S.vctx.vertex = &D.vertex; T.hw_bootup_ret = -EIO;
    expect(npu_hwdev_secure_bootup(&D, &S.vctx, &ctrl) == -EIO,
           "failed secure resume is returned");
    expect(T.hw_shutdown_calls == 0 && T.boot_put_calls == 0 &&
           T.lock_depth == 0 && T.lock_acquires == T.lock_releases,
           "failed resume with no committed refs performs no inverse teardown");
    expect(test_bit(NPU_DEVICE_ERR_STATE_SHUTDOWN_UNCERTAIN, &D.err_state) &&
           D.is_secure == 1,
           "failed secure resume retains uncertain state without retry");
    release_fixture();
}

static void test_secure_bootup_success(void) {
    struct vs4l_ctrl ctrl = { .value = NPU_HWDEV_ID_NPU, .mem_size = 0 };
    reset_fixture(); S.vctx.vertex = &D.vertex;
    expect(npu_hwdev_secure_bootup(&D, &S.vctx, &ctrl) == 0,
           "ordinary secure bootup succeeds");
    expect(D.vertex.secure_count == 1 && D.is_secure == 1 &&
           T.hw_bootup_calls == 1 && T.hw_shutdown_calls == 0,
           "ordinary secure bootup retains acquired secure state");
    expect(T.lock_depth == 0 && T.lock_acquires == T.lock_releases,
           "ordinary secure bootup balances lock");
    release_fixture();
}

static void test_secure_bootdown_error_and_wrapper_state(void) {
    struct vs4l_ctrl ctrl = { .ctrl = BOOT_DOWN | SECURE,
                              .value = NPU_HWDEV_ID_NPU };
    reset_fixture(); S.vctx.vertex = &D.vertex;
    S.sec_mem_buf = calloc(1, sizeof(*S.sec_mem_buf));
    D.vertex.secure_count = 1; D.is_secure = 1;
    S.vctx.state = BIT(NPU_VERTEX_POWER); T.hw_shutdown_ret = -EIO;
    struct file file = { .private_data = &S.vctx };
    expect(npu_vertex_bootup(&file, &ctrl) == -EIO,
           "secure wrapper propagates bootdown failure");
    expect(S.vctx.state & BIT(NPU_VERTEX_POWER),
           "failed bootdown keeps vertex powered state");
    expect(D.vertex.secure_count == 1 && D.is_secure == 1 &&
           S.sec_mem_buf != NULL && T.memory_free_calls == 0,
           "failed secure shutdown retains count and secure buffer");
    int shuts = T.hw_shutdown_calls;
    expect(npu_vertex_bootup(&file, &ctrl) != 0 && T.hw_shutdown_calls == shuts,
           "uncertain secure bootdown is not automatically retried");
    expect(T.lock_depth == 0 && T.lock_acquires == T.lock_releases,
           "secure bootdown balances lock");
    release_fixture();

    reset_fixture(); S.vctx.vertex = &D.vertex;
    S.sec_mem_buf = calloc(1, sizeof(*S.sec_mem_buf));
    D.vertex.secure_count = 1; D.is_secure = 1;
    S.vctx.state = BIT(NPU_VERTEX_POWER); T.hw_shutdown_ret = 0;
    expect(npu_vertex_bootup(&file, &ctrl) == 0,
           "ordinary secure bootdown succeeds");
    expect(!(S.vctx.state & BIT(NPU_VERTEX_POWER)) &&
           D.vertex.secure_count == 0 && T.hw_shutdown_calls == 1,
           "successful explicit bootdown clears per-session power state");
    release_fixture();
}

static void test_normal_bootdown_error(void) {
    struct vs4l_ctrl ctrl = { .value = NPU_HWDEV_ID_NPU };
    reset_fixture(); S.vctx.vertex = &D.vertex; D.vertex.normal_count = 1;
    D.vertex.boot_cnt.refcount.counter = 1; D.is_secure = 0;
    T.hw_shutdown_ret = -EIO;
    expect(npu_hwdev_normal_bootdown(&D, &S.vctx, &ctrl) == -EIO,
           "normal bootdown returns hardware shutdown failure");
    expect(T.hw_shutdown_calls == 1 && D.vertex.normal_count == 1 &&
           test_bit(NPU_DEVICE_ERR_STATE_SHUTDOWN_UNCERTAIN, &D.err_state),
           "normal count retained and teardown poisoned");
    expect(T.events[2] == 1 && T.events[3] == 50 && T.events[4] == 5 &&
           T.events[5] == 6 && T.events[6] == 7 && T.events[7] == 10 &&
           T.events[8] == 60,
           "normal boot-ref device shutdown precedes hardware-off callback");
    int shuts = T.hw_shutdown_calls;
    expect(npu_hwdev_normal_bootdown(&D, &S.vctx, &ctrl) != 0 &&
           T.hw_shutdown_calls == shuts,
           "normal shutdown error is not autoretried");
    expect(T.lock_depth == 0 && T.lock_acquires == T.lock_releases,
           "normal bootdown balances lock");
    release_fixture();
}

int main(int argc, char **argv) {
    if (argc > 1 && !strcmp(argv[1], "panic")) {
        reset_fixture(); D.state |= BIT(NPU_DEVICE_STATE_OPEN);
        T.suspend_ret = -EIO;
        return npu_device_recovery_close(&D);
    }
    if (argc > 1 && !strcmp(argv[1], "baseline_open")) {
        struct file file; reset_fixture(); memset(&file, 0, sizeof(file));
        file.dev.driver_data = &D.vertex; T.emergency_after_open_get = 1;
        T.suspend_ret = -EIO;
        return npu_vertex_open(&file);
    }
    if (argc > 1 && !strcmp(argv[1], "baseline_boot_caller")) {
        struct file file; struct vs4l_ctrl ctrl = { .value = NPU_HWDEV_ID_NPU };
        reset_fixture(); memset(&file, 0, sizeof(file));
        file.private_data = &S.vctx; T.emergency_after_boot_get = 1;
        T.suspend_ret = -EIO;
        return __npu_vertex_bootup(&file, &ctrl);
    }
    if (argc > 1 && !strcmp(argv[1], "zero_ref")) {
        reset_fixture(); H.status = NPU_HWDEV_STATUS_ERROR;
        g_hwdev_list[0] = &H; g_hwdev_num = 1;
        atomic_set(&H.init_cnt.refcount, 0);
        int ret = npu_hwdev_recovery_shutdown(&D);
        if (ret == -EINVAL && T.hw_put_calls == 1) {
            puts("BASELINE_FAIL ERROR_zero_ref_attempted_put"); return 1;
        }
        return 2;
    }
    if (argc > 1 && !strcmp(argv[1], "baseline_error_nonzero")) {
        reset_fixture(); H.status = NPU_HWDEV_STATUS_ERROR;
        atomic_set(&H.init_cnt.refcount, 1);
        atomic_set(&H.boot_cnt.refcount, 1);
        g_hwdev_list[0] = &H; g_hwdev_num = 1;
        int ret = npu_hwdev_recovery_shutdown(&D);
        if (ret == 0 && T.hw_put_calls == 2 &&
            atomic_read(&H.init_cnt.refcount) == 0 &&
            atomic_read(&H.boot_cnt.refcount) == 0) {
            puts("BASELINE_FAIL ERROR_status_inverse_callbacks"); return 1;
        }
        return 2;
    }
    if (argc > 1 && !strcmp(argv[1], "baseline_close")) {
        struct file file; reset_fixture(); memset(&file, 0, sizeof(file));
        file.private_data = &S.vctx; S.vctx.state = BIT(NPU_VERTEX_POWER);
        D.vertex.normal_count = 1; T.hw_shutdown_ret = -EIO;
        int ret = npu_vertex_close(&file);
        if (ret == -EIO && T.session_close_calls == 1 && T.open_put_calls == 0) {
            puts("BASELINE_FAIL session_closed_before_shutdown_error"); return 1;
        }
        return 2;
    }
    if (argc > 1 && !strcmp(argv[1], "baseline_normal")) {
        struct vs4l_ctrl ctrl = { .value = NPU_HWDEV_ID_NPU };
        reset_fixture(); S.vctx.vertex = &D.vertex; D.vertex.normal_count = 1;
        T.hw_shutdown_ret = -EIO;
        int ret = npu_hwdev_normal_bootdown(&D, &S.vctx, &ctrl);
        if (ret == 0 && T.hw_shutdown_calls == 1 && D.vertex.normal_count == 0) {
            puts("BASELINE_FAIL normal_bootdown_false_success"); return 1;
        }
        return 2;
    }
    if (argc > 1 && !strcmp(argv[1], "baseline_secure")) {
        struct vs4l_ctrl ctrl = { .value = NPU_HWDEV_ID_NPU, .mem_size = 64 };
        reset_fixture(); S.vctx.vertex = &D.vertex; S.memory = &D.system.memory;
        T.memory_alloc_ret = -ENOMEM; T.hw_shutdown_ret = -EIO;
        int ret = npu_hwdev_secure_bootup(&D, &S.vctx, &ctrl);
        if (ret == -ENOMEM && T.hw_shutdown_calls == 1 &&
            S.sec_mem_buf != NULL && T.kfree_calls == 1 && D.is_secure == 0) {
            puts("BASELINE_FAIL secure_cleanup_shutdown_error_discarded"); return 1;
        }
        return 2;
    }
    if (argc > 1 && !strcmp(argv[1], "baseline_secure_down")) {
        struct vs4l_ctrl ctrl = { .value = NPU_HWDEV_ID_NPU };
        reset_fixture(); S.vctx.vertex = &D.vertex;
        S.sec_mem_buf = calloc(1, sizeof(*S.sec_mem_buf));
        D.vertex.secure_count = 1; D.is_secure = 1; T.hw_shutdown_ret = -EIO;
        int ret = npu_hwdev_secure_bootdown(&D, &S.vctx, &ctrl);
        if (ret == 0 && D.vertex.secure_count == 0 && !S.sec_mem_buf &&
            T.memory_free_calls == 1) {
            puts("BASELINE_FAIL secure_bootdown_false_success_and_free"); return 1;
        }
        return 2;
    }

    test_recovery_error_and_retry();
    test_recovery_success();
    test_recovery_unequal_multileaf_and_dnc();
    test_error_zero_ref();
    test_final_poison_no_retry();
    test_recovery_caller_retains_refs();
    test_sticky_quarantine_refuses_new_work();
    test_close_error_retains_session();
    test_ordinary_close_and_already_down();
    test_secure_bootup_failed_resume_no_inverse();
    test_secure_bootup_success();
    test_secure_bootup_cleanup_error();
    test_secure_bootdown_error_and_wrapper_state();
    test_normal_bootdown_error();
    puts("PASS extracted NPU shutdown/recovery C ownership regressions");
    return 0;
}
"""


def compile_and_run(compiler: str, temp: Path, name: str, functions: str,
                    optimization: str, mode: str | None = None) -> subprocess.CompletedProcess[str]:
    source = temp / f"{name}.c"
    binary = temp / name
    source.write_text(PRELUDE.replace("/* EXACT_DRIVER_FUNCTIONS */", functions) + "\n")
    compiled = subprocess.run(
        [compiler, "-std=gnu11", optimization, "-Wall", "-Wextra", "-Werror",
         "-Wno-unused-function", "-Wno-unused-parameter", "-Wno-unused-variable",
         "-Wno-unused-but-set-variable", "-Wno-unused-label", str(source), "-o", str(binary)],
        capture_output=True, text=True, check=False, timeout=30,
    )
    check(compiled.returncode == 0,
          f"{name} extracted-C compile failed:\n{compiled.stdout}{compiled.stderr}")
    command = [str(binary)] + ([mode] if mode else [])
    return subprocess.run(command, capture_output=True, text=True,
                          check=False, timeout=8)


def check_source_gates(sources: dict[str, bytes]) -> None:
    device = sources[DEVICE_C].decode()
    vertex = sources[VERTEX_C].decode()
    open_body = function_body(device, "int npu_device_open(")
    boot_body = function_body(device, "int npu_device_bootup(")
    recovery = function_body(device, "int npu_device_recovery_close(")
    vertex_open = function_body(vertex, "static int npu_vertex_open(")
    vertex_close = function_body(vertex, "static int npu_vertex_close(", False)
    vertex_boot = function_body(vertex, "static int npu_vertex_bootup(")
    check(open_body.find("NPU_DEVICE_ERR_STATE_SHUTDOWN_UNCERTAIN") >= 0 and
          open_body.find("NPU_DEVICE_ERR_STATE_SHUTDOWN_UNCERTAIN") <
          open_body.find("clear_bit(NPU_DEVICE_ERR_STATE_EMERGENCY"),
          "device open must refuse sticky uncertainty before clearing emergency")
    check(boot_body.find("NPU_DEVICE_ERR_STATE_SHUTDOWN_UNCERTAIN") >= 0 and
          boot_body.find("NPU_DEVICE_ERR_STATE_SHUTDOWN_UNCERTAIN") <
          boot_body.find("npu_system_resume"),
          "device bootup must refuse sticky uncertainty before resume")
    check("BUG_ON(1)" not in recovery and "return ret;" in recovery,
          "recovery-close failure must return rather than panic")
    check(vertex_open.find("NPU_DEVICE_ERR_STATE_SHUTDOWN_UNCERTAIN") >= 0 and
          vertex_open.find("NPU_DEVICE_ERR_STATE_SHUTDOWN_UNCERTAIN") <
          vertex_open.find("__vref_get(&vertex->open_cnt)"),
          "vertex open must gate uncertainty before acquiring a ref")
    check(vertex_close.find("NPU_DEVICE_ERR_STATE_SHUTDOWN_UNCERTAIN") >= 0,
          "vertex close must retain an uncertain session")
    check(vertex_close.find("ret = __vref_put(&vertex->boot_cnt)") <
          vertex_close.find("ret = npu_hwdev_shutdown(device, hids)") <
          vertex_close.find("ret = npu_session_close(session)"),
          "close must preserve boot-ref protocol order and defer session close")
    check("else if (!ret && (ctrl->ctrl & MASK_BIT_UP_DOWN) == BOOT_DOWN)" in vertex_boot,
          "successful explicit bootdown must clear per-session power state")


def check_baseline(name: str, result: subprocess.CompletedProcess[str]) -> None:
    check(result.returncode == 1 and "BASELINE_FAIL" in result.stdout,
          f"baseline flaw not reproduced in {name}: rc={result.returncode}\n"
          f"{result.stdout}{result.stderr}")


def main() -> int:
    try:
        sources, source_label = load_sources()
    except SourceFixtureUnavailable as error:
        print(f"SKIP exact-C NPU shutdown tests: {error}")
        return 77
    compiler = shutil.which("cc") or shutil.which("gcc")
    check(compiler is not None, "host C compiler cc/gcc is required")
    with tempfile.TemporaryDirectory(prefix="npu-shutdown-ownership-") as name:
        temp = Path(name)
        standalone = temp / "standalone"
        stack_base = temp / "stack-base"
        combined = temp / "combined"
        standalone.mkdir(); stack_base.mkdir(); combined.mkdir()
        write_sources(standalone, sources)
        write_sources(stack_base, sources)
        write_sources(combined, sources)
        apply_patch(standalone, OWN_PATCH, "standalone ownership patch")
        apply_patch(stack_base, REF_PATCH, "baseline refcount transaction patch")
        apply_patch(stack_base, PM_PATCH, "baseline PM callback patch")
        apply_patch(combined, REF_PATCH, "refcount transaction patch")
        apply_patch(combined, PM_PATCH, "PM callback patch")
        apply_patch(combined, OWN_PATCH, "combined ownership patch")
        baseline_sources = {path: (stack_base / path).read_bytes()
                            for path in sources}
        patched_sources = {path: (combined / path).read_bytes() for path in sources}
        check_source_gates(patched_sources)
        baseline_functions = extract_functions(baseline_sources)
        patched_functions = extract_functions(patched_sources)

        for optimization in ("-O0", "-O2"):
            # Each baseline reproduction is isolated because recovery-close deliberately BUG_ONs.
            for mode in ("panic",):
                panic = compile_and_run(compiler, temp, f"baseline_panic_{optimization[2:]}",
                                        baseline_functions, optimization, mode)
                check(panic.returncode == -signal.SIGABRT,
                      f"baseline recovery panic not reproduced: rc={panic.returncode}")
            for mode in ("baseline_open", "baseline_boot_caller"):
                caller_panic = compile_and_run(
                    compiler, temp, f"baseline_{mode}_{optimization[2:]}",
                    baseline_functions, optimization, mode)
                check(caller_panic.returncode == -signal.SIGABRT,
                      f"baseline recovery caller panic not reproduced in {mode}: "
                      f"rc={caller_panic.returncode}")
            for mode in ("zero_ref", "baseline_error_nonzero", "baseline_close", "baseline_normal",
                         "baseline_secure", "baseline_secure_down"):
                result = compile_and_run(
                    compiler, temp, f"baseline_{mode}_{optimization[2:]}",
                    baseline_functions, optimization, mode)
                check_baseline(mode, result)

            patched = compile_and_run(compiler, temp, f"patched_{optimization[2:]}",
                                      patched_functions, optimization)
            check(patched.returncode == 0,
                  f"patched extracted-C regressions failed: {patched.stdout}{patched.stderr}")
            print(patched.stdout, end="")

        print(f"SOURCE {source_label}")
        print("LIMIT exact extracted driver C with host shims only; no kernel/device evidence")
    return 0


if __name__ == "__main__":
    sys.exit(main())
