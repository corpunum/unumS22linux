/*
 * Minimal libvulkan.so bridge for one headless Samsung HAL path.
 *
 * This is not a Vulkan loader and does not implement WSI, ICD selection,
 * validation layers, or feature emulation. It opens the fixed vendor HAL
 * once, follows the AOSP hwvulkan_device_t ABI, and forwards global entry
 * points to that device. Instance/device entry points are returned by the
 * vendor GetInstanceProcAddr callback.
 */
#define VK_NO_PROTOTYPES
#define _POSIX_C_SOURCE 200809L

#include <vulkan/vulkan.h>
#include <dlfcn.h>
#include <pthread.h>
#include <stdlib.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#ifndef BRIDGE_TRACE
#define BRIDGE_TRACE 0
#endif

#if BRIDGE_TRACE
#define BRIDGE_TRACE_STAGE(label)                                                   \
    do {                                                                            \
        (void)fprintf(stderr, "VULKAN_BRIDGE_TRACE %s\n", (label));                \
        (void)fflush(stderr);                                                       \
    } while (0)
#else
#define BRIDGE_TRACE_STAGE(label) do { (void)(label); } while (0)
#endif

#ifndef VENDOR_VULKAN
#define VENDOR_VULKAN "/vendor/lib64/hw/vulkan.samsung.so"
#endif
#define HWVULKAN_DEVICE_0 "vk0"
#define HARDWARE_MODULE_TAG 0x48574d54u
#define HARDWARE_DEVICE_TAG 0x48574454u
#define BRIDGE_EXPORT __attribute__((visibility("default")))

typedef struct hw_module_t hw_module_t;
typedef struct hw_device_t hw_device_t;
typedef struct hw_module_methods_t {
    int (*open)(const hw_module_t *module, const char *id, hw_device_t **device);
} hw_module_methods_t;

/* Exact LP64 hardware.h layout used by hwvulkan.h. */
struct hw_module_t {
    uint32_t tag;
    uint16_t module_api_version;
    uint16_t hal_api_version;
    const char *id;
    const char *name;
    const char *author;
    hw_module_methods_t *methods;
    void *dso;
#if defined(__LP64__)
    uint64_t reserved[32 - 7];
#else
    uint32_t reserved[32 - 7];
#endif
};

struct hw_device_t {
    uint32_t tag;
    uint32_t version;
    hw_module_t *module;
#if defined(__LP64__)
    uint64_t reserved[12];
#else
    uint32_t reserved[12];
#endif
    int (*close)(hw_device_t *device);
};

typedef struct {
    hw_module_t common;
} hwvulkan_module_t;

typedef struct {
    hw_device_t common;
    PFN_vkEnumerateInstanceExtensionProperties EnumerateInstanceExtensionProperties;
    PFN_vkCreateInstance CreateInstance;
    PFN_vkGetInstanceProcAddr GetInstanceProcAddr;
} hwvulkan_device_t;

typedef struct {
    void *handle;
    hwvulkan_device_t *device;
    int status;
    VkInstance instance;
    PFN_vkGetDeviceProcAddr GetDeviceProcAddr;
    PFN_vkGetPhysicalDeviceFeatures2 GetPhysicalDeviceFeatures2;
    PFN_vkCmdCopyBuffer CmdCopyBuffer;
} bridge_state;

static bridge_state state;
static pthread_once_t state_once = PTHREAD_ONCE_INIT;
static pthread_mutex_t callback_mutex = PTHREAD_MUTEX_INITIALIZER;

VKAPI_ATTR PFN_vkVoidFunction VKAPI_CALL vkGetInstanceProcAddr(VkInstance instance,
                                                               const char *pName);
VKAPI_ATTR VkResult VKAPI_CALL vkCreateInstance(const VkInstanceCreateInfo *pCreateInfo,
                                                const VkAllocationCallbacks *pAllocator,
                                                VkInstance *pInstance);
VKAPI_ATTR VkResult VKAPI_CALL vkEnumerateInstanceExtensionProperties(
    const char *pLayerName, uint32_t *pPropertyCount, VkExtensionProperties *pProperties);
