#!/usr/bin/env python3
"""Static host-only checks for the Android Vulkan HAL bridge."""

from __future__ import annotations

from pathlib import Path
import re
import os
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[2]
BRIDGE = Path(os.environ.get(
    "VULKAN_BRIDGE_PATH",
    str(ROOT / "rootfs/gpu-compat-20260921/vulkan-bridge/libvulkan-v4.so"),
))
EXPECTED = {
    "vkGetInstanceProcAddr",
    "vkCreateInstance",
    "vkEnumerateInstanceExtensionProperties",
    "vkEnumerateInstanceLayerProperties",
    "vkEnumerateInstanceVersion",
    "vkGetDeviceProcAddr",
    "vkGetPhysicalDeviceFeatures2",
    "vkCmdCopyBuffer",
}


def run(*args: str) -> str:
    return subprocess.run(args, check=True, text=True, capture_output=True).stdout


def exports(path: Path) -> set[str]:
    result = set()
    for line in run("readelf", "--dyn-syms", "--wide", str(path)).splitlines():
        parts = line.split()
        if not parts or not parts[0].endswith(":") or "UND" in parts:
            continue
        try:
            i = parts.index("DEFAULT")
            name = parts[i + 2]
        except (ValueError, IndexError):
            continue
        result.add(name.split("@", 1)[0])
    return result


def needed(path: Path) -> list[str]:
    result = []
    for line in run("readelf", "-dW", str(path)).splitlines():
        if "(NEEDED)" not in line:
            continue
        match = re.search(r"\[(.*?)\]", line)
        if match:
            result.append(match.group(1))
    return result


def main() -> int:
    if not BRIDGE.is_file():
        print(f"missing bridge: {BRIDGE}", file=sys.stderr)
        return 1
    file_info = run("file", str(BRIDGE))
    if "ELF 64-bit" not in file_info or "ARM aarch64" not in file_info:
        print(f"wrong bridge ELF: {file_info.strip()}", file=sys.stderr)
        return 1
    dynamic = run("readelf", "-dW", str(BRIDGE))
    if "Library soname: [libvulkan.so]" not in dynamic:
        print("wrong SONAME", file=sys.stderr)
        return 1
    actual_needed = needed(BRIDGE)
    if set(actual_needed) != {"libc.so", "libdl.so"}:
        print(f"unexpected NEEDED={actual_needed}", file=sys.stderr)
        return 1
    actual_exports = exports(BRIDGE)
    if actual_exports != EXPECTED:
        print(f"unexpected exports={sorted(actual_exports)}", file=sys.stderr)
        return 1
    functional_test()
    print("VULKAN_BRIDGE_STATIC_TEST PASS")
    print(f"exports={','.join(sorted(actual_exports))}")
    print(f"needed={','.join(actual_needed)}")
    return 0


