/*
 * Direct Android hwvulkan HAL probe for the Samsung vendor ICD.
 *
 * This intentionally does not load libvulkan.so.  It opens the vendor DSO,
 * obtains its exported HMI (HAL_MODULE_INFO_SYM), and follows the ABI used by
 * Android's Vulkan loader.  The --load-only mode still runs ELF constructors;
 * it is a load/ABI probe, not a dry run.
 *
 * ABI references (the definitions below are kept local so this builds with
 * the NDK alone):
 *   frameworks/native/vulkan/include/hardware/hwvulkan.h
 *   hardware/libhardware/include_all/hardware/hardware.h
 */
#define VK_NO_PROTOTYPES
#define _POSIX_C_SOURCE 200809L

#include <vulkan/vulkan.h>
#include <dlfcn.h>
#include <errno.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define HWVULKAN_HARDWARE_MODULE_ID "vulkan"
#define HWVULKAN_DEVICE_0 "vk0"
#define HARDWARE_MODULE_TAG 0x48574d54u /* MAKE_TAG_CONSTANT('H','W','M','T') */
#define HARDWARE_DEVICE_TAG 0x48574454u /* MAKE_TAG_CONSTANT('H','W','D','T') */
#define HARDWARE_MODULE_API_VERSION(major, minor) (((major) << 8) | (minor))
#define HARDWARE_DEVICE_API_VERSION_2(maj, min, sub) \
    (((maj) << 24) | ((min) << 16) | (sub))

typedef struct hw_module_t hw_module_t;
typedef struct hw_device_t hw_device_t;
typedef struct hw_module_methods_t {
    int (*open)(const hw_module_t *module, const char *id, hw_device_t **device);
} hw_module_methods_t;

/* Current AOSP hardware.h layout.  Do not replace with a guessed subset. */
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

_Static_assert(offsetof(hwvulkan_module_t, common) == 0, "hwvulkan_module_t ABI");
_Static_assert(offsetof(hwvulkan_device_t, EnumerateInstanceExtensionProperties) ==
               sizeof(hw_device_t), "hwvulkan_device_t ABI");

typedef struct {
    PFN_vkDestroyInstance vkDestroyInstance;
    PFN_vkEnumeratePhysicalDevices vkEnumeratePhysicalDevices;
    PFN_vkGetPhysicalDeviceProperties vkGetPhysicalDeviceProperties;
    PFN_vkGetPhysicalDeviceQueueFamilyProperties vkGetPhysicalDeviceQueueFamilyProperties;
    PFN_vkGetPhysicalDeviceMemoryProperties vkGetPhysicalDeviceMemoryProperties;
    PFN_vkCreateDevice vkCreateDevice;
    PFN_vkGetDeviceProcAddr vkGetDeviceProcAddr;
} instance_api;

typedef struct {
    PFN_vkDestroyDevice vkDestroyDevice;
    PFN_vkGetDeviceQueue vkGetDeviceQueue;
    PFN_vkCreateBuffer vkCreateBuffer;
    PFN_vkDestroyBuffer vkDestroyBuffer;
    PFN_vkGetBufferMemoryRequirements vkGetBufferMemoryRequirements;
    PFN_vkAllocateMemory vkAllocateMemory;
    PFN_vkFreeMemory vkFreeMemory;
    PFN_vkBindBufferMemory vkBindBufferMemory;
    PFN_vkMapMemory vkMapMemory;
    PFN_vkUnmapMemory vkUnmapMemory;
    PFN_vkFlushMappedMemoryRanges vkFlushMappedMemoryRanges;
    PFN_vkInvalidateMappedMemoryRanges vkInvalidateMappedMemoryRanges;
    PFN_vkCreateShaderModule vkCreateShaderModule;
    PFN_vkDestroyShaderModule vkDestroyShaderModule;
    PFN_vkCreateDescriptorSetLayout vkCreateDescriptorSetLayout;
    PFN_vkDestroyDescriptorSetLayout vkDestroyDescriptorSetLayout;
    PFN_vkCreatePipelineLayout vkCreatePipelineLayout;
    PFN_vkDestroyPipelineLayout vkDestroyPipelineLayout;
    PFN_vkCreateComputePipelines vkCreateComputePipelines;
    PFN_vkDestroyPipeline vkDestroyPipeline;
    PFN_vkCreateDescriptorPool vkCreateDescriptorPool;
    PFN_vkDestroyDescriptorPool vkDestroyDescriptorPool;
    PFN_vkAllocateDescriptorSets vkAllocateDescriptorSets;
    PFN_vkUpdateDescriptorSets vkUpdateDescriptorSets;
    PFN_vkCreateCommandPool vkCreateCommandPool;
    PFN_vkDestroyCommandPool vkDestroyCommandPool;
    PFN_vkAllocateCommandBuffers vkAllocateCommandBuffers;
    PFN_vkFreeCommandBuffers vkFreeCommandBuffers;
    PFN_vkBeginCommandBuffer vkBeginCommandBuffer;
    PFN_vkEndCommandBuffer vkEndCommandBuffer;
    PFN_vkCmdBindPipeline vkCmdBindPipeline;
    PFN_vkCmdBindDescriptorSets vkCmdBindDescriptorSets;
    PFN_vkCmdDispatch vkCmdDispatch;
    PFN_vkCmdPipelineBarrier vkCmdPipelineBarrier;
    PFN_vkCreateFence vkCreateFence;
    PFN_vkDestroyFence vkDestroyFence;
    PFN_vkQueueSubmit vkQueueSubmit;
    PFN_vkWaitForFences vkWaitForFences;
} device_api;

static void stage(const char *name)
{
    printf("STAGE %s\n", name);
    fflush(stdout);
}

static const char *vk_result_name(VkResult r)
{
    switch (r) {
    case VK_SUCCESS: return "VK_SUCCESS";
    case VK_NOT_READY: return "VK_NOT_READY";
    case VK_TIMEOUT: return "VK_TIMEOUT";
    case VK_EVENT_SET: return "VK_EVENT_SET";
    case VK_EVENT_RESET: return "VK_EVENT_RESET";
    case VK_INCOMPLETE: return "VK_INCOMPLETE";
    case VK_ERROR_OUT_OF_HOST_MEMORY: return "VK_ERROR_OUT_OF_HOST_MEMORY";
    case VK_ERROR_OUT_OF_DEVICE_MEMORY: return "VK_ERROR_OUT_OF_DEVICE_MEMORY";
    case VK_ERROR_INITIALIZATION_FAILED: return "VK_ERROR_INITIALIZATION_FAILED";
    case VK_ERROR_DEVICE_LOST: return "VK_ERROR_DEVICE_LOST";
    case VK_ERROR_FEATURE_NOT_PRESENT: return "VK_ERROR_FEATURE_NOT_PRESENT";
    case VK_ERROR_EXTENSION_NOT_PRESENT: return "VK_ERROR_EXTENSION_NOT_PRESENT";
    default: return "VK_ERROR_OTHER";
    }
}

