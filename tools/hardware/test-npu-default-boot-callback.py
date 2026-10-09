#!/usr/bin/env python3
"""Run exact pinned NPU PM/clock callback C under host shims only."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import textwrap
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
PATCH = ROOT / "tools/hardware/npu-default-boot-callback-fix.patch"
REF_TRANSACTION_PATCH = ROOT / "tools/hardware/npu-refcount-transaction-fix.patch"
PINNED_BASE = "4e5c5ad7d950e4de0688b5663965f2075654b2ad"
SOURCE_COMMIT = "3fca50941422439b2019db2e4a3dc1016b2138a1"
HWDEV_C = "drivers/vision/npu/core/npu-hw-device.c"
CLOCK_C = "drivers/vision/npu/core/npu-clock.c"
HWDEV_H = "drivers/vision/npu/core/npu-hw-device.h"
PM_RUNTIME_H = "include/linux/pm_runtime.h"
CLK_H = "include/linux/clk.h"
VERTEX_C = "drivers/vision/npu/core/npu-vertex.c"
SOURCE_FILES = (HWDEV_C, CLOCK_C, HWDEV_H, PM_RUNTIME_H, CLK_H, VERTEX_C)
SOURCE_SHA256 = {
    HWDEV_C: "14617a6f8e5b08e1bb169618daa8544f2680ad6709cb9f3b9730919d4dc8e16f",
    CLOCK_C: "0e3d87104de1667bf8e3c197d2a85a0d61ba725b7290c3ed9ffaff58630672c5",
    HWDEV_H: "43165437c7b6a4c50599c2677536376ab31579de0f5866c8b76e33ff7813e9c3",
    PM_RUNTIME_H: "8a5982620fd46a59346c9568f9fcf57790509d81421b610000dd09a25c0daac1",
    CLK_H: "8946bdd3c492f0204d3b6b051e4a9f3690a0f0741894758d3081e8f92cd332ea",
    VERTEX_C: "0e130ccedaebab85b2d6e78453a049610abed431c04ab630e4426a0f7aca077a",
}
SOURCE_URL = "https://raw.githubusercontent.com/LineageOS/android_kernel_samsung_s5e9925"
MAX_SOURCE_BYTES = 128 * 1024
FETCH_TIMEOUT = 5

EXACT_FUNCTIONS = (
    (PM_RUNTIME_H, "static inline void pm_runtime_put_noidle("),
    (PM_RUNTIME_H, "static inline int pm_runtime_get_sync("),
    (PM_RUNTIME_H, "static inline int pm_runtime_resume_and_get("),
    (PM_RUNTIME_H, "static inline int pm_runtime_put_sync("),
    (CLK_H, "static inline int clk_prepare_enable("),
    (CLK_H, "static inline void clk_disable_unprepare("),
    (CLOCK_C, "int npu_clk_prepare_enable("),
    (CLOCK_C, "void npu_clk_disable_unprepare("),
    (HWDEV_C, "static int npu_hwdev_default_boot("),
)


class SourceFixtureUnavailable(Exception):
    pass


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


def verify_hash(relative: str, data: bytes) -> None:
    actual = hashlib.sha256(data).hexdigest()
    check(actual == SOURCE_SHA256[relative],
          f"pinned source hash mismatch for {relative}: {actual}")


def fetch_source(relative: str) -> bytes:
    url = f"{SOURCE_URL}/{PINNED_BASE}/{relative}"
    request = urllib.request.Request(
        url, headers={"User-Agent": "S22-NPU-PM-callback-host-test/1"})
    try:
        with urllib.request.urlopen(request, timeout=FETCH_TIMEOUT) as response:
            check(response.geturl() == url,
                  f"pinned source redirected unexpectedly: {response.geturl()}")
            content_length = response.headers.get("Content-Length")
            if content_length is not None:
                check(int(content_length) <= MAX_SOURCE_BYTES,
                      f"pinned source too large: {relative}")
            data = response.read(MAX_SOURCE_BYTES + 1)
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise SourceFixtureUnavailable(f"pinned source fetch unavailable: {error}") from error
    check(len(data) <= MAX_SOURCE_BYTES, f"pinned source too large: {relative}")
    verify_hash(relative, data)
    return data


def load_sources() -> tuple[dict[str, bytes], str]:
    configured_root = os.environ.get("S22_NPU_PM_CALLBACK_SOURCE_TREE")
    if configured_root:
        source_root = Path(configured_root).expanduser()
        check(source_root.is_dir(), f"configured source tree not found: {source_root}")
        actual = git_output(source_root, "rev-parse", "HEAD")
        check(actual == SOURCE_COMMIT,
              f"expected derived source HEAD {SOURCE_COMMIT}, found {actual}")
        check(not git_output(source_root, "status", "--porcelain"),
              "configured source tree must be clean")
        unchanged = subprocess.run(
            ["git", "-C", str(source_root), "diff", "--quiet", PINNED_BASE,
             SOURCE_COMMIT, "--", *SOURCE_FILES],
            capture_output=True, text=True, check=False, timeout=10,
        )
        check(unchanged.returncode == 0,
              "callback fixture files differ between pinned and derived sources")
        sources = {relative: (source_root / relative).read_bytes()
                   for relative in SOURCE_FILES}
        for relative, data in sources.items():
            verify_hash(relative, data)
        return sources, f"verified derived source {SOURCE_COMMIT} == {PINNED_BASE} fixtures"

    return ({relative: fetch_source(relative) for relative in SOURCE_FILES},
            f"public pinned source {PINNED_BASE}")


def extract(sources: dict[str, bytes]) -> str:
    parts = []
    for relative, marker in EXACT_FUNCTIONS:
        try:
            text = sources[relative].decode("utf-8")
        except (KeyError, UnicodeError) as error:
            check(False, f"cannot decode pinned source {relative}: {error}")
            raise AssertionError("unreachable")
        body = function_body(text, marker)
        check(body, f"source is missing {marker} in {relative}")
        parts.append(body)
    return "\n\n".join(parts)


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
          "callback patch does not apply to exact pinned source:\n" + checked.stderr)
    applied = subprocess.run(
        ["git", "apply", str(PATCH)], cwd=temp,
        capture_output=True, text=True, check=False, timeout=10,
    )
    check(applied.returncode == 0,
          "cannot apply callback patch to temporary source copy:\n" + applied.stderr)

    combined = temp / "combined-patches"
    for relative, data in sources.items():
        destination = combined / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
    for patch_file, label in (
        (REF_TRANSACTION_PATCH, "reference transaction"),
        (PATCH, "PM callback"),
    ):
        combined_check = subprocess.run(
            ["git", "apply", "--check", str(patch_file)], cwd=combined,
            capture_output=True, text=True, check=False, timeout=10,
        )
        check(combined_check.returncode == 0,
              f"{label} patch fails in combined exact-source stack:\n" +
              combined_check.stderr)
        combined_apply = subprocess.run(
            ["git", "apply", str(patch_file)], cwd=combined,
            capture_output=True, text=True, check=False, timeout=10,
        )
        check(combined_apply.returncode == 0,
              f"{label} patch application failed in combined exact-source stack:\n" +
              combined_apply.stderr)
    print("PASS plain apply checks: callback patch alone and after ref transaction patch")

    patched_sources = {relative: (temp / relative).read_bytes()
                       for relative in sources}
    return extract(patched_sources)


HARNESS = r"""
#define _GNU_SOURCE
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define RPM_GET_PUT 1
#define NPU_HWDEV_TYPE_PWRCTRL 0x1
#define NPU_HWDEV_TYPE_CLKCTRL 0x2
#define NPU_HWDEV_STATUS_PWR_CLK_OFF 0x0
#define NPU_HWDEV_STATUS_PWR_CLK_ON 0x1
#define NPU_HWDEV_STATUS_ACTIVE 0x2
#define NPU_HWDEV_STATUS_ERROR 0x4
static void test_npu_err(const char *format, ...);
#define npu_err(...) test_npu_err(__VA_ARGS__)
#define npu_info(...) ((void)0)

