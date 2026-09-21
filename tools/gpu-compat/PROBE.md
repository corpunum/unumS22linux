# Xclipse OpenCL probe

opencl-probe.c is a small, dynamically loaded OpenCL correctness probe for
the Samsung libSGPUOpenCL.so path. It has three target modes and one offline
mode:

    opencl-probe --self-test
    opencl-probe --load-only [--library /path/to/libSGPUOpenCL.so]
    opencl-probe --enumerate [--library /path/to/libSGPUOpenCL.so]
    opencl-probe --compute 256|1024 [--library /path/to/libSGPUOpenCL.so]

The default library name is libSGPUOpenCL.so; use --library when Android
stores it elsewhere. --load-only explicitly uses dlopen with RTLD_NOW, so
vendor ELF constructors execute. It checks every export needed by enumeration
and compute and is not a dry run.

Enumeration asks the loaded library for GPU devices only, then independently
checks CL_DEVICE_TYPE_GPU, rejects any CPU-marked device, and requires the
device name to contain Xclipse (case-insensitive). It prints platform name,
vendor, API version, device name/type, driver version, and OpenCL C version.
Enumeration success is not compute success.

Compute writes an allocated device buffer with the 0xa5 byte pattern, builds
a tiny OpenCL C kernel, dispatches exactly 256 or 1024 work-items, waits for
the kernel event, reads the same allocated buffer, waits for the readback
event, and verifies every word against i * 3 + 7. Any mismatch, CPU/fallback
device, missing Xclipse name, OpenCL error, or build failure is a hard failure.
Build failures include the vendor build log. A STAGE line is flushed before
each vendor call to make a supervised crash location visible.

There is deliberately no in-process timeout and no signal cleanup handler. A
parent must enforce the wall-clock timeout; if command completion is not
proven, the probe skips OpenCL object release and dlclose and lets the parent
terminate the process. This avoids pretending that cleanup can undo a wedged
kernel. The probe does not open displays, use WSI/EGL, touch partitions, or
write anything except its own allocated OpenCL buffer.

## Host build and tests

The source has its own ABI typedefs and needs only POSIX dlopen/dlsym:

    cc -std=c11 -Wall -Wextra -Werror tools/gpu-compat/opencl-probe.c -ldl -o opencl-probe
    python3 tools/gpu-compat/test-opencl-probe.py
    ./opencl-probe --self-test

The offline test intentionally never loads the Samsung library. No Android NDK
or cross toolchain is downloaded or installed by this probe. For an Android
ARM64 build, use an existing NDK/Clang or target musl/aarch64 toolchain,
compile this C file for the target ABI, and link only the platform loader
(-ldl, or the Android linker equivalent). The runtime must provide libdl/
dlsym and the exact vendor libSGPUOpenCL.so; this source does not link against
a host OpenCL implementation and does not bundle one.

The probe executable's Android NDK build has exactly two ELF NEEDED entries:
libdl.so and libc.so. The copied ARM64 Samsung vendor object inspected for the
target has NEEDED entries libdl.so, libsync.so,
android.hardware.graphics.mapper@4.0-impl-sgr.so, liblog.so,
libnativewindow.so, libc.so, and libm.so, with RUNPATH /vendor/lib64/hw.
Those vendor dependencies are runtime image requirements, not host link-time
dependencies; verify the target image supplies them before loading the vendor
object.

Run --compute only under a supervisor that can terminate the process if the
vendor driver hangs. Repeat the supervised run in the parent if repeatability
is required; one PASS is one completed event/readback proof, not a claim about
a model or other GPU APIs.
