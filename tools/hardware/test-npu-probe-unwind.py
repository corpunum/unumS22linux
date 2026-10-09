#!/usr/bin/env python3
"""Execute pinned NPU clock acquisition and probe bodies under host shims."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
PATCH = ROOT / "tools/hardware/npu-probe-unwind-fix.patch"
REF_TRANSACTION_PATCH = ROOT / "tools/hardware/npu-refcount-transaction-fix.patch"
CALLBACK_PATCH = ROOT / "tools/hardware/npu-default-boot-callback-fix.patch"
PINNED_BASE = "4e5c5ad7d950e4de0688b5663965f2075654b2ad"
SOURCE_COMMIT = "3fca50941422439b2019db2e4a3dc1016b2138a1"
SOURCE_URL = "https://raw.githubusercontent.com/LineageOS/android_kernel_samsung_s5e9925"
MAX_SOURCE_BYTES = 512 * 1024
FETCH_TIMEOUT = 5

CLOCK_C = "drivers/vision/npu/core/npu-clock.c"
CLOCK_H = "drivers/vision/npu/core/npu-clock.h"
HWDEV_C = "drivers/vision/npu/core/npu-hw-device.c"
HWDEV_H = "drivers/vision/npu/core/npu-hw-device.h"
VERTEX_C = "drivers/vision/npu/core/npu-vertex.c"
CORE_C = "drivers/vision/npu/core/npu-core.c"
SYSTEM_C = "drivers/vision/npu/core/npu-system.c"
S5E9925_DTS = "arch/arm64/boot/dts/exynos/s5e9925.dts"
S5E9925_CONFIG = "arch/arm64/configs/s5e9925_defconfig"
OF_H = "include/linux/of.h"
OF_PROPERTY_C = "drivers/of/property.c"
SOURCE_FILES = (
    CLOCK_C, CLOCK_H, HWDEV_C, HWDEV_H, VERTEX_C, CORE_C, SYSTEM_C,
    S5E9925_DTS, S5E9925_CONFIG, OF_H, OF_PROPERTY_C,
)
PATCH_FILES = (CLOCK_C, CLOCK_H, HWDEV_C, HWDEV_H, VERTEX_C)
SOURCE_SHA256 = {
    CLOCK_C: "0e3d87104de1667bf8e3c197d2a85a0d61ba725b7290c3ed9ffaff58630672c5",
    CLOCK_H: "90cd0cce235f5aeaba8191fd8279a397ff61624314d9c4b93f1e85bc10c982cc",
    HWDEV_C: "14617a6f8e5b08e1bb169618daa8544f2680ad6709cb9f3b9730919d4dc8e16f",
    HWDEV_H: "43165437c7b6a4c50599c2677536376ab31579de0f5866c8b76e33ff7813e9c3",
    VERTEX_C: "0e130ccedaebab85b2d6e78453a049610abed431c04ab630e4426a0f7aca077a",
    CORE_C: "4316700ba00d739b1414b385b8626fb221820db729f9bd6d47ffeed8ac50aa84",
    SYSTEM_C: "96eaa6bf1511f3e6414e3e376d62592229454a2ea1687d760b7bb8e5952b1a05",
    S5E9925_DTS: "0271bf6b781ec1b3b09d559fa175a7f614e1fd211672dc65c748db9a396798e0",
    S5E9925_CONFIG: "de87dbdff5a4082b2aa6fd511a69b9766ddfb0369738c76885c9766178f2b4f5",
    OF_H: "54c6532bd4c838c8d7ffef2a54052dfb2c7ae306a32de92c339bb7aa95566bbf",
    OF_PROPERTY_C: "78ddae866197962692d77657817b35013b87929c0e2bc3d475665dfd3d5e8530",
}

EXACT_FUNCTIONS = (
    (OF_PROPERTY_C, "int of_property_read_string_helper("),
    (OF_H, "static inline int of_property_count_strings("),
    (OF_H, "static inline int of_property_read_string_index("),
    (CLOCK_C, "static int __npu_clk_get("),
    (CLOCK_C, "int npu_clk_get("),
    (CLOCK_C, "int npu_clk_get_optional("),
    (CLOCK_C, "void npu_clk_put("),
    (HWDEV_C, "static int npu_hwdev_probe("),
    (HWDEV_C, "static int npu_hwdev_remove("),
)
BASELINE_FUNCTIONS = (
    (OF_PROPERTY_C, "int of_property_read_string_helper("),
    (OF_H, "static inline int of_property_count_strings("),
    (OF_H, "static inline int of_property_read_string_index("),
    (CLOCK_C, "int npu_clk_get("),
    (CLOCK_C, "void npu_clk_put("),
    (HWDEV_C, "static int npu_hwdev_probe("),
    (HWDEV_C, "static int npu_hwdev_remove("),
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
        url, headers={"User-Agent": "S22-NPU-probe-unwind-host-test/1"})
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
        raise SourceFixtureUnavailable(
            f"pinned source fetch unavailable: {error}") from error
    check(len(data) <= MAX_SOURCE_BYTES,
          f"pinned source too large: {relative}")
    verify_hash(relative, data)
    return data


def load_sources() -> tuple[dict[str, bytes], str]:
    configured_root = os.environ.get("S22_NPU_PROBE_SOURCE_TREE")
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
              "one or more probe fixtures differ from the pinned source")
        sources = {relative: (source_root / relative).read_bytes()
                   for relative in SOURCE_FILES}
        for relative, data in sources.items():
            verify_hash(relative, data)
        return sources, f"verified clean derived source {SOURCE_COMMIT} fixtures"

    sources = {relative: fetch_source(relative) for relative in SOURCE_FILES}
    return sources, f"public pinned source {PINNED_BASE}"


def extract(sources: dict[str, bytes], functions=EXACT_FUNCTIONS) -> str:
    parts = []
    for relative, marker in functions:
        try:
            text = sources[relative].decode("utf-8")
        except (KeyError, UnicodeError) as error:
            check(False, f"cannot decode pinned source {relative}: {error}")
            raise RuntimeError("unreachable")
        body = function_body(text, marker)
        check(body, f"source is missing {marker} in {relative}")
        parts.append(body)
    return "\n\n".join(parts)


def node_body(dts: str, label: str) -> str:
    match = re.search(rf"\b{re.escape(label)}\s*\{{", dts)
    check(match is not None, f"pinned DT is missing node {label}")
    start = match.end() - 1
    depth = 0
    for end in range(start, len(dts)):
        if dts[end] == "{":
            depth += 1
        elif dts[end] == "}":
            depth -= 1
            if depth == 0:
                return dts[start + 1:end]
    check(False, f"pinned DT node {label} is unterminated")
    return ""


def dt_clock_names(body: str) -> list[str] | None:
    match = re.search(r'\bclock-names\s*=\s*((?:"[^"]*"\s*,?\s*)+);', body)
    if not match:
        return None
    return re.findall(r'"([^"]*)"', match.group(1))


def dt_type(body: str) -> int:
    match = re.search(r'samsung,npuhwdev-type\s*=\s*<\s*(0x[0-9a-fA-F]+|[0-9]+)\s*>', body)
    check(match is not None, "pinned hwdev node has no type property")
    return int(match.group(1), 0)


def check_dt_and_callers(sources: dict[str, bytes]) -> None:
    dts = sources[S5E9925_DTS].decode("utf-8")
    dnc = node_body(dts, "hwdev_dnc")
    npu = node_body(dts, "hwdev_npu")
    dsp = node_body(dts, "hwdev_dsp")
    cl1 = node_body(dts, "hwdev_cl1")
    mif = node_body(dts, "hwdev_mif")
    intc = node_body(dts, "hwdev_int")
    system = node_body(dts, "npu_exynos")
    check(dt_clock_names(dnc) == ["dnc_noc"],
          "pinned DNC DT clock contract changed")
    check(dt_type(dnc) == 0x03, "pinned DNC type changed")
    for label, body, expected_type in (
        ("NPU", npu, 0x07), ("DSP", dsp, 0x07),
        ("CL1", cl1, 0x04), ("MIF", mif, 0x08), ("INT", intc, 0x08),
    ):
        check(dt_clock_names(body) is None,
              f"pinned {label} DT unexpectedly requires direct clock names")
        check(dt_type(body) == expected_type,
              f"pinned {label} hwdev type changed")
    check(dt_clock_names(system) is None,
          "pinned npu_exynos system node unexpectedly gained clock-names")

    config = sources[S5E9925_CONFIG].decode("utf-8")
    check("CONFIG_NPU_USE_BOOT_IOCTL=y" in config,
          "pinned defconfig no longer enables NPU boot ioctl")
    check("# CONFIG_NPU_CORE_DRIVER is not set" in config,
          "pinned defconfig no longer disables the standalone NPU core driver")
    core = sources[CORE_C].decode("utf-8")
    check("#ifdef CONFIG_NPU_CORE_DRIVER\nint npu_core_probe(" in core,
          "npu_core_probe compile guard changed")
    check('npu_clk_get(&core->clks, dev)' in core,
          "shared required-clock core caller changed")
    system_source = sources[SYSTEM_C].decode("utf-8")
    call_at = system_source.find("ret = __npu_clk_get(system, dev);")
    check(call_at >= 0, "shared system clock caller missing")
    guard_at = system_source.rfind("#ifndef CONFIG_NPU_USE_BOOT_IOCTL", 0, call_at)
    end_at = system_source.find("#endif", call_at)
    check(guard_at >= 0 and end_at > call_at,
          "shared system clock caller compile guard changed")
    check("npu_clk_get(&system->clks, dev)" in system_source,
          "shared required-clock system caller changed")
    check("npucore-id" not in dts,
          "pinned DT unexpectedly includes a standalone NPU core node")
    print("PASS pinned DT: DNC has dnc_noc; NPU/DSP and auxiliary hwdevs omit direct clocks")
    print("PASS caller audit: optional helper is needed only by hwdev probe in current defconfig")


def write_source_copy(destination: Path, sources: dict[str, bytes]) -> None:
    for relative in PATCH_FILES:
        path = destination / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(sources[relative])


def apply_patch_file(source_root: Path, patch_path: Path, label: str,
                     check_only: bool = False) -> None:
    command = ["git", "apply", "--whitespace=error-all"]
    if check_only:
        command.append("--check")
    command.append(str(patch_path))
    result = subprocess.run(
        command, cwd=source_root, capture_output=True, text=True,
        check=False, timeout=10,
    )
    check(result.returncode == 0,
          f"{label} failed in temporary source copy:\n{result.stderr}")


def prepare_sources(temp: Path, sources: dict[str, bytes]) -> tuple[str, str]:
    standalone = temp / "standalone"
    write_source_copy(standalone, sources)
    apply_patch_file(standalone, PATCH, "standalone probe patch", check_only=True)
    apply_patch_file(standalone, PATCH, "standalone probe patch")
    patched_functions = extract({
        **sources,
        CLOCK_C: (standalone / CLOCK_C).read_bytes(),
        CLOCK_H: (standalone / CLOCK_H).read_bytes(),
        HWDEV_C: (standalone / HWDEV_C).read_bytes(),
    })

    combined = temp / "combined"
    write_source_copy(combined, sources)
    for patch_path, label in (
        (REF_TRANSACTION_PATCH, "NPU ref-transaction patch"),
        (CALLBACK_PATCH, "NPU default-boot callback patch"),
        (PATCH, "NPU probe-unwind patch"),
    ):
        apply_patch_file(combined, patch_path, label, check_only=True)
        apply_patch_file(combined, patch_path, label)
    print("PASS plain apply checks: standalone and ref-transaction + callback + probe patch stack")
    return extract(sources, BASELINE_FUNCTIONS), patched_functions


HARNESS = r"""
#define _GNU_SOURCE
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdarg.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static void discard_log(const char *format, ...)
{ (void)format; }