typedef struct { int counter; } atomic_t;
static bool atomic_add_unless(atomic_t *value, int delta, int unless)
{
    if (value->counter == unless)
        return false;
    value->counter += delta;
    return true;
}
struct device_power { atomic_t usage_count; };
struct device { struct device_power power; };
struct clk {
    int prepare_result;
    int enable_result;
    int prepared;
    int enabled;
    int prepare_calls;
    int enable_calls;
    int disable_calls;
    int unprepare_calls;
};
struct npu_clocks { int clk_count; struct clk *clocks[4]; };
struct npu_hw_device {
    struct device *dev;
    struct npu_clocks clks;
    const char *name;
    unsigned int id;
    unsigned int type;
    unsigned int status;
};

static int pm_resume_result;
static int pm_idle_result;
static int pm_resume_calls;
static int pm_idle_calls;
static int log_data_calls;
static int clk_prepare_result;
static int clk_enable_result;
static int cleanup_pm_error_seen;
static int cleanup_pm_error_calls;

static void test_npu_err(const char *format, ...)
{
    va_list args;
    if (strcmp(format, "fail to release runtime PM after clock error(%d)\n"))
        return;
    va_start(args, format);
    cleanup_pm_error_seen = va_arg(args, int);
    va_end(args);
    cleanup_pm_error_calls++;
}

