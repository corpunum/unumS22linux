#!/usr/bin/env python3
"""Execute pinned NPU refcount C before and after the proposed transaction fix.

The host shims exercise extracted kernel function bodies. They do not execute
Linux, validate hardware, or authorize NPU BOOTUP.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
PATCH = ROOT / "tools/hardware/npu-refcount-transaction-fix.patch"
PINNED_BASE = "4e5c5ad7d950e4de0688b5663965f2075654b2ad"
HEADER = "drivers/vision/npu/core/npu-hw-device.h"
SOURCE = "drivers/vision/npu/core/npu-hw-device.c"
SOURCE_COMMIT = "3fca50941422439b2019db2e4a3dc1016b2138a1"
SOURCE_SHA256 = {
    HEADER: "43165437c7b6a4c50599c2677536376ab31579de0f5866c8b76e33ff7813e9c3",
    SOURCE: "14617a6f8e5b08e1bb169618daa8544f2680ad6709cb9f3b9730919d4dc8e16f",
}
SOURCE_URL = "https://raw.githubusercontent.com/LineageOS/android_kernel_samsung_s5e9925"
MAX_SOURCE_BYTES = 128 * 1024
SOURCE_FETCH_TIMEOUT = 5

PATCHED_FUNCTIONS = (
    (HEADER, "static inline int npu_hw_ref_get("),
    (HEADER, "static inline int npu_hw_ref_put("),
    (HEADER, "static inline int npu_hw_ref_abort_put("),
    (HEADER, "static inline int npu_hw_ref_open("),
    (HEADER, "static inline int npu_hw_ref_close("),
    (HEADER, "static inline int npu_hw_ref_init("),
    (HEADER, "static inline int npu_hw_ref_abort_init("),
    (HEADER, "static inline int npu_hw_ref_deinit("),
    (HEADER, "static inline void npu_hw_ref_setup("),
    (SOURCE, "static int npu_hwdev_npu_init("),
    (SOURCE, "static int npu_hwdev_npu_init_abort("),
    (SOURCE, "static int npu_hwdev_dnc_init_abort("),
    (SOURCE, "static int npu_hwdev_dsp_init_abort("),
    (SOURCE, "static int npu_hwdev_dnc_init("),
    (SOURCE, "static int npu_hwdev_dsp_init("),
    (SOURCE, "int npu_hwdev_bootup("),
    (SOURCE, "int npu_hwdev_shutdown("),
    (SOURCE, "int npu_hwdev_recovery_shutdown("),
)

BASELINE_FUNCTIONS = (
    (HEADER, "static inline int npu_hw_ref_get("),
    (HEADER, "static inline int npu_hw_ref_put("),
    (HEADER, "static inline int npu_hw_ref_init("),
    (SOURCE, "int npu_hwdev_bootup("),
    (SOURCE, "int npu_hwdev_shutdown("),
)


def check(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def function_body(source: str, marker: str) -> str:
    start = source.find(marker)
    while start >= 0:
        brace = source.find("{", start)
        semicolon = source.find(";", start)
        if brace < 0:
            return ""
        if semicolon < 0 or brace < semicolon:
            break
        start = source.find(marker, start + len(marker))
    if start < 0:
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


def git_output(source_root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(source_root), *args],
        capture_output=True, text=True, check=False, timeout=10,
    )
    check(result.returncode == 0,
          f"source git command failed: {' '.join(args)}\n{result.stderr}")
    return result.stdout.strip()


def read_source_bytes(sources: dict[str, bytes],
                      functions: tuple[tuple[str, str], ...]) -> str:
    parts = []
    for relative, marker in functions:
        try:
            source = sources[relative].decode("utf-8")
        except (KeyError, UnicodeError) as error:
            check(False, f"cannot decode pinned source {relative}: {error}")
            raise AssertionError("unreachable")
        body = function_body(source, marker)
        check(body, f"source is missing {marker} in {relative}")
        parts.append(body)
    return "\n\n".join(parts)


class SourceFixtureUnavailable(Exception):
    pass


def verify_source_hash(relative: str, data: bytes) -> None:
    actual = hashlib.sha256(data).hexdigest()
    check(actual == SOURCE_SHA256[relative],
          f"pinned source hash mismatch for {relative}: {actual}")


def fetch_public_source(relative: str) -> bytes:
    url = f"{SOURCE_URL}/{PINNED_BASE}/{relative}"
    request = urllib.request.Request(
        url, headers={"User-Agent": "S22-NPU-refcount-host-test/1"})
    try:
        with urllib.request.urlopen(request, timeout=SOURCE_FETCH_TIMEOUT) as response:
            check(response.geturl() == url,
                  f"pinned source redirected unexpectedly: {response.geturl()}")
            length = response.headers.get("Content-Length")
            if length is not None:
                check(int(length) <= MAX_SOURCE_BYTES,
                      f"pinned source too large: {relative}")
            data = response.read(MAX_SOURCE_BYTES + 1)
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise SourceFixtureUnavailable(f"pinned source fetch unavailable: {error}") from error
    check(len(data) <= MAX_SOURCE_BYTES, f"pinned source too large: {relative}")
    verify_source_hash(relative, data)
    return data


def load_pinned_sources() -> tuple[dict[str, bytes], str]:
    configured_root = os.environ.get("S22_NPU_REFCOUNT_SOURCE_TREE")
    if configured_root:
        source_root = Path(configured_root).expanduser()
        check(source_root.is_dir(),
              f"configured pinned source worktree not found: {source_root}")
        actual = git_output(source_root, "rev-parse", "HEAD")
        check(actual == SOURCE_COMMIT,
              f"expected derived source HEAD {SOURCE_COMMIT}, found {actual}")
        check(not git_output(source_root, "status", "--porcelain"),
              "configured source worktree must be clean")
        npu_delta = subprocess.run(
            ["git", "-C", str(source_root), "diff", "--quiet", PINNED_BASE,
             SOURCE_COMMIT, "--", HEADER, SOURCE],
            capture_output=True, text=True, check=False, timeout=10,
        )
        check(npu_delta.returncode == 0,
              "NPU hwdev files differ between pinned base and derived source HEAD")
        sources = {relative: (source_root / relative).read_bytes()
                   for relative in (HEADER, SOURCE)}
        for relative, data in sources.items():
            verify_source_hash(relative, data)
        return sources, f"verified local derived source {SOURCE_COMMIT} == {PINNED_BASE} NPU files"

    sources = {relative: fetch_public_source(relative)
               for relative in (HEADER, SOURCE)}
    return sources, f"public pinned source {PINNED_BASE}"


def prepare_patched_source(temp: Path, sources: dict[str, bytes]) -> str:
    for relative, data in sources.items():
        destination = temp / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
    checked = subprocess.run(
        ["git", "apply", "--check", str(PATCH)], cwd=temp,
        capture_output=True, text=True, check=False, timeout=10,
    )
    check(checked.returncode == 0,
          "transaction patch does not apply to exact pinned source:\n" + checked.stderr)
    applied = subprocess.run(
        ["git", "apply", str(PATCH)], cwd=temp,
        capture_output=True, text=True, check=False, timeout=10,
    )
    check(applied.returncode == 0,
          "cannot apply patch to temporary source copy:\n" + applied.stderr)
    patched_sources = {relative: (temp / relative).read_bytes()
                       for relative in sources}
    extracted = read_source_bytes(patched_sources, PATCHED_FUNCTIONS)
    for marker in (
        "/* Publish the first owner only after its callback has succeeded. */",
        "if (count <= 0)",
        "hw_ref->failure = ret",
        "npu_hw_ref_abort_put(device, &phdev->init_cnt)",
        "npu_hwdev_dsp_init_abort",
        "dsp_kernel_manager_close(&hdev->device->kmgr, 1)",
        "return ret;",
    ):
        check(marker in extracted, f"patched C extraction omits {marker!r}")
    return extracted


BASELINE_HARNESS = r"""
#define _GNU_SOURCE
#include <errno.h>
#include <limits.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