#ifndef EPROBE_DEFER
#define EPROBE_DEFER 517
#endif
#define GFP_KERNEL 0
#define NPU_HWDEVICE_MAX_DEVICES 10
#define NPU_HWDEV_TYPE_PWRCTRL 0x1
#define NPU_HWDEV_TYPE_CLKCTRL 0x2
#define NPU_HWDEV_TYPE_DVFS 0x4
#define NPU_HWDEV_TYPE_BTS 0x8
#define NPU_HWDEV_STATUS_PWR_CLK_OFF 0x0
#define NPU_HWDEV_STATUS_PWR_CLK_ON 0x1
#define NPU_HWDEV_STATUS_ACTIVE 0x2
#define MAX_CLOCKS 4
#define MAX_ALLOCATIONS 32
#define MAX_CLOCK_RESOURCES 16

#define BUG_ON(condition) do { if (condition) abort(); } while (0)
#define probe_err(...) discard_log(__VA_ARGS__)
#define probe_warn(...) discard_log(__VA_ARGS__)
#define probe_info(...) discard_log(__VA_ARGS__)
#define npu_err(...) discard_log(__VA_ARGS__)

#define MAX_ERRNO 4095
#define IS_ERR_VALUE(value) ((unsigned long)(value) >= (unsigned long)-MAX_ERRNO)
#define ERR_PTR(error) ((void *)(intptr_t)(error))
#define IS_ERR(pointer) IS_ERR_VALUE((uintptr_t)(pointer))
#define IS_ERR_OR_NULL(pointer) (!(pointer) || IS_ERR(pointer))
#define PTR_ERR(pointer) ((long)(intptr_t)(pointer))