static int __pm_runtime_resume(struct device *dev, int flags)
{
    if (flags & RPM_GET_PUT)
        dev->power.usage_count.counter++;
    pm_resume_calls++;
    return pm_resume_result;
}
static int __pm_runtime_idle(struct device *dev, int flags)
{
    (void)flags;
    if (dev->power.usage_count.counter > 0)
        dev->power.usage_count.counter--;
    pm_idle_calls++;
    return pm_idle_result;
}
static int clk_prepare(struct clk *clk)
{
    clk->prepare_calls++;
    if (clk_prepare_result || clk->prepare_result)
        return clk_prepare_result ? clk_prepare_result : clk->prepare_result;
    clk->prepared++;
    return 0;
}
static int clk_enable(struct clk *clk)
{
    clk->enable_calls++;
    if (clk_enable_result || clk->enable_result)
        return clk_enable_result ? clk_enable_result : clk->enable_result;
    clk->enabled++;
    return 0;
}
static void clk_disable(struct clk *clk)
{
    clk->disable_calls++;
    if (clk->enabled > 0)
        clk->enabled--;
}
static void clk_unprepare(struct clk *clk)
{
    clk->unprepare_calls++;
    if (clk->prepared > 0)
        clk->prepared--;
}
static bool __clk_is_enabled(struct clk *clk) { return clk->enabled > 0; }
static void npu_log_hwdev_set_data(unsigned int id)
{ (void)id; log_data_calls++; }

/* Exact pinned PM, common-clock, NPU-clock, and hwdev function bodies. */
#include "functions.inc"

static void expect(bool condition, const char *message)
{
    if (!condition) {
        fprintf(stderr, "FAIL: %s\n", message);
        exit(1);
    }
}
struct fixture {
    struct device device;
    struct npu_hw_device hdev;
    struct clk clocks[2];
};
static void fixture_init(struct fixture *f, unsigned int type)
{
    memset(f, 0, sizeof(*f));
    f->hdev.dev = &f->device;
    f->hdev.name = "test-hwdev";
    f->hdev.id = 2;
    f->hdev.type = type;
    f->hdev.clks.clk_count = 2;
    f->hdev.clks.clocks[0] = &f->clocks[0];
    f->hdev.clks.clocks[1] = &f->clocks[1];
    pm_resume_result = 0;
    pm_idle_result = 0;
    pm_resume_calls = 0;
    pm_idle_calls = 0;
    log_data_calls = 0;
    clk_prepare_result = 0;
    clk_enable_result = 0;
    cleanup_pm_error_seen = 0;
    cleanup_pm_error_calls = 0;
}

