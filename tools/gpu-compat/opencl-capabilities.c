/*
 * Numeric capability report for the exact Xclipse GPU selected by the
 * OpenCL correctness probe. The embedded probe main is renamed so this file
 * reuses its ABI typedefs, loader, staged calls, and Xclipse-only selector.
 *
 * --self-test is host-only and never loads a vendor object.
 * --capabilities [--library PATH] is a target operation; supervise it.
 */
#define main opencl_capabilities_embedded_probe_main
#include "opencl-probe.c"
#undef main

#include <stddef.h>

enum {
    CAP_CL_TRUE = 1,
    CL_DEVICE_MAX_COMPUTE_UNITS = 0x1002,
    CL_DEVICE_ADDRESS_BITS = 0x100d,
    CL_DEVICE_MAX_MEM_ALLOC_SIZE = 0x1010,
    CL_DEVICE_GLOBAL_MEM_SIZE = 0x101f,
    CL_DEVICE_EXTENSIONS = 0x1030,
    CL_DEVICE_HALF_FP_CONFIG = 0x1033,
    CL_DEVICE_HOST_UNIFIED_MEMORY = 0x1035,
    CL_DEVICE_LOCAL_MEM_SIZE = 0x1023
};

typedef struct {
    cl_ulong global_memory_bytes;
    cl_ulong max_alloc_bytes;
    cl_ulong local_memory_bytes;
    cl_uint compute_units;
    cl_uint address_bits;
    cl_bool unified_memory;
    cl_bitfield half_fp_config;
} numeric_capabilities;

static int extension_token(const char *extensions, const char *wanted)
{
    const char *cursor = extensions;
    size_t wanted_length = strlen(wanted);

    if (extensions == NULL || wanted_length == 0)
        return 0;
    while (*cursor != '\0') {
        const char *end = strchr(cursor, ' ');
        size_t length = end == NULL ? strlen(cursor) : (size_t)(end - cursor);

        if (length == wanted_length && strncmp(cursor, wanted, length) == 0)
            return 1;
        if (end == NULL)
            break;
        cursor = end + 1;
    }
    return 0;
}

static int device_scalar(opencl_api *api, cl_device_id device, cl_device_info info,
                         const char *label, size_t size, void *value)
{
    cl_int error;
    char call[96];

    (void)snprintf(call, sizeof(call), "clGetDeviceInfo(%s)", label);
    stage(call);
    error = api->clGetDeviceInfo(device, info, size, value, NULL);
    return check_error(call, error);
}

static int capability_self_test(void)
{
    numeric_capabilities values;

    if (sizeof(cl_ulong) != 8 || sizeof(cl_bitfield) != 8 ||
        sizeof(cl_uint) != 4 || sizeof(cl_bool) != 4)
        return 1;
    memset(&values, 0, sizeof(values));
    values.global_memory_bytes = 8ULL * 1024ULL * 1024ULL * 1024ULL;
    values.max_alloc_bytes = 2ULL * 1024ULL * 1024ULL * 1024ULL;
    values.local_memory_bytes = 64ULL * 1024ULL;
    values.compute_units = 4;
    values.address_bits = 64;
    values.unified_memory = CAP_CL_TRUE;
    values.half_fp_config = 1;
    if (values.global_memory_bytes <= values.max_alloc_bytes ||
        values.local_memory_bytes == 0 || values.compute_units == 0 ||
        values.address_bits != 64 || values.unified_memory != CAP_CL_TRUE ||
        values.half_fp_config == 0)
        return 1;
    if (!extension_token("cl_khr_fp16 cl_khr_subgroups", "cl_khr_fp16") ||
        extension_token("cl_khr_fp16x cl_khr_subgroups", "cl_khr_fp16") ||
        !extension_token("cl_khr_fp16", "cl_khr_fp16"))
        return 1;
    printf("SELF_TEST PASS capability-layout=ABI-widths extension-token-boundary\n");
    fflush(stdout);
    return 0;
}

static int capability_arguments(int argc, char **argv, const char **library_path)
{
    int index;

    *library_path = "libSGPUOpenCL.so";
    if (argc < 2 || strcmp(argv[1], "--capabilities") != 0)
        return -1;
    index = 2;
    while (index < argc) {
        if (strcmp(argv[index], "--library") != 0 ||
            index + 1 >= argc || argv[index + 1][0] == '\0')
            return -1;
        *library_path = argv[index + 1];
        index += 2;
    }
    return 0;
}

static void capability_usage(const char *program)
{
    fprintf(stderr, "usage: %s --self-test | --capabilities [--library PATH]\n", program);
}