typedef uint32_t __u32;
typedef struct { atomic_int value; } atomic_t;
struct npu_device;
struct npu_hw_device;
struct npu_hw_refcount {
    atomic_t refcount;
    struct npu_hw_device *hdev;
    int (*first)(struct npu_device *, struct npu_hw_device *);
    int (*final)(struct npu_device *, struct npu_hw_device *);
};
struct npu_hw_ops {
    int (*boot)(struct npu_hw_device *, bool);
    int (*init)(struct npu_hw_device *, bool);
};
struct npu_hw_device {
    char *name;
    char *parent;
    int id;
    struct npu_hw_refcount boot_cnt;
    struct npu_hw_refcount init_cnt;
    struct npu_hw_ops ops;
};
struct npu_device { bool is_secure; };

static struct npu_hw_device *g_hwdev_list[3];
static int g_hwdev_num;
static atomic_int failures;
static atomic_int child_init_calls;
static atomic_int final_calls;
static atomic_int first_calls;
static atomic_int gate_entered;
static atomic_int gate_release;
static atomic_int second_done;

static int atomic_read(const atomic_t *value)
{
    return atomic_load_explicit(&value->value, memory_order_seq_cst);
}
static int atomic_inc_return(atomic_t *value)
{
    return atomic_fetch_add_explicit(&value->value, 1, memory_order_seq_cst) + 1;
}
static int atomic_dec_return(atomic_t *value)
{
    return atomic_fetch_sub_explicit(&value->value, 1, memory_order_seq_cst) - 1;
}
static void atomic_set(atomic_t *value, int next)
{
    atomic_store_explicit(&value->value, next, memory_order_seq_cst);
}

#define BUG_ON(value) do { if (value) abort(); } while (0)
#define npu_info(...) ((void)0)
#define npu_err(...) ((void)0)

static struct npu_hw_device *npu_get_hdev(char *name)
{
    int i;
    for (i = 0; i < g_hwdev_num; i++)
        if (g_hwdev_list[i] && g_hwdev_list[i]->name &&
            !strcmp(g_hwdev_list[i]->name, name))
            return g_hwdev_list[i];
    return NULL;
}

/* Exact baseline helper and caller bodies are inserted here. */
#include "baseline.inc"

static void record_failure(const char *name)
{
    printf("BASELINE_FAIL %s\n", name);
    atomic_fetch_add(&failures, 1);
}

static int fail_first(struct npu_device *device, struct npu_hw_device *hdev)
{
    (void)device;
    (void)hdev;
    return -EIO;
}
static int fail_final(struct npu_device *device, struct npu_hw_device *hdev)
{
    (void)device;
    (void)hdev;
    atomic_fetch_add(&final_calls, 1);
    return -EIO;
}
static int count_child_init(struct npu_hw_device *hdev, bool on)
{
    (void)hdev;
    if (on)
        atomic_fetch_add(&child_init_calls, 1);
    return 0;
}
static int blocking_first(struct npu_device *device, struct npu_hw_device *hdev)
{
    (void)device;
    (void)hdev;
    atomic_fetch_add(&first_calls, 1);
    atomic_store(&gate_entered, 1);
    while (!atomic_load(&gate_release))
        nanosleep(&(struct timespec){ .tv_sec = 0, .tv_nsec = 1000000 }, NULL);
    return -EIO;
}
struct thread_arg {
    struct npu_device *device;
    struct npu_hw_refcount *ref;
    int ret;
    bool second;
};
static void *get_thread(void *opaque)
{
    struct thread_arg *arg = opaque;
    arg->ret = npu_hw_ref_get(arg->device, arg->ref);
    if (arg->second)
        atomic_store(&second_done, 1);
    return NULL;
}

