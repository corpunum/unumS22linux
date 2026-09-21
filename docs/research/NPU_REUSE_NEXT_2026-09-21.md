# S5E9925 NPU reuse: next safe step

Research date: 2026-09-21. Target: Galaxy S22 SM-S901B/DS (r0s), Exynos
2200/S5E9925. This is a source and artifact audit. No NPU device was opened,
no ioctl was issued, no firmware or service was loaded, and no model was run.
No Git operation was performed.

## Verdict

Subsequent milestone: the exact Bionic ENN wrapper now loads on the phone and
all six selected API symbols resolve in a no-hardware-node sandbox. See the
[successful loader trial](NPU_ENN_STAGE_2026-09-21.md). A later initializer trace
attempts direct allocator and NPU opens in the sandbox; all fail absent, yet
the API returns zero. This is not hardware readiness. The following
model/firmware/ABI boundaries still apply.

The phone has a real Samsung NPU kernel implementation, and a separate
read-only [live hardware inventory](HARDWARE_REUSE_NEXT_2026-09-21.md) records
`npu`, `npu_exynos`, and `hwdev_npu` as live with `/dev/vertex10` present.
There is still no accepted NPU execution path in the native Linux userspace.
The working
OpenCL/Vulkan results are GPU evidence only; they do not establish NPU access,
firmware boot, model compatibility, or acceleration.

The nearest plausible route is a matched Android/vendor ENN environment: the
exact S5E9925 kernel/module, matching firmware, Bionic ENN libraries,
dma-buf/permissions, and an NCP-v25 graph produced by a matching Samsung
toolchain. HIDL/binder services are a likely dependency boundary from the
recovered symbols and DT_NEEDED edges, not proof that the whole Android stack
is mandatory; the actual calls must be traced in a supervised experiment.
Copying `libenn_public_api_cpp.so` or an `.nnc` file into glibc userspace is not
sufficient.

## What the exact kernel requires

The pinned S5E9925 source (`4e5c5ad7d950e4de0688b5663965f2075654b2ad`) shows:

- `CONFIG_EXYNOS_NPU=m`, `CONFIG_NPU_USE_HW_DEVICE=y`,
  `CONFIG_NPU_USE_BOOT_IOCTL=y`, `CONFIG_DSP_USE_VS4L=y`, NCP version 25,
  mailbox v9, command v10, two NPU cores, and LLC/IMB allocators.
- The VS4L contract is an internal vendor ABI (`S_GRAPH`, `S_FORMAT`,
  `S_PARAM`, `PREPARE`, `STREAM_ON`, `QBUF`/`DQBUF`, `BOOTUP`, `SYNC`, and
  related operations), not stable upstream Linux UAPI. Its pointer-bearing
  structures must match the exact 64-bit kernel configuration.
- With DSP VS4L enabled, `npu-binary.h` selects `AIE.bin` and requests
  `vectors.bin` from `/vendor/firmware`; the alternate main image is
  conditional (`NPU.bin`). Local artifacts include `AIE.bin` and
  `dsp_reloc_rules.bin`, but the exact `vectors.bin`, complete signed image,
  and boot/loader path are not established.
- Android policy declares `/dev/vertex10` for the NPU. The separate live
  inventory also sees the node and the three NPU-related modules, which is
  evidence of kernel exposure. It is not proof of firmware boot, an accepted
  ioctl sequence, or inference.

The recovered ENN library exposes useful names (`EnnInitialize`, model open,
buffer create/commit, execute/wait, metadata, and release), but no public
S5E9925 C++ ABI structs were found. Its dependency closure includes Bionic and
libc++, `libdmabufheap`, HIDL memory/base, and
`vendor.samsung_slsi.hardware.enn@1.0.so`; Android binder/HIDL, init/VINTF,
SELinux policy, heaps, and vendor services remain part of the runtime
boundary.

## Model-format boundary

The host-only inventory found 18 recovered `*.nnc*` files. All have an `ENNC`
signature at offset 4: 16 use a `0x20` header-size word and 2 use `0x24`.
The corrected bounded scanner found the NCP-v25 magic at a nonzero inner
offset in all 18 files. All 18 pass the pinned validator's offset/body range,
memory-index, group ISA/intrinsic, and thread-index checks. These are still
only structural candidates: the ENNC container contract, firmware/compiler
compatibility, and hardware execution are unknown. The NCP magic at an inner
offset must not be treated as permission to feed the container to the kernel.

The scanner records only file names, sizes, SHA-256 hashes, ENNC/NCP offsets,
header fields, counts, and validation outcomes in
`rootfs/hardware-reuse-20260921/ennc-ncp-inventory-v2.json`; it emits no model
contents. Its source is `tools/hardware/inspect-ennc-ncp.py`.
The checks are bounded by the remaining file bytes from each candidate NCP
offset; they do not establish the ENNC container payload length or prove that
the candidate is the selected model object.