VKAPI_ATTR VkResult VKAPI_CALL vkEnumerateInstanceLayerProperties(
    uint32_t *pPropertyCount, VkLayerProperties *pProperties);
VKAPI_ATTR VkResult VKAPI_CALL vkEnumerateInstanceVersion(uint32_t *pApiVersion);
VKAPI_ATTR PFN_vkVoidFunction VKAPI_CALL vkGetDeviceProcAddr(VkDevice device,
                                                             const char *pName);
VKAPI_ATTR void VKAPI_CALL vkGetPhysicalDeviceFeatures2(VkPhysicalDevice physicalDevice,
                                                        VkPhysicalDeviceFeatures2 *pFeatures);
VKAPI_ATTR void VKAPI_CALL vkCmdCopyBuffer(VkCommandBuffer commandBuffer, VkBuffer srcBuffer,
                                           VkBuffer dstBuffer, uint32_t regionCount,
                                           const VkBufferCopy *pRegions);

static void bridge_init_once(void)
{
    hwvulkan_module_t *module;
    hw_device_t *raw = NULL;
    const char *error;

    memset(&state, 0, sizeof(state));
    state.status = VK_ERROR_INITIALIZATION_FAILED;
    BRIDGE_TRACE_STAGE("init:before-dlopen");
    state.handle = dlopen(VENDOR_VULKAN, RTLD_NOW | RTLD_LOCAL);
    if (state.handle == NULL) {
        BRIDGE_TRACE_STAGE("init:dlopen-failed");
        return;
    }
    BRIDGE_TRACE_STAGE("init:after-dlopen");
    BRIDGE_TRACE_STAGE("init:before-dlsym-HMI");
    /* dlerror is thread-local and may contain an unrelated earlier lookup
     * error from the host application's plugin search.  Clear it before the
     * dlsym whose result is being validated. */
    (void)dlerror();
    module = (hwvulkan_module_t *)dlsym(state.handle, "HMI");
    error = dlerror();
    BRIDGE_TRACE_STAGE("init:after-dlsym-HMI");
#if BRIDGE_TRACE
    (void)fprintf(stderr,
                  "VULKAN_BRIDGE_TRACE HMI fields module=%p error=%s tag=0x%x id=%s methods=%p open=%p\n",
                  (void *)module, error != NULL ? error : "(null)",
                  module != NULL ? module->common.tag : 0u,
                  module != NULL && module->common.id != NULL ? module->common.id : "(null)",
                  module != NULL ? (void *)module->common.methods : NULL,
                  module != NULL && module->common.methods != NULL
                      ? (void *)module->common.methods->open : NULL);
    (void)fflush(stderr);
#endif
    if (module == NULL || error != NULL || module->common.tag != HARDWARE_MODULE_TAG ||
        module->common.id == NULL || strcmp(module->common.id, "vulkan") != 0 ||
        module->common.methods == NULL || module->common.methods->open == NULL) {
        BRIDGE_TRACE_STAGE("init:HMI-invalid");
        return;
    }
    BRIDGE_TRACE_STAGE("init:before-HMI-open");
    if (module->common.methods->open(&module->common, HWVULKAN_DEVICE_0, &raw) != 0 ||
        raw == NULL || raw->tag != HARDWARE_DEVICE_TAG) {
        BRIDGE_TRACE_STAGE("init:HMI-open-failed");
        return;
    }
    BRIDGE_TRACE_STAGE("init:after-HMI-open");
    state.device = (hwvulkan_device_t *)raw;
    if (state.device->EnumerateInstanceExtensionProperties == NULL ||
        state.device->CreateInstance == NULL || state.device->GetInstanceProcAddr == NULL)
        return;
    BRIDGE_TRACE_STAGE("init:callbacks-valid");
    state.status = VK_SUCCESS;
    BRIDGE_TRACE_STAGE("init:ready");
}

static int bridge_ready(void)
{
    return pthread_once(&state_once, bridge_init_once) == 0 && state.status == VK_SUCCESS;
}