struct property {
    const char *value;
    int length;
};
struct device_node {
    const char *name;
    struct property *clock_names;
    const char *hwdev_name;
    const char *hwdev_parent;
    unsigned int hwdev_type;
    unsigned int hwdev_id;
};
struct clk {
    const char *name;
    int enabled;
    int disable_calls;
    int managed_release_calls;
    int explicit_put_calls;
};
struct device {
    struct device_node *of_node;
    void *driver_data;
    int runtime_enable_calls;
    int runtime_disable_calls;
    bool runtime_enabled;
    int clock_provider_fail_index;
    int clock_provider_fail_errno;
    int clock_provider_null_index;
    int clock_provider_calls;
    int clock_name_count;
    const char *clock_names[MAX_CLOCKS];
    struct clk clocks[MAX_CLOCKS];
};
struct platform_device { struct device dev; };
struct npu_clocks { struct clk **clocks; int clk_count; };
struct npu_hw_device;
typedef int (*ref_callback)(void);
struct npu_hw_ref { int unused; };
struct npu_hwdev_ops {
    int (*boot)(struct npu_hw_device *, bool);
    int (*init)(struct npu_hw_device *, bool);
};
struct npu_hw_device {
    struct device *dev;
    struct npu_clocks clks;
    const char *name;
    const char *parent;
    unsigned int id;
    unsigned int type;
    unsigned int status;
    int idle_load;
    struct npu_hw_ref boot_cnt;
    struct npu_hw_ref init_cnt;
    struct npu_hwdev_ops ops;
};

static struct npu_hw_device *g_hwdev_list[NPU_HWDEVICE_MAX_DEVICES];
static unsigned int g_hwdev_num;
static int property_lookup_calls;
static int allocation_attempts;
static int allocation_fail_at;
static void *allocations[MAX_ALLOCATIONS];
static int allocation_count;
static struct clk *clock_resources[MAX_CLOCK_RESOURCES];
static bool clock_resource_active[MAX_CLOCK_RESOURCES];
static int clock_resource_count;
static int managed_acquires;
static int managed_releases;
static int explicit_puts;
static int invalid_puts;

static struct property *of_find_property(const struct device_node *node,
                                         const char *name, int *length)
{
    property_lookup_calls++;
    if (!node || strcmp(name, "clock-names") || !node->clock_names)
        return NULL;
    if (length)
        *length = node->clock_names->length;
    return node->clock_names;
}

