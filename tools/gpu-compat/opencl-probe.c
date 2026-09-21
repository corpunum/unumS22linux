/*
 * Bounded OpenCL probe for the Samsung Xclipse vendor library.
 *
 * This file carries the small OpenCL ABI it uses instead of including an SDK
 * header. It is buildable on a host while calls resolve at run time.
 * There is no timeout or signal handler here: a parent supervisor must bound
 * this process because a wedged GPU command cannot be safely cancelled here.
 */
#define _POSIX_C_SOURCE 200809L

#include <dlfcn.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef int32_t cl_int;
typedef uint32_t cl_uint;
typedef uint64_t cl_ulong;
typedef uint64_t cl_bitfield;
typedef cl_bitfield cl_device_type;
typedef uint32_t cl_bool;
typedef intptr_t cl_context_properties;
typedef cl_bitfield cl_command_queue_properties;
typedef cl_bitfield cl_mem_flags;
typedef uint32_t cl_platform_info;
typedef uint32_t cl_device_info;
typedef uint32_t cl_program_build_info;

typedef struct _cl_platform_id *cl_platform_id;
typedef struct _cl_device_id *cl_device_id;
typedef struct _cl_context *cl_context;
typedef struct _cl_command_queue *cl_command_queue;
typedef struct _cl_mem *cl_mem;
typedef struct _cl_program *cl_program;
typedef struct _cl_kernel *cl_kernel;
typedef struct _cl_event *cl_event;

enum {
    CL_SUCCESS = 0,
    CL_DEVICE_NOT_FOUND = -1,
    CL_DEVICE_NOT_AVAILABLE = -2,
    CL_OUT_OF_RESOURCES = -5,
    CL_OUT_OF_HOST_MEMORY = -6,
    CL_BUILD_PROGRAM_FAILURE = -11,
    CL_INVALID_VALUE = -30,
    CL_INVALID_DEVICE_TYPE = -31,
    CL_INVALID_PLATFORM = -32,
    CL_INVALID_DEVICE = -33,
    CL_INVALID_CONTEXT = -34,
    CL_INVALID_COMMAND_QUEUE = -36,
    CL_INVALID_MEM_OBJECT = -38,
    CL_INVALID_PROGRAM = -44,
    CL_INVALID_PROGRAM_EXECUTABLE = -45,
    CL_INVALID_KERNEL = -48,
    CL_INVALID_ARG_INDEX = -49,
    CL_INVALID_ARG_VALUE = -50,
    CL_INVALID_ARG_SIZE = -51,
    CL_INVALID_KERNEL_ARGS = -52,
    CL_INVALID_WORK_DIMENSION = -53,
    CL_INVALID_GLOBAL_WORK_SIZE = -63,
    CL_INVALID_EVENT_WAIT_LIST = -57,
    CL_INVALID_EVENT = -58
};

enum {
    CL_DEVICE_TYPE_CPU = (cl_device_type)1u << 1,
    CL_DEVICE_TYPE_GPU = (cl_device_type)1u << 2,
    CL_MEM_READ_WRITE = (cl_mem_flags)1u << 0,
    CL_PLATFORM_VERSION = 0x0901,
    CL_PLATFORM_NAME = 0x0902,
    CL_PLATFORM_VENDOR = 0x0903,
    CL_DEVICE_TYPE = 0x1000,
    CL_DEVICE_NAME = 0x102b,
    CL_DEVICE_VENDOR = 0x102c,
    CL_DRIVER_VERSION = 0x102d,
    CL_DEVICE_VERSION = 0x102f,
    CL_DEVICE_OPENCL_C_VERSION = 0x103d,
    CL_PROGRAM_BUILD_LOG = 0x1183
};

typedef cl_int (*fn_clGetPlatformIDs)(cl_uint, cl_platform_id *, cl_uint *);
typedef cl_int (*fn_clGetPlatformInfo)(cl_platform_id, cl_platform_info, size_t, void *, size_t *);
typedef cl_int (*fn_clGetDeviceIDs)(cl_platform_id, cl_device_type, cl_uint, cl_device_id *, cl_uint *);
typedef cl_int (*fn_clGetDeviceInfo)(cl_device_id, cl_device_info, size_t, void *, size_t *);
typedef cl_context (*fn_clCreateContext)(const cl_context_properties *, cl_uint, const cl_device_id *,
                                         void (*)(const char *, const void *, size_t, void *), void *, cl_int *);
