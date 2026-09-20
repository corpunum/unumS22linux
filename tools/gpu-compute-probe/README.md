# Headless Vulkan compute correctness probe

Current result: subsequent native phone runs failed numerical compute through
the experimental RADV path. A record/teardown lifecycle bug was fixed, but
real fence wait/command submission still faults. No GPU model benchmark passed.
See `docs/DRIVER_MODELS_2026-09-20.md`. Compiled probe/SPIR-V artifacts are
local-only; the shader and C source are published below.

This probe has no WSI, EGL, display, or phone interaction. It dynamically loads
`libvulkan.so.1`, enumerates physical devices, rejects `VK_PHYSICAL_DEVICE_TYPE_CPU`,
selects a compute-capable non-CPU device, writes 256 values with a 64-wide
compute shader, waits on a fence (3 seconds), and validates every host-visible
readback value against `value[i] = i*3 + 7`. It prints `PASS` only after exact
readback validation. It does not run in this task because host GPU workloads
are shared with production; a lavapipe run, if desired, must be explicitly
separate and labeled software.

Build the SPIR-V and native host probe:

```
glslc -O compute.comp -o compute.spv
cc -O2 -Wall -Wextra -o gpu-compute-probe gpu-compute-probe.c -ldl
./gpu-compute-probe ./compute.spv
```

The program is intentionally loader-only (`dlopen`/`dlsym`) and needs no Vulkan
link-time library. For the phone's Alpine ARM64 userspace, compile with the
phone's musl/aarch64 toolchain and use its `libvulkan.so.1`; do not substitute
lavapipe or llvmpipe as an acceleration result.

Host-only cross-build artifact:
`gpu-compute-probe-aarch64-musl` (AArch64 PIE, interpreter
`/lib/ld-musl-aarch64.so.1`, SHA256
`863f1cfd124d67c5127c9fada6fe3aaed2295ae5df158261e7c55bea58898fc9`). It was
compiled with Clang targeting `aarch64-linux-musl` against the read-only
`/tmp/radv-native-root.2ZvJzf` sysroot. No Vulkan library was linked; runtime
resolution remains through the target's `libvulkan.so.1`.