def functional_test() -> None:
    """Compile a tiny fake HAL and exercise the bridge on the host only."""
    fake_source = r'''
#include <vulkan/vulkan.h>
#include <stdint.h>
#include <string.h>

#define HWVULKAN_DEVICE_0 "vk0"
#define HARDWARE_MODULE_TAG 0x48574d54u
#define HARDWARE_DEVICE_TAG 0x48574454u

typedef struct hw_module_t hw_module_t;
typedef struct hw_device_t hw_device_t;
typedef struct hw_module_methods_t {
    int (*open)(const hw_module_t *, const char *, hw_device_t **);
} hw_module_methods_t;
struct hw_module_t {
    uint32_t tag; uint16_t module_api_version; uint16_t hal_api_version;
    const char *id; const char *name; const char *author;
    hw_module_methods_t *methods; void *dso; uint64_t reserved[25];
};
struct hw_device_t {
    uint32_t tag; uint32_t version; hw_module_t *module;
    uint64_t reserved[12]; int (*close)(hw_device_t *);
};
typedef struct { hw_module_t common; } hwvulkan_module_t;
typedef struct {
    hw_device_t common;
    PFN_vkEnumerateInstanceExtensionProperties EnumerateInstanceExtensionProperties;
    PFN_vkCreateInstance CreateInstance;
    PFN_vkGetInstanceProcAddr GetInstanceProcAddr;
} hwvulkan_device_t;

static VkResult fake_extensions(const char *layer, uint32_t *count, VkExtensionProperties *props)
{
    (void)layer;
    if (!count) return VK_ERROR_INITIALIZATION_FAILED;
    if (!props) { *count = 1; return VK_SUCCESS; }
    if (*count == 0) return VK_INCOMPLETE;
    memset(props, 0, sizeof(*props));
    strcpy(props[0].extensionName, "VK_FAKE_TEST");
    props[0].specVersion = 1;
    *count = 1;
    return VK_SUCCESS;
}
static VkResult fake_version(uint32_t *version)
{
    if (!version) return VK_ERROR_INITIALIZATION_FAILED;
    *version = VK_MAKE_VERSION(1, 3, 279);
    return VK_SUCCESS;
}
static VkResult fake_create(const VkInstanceCreateInfo *info,
                            const VkAllocationCallbacks *alloc, VkInstance *instance)
{
    (void)info; (void)alloc;
    if (!instance) return VK_ERROR_INITIALIZATION_FAILED;
    *instance = (VkInstance)(uintptr_t)0x1234;
    return VK_SUCCESS;
}
__attribute__((visibility("default"))) void fake_marker(void) {}
__attribute__((visibility("default"))) int fake_features_called;
__attribute__((visibility("default"))) int fake_copy_called;
static void fake_features(VkPhysicalDevice physical_device, VkPhysicalDeviceFeatures2 *features)
{
    (void)physical_device;
    ++fake_features_called;
    if (features) features->features.robustBufferAccess = VK_TRUE;
}
static PFN_vkVoidFunction fake_gdp(VkDevice device, const char *name)
{
    (void)device;
    if (name && strcmp(name, "vkFakeDevice") == 0) return (PFN_vkVoidFunction)fake_marker;
    return NULL;
}
static void fake_copy(VkCommandBuffer command_buffer, VkBuffer src_buffer, VkBuffer dst_buffer,
                      uint32_t region_count, const VkBufferCopy *regions)
{
    (void)command_buffer; (void)src_buffer; (void)dst_buffer;
    (void)region_count; (void)regions;
    ++fake_copy_called;
}
static PFN_vkVoidFunction fake_gipa(VkInstance instance, const char *name)
{
    (void)instance;
    if (!name) return NULL;
    if (strcmp(name, "vkEnumerateInstanceVersion") == 0) return (PFN_vkVoidFunction)fake_version;
    if (strcmp(name, "vkEnumerateInstanceExtensionProperties") == 0) return (PFN_vkVoidFunction)fake_extensions;
    if (strcmp(name, "vkCreateInstance") == 0) return (PFN_vkVoidFunction)fake_create;
    if (strcmp(name, "vkGetDeviceProcAddr") == 0) return (PFN_vkVoidFunction)fake_gdp;
    if (strcmp(name, "vkGetPhysicalDeviceFeatures2") == 0) return (PFN_vkVoidFunction)fake_features;
    if (strcmp(name, "vkCmdCopyBuffer") == 0) return (PFN_vkVoidFunction)fake_copy;
    if (strcmp(name, "vkFakeFunction") == 0) return (PFN_vkVoidFunction)fake_marker;
    return NULL;
}
static hwvulkan_device_t fake_device;
static int fake_open(const hw_module_t *module, const char *id, hw_device_t **out)
{
    if (!module || !id || strcmp(id, HWVULKAN_DEVICE_0) != 0 || !out) return -1;
    fake_device.common.tag = HARDWARE_DEVICE_TAG;
    fake_device.common.version = 0x00010000u;
    fake_device.common.module = (hw_module_t *)module;
    fake_device.EnumerateInstanceExtensionProperties = fake_extensions;
    fake_device.CreateInstance = fake_create;
    fake_device.GetInstanceProcAddr = fake_gipa;
    *out = &fake_device.common;
    return 0;
}
static hw_module_methods_t methods = { fake_open };
__attribute__((visibility("default")))
hwvulkan_module_t HMI = {
    .common = {
        .tag = HARDWARE_MODULE_TAG, .module_api_version = 0x0100,
        .hal_api_version = 0x0001, .id = "vulkan", .name = "fake",
        .author = "host-test", .methods = &methods,
    },
};
'''
    harness_source = r'''
#include <vulkan/vulkan.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <dlfcn.h>
extern PFN_vkVoidFunction vkGetInstanceProcAddr(VkInstance, const char *);
extern VkResult vkCreateInstance(const VkInstanceCreateInfo *, const VkAllocationCallbacks *, VkInstance *);
extern VkResult vkEnumerateInstanceExtensionProperties(const char *, uint32_t *, VkExtensionProperties *);
extern VkResult vkEnumerateInstanceLayerProperties(uint32_t *, VkLayerProperties *);
extern VkResult vkEnumerateInstanceVersion(uint32_t *);
extern PFN_vkVoidFunction vkGetDeviceProcAddr(VkDevice, const char *);
extern void vkGetPhysicalDeviceFeatures2(VkPhysicalDevice, VkPhysicalDeviceFeatures2 *);
extern void vkCmdCopyBuffer(VkCommandBuffer, VkBuffer, VkBuffer, uint32_t, const VkBufferCopy *);
static void fail(const char *s) { fprintf(stderr, "FAIL %s\n", s); }
int main(int argc, char **argv) {
    uint32_t version = 0, count = 0;
    VkExtensionProperties prop;
    VkInstance instance = VK_NULL_HANDLE;
    VkResult r;
    void *fake;
    void *expected_marker;
    void *stale_error_handle;
    int *features_called;
    int *copy_called;
    VkPhysicalDeviceFeatures2 features = { .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2 };
    if (argc != 2) { fail("fake path"); return 1; }
    /* Simulate a preceding plugin probe that left an unrelated dlerror on
     * this thread. The bridge must clear it before validating HMI dlsym. */
    stale_error_handle = dlopen("/definitely/missing/vulkan-plugin.so", RTLD_NOW | RTLD_LOCAL);
    (void)stale_error_handle;
    if (vkEnumerateInstanceVersion(NULL) != VK_ERROR_INITIALIZATION_FAILED) { fail("null version"); return 1; }
    r = vkEnumerateInstanceVersion(&version);
    if (r != VK_SUCCESS || version != VK_MAKE_VERSION(1, 3, 279)) { fail("version"); return 2; }
    r = vkEnumerateInstanceExtensionProperties(NULL, &count, NULL);
    if (r != VK_SUCCESS || count != 1) { fail("extension count"); return 3; }
    count = 1;
    r = vkEnumerateInstanceExtensionProperties(NULL, &count, &prop);
    if (r != VK_SUCCESS || strcmp(prop.extensionName, "VK_FAKE_TEST") != 0) { fail("extension forward"); return 4; }
    r = vkCreateInstance(NULL, NULL, &instance);
    if (r != VK_SUCCESS || instance != (VkInstance)(uintptr_t)0x1234) { fail("create forward"); return 5; }
    fake = dlopen(argv[1], RTLD_NOW | RTLD_LOCAL);
    expected_marker = fake != NULL ? dlsym(fake, "fake_marker") : NULL;
    if (expected_marker == NULL || (void *)vkGetInstanceProcAddr(NULL, "vkFakeFunction") != expected_marker) { fail("GIPA exact forward"); return 6; }
    count = 99;
    if (vkEnumerateInstanceLayerProperties(&count, NULL) != VK_SUCCESS || count != 0) { fail("no layers"); return 7; }
    if ((void *)vkGetDeviceProcAddr((VkDevice)(uintptr_t)0x55, "vkFakeDevice") != expected_marker) { fail("GDP exact forward"); return 8; }
    features_called = fake != NULL ? (int *)dlsym(fake, "fake_features_called") : NULL;
    vkGetPhysicalDeviceFeatures2((VkPhysicalDevice)(uintptr_t)0x66, &features);
    if (features.features.robustBufferAccess != VK_TRUE || features_called == NULL || *features_called != 1) { fail("features2 exact forward"); return 9; }
    vkCmdCopyBuffer((VkCommandBuffer)(uintptr_t)0x77, (VkBuffer)(uintptr_t)0x88,
                    (VkBuffer)(uintptr_t)0x99, 0, NULL);
    copy_called = fake != NULL ? (int *)dlsym(fake, "fake_copy_called") : NULL;
    if (copy_called == NULL || *copy_called != 1) { fail("copy exact forward"); return 10; }
    puts("VULKAN_BRIDGE_FUNCTIONAL_TEST PASS");
    return 0;
}
'''
    with tempfile.TemporaryDirectory(prefix="vulkan-bridge-test-") as tmp:
        directory = Path(tmp)
        fake_c = directory / "fake-hal.c"
        harness_c = directory / "harness.c"
        fake_so = directory / "vulkan.samsung.so"
        bridge_so = directory / "libvulkan.so"
        harness = directory / "harness"
        fake_c.write_text(fake_source, encoding="utf-8")
        harness_c.write_text(harness_source, encoding="utf-8")
        compiler = os.environ.get("CC", "cc")
        subprocess.run([compiler, "-std=c11", "-Wall", "-Wextra", "-Werror", "-fPIC",
                        "-shared", "-Wl,-soname,vulkan.samsung.so", "-o", str(fake_so),
                        str(fake_c)], check=True)
        subprocess.run([compiler, "-std=c11", "-Wall", "-Wextra", "-Werror", "-fPIC",
                        "-shared", "-Wl,-soname,libvulkan.so", f'-DVENDOR_VULKAN="{fake_so}"',
                        "-I/usr/include", "-o", str(bridge_so),
                        str(ROOT / "tools/gpu-compat/vulkan-hal-bridge.c"), "-ldl", "-pthread"], check=True)
        subprocess.run([compiler, "-std=c11", "-Wall", "-Wextra", "-Werror", "-o", str(harness),
                        "-I/usr/include", str(harness_c), "-L" + str(directory), "-lvulkan",
                        "-Wl,-rpath," + str(directory)], check=True)
        env = os.environ.copy()
        env["LD_LIBRARY_PATH"] = str(directory)
        subprocess.run([str(harness), str(fake_so)], check=True, env=env)


if __name__ == "__main__":
    raise SystemExit(main())