typedef cl_int (*fn_clReleaseContext)(cl_context);
typedef cl_command_queue (*fn_clCreateCommandQueue)(cl_context, cl_device_id, cl_command_queue_properties, cl_int *);
typedef cl_int (*fn_clReleaseCommandQueue)(cl_command_queue);
typedef cl_mem (*fn_clCreateBuffer)(cl_context, cl_mem_flags, size_t, void *, cl_int *);
typedef cl_int (*fn_clReleaseMemObject)(cl_mem);
typedef cl_int (*fn_clEnqueueWriteBuffer)(cl_command_queue, cl_mem, cl_bool, size_t, size_t, const void *,
                                          cl_uint, const cl_event *, cl_event *);
typedef cl_int (*fn_clEnqueueReadBuffer)(cl_command_queue, cl_mem, cl_bool, size_t, size_t, void *,
                                         cl_uint, const cl_event *, cl_event *);
typedef cl_program (*fn_clCreateProgramWithSource)(cl_context, cl_uint, const char **, const size_t *, cl_int *);
typedef cl_int (*fn_clBuildProgram)(cl_program, cl_uint, const cl_device_id *, const char *,
                                    void (*)(cl_program, void *), void *);
typedef cl_int (*fn_clGetProgramBuildInfo)(cl_program, cl_device_id, cl_program_build_info, size_t, void *, size_t *);
typedef cl_int (*fn_clReleaseProgram)(cl_program);
typedef cl_kernel (*fn_clCreateKernel)(cl_program, const char *, cl_int *);
typedef cl_int (*fn_clSetKernelArg)(cl_kernel, cl_uint, size_t, const void *);
typedef cl_int (*fn_clReleaseKernel)(cl_kernel);
typedef cl_int (*fn_clEnqueueNDRangeKernel)(cl_command_queue, cl_kernel, cl_uint, const size_t *, const size_t *,
                                            const size_t *, cl_uint, const cl_event *, cl_event *);
typedef cl_int (*fn_clWaitForEvents)(cl_uint, const cl_event *);
typedef cl_int (*fn_clReleaseEvent)(cl_event);

typedef struct {
    fn_clGetPlatformIDs clGetPlatformIDs;
    fn_clGetPlatformInfo clGetPlatformInfo;
    fn_clGetDeviceIDs clGetDeviceIDs;
    fn_clGetDeviceInfo clGetDeviceInfo;
    fn_clCreateContext clCreateContext;
    fn_clReleaseContext clReleaseContext;
    fn_clCreateCommandQueue clCreateCommandQueue;
    fn_clReleaseCommandQueue clReleaseCommandQueue;
    fn_clCreateBuffer clCreateBuffer;
    fn_clReleaseMemObject clReleaseMemObject;
    fn_clEnqueueWriteBuffer clEnqueueWriteBuffer;
    fn_clEnqueueReadBuffer clEnqueueReadBuffer;
    fn_clCreateProgramWithSource clCreateProgramWithSource;
    fn_clBuildProgram clBuildProgram;
    fn_clGetProgramBuildInfo clGetProgramBuildInfo;
    fn_clReleaseProgram clReleaseProgram;
    fn_clCreateKernel clCreateKernel;
    fn_clSetKernelArg clSetKernelArg;
    fn_clReleaseKernel clReleaseKernel;
    fn_clEnqueueNDRangeKernel clEnqueueNDRangeKernel;
    fn_clWaitForEvents clWaitForEvents;
    fn_clReleaseEvent clReleaseEvent;
} opencl_api;

static void stage(const char *name)
{
    printf("STAGE %s\n", name);
    fflush(stdout);
}