int main(void)
{
    struct npu_device device = {0};
    struct npu_hw_device parent = { .name = "parent", .id = 1 };
    struct npu_hw_device child = { .name = "child", .id = 2, .parent = "parent" };
    struct npu_hw_device only = { .name = "only", .id = 1 };
    struct npu_hw_device concurrent = { .name = "concurrent", .id = 1 };
    pthread_t first_thread, second_thread;
    struct thread_arg first_arg, second_arg;
    int ret;

    /* First callback failure must leave no published reference. */
    concurrent.boot_cnt.refcount.value = ATOMIC_VAR_INIT(0);
    concurrent.boot_cnt.hdev = &concurrent;
    concurrent.boot_cnt.first = fail_first;
    ret = npu_hw_ref_get(&device, &concurrent.boot_cnt);
    if (ret != -EIO || atomic_read(&concurrent.boot_cnt.refcount) != 0)
        record_failure("failed_first_callback_published_count");

    /* Parent init errors must stop before the child init callback. */
    g_hwdev_num = 2;
    g_hwdev_list[0] = &parent;
    g_hwdev_list[1] = &child;
    parent.init_cnt.refcount.value = ATOMIC_VAR_INIT(0);
    parent.init_cnt.hdev = &parent;
    parent.init_cnt.first = fail_first;
    child.ops.init = count_child_init;
    ret = npu_hw_ref_init(&device, &child);
    if (ret != -EIO || atomic_load(&child_init_calls) != 0)
        record_failure("parent_init_error_was_ignored");

    /* Invalid put must refuse underflow. */
    only.init_cnt.refcount.value = ATOMIC_VAR_INIT(0);
    only.init_cnt.hdev = &only;
    only.init_cnt.final = fail_final;
    ret = npu_hw_ref_put(&device, &only.init_cnt);
    if (ret != -EINVAL || atomic_read(&only.init_cnt.refcount) != 0)
        record_failure("invalid_put_underflowed_count");

    /* The shutdown caller must report a final callback error. */
    only.init_cnt.refcount.value = ATOMIC_VAR_INIT(1);
    g_hwdev_num = 1;
    g_hwdev_list[0] = &only;
    ret = npu_hwdev_shutdown(&device, 1);
    if (ret != -EIO)
        record_failure("shutdown_hid_final_callback_error");

    /* A concurrent get must not pass a still-pending first callback. */
    concurrent.boot_cnt.refcount.value = ATOMIC_VAR_INIT(0);
    concurrent.boot_cnt.hdev = &concurrent;
    concurrent.boot_cnt.first = blocking_first;
    atomic_store(&gate_entered, 0);
    atomic_store(&gate_release, 0);
    atomic_store(&second_done, 0);
    first_arg = (struct thread_arg){ &device, &concurrent.boot_cnt, 0, false };
    second_arg = (struct thread_arg){ &device, &concurrent.boot_cnt, 0, true };
    pthread_create(&first_thread, NULL, get_thread, &first_arg);
    while (!atomic_load(&gate_entered))
        nanosleep(&(struct timespec){ .tv_sec = 0, .tv_nsec = 1000000 }, NULL);
    pthread_create(&second_thread, NULL, get_thread, &second_arg);
    for (int i = 0; i < 250 && !atomic_load(&second_done); i++)
        nanosleep(&(struct timespec){ .tv_sec = 0, .tv_nsec = 1000000 }, NULL);
    if (!atomic_load(&second_done))
        record_failure("baseline_second_get_did_not_complete_before_first_callback");
    atomic_store(&gate_release, 1);
    pthread_join(first_thread, NULL);
    pthread_join(second_thread, NULL);
    if (second_arg.ret == 0 && atomic_read(&concurrent.boot_cnt.refcount) == 2)
        printf("BASELINE_REPRO concurrent_get_returned_success_while_first_callback_failed\n");
    else
        record_failure("baseline_concurrency_reproducer_changed");

    if (atomic_load(&failures))
        return 1;
    puts("ERROR baseline unexpectedly satisfies all transaction assertions");
    return 0;
}
"""


PATCHED_HARNESS = r"""
#define _GNU_SOURCE
#include <errno.h>
#include <limits.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#define CONFIG_DSP_USE_VS4L 1
#define NPU_HWDEV_ID_DNC 0x1
#define NPU_HWDEV_ID_NPU 0x2
#define NPU_HWDEV_ID_DSP 0x4
#define NPU_FW_LOAD_SUCCESS 1
#define npu_info(...) ((void)0)
#define npu_err(...) ((void)0)
#define probe_err(...) ((void)0)
#define npu_dbg(...) ((void)0)
#define probe_warn(...) ((void)0)
#define probe_info(...) ((void)0)
#define BUG_ON(value) do { if (value) abort(); } while (0)

