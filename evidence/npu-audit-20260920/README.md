# Exynos 2200 NPU audit — 2026-09-20

## Finding

The checked-in downstream kernel configuration enables the Samsung NPU as a
module (`CONFIG_EXYNOS_NPU=m`) and the device policy declares `/dev/vertex10`
mode `0644` (`lineage/android_device_samsung_s5e9925-common/configs/init/ueventd.s5e9925.rc:235`). This proves a kernel device endpoint is intended; it does
not prove firmware loaded, that VS4L sessions are accepted, or that inference
works. The supplied kernel tree has no NPU driver source/UAPI (only the generic
NPU device-tree IDs), so an ABI-compatible ioctl version query cannot be derived
locally.

The Android proprietary manifest lists the closed ENN stack:
`libenn_common_utils.so`, `libenn_engine_lib.so`, `libenn_model.so`,
`libenn_public_api_cpp_lib.so`, `libenn_user_driver_unified.so`, and the ENN HIDL
library, plus `libann.elf`, `libnn.elf`, `libnn_af16wf16.elf`, `libnn_sdma.elf`,
and two PAMIR `.nnc` models (`...QVGA...`, `...VGA...`). These are names in a
manifest, not present blobs. The FYI3 extracted recovery ramdisk does contain
the kernel module `lib/modules/npu.ko`, plus
`lib/firmware/sgpu/vangogh_lite_unified.bin` and a touchscreen firmware file;
no `NPU.bin`, `vectors.bin`, ENN `.so`, `.elf`, or `.nnc` was found there. A
module/device node without its matching firmware and userspace graph/runtime
chain is not an inference result. The
large stock AP/factory archives were intentionally not opened, extracted,
mounted, or copied.

## Obtainability and least-risk path

Samsung's public `Samsung/ENNDelegate` repository is a self-extracting archive
of Android ENN libraries and states that it integrates with Android apps for
on-device compilation; it is not a native Linux Exynos-2200 SDK/compiler.
Samsung's public Neural SDK page says third-party SDK downloads are no longer
provided. Samsung/ONE is an open Linux-capable compiler framework, but its
availability does not establish the proprietary Exynos-2200 ENN backend,
firmware ABI, or NCP-v25 output needed by this phone. Android's Samsung
ExecuTorch example likewise requires an Android phone, ENN libraries, and
`adb`; it is not a standalone Alpine runtime. Therefore the obtainable route is
to preserve matching stock vendor blobs and Android ENN runtime/compiler assets,
then validate in an Android-compatible environment. A native Linux port would
need a proven matching ENN runtime and firmware pair; no such pair is present
in this workspace.

## Probe boundary

`tools/npu-probe/npu-open-probe.c` is intentionally only `open(O_RDONLY)`,
`fstat`, and `close`. Opening may still have driver side effects, so owner
approval is required before running it on hardware. No guessed VS4L ioctl is
included: public descriptions identify `VS4L_VERTEXIOC_VERSION` on some newer
trees, while this exact downstream UAPI is absent. Do not issue `S_GRAPH`,
`S_FORMAT`, `BOOTUP`, queue/stream, mmap, or firmware ioctls as a “version”
probe. A future ioctl probe is safe only after obtaining the exact matching
vendor NPU UAPI header and reviewing the driver's open/version handlers.

## Primary references

* Samsung ENNDelegate: https://github.com/Samsung/ENNDelegate
* Samsung Neural SDK availability notice: https://developer.samsung.com/neural/overview.html
* Samsung ONE compiler/runtime project: https://github.com/Samsung/ONE
* Android ExecuTorch Samsung example (Android/adb/ENN dependencies): https://android.googlesource.com/platform/external/executorch/+/0d5192c45c75fc7d33ab220884b0da7d91a9f0ad/examples/samsung
* Samsung NPU security advisories (Exynos 2200 affected; validates caution around untrusted VS4L inputs): https://semiconductor.samsung.com/support/quality-support/product-security-updates/cve-2025-62816/

## Conclusion

There is no justified “NPU driver fix” to apply in this host-only scope. The
least-risk next step is acquisition/verification of exact stock vendor NPU
blobs and matching Android ENN userspace, followed by a separately approved
read-only endpoint probe. A real model run must report the exact model artifact,
runtime libraries, firmware load result, accepted graph, output tensor, and
repeatable timing; `/dev/vertex10` alone is not evidence of acceleration.