static PFN_vkVoidFunction bridge_global(const char *name)
{
    if (strcmp(name, "vkGetInstanceProcAddr") == 0)
        return (PFN_vkVoidFunction)vkGetInstanceProcAddr;
    if (strcmp(name, "vkCreateInstance") == 0)
        return (PFN_vkVoidFunction)vkCreateInstance;
    if (strcmp(name, "vkEnumerateInstanceExtensionProperties") == 0)
        return (PFN_vkVoidFunction)vkEnumerateInstanceExtensionProperties;
    if (strcmp(name, "vkEnumerateInstanceLayerProperties") == 0)
        return (PFN_vkVoidFunction)vkEnumerateInstanceLayerProperties;
    if (strcmp(name, "vkEnumerateInstanceVersion") == 0)
        return (PFN_vkVoidFunction)vkEnumerateInstanceVersion;
    if (strcmp(name, "vkGetDeviceProcAddr") == 0)
        return (PFN_vkVoidFunction)vkGetDeviceProcAddr;
    if (strcmp(name, "vkGetPhysicalDeviceFeatures2") == 0)
        return (PFN_vkVoidFunction)vkGetPhysicalDeviceFeatures2;
    if (strcmp(name, "vkCmdCopyBuffer") == 0)
        return (PFN_vkVoidFunction)vkCmdCopyBuffer;
    return NULL;
}

BRIDGE_EXPORT VKAPI_ATTR PFN_vkVoidFunction VKAPI_CALL
vkGetInstanceProcAddr(VkInstance instance, const char *pName)
{
    PFN_vkVoidFunction global;
    BRIDGE_TRACE_STAGE("GIPA:entry");
    if (pName == NULL || !bridge_ready())
        return NULL;
    global = bridge_global(pName);
    if (global != NULL)
        return global;
    BRIDGE_TRACE_STAGE("GIPA:before-vendor");
    return state.device->GetInstanceProcAddr(instance, pName);
}

BRIDGE_EXPORT VKAPI_ATTR VkResult VKAPI_CALL
vkEnumerateInstanceExtensionProperties(const char *pLayerName, uint32_t *pPropertyCount,
                                       VkExtensionProperties *pProperties)
{
    BRIDGE_TRACE_STAGE("EnumerateExtensions:entry");
    if (!bridge_ready())
        return (VkResult)state.status;
    BRIDGE_TRACE_STAGE("EnumerateExtensions:before-vendor");
    return state.device->EnumerateInstanceExtensionProperties(pLayerName, pPropertyCount,
                                                               pProperties);
}

BRIDGE_EXPORT VKAPI_ATTR VkResult VKAPI_CALL
vkEnumerateInstanceLayerProperties(uint32_t *pPropertyCount, VkLayerProperties *pProperties)
{
    /* This bridge has no layer loader/service. Report the actual empty layer
     * set; no GPU feature or validation layer is fabricated. */
    if (pPropertyCount == NULL)
        return VK_ERROR_INITIALIZATION_FAILED;
    *pPropertyCount = 0;
    (void)pProperties;
    return VK_SUCCESS;
}

BRIDGE_EXPORT VKAPI_ATTR VkResult VKAPI_CALL
vkEnumerateInstanceVersion(uint32_t *pApiVersion)
{
    PFN_vkEnumerateInstanceVersion function;
    BRIDGE_TRACE_STAGE("EnumerateVersion:entry");
    if (!bridge_ready())
        return (VkResult)state.status;
    if (pApiVersion == NULL)
        return VK_ERROR_INITIALIZATION_FAILED;
    BRIDGE_TRACE_STAGE("EnumerateVersion:before-vendor-GIPA-null");
    function = (PFN_vkEnumerateInstanceVersion)
        state.device->GetInstanceProcAddr(VK_NULL_HANDLE, "vkEnumerateInstanceVersion");
    BRIDGE_TRACE_STAGE("EnumerateVersion:after-vendor-GIPA-null");
    if (function == NULL)
        return VK_ERROR_FEATURE_NOT_PRESENT;
    BRIDGE_TRACE_STAGE("EnumerateVersion:before-vendor-version");
    return function(pApiVersion);
}