static int of_property_read_string(const struct device_node *node,
                                   const char *name, const char **output)
{
    if (!strcmp(name, "samsung,npuhwdev-name")) {
        if (!node->hwdev_name)
            return -EINVAL;
        *output = node->hwdev_name;
        return 0;
    }
    if (!strcmp(name, "samsung,npuhwdev-parent")) {
        if (!node->hwdev_parent)
            return -EINVAL;
        *output = node->hwdev_parent;
        return 0;
    }
    return -EINVAL;
}

static int of_property_read_u32(const struct device_node *node,
                                const char *name, unsigned int *value)
{
    if (!strcmp(name, "samsung,npuhwdev-type")) {
        *value = node->hwdev_type;
        return 0;
    }
    if (!strcmp(name, "samsung,npuhwdev-id")) {
        *value = node->hwdev_id;
        return 0;
    }
    return -EINVAL;
}

static void *managed_alloc(size_t bytes, bool zero)
{
    void *memory;
    allocation_attempts++;
    if (allocation_fail_at && allocation_attempts == allocation_fail_at)
        return NULL;
    if (allocation_count >= MAX_ALLOCATIONS)
        return NULL;
    memory = malloc(bytes ? bytes : 1);
    if (!memory)
        return NULL;
    memset(memory, zero ? 0 : 0xa5, bytes ? bytes : 1);
    allocations[allocation_count++] = memory;
    return memory;
}

static void *devm_kzalloc(struct device *dev, size_t bytes, int flags)
{
    (void)dev; (void)flags;
    return managed_alloc(bytes, true);
}
static void *devm_kmalloc(struct device *dev, size_t bytes, int flags)
{
    (void)dev; (void)flags;
    return managed_alloc(bytes, false);
}
static void *devm_kcalloc(struct device *dev, size_t count, size_t bytes,
                          int flags)
{
    (void)dev; (void)flags;
    if (bytes && count > SIZE_MAX / bytes)
        return NULL;
    return managed_alloc(count * bytes, true);
}

static struct clk *devm_clk_get(struct device *dev, const char *name)
{
    int i;
    dev->clock_provider_calls++;
    for (i = 0; i < dev->clock_name_count; i++) {
        if (strcmp(name, dev->clock_names[i]))
            continue;
        if (i == dev->clock_provider_fail_index)
            return ERR_PTR(dev->clock_provider_fail_errno);
        if (i == dev->clock_provider_null_index)
            return NULL;
        if (clock_resource_count >= MAX_CLOCK_RESOURCES)
            return ERR_PTR(-ENOMEM);
        clock_resources[clock_resource_count] = &dev->clocks[i];
        clock_resource_active[clock_resource_count] = true;
        clock_resource_count++;
        managed_acquires++;
        return &dev->clocks[i];
    }
    return ERR_PTR(-ENOENT);
}

static void devm_clk_put(struct device *dev, struct clk *clk)
{
    int i;
    (void)dev;
    explicit_puts++;
    for (i = clock_resource_count - 1; i >= 0; i--) {
        if (clock_resource_active[i] && clock_resources[i] == clk) {
            clock_resource_active[i] = false;
            clk->explicit_put_calls++;
            clk->managed_release_calls++;
            managed_releases++;
            return;
        }
    }
    invalid_puts++;
}

static void release_devres(void)
{
    int i;
    for (i = clock_resource_count - 1; i >= 0; i--) {
        if (!clock_resource_active[i])
            continue;
        clock_resource_active[i] = false;
        clock_resources[i]->managed_release_calls++;
        managed_releases++;
    }
    for (i = allocation_count - 1; i >= 0; i--)
        free(allocations[i]);
    memset(allocations, 0, sizeof(allocations));
    allocation_count = 0;
}

static void pm_runtime_enable(struct device *dev)
{
    dev->runtime_enable_calls++;
    dev->runtime_enabled = true;
}
static void pm_runtime_disable(struct device *dev)
{
    dev->runtime_disable_calls++;
    dev->runtime_enabled = false;
}
static void dev_set_drvdata(struct device *dev, void *data)
{ dev->driver_data = data; }
static void *dev_get_drvdata(struct device *dev)
{ return dev->driver_data; }

static int npu_hwdev_default_boot(struct npu_hw_device *hdev, bool on)
{ (void)hdev; (void)on; return 0; }
static int npu_hwdev_dsp_init(struct npu_hw_device *hdev, bool on)
{ (void)hdev; (void)on; return 0; }
static int npu_hwdev_dnc_init(struct npu_hw_device *hdev, bool on)
{ (void)hdev; (void)on; return 0; }
static int npu_hwdev_npu_init(struct npu_hw_device *hdev, bool on)
{ (void)hdev; (void)on; return 0; }
static int npu_hw_ref_open(void) { return 0; }
static int npu_hw_ref_close(void) { return 0; }
static int npu_hw_ref_init(void) { return 0; }
static int npu_hw_ref_deinit(void) { return 0; }
static void npu_hw_ref_setup(struct npu_hw_ref *ref,
                             struct npu_hw_device *hdev,
                             ref_callback first, ref_callback second)
{ (void)ref; (void)hdev; (void)first; (void)second; }

/* Exact pinned OF string helper/count/index functions and NPU clock/probe bodies. */
/* EXACT_FUNCTIONS */

static void expect(bool condition, const char *message)
{
    if (!condition) {
        fprintf(stderr, "FAIL: %s\n", message);
        exit(1);
    }
}