static void baseline_tests(void)
{
    struct fixture f;
    int ret;

    fixture_init(&f, NPU_HWDEV_TYPE_PWRCTRL | NPU_HWDEV_TYPE_CLKCTRL);
    pm_resume_result = -EIO;
    ret = npu_hwdev_default_boot(&f.hdev, true);
    if (ret == 0 && f.hdev.status ==
            ((NPU_HWDEV_STATUS_ACTIVE << 16) | NPU_HWDEV_STATUS_PWR_CLK_ON) &&
        f.device.power.usage_count.counter == 1 && f.clocks[0].enabled == 1)
        puts("BASELINE_FAIL negative_pm_error_masked_by_clock_success");
    else
        expect(false, "baseline PM-error reproducer changed");

    fixture_init(&f, NPU_HWDEV_TYPE_PWRCTRL);
    pm_resume_result = 1;
    ret = npu_hwdev_default_boot(&f.hdev, true);
    if (ret == 1 && f.hdev.status ==
            ((NPU_HWDEV_STATUS_ACTIVE << 16) | NPU_HWDEV_STATUS_PWR_CLK_ON))
        puts("BASELINE_FAIL positive_pm_success_returned_as_error");
    else
        expect(false, "baseline positive-PM reproducer changed");

    fixture_init(&f, NPU_HWDEV_TYPE_PWRCTRL | NPU_HWDEV_TYPE_CLKCTRL);
    f.clocks[1].enable_result = -EIO;
    ret = npu_hwdev_default_boot(&f.hdev, true);
    if (ret == -EIO && f.hdev.status ==
            ((NPU_HWDEV_STATUS_ACTIVE << 16) | NPU_HWDEV_STATUS_PWR_CLK_ON) &&
        f.device.power.usage_count.counter == 1 && f.clocks[0].enabled == 0)
        puts("BASELINE_FAIL clock_error_published_active_status_and_retained_pm_ref");
    else
        expect(false, "baseline clock/status reproducer changed");

    fixture_init(&f, NPU_HWDEV_TYPE_PWRCTRL);
    pm_idle_result = 1;
    ret = npu_hwdev_default_boot(&f.hdev, false);
    if (ret == 1 && f.hdev.status == NPU_HWDEV_STATUS_PWR_CLK_OFF &&
        f.device.power.usage_count.counter == 0)
        puts("BASELINE_FAIL positive_pm_put_success_returned_as_error");
    else
        expect(false, "baseline positive-PM-put reproducer changed");

    fixture_init(&f, NPU_HWDEV_TYPE_PWRCTRL);
    pm_idle_result = -EREMOTEIO;
    ret = npu_hwdev_default_boot(&f.hdev, false);
    if (ret == -EREMOTEIO && f.hdev.status == NPU_HWDEV_STATUS_PWR_CLK_OFF &&
        f.device.power.usage_count.counter == 0)
        puts("BASELINE_FAIL negative_pm_put_published_powered_off");
    else
        expect(false, "baseline negative-PM-put reproducer changed");

    fixture_init(&f, NPU_HWDEV_TYPE_PWRCTRL | NPU_HWDEV_TYPE_CLKCTRL);
    f.hdev.clks.clocks[1] = NULL;
    ret = npu_hwdev_default_boot(&f.hdev, true);
    if (ret == 0 && f.hdev.status ==
            ((NPU_HWDEV_STATUS_ACTIVE << 16) | NPU_HWDEV_STATUS_PWR_CLK_ON) &&
        f.device.power.usage_count.counter == 1 && pm_idle_calls == 0 &&
        f.clocks[0].enabled == 0 && f.clocks[0].prepared == 0 &&
        f.clocks[0].disable_calls == 1 && f.clocks[0].unprepare_calls == 1)
        puts("BASELINE_FAIL later_null_clock_returned_zero_after_clock_rollback");
    else
        expect(false, "baseline later-null-clock reproducer changed");
}