BRIDGE_EXPORT VKAPI_ATTR VkResult VKAPI_CALL
vkCreateInstance(const VkInstanceCreateInfo *pCreateInfo,
                 const VkAllocationCallbacks *pAllocator, VkInstance *pInstance)
{
    PFN_vkDestroyInstance destroy_instance;
    PFN_vkGetDeviceProcAddr get_device_proc_addr;
    PFN_vkGetPhysicalDeviceFeatures2 get_features2;
    PFN_vkCmdCopyBuffer cmd_copy_buffer;
    VkResult result;

    BRIDGE_TRACE_STAGE("CreateInstance:entry");
    if (!bridge_ready())
        return (VkResult)state.status;
    BRIDGE_TRACE_STAGE("CreateInstance:before-vendor");
    result = state.device->CreateInstance(pCreateInfo, pAllocator, pInstance);
    BRIDGE_TRACE_STAGE("CreateInstance:after-vendor");
    if (result != VK_SUCCESS || pInstance == NULL || *pInstance == VK_NULL_HANDLE)
        return result;
    get_device_proc_addr = (PFN_vkGetDeviceProcAddr)
        state.device->GetInstanceProcAddr(*pInstance, "vkGetDeviceProcAddr");
    get_features2 = (PFN_vkGetPhysicalDeviceFeatures2)
        state.device->GetInstanceProcAddr(*pInstance, "vkGetPhysicalDeviceFeatures2");
    cmd_copy_buffer = (PFN_vkCmdCopyBuffer)
        state.device->GetInstanceProcAddr(*pInstance, "vkCmdCopyBuffer");
    if (get_device_proc_addr == NULL || get_features2 == NULL || cmd_copy_buffer == NULL) {
        destroy_instance = (PFN_vkDestroyInstance)
            state.device->GetInstanceProcAddr(*pInstance, "vkDestroyInstance");
        if (destroy_instance != NULL)
            destroy_instance(*pInstance, pAllocator);
        *pInstance = VK_NULL_HANDLE;
        return VK_ERROR_INITIALIZATION_FAILED;
    }
    pthread_mutex_lock(&callback_mutex);
    state.instance = *pInstance;
    state.GetDeviceProcAddr = get_device_proc_addr;
    state.GetPhysicalDeviceFeatures2 = get_features2;
    state.CmdCopyBuffer = cmd_copy_buffer;
    pthread_mutex_unlock(&callback_mutex);
    return VK_SUCCESS;
}

BRIDGE_EXPORT VKAPI_ATTR PFN_vkVoidFunction VKAPI_CALL
vkGetDeviceProcAddr(VkDevice device, const char *pName)
{
    PFN_vkGetDeviceProcAddr function;
    pthread_mutex_lock(&callback_mutex);
    function = state.GetDeviceProcAddr;
    pthread_mutex_unlock(&callback_mutex);
    if (function == NULL || pName == NULL)
        return NULL;
    return function(device, pName);
}

static void bridge_missing_void_callback(const char *name)
{
    (void)fprintf(stderr, "VULKAN_BRIDGE_MISSING_VOID_CALLBACK name=%s\n", name);
    abort();
}

BRIDGE_EXPORT VKAPI_ATTR void VKAPI_CALL
vkGetPhysicalDeviceFeatures2(VkPhysicalDevice physicalDevice, VkPhysicalDeviceFeatures2 *pFeatures)
{
    PFN_vkGetPhysicalDeviceFeatures2 function;
    pthread_mutex_lock(&callback_mutex);
    function = state.GetPhysicalDeviceFeatures2;
    pthread_mutex_unlock(&callback_mutex);
    if (function == NULL)
        bridge_missing_void_callback("vkGetPhysicalDeviceFeatures2");
    function(physicalDevice, pFeatures);
}

BRIDGE_EXPORT VKAPI_ATTR void VKAPI_CALL
vkCmdCopyBuffer(VkCommandBuffer commandBuffer, VkBuffer srcBuffer, VkBuffer dstBuffer,
                uint32_t regionCount, const VkBufferCopy *pRegions)
{
    PFN_vkCmdCopyBuffer function;
    pthread_mutex_lock(&callback_mutex);
    function = state.CmdCopyBuffer;
    pthread_mutex_unlock(&callback_mutex);
    if (function == NULL)
        bridge_missing_void_callback("vkCmdCopyBuffer");
    function(commandBuffer, srcBuffer, dstBuffer, regionCount, pRegions);
}