typedef uint32_t u32;
typedef uint32_t __u32;
typedef struct { atomic_int value; } atomic_t;
struct mutex { pthread_mutex_t native; atomic_int waiters; };
static void mutex_init(struct mutex *lock)
{
    pthread_mutex_init(&lock->native, NULL);
    atomic_init(&lock->waiters, 0);
}
static void mutex_lock(struct mutex *lock)
{
    int result = pthread_mutex_trylock(&lock->native);
    if (result == 0)
        return;
    if (result != EBUSY)
        abort();
    atomic_fetch_add(&lock->waiters, 1);
    pthread_mutex_lock(&lock->native);
    atomic_fetch_sub(&lock->waiters, 1);
}
static void mutex_unlock(struct mutex *lock)
{
    pthread_mutex_unlock(&lock->native);
}
static int atomic_read(const atomic_t *value)
{
    return atomic_load_explicit(&value->value, memory_order_seq_cst);
}
static void atomic_set(atomic_t *value, int next)
{
    atomic_store_explicit(&value->value, next, memory_order_seq_cst);
}

struct npu_device;
struct npu_hw_device;
struct npu_system { unsigned int fw_load_success; };
struct dsp_kernel_manager { unsigned int dl_init; };
struct npu_hw_refcount {
    atomic_t refcount;
    struct mutex lock;
    int failure;
    struct npu_hw_device *hdev;
    int (*first)(struct npu_device *, struct npu_hw_device *);
    int (*final)(struct npu_device *, struct npu_hw_device *);
    int (*abort)(struct npu_device *, struct npu_hw_device *);
};
struct npu_hw_ops {
    int (*boot)(struct npu_hw_device *, bool);
    int (*init)(struct npu_hw_device *, bool);
    int (*init_abort)(struct npu_hw_device *);
};
struct npu_hw_device {
    char *name;
    char *parent;
    int id;
    unsigned int status;
    struct npu_device *device;
    struct npu_hw_refcount boot_cnt;
    struct npu_hw_refcount init_cnt;
    struct npu_hw_ops ops;
};
struct npu_device {
    bool is_secure;
    struct npu_system system;
    struct dsp_kernel_manager kmgr;
};

static struct npu_hw_device *g_hwdev_list[3];
static int g_hwdev_num;
static int binary_load_result;
static int manager_open_result;
static int manager_open_calls;
static int manager_close_calls;
static int stm_disable_calls;
static int stm_enable_count;
static int stm_disable_result;
static int boot_fail_id;
static int boot_fail_on;
static int boot_calls[3][2];
static int mock_init_on_calls;
static int mock_init_off_calls;
static int partial_boot_side_effects;

static int npu_stm_disable(struct npu_system *system, int hid)
{
    (void)system;
    (void)hid;
    stm_disable_calls++;
    stm_enable_count--;
    return stm_disable_result;
}
static int dsp_system_load_binary(struct npu_system *system)
{
    (void)system;
    return binary_load_result;
}
static int dsp_kernel_manager_open(struct npu_system *system,
        struct dsp_kernel_manager *manager)
{
    (void)system;
    manager_open_calls++;
    if (!manager_open_result)
        manager->dl_init++;
    return manager_open_result;
}
static void dsp_kernel_manager_close(struct dsp_kernel_manager *manager,
        unsigned int count)
{
    manager_close_calls++;
    if (manager->dl_init > count)
        manager->dl_init -= count;
    else
        manager->dl_init = 0;
}
static struct npu_hw_device *npu_get_hdev(char *name)
{
    for (int i = 0; i < g_hwdev_num; i++)
        if (g_hwdev_list[i] && g_hwdev_list[i]->name &&
            !strcmp(g_hwdev_list[i]->name, name))
            return g_hwdev_list[i];
    return NULL;
}

/* Exact patched production function bodies are inserted here. */
#include "patched.inc"

static void expect(bool condition, const char *message)
{
    if (!condition) {
        fprintf(stderr, "FAIL: %s\n", message);
        exit(1);
    }
}
static int test_boot(struct npu_hw_device *hdev, bool on)
{
    int index = hdev->id == NPU_HWDEV_ID_DNC ? 0 :
        hdev->id == NPU_HWDEV_ID_NPU ? 1 : 2;
    boot_calls[index][on ? 1 : 0]++;
    if (on && hdev->id == boot_fail_id) {
        partial_boot_side_effects++;
        return boot_fail_on;
    }
    return 0;
}
static int fail_parent_final_boot(struct npu_hw_device *hdev, bool on)
{
    (void)hdev;
    return on ? 0 : -EREMOTEIO;
}
static int fail_parent_final_init(struct npu_hw_device *hdev, bool on)
{
    (void)hdev;
    return on ? 0 : -EREMOTEIO;
}
static int count_init(struct npu_hw_device *hdev, bool on)
{
    (void)hdev;
    if (on)
        mock_init_on_calls++;
    else
        mock_init_off_calls++;
    return 0;
}
static int fail_final_init(struct npu_hw_device *hdev, bool on)
{
    (void)hdev;
    if (on) {
        mock_init_on_calls++;
        return 0;
    }
    mock_init_off_calls++;
    return -EIO;
}
static int fail_parent_first(struct npu_device *device,
        struct npu_hw_device *hdev)
{
    (void)device;
    (void)hdev;
    return -EREMOTEIO;
}
static void reset_state(void)
{
    binary_load_result = 0;
    manager_open_result = 0;
    manager_open_calls = 0;
    manager_close_calls = 0;
    stm_disable_calls = 0;
    stm_enable_count = 0;
    stm_disable_result = 0;
    boot_fail_id = 0;
    boot_fail_on = 0;
    partial_boot_side_effects = 0;
    memset(boot_calls, 0, sizeof(boot_calls));
    mock_init_on_calls = 0;
    mock_init_off_calls = 0;
}
static void configure_device(struct npu_device *device,
        struct npu_hw_device devices[3])
{
    static char *const names[] = { "DNC", "NPU", "DSP" };
    memset(device, 0, sizeof(*device));
    memset(devices, 0, sizeof(*devices) * 3);
    device->system.fw_load_success = NPU_FW_LOAD_SUCCESS;
    g_hwdev_num = 3;
    for (int i = 0; i < 3; i++) {
        devices[i].name = names[i];
        devices[i].id = 1 << i;
        devices[i].parent = i ? names[0] : NULL;
        devices[i].device = device;
        devices[i].ops.boot = test_boot;
        devices[i].ops.init = i == 0 ? npu_hwdev_dnc_init :
            i == 1 ? npu_hwdev_npu_init : npu_hwdev_dsp_init;
        devices[i].ops.init_abort = i == 0 ? npu_hwdev_dnc_init_abort :
            i == 1 ? npu_hwdev_npu_init_abort : npu_hwdev_dsp_init_abort;
        g_hwdev_list[i] = &devices[i];
        npu_hw_ref_setup(&devices[i].boot_cnt, &devices[i],
            npu_hw_ref_open, npu_hw_ref_close, NULL);
        npu_hw_ref_setup(&devices[i].init_cnt, &devices[i],
            npu_hw_ref_init, npu_hw_ref_deinit, npu_hw_ref_abort_init);
    }
}