static void test_negative_pm_error_balances_usage_and_short_circuits_clock(void)
{
    struct fixture f;
    int ret;
    fixture_init(&f, NPU_HWDEV_TYPE_PWRCTRL | NPU_HWDEV_TYPE_CLKCTRL);
    pm_resume_result = -EIO;
    ret = npu_hwdev_default_boot(&f.hdev, true);
    expect(ret == -EIO, "negative PM resume error was masked");
    expect(pm_resume_calls == 1 && pm_idle_calls == 0 &&
        f.device.power.usage_count.counter == 0,
        "resume_and_get did not balance its failed usage-count increment");
    expect(f.clocks[0].prepare_calls == 0 && f.clocks[1].prepare_calls == 0,
        "clock setup ran after PM resume failure");
    expect(f.hdev.status == NPU_HWDEV_STATUS_ERROR,
        "failed PM resume published a successful status");
    puts("PASS actual patched C: negative PM error balances and stops before clocks");
}

static void test_positive_pm_success_is_normalized(void)
{
    struct fixture f;
    int ret;
    fixture_init(&f, NPU_HWDEV_TYPE_PWRCTRL);
    pm_resume_result = 1;
    ret = npu_hwdev_default_boot(&f.hdev, true);
    expect(ret == 0, "nonnegative PM success was returned as an error");
    expect(f.device.power.usage_count.counter == 1 && pm_idle_calls == 0,
        "successful PM reference was not retained");
    expect(f.hdev.status ==
        ((NPU_HWDEV_STATUS_ACTIVE << 16) | NPU_HWDEV_STATUS_PWR_CLK_ON),
        "successful PM resume did not publish active status");
    puts("PASS actual patched C: positive PM success is normalized to zero");
}

static void test_clock_failure_rolls_back_clocks_and_matching_pm_ref(void)
{
    struct fixture f;
    int ret;
    fixture_init(&f, NPU_HWDEV_TYPE_PWRCTRL | NPU_HWDEV_TYPE_CLKCTRL);
    f.clocks[1].enable_result = -EIO;
    ret = npu_hwdev_default_boot(&f.hdev, true);
    expect(ret == -EIO, "clock error was lost or replaced by cleanup status");
    expect(f.hdev.status == NPU_HWDEV_STATUS_ERROR,
        "clock failure published active status");
    expect(f.device.power.usage_count.counter == 0 && pm_idle_calls == 1,
        "clock failure did not release exactly its successful PM acquisition");
    expect(cleanup_pm_error_calls == 0,
        "successful matching PM release was logged as a failure");
    expect(f.clocks[0].enabled == 0 && f.clocks[0].prepared == 0 &&
        f.clocks[0].disable_calls == 1 && f.clocks[0].unprepare_calls == 1,
        "NPU clock wrapper did not roll back its earlier clock");
    expect(f.clocks[1].enabled == 0 && f.clocks[1].prepared == 0 &&
        f.clocks[1].disable_calls == 0 && f.clocks[1].unprepare_calls == 1,
        "failed clock prepare/enable was not left balanced");
    puts("PASS actual patched C: clock failure retains error status and balances known acquisitions");
}

static void test_clock_failure_pm_cleanup_error_is_recorded_once(void)
{
    struct fixture f;
    int ret;
    fixture_init(&f, NPU_HWDEV_TYPE_PWRCTRL | NPU_HWDEV_TYPE_CLKCTRL);
    f.clocks[1].enable_result = -EIO;
    pm_idle_result = -EREMOTEIO;
    ret = npu_hwdev_default_boot(&f.hdev, true);
    expect(ret == -EIO && f.hdev.status == NPU_HWDEV_STATUS_ERROR,
        "PM cleanup error replaced the primary clock error or published success");
    expect(f.device.power.usage_count.counter == 0 && pm_idle_calls == 1,
        "failed PM cleanup was retried or failed to follow decrement-on-error");
    expect(cleanup_pm_error_seen == -EREMOTEIO && cleanup_pm_error_calls == 1,
        "negative matching PM cleanup result was not recorded exactly once");
    expect(f.clocks[0].enabled == 0 && f.clocks[0].prepared == 0 &&
        f.clocks[1].enabled == 0 && f.clocks[1].prepared == 0,
        "pinned clock helper did not roll back before PM cleanup");
    puts("PASS actual patched C: negative clock-failure PM cleanup is logged once without retry");
}