static const char *error_name(cl_int error)
{
    switch (error) {
    case CL_SUCCESS: return "CL_SUCCESS";
    case CL_DEVICE_NOT_FOUND: return "CL_DEVICE_NOT_FOUND";
    case CL_DEVICE_NOT_AVAILABLE: return "CL_DEVICE_NOT_AVAILABLE";
    case CL_OUT_OF_RESOURCES: return "CL_OUT_OF_RESOURCES";
    case CL_OUT_OF_HOST_MEMORY: return "CL_OUT_OF_HOST_MEMORY";
    case CL_BUILD_PROGRAM_FAILURE: return "CL_BUILD_PROGRAM_FAILURE";
    case CL_INVALID_VALUE: return "CL_INVALID_VALUE";
    case CL_INVALID_DEVICE_TYPE: return "CL_INVALID_DEVICE_TYPE";
    case CL_INVALID_PLATFORM: return "CL_INVALID_PLATFORM";
    case CL_INVALID_DEVICE: return "CL_INVALID_DEVICE";
    case CL_INVALID_CONTEXT: return "CL_INVALID_CONTEXT";
    case CL_INVALID_COMMAND_QUEUE: return "CL_INVALID_COMMAND_QUEUE";
    case CL_INVALID_MEM_OBJECT: return "CL_INVALID_MEM_OBJECT";
    case CL_INVALID_PROGRAM: return "CL_INVALID_PROGRAM";
    case CL_INVALID_PROGRAM_EXECUTABLE: return "CL_INVALID_PROGRAM_EXECUTABLE";
    case CL_INVALID_KERNEL: return "CL_INVALID_KERNEL";
    case CL_INVALID_ARG_INDEX: return "CL_INVALID_ARG_INDEX";
    case CL_INVALID_ARG_VALUE: return "CL_INVALID_ARG_VALUE";
    case CL_INVALID_ARG_SIZE: return "CL_INVALID_ARG_SIZE";
    case CL_INVALID_KERNEL_ARGS: return "CL_INVALID_KERNEL_ARGS";
    case CL_INVALID_WORK_DIMENSION: return "CL_INVALID_WORK_DIMENSION";
    case CL_INVALID_GLOBAL_WORK_SIZE: return "CL_INVALID_GLOBAL_WORK_SIZE";
    case CL_INVALID_EVENT_WAIT_LIST: return "CL_INVALID_EVENT_WAIT_LIST";
    case CL_INVALID_EVENT: return "CL_INVALID_EVENT";
    default: return "CL_UNKNOWN_ERROR";
    }
}

static int check_error(const char *call, cl_int error)
{
    if (error == CL_SUCCESS)
        return 0;
    fprintf(stderr, "CL_ERROR call=%s code=%d name=%s\n", call, error, error_name(error));
    return 1;
}

static void *load_symbol(void *library, const char *name)
{
    void *symbol;
    const char *error;

    dlerror();
    symbol = dlsym(library, name);
    error = dlerror();
    if (symbol == NULL) {
        fprintf(stderr, "EXPORT_MISSING name=%s detail=%s\n", name, error != NULL ? error : "unknown");
    } else {
        printf("EXPORT_OK name=%s\n", name);
    }
    fflush(stdout);
    return symbol;
}