struct fixture {
    struct platform_device pdev;
    struct device_node node;
    struct property clock_property;
    char clock_bytes[128];
};

static void fixture_reset_globals(void)
{
    memset(g_hwdev_list, 0, sizeof(g_hwdev_list));
    g_hwdev_num = 0;
    property_lookup_calls = 0;
    allocation_attempts = 0;
    allocation_fail_at = 0;
    allocation_count = 0;
    memset(allocations, 0, sizeof(allocations));
    memset(clock_resources, 0, sizeof(clock_resources));
    memset(clock_resource_active, 0, sizeof(clock_resource_active));
    clock_resource_count = 0;
    managed_acquires = 0;
    managed_releases = 0;
    explicit_puts = 0;
    invalid_puts = 0;
}

static void fixture_init(struct fixture *f, const char *node_name,
                         const char *hwdev_name, unsigned int type,
                         unsigned int id, const char *const *clock_names,
                         int clock_count)
{
    int i;
    release_devres();
    fixture_reset_globals();
    memset(f, 0, sizeof(*f));
    f->node.name = node_name;
    f->node.hwdev_name = hwdev_name;
    f->node.hwdev_type = type;
    f->node.hwdev_id = id;
    f->pdev.dev.of_node = &f->node;
    f->pdev.dev.clock_provider_fail_index = -1;
    f->pdev.dev.clock_provider_null_index = -1;
    f->pdev.dev.clock_name_count = clock_count;
    for (i = 0; i < clock_count; i++) {
        size_t length = strlen(clock_names[i]) + 1;
        size_t offset = (size_t)f->clock_property.length;
        expect(offset + length <= sizeof(f->clock_bytes), "clock fixture too large");
        memcpy(&f->clock_bytes[offset], clock_names[i], length);
        f->clock_property.length += (int)length;
        f->pdev.dev.clock_names[i] = clock_names[i];
        f->pdev.dev.clocks[i].name = clock_names[i];
    }
    if (clock_count > 0) {
        f->clock_property.value = f->clock_bytes;
        f->node.clock_names = &f->clock_property;
    }
}

static struct npu_hw_device *failed_probe_hdev(void)
{
    expect(allocation_count > 0, "failed probe did not allocate its hdev");
    return (struct npu_hw_device *)allocations[0];
}

static void assert_no_probe_side_effects(struct fixture *f, int expected,
                                         bool hdev_allocated)
{
    struct npu_hw_device *hdev = NULL;
    int ret = npu_hwdev_probe(&f->pdev);
    int i;
    expect(ret == expected, "probe did not preserve the expected failure");
    expect(g_hwdev_num == 0 && g_hwdev_list[0] == NULL,
        "failed probe changed the global hwdev registry");
    expect(f->pdev.dev.runtime_enable_calls == 0 &&
        !f->pdev.dev.runtime_enabled,
        "failed probe enabled runtime PM");
    expect(f->pdev.dev.driver_data == NULL,
        "failed probe published driver data");
    if (hdev_allocated) {
        hdev = failed_probe_hdev();
        expect(hdev->clks.clocks == NULL && hdev->clks.clk_count == 0,
            "failed clock acquisition left nonempty consumer state");
    }
    for (i = 0; i < MAX_CLOCKS; i++)
        expect(f->pdev.dev.clocks[i].disable_calls == 0,
            "failed probe disabled a clock that was never enabled");
    expect(explicit_puts == 0 && invalid_puts == 0,
        "probe failure attempted an explicit or invalid devm clock put");
    release_devres();
    expect(managed_releases == managed_acquires,
        "probe failure did not release each successful devm clock exactly once");
    for (i = 0; i < MAX_CLOCKS; i++)
        expect(f->pdev.dev.clocks[i].managed_release_calls <= 1,
            "probe failure released a devm clock more than once");
}

#if !TEST_BASELINE
static void test_pinned_of_count_string_errno_semantics(void)
{
    struct device_node node;
    struct property property;
    char valid[] = "one\0two\0";
    char malformed[] = {'x', 'y'};
    char empty = 0;
    memset(&node, 0, sizeof(node));
    expect(of_property_count_strings(&node, "clock-names") == -EINVAL,
        "pinned OF helper missing-property errno changed");
    property.value = NULL;
    property.length = 0;
    node.clock_names = &property;
    expect(of_property_count_strings(&node, "clock-names") == -ENODATA,
        "pinned OF helper no-value errno changed");
    property.value = &empty;
    property.length = 0;
    expect(of_property_count_strings(&node, "clock-names") == -ENODATA,
        "pinned OF helper empty-property errno changed");
    property.value = malformed;
    property.length = sizeof(malformed);
    expect(of_property_count_strings(&node, "clock-names") == -EILSEQ,
        "pinned OF helper malformed-string errno changed");
    property.value = valid;
    property.length = sizeof(valid) - 1;
    expect(of_property_count_strings(&node, "clock-names") == 2,
        "pinned OF helper valid string count changed");
    puts("PASS actual pinned OF helper: missing=-EINVAL, no-data=-ENODATA, malformed=-EILSEQ");
}