static void test_first_null_clock_fails_and_balances_pm(void)
{
    struct fixture f;
    int ret;
    fixture_init(&f, NPU_HWDEV_TYPE_PWRCTRL | NPU_HWDEV_TYPE_CLKCTRL);
    f.hdev.clks.clocks[0] = NULL;
    ret = npu_hwdev_default_boot(&f.hdev, true);
    expect(ret == -EINVAL && f.hdev.status == NPU_HWDEV_STATUS_ERROR,
        "first null clock entry did not produce deterministic ERROR");
    expect(pm_resume_calls == 1 && pm_idle_calls == 1 &&
        f.device.power.usage_count.counter == 0,
        "first null clock entry did not balance exactly one PM acquisition");
    expect(f.clocks[0].prepare_calls == 0 && f.clocks[1].prepare_calls == 0 &&
        f.clocks[0].disable_calls == 0 && f.clocks[1].disable_calls == 0,
        "clock callbacks ran despite the first entry being null");
    puts("PASS actual patched C: first null clock is -EINVAL, no enable, one PM balance");
}

static void test_later_null_clock_rolls_back_and_balances_pm(void)
{
    struct fixture f;
    int ret;
    fixture_init(&f, NPU_HWDEV_TYPE_PWRCTRL | NPU_HWDEV_TYPE_CLKCTRL);
    f.hdev.clks.clocks[1] = NULL;
    ret = npu_hwdev_default_boot(&f.hdev, true);
    expect(ret == -EINVAL && f.hdev.status == NPU_HWDEV_STATUS_ERROR,
        "later null clock did not replace stale successful ret with -EINVAL");
    expect(pm_resume_calls == 1 && pm_idle_calls == 1 &&
        f.device.power.usage_count.counter == 0,
        "later null clock did not balance exactly one PM acquisition");
    expect(f.clocks[0].enabled == 0 && f.clocks[0].prepared == 0 &&
        f.clocks[0].disable_calls == 1 && f.clocks[0].unprepare_calls == 1,
        "later null clock did not roll back its earlier successful clock");
    expect(f.clocks[1].prepare_calls == 0 && f.clocks[1].enable_calls == 0,
        "null clock entry unexpectedly reached clock operations");
    puts("PASS actual patched C: later null clock returns -EINVAL after rollback and PM balance");
}

static void test_empty_clock_list_remains_a_successful_noop(void)
{
    struct fixture f;
    int ret;
    fixture_init(&f, NPU_HWDEV_TYPE_PWRCTRL | NPU_HWDEV_TYPE_CLKCTRL);
    f.hdev.clks.clk_count = 0;
    ret = npu_hwdev_default_boot(&f.hdev, true);
    expect(ret == 0 && f.hdev.status ==
            ((NPU_HWDEV_STATUS_ACTIVE << 16) | NPU_HWDEV_STATUS_PWR_CLK_ON),
        "empty clock list no longer preserves successful no-op behavior");
    expect(f.device.power.usage_count.counter == 1 && pm_idle_calls == 0 &&
        f.clocks[0].prepare_calls == 0 && f.clocks[1].prepare_calls == 0,
        "empty clock list changed PM ownership or attempted clock operations");
    puts("PASS actual patched C: empty clock list remains a successful no-op");
}