static int vk_check(const char *call, VkResult r)
{
    if (r == VK_SUCCESS)
        return 0;
    fprintf(stderr, "VK_ERROR call=%s result=%d name=%s\n", call, r, vk_result_name(r));
    return 1;
}

static void *load_hmi(const char *path, void **handle_out)
{
    void *handle;
    void *sym;
    const char *error;

    stage("dlopen(vulkan HAL)");
    dlerror();
    handle = dlopen(path, RTLD_NOW | RTLD_LOCAL);
    error = dlerror();
    if (handle == NULL) {
        fprintf(stderr, "HAL_DLOPEN_ERROR path=%s detail=%s\n", path,
                error != NULL ? error : "unknown");
        return NULL;
    }
    dlerror();
    sym = dlsym(handle, "HMI");
    error = dlerror();
    if (sym == NULL || error != NULL) {
        fprintf(stderr, "HAL_HMI_MISSING path=%s detail=%s\n", path,
                error != NULL ? error : "unknown");
        dlclose(handle);
        return NULL;
    }
    *handle_out = handle;
    printf("HAL_HMI_OK path=%s symbol=HMI\n", path);
    fflush(stdout);
    return sym;
}

static int validate_module(const hwvulkan_module_t *module)
{
    const hw_module_t *common = &module->common;
    if (common->tag != HARDWARE_MODULE_TAG) {
        fprintf(stderr, "HAL_ABI_ERROR module_tag=0x%08x expected=0x%08x\n",
                common->tag, HARDWARE_MODULE_TAG);
        return 1;
    }
    if (common->methods == NULL || common->methods->open == NULL) {
        fprintf(stderr, "HAL_ABI_ERROR module open method is missing\n");
        return 1;
    }
    printf("HAL_MODULE id=%s name=%s author=%s module_api=0x%04x hal_api=0x%04x\n",
           common->id != NULL ? common->id : "(null)",
           common->name != NULL ? common->name : "(null)",
           common->author != NULL ? common->author : "(null)",
           common->module_api_version, common->hal_api_version);
    fflush(stdout);
    return 0;
}

static int open_device(const hwvulkan_module_t *module, hwvulkan_device_t **out)
{
    hw_device_t *raw = NULL;
    int r;

    stage("HMI.common.methods->open(vk0)");
    r = module->common.methods->open(&module->common, HWVULKAN_DEVICE_0, &raw);
    if (r != 0 || raw == NULL) {
        fprintf(stderr, "HAL_OPEN_ERROR return=%d device=%s\n", r, raw != NULL ? "non-null" : "null");
        return 1;
    }
    /* AOSP's Vulkan loader does not require a close callback for this HAL;
     * some vendor implementations leave it NULL. */
    if (raw->tag != HARDWARE_DEVICE_TAG) {
        fprintf(stderr, "HAL_ABI_ERROR device_tag=0x%08x expected=0x%08x\n", raw->tag,
                HARDWARE_DEVICE_TAG);
        if (raw->close != NULL)
            raw->close(raw);
        return 1;
    }
    *out = (hwvulkan_device_t *)raw;
    printf("HAL_DEVICE_OK tag=0x%08x version=0x%08x\n", raw->tag, raw->version);
    fflush(stdout);
    return 0;
}

static void close_device(hwvulkan_device_t *device)
{
    if (device != NULL && device->common.close != NULL) {
        int r = device->common.close(&device->common);
        printf("HAL_DEVICE_CLOSE return=%d\n", r);
        fflush(stdout);
    }
}