static void test_optional_missing_clock_names_matches_pinned_npu_nodes(void)
{
    struct fixture f;
    struct npu_clocks clocks = {(struct clk **)(uintptr_t)0x55, 9};
    int ret;
    fixture_init(&f, "hwdev_npu", "NPU", 0x07, 0x02, NULL, 0);
    ret = npu_clk_get_optional(&clocks, &f.pdev.dev);
    expect(ret == 0 && clocks.clocks == NULL && clocks.clk_count == 0,
        "optional helper did not represent absent names as an empty list");
    expect(property_lookup_calls == 1,
        "optional absent-property path called count_strings after its presence check");
    expect(npu_clk_get(&clocks, &f.pdev.dev) == -EINVAL &&
        clocks.clocks == NULL && clocks.clk_count == 0,
        "strict shared API no longer rejects missing clock-names");
    expect(g_hwdev_num == 0 && f.pdev.dev.runtime_enable_calls == 0,
        "direct clock helper changed probe or PM state");
    puts("PASS optional/strict APIs: NPU absent property is empty only through the hwdev API");
}

static void test_type_specific_optional_clock_contract(void)
{
    static const struct {
        const char *node;
        const char *name;
        unsigned int type;
        unsigned int id;
    } optional_nodes[] = {
        {"hwdev_npu", "NPU", 0x07, 0x02},
        {"hwdev_dsp", "DSP", 0x07, 0x04},
        {"hwdev_cl1", "CL1", 0x04, 0x40},
        {"hwdev_mif", "MIF", 0x08, 0x08},
        {"hwdev_int", "INT", 0x08, 0x10},
    };
    struct fixture f;
    struct npu_hw_device *hdev;
    size_t i;
    int ret;
    fixture_init(&f, "hwdev_dnc", "DNC", 0x03, 0x01, NULL, 0);
    assert_no_probe_side_effects(&f, -EINVAL, true);

    for (i = 0; i < sizeof(optional_nodes) / sizeof(optional_nodes[0]); i++) {
        fixture_init(&f, optional_nodes[i].node, optional_nodes[i].name,
            optional_nodes[i].type, optional_nodes[i].id, NULL, 0);
        ret = npu_hwdev_probe(&f.pdev);
        expect(ret == 0 && g_hwdev_num == 1 && g_hwdev_list[0] != NULL,
            "pinned no-direct-clock hwdev did not probe as an empty list");
        hdev = g_hwdev_list[0];
        expect(hdev->clks.clocks == NULL && hdev->clks.clk_count == 0 &&
            f.pdev.dev.runtime_enable_calls == 1 && f.pdev.dev.driver_data == hdev,
            "optional no-clock probe did not publish a coherent state");
        expect(f.pdev.dev.clock_provider_calls == 0,
            "no-clock node unexpectedly requested a provider clock");
        ret = npu_hwdev_remove(&f.pdev);
        expect(ret == 0 && f.pdev.dev.runtime_disable_calls == 1,
            "optional no-clock remove did not disable runtime PM once");
        expect(explicit_puts == 0 && invalid_puts == 0,
            "optional no-clock remove attempted a clock put");
        release_devres();
        expect(managed_releases == 0 && invalid_puts == 0,
            "optional no-clock remove created an unexpected devres release");
    }
    puts("PASS exact probe C: DNC missing list rejected; NPU/DSP/CL1/MIF/INT empty lists accepted");
}

static void test_dnc_clock_success_and_remove_puts_exactly_once(void)
{
    const char *names[] = {"dnc_noc"};
    struct fixture f;
    struct npu_hw_device *hdev;
    int ret;
    fixture_init(&f, "hwdev_dnc", "DNC", 0x03, 0x01, names, 1);
    ret = npu_hwdev_probe(&f.pdev);
    expect(ret == 0 && g_hwdev_num == 1 && f.pdev.dev.runtime_enable_calls == 1,
        "DNC listed-clock probe failed");
    hdev = g_hwdev_list[0];
    expect(hdev->clks.clk_count == 1 && hdev->clks.clocks[0] == &f.pdev.dev.clocks[0] &&
        hdev->clks.clocks[1] == NULL,
        "DNC did not publish a fully initialized terminated clock list");
    expect(f.pdev.dev.clocks[0].enabled == 0 &&
        f.pdev.dev.clocks[0].disable_calls == 0,
        "probe enabled or disabled a clock during acquisition");
    ret = npu_hwdev_remove(&f.pdev);
    expect(ret == 0 && f.pdev.dev.runtime_disable_calls == 1,
        "DNC remove did not complete");
    expect(explicit_puts == 1 && invalid_puts == 0 &&
        managed_releases == 1 && f.pdev.dev.clocks[0].managed_release_calls == 1 &&
        f.pdev.dev.clocks[0].explicit_put_calls == 1,
        "DNC devm clock reference was not released exactly once");
    expect(f.pdev.dev.clocks[0].disable_calls == 0 &&
        f.pdev.dev.clocks[0].enabled == 0,
        "DNC remove decremented a clock that was never enabled");
    release_devres();
    expect(managed_releases == 1 && invalid_puts == 0,
        "automatic devres cleanup repeated the explicit devm_clk_put");
    puts("PASS exact remove C: listed clock is devm-put once; no disable on never-enabled clock");
}

static void test_probe_allocation_failures_are_transactional(void)
{
    const char *names[] = {"clk0", "clk1", "clk2"};
    struct fixture f;
    int fail_at;
    for (fail_at = 1; fail_at <= 3; fail_at++) {
        fixture_init(&f, "hwdev_dnc", "DNC", 0x03, 0x01, names, 3);
        allocation_fail_at = fail_at;
        assert_no_probe_side_effects(&f, -ENOMEM, fail_at != 1);
        expect(f.pdev.dev.clock_provider_calls == 0,
            "allocation failure reached the clock provider");
    }
    puts("PASS exact probe C: hdev, name-array, and clock-array allocation failures are clean");
}

