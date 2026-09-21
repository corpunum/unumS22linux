# Minimal Samsung Vulkan HAL bridge

`vulkan-hal-bridge.c` is a per-process `libvulkan.so` bridge for the one
recovered Samsung HAL path:

```text
/vendor/lib64/hw/vulkan.samsung.so -> HMI -> vk0
```

It is not a general Vulkan loader. It has no ICD discovery, WSI, display
services, validation layers, feature emulation, or CPU fallback. The vendor
HAL is opened lazily once with `pthread_once`; failed initialization returns
the actual initialization error or NULL function pointer and is not converted
into a fake GPU result. The bridge intentionally never calls `dlclose` or the
HAL close callback during process lifetime.

The exported global surface is limited to what llama.cpp's ggml-vulkan path
needs before instance dispatch initialization:

```text
vkGetInstanceProcAddr
vkCreateInstance
vkEnumerateInstanceExtensionProperties
vkEnumerateInstanceLayerProperties
vkEnumerateInstanceVersion
vkGetDeviceProcAddr
vkGetPhysicalDeviceFeatures2
vkCmdCopyBuffer
```

`vkGetInstanceProcAddr` forwards all other names to the vendor
`hwvulkan_device_t::GetInstanceProcAddr`. The bridge reports an actual empty
instance-layer list because it does not provide a layer loader; it does not
invent validation or GPU layers.

After a successful `vkCreateInstance`, the bridge resolves the three
instance/device-scoped callbacks above from the vendor's instance dispatch and
keeps those function pointers for the process lifetime. If any callback is
missing, instance creation is rolled back with
`VK_ERROR_INITIALIZATION_FAILED`. The `void` callbacks
`vkGetPhysicalDeviceFeatures2` and `vkCmdCopyBuffer` fail-fast if their cached
vendor pointer is unexpectedly absent; they never synthesize a result or
execute a CPU fallback. `vkGetDeviceProcAddr` returns the vendor's exact
pointer result, including NULL.

## Host-only build

```sh
cd /home/corpunum/s22-linux
NDK=$PWD/rootfs/gpu-compat-20260921/toolchain/android-ndk-r27c/toolchains/llvm/prebuilt/linux-x86_64
mkdir -p rootfs/gpu-compat-20260921/vulkan-bridge
test ! -e rootfs/gpu-compat-20260921/vulkan-bridge/libvulkan-rebuilt.so
"$NDK/bin/aarch64-linux-android31-clang" -std=c11 -Wall -Wextra -Werror \
  -fPIC -fvisibility=hidden -shared -Wl,-soname,libvulkan.so \
  -I"$NDK/sysroot/usr/include" \
  -o rootfs/gpu-compat-20260921/vulkan-bridge/libvulkan-rebuilt.so \
  tools/gpu-compat/vulkan-hal-bridge.c -ldl
VULKAN_BRIDGE_PATH=rootfs/gpu-compat-20260921/vulkan-bridge/libvulkan-rebuilt.so \
  python3 tools/gpu-compat/test-vulkan-hal-bridge.py
```

The test is static/host-only: it checks the AArch64 ELF, SONAME, NEEDED set,
and exact exported bridge surface. It also builds a temporary native fake HAL
with the same HMI ABI and functionally checks `pthread_once` initialization,
`vkGetInstanceProcAddr` exact forwarding, `vkCreateInstance`, extension and
version forwarding, and the empty-layer contract. The fake test uses a
compile-time path override only; it never loads the AArch64 bridge or Samsung
vendor library. Runtime success still requires the parent's isolated
Bionic/vendor staging and a real target-side process.

Before validating the HMI result, the bridge clears the calling thread's
thread-local `dlerror()` state. This matters when an embedding process has
performed an unsuccessful plugin lookup immediately before loading the bridge;
that unrelated error must not make a valid HMI lookup appear invalid.

The original `libvulkan.so` artifact remains unchanged at
`b1fff13ed3e8b89739719caa958de8b703ffccbb6bcf8dd08b253afb7ab3a46d`.
The v2 artifact, with the same SONAME (`libvulkan.so`), is
`1d936597473f2d9bde6ab48dfaa8546a1dd0ffcccf4a8df36406b0ed826a745d`.

For diagnosis only, `libvulkan-v4.so` was built with `-DBRIDGE_TRACE=1`; it
has the same SONAME and the HMI/dlerror fix plus stage logging. Its hash is
`9c9a7c748ce9170a5b19bc76bf653932b7f02505d64e6cdcd804a17c047195c4`.
This exact v4 artifact subsequently passed the target-side matrix and Qwen0.8B
tests in [the measured model record](../../docs/research/GPU_LLAMA_VULKAN_2026-09-21.md).
The default host test inspects v4; a rebuilt library is a new artifact and needs
its own target acceptance. Do not overwrite accepted phone libraries.