struct gate_state {
    pthread_mutex_t lock;
    pthread_cond_t cond;
    bool entered;
    bool released;
    int result;
    int calls;
};
static struct gate_state gate = {
    .lock = PTHREAD_MUTEX_INITIALIZER,
    .cond = PTHREAD_COND_INITIALIZER,
};
static int gated_first(struct npu_device *device, struct npu_hw_device *hdev)
{
    (void)device;
    (void)hdev;
    pthread_mutex_lock(&gate.lock);
    gate.calls++;
    gate.entered = true;
    pthread_cond_broadcast(&gate.cond);
    while (!gate.released)
        pthread_cond_wait(&gate.cond, &gate.lock);
    pthread_mutex_unlock(&gate.lock);
    return gate.result;
}
struct get_arg {
    struct npu_device *device;
    struct npu_hw_refcount *ref;
    int ret;
    atomic_bool done;
};
static void *get_thread(void *opaque)
{
    struct get_arg *arg = opaque;
    arg->ret = npu_hw_ref_get(arg->device, arg->ref);
    atomic_store(&arg->done, true);
    return NULL;
}
static void gate_reset(int result)
{
    pthread_mutex_lock(&gate.lock);
    gate.entered = false;
    gate.released = false;
    gate.result = result;
    gate.calls = 0;
    pthread_mutex_unlock(&gate.lock);
}
static void wait_for_gate_entry(void)
{
    pthread_mutex_lock(&gate.lock);
    while (!gate.entered)
        pthread_cond_wait(&gate.cond, &gate.lock);
    pthread_mutex_unlock(&gate.lock);
}
static void release_gate(void)
{
    pthread_mutex_lock(&gate.lock);
    gate.released = true;
    pthread_cond_broadcast(&gate.cond);
    pthread_mutex_unlock(&gate.lock);
}

static int fail_first_ref_final(struct npu_device *device,
        struct npu_hw_device *hdev);

static void wait_for_contender(struct mutex *lock)
{
    for (int i = 0; i < 1000 && !atomic_load(&lock->waiters); i++)
        nanosleep(&(struct timespec){ .tv_sec = 0, .tv_nsec = 1000000 }, NULL);
}

static void test_concurrent_first_callback_failure(void)
{
    struct npu_device device = {0};
    struct npu_hw_device hdev = { .name = "race", .id = 1 };
    pthread_t first_thread, second_thread;
    struct get_arg first_arg = { .device = &device, .ref = &hdev.boot_cnt };
    struct get_arg second_arg = { .device = &device, .ref = &hdev.boot_cnt };
    reset_state();
    hdev.ops.boot = test_boot;
    hdev.ops.init = count_init;
    npu_hw_ref_setup(&hdev.boot_cnt, &hdev, gated_first, NULL, NULL);
    /* Install a final so get/put follows the production setup contract. */
    hdev.boot_cnt.final = fail_first_ref_final;
    gate_reset(-EIO);
    pthread_create(&first_thread, NULL, get_thread, &first_arg);
    wait_for_gate_entry();
    pthread_create(&second_thread, NULL, get_thread, &second_arg);
    wait_for_contender(&hdev.boot_cnt.lock);
    expect(atomic_load(&hdev.boot_cnt.lock.waiters) > 0,
        "concurrent getter did not serialize on the per-ref lock");
    expect(!atomic_load(&second_arg.done),
        "second get returned while first callback was pending");
    release_gate();
    pthread_join(first_thread, NULL);
    pthread_join(second_thread, NULL);
    expect(first_arg.ret == -EIO && second_arg.ret == -EIO,
        "failed first callback result was not shared truthfully");
    expect(atomic_read(&hdev.boot_cnt.refcount) == 0 &&
        hdev.boot_cnt.failure == -EIO,
        "failed first callback published or lost its failure state");
    expect(gate.calls == 1,
        "serialized first callback ran more than once after failure");
    puts("PASS actual patched C: concurrent get waits for failed first callback");
}

static int fail_first_ref_final(struct npu_device *device,
        struct npu_hw_device *hdev)
{
    (void)device;
    (void)hdev;
    return 0;
}