static void test_malformed_present_clock_names_fail_closed(void)
{
    struct fixture f;
    struct property present_without_value = {NULL, 0};
    struct property unterminated = {NULL, 2};
    char bad[] = {'a', 'b'};
    fixture_init(&f, "hwdev_dnc", "DNC", 0x03, 0x01, NULL, 0);
    f.node.clock_names = &present_without_value;
    assert_no_probe_side_effects(&f, -ENODATA, true);

    fixture_init(&f, "hwdev_dnc", "DNC", 0x03, 0x01, NULL, 0);
    unterminated.value = bad;
    f.node.clock_names = &unterminated;
    assert_no_probe_side_effects(&f, -EILSEQ, true);

    fixture_init(&f, "hwdev_npu", "NPU", 0x07, 0x02, NULL, 0);
    f.node.clock_names = &present_without_value;
    assert_no_probe_side_effects(&f, -ENODATA, true);

    fixture_init(&f, "hwdev_npu", "NPU", 0x07, 0x02, NULL, 0);
    unterminated.value = bad;
    f.node.clock_names = &unterminated;
    assert_no_probe_side_effects(&f, -EILSEQ, true);
    puts("PASS exact probe C: present empty/no-data and malformed DT strings are rejected distinctly");
}

static void test_provider_errors_and_defer_unwind_first_middle_last(void)
{
    const char *names[] = {"clk0", "clk1", "clk2"};
    const int fail_indices[] = {0, 1, 2};
    const int fail_errors[] = {-EPROBE_DEFER, -EIO, -ENOENT};
    const int success_counts[] = {0, 1, 2};
    struct fixture f;
    int scenario;
    for (scenario = 0; scenario < 3; scenario++) {
        int i;
        fixture_init(&f, "hwdev_dnc", "DNC", 0x03, 0x01, names, 3);
        f.pdev.dev.clock_provider_fail_index = fail_indices[scenario];
        f.pdev.dev.clock_provider_fail_errno = fail_errors[scenario];
        assert_no_probe_side_effects(&f, fail_errors[scenario], true);
        expect(managed_acquires == success_counts[scenario] &&
            managed_releases == success_counts[scenario],
            "provider failure did not unwind exactly the prior successful acquisitions");
        expect(f.pdev.dev.clock_provider_calls == fail_indices[scenario] + 1,
            "provider failure did not stop at its first failed clock");
        for (i = 0; i < success_counts[scenario]; i++)
            expect(f.pdev.dev.clocks[i].managed_release_calls == 1,
                "previous successful clock was not released exactly once");
        for (i = success_counts[scenario]; i < MAX_CLOCKS; i++)
            expect(f.pdev.dev.clocks[i].managed_release_calls == 0,
                "failed or later clock was incorrectly released");
    }
    puts("PASS exact probe C: first -EPROBE_DEFER, middle error, and last error unwind transactionally");
}

static void test_provider_null_is_deterministic_and_safe(void)
{
    const char *names[] = {"clk0", "clk1"};
    struct fixture f;
    fixture_init(&f, "hwdev_dnc", "DNC", 0x03, 0x01, names, 2);
    f.pdev.dev.clock_provider_null_index = 1;
    assert_no_probe_side_effects(&f, -EINVAL, true);
    expect(managed_acquires == 1 && managed_releases == 1,
        "NULL provider result did not unwind its preceding acquisition");
    puts("PASS exact probe C: unexpected NULL provider result fails as -EINVAL with empty state");
}

static void test_optional_clock_provider_defer_is_not_ignored(void)
{
    const char *names[] = {"clk0", "clk1"};
    struct fixture f;
    fixture_init(&f, "hwdev_npu", "NPU", 0x07, 0x02, names, 2);
    f.pdev.dev.clock_provider_fail_index = 1;
    f.pdev.dev.clock_provider_fail_errno = -EPROBE_DEFER;
    assert_no_probe_side_effects(&f, -EPROBE_DEFER, true);
    expect(managed_acquires == 1 && managed_releases == 1 &&
        f.pdev.dev.clock_provider_calls == 2,
        "optional-list provider defer did not preserve/unwind correctly");
    puts("PASS exact probe C: optional-list provider -EPROBE_DEFER is fatal and unwound");
}

static void test_dt_count_errors_return_empty_state(void)
{
    struct fixture f;
    struct property malformed = {NULL, 0};
    char bytes[] = {'o', 'n', 'l', 'y'};
    struct npu_clocks clocks = {(struct clk **)(uintptr_t)0x55, 99};
    fixture_init(&f, "hwdev_dnc", "DNC", 0x03, 0x01, NULL, 0);
    f.node.clock_names = &malformed;
    expect(npu_clk_get_optional(&clocks, &f.pdev.dev) == -ENODATA &&
        clocks.clocks == NULL && clocks.clk_count == 0,
        "count_strings no-data error was not preserved with empty state");

    fixture_init(&f, "hwdev_dnc", "DNC", 0x03, 0x01, NULL, 0);
    malformed.value = bytes;
    malformed.length = (int)sizeof(bytes) - 1;
    f.node.clock_names = &malformed;
    f.pdev.dev.clock_names[0] = "only-one";
    f.pdev.dev.clock_names[1] = "missing-from-property";
    f.pdev.dev.clock_name_count = 2;
    clocks.clocks = (struct clk **)(uintptr_t)0x55;
    clocks.clk_count = 99;
    expect(npu_clk_get_optional(&clocks, &f.pdev.dev) == -EILSEQ &&
        clocks.clocks == NULL && clocks.clk_count == 0,
        "count_strings malformed-string error was not preserved with empty state");
    puts("PASS exact clock C: DT count/string errors preserve errno and clear partial state");
}
#endif