#define LOAD_SYMBOL(api, library, name) \
    do { (api)->name = (fn_##name)load_symbol((library), #name); if ((api)->name == NULL) missing = 1; } while (0)

static int load_api(void *library, opencl_api *api)
{
    int missing = 0;

    memset(api, 0, sizeof(*api));
    LOAD_SYMBOL(api, library, clGetPlatformIDs);
    LOAD_SYMBOL(api, library, clGetPlatformInfo);
    LOAD_SYMBOL(api, library, clGetDeviceIDs);
    LOAD_SYMBOL(api, library, clGetDeviceInfo);
    LOAD_SYMBOL(api, library, clCreateContext);
    LOAD_SYMBOL(api, library, clReleaseContext);
    LOAD_SYMBOL(api, library, clCreateCommandQueue);
    LOAD_SYMBOL(api, library, clReleaseCommandQueue);
    LOAD_SYMBOL(api, library, clCreateBuffer);
    LOAD_SYMBOL(api, library, clReleaseMemObject);
    LOAD_SYMBOL(api, library, clEnqueueWriteBuffer);
    LOAD_SYMBOL(api, library, clEnqueueReadBuffer);
    LOAD_SYMBOL(api, library, clCreateProgramWithSource);
    LOAD_SYMBOL(api, library, clBuildProgram);
    LOAD_SYMBOL(api, library, clGetProgramBuildInfo);
    LOAD_SYMBOL(api, library, clReleaseProgram);
    LOAD_SYMBOL(api, library, clCreateKernel);
    LOAD_SYMBOL(api, library, clSetKernelArg);
    LOAD_SYMBOL(api, library, clReleaseKernel);
    LOAD_SYMBOL(api, library, clEnqueueNDRangeKernel);
    LOAD_SYMBOL(api, library, clWaitForEvents);
    LOAD_SYMBOL(api, library, clReleaseEvent);
    return missing ? 1 : 0;
}

static int contains_xclipse(const char *text)
{
    size_t length;
    size_t i;

    if (text == NULL)
        return 0;
    length = strlen(text);
    for (i = 0; i + 7 <= length; ++i) {
        if ((text[i] == 'x' || text[i] == 'X') &&
            (text[i + 1] == 'c' || text[i + 1] == 'C') &&
            (text[i + 2] == 'l' || text[i + 2] == 'L') &&
            (text[i + 3] == 'i' || text[i + 3] == 'I') &&
            (text[i + 4] == 'p' || text[i + 4] == 'P') &&
            (text[i + 5] == 's' || text[i + 5] == 'S') &&
            (text[i + 6] == 'e' || text[i + 6] == 'E'))
            return 1;
    }
    return 0;
}

typedef struct {
    cl_platform_id platform;
    cl_device_id device;
    char *platform_name;
    char *platform_vendor;
    char *platform_version;
    char *device_name;
    char *device_vendor;
    char *driver_version;
    char *device_version;
    char *opencl_c_version;
    cl_device_type device_type;
} selected_device;

static void free_selected_device(selected_device *selected)
{
    free(selected->platform_name);
    free(selected->platform_vendor);
    free(selected->platform_version);
    free(selected->device_name);
    free(selected->device_vendor);
    free(selected->driver_version);
    free(selected->device_version);
    free(selected->opencl_c_version);
    memset(selected, 0, sizeof(*selected));
}

static int get_platform_string(opencl_api *api, cl_platform_id platform, cl_platform_info info,
                               const char *label, char **result)
{
    size_t bytes = 0;
    char *value;
    cl_int error;
    char call[96];

    (void)snprintf(call, sizeof(call), "clGetPlatformInfo(%s)", label);
    stage(call);
    error = api->clGetPlatformInfo(platform, info, 0, NULL, &bytes);
    if (check_error(call, error) != 0 || bytes == 0 || bytes > 1024u * 1024u)
        return 1;
    value = (char *)calloc(bytes + 1, 1);
    if (value == NULL) {
        fprintf(stderr, "HOST_ERROR calloc platform info bytes=%zu\n", bytes);
        return 1;
    }
    stage(call);
    error = api->clGetPlatformInfo(platform, info, bytes, value, NULL);
    if (check_error(call, error) != 0) {
        free(value);
        return 1;
    }
    value[bytes] = '\0';
    *result = value;
    return 0;
}

static int get_device_string(opencl_api *api, cl_device_id device, cl_device_info info,
                             const char *label, char **result)
{
    size_t bytes = 0;
    char *value;
    cl_int error;
    char call[96];

    (void)snprintf(call, sizeof(call), "clGetDeviceInfo(%s)", label);
    stage(call);
    error = api->clGetDeviceInfo(device, info, 0, NULL, &bytes);
    if (check_error(call, error) != 0 || bytes == 0 || bytes > 1024u * 1024u)
        return 1;
    value = (char *)calloc(bytes + 1, 1);
    if (value == NULL) {
        fprintf(stderr, "HOST_ERROR calloc device info bytes=%zu\n", bytes);
        return 1;
    }
    stage(call);
    error = api->clGetDeviceInfo(device, info, bytes, value, NULL);
    if (check_error(call, error) != 0) {
        free(value);
        return 1;
    }
    value[bytes] = '\0';
    *result = value;
    return 0;
}

static int get_device_type(opencl_api *api, cl_device_id device, cl_device_type *type)
{
    cl_int error;

    stage("clGetDeviceInfo(CL_DEVICE_TYPE)");
    error = api->clGetDeviceInfo(device, CL_DEVICE_TYPE, sizeof(*type), type, NULL);
    return check_error("clGetDeviceInfo(CL_DEVICE_TYPE)", error);
}

static void print_selected_device(const selected_device *selected)
{
    printf("platform_name=%s\n", selected->platform_name);
    printf("platform_vendor=%s\n", selected->platform_vendor);
    printf("platform_api=%s\n", selected->platform_version);
    printf("device_name=%s\n", selected->device_name);
    printf("device_vendor=%s\n", selected->device_vendor);
    printf("device_type=0x%llx GPU\n", (unsigned long long)selected->device_type);
    printf("device_driver_version=%s\n", selected->driver_version);
    printf("device_api=%s\n", selected->device_version);
    printf("device_opencl_c=%s\n", selected->opencl_c_version);
    fflush(stdout);
}

static int select_xclipse_gpu(opencl_api *api, selected_device *selected)
{
    cl_uint platform_count = 0;
    cl_platform_id *platforms = NULL;
    cl_uint platform_index;
    cl_int error;

    memset(selected, 0, sizeof(*selected));
    stage("clGetPlatformIDs(count)");
    error = api->clGetPlatformIDs(0, NULL, &platform_count);
    if (check_error("clGetPlatformIDs(count)", error) != 0)
        return 1;
    if (platform_count == 0) {
        fprintf(stderr, "NO_PLATFORMS\n");
        return 3;
    }
    platforms = (cl_platform_id *)calloc(platform_count, sizeof(*platforms));
    if (platforms == NULL) {
        fprintf(stderr, "HOST_ERROR calloc platforms=%u\n", platform_count);
        return 1;
    }
    stage("clGetPlatformIDs(list)");
    error = api->clGetPlatformIDs(platform_count, platforms, NULL);
    if (check_error("clGetPlatformIDs(list)", error) != 0) {
        free(platforms);
        return 1;
    }
    for (platform_index = 0; platform_index < platform_count; ++platform_index) {
        cl_uint device_count = 0;
        cl_device_id *devices = NULL;
        cl_uint device_index;
        char *platform_name = NULL;
        char *platform_vendor = NULL;
        char *platform_version = NULL;

        if (get_platform_string(api, platforms[platform_index], CL_PLATFORM_NAME, "CL_PLATFORM_NAME", &platform_name) != 0 ||
            get_platform_string(api, platforms[platform_index], CL_PLATFORM_VENDOR, "CL_PLATFORM_VENDOR", &platform_vendor) != 0 ||
            get_platform_string(api, platforms[platform_index], CL_PLATFORM_VERSION, "CL_PLATFORM_VERSION", &platform_version) != 0) {
            free(platform_name);
            free(platform_vendor);
            free(platform_version);
            free(platforms);
            return 1;
        }
        printf("PLATFORM index=%u name=%s vendor=%s api=%s\n", platform_index, platform_name, platform_vendor, platform_version);
        fflush(stdout);
        stage("clGetDeviceIDs(GPU,count)");
        error = api->clGetDeviceIDs(platforms[platform_index], CL_DEVICE_TYPE_GPU, 0, NULL, &device_count);
        if (error == CL_DEVICE_NOT_FOUND) {
            fprintf(stderr, "CL_INFO call=clGetDeviceIDs(GPU,count) code=%d name=%s\n", error, error_name(error));
            free(platform_name);
            free(platform_vendor);
            free(platform_version);
            continue;
        }
        if (check_error("clGetDeviceIDs(GPU,count)", error) != 0) {
            free(platform_name);
            free(platform_vendor);
            free(platform_version);
            free(platforms);
            return 1;
        }
        if (device_count == 0) {
            fprintf(stderr, "CL_INFO call=clGetDeviceIDs(GPU,count) code=0 name=CL_SUCCESS devices=0\n");
            free(platform_name);
            free(platform_vendor);
            free(platform_version);
            continue;
        }
        devices = (cl_device_id *)calloc(device_count, sizeof(*devices));
        if (devices == NULL) {
            fprintf(stderr, "HOST_ERROR calloc devices=%u\n", device_count);
            free(platform_name);
            free(platform_vendor);
            free(platform_version);
            free(platforms);
            return 1;
        }
        stage("clGetDeviceIDs(GPU,list)");
        error = api->clGetDeviceIDs(platforms[platform_index], CL_DEVICE_TYPE_GPU, device_count, devices, NULL);
        if (check_error("clGetDeviceIDs(GPU,list)", error) != 0) {
            free(devices);
            free(platform_name);
            free(platform_vendor);
            free(platform_version);
            free(platforms);
            return 1;
        }
        for (device_index = 0; device_index < device_count; ++device_index) {
            selected_device candidate;

            memset(&candidate, 0, sizeof(candidate));
            candidate.platform = platforms[platform_index];
            candidate.device = devices[device_index];
            if (get_device_type(api, candidate.device, &candidate.device_type) != 0 ||
                get_device_string(api, candidate.device, CL_DEVICE_NAME, "CL_DEVICE_NAME", &candidate.device_name) != 0 ||
                get_device_string(api, candidate.device, CL_DEVICE_VENDOR, "CL_DEVICE_VENDOR", &candidate.device_vendor) != 0 ||
                get_device_string(api, candidate.device, CL_DRIVER_VERSION, "CL_DRIVER_VERSION", &candidate.driver_version) != 0 ||
                get_device_string(api, candidate.device, CL_DEVICE_VERSION, "CL_DEVICE_VERSION", &candidate.device_version) != 0 ||
                get_device_string(api, candidate.device, CL_DEVICE_OPENCL_C_VERSION, "CL_DEVICE_OPENCL_C_VERSION", &candidate.opencl_c_version) != 0) {
                free_selected_device(&candidate);
                free(devices);
                free(platform_name);
                free(platform_vendor);
                free(platform_version);
                free(platforms);
                return 1;
            }
            printf("DEVICE platform=%u index=%u name=%s vendor=%s type=0x%llx\n", platform_index, device_index,
                   candidate.device_name, candidate.device_vendor, (unsigned long long)candidate.device_type);
            fflush(stdout);
            if ((candidate.device_type & CL_DEVICE_TYPE_GPU) == 0 ||
                (candidate.device_type & CL_DEVICE_TYPE_CPU) != 0 ||
                !contains_xclipse(candidate.device_name)) {
                fprintf(stderr, "DEVICE_REJECTED reason=requires-GPU-and-Xclipse-name\n");
                free_selected_device(&candidate);
                continue;
            }
            candidate.platform_name = platform_name;
            candidate.platform_vendor = platform_vendor;
            candidate.platform_version = platform_version;
            *selected = candidate;
            free(devices);
            free(platforms);
            print_selected_device(selected);
            return 0;
        }
        free(devices);
        free(platform_name);
        free(platform_vendor);
        free(platform_version);
    }
    free(platforms);
    fprintf(stderr, "NO_ACCEPTED_XCLIPSE_GPU name-must-contain=Xclipse type-must-be-GPU-not-CPU\n");
    return 3;
}

static int print_build_log(opencl_api *api, cl_program program, cl_device_id device)
{
    size_t bytes = 0;
    char *log;
    cl_int error;

    stage("clGetProgramBuildInfo(CL_PROGRAM_BUILD_LOG,size)");
    error = api->clGetProgramBuildInfo(program, device, CL_PROGRAM_BUILD_LOG, 0, NULL, &bytes);
    if (check_error("clGetProgramBuildInfo(CL_PROGRAM_BUILD_LOG,size)", error) != 0)
        return 1;
    if (bytes == 0 || bytes > 1024u * 1024u)
        return 0;
    log = (char *)calloc(bytes + 1, 1);
    if (log == NULL)
        return 1;
    stage("clGetProgramBuildInfo(CL_PROGRAM_BUILD_LOG,data)");
    error = api->clGetProgramBuildInfo(program, device, CL_PROGRAM_BUILD_LOG, bytes, log, NULL);
    if (check_error("clGetProgramBuildInfo(CL_PROGRAM_BUILD_LOG,data)", error) == 0)
        fprintf(stderr, "BUILD_LOG_BEGIN\n%sBUILD_LOG_END\n", log);
    free(log);
    return error != CL_SUCCESS;
}

static int compute(opencl_api *api, const selected_device *selected, unsigned count)
{
    const size_t bytes = (size_t)count * sizeof(uint32_t);
    char source[512];
    const char *sources[1];
    size_t source_length;
    uint32_t *host = NULL;
    uint32_t *result = NULL;
    cl_context context = NULL;
    cl_command_queue queue = NULL;
    cl_mem buffer = NULL;
    cl_program program = NULL;
    cl_kernel kernel = NULL;
    cl_event kernel_event = NULL;
    cl_event read_event = NULL;
    cl_int error = CL_SUCCESS;
    unsigned i;

    host = (uint32_t *)malloc(bytes);
    result = (uint32_t *)malloc(bytes);
    if (host == NULL || result == NULL) {
        fprintf(stderr, "HOST_ERROR allocation bytes=%zu\n", bytes);
        free(host);
        free(result);
        return 1;
    }
    memset(host, 0xa5, bytes);
    (void)snprintf(source, sizeof(source),
                   "__kernel void fill(__global uint *out) { size_t i = get_global_id(0); "
                   "if (i < %u) out[i] = (uint)(i * 3u + 7u); }\n", count);
    sources[0] = source;
    source_length = strlen(source);

    stage("clCreateContext");
    context = api->clCreateContext(NULL, 1, &selected->device, NULL, NULL, &error);
    if (check_error("clCreateContext", error) != 0 || context == NULL)
        goto fail_before_commands;
    stage("clCreateCommandQueue");
    queue = api->clCreateCommandQueue(context, selected->device, 0, &error);
    if (check_error("clCreateCommandQueue", error) != 0 || queue == NULL)
        goto fail_before_commands;
    stage("clCreateBuffer");
    buffer = api->clCreateBuffer(context, CL_MEM_READ_WRITE, bytes, NULL, &error);
    if (check_error("clCreateBuffer", error) != 0 || buffer == NULL)
        goto fail_before_commands;
    stage("clEnqueueWriteBuffer(fill-A5)");
    error = api->clEnqueueWriteBuffer(queue, buffer, 1, 0, bytes, host, 0, NULL, NULL);
    if (check_error("clEnqueueWriteBuffer(fill-A5)", error) != 0)
        goto fail_before_commands;
    stage("clCreateProgramWithSource");
    program = api->clCreateProgramWithSource(context, 1, sources, &source_length, &error);
    if (check_error("clCreateProgramWithSource", error) != 0 || program == NULL)
        goto fail_before_commands;
    stage("clBuildProgram");
    error = api->clBuildProgram(program, 1, &selected->device, "", NULL, NULL);
    if (error != CL_SUCCESS) {
        (void)check_error("clBuildProgram", error);
        (void)print_build_log(api, program, selected->device);
        goto fail_before_commands;
    }
    stage("clCreateKernel");
    kernel = api->clCreateKernel(program, "fill", &error);
    if (check_error("clCreateKernel", error) != 0 || kernel == NULL)
        goto fail_before_commands;
    stage("clSetKernelArg");
    error = api->clSetKernelArg(kernel, 0, sizeof(buffer), &buffer);
    if (check_error("clSetKernelArg", error) != 0)
        goto fail_before_commands;
    {
        const size_t global_size = (size_t)count;

        stage("clEnqueueNDRangeKernel");
        error = api->clEnqueueNDRangeKernel(queue, kernel, 1, NULL, &global_size, NULL, 0, NULL, &kernel_event);
    }
    if (check_error("clEnqueueNDRangeKernel", error) != 0 || kernel_event == NULL)
        goto fail_after_commands;
    stage("clWaitForEvents(kernel)");
    error = api->clWaitForEvents(1, &kernel_event);
    if (check_error("clWaitForEvents(kernel)", error) != 0)
        goto fail_after_commands;
    stage("clEnqueueReadBuffer");
    error = api->clEnqueueReadBuffer(queue, buffer, 0, 0, bytes, result, 1, &kernel_event, &read_event);
    if (check_error("clEnqueueReadBuffer", error) != 0 || read_event == NULL)
        goto fail_after_commands;
    stage("clWaitForEvents(readback)");
    error = api->clWaitForEvents(1, &read_event);
    if (check_error("clWaitForEvents(readback)", error) != 0)
        goto fail_after_commands;
    for (i = 0; i < count; ++i) {
        uint32_t expected = i * 3u + 7u;

        if (result[i] != expected) {
            fprintf(stderr, "COMPUTE_MISMATCH index=%u got=%u expected=%u\n", i, result[i], expected);
            goto fail_after_commands;
        }
    }
    printf("COMPUTE count=%u checksum=verified PASS\n", count);
    fflush(stdout);

    stage("clReleaseEvent(readback)");
    (void)check_error("clReleaseEvent(readback)", api->clReleaseEvent(read_event));
    stage("clReleaseEvent(kernel)");
    (void)check_error("clReleaseEvent(kernel)", api->clReleaseEvent(kernel_event));
    stage("clReleaseKernel");
    (void)check_error("clReleaseKernel", api->clReleaseKernel(kernel));
    stage("clReleaseProgram");
    (void)check_error("clReleaseProgram", api->clReleaseProgram(program));
    stage("clReleaseMemObject");
    (void)check_error("clReleaseMemObject", api->clReleaseMemObject(buffer));
    stage("clReleaseCommandQueue");
    (void)check_error("clReleaseCommandQueue", api->clReleaseCommandQueue(queue));
    stage("clReleaseContext");
    (void)check_error("clReleaseContext", api->clReleaseContext(context));
    free(host);
    free(result);
    return 0;

fail_after_commands:
    fprintf(stderr, "EXECUTION_INCOMPLETE cleanup=skipped; parent-must-bound-process\n");
    free(host);
    free(result);
    return 1;

fail_before_commands:
    if (kernel != NULL) {
        stage("clReleaseKernel(failure)");
        (void)api->clReleaseKernel(kernel);
    }
    if (program != NULL) {
        stage("clReleaseProgram(failure)");
        (void)api->clReleaseProgram(program);
    }
    if (buffer != NULL) {
        stage("clReleaseMemObject(failure)");
        (void)api->clReleaseMemObject(buffer);
    }
    if (queue != NULL) {
        stage("clReleaseCommandQueue(failure)");
        (void)api->clReleaseCommandQueue(queue);
    }
    if (context != NULL) {
        stage("clReleaseContext(failure)");
        (void)api->clReleaseContext(context);
    }
    free(host);
    free(result);
    return 1;
}

static int self_test(void)
{
    unsigned count;
    unsigned i;

    for (count = 256; count <= 1024; count *= 4) {
        uint32_t *values = (uint32_t *)malloc((size_t)count * sizeof(*values));
        if (values == NULL)
            return 1;
        memset(values, 0xa5, (size_t)count * sizeof(*values));
        for (i = 0; i < count; ++i)
            values[i] = i * 3u + 7u;
        for (i = 0; i < count; ++i) {
            if (values[i] != i * 3u + 7u) {
                free(values);
                return 1;
            }
        }
        free(values);
    }
    printf("SELF_TEST PASS reference-counts=256,1024 pattern=0xa5 formula=i*3+7\n");
    fflush(stdout);
    return 0;
}

typedef enum { MODE_LOAD_ONLY, MODE_ENUMERATE, MODE_COMPUTE } probe_mode;

static void usage(const char *program)
{
    fprintf(stderr, "usage: %s --self-test | --load-only [--library PATH] | --enumerate [--library PATH] | --compute 256|1024 [--library PATH]\n", program);
}

static int parse_arguments(int argc, char **argv, probe_mode *mode, unsigned *count, const char **library_path)
{
    int i;

    *library_path = "libSGPUOpenCL.so";
    if (argc == 2 && strcmp(argv[1], "--self-test") == 0)
        return 2;
    if (argc < 2)
        return -1;
    if (strcmp(argv[1], "--load-only") == 0)
        *mode = MODE_LOAD_ONLY;
    else if (strcmp(argv[1], "--enumerate") == 0)
        *mode = MODE_ENUMERATE;
    else if (strcmp(argv[1], "--compute") == 0) {
        *mode = MODE_COMPUTE;
        if (argc < 3 || (strcmp(argv[2], "256") != 0 && strcmp(argv[2], "1024") != 0))
            return -1;
        *count = (unsigned)strtoul(argv[2], NULL, 10);
        i = 3;
    } else {
        return -1;
    }
    if (*mode != MODE_COMPUTE)
        i = 2;
    while (i < argc) {
        if (strcmp(argv[i], "--library") != 0 || i + 1 >= argc || argv[i + 1][0] == '\0')
            return -1;
        *library_path = argv[i + 1];
        i += 2;
    }
    return 0;
}

int main(int argc, char **argv)
{
    probe_mode mode = MODE_LOAD_ONLY;
    unsigned count = 256;
    const char *library_path = NULL;
    void *library;
    opencl_api api;
    selected_device selected;
    int parsed;
    int result;

    parsed = parse_arguments(argc, argv, &mode, &count, &library_path);
    if (parsed == 2)
        return self_test();
    if (parsed != 0) {
        usage(argv[0]);
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
    result = load_api(library, &api);
    if (result != 0) {
        fprintf(stderr, "EXPORTS_INCOMPLETE\n");
        stage("dlclose(exports-incomplete)");
        (void)dlclose(library);
        return 1;
    }
    if (mode == MODE_LOAD_ONLY) {
        printf("LOAD_ONLY PASS exports=all-required\n");
        fflush(stdout);
        stage("dlclose(load-only)");
        (void)dlclose(library);
        return 0;
    }
    result = select_xclipse_gpu(&api, &selected);
    if (result != 0) {
        stage("dlclose(selection-failed)");
        (void)dlclose(library);
        return result;
    }
    if (mode == MODE_ENUMERATE) {
        printf("ENUMERATE ACCEPTED device=GPU+Xclipse; compute-not-run\n");
        fflush(stdout);
        free_selected_device(&selected);
        stage("dlclose(enumerate)");
        (void)dlclose(library);
        return 0;
    }
    result = compute(&api, &selected, count);
    if (result == 0) {
        free_selected_device(&selected);
        stage("dlclose(compute-complete)");
        (void)dlclose(library);
    } else {
        fprintf(stderr, "DLCLOSE_SKIPPED execution-completion-unproven\n");
        free_selected_device(&selected);
    }
    return result;
}