static void test_concurrent_first_callback_success(void)
{
    struct npu_device device = {0};
    struct npu_hw_device hdev = { .name = "race", .id = 1 };
    pthread_t first_thread, second_thread;
    struct get_arg first_arg = { .device = &device, .ref = &hdev.boot_cnt };
    struct get_arg second_arg = { .device = &device, .ref = &hdev.boot_cnt };
    reset_state();
    hdev.ops.boot = test_boot;
    hdev.ops.init = count_init;
    npu_hw_ref_setup(&hdev.boot_cnt, &hdev, gated_first,
        fail_first_ref_final, NULL);
    gate_reset(0);
    pthread_create(&first_thread, NULL, get_thread, &first_arg);
    wait_for_gate_entry();
    pthread_create(&second_thread, NULL, get_thread, &second_arg);
    wait_for_contender(&hdev.boot_cnt.lock);
    expect(atomic_load(&hdev.boot_cnt.lock.waiters) > 0,
        "concurrent getter did not serialize on successful first callback");
    expect(!atomic_load(&second_arg.done),
        "second get returned while successful first callback was pending");
    release_gate();
    pthread_join(first_thread, NULL);
    pthread_join(second_thread, NULL);
    expect(first_arg.ret == 0 && second_arg.ret == 0,
        "serialized successful gets did not both succeed");
    expect(atomic_read(&hdev.boot_cnt.refcount) == 2 && gate.calls == 1,
        "successful first callback was not published exactly once");
    puts("PASS actual patched C: concurrent success publishes two owners once");
}

static void test_failed_dsp_acquire_and_parent_abort(void)
{
    struct npu_device device;
    struct npu_hw_device devices[3];
    int ret;
    reset_state();
    configure_device(&device, devices);
    manager_open_result = -EIO;
    ret = npu_hwdev_bootup(&device, NPU_HWDEV_ID_DSP);
    expect(ret == -EIO, "DSP init callback error was hidden by bootup");
    expect(atomic_read(&devices[2].init_cnt.refcount) == 0 &&
        devices[2].init_cnt.failure == -EIO,
        "failed DSP first init published an owner");
    expect(atomic_read(&devices[0].init_cnt.refcount) == 0,
        "failed DSP init retained its DNC parent reference");
    expect(manager_open_calls == 1 && manager_close_calls == 0,
        "failed manager open called close without manager ownership");
    expect(stm_disable_calls == 0 && stm_enable_count == 0,
        "failed DSP init used the generic STM-decrementing inverse");
    expect(atomic_read(&devices[2].boot_cnt.refcount) == 0 &&
        atomic_read(&devices[0].boot_cnt.refcount) == 0,
        "failed DSP init did not release matching boot references");
    puts("PASS actual patched C: DSP failure aborts DNC count without STM disable");
}

static void test_multi_ref_rollback_uses_source_abort(void)
{
    struct npu_device device;
    struct npu_hw_device devices[3];
    int ret;
    reset_state();
    configure_device(&device, devices);
    manager_open_result = -EIO;
    ret = npu_hwdev_bootup(&device, NPU_HWDEV_ID_NPU | NPU_HWDEV_ID_DSP);
    expect(ret == -EIO, "multi-ref bootup did not return DSP parent error");
    for (int i = 0; i < 3; i++) {
        expect(atomic_read(&devices[i].init_cnt.refcount) == 0,
            "transaction rollback left an init reference published");
        expect(atomic_read(&devices[i].boot_cnt.refcount) == 0,
            "transaction rollback left a boot reference published");
    }
    expect(stm_disable_calls == 0 && stm_enable_count == 0,
        "transaction rollback invoked STM disable before STM enable");
    expect(manager_close_calls == 0,
        "failed DSP manager open was closed despite having no manager owner");
    puts("PASS actual patched C: selected-ref rollback avoids generic inverse callbacks");
}

static void test_successful_dsp_abort_is_manager_only(void)
{
    struct npu_device device;
    struct npu_hw_device devices[3];
    int ret;
    reset_state();
    configure_device(&device, devices);
    ret = npu_hw_ref_get(&device, &devices[2].init_cnt);
    expect(ret == 0 && device.kmgr.dl_init == 1,
        "DSP init did not acquire the manager fixture");
    ret = npu_hw_ref_abort_put(&device, &devices[2].init_cnt);
    expect(ret == 0, "source-specific DSP init abort failed");
    expect(device.kmgr.dl_init == 0 && manager_close_calls == 1,
        "DSP abort did not close exactly its manager acquisition");
    expect(stm_disable_calls == 0 && stm_enable_count == 0,
        "DSP abort called STM disable without a matching enable");
    expect(atomic_read(&devices[0].init_cnt.refcount) == 0 &&
        atomic_read(&devices[2].init_cnt.refcount) == 0,
        "DSP source abort did not release its parent and child references");
    puts("PASS actual patched C: successful DSP abort closes manager only");
}