static int baseline_repro(void)
{
    const char *names[] = {"clk0", "clk1", "clk2"};
    struct fixture f;
    struct npu_hw_device *hdev;
    uintptr_t poison_bits;
    int ret;
    memset(&poison_bits, 0xa5, sizeof(poison_bits));
    fixture_init(&f, "hwdev_dnc", "DNC", 0x03, 0x01, names, 3);
    f.pdev.dev.clock_provider_fail_index = 1;
    f.pdev.dev.clock_provider_fail_errno = -EPROBE_DEFER;
    ret = npu_hwdev_probe(&f.pdev);
    expect(ret == 0 && g_hwdev_num == 1 && g_hwdev_list[0] != NULL,
        "baseline no longer warns then registers after clock defer");
    hdev = g_hwdev_list[0];
    expect(hdev->clks.clk_count == 3 && hdev->clks.clocks != NULL,
        "baseline deterministic repro lost its unzeroed array");
    expect((uintptr_t)hdev->clks.clocks[1] == poison_bits,
        "baseline poison-filled failed clock slot was not deterministic");
    expect(f.pdev.dev.runtime_enable_calls == 1 &&
        f.pdev.dev.driver_data == hdev,
        "baseline did not activate PM and publish drvdata after failed acquisition");
    release_devres();
    expect(managed_acquires == 1 && managed_releases == 1 && invalid_puts == 0,
        "baseline acquired clock devres did not unwind once at probe teardown");
    puts("BASELINE_REPRO indeterminate clock slot retained while probe registers device");
    return 0;
}

static int patched_tests(void)
{
#if TEST_BASELINE
    return 0;
#else
    test_pinned_of_count_string_errno_semantics();
    test_optional_missing_clock_names_matches_pinned_npu_nodes();
    test_type_specific_optional_clock_contract();
    test_dnc_clock_success_and_remove_puts_exactly_once();
    test_probe_allocation_failures_are_transactional();
    test_malformed_present_clock_names_fail_closed();
    test_provider_errors_and_defer_unwind_first_middle_last();
    test_provider_null_is_deterministic_and_safe();
    test_optional_clock_provider_defer_is_not_ignored();
    test_dt_count_errors_return_empty_state();
    release_devres();
    return 0;
#endif
}

int main(int argc, char **argv)
{
    (void)argv;
#if TEST_BASELINE
    (void)argc;
    return baseline_repro();
#else
    (void)argc;
    return patched_tests();
#endif
}
"""


def compile_and_run(temp: Path, cc: str, optimization: str,
                    functions: str, label: str, baseline: bool) -> None:
    source = temp / f"{label}-{optimization}.c"
    binary = temp / f"{label}-{optimization}"
    complete = HARNESS.replace("/* EXACT_FUNCTIONS */", functions)
    source.write_text(complete, encoding="utf-8")
    result = subprocess.run(
        [cc, "-std=gnu11", optimization, "-Wall", "-Wextra", "-Werror",
         "-Wno-sign-compare",
         "-Wno-unused-function", f"-DTEST_BASELINE={1 if baseline else 0}",
         str(source), "-o", str(binary)],
        capture_output=True, text=True, check=False, timeout=30,
    )
    check(result.returncode == 0,
          f"{label} C compile failed at {optimization}:\n{result.stderr}")
    run = subprocess.run(
        [str(binary)], capture_output=True, text=True, check=False, timeout=10,
    )
    expected = "BASELINE_REPRO" if baseline else "PASS exact"
    check(run.returncode == 0 and expected in run.stdout,
          f"{label} C run failed at {optimization}:\n{run.stdout}\n{run.stderr}")
    print(run.stdout, end="")
    print(f"PASS actual extracted pinned C {label} {optimization}")


def main() -> int:
    try:
        sources, identity = load_sources()
        check_dt_and_callers(sources)
        compiler = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
        check(compiler is not None, "no host C compiler found")
        with tempfile.TemporaryDirectory(prefix="s22-npu-probe-unwind-") as name:
            temp = Path(name)
            baseline_functions, patched_functions = prepare_sources(temp, sources)
            for optimization in ("-O0", "-O2"):
                compile_and_run(temp, compiler, optimization,
                                baseline_functions, "baseline", True)
                compile_and_run(temp, compiler, optimization,
                                patched_functions, "patched", False)
        print(f"SOURCE_FIXTURE {identity}; pinned base {PINNED_BASE}")
        print("RESULT host-source-tested only; no kernel build or device acceptance")
        return 0
    except SourceFixtureUnavailable as error:
        print(f"SOURCE_FIXTURE_UNAVAILABLE {error}", file=sys.stderr)
        return 77
    except (RuntimeError, OSError, subprocess.SubprocessError) as error:
        print(f"FAIL {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
