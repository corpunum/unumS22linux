# Existing NPU stacks for Galaxy S22 Exynos 2200 (S5E9925)

Research date: 2026-09-21. Target: Samsung Galaxy S22 SM-S901B/DS (r0s),
Exynos 2200 / S5E9925. This is a read-only source and artifact audit. No phone
ioctl, NPU open, package installation, reboot, firmware write, or model call was
performed.

## Bottom line

There is a real Samsung NPU kernel implementation for this SoC, and the exact
Lineage kernel checkout contains the graph and ioctl definitions needed to
understand its contract. There is not currently a proven, native-glibc Linux
NPU stack that can be copied into the Alpine/Arch userspace and run inference.

The closest plausible route is an Android-compatible vendor environment using
the matching 5.10 S5E9925 kernel, vendor NPU firmware, Bionic-compatible ENN
libraries, and an NCP-v25 model produced by a matching Samsung compiler. The
recovered HIDL/ENN references make Android service plumbing a likely boundary,
but DT_NEEDED and symbol evidence do not prove that every Android service is
required; the actual calls need tracing. The closest *source* route is to retain the exact
kernel NPU module and write a carefully versioned userspace adapter around the
published internal headers; this still requires proving firmware loading,
buffer allocation, permissions, and one real inference on the phone.

Current ExecuTorch Samsung backend work is not a drop-in answer for this phone:
its official documentation accepts only Exynos 2500 (E9955) and Exynos 2600
(E9965), not Exynos 2200/S5E9925, and it requires Samsung Exynos AI LiteCore.

## Ranked routes

| Rank | Route | Hardware/version match | What is actually reusable | Status |
| --- | --- | --- | --- | --- |
| 1 | Exact S5E9925 kernel NPU driver + Android ENN/vendor closure | Exact SoC and kernel lineage; local SHA `4e5c5ad7d950e4de0688b5663965f2075654b2ad` | Kernel source/module, VS4L definitions, NCP-v25 validation, recovered AIE/DSP assets, ENN symbols and Android service declarations | Closest route; no accepted inference yet |
| 2 | Samsung/ENNDelegate v3.x and `exynos-eco/enn-sdk-samples` | ENN is intended for Samsung Exynos, and samples demonstrate Android execution, but public material does not establish S5E9925 binary/version compatibility | API usage ideas, sample model lifecycle, limited wrapper headers; archives are proprietary self-extracting libraries | Android-userland dependent; not native Linux |
| 3 | ExecuTorch Samsung ENN backend | Officially E9955/E9965 only; no E2200 compile spec | AOT/delegate architecture, runner integration, ENN dynamic-loading pattern | Not compatible by evidence; do not label S5E9925 supported |
| 4 | Samsung ONE | General Linux-kernel-based OS runtime/compiler project | Generic IR/compiler/runtime components and CPU/GPU paths | No S5E9925 VS4L/ENN backend contract found |
| 5 | Generic Android NNAPI / LiteRT Samsung path | Android API/HAL integration, but current LiteCore docs list E9955/E9965 only | Framework-level fallback design | Not a native Linux NPU driver and no E2200 support evidence |

## Exact local kernel contract

The local tree is not merely a device node stub:

* `s5e9925_defconfig` selects `CONFIG_EXYNOS_NPU=m`, `CONFIG_VISION_CORE=y`,
  hardware NPU mode, `CONFIG_NPU_USE_HW_DEVICE=y`, `CONFIG_NPU_USE_BOOT_IOCTL=y`,
  `CONFIG_DSP_USE_VS4L=y`, NCP version 25, mailbox version 9, command version 10,
  two NPU cores, LLC and IMB allocators: [defconfig](https://github.com/LineageOS/android_kernel_samsung_s5e9925/blob/4e5c5ad7d950e4de0688b5663965f2075654b2ad/arch/arm64/configs/s5e9925_defconfig).
* The kernel Makefile builds the complete module family (`npu-device`,
  `npu-vertex`, `npu-binary`, `npu-memory`, `npu-session`, protocol/mailbox,
  DSP dynamic-linker code, and scheduler), not only a placeholder: [vision Makefile](https://github.com/LineageOS/android_kernel_samsung_s5e9925/blob/4e5c5ad7d950e4de0688b5663965f2075654b2ad/drivers/vision/Makefile).
* The matching VS4L header defines `vs4l_graph`, format/parameter/container
  structures and `VS4L_VERTEXIOC_S_GRAPH`, `S_FORMAT`, `S_PARAM`, `STREAM_ON`,
  `QBUF`, `DQBUF`, `PREPARE`, `UNPREPARE`, `BOOTUP`, `VERSION`, and `SYNC`:
  [vs4l.h](https://github.com/LineageOS/android_kernel_samsung_s5e9925/blob/4e5c5ad7d950e4de0688b5663965f2075654b2ad/drivers/vision/vision-core/include/vs4l.h).
  These are internal vendor-kernel definitions, not a stable upstream Linux
  UAPI. The structures contain `unsigned long` pointers and config-conditional
  fields, so a userspace copy must be built against the exact configuration and
  64-bit ABI.
* NCP v25 is a compiler-produced container, not ONNX/TFLite. Its header declares
  magic values, version 25, address/memory/power/interruption/LLC/group/thread
  vectors and a body. [ncp_header_v25.h](https://github.com/LineageOS/android_kernel_samsung_s5e9925/blob/4e5c5ad7d950e4de0688b5663965f2075654b2ad/drivers/vision/npu/core/include/ncp_header_v25.h)
* The kernel validates the user NCP version and every vector/body range before
  execution, including v25 interruption, LLC, group and thread references:
  [npu-util-common.c](https://github.com/LineageOS/android_kernel_samsung_s5e9925/blob/4e5c5ad7d950e4de0688b5663965f2075654b2ad/drivers/vision/npu/core/npu-util-common.c).
  This gives us a real offline structural checker target, but passing it would
  not prove firmware or hardware execution.
* `npu-binary.h` selects `AIE.bin` when `CONFIG_DSP_USE_VS4L` is enabled,
  otherwise `NPU.bin`, and names `vectors.bin`, `/vendor/firmware/`, and NCP
  object paths: [npu-binary.h](https://github.com/LineageOS/android_kernel_samsung_s5e9925/blob/4e5c5ad7d950e4de0688b5663965f2075654b2ad/drivers/vision/npu/core/include/npu-binary.h).
  The exact source calls `request_firmware()` for the main image and
  `vectors.bin`; `NPU.bin` is conditional, while `vectors.bin` is an explicit
  source request for the relevant code path. This still does not prove that all
  execution modes require vectors or that the recovered AIE is the correct
  signed image for this boot.
* Android device policy declares `/dev/vertex10` for the NPU at mode 0644,
  system/system: [ueventd.s5e9925.rc](https://github.com/LineageOS/android_device_samsung_s5e9925-common/blob/7be96871126c07d6d51d250a4e0048b58e94eb63/configs/init/ueventd.s5e9925.rc).
  A node declaration proves intended Android exposure, not a successful probe
  or inference.

### Bounded `.nnc` first-byte check

As a host-only check, the 18 corrected decompressed files matching `*.nnc*`
under `rootfs/npu-vendor-assets-decompressed/` were read for their first 32
bytes with `od`; no parser, decompressor, ENN library, or phone path was used.
All 18 begin with the little-endian bytes:

```text
20 00 00 00 45 4e 4e 43 ...
```

That is an `ENNC` Samsung container signature, not the raw NCP-v25 magic
(`e0 fe 0f 0c`, little-endian `0x0C0FFEE0`) at file offset zero. Two files
begin with `24 00 00 00 ENNC` and the remaining 16 with `20 00 00 00 ENNC`;
this is a container-header variation. A subsequent corrected host-only
scanner found the NCP-v25 magic at a nonzero inner offset in all 18 files and
all 18 passed the pinned validator's structural offset/body, memory-index,
group ISA/intrinsic, and thread-index checks. This remains **structural
candidate** evidence, not firmware/compiler compatibility or execution
evidence: the complete ENNC unwrapping contract is still unknown. Do not feed
the ENNC prefix directly to the kernel NCP validator.

Primary exact-source URLs:

* [Lineage S5E9925 VS4L header at exact kernel SHA](https://raw.githubusercontent.com/LineageOS/android_kernel_samsung_s5e9925/4e5c5ad7d950e4de0688b5663965f2075654b2ad/drivers/vision/vision-core/include/vs4l.h)
* [Lineage S5E9925 NCP-v25 header at exact kernel SHA](https://raw.githubusercontent.com/LineageOS/android_kernel_samsung_s5e9925/4e5c5ad7d950e4de0688b5663965f2075654b2ad/drivers/vision/npu/core/include/ncp_header_v25.h)
* [Lineage S5E9925 firmware-name header at exact kernel SHA](https://raw.githubusercontent.com/LineageOS/android_kernel_samsung_s5e9925/4e5c5ad7d950e4de0688b5663965f2075654b2ad/drivers/vision/npu/core/include/npu-binary.h)
* [Pinned S5E9925 defconfig NPU settings](https://github.com/LineageOS/android_kernel_samsung_s5e9925/blob/4e5c5ad7d950e4de0688b5663965f2075654b2ad/arch/arm64/configs/s5e9925_defconfig#L6955)
* [Pinned S5E9925 NCP validator](https://github.com/LineageOS/android_kernel_samsung_s5e9925/blob/4e5c5ad7d950e4de0688b5663965f2075654b2ad/drivers/vision/npu/core/npu-util-common.c#L174)
* [ExtremeXT matching-family NPU source audit revision](https://github.com/ExtremeXT/android_kernel_samsung_s5e9925/tree/a3310850291d228b1ba9568a28bde5a019262541/drivers/vision/npu/core)

## ENN and vendor artifacts

The local extraction recovered AArch64 `libenn_public_api_cpp_lib.so` and its
ENN entry points (`EnnInitialize`, model open, buffer creation/commit, execute,
wait, metadata and release), plus multiple vendor driver libraries, NNC files,
`AIE.bin`, and `dsp_reloc_rules.bin`. The dynamic closure includes Android
Bionic/libc++, `libdmabufheap`, HIDL memory/base, and
`vendor.samsung_slsi.hardware.enn@1.0.so`; this is why copying one `.so` into
glibc userspace is insufficient. See the local [runtime feasibility audit](../../evidence/npu-audit-20260920/runtime-feasibility.md),
[ELF dependency inventory](../../evidence/npu-audit-20260920/decompressed-elf-dependencies.txt),
and [vendor extraction summary](../../evidence/npu-audit-20260920/vendor-extraction-summary.md).

`AIE.bin` and `dsp_reloc_rules.bin` are present in the corrected extraction;
the exact `vectors.bin` was not recovered. `NPU.bin` is not a confirmed missing
requirement because the kernel source makes the main name conditional on
`CONFIG_DSP_USE_VS4L`. Do not turn either absence into a universal claim.

Samsung's public [ENNDelegate repository](https://github.com/Samsung/ENNDelegate)
describes itself as a vendor delegate for Android apps and distributes
self-extracting library archives, not a portable header/UAPI package. The
local audit of v3.1.13's embedded archive found only the public wrapper headers
`include/enn_wrapper_log.h` and `include/enn_wrapper_sq.h`; it did not contain
`npu-binary.h`, VS4L UAPI, S5E9925 kernel headers, or `vectors.bin`. The local
recovered AArch64 ENN library does expose the concrete symbols
`EnnInitialize`, `EnnOpenModel`, `EnnCreateBuffer`, `EnnSetBuffers`,
`EnnExecuteModel`, `EnnExecuteModelWait`, and `EnnGetMetaInfo`, but those symbol
names do not provide the missing C++ ABI struct layouts.
The
public [ENN SDK samples](https://github.com/exynos-eco/enn-sdk-samples) are
explicitly Android applications run through Android Studio/ADB, with models
optimized for Exynos; they are useful for API and model-lifecycle references,
not a Linux service or kernel port.

The official ExecuTorch backend makes the dependency boundary explicit:

* [Samsung backend README](https://raw.githubusercontent.com/pytorch/executorch/main/backends/samsung/README.md)
  says the delegate targets Exynos NPU/DSP, is built on Samsung EXYNOS_LITECORE,
  and supports only E9955/E9965.
* [Official partitioner docs](https://docs.pytorch.org/executorch/stable/backends/samsung/samsung-partitioner.html)
  state that only E9955 (Exynos 2500) and E9965 (Exynos 2600) are valid chipset
  names. S5E9925 is absent.
* The [official LiteCore device list](https://soc-developer.semiconductor.samsung.com/global/development/ai-litecore/document/documentation)
  likewise lists only Exynos 2500/E9955 and Exynos 2600/E9965.
* The backend runtime dynamically loads `libenn_public_api_cpp.so` and resolves
  ENN symbols rather than implementing the NPU itself: [enn_api_implementation.cpp](https://raw.githubusercontent.com/pytorch/executorch/main/backends/samsung/runtime/enn_api_implementation.cpp).
  Its build requires the LiteCore SDK and an Android NDK: [build.sh](https://raw.githubusercontent.com/pytorch/executorch/main/backends/samsung/build.sh).
* Even a current official ExecuTorch issue from an S24 attempt records failure
  from missing `libenn_public_api_cpp.so` and additional missing vendor
  dependencies (`libenn_user.samsung_slsi.so`): [issue #16395](https://github.com/pytorch/executorch/issues/16395#issue-3752588106).
  This is evidence of vendor-closure dependence, not evidence that S22 works.

Do not merge this current LiteCore boundary with older Samsung Neural SDK
material. Samsung's legacy [Neural SDK 3.0 page](https://developer.samsung.com/neural/overview.html)
is dated May 2021, says the SDK is no longer provided to third-party developers,
and explicitly says model-conversion tools are obtained separately from vendor
sites. Older device/support lists or Exynos 2200-era NNC examples therefore
remain historical Android-vendor evidence; they do not extend the current
LiteCore/ExecuTorch supported-chipset list to S5E9925. The local extraction's
`ex2200`/`enn200c` model filename is evidence that Samsung shipped E2200-targeted
artifacts, not proof that a current public compiler can generate compatible
NCP-v25 graphs.

Samsung [ONE](https://github.com/Samsung/ONE) is valuable as a general compiler
and runtime reference: its README says it targets Linux-kernel-based OSes and
CPU/GPU/DSP/NPU processors. It does not, however, publish this S5E9925 VS4L
userspace ABI, Samsung's proprietary ENN structs, or a verified E2200 target.
Use its generic compiler/IR components only where the selected backend is
actually supported; do not infer that ONE master can emit an S5E9925 NCP-v25
container.

## What can be reused now

1. **Offline, no hardware:** the corrected host-only checker now inventories
   all 18 inner NCP-v25 candidates against the exact v25 magic/version/vector/
   body/index rules. The report is
   `rootfs/hardware-reuse-20260921/ennc-ncp-inventory-v2.json`; it records
   metadata and hashes only. This validates structural claims only; it cannot
   certify an NPU model or resolve the complete ENNC runtime contract.
2. **Kernel build research:** use the exact kernel source/config to audit the
   full module dependency closure and device-tree/reserved-memory requirements.
   Do not load or replace modules without a separate owner-approved hardware
   action.
3. **Android compatibility route:** preserve the recovered ENN closure and
   vendor HIDL/init/VINTF contract as a separate Android-targeted artifact.
   This is currently the best-documented vendor route toward real S5E9925
   inference, but its libraries must remain matched to the phone build and
   Bionic/vendor service. A native userspace adapter remains an open research
   route; it must be compiled and validated against the exact kernel contract
   before it can be considered usable.
4. **Framework integration:** borrow ExecuTorch's model partitioning and runner
   shape only after an S5E9925 ENN compiler/runtime is independently proven.
   Current E9955/E9965 compile specs cannot be relabeled E2200.

## What remains unknown or blocked

* No public, stable S5E9925 ENN C++ headers/ABI structs were found. Recovered
  symbols do not establish struct layouts or model-version compatibility.
* No exact `vectors.bin` was recovered; AIE/DSP assets alone do not prove a
  complete firmware closure, signature/imgloader path, or boot success.
* The downstream kernel's internal headers are available, but Android binder,
  HIDL memory, dma-buf heaps, SELinux/device permissions, vendor service, and
  firmware integration are not present in native Alpine/Arch userspace.
* No NPU inference, accepted VS4L ioctl sequence, firmware boot, or output
  comparison has been run. Existing `/dev/vertex10` intent and module strings
  are not execution evidence.

## One next read-only feasibility check

The host-only structural inventory is complete: all 18 files have an inner
NCP-v25 magic and pass the pinned bounds/cross-index checks. The next safe
research step is to explain the ENNC framing and compare those candidates to a
matching compiler/runtime corpus. Do not open `/dev/vertex10`, issue ioctls,
load firmware, or call ENN; firmware, ENN ABI, and hardware execution remain
explicitly unproven.

## Existing project evidence

* [runtime-feasibility.md](../../evidence/npu-audit-20260920/runtime-feasibility.md)
* [public-s9925-npu-source.txt](../../evidence/npu-audit-20260920/public-s9925-npu-source.txt)
* [DRIVER_MODELS_2026-09-20.md](../DRIVER_MODELS_2026-09-20.md)
* [EXPERIMENTS.md NPU entries](../../EXPERIMENTS.md)