static void test_parent_error_propagation(void)
{
    struct npu_device device;
    struct npu_hw_device devices[3];
    int ret;
    reset_state();
    configure_device(&device, devices);
    devices[0].boot_cnt.first = fail_parent_first;
    ret = npu_hw_ref_get(&device, &devices[2].boot_cnt);
    expect(ret == -EREMOTEIO,
        "parent boot callback error was not propagated through child open");
    expect(boot_calls[2][1] == 0,
        "child boot callback ran after parent boot acquisition failed");
    expect(atomic_read(&devices[0].boot_cnt.refcount) == 0 &&
        atomic_read(&devices[2].boot_cnt.refcount) == 0,
        "parent error published a parent or child boot reference");

    reset_state();
    configure_device(&device, devices);
    devices[0].init_cnt.first = fail_parent_first;
    devices[1].ops.init = count_init;
    ret = npu_hw_ref_get(&device, &devices[1].init_cnt);
    expect(ret == -EREMOTEIO,
        "parent init callback error was not propagated through child init");
    expect(mock_init_on_calls == 0,
        "child init callback ran after parent init acquisition failed");
    expect(atomic_read(&devices[0].init_cnt.refcount) == 0 &&
        atomic_read(&devices[1].init_cnt.refcount) == 0,
        "parent init error published parent or child reference");
    puts("PASS actual patched C: boot and init parent errors propagate");
}

static void test_partial_child_boot_failure_retains_parent(void)
{
    struct npu_device device;
    struct npu_hw_device devices[3];
    int ret;
    reset_state();
    configure_device(&device, devices);
    boot_fail_id = NPU_HWDEV_ID_NPU;
    boot_fail_on = -EIO;
    ret = npu_hwdev_bootup(&device, NPU_HWDEV_ID_DNC | NPU_HWDEV_ID_NPU);
    expect(ret == -EIO && partial_boot_side_effects == 1,
        "leaf boot callback error was not returned after its partial side effect");
    expect(atomic_read(&devices[1].boot_cnt.refcount) == 0 &&
        devices[1].boot_cnt.failure == -EIO,
        "failed leaf boot callback was published as a successful owner");
    expect(atomic_read(&devices[0].boot_cnt.refcount) == 1 &&
        devices[0].boot_cnt.failure == 0,
        "rollback released the parent beneath a partially booted child");
    expect(boot_calls[0][0] == 0,
        "bootup unwind powered down the retained parent dependency");
    puts("PASS actual patched C: partial child boot failure retains parent dependency");
}

static void test_parent_final_errors_propagate(void)
{
    struct npu_device device;
    struct npu_hw_device devices[3];
    int ret;
    reset_state();
    configure_device(&device, devices);
    devices[0].ops.boot = fail_parent_final_boot;
    ret = npu_hw_ref_get(&device, &devices[1].boot_cnt);
    expect(ret == 0, "parent boot error fixture acquisition failed");
    ret = npu_hw_ref_put(&device, &devices[1].boot_cnt);
    expect(ret == -EREMOTEIO,
        "child boot close hid its parent's final callback error");
    expect(atomic_read(&devices[1].boot_cnt.refcount) == 1 &&
        devices[1].boot_cnt.failure == -EREMOTEIO &&
        atomic_read(&devices[0].boot_cnt.refcount) == 1 &&
        devices[0].boot_cnt.failure == -EREMOTEIO,
        "parent boot final error did not poison both retained ownership paths");

    reset_state();
    configure_device(&device, devices);
    devices[0].ops.init = fail_parent_final_init;
    stm_enable_count = 1;
    ret = npu_hw_ref_get(&device, &devices[1].init_cnt);
    expect(ret == 0, "parent init error fixture acquisition failed");
    ret = npu_hw_ref_put(&device, &devices[1].init_cnt);
    expect(ret == -EREMOTEIO,
        "child init teardown hid its parent's final callback error");
    expect(atomic_read(&devices[1].init_cnt.refcount) == 1 &&
        devices[1].init_cnt.failure == -EREMOTEIO &&
        atomic_read(&devices[0].init_cnt.refcount) == 1 &&
        devices[0].init_cnt.failure == -EREMOTEIO,
        "parent init final error did not poison both retained ownership paths");
    puts("PASS actual patched C: boot-close and init-deinit propagate parent final errors");
}

static void test_invalid_put_refuses_underflow(void)
{
    struct npu_device device = {0};
    struct npu_hw_device hdev = { .name = "underflow", .id = 1 };
    int ret;
    reset_state();
    hdev.ops.boot = test_boot;
    hdev.ops.init = count_init;
    npu_hw_ref_setup(&hdev.init_cnt, &hdev, npu_hw_ref_init,
        npu_hw_ref_deinit, npu_hw_ref_abort_init);
    ret = npu_hw_ref_put(&device, &hdev.init_cnt);
    expect(ret == -EINVAL && atomic_read(&hdev.init_cnt.refcount) == 0,
        "invalid put underflowed the per-ref count");
    expect(stm_disable_calls == 0,
        "invalid put invoked a final callback");
    puts("PASS actual patched C: invalid put refuses underflow");
}

static void test_final_error_stays_poisoned_and_recovery_stops(void)
{
    struct npu_device device = {0};
    struct npu_hw_device hdev = { .name = "only", .id = 1, .status = 1 };
    int ret;
    reset_state();
    hdev.device = &device;
    hdev.ops.boot = test_boot;
    hdev.ops.init = fail_final_init;
    npu_hw_ref_setup(&hdev.boot_cnt, &hdev, npu_hw_ref_open,
        npu_hw_ref_close, NULL);
    npu_hw_ref_setup(&hdev.init_cnt, &hdev, npu_hw_ref_init,
        npu_hw_ref_deinit, npu_hw_ref_abort_init);
    g_hwdev_num = 1;
    g_hwdev_list[0] = &hdev;

    ret = npu_hw_ref_get(&device, &hdev.init_cnt);
    expect(ret == 0 && atomic_read(&hdev.init_cnt.refcount) == 1,
        "final-error fixture first acquisition failed");
    ret = npu_hwdev_shutdown(&device, 1);
    expect(ret == -EIO,
        "shutdown did not report its final callback error");
    expect(atomic_read(&hdev.init_cnt.refcount) == 1 &&
        hdev.init_cnt.failure == -EIO,
        "failed final callback falsely published the reference as released");
    expect(boot_calls[0][0] == 0,
        "shutdown released boot power after init teardown failed");

    ret = npu_hw_ref_get(&device, &hdev.init_cnt);
    expect(ret == -EIO && mock_init_on_calls == 1,
        "get retried a reference after uncertain final teardown");
    ret = npu_hw_ref_put(&device, &hdev.init_cnt);
    expect(ret == -EIO && mock_init_off_calls == 1,
        "put retried a final callback after uncertain teardown");

    ret = npu_hwdev_recovery_shutdown(&device);
    expect(ret == -EIO && mock_init_off_calls == 1,
        "recovery shutdown spun or retried after a poisoned final callback");
    puts("PASS actual patched C: final error stays owned, blocks retries, stops recovery");
}