static void test_clock_only_failure_does_not_put_unowned_pm_ref(void)
{
    struct fixture f;
    int ret;
    fixture_init(&f, NPU_HWDEV_TYPE_CLKCTRL);
    f.clocks[1].enable_result = -EIO;
    ret = npu_hwdev_default_boot(&f.hdev, true);
    expect(ret == -EIO && f.hdev.status == NPU_HWDEV_STATUS_ERROR,
        "clock-only failure result/status changed");
    expect(pm_resume_calls == 0 && pm_idle_calls == 0 &&
        f.device.power.usage_count.counter == 0,
        "clock-only failure modified an unowned PM reference");
    puts("PASS actual patched C: clock-only error performs no PM inverse");
}

static void test_success_and_powerdown_pairing_remain(void)
{
    struct fixture f;
    int ret;
    fixture_init(&f, NPU_HWDEV_TYPE_PWRCTRL | NPU_HWDEV_TYPE_CLKCTRL);
    pm_resume_result = 1;
    ret = npu_hwdev_default_boot(&f.hdev, true);
    expect(ret == 0 && f.device.power.usage_count.counter == 1,
        "successful mixed PM/clock boot did not keep one PM owner");
    expect(f.clocks[0].enabled == 1 && f.clocks[1].enabled == 1,
        "successful mixed boot did not enable both clocks");
    expect(f.hdev.status ==
        ((NPU_HWDEV_STATUS_ACTIVE << 16) | NPU_HWDEV_STATUS_PWR_CLK_ON),
        "successful mixed boot did not publish active status");

    pm_idle_result = 1;
    ret = npu_hwdev_default_boot(&f.hdev, false);
    expect(ret == 0 && f.device.power.usage_count.counter == 0,
        "positive successful powerdown did not normalize and release PM count");
    expect(f.clocks[0].enabled == 0 && f.clocks[1].enabled == 0 &&
        f.hdev.status == NPU_HWDEV_STATUS_PWR_CLK_OFF,
        "matching powerdown did not disable clocks or publish off status");
    puts("PASS actual patched C: success and matching powerdown remain paired");
}

static void test_negative_pm_put_keeps_unknown_state_and_is_not_retried(void)
{
    struct fixture f;
    int ret;
    fixture_init(&f, NPU_HWDEV_TYPE_PWRCTRL | NPU_HWDEV_TYPE_CLKCTRL);
    f.device.power.usage_count.counter = 1;
    f.clocks[0].prepared = 1;
    f.clocks[0].enabled = 1;
    f.clocks[1].prepared = 1;
    f.clocks[1].enabled = 1;
    pm_idle_result = -EREMOTEIO;
    ret = npu_hwdev_default_boot(&f.hdev, false);
    expect(ret == -EREMOTEIO, "negative PM put result was suppressed");
    expect(f.hdev.status == NPU_HWDEV_STATUS_ERROR,
        "failed PM put published the device as proven off");
    expect(f.device.power.usage_count.counter == 0 && pm_idle_calls == 1,
        "failed PM put did not follow the pinned decrement-on-error contract");
    expect(f.clocks[0].enabled == 0 && f.clocks[1].enabled == 0 &&
        f.clocks[0].disable_calls == 1 && f.clocks[1].disable_calls == 1,
        "powerdown clock callback did not run exactly once before PM put");
    puts("PASS actual patched C: negative PM put is an error/unknown state and is not retried");
}

