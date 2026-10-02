# ABOX native module-only build — 2026-10-02

## Verdict

The reviewed three-patch ABOX stack compiled successfully as one native
`snd-soc-samsung-abox.ko` target against the exact exported native config and
the pinned Clang 18/LLD 18 toolchain. The output imports all 359 dependencies
with matching CRCs from the filtered native-eight `Module.symvers`, including
`module_layout`, and its vermagic matches the baseline ABOX module.

Integration with the unchanged native-eight module set is **blocked**. Of the
28 ABOX exports, 10 have different MODVERSIONS CRCs. A read-only scan of all
329 baseline `.ko` files found two existing consumers with six imports that
still require the old CRCs. The new ABOX module and those unchanged consumers
are therefore not a compatible set under MODVERSIONS. Do not force-load,
suppress CRC checks, or treat this narrow compile as an integrated module set.
The dependent modules must be rebuilt against the final ABOX export table (or
the ABI change must be separately corrected and reviewed) before package/load
compatibility can be reconsidered.

No image or `vmlinux` was rebuilt; this was a single-module host build. No
module was installed, packaged, published, deployed, or loaded, and there was
no phone, firmware, DMA, PCM, or physical-audio operation.

## Pinned source and patches

The clean audio source worktree is commit
`3c11bdda6ba6ba27fb4eb7e2cb096d98c514d6d3`, tree
`8aac19a6621ed74db63960d0f4af84557a20404c`, directly on native-eight base
`872bffb8ea2ea657f94d10b866dc655b5718d6db` (tree
`417e4a55e222b99ecca0f198e081d2d808ef5628`). Its only four changed paths are
`include/trace/events/samsung_abox.h` and
`sound/soc/samsung/abox/{abox.c,abox.h,abox_rdma.c}`. No NPU9/NPU10, kernel
core, or config patch is included.

The exact ordered inputs were:

| Patch | SHA-256 |
|---|---|
| `audio-ipc-observation-fix.patch` | `afe1cbd271559b9aba5907ec732613a6efabb730e4763b8356c3c72906c93a4d` |
| `audio-ipc-error-path-2026-10-02.patch` | `676735886d88441232d3e56aa07c77e38f63f823ffa09b90addc51918ef9fc9d` |
| `audio-ipc-worker-pm.patch` | `eeb5e15727fddbc3b153a08a1c77edee5e7be4a527d7ef1dbffb4ad3e4722c8a` |

The observation, queue/error-path, and worker-PM extracted-C harnesses each
passed with the exact local pinned fixture under normal Python, `python3 -O`,
and effective `PYTHONOPTIMIZE=1` modes: nine successful harness invocations
in total. Their C cases include O0/O2 builds. The modpost-Symvers preparation
helper's tests passed in all three Python modes as well. These are host/source
tests; they do not execute Linux workqueues, runtime PM, device IPC, or audio
hardware.

## Configuration, toolchain, and command

The copied configuration SHA-256 is the exact native export
`d762d5fc71e369013ee36657d007063ce1f0faca9707d5f9ccfba2597b7fcd16`.
`olddefconfig` completed successfully in a separate bounded phase and left the
config byte-identical (same SHA before and after). The configuration retained
`CONFIG_SND_SOC_SAMSUNG_ABOX=m`, tracepoints, CFI, shadow call stack,
MODVERSIONS, and `CONFIG_LTO_NONE=y`; no LTO mode was changed.

The exact base `Module.symvers` is SHA-256
`15fc69e815cb4da6cb3372f4b5141005ab2e770b0414b67f230f1b185bf03df7` (17,283
rows). The existing Symvers preparation helper removed only the 28 rows owned
by the ABOX target, retaining all other owners and `module_layout`, yielding
17,255 rows in `abox-dependencies.symvers`, SHA-256
`ada1bf87ea2f99233622f4b75a7a88a93952cb24fc1f806d727bf6177352cafa`. A
direct byte comparison confirmed it equals the base file with exactly those
28 owner rows removed. The preparation helper's regression suite passed under
all three Python modes.

All 13 resolved LLVM/GNU helper tools matched the native-eight build receipt.
Clang is Ubuntu 18.1.3 (SHA-256
`8ef402d453d1ba4902e4ee0f0f847f6cfa01400c95aa43c24e97818b9c0e3f45`); LLD is
Ubuntu 18.1.3 (SHA-256
`7ad9a0e8fe6d0e79b71172d731e33872c0274e49fceb7b516d774876d5a58ade`). The
build used an `env -i` environment, `nice -n 10`, `-j1`, and a monitored
process group with 8 GiB available-memory and 16 GiB free-disk abort
thresholds. The preflight reported 25,946,513,408 bytes available RAM and
50,588,872,704 bytes free disk.