static void test_normal_shutdown_and_dnc_abort_are_distinct(void)
{
    struct npu_device device;
    struct npu_hw_device devices[3];
    int ret;
    reset_state();
    configure_device(&device, devices);
    ret = npu_hwdev_bootup(&device, NPU_HWDEV_ID_NPU);
    expect(ret == 0, "NPU bootup failed in host fixture");
    stm_enable_count = 2;
    ret = npu_hwdev_shutdown(&device, NPU_HWDEV_ID_NPU);
    expect(ret == 0 && stm_disable_calls == 2 && stm_enable_count == 0,
        "normal shutdown did not retain the paired STM final callbacks");
    expect(atomic_read(&devices[0].init_cnt.refcount) == 0 &&
        atomic_read(&devices[1].init_cnt.refcount) == 0,
        "normal shutdown left NPU or DNC init counts");

    reset_state();
    configure_device(&device, devices);
    ret = npu_hw_ref_get(&device, &devices[0].init_cnt);
    expect(ret == 0, "DNC init-on no-op failed");
    ret = npu_hw_ref_abort_put(&device, &devices[0].init_cnt);
    expect(ret == 0 && stm_disable_calls == 0,
        "DNC init abort invoked the STM-decrementing final callback");
    puts("PASS actual patched C: normal STM teardown differs from DNC abort");
}

int main(void)
{
    test_concurrent_first_callback_failure();
    test_concurrent_first_callback_success();
    test_failed_dsp_acquire_and_parent_abort();
    test_multi_ref_rollback_uses_source_abort();
    test_successful_dsp_abort_is_manager_only();
    test_parent_error_propagation();
    test_partial_child_boot_failure_retains_parent();
    test_parent_final_errors_propagate();
    test_invalid_put_refuses_underflow();
    test_final_error_stays_poisoned_and_recovery_stops();
    test_normal_shutdown_and_dnc_abort_are_distinct();
    puts("PASS actual patched C: all transactional refcount regressions");
    return 0;
}
"""


def compile_and_run(compiler: str, directory: Path, source_text: str,
                    include_text: str, source_name: str, expected: str,
                    optimization: str) -> str:
    source = directory / source_name
    include = directory / ("baseline.inc" if source_name.startswith("baseline")
                           else "patched.inc")
    binary = directory / source_name.removesuffix(".c")
    source.write_text(textwrap.dedent(source_text))
    include.write_text(include_text)
    compiled = subprocess.run(
        [compiler, "-std=gnu11", optimization, "-Wall", "-Wextra", "-Werror",
         "-Wno-unused-function", "-pthread", "-I", str(directory),
         str(source), "-o", str(binary)],
        capture_output=True, text=True, check=False, timeout=20,
    )
    check(compiled.returncode == 0,
          f"{source_name} did not compile:\n{compiled.stdout}{compiled.stderr}")
    executed = subprocess.run(
        [str(binary)], capture_output=True, text=True, check=False, timeout=8,
    )
    if expected == "baseline-fails":
        check(executed.returncode == 1 and "BASELINE_FAIL" in executed.stdout,
              "baseline assertions did not fail against pinned source:\n" +
              executed.stdout + executed.stderr)
    else:
        check(executed.returncode == 0,
              f"{source_name} failed:\n{executed.stdout}{executed.stderr}")
    return executed.stdout


def main() -> int:
    try:
        sources, source_label = load_pinned_sources()
    except SourceFixtureUnavailable as error:
        print(f"SKIP actual-C transaction test: {error}")
        return 77
    compiler = shutil.which("cc") or shutil.which("gcc")
    check(compiler is not None, "host C compiler (cc/gcc) is required")
    with tempfile.TemporaryDirectory(prefix="npu-refcount-transaction-") as name:
        temp = Path(name)
        baseline = read_source_bytes(sources, BASELINE_FUNCTIONS)
        patched = prepare_patched_source(temp, sources)
        for optimization in ("-O0", "-O2"):
            baseline_output = compile_and_run(
                compiler, temp, BASELINE_HARNESS, baseline,
                f"baseline-{optimization[2:]}.c", "baseline-fails", optimization,
            )
            print(baseline_output, end="")
            patched_output = compile_and_run(
                compiler, temp, PATCHED_HARNESS, patched,
                f"patched-{optimization[2:]}.c", "passes", optimization,
            )
            print(patched_output, end="")
    print(f"SOURCE {source_label}")
    print("LIMIT exact extracted C with host pthread/PM/STM/manager shims only; no kernel build, runtime, or device evidence")
    return 0


if __name__ == "__main__":
    sys.exit(main())