int main(void)
{
#ifdef BASELINE
    baseline_tests();
    return 1;
#else
    test_negative_pm_error_balances_usage_and_short_circuits_clock();
    test_positive_pm_success_is_normalized();
    test_clock_failure_rolls_back_clocks_and_matching_pm_ref();
    test_clock_failure_pm_cleanup_error_is_recorded_once();
    test_first_null_clock_fails_and_balances_pm();
    test_later_null_clock_rolls_back_and_balances_pm();
    test_empty_clock_list_remains_a_successful_noop();
    test_clock_only_failure_does_not_put_unowned_pm_ref();
    test_success_and_powerdown_pairing_remain();
    test_negative_pm_put_keeps_unknown_state_and_is_not_retried();
    puts("PASS actual patched C: all PM/clock callback regressions");
    return 0;
#endif
}
"""


def compile_and_run(compiler: str, temp: Path, functions: str,
                    source_name: str, optimization: str,
                    baseline: bool = False) -> str:
    source = temp / source_name
    included = temp / "functions.inc"
    binary = temp / source_name.removesuffix(".c")
    included.write_text(functions)
    source.write_text(textwrap.dedent(("#define BASELINE 1\n" if baseline else "") + HARNESS))
    result = subprocess.run(
        [compiler, "-std=gnu11", optimization, "-Wall", "-Wextra", "-Werror",
         "-Wno-unused-function", str(source), "-o", str(binary)],
        capture_output=True, text=True, check=False, timeout=20,
    )
    check(result.returncode == 0,
          f"{source_name} did not compile:\n{result.stdout}{result.stderr}")
    executed = subprocess.run(
        [str(binary)], capture_output=True, text=True, check=False, timeout=8,
    )
    if baseline:
        check(executed.returncode == 1 and "BASELINE_FAIL" in executed.stdout,
              "baseline did not reproduce the pinned callback/helper defects:\n" +
              executed.stdout + executed.stderr)
    else:
        check(executed.returncode == 0,
              f"{source_name} failed:\n{executed.stdout}{executed.stderr}")
    return executed.stdout


def main() -> int:
    try:
        sources, source_label = load_sources()
    except SourceFixtureUnavailable as error:
        print(f"SKIP actual-C PM callback test: {error}")
        return 77

    header = sources[HWDEV_H].decode("utf-8")
    for marker in (
        "NPU_HWDEV_TYPE_PWRCTRL = 0x1",
        "NPU_HWDEV_TYPE_CLKCTRL = 0x2",
        "NPU_HWDEV_STATUS_PWR_CLK_OFF = 0x0",
        "NPU_HWDEV_STATUS_PWR_CLK_ON = 0x1",
        "NPU_HWDEV_STATUS_ACTIVE = 0x2",
        "NPU_HWDEV_STATUS_ERROR = 0x4",
    ):
        check(marker in header, f"pinned hwdev header omits {marker}")
    pm_header = sources[PM_RUNTIME_H].decode("utf-8")
    check("runtime PM usage counter of @dev remains\n * incremented in all cases, even if it returns an error code." in pm_header,
          "pinned get_sync usage-count contract changed")
    check("if (ret < 0) {\n\t\tpm_runtime_put_noidle(dev);\n\t\treturn ret;" in pm_header,
          "pinned resume_and_get failure-balancing contract changed")
    check("runtime PM usage counter of @dev remains\n * decremented in all cases, even if it returns an error code." in pm_header,
          "pinned put_sync decrement-on-error contract changed")
    clocks = sources[CLOCK_C].decode("utf-8")
    check("/* roll back */\n\tfor (i = i - 1; i >= 0; i--)\n\t\tclk_disable_unprepare(clocks->clocks[i]);" in clocks,
          "pinned NPU clock-enable partial rollback changed")

    compiler = shutil.which("cc") or shutil.which("gcc")
    check(compiler is not None, "host C compiler (cc/gcc) is required")
    baseline = extract(sources)
    with tempfile.TemporaryDirectory(prefix="npu-default-boot-callback-") as name:
        temp = Path(name)
        patched = prepare_patched_source(temp, sources)
        for optimization in ("-O0", "-O2"):
            print(compile_and_run(compiler, temp, baseline,
                f"baseline-{optimization[2:]}.c", optimization, baseline=True), end="")
            print(compile_and_run(compiler, temp, patched,
                f"patched-{optimization[2:]}.c", optimization), end="")
    print(f"SOURCE {source_label}")
    print("LIMIT exact pinned C bodies with host PM/clock shims; no kernel build, firmware, or device evidence")
    return 0


if __name__ == "__main__":
    sys.exit(main())