static int load_instance_api(PFN_vkGetInstanceProcAddr gip, VkInstance instance,
                             instance_api *api)
{
    memset(api, 0, sizeof(*api));
#define LOAD_I(name) do { \
    api->name = (PFN_##name)gip(instance, #name); \
    if (api->name == NULL) { fprintf(stderr, "VK_EXPORT_MISSING name=%s\n", #name); return 1; } \
} while (0)
    LOAD_I(vkDestroyInstance);
    LOAD_I(vkEnumeratePhysicalDevices);
    LOAD_I(vkGetPhysicalDeviceProperties);
    LOAD_I(vkGetPhysicalDeviceQueueFamilyProperties);
    LOAD_I(vkGetPhysicalDeviceMemoryProperties);
    LOAD_I(vkCreateDevice);
    LOAD_I(vkGetDeviceProcAddr);
#undef LOAD_I
    return 0;
}

static int load_device_api(PFN_vkGetDeviceProcAddr gdp, VkDevice device, device_api *api)
{
    memset(api, 0, sizeof(*api));
#define LOAD_D(name) do { \
    api->name = (PFN_##name)gdp(device, #name); \
    if (api->name == NULL) { fprintf(stderr, "VK_EXPORT_MISSING name=%s\n", #name); return 1; } \
} while (0)
    LOAD_D(vkDestroyDevice); LOAD_D(vkGetDeviceQueue); LOAD_D(vkCreateBuffer); LOAD_D(vkDestroyBuffer);
    LOAD_D(vkGetBufferMemoryRequirements); LOAD_D(vkAllocateMemory); LOAD_D(vkFreeMemory);
    LOAD_D(vkBindBufferMemory); LOAD_D(vkMapMemory); LOAD_D(vkUnmapMemory);
    LOAD_D(vkFlushMappedMemoryRanges); LOAD_D(vkInvalidateMappedMemoryRanges);
    LOAD_D(vkCreateShaderModule); LOAD_D(vkDestroyShaderModule);
    LOAD_D(vkCreateDescriptorSetLayout); LOAD_D(vkDestroyDescriptorSetLayout);
    LOAD_D(vkCreatePipelineLayout); LOAD_D(vkDestroyPipelineLayout);
    LOAD_D(vkCreateComputePipelines); LOAD_D(vkDestroyPipeline);
    LOAD_D(vkCreateDescriptorPool); LOAD_D(vkDestroyDescriptorPool);
    LOAD_D(vkAllocateDescriptorSets); LOAD_D(vkUpdateDescriptorSets);
    LOAD_D(vkCreateCommandPool); LOAD_D(vkDestroyCommandPool);
    LOAD_D(vkAllocateCommandBuffers); LOAD_D(vkFreeCommandBuffers);
    LOAD_D(vkBeginCommandBuffer); LOAD_D(vkEndCommandBuffer);
    LOAD_D(vkCmdBindPipeline); LOAD_D(vkCmdBindDescriptorSets); LOAD_D(vkCmdDispatch);
    LOAD_D(vkCmdPipelineBarrier); LOAD_D(vkCreateFence); LOAD_D(vkDestroyFence);
    LOAD_D(vkQueueSubmit); LOAD_D(vkWaitForFences);
#undef LOAD_D
    return 0;
}

static int make_instance(const hwvulkan_device_t *device, VkInstance *out,
                         instance_api *api)
{
    VkApplicationInfo app;
    VkInstanceCreateInfo info;
    VkResult r;

    if (device->CreateInstance == NULL || device->GetInstanceProcAddr == NULL) {
        fprintf(stderr, "HAL_ABI_ERROR Vulkan function pointers are incomplete\n");
        return 1;
    }
    memset(&app, 0, sizeof(app));
    app.sType = VK_STRUCTURE_TYPE_APPLICATION_INFO;
    app.pApplicationName = "s22-vulkan-hal-probe";
    app.applicationVersion = 1;
    app.pEngineName = "s22-vulkan-hal-probe";
    app.engineVersion = 1;
    app.apiVersion = VK_API_VERSION_1_0;
    memset(&info, 0, sizeof(info));
    info.sType = VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO;
    info.pApplicationInfo = &app;

    stage("HAL CreateInstance");
    r = device->CreateInstance(&info, NULL, out);
    if (vk_check("HMI.CreateInstance", r) != 0)
        return 1;
    if (load_instance_api(device->GetInstanceProcAddr, *out, api) != 0) {
        api->vkDestroyInstance(*out, NULL);
        *out = VK_NULL_HANDLE;
        return 1;
    }
    return 0;
}

static int select_device(const instance_api *ia, VkInstance instance,
                         VkPhysicalDevice *out, uint32_t *queue_family,
                         VkPhysicalDeviceProperties *properties)
{
    uint32_t count = 0;
    VkPhysicalDevice *devices = NULL;
    int found = 0;
    VkResult r;

    stage("vkEnumeratePhysicalDevices");
    r = ia->vkEnumeratePhysicalDevices(instance, &count, NULL);
    if (vk_check("vkEnumeratePhysicalDevices(count)", r) != 0 || count == 0) {
        fprintf(stderr, "NO_PHYSICAL_DEVICES count=%u\n", count);
        return 1;
    }
    devices = (VkPhysicalDevice *)calloc(count, sizeof(*devices));
    if (devices == NULL)
        return 1;
    r = ia->vkEnumeratePhysicalDevices(instance, &count, devices);
    if (vk_check("vkEnumeratePhysicalDevices(list)", r) != 0) {
        free(devices);
        return 1;
    }
    for (uint32_t i = 0; i < count; ++i) {
        VkPhysicalDeviceProperties p;
        uint32_t qcount = 0;
        VkQueueFamilyProperties *queues = NULL;
        ia->vkGetPhysicalDeviceProperties(devices[i], &p);
        ia->vkGetPhysicalDeviceQueueFamilyProperties(devices[i], &qcount, NULL);
        if (qcount != 0)
            queues = (VkQueueFamilyProperties *)calloc(qcount, sizeof(*queues));
        if (qcount != 0 && queues == NULL)
            continue;
        ia->vkGetPhysicalDeviceQueueFamilyProperties(devices[i], &qcount, queues);
        printf("PHYSICAL_DEVICE index=%u name=%s type=%u api=%u.%u.%u driver=%u queues=%u\n",
               i, p.deviceName, p.deviceType, VK_VERSION_MAJOR(p.apiVersion),
               VK_VERSION_MINOR(p.apiVersion), VK_VERSION_PATCH(p.apiVersion),
               p.driverVersion, qcount);
        for (uint32_t q = 0; q < qcount; ++q) {
            int has_xclipse = 0;
            for (const char *name = p.deviceName; *name != '\0'; ++name) {
                const char *needle = "Xclipse";
                const char *candidate = name;
                while (*candidate != '\0' && *needle != '\0') {
                    char a = *candidate;
                    char b = *needle;
                    if (a >= 'A' && a <= 'Z') a = (char)(a - 'A' + 'a');
                    if (b >= 'A' && b <= 'Z') b = (char)(b - 'A' + 'a');
                    if (a != b) break;
                    ++candidate;
                    ++needle;
                }
                if (*needle == '\0') {
                    has_xclipse = 1;
                    break;
                }
            }
            if (!found && has_xclipse &&
                (p.deviceType == VK_PHYSICAL_DEVICE_TYPE_INTEGRATED_GPU ||
                           p.deviceType == VK_PHYSICAL_DEVICE_TYPE_DISCRETE_GPU) &&
                (queues[q].queueFlags & VK_QUEUE_COMPUTE_BIT) != 0) {
                *out = devices[i];
                *queue_family = q;
                *properties = p;
                found = 1;
            }
        }
        free(queues);
    }
    free(devices);
    if (!found) {
        fprintf(stderr, "NO_NON_CPU_COMPUTE_DEVICE\n");
        return 1;
    }
    printf("GPU_SELECTED name=%s type=%u queue_family=%u\n", properties->deviceName,
           properties->deviceType, *queue_family);
    fflush(stdout);
    return 0;
}

static int enumerate_mode(const char *path)
{
    void *handle = NULL;
    hwvulkan_module_t *module = (hwvulkan_module_t *)load_hmi(path, &handle);
    hwvulkan_device_t *device = NULL;
    VkInstance instance = VK_NULL_HANDLE;
    instance_api ia;
    VkPhysicalDevice physical = VK_NULL_HANDLE;
    VkPhysicalDeviceProperties properties;
    uint32_t queue_family = 0;
    int rc = 1;

    if (module == NULL || validate_module(module) != 0)
        goto done;
    if (open_device(module, &device) != 0)
        goto done;
    if (make_instance(device, &instance, &ia) != 0)
        goto done;
    if (select_device(&ia, instance, &physical, &queue_family, &properties) != 0)
        goto done;
    printf("ENUMERATE PASS device=%s type=%u queue_family=%u\n", properties.deviceName,
           properties.deviceType, queue_family);
    fflush(stdout);
    rc = 0;
done:
    if (instance != VK_NULL_HANDLE)
        ia.vkDestroyInstance(instance, NULL);
    close_device(device);
    if (handle != NULL)
        dlclose(handle);
    return rc;
}

/* --caps: capability inventory only.  Creates an instance (apiVersion 1.1)
 * and queries physical-device properties; never creates a VkDevice, never
 * allocates memory and never submits GPU work. */
static int has_ext(const VkExtensionProperties *e, uint32_t n, const char *name)
{
    for (uint32_t i = 0; i < n; ++i)
        if (strcmp(e[i].extensionName, name) == 0)
            return 1;
    return 0;
}

static void caps_formats(PFN_vkGetPhysicalDeviceFormatProperties2 fp2,
                         PFN_vkGetPhysicalDeviceImageFormatProperties2 ifp2,
                         VkPhysicalDevice pd, VkFormat format, const char *label, int drm_mod)
{
    VkDrmFormatModifierPropertiesEXT mods[64];
    VkDrmFormatModifierPropertiesListEXT list;
    VkFormatProperties2 props;
    memset(&list, 0, sizeof(list));
    memset(&props, 0, sizeof(props));
    list.sType = VK_STRUCTURE_TYPE_DRM_FORMAT_MODIFIER_PROPERTIES_LIST_EXT;
    props.sType = VK_STRUCTURE_TYPE_FORMAT_PROPERTIES_2;
    if (drm_mod) {
        props.pNext = &list;
        fp2(pd, format, &props);
        if (list.drmFormatModifierCount > 64)
            list.drmFormatModifierCount = 64;
        list.pDrmFormatModifierProperties = mods;
    }
    fp2(pd, format, &props);
    printf("CAPS_FORMAT format=%s linear=0x%x optimal=0x%x buffer=0x%x modifiers=%u\n", label,
           props.formatProperties.linearTilingFeatures, props.formatProperties.optimalTilingFeatures,
           props.formatProperties.bufferFeatures, drm_mod ? list.drmFormatModifierCount : 0);
    for (uint32_t i = 0; drm_mod && i < list.drmFormatModifierCount; ++i)
        printf("CAPS_MODIFIER format=%s modifier=0x%016llx planes=%u features=0x%x\n", label,
               (unsigned long long)mods[i].drmFormatModifier, mods[i].drmFormatModifierPlaneCount,
               mods[i].drmFormatModifierTilingFeatures);
    if (ifp2 != NULL) {
        /* LINEAR 1080x2340 image exportable/importable as dma_buf? */
        VkPhysicalDeviceExternalImageFormatInfo ext_info;
        VkPhysicalDeviceImageDrmFormatModifierInfoEXT mod_info;
        VkPhysicalDeviceImageFormatInfo2 info;
        VkExternalImageFormatProperties ext_props;
        VkImageFormatProperties2 out;
        VkResult r;
        memset(&ext_info, 0, sizeof(ext_info));
        memset(&mod_info, 0, sizeof(mod_info));
        memset(&info, 0, sizeof(info));
        memset(&ext_props, 0, sizeof(ext_props));
        memset(&out, 0, sizeof(out));
        ext_info.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_EXTERNAL_IMAGE_FORMAT_INFO;
        ext_info.handleType = VK_EXTERNAL_MEMORY_HANDLE_TYPE_DMA_BUF_BIT_EXT;
        mod_info.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_IMAGE_DRM_FORMAT_MODIFIER_INFO_EXT;
        mod_info.drmFormatModifier = 0; /* DRM_FORMAT_MOD_LINEAR */
        mod_info.sharingMode = VK_SHARING_MODE_EXCLUSIVE;
        info.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_IMAGE_FORMAT_INFO_2;
        info.pNext = &ext_info;
        info.format = format;
        info.type = VK_IMAGE_TYPE_2D;
        info.usage = VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT | VK_IMAGE_USAGE_TRANSFER_SRC_BIT;
        if (drm_mod) {
            ext_info.pNext = &mod_info;
            info.tiling = VK_IMAGE_TILING_DRM_FORMAT_MODIFIER_EXT;
        } else {
            info.tiling = VK_IMAGE_TILING_LINEAR;
        }
        ext_props.sType = VK_STRUCTURE_TYPE_EXTERNAL_IMAGE_FORMAT_PROPERTIES;
        out.sType = VK_STRUCTURE_TYPE_IMAGE_FORMAT_PROPERTIES_2;
        out.pNext = &ext_props;
        r = ifp2(pd, &info, &out);
        printf("CAPS_DMABUF_IMAGE format=%s tiling=%s result=%d max=%ux%u features=0x%x export=0x%x compatible=0x%x\n",
               label, drm_mod ? "drm-linear" : "linear", (int)r,
               out.imageFormatProperties.maxExtent.width, out.imageFormatProperties.maxExtent.height,
               ext_props.externalMemoryProperties.externalMemoryFeatures,
               ext_props.externalMemoryProperties.exportFromImportedHandleTypes,
               ext_props.externalMemoryProperties.compatibleHandleTypes);
    }
}

static int caps_mode(const char *path)
{
    void *handle = NULL;
    hwvulkan_module_t *module = (hwvulkan_module_t *)load_hmi(path, &handle);
    hwvulkan_device_t *device = NULL;
    VkInstance instance = VK_NULL_HANDLE;
    PFN_vkDestroyInstance destroy = NULL;
    VkPhysicalDevice pd[8];
    uint32_t count = 8;
    int rc = 1;

    if (module == NULL || validate_module(module) != 0 || open_device(module, &device) != 0)
        goto done;
    {
        VkApplicationInfo app;
        VkInstanceCreateInfo info;
        PFN_vkGetInstanceProcAddr gip = device->GetInstanceProcAddr;
        PFN_vkEnumerateInstanceVersion eiv =
            (PFN_vkEnumerateInstanceVersion)gip(VK_NULL_HANDLE, "vkEnumerateInstanceVersion");
        uint32_t iver = VK_API_VERSION_1_0;
        uint32_t n = 0;
        VkExtensionProperties *iext = NULL;
        if (eiv != NULL)
            eiv(&iver);
        printf("CAPS_INSTANCE_VERSION %u.%u.%u\n", VK_VERSION_MAJOR(iver), VK_VERSION_MINOR(iver),
               VK_VERSION_PATCH(iver));
        if (device->EnumerateInstanceExtensionProperties(NULL, &n, NULL) == VK_SUCCESS && n > 0 &&
            (iext = (VkExtensionProperties *)calloc(n, sizeof(*iext))) != NULL &&
            device->EnumerateInstanceExtensionProperties(NULL, &n, iext) == VK_SUCCESS)
            for (uint32_t i = 0; i < n; ++i)
                printf("CAPS_INSTANCE_EXT %s %u\n", iext[i].extensionName, iext[i].specVersion);
        free(iext);
        memset(&app, 0, sizeof(app));
        app.sType = VK_STRUCTURE_TYPE_APPLICATION_INFO;
        app.pApplicationName = "s22-vulkan-hal-caps";
        app.apiVersion = VK_API_VERSION_1_1;
        memset(&info, 0, sizeof(info));
        info.sType = VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO;
        info.pApplicationInfo = &app;
        stage("HAL CreateInstance(1.1)");
        if (vk_check("HMI.CreateInstance", device->CreateInstance(&info, NULL, &instance)) != 0)
            goto done;
        destroy = (PFN_vkDestroyInstance)gip(instance, "vkDestroyInstance");
        PFN_vkEnumeratePhysicalDevices epd =
            (PFN_vkEnumeratePhysicalDevices)gip(instance, "vkEnumeratePhysicalDevices");
        PFN_vkEnumerateDeviceExtensionProperties edep =
            (PFN_vkEnumerateDeviceExtensionProperties)gip(instance, "vkEnumerateDeviceExtensionProperties");
        PFN_vkGetPhysicalDeviceProperties2 gpp2 =
            (PFN_vkGetPhysicalDeviceProperties2)gip(instance, "vkGetPhysicalDeviceProperties2");
        PFN_vkGetPhysicalDeviceMemoryProperties gmp =
            (PFN_vkGetPhysicalDeviceMemoryProperties)gip(instance, "vkGetPhysicalDeviceMemoryProperties");
        PFN_vkGetPhysicalDeviceFormatProperties2 fp2 =
            (PFN_vkGetPhysicalDeviceFormatProperties2)gip(instance, "vkGetPhysicalDeviceFormatProperties2");
        PFN_vkGetPhysicalDeviceImageFormatProperties2 ifp2 =
            (PFN_vkGetPhysicalDeviceImageFormatProperties2)gip(instance, "vkGetPhysicalDeviceImageFormatProperties2");
        if (destroy == NULL || epd == NULL || edep == NULL || gpp2 == NULL || gmp == NULL || fp2 == NULL) {
            fprintf(stderr, "VK_EXPORT_MISSING caps\n");
            goto done;
        }
        if (vk_check("vkEnumeratePhysicalDevices", epd(instance, &count, pd)) != 0 && count == 0)
            goto done;
        for (uint32_t d = 0; d < count; ++d) {
            uint32_t en = 0;
            VkExtensionProperties *ext = NULL;
            VkPhysicalDeviceDriverProperties drv;
            VkPhysicalDeviceDrmPropertiesEXT drm;
            VkPhysicalDeviceProperties2 p2;
            VkPhysicalDeviceMemoryProperties mem;
            int has_drm, has_mod;
            edep(pd[d], NULL, &en, NULL);
            ext = (VkExtensionProperties *)calloc(en ? en : 1, sizeof(*ext));
            if (ext == NULL)
                goto done;
            edep(pd[d], NULL, &en, ext);
            has_drm = has_ext(ext, en, VK_EXT_PHYSICAL_DEVICE_DRM_EXTENSION_NAME);
            has_mod = has_ext(ext, en, VK_EXT_IMAGE_DRM_FORMAT_MODIFIER_EXTENSION_NAME);
            memset(&drv, 0, sizeof(drv));
            memset(&drm, 0, sizeof(drm));
            memset(&p2, 0, sizeof(p2));
            drv.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_DRIVER_PROPERTIES;
            drm.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_DRM_PROPERTIES_EXT;
            p2.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PROPERTIES_2;
            if (has_ext(ext, en, VK_KHR_DRIVER_PROPERTIES_EXTENSION_NAME))
                p2.pNext = &drv;
            if (has_drm) {
                drm.pNext = p2.pNext;
                p2.pNext = &drm;
            }
            gpp2(pd[d], &p2);
            printf("CAPS_DEVICE index=%u name=%s type=%u api=%u.%u.%u driver=%u vendor=0x%x device=0x%x extensions=%u\n",
                   d, p2.properties.deviceName, p2.properties.deviceType,
                   VK_VERSION_MAJOR(p2.properties.apiVersion), VK_VERSION_MINOR(p2.properties.apiVersion),
                   VK_VERSION_PATCH(p2.properties.apiVersion), p2.properties.driverVersion,
                   p2.properties.vendorID, p2.properties.deviceID, en);
            if (p2.pNext != NULL && drv.driverID != 0)
                printf("CAPS_DRIVER id=%d name=%s info=%s\n", (int)drv.driverID, drv.driverName, drv.driverInfo);
            if (has_drm)
                printf("CAPS_DRM primary=%d %lld:%lld render=%d %lld:%lld\n",
                       drm.hasPrimary, (long long)drm.primaryMajor, (long long)drm.primaryMinor,
                       drm.hasRender, (long long)drm.renderMajor, (long long)drm.renderMinor);
            for (uint32_t i = 0; i < en; ++i)
                printf("CAPS_DEVICE_EXT %s %u\n", ext[i].extensionName, ext[i].specVersion);
            gmp(pd[d], &mem);
            for (uint32_t i = 0; i < mem.memoryHeapCount; ++i)
                printf("CAPS_HEAP index=%u size_mib=%llu flags=0x%x\n", i,
                       (unsigned long long)(mem.memoryHeaps[i].size >> 20), mem.memoryHeaps[i].flags);
            for (uint32_t i = 0; i < mem.memoryTypeCount; ++i)
                printf("CAPS_MEMTYPE index=%u heap=%u flags=0x%x\n", i, mem.memoryTypes[i].heapIndex,
                       mem.memoryTypes[i].propertyFlags);
            caps_formats(fp2, ifp2, pd[d], VK_FORMAT_B8G8R8A8_UNORM, "B8G8R8A8_UNORM", has_mod);
            caps_formats(fp2, ifp2, pd[d], VK_FORMAT_R8G8B8A8_UNORM, "R8G8B8A8_UNORM", has_mod);
            free(ext);
        }
    }
    printf("CAPS PASS devices=%u no_device_created=1 no_submission=1\n", count);
    fflush(stdout);
    rc = 0;
done:
    if (instance != VK_NULL_HANDLE && destroy != NULL)
        destroy(instance, NULL);
    close_device(device);
    if (handle != NULL)
        dlclose(handle);
    return rc;
}

static int read_shader(const char *path, uint32_t **code_out, size_t *size_out)
{
    FILE *file = fopen(path, "rb");
    long size;
    uint32_t *code;
    if (file == NULL) {
        fprintf(stderr, "SHADER_OPEN_ERROR path=%s errno=%d\n", path, errno);
        return 1;
    }
    if (fseek(file, 0, SEEK_END) != 0 || (size = ftell(file)) < 4 ||
        (size & 3) != 0 || size > 1024 * 1024 || fseek(file, 0, SEEK_SET) != 0) {
        fprintf(stderr, "SHADER_SIZE_ERROR path=%s\n", path);
        fclose(file);
        return 1;
    }
    code = (uint32_t *)malloc((size_t)size);
    if (code == NULL || fread(code, 1, (size_t)size, file) != (size_t)size) {
        fprintf(stderr, "SHADER_READ_ERROR path=%s\n", path);
        free(code);
        fclose(file);
        return 1;
    }
    fclose(file);
    if (code[0] != 0x07230203u) {
        fprintf(stderr, "SHADER_MAGIC_ERROR path=%s magic=0x%08x\n", path, code[0]);
        free(code);
        return 1;
    }
    *code_out = code;
    *size_out = (size_t)size;
    return 0;
}

static int compute_mode(const char *path, const char *shader_path)
{
    void *handle = NULL;
    hwvulkan_module_t *module = (hwvulkan_module_t *)load_hmi(path, &handle);
    hwvulkan_device_t *hal_device = NULL;
    VkInstance instance = VK_NULL_HANDLE;
    instance_api ia;
    VkPhysicalDevice physical = VK_NULL_HANDLE;
    VkPhysicalDeviceProperties properties;
    uint32_t queue_family = 0;
    VkDevice device = VK_NULL_HANDLE;
    device_api da;
    VkQueue queue = VK_NULL_HANDLE;
    VkBuffer buffer = VK_NULL_HANDLE;
    VkDeviceMemory memory = VK_NULL_HANDLE;
    VkShaderModule shader = VK_NULL_HANDLE;
    VkDescriptorSetLayout set_layout = VK_NULL_HANDLE;
    VkPipelineLayout pipeline_layout = VK_NULL_HANDLE;
    VkPipeline pipeline = VK_NULL_HANDLE;
    VkDescriptorPool descriptor_pool = VK_NULL_HANDLE;
    VkDescriptorSet descriptor_set = VK_NULL_HANDLE;
    VkCommandPool command_pool = VK_NULL_HANDLE;
    VkCommandBuffer command_buffer = VK_NULL_HANDLE;
    VkFence fence = VK_NULL_HANDLE;
    uint32_t *code = NULL;
    size_t code_size = 0;
    void *mapped = NULL;
    uint32_t memory_type = UINT32_MAX;
    VkMemoryPropertyFlags memory_flags = 0;
    int submitted = 0;
    int submission_complete = 0;
    int device_api_loaded = 0;
    int safe_cleanup = 0;
    int rc = 1;
    const uint32_t n = 256;
    const VkDeviceSize bytes = (VkDeviceSize)n * sizeof(uint32_t);

    if (module == NULL || validate_module(module) != 0)
        goto done;
    if (open_device(module, &hal_device) != 0)
        goto done;
    if (make_instance(hal_device, &instance, &ia) != 0)
        goto done;
    if (select_device(&ia, instance, &physical, &queue_family, &properties) != 0)
        goto done;
    if (read_shader(shader_path, &code, &code_size) != 0)
        goto done;

    {
        float priority = 1.0f;
        VkDeviceQueueCreateInfo qci;
        VkDeviceCreateInfo dci;
        memset(&qci, 0, sizeof(qci));
        qci.sType = VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO;
        qci.queueFamilyIndex = queue_family;
        qci.queueCount = 1;
        qci.pQueuePriorities = &priority;
        memset(&dci, 0, sizeof(dci));
        dci.sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO;
        dci.queueCreateInfoCount = 1;
        dci.pQueueCreateInfos = &qci;
        stage("vkCreateDevice");
        if (vk_check("vkCreateDevice", ia.vkCreateDevice(physical, &dci, NULL, &device)) != 0)
            goto done;
    }
    if (load_device_api(ia.vkGetDeviceProcAddr, device, &da) != 0)
        goto done;
    device_api_loaded = 1;
    da.vkGetDeviceQueue(device, queue_family, 0, &queue);

    {
        VkBufferCreateInfo bci;
        VkMemoryRequirements requirements;
        VkPhysicalDeviceMemoryProperties memory_properties;
        memset(&bci, 0, sizeof(bci));
        bci.sType = VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO;
        bci.size = bytes;
        bci.usage = VK_BUFFER_USAGE_STORAGE_BUFFER_BIT;
        bci.sharingMode = VK_SHARING_MODE_EXCLUSIVE;
        if (vk_check("vkCreateBuffer", da.vkCreateBuffer(device, &bci, NULL, &buffer)) != 0)
            goto done;
        da.vkGetBufferMemoryRequirements(device, buffer, &requirements);
        ia.vkGetPhysicalDeviceMemoryProperties(physical, &memory_properties);
        for (uint32_t i = 0; i < memory_properties.memoryTypeCount; ++i) {
            VkMemoryPropertyFlags flags = memory_properties.memoryTypes[i].propertyFlags;
            if ((requirements.memoryTypeBits & (1u << i)) != 0 &&
                (flags & VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT) != 0) {
                memory_type = i;
                memory_flags = flags;
                if ((flags & VK_MEMORY_PROPERTY_HOST_COHERENT_BIT) != 0)
                    break;
            }
        }
        if (memory_type == UINT32_MAX) {
            fprintf(stderr, "NO_HOST_VISIBLE_MEMORY\n");
            goto done;
        }
        VkMemoryAllocateInfo mai;
        memset(&mai, 0, sizeof(mai));
        mai.sType = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO;
        mai.allocationSize = requirements.size;
        mai.memoryTypeIndex = memory_type;
        if (vk_check("vkAllocateMemory", da.vkAllocateMemory(device, &mai, NULL, &memory)) != 0)
            goto done;
        if (vk_check("vkBindBufferMemory", da.vkBindBufferMemory(device, buffer, memory, 0)) != 0)
            goto done;
    }

    if (vk_check("vkMapMemory(initial)", da.vkMapMemory(device, memory, 0, VK_WHOLE_SIZE, 0, &mapped)) != 0)
        goto done;
    for (uint32_t i = 0; i < n; ++i)
        ((uint32_t *)mapped)[i] = 0xa5a5a5a5u;
    if ((memory_flags & VK_MEMORY_PROPERTY_HOST_COHERENT_BIT) == 0) {
        VkMappedMemoryRange range;
        memset(&range, 0, sizeof(range));
        range.sType = VK_STRUCTURE_TYPE_MAPPED_MEMORY_RANGE;
        range.memory = memory;
        range.offset = 0;
        range.size = VK_WHOLE_SIZE;
        if (vk_check("vkFlushMappedMemoryRanges(initial)",
                     da.vkFlushMappedMemoryRanges(device, 1, &range)) != 0) {
            da.vkUnmapMemory(device, memory);
            mapped = NULL;
            goto done;
        }
    }
    da.vkUnmapMemory(device, memory);
    mapped = NULL;

    {
        VkShaderModuleCreateInfo smi;
        memset(&smi, 0, sizeof(smi));
        smi.sType = VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO;
        smi.codeSize = code_size;
        smi.pCode = code;
        if (vk_check("vkCreateShaderModule", da.vkCreateShaderModule(device, &smi, NULL, &shader)) != 0)
            goto done;
    }
    {
        VkDescriptorSetLayoutBinding binding;
        VkDescriptorSetLayoutCreateInfo slci;
        VkPipelineLayoutCreateInfo plci;
        VkPipelineShaderStageCreateInfo stage_info;
        VkComputePipelineCreateInfo pci;
        memset(&binding, 0, sizeof(binding));
        binding.binding = 0;
        binding.descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER;
        binding.descriptorCount = 1;
        binding.stageFlags = VK_SHADER_STAGE_COMPUTE_BIT;
        memset(&slci, 0, sizeof(slci));
        slci.sType = VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO;
        slci.bindingCount = 1;
        slci.pBindings = &binding;
        if (vk_check("vkCreateDescriptorSetLayout", da.vkCreateDescriptorSetLayout(device, &slci, NULL, &set_layout)) != 0)
            goto done;
        memset(&plci, 0, sizeof(plci));
        plci.sType = VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO;
        plci.setLayoutCount = 1;
        plci.pSetLayouts = &set_layout;
        if (vk_check("vkCreatePipelineLayout", da.vkCreatePipelineLayout(device, &plci, NULL, &pipeline_layout)) != 0)
            goto done;
        memset(&stage_info, 0, sizeof(stage_info));
        stage_info.sType = VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO;
        stage_info.stage = VK_SHADER_STAGE_COMPUTE_BIT;
        stage_info.module = shader;
        stage_info.pName = "main";
        memset(&pci, 0, sizeof(pci));
        pci.sType = VK_STRUCTURE_TYPE_COMPUTE_PIPELINE_CREATE_INFO;
        pci.stage = stage_info;
        pci.layout = pipeline_layout;
        if (vk_check("vkCreateComputePipelines",
                     da.vkCreateComputePipelines(device, VK_NULL_HANDLE, 1, &pci, NULL, &pipeline)) != 0)
            goto done;
    }
    {
        VkDescriptorPoolSize pool_size;
        VkDescriptorPoolCreateInfo dpci;
        VkDescriptorSetAllocateInfo dsai;
        VkDescriptorBufferInfo buffer_info;
        VkWriteDescriptorSet write;
        memset(&pool_size, 0, sizeof(pool_size));
        pool_size.type = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER;
        pool_size.descriptorCount = 1;
        memset(&dpci, 0, sizeof(dpci));
        dpci.sType = VK_STRUCTURE_TYPE_DESCRIPTOR_POOL_CREATE_INFO;
        dpci.maxSets = 1;
        dpci.poolSizeCount = 1;
        dpci.pPoolSizes = &pool_size;
        if (vk_check("vkCreateDescriptorPool", da.vkCreateDescriptorPool(device, &dpci, NULL, &descriptor_pool)) != 0)
            goto done;
        memset(&dsai, 0, sizeof(dsai));
        dsai.sType = VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO;
        dsai.descriptorPool = descriptor_pool;
        dsai.descriptorSetCount = 1;
        dsai.pSetLayouts = &set_layout;
        if (vk_check("vkAllocateDescriptorSets", da.vkAllocateDescriptorSets(device, &dsai, &descriptor_set)) != 0)
            goto done;
        buffer_info.buffer = buffer;
        buffer_info.offset = 0;
        buffer_info.range = bytes;
        memset(&write, 0, sizeof(write));
        write.sType = VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET;
        write.dstSet = descriptor_set;
        write.dstBinding = 0;
        write.descriptorCount = 1;
        write.descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER;
        write.pBufferInfo = &buffer_info;
        da.vkUpdateDescriptorSets(device, 1, &write, 0, NULL);
    }
    {
        VkCommandPoolCreateInfo cpci;
        VkCommandBufferAllocateInfo cbai;
        memset(&cpci, 0, sizeof(cpci));
        cpci.sType = VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO;
        cpci.queueFamilyIndex = queue_family;
        if (vk_check("vkCreateCommandPool", da.vkCreateCommandPool(device, &cpci, NULL, &command_pool)) != 0)
            goto done;
        memset(&cbai, 0, sizeof(cbai));
        cbai.sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO;
        cbai.commandPool = command_pool;
        cbai.level = VK_COMMAND_BUFFER_LEVEL_PRIMARY;
        cbai.commandBufferCount = 1;
        if (vk_check("vkAllocateCommandBuffers", da.vkAllocateCommandBuffers(device, &cbai, &command_buffer)) != 0)
            goto done;
        VkCommandBufferBeginInfo begin;
        memset(&begin, 0, sizeof(begin));
        begin.sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO;
        begin.flags = VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT;
        if (vk_check("vkBeginCommandBuffer", da.vkBeginCommandBuffer(command_buffer, &begin)) != 0)
            goto done;
        da.vkCmdBindPipeline(command_buffer, VK_PIPELINE_BIND_POINT_COMPUTE, pipeline);
        da.vkCmdBindDescriptorSets(command_buffer, VK_PIPELINE_BIND_POINT_COMPUTE,
                                   pipeline_layout, 0, 1, &descriptor_set, 0, NULL);
        da.vkCmdDispatch(command_buffer, 4, 1, 1);
        VkMemoryBarrier barrier;
        memset(&barrier, 0, sizeof(barrier));
        barrier.sType = VK_STRUCTURE_TYPE_MEMORY_BARRIER;
        barrier.srcAccessMask = VK_ACCESS_SHADER_WRITE_BIT;
        barrier.dstAccessMask = VK_ACCESS_HOST_READ_BIT;
        da.vkCmdPipelineBarrier(command_buffer, VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT,
                                VK_PIPELINE_STAGE_HOST_BIT, 0, 1, &barrier, 0, NULL, 0, NULL);
        if (vk_check("vkEndCommandBuffer", da.vkEndCommandBuffer(command_buffer)) != 0)
            goto done;
    }
    {
        VkFenceCreateInfo fci;
        VkSubmitInfo submit;
        memset(&fci, 0, sizeof(fci));
        fci.sType = VK_STRUCTURE_TYPE_FENCE_CREATE_INFO;
        if (vk_check("vkCreateFence", da.vkCreateFence(device, &fci, NULL, &fence)) != 0)
            goto done;
        memset(&submit, 0, sizeof(submit));
        submit.sType = VK_STRUCTURE_TYPE_SUBMIT_INFO;
        submit.commandBufferCount = 1;
        submit.pCommandBuffers = &command_buffer;
        stage("vkQueueSubmit");
        /* Treat even a failed submit as potentially having reached the
         * driver. Cleanup is deferred unless fence completion is proven. */
        submitted = 1;
        if (vk_check("vkQueueSubmit", da.vkQueueSubmit(queue, 1, &submit, fence)) != 0)
            goto done;
        stage("vkWaitForFences(3s)");
        if (vk_check("vkWaitForFences", da.vkWaitForFences(device, 1, &fence, VK_TRUE, 3000000000ULL)) != 0) {
            fprintf(stderr, "SUBMISSION_COMPLETION_UNPROVEN leave_objects_for_process_exit=1\n");
            goto done;
        }
        submission_complete = 1;
    }
    if (vk_check("vkMapMemory(readback)", da.vkMapMemory(device, memory, 0, VK_WHOLE_SIZE, 0, &mapped)) != 0)
        goto done;
    if ((memory_flags & VK_MEMORY_PROPERTY_HOST_COHERENT_BIT) == 0) {
        VkMappedMemoryRange range;
        memset(&range, 0, sizeof(range));
        range.sType = VK_STRUCTURE_TYPE_MAPPED_MEMORY_RANGE;
        range.memory = memory;
        range.offset = 0;
        range.size = VK_WHOLE_SIZE;
        if (vk_check("vkInvalidateMappedMemoryRanges(readback)",
                     da.vkInvalidateMappedMemoryRanges(device, 1, &range)) != 0)
            goto done;
    }
    {
        uint32_t *values = (uint32_t *)mapped;
        uint64_t checksum = 0;
        for (uint32_t i = 0; i < n; ++i) {
            uint32_t expected = i * 3u + 7u;
            if (values[i] != expected) {
                fprintf(stderr, "COMPUTE_MISMATCH index=%u got=%u expected=%u\n", i, values[i], expected);
                goto done;
            }
            checksum += values[i];
        }
        printf("COMPUTE256 checksum=%llu device=%s PASS\n",
               (unsigned long long)checksum, properties.deviceName);
        fflush(stdout);
    }
    rc = 0;
done:
    /* Once a submitted fence timed out, cleanup can itself call into a wedged
     * driver.  Let the bounded parent reap the process in that case. */
    safe_cleanup = !submitted || submission_complete;
    if (mapped != NULL && device != VK_NULL_HANDLE && safe_cleanup)
        da.vkUnmapMemory(device, memory);
    free(code);
    if (device != VK_NULL_HANDLE && device_api_loaded && safe_cleanup) {
        if (fence != VK_NULL_HANDLE) da.vkDestroyFence(device, fence, NULL);
        if (command_buffer != VK_NULL_HANDLE) da.vkFreeCommandBuffers(device, command_pool, 1, &command_buffer);
        if (command_pool != VK_NULL_HANDLE) da.vkDestroyCommandPool(device, command_pool, NULL);
        if (descriptor_pool != VK_NULL_HANDLE) da.vkDestroyDescriptorPool(device, descriptor_pool, NULL);
        if (pipeline != VK_NULL_HANDLE) da.vkDestroyPipeline(device, pipeline, NULL);
        if (pipeline_layout != VK_NULL_HANDLE) da.vkDestroyPipelineLayout(device, pipeline_layout, NULL);
        if (shader != VK_NULL_HANDLE) da.vkDestroyShaderModule(device, shader, NULL);
        if (set_layout != VK_NULL_HANDLE) da.vkDestroyDescriptorSetLayout(device, set_layout, NULL);
        if (buffer != VK_NULL_HANDLE) da.vkDestroyBuffer(device, buffer, NULL);
        if (memory != VK_NULL_HANDLE) da.vkFreeMemory(device, memory, NULL);
    }
    if (device != VK_NULL_HANDLE && device_api_loaded && safe_cleanup)
        da.vkDestroyDevice(device, NULL);
    if (instance != VK_NULL_HANDLE && (device == VK_NULL_HANDLE || safe_cleanup))
        ia.vkDestroyInstance(instance, NULL);
    if (safe_cleanup)
        close_device(hal_device);
    else
        fprintf(stderr, "SUBMISSION_COMPLETION_UNPROVEN skip HAL close and dlclose\n");
    if (handle != NULL && safe_cleanup)
        dlclose(handle);
    return rc;
}

static void usage(const char *name)
{
    fprintf(stderr, "usage: %s --load-only|--enumerate|--caps|--compute256 [--library PATH] [--shader PATH]\n", name);
}

int main(int argc, char **argv)
{
    const char *mode = NULL;
    const char *library = "/vendor/lib64/hw/vulkan.samsung.so";
    const char *shader = "tools/gpu-compute-probe/compute.spv";
    for (int i = 1; i < argc; ++i) {
        if (strcmp(argv[i], "--load-only") == 0 || strcmp(argv[i], "--enumerate") == 0 ||
            strcmp(argv[i], "--compute256") == 0 || strcmp(argv[i], "--caps") == 0) {
            if (mode != NULL) { usage(argv[0]); return 2; }
            mode = argv[i];
        } else if (strcmp(argv[i], "--library") == 0 && i + 1 < argc) {
            library = argv[++i];
        } else if (strcmp(argv[i], "--shader") == 0 && i + 1 < argc) {
            shader = argv[++i];
        } else {
            usage(argv[0]);
            return 2;
        }
    }
    if (mode == NULL) {
        usage(argv[0]);
        return 2;
    }
    if (strcmp(mode, "--load-only") == 0) {
        void *handle = NULL;
        hwvulkan_module_t *module = (hwvulkan_module_t *)load_hmi(library, &handle);
        int rc;
        if (module == NULL) {
            /* A failed dlopen/HMI lookup does not prove whether all vendor
             * constructors completed, so do not report it as a run. */
            printf("LOAD_ONLY FAIL constructors_ran=unknown open_called=0\n");
            fflush(stdout);
            if (handle != NULL) dlclose(handle);
            return 1;
        }
        rc = validate_module(module);
        printf("LOAD_ONLY %s constructors_ran=1 open_called=0\n", rc == 0 ? "PASS" : "FAIL");
        fflush(stdout);
        if (handle != NULL) dlclose(handle);
        return rc;
    }
    if (strcmp(mode, "--enumerate") == 0)
        return enumerate_mode(library);
    if (strcmp(mode, "--caps") == 0)
        return caps_mode(library);
    return compute_mode(library, shader);
}