After the config guard, one invocation ran the pinned single-target route
(paths abbreviated to avoid publishing local host paths):

```text
env -i PATH=/usr/lib/llvm-18/bin:/usr/bin:/bin LANG=C LC_ALL=C
  nice -n 10 make -C <audio-source> O=<fresh-output>
  ARCH=arm64 LLVM=1 CROSS_COMPILE=aarch64-linux-gnu- LOCALVERSION=
  input-symdump=<fresh-output>/abox-dependencies.symvers -j1 V=1
  sound/soc/samsung/abox/snd-soc-samsung-abox.ko
```

The monitored build exited 0, did not hit a resource abort, and its process
group was absent at completion. The actual modpost command consumed the
filtered `input-symdump` and retained unresolved-symbol errors; no warning or
error suppression was found. The log has no warning/error diagnostics. There
were 28 ABOX C translation-unit compile commands, each carrying both
`-fsanitize=kcfi` and `-fsanitize=shadow-call-stack`; the module metadata
object is separate. The link log contains no `-flto`. No `Image`, `vmlinux`,
or other `.ko` was produced in this fresh module-only output.

## Artifact and ABI results

The only generated module is ELF64 AArch64
`snd-soc-samsung-abox.ko`, 9,583,728 bytes, SHA-256
`38ddcb2c1fd369b217d022f56b8a20a5488fedc01d6583fd3b2e4be5ec685df8`, GNU
Build ID `052414946f06902f9e5caca6a69d2732235eb720`. It has `__versions` and
vermagic `5.10.260-g4e5c5ad7d950 SMP preempt mod_unload modversions aarch64`,
matching the baseline ABOX module. Its 359 imports have zero missing,
ambiguous, or CRC-mismatched entries against the filtered dependency file;
the `module_layout` CRC is `0x0e3c515c` on both sides.

The module exports the same 28 names as the baseline ABOX module. Eighteen
CRCs are unchanged; the 10 differences are:

| Export | Baseline CRC | New CRC |
|---|---:|---:|
| `abox_hw_params_fixup_helper` | `0x97d1fec7` | `0x2004e1bc` |
| `abox_iommu_map` | `0xc12c99a2` | `0x67a41b5b` |
| `abox_iommu_map_sg` | `0x73a131d9` | `0xfb1a2964` |
| `abox_iommu_unmap` | `0xdbbfe1df` | `0xe8251aec` |
| `abox_iova_to_phys` | `0x46589d01` | `0xac033b11` |
| `abox_iova_to_virt` | `0x6f844da0` | `0x9aa832d4` |
| `abox_register_ipc_handler` | `0x9318da9c` | `0x2242e539` |
| `abox_request_cpu_gear_ext` | `0xb57ee649` | `0x7d80e140` |
| `abox_request_dram_on` | `0xc59d7e2f` | `0x7fe9fa89` |
| `abox_request_ipc` | `0xded8a290` | `0xf2895e57` |

I read `__versions` from all 329 `.ko` artifacts in the frozen native-eight
output. Two baseline consumers still require six old CRCs:

| Existing module | Stale imports in that module |
|---|---|
| `sound/soc/samsung/rainbow_prince.ko` | `abox_hw_params_fixup_helper` |
| `sound/usb/exynos-usb-audio-offloading.ko` | `abox_iova_to_phys`, `abox_request_ipc`, `abox_iommu_map`, `abox_iommu_unmap`, `abox_register_ipc_handler` |

This is static MODVERSIONS compatibility evidence against the frozen host
module set, not a module-load test or a claim about modules currently loaded on
any phone. The unchanged native-eight artifacts were not modified: its
`Module.symvers` remains SHA-256
`15fc69e815cb4da6cb3372f4b5141005ab2e770b0414b67f230f1b185bf03df7`, `Image`
`c1e27df303465bc4917bb24699f38792023a979c40728b58298b9b62815f000e`,
`vmlinux` `067fef0fe101f9df0f16652080841ff88bddbe34e3074789120ba13df269a503`,
and `npu.ko` `5e587db7065bc94755ed48f74bd8a904a1d37b243a2b8715701cbeef6e1459bb`.

The private host logs, phase receipts, and outputs remain outside this source
repository. Their sanitized hash bindings are recorded in
`evidence/s22-audio-native-module-build-20261002.json`. This build establishes
only that the specified ABOX module target compiled and its own imported
symbols match the baseline dependencies. It does not clear the changed export
ABI, prove the full module set, establish current-phone compatibility, or
authorize boot, deployment, load, or audio-runtime testing.