int main(int argc, char **argv)
{
    const char *library_path;
    void *library;
    opencl_api api;
    selected_device selected;
    numeric_capabilities values;
    char *extensions = NULL;
    int argument_result;
    int fp16_extension_supported;
    int result;

    if (argc == 2 && strcmp(argv[1], "--self-test") == 0)
        return capability_self_test();
    argument_result = capability_arguments(argc, argv, &library_path);
    if (argument_result != 0) {
        capability_usage(argv[0]);
        return 2;
    }

    printf("OPENCL_LIBRARY path=%s\n", library_path);
    printf("WARNING load executes vendor ELF constructors; this is not a dry-run\n");
    fflush(stdout);
    stage("dlopen");
    library = dlopen(library_path, RTLD_NOW | RTLD_LOCAL);
    if (library == NULL) {
        fprintf(stderr, "DLOPEN_ERROR path=%s detail=%s\n", library_path, dlerror());
        return 1;
    }
    if (load_api(library, &api) != 0) {
        fprintf(stderr, "EXPORTS_INCOMPLETE\n");
        stage("dlclose(exports-incomplete)");
        (void)dlclose(library);
        return 1;
    }
    result = select_xclipse_gpu(&api, &selected);
    if (result != 0) {
        stage("dlclose(selection-failed)");
        (void)dlclose(library);
        return result;
    }
    memset(&values, 0, sizeof(values));
    if (device_scalar(&api, selected.device, CL_DEVICE_GLOBAL_MEM_SIZE,
                      "CL_DEVICE_GLOBAL_MEM_SIZE", sizeof(values.global_memory_bytes),
                      &values.global_memory_bytes) != 0 ||
        device_scalar(&api, selected.device, CL_DEVICE_MAX_MEM_ALLOC_SIZE,
                      "CL_DEVICE_MAX_MEM_ALLOC_SIZE", sizeof(values.max_alloc_bytes),
                      &values.max_alloc_bytes) != 0 ||
        device_scalar(&api, selected.device, CL_DEVICE_LOCAL_MEM_SIZE,
                      "CL_DEVICE_LOCAL_MEM_SIZE", sizeof(values.local_memory_bytes),
                      &values.local_memory_bytes) != 0 ||
        device_scalar(&api, selected.device, CL_DEVICE_MAX_COMPUTE_UNITS,
                      "CL_DEVICE_MAX_COMPUTE_UNITS", sizeof(values.compute_units),
                      &values.compute_units) != 0 ||
        device_scalar(&api, selected.device, CL_DEVICE_ADDRESS_BITS,
                      "CL_DEVICE_ADDRESS_BITS", sizeof(values.address_bits),
                      &values.address_bits) != 0 ||
        device_scalar(&api, selected.device, CL_DEVICE_HOST_UNIFIED_MEMORY,
                      "CL_DEVICE_HOST_UNIFIED_MEMORY", sizeof(values.unified_memory),
                      &values.unified_memory) != 0 ||
        get_device_string(&api, selected.device, CL_DEVICE_EXTENSIONS,
                          "CL_DEVICE_EXTENSIONS", &extensions) != 0) {
        free(extensions);
        free_selected_device(&selected);
        stage("dlclose(capability-query-failed)");
        (void)dlclose(library);
        return 1;
    }
    if (extension_token(extensions, "cl_khr_fp16") &&
        device_scalar(&api, selected.device, CL_DEVICE_HALF_FP_CONFIG,
                      "CL_DEVICE_HALF_FP_CONFIG", sizeof(values.half_fp_config),
                      &values.half_fp_config) != 0) {
        free(extensions);
        free_selected_device(&selected);
        stage("dlclose(fp16-query-failed)");
        (void)dlclose(library);
        return 1;
    }
    fp16_extension_supported = extension_token(extensions, "cl_khr_fp16") &&
                   values.half_fp_config != 0;
    printf("capability_global_memory_bytes=%llu\n",
           (unsigned long long)values.global_memory_bytes);
    printf("capability_max_alloc_bytes=%llu\n",
           (unsigned long long)values.max_alloc_bytes);
    printf("capability_local_memory_bytes=%llu\n",
           (unsigned long long)values.local_memory_bytes);
    printf("capability_compute_units=%u\n", values.compute_units);
    printf("capability_address_bits=%u\n", values.address_bits);
    printf("capability_host_unified_memory=%u\n", values.unified_memory);
    printf("capability_half_fp_config=0x%llx\n",
           (unsigned long long)values.half_fp_config);
    printf("capability_fp16_extension_supported=%u\n", fp16_extension_supported);
    printf("capability_driver_version=%s\n", selected.driver_version);
    printf("capability_device_version=%s\n", selected.device_version);
    printf("capability_extensions=%s\n", extensions);
    fflush(stdout);
    free(extensions);
    free_selected_device(&selected);
    stage("dlclose(capabilities-complete)");
    (void)dlclose(library);
    return 0;
}