The first report was preserved as
`rootfs/hardware-reuse-20260921/ennc-ncp-inventory-v1-buggy.json`; it used an
incorrect hand-written memory-vector size and is not evidence. The corrected
v2 report is `rootfs/hardware-reuse-20260921/ennc-ncp-inventory-v2.json`.
The host C layout probe uses the pinned header with its real typedefs and
checks the header/vector sizes, every manually used header offset, and the
memory enum values. Ten host regression tests cover truncation, malformed
ranges, zero-offset safety, invalid indices, group/thread checks, and the C
layout comparison.

Current official Samsung LiteCore/ExecuTorch documentation lists only Exynos
2500/E9955 and Exynos 2600/E9965. It does not support S5E9925 by declaration.
Samsung ONE is a general compiler/runtime project, but no S5E9925 VS4L/ENN
backend contract was found. The official Exynos Eco Linux samples demonstrate
ENN `.nnc` execution on a V920 board with EA-SDK/ADB; that is useful lifecycle
evidence, not proof of S22/E2200 binary compatibility.

## Evidence status

| Area | Proven now | Not proven |
| --- | --- | --- |
| Kernel | Exact S5E9925 VS4L/NCP-v25 headers, validator, firmware names, and config are present in source; read-only live inventory sees `npu`, `npu_exynos`, `hwdev_npu`, and `/dev/vertex10` | Firmware boot, accepted ioctl sequence, reserved-memory/runtime correctness |
| Vendor runtime | ENN symbols and several AArch64 libraries/assets were recovered | ABI structs, `vectors.bin`, HIDL/service/SELinux closure, matching compiler |
| Models | All 18 contain an inner NCP-v25 magic and pass the pinned structural range/index checks | ENNC unwrapping contract, compiler/firmware compatibility, inference output |
| GPU | Three clean OpenCL and three clean Vulkan arithmetic trials, plus bounded llama.cpp Vulkan operation and Qwen3.5 0.8B 25/25-layer offload evidence on Samsung Xclipse 920 | NPU capability or general-model/long-duration guarantees |
| Phone | No destructive changes were made | No NPU open/ioctl/firmware/model experiment has been accepted |

## Initial model-focused proposal and subsequent loader gate

The host-only structural inventory is now complete. It found 18 inner NCP
magic locations and all 18 candidates pass the pinned bounds/index checks, but
it did not identify the complete ENNC unpacking contract. The next safe step is
to explain the ENNC framing and compare these candidates against a matching
compiler/runtime corpus, still without touching `/dev/vertex10`, ENN, firmware,
or the phone.

The independent no-call loader gate has since passed without any model or
device access. The isolated initializer then exposed direct NPU/allocator
requests while no such nodes were present. The next audit is their exact
open/close and firmware lifecycle before actual device exposure;
it must not assume the whole Android stack, guess an ioctl sequence, or
substitute missing firmware. An actual VS4L/ENN inference remains blocked until the exact model
format, firmware set, ABI, permissions, and required runtime calls are
identified.

## Primary sources

- [S5E9925 VS4L header](https://raw.githubusercontent.com/LineageOS/android_kernel_samsung_s5e9925/4e5c5ad7d950e4de0688b5663965f2075654b2ad/drivers/vision/vision-core/include/vs4l.h)
- [S5E9925 NCP-v25 header](https://raw.githubusercontent.com/LineageOS/android_kernel_samsung_s5e9925/4e5c5ad7d950e4de0688b5663965f2075654b2ad/drivers/vision/npu/core/include/ncp_header_v25.h)
- [S5E9925 firmware names](https://raw.githubusercontent.com/LineageOS/android_kernel_samsung_s5e9925/4e5c5ad7d950e4de0688b5663965f2075654b2ad/drivers/vision/npu/core/include/npu-binary.h)
- [Pinned S5E9925 defconfig](https://github.com/LineageOS/android_kernel_samsung_s5e9925/blob/4e5c5ad7d950e4de0688b5663965f2075654b2ad/arch/arm64/configs/s5e9925_defconfig#L6955)
- [Pinned NCP validator](https://github.com/LineageOS/android_kernel_samsung_s5e9925/blob/4e5c5ad7d950e4de0688b5663965f2075654b2ad/drivers/vision/npu/core/npu-util-common.c#L174)
- [Samsung ENNDelegate](https://github.com/Samsung/ENNDelegate)
- [Exynos Eco ENN Linux samples](https://github.com/exynos-eco/enn-sdk-samples-v920-linux)
- [Samsung LiteCore device documentation](https://soc-developer.semiconductor.samsung.com/global/development/ai-litecore/document/documentation)
- [ExecuTorch Samsung backend](https://raw.githubusercontent.com/pytorch/executorch/main/backends/samsung/README.md)
- [Samsung ONE](https://github.com/Samsung/ONE)

Detailed local source/artifact evidence is in
[`NPU_EXISTING_STACKS_2026-09-21.md`](NPU_EXISTING_STACKS_2026-09-21.md) and
the NPU entries in [`EXPERIMENTS.md`](../../EXPERIMENTS.md).
