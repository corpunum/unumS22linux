# Samsung Vulkan HAL probe

Target milestone (2026-09-21): the isolated native-Linux run now loads,
enumerates Xclipse920 and passes the original numerical shader three times
without new GPU faults. See [measured evidence](../../docs/research/GPU_VULKAN_WORKING_2026-09-21.md).
The host-only sections below describe reconstruction checks, not the full
current device status. Model offload and WSI remain separate acceptance gates.

`vulkan-hal-probe.c` is a small Android/aarch64 probe for the recovered
Samsung HAL at
`rootfs/gpu-vendor-reuse-20260921/lib64/hw/vulkan.samsung.so`. It uses the
vendor DSO's exported `HMI` object directly. It does not load
`libvulkan.so.1`, install a replacement driver, or provide a CPU fallback.

The local ABI declarations are the current AOSP `hw_module_t`/`hw_device_t`
and `hwvulkan_device_t` layouts:

* [AOSP hwvulkan.h](https://android.googlesource.com/platform/frameworks/native/+/7b6a56ef2a/vulkan/include/hardware/hwvulkan.h)
* [AOSP hardware.h](https://android.googlesource.com/platform/hardware/libhardware/+/android16-release/include_all/hardware/hardware.h)
* [AOSP Vulkan HAL loader call sequence](https://android.googlesource.com/platform/frameworks/native/+/324b700b4b0669683efff3d696324dc0d4e24ec5/vulkan/libvulkan/driver.cpp)

## Build (host-only)

The NDK r27c toolchain is already staged locally. This compiles an Android
PIE but intentionally does not execute it on the host; the aarch64 vendor DSO
cannot be validated by a native host run.

```sh
cd /home/corpunum/s22-linux
NDK=$PWD/rootfs/gpu-compat-20260921/toolchain/android-ndk-r27c/toolchains/llvm/prebuilt/linux-x86_64
"$NDK/bin/aarch64-linux-android31-clang" -std=c11 -Wall -Wextra -Werror \
  -fPIE -pie -I"$NDK/sysroot/usr/include" \
  -o rootfs/gpu-compat-20260921/vulkan-hal-probe-android \
  tools/gpu-compat/vulkan-hal-probe.c -ldl
```

The resulting executable is an AArch64 PIE with interpreter
`/system/bin/linker64` and only `libc.so`/`libdl.so` as direct NEEDED entries.
The build is not evidence that the vendor HAL loads or that a GPU is present.

## Modes

```text
--load-only   dlopen(RTLD_NOW) the HAL, run constructors, find HMI, validate
              module ABI, and close. It never calls HMI.open.
--enumerate   load the HMI, call HMI.common.methods->open("vk0"), create a
              Vulkan instance through the HAL, enumerate physical devices and
              require an Xclipse-named integrated/discrete device with a
              compute queue.
--compute256  perform the same open/instance/device path, dispatch the local
              compute.spv over 256 uint32 values, wait on a real fence, apply
              non-coherent flush/invalidate operations, and validate every
              value against i*3+7. CPU devices are rejected.
```

The defaults assume the probe is run inside an Android-compatible root where
the vendor file is `/vendor/lib64/hw/vulkan.samsung.so` and the working
directory contains `tools/gpu-compute-probe/compute.spv`. Override either
with `--library PATH` or `--shader PATH`.

`--load-only` is deliberately not called a dry run: ELF constructors execute
when `dlopen(..., RTLD_NOW)` runs. `--enumerate` and `--compute256` are the
first modes that call the HAL `open` method. A parent supervisor must bound
these calls; the probe does not attempt to cancel a wedged GPU submission.

## Current host evidence and limits

The source builds successfully with NDK r27c. The vendor ELF is AArch64,
exports `HMI`, and has `BIND_NOW`; its static NEEDED closure includes
`libhardware.so`, `libsync.so`, `libsbwchelper.so`, the Samsung mapper
implementation, `liblog.so`, `libcutils.so`, `libnativewindow.so`, Bionic
`libc.so`/`libm.so`, and `libdl.so`. Those dependencies are not silently
replaced by this probe.

No host-only result here proves constructor success, HAL enumeration, or
compute correctness. Those require the isolated Bionic/vendor staging and
the real target GPU device path managed by the parent task. A successful
`COMPUTE256` line is only emitted after fence completion and all 256 pattern
checks pass.

## Separate loader-closure sentinels

`tools/gpu-compat/build-vulkan-shims.py` writes only to
`rootfs/gpu-compat-20260921/shims-vulkan/`; it does not modify the existing
OpenCL `shims/` directory or the private runtime. It derives the recovered
Vulkan HAL imports and builds independent abort-on-call libraries for:

* six `AHardwareBuffer_*` symbols in `libnativewindow.so`;
* 27 Samsung SGR metadata symbols in
  `android.hardware.graphics.mapper@4.0-impl-sgr.so`;
* `sync_wait` in `libsync.so`.
* empty `libhardware.so` and `libgralloctypes.so` SONAME carriers for
  file-level dependencies with no imported symbols.

The HAL has a direct `DT_NEEDED` on `libhardware.so`, and real
`libexynosgraphicbuffer_core.so` has direct `DT_NEEDED` entries on both
`libhardware.so` and `libgralloctypes.so`, but the real Vulkan closure has no
imported `LIBHARDWARE` or gralloc symbols. These carriers are for a
load-closure experiment only; they are not hardware or gralloc
implementations and must not be used to claim `open` or GPU readiness.
`libsbwchelper.so`, `libexynosgraphicbuffer_core.so`, ION/DRM,
HIDL, libcutils, liblog, libc++, and the other GPU-side dependencies remain
real files and are not stubbed.

The generated manifest records the exact import sets and hashes. Host tests
load each generated interop DSO and verify that its first call exits 125 with
the symbol name. This proves only sentinel behavior and ELF export shape;
the vendor HAL and any GPU path remain unexecuted here.

Build the separate set with:

```sh
python3 tools/gpu-compat/build-vulkan-shims.py
```

The current host-side artifact hashes are recorded in
`rootfs/gpu-compat-20260921/shims-vulkan/manifest.json`: vendor HAL
`c42a069e05087e925dd09bfa4c6bd47c6d6667d39093bbf6ef315b0ef814dfeb`,
`libhardware.so` carrier
`d22df0fe2d3f786e8a7694d71acbebd75544176166e48d947939c09cfd6b575b`,
`libgralloctypes.so` carrier
`e70e375e148fbff03a57b8aa3f5e3863eed5e78d87954eee808a8218853f4c40`,
mapper sentinel
`1242279aa8dec20caae4bad2602039d799a950b730cc765ff1d77dd81b023093`,
nativewindow sentinel
`51182fd5ce1e900e125c343da42cca9ea1a2ffcfce814370a6a1b246c2bb0c73`, and
sync sentinel
`cc9bc267f7386238fecbc52f1a8eaf12df5355881b24f8589a93510e0f54bf6f`.

Static file-level resolution with the new directory is complete for the
Vulkan HAL and `libsbwchelper.so`: system `liblog/libcutils/libdl/libc/libm`,
the real vendor `libsbwchelper/libexynosgraphicbuffer_core`, and the three
Vulkan interop sentinels are all present in their intended namespace. Relative
to the existing OpenCL shim set, the only newly missing callable imports were
`AHardwareBuffer_allocate`, `AHardwareBuffer_getId`, and
`_Z35sgr_intf_get_sgr_metadata_dataspacePK13native_handle`. This is static
closure readiness only; no Android linker or vendor constructor was executed.
The recovered real mapper ELF does import 35 gralloc symbols, but it is not in
this closure: its SONAME is replaced by the dedicated mapper sentinel. The
Vulkan HAL, SBWCHelper, and Exynos buffer core import zero symbols whose
unmangled names contain `gralloc`.
