# Independent native NPU artifact review — 2026-10-02

## Scope and verdict

Reviewed the completed `native-eight` host build bound to frozen kernel
commit `872bffb8ea2ea657f94d10b866dc655b5718d6db`, tree
`417e4a55e222b99ecca0f198e081d2d808ef5628`, based on
`3fca50941422439b2019db2e4a3dc1016b2138a1`. The source worktree was clean;
its 11 changed paths are all under `drivers/vision/npu/`, and `sound/` is
unchanged from the base.

No artifact-binding, toolchain-provenance, or same-build module-ABI blocker
was found within this bounded audit. This is a host artifact result only. It
does not authorize or establish deployment, module loading, BOOTUP, firmware
readiness, or hardware behavior.

## Frozen build and artifact binding

The build receipt reports `host_build_pass`, profile `native-eight`, kernel
release `5.10.260-g4e5c5ad7d950`, 329 modules, exit code 0, and no resource
abort. Its source commit/tree/base, config SHA-256
`d762d5fc71e369013ee36657d007063ce1f0faca9707d5f9ccfba2597b7fcd16`, and
builder SHA-256
`9b94c31a72cf4216dff1e5a8da5dc327cc74ed89465b9d8a8dfb7d6b2b41e3b4` match
the completed phase record and inspected files. That builder hash matches the
frozen builder blob from commit
`3c5be3728da4789100f29265b9c69e0048186cce`.

Receipt SHA-256 is
`202a9480a6b086dab7971ff115862e41174c3c35bfaadaedca6402503177aa98`, phase
record SHA-256 is
`ebfadae38022e247c9aeb25646dfcdaa8047ba15b79bea7dfd07622c64426edf`, and
build-log SHA-256 is
`244c567e04802169dfb0dfb406d9dd9e609350dc1bc265b337c78496a2af46a0`. The
recorded make invocation is `ARCH=arm64 LLVM=1
CROSS_COMPILE=aarch64-linux-gnu- -j1 Image modules`. The recorded clang,
LLD, LLVM utility, and GNU cross-tool binary hashes and reported versions
were checked against the resolved tools; generated compiler metadata reports
the same clang 18.1.3 / LLD 18.1.3 pair. The build log contains no compiler
or linker error/warning diagnostic matches in the checked patterns.

The output `.config` hash matches the exported config. Relevant settings are
`CONFIG_EXYNOS_NPU=m`, `CONFIG_MODVERSIONS=y`, `CONFIG_CFI_CLANG=y`,
`CONFIG_SHADOW_CALL_STACK=y`, and `CONFIG_LTO_NONE=y`. This is not a
ThinLTO build: no `-flto` appears in the inspected compile/preprocess command
records. The build command does not explicitly override `LLVM_IAS` or `LD`.

Independent artifact hashes match the receipt:

| Artifact | SHA-256 |
| --- | --- |
| `vmlinux` | `067fef0fe101f9df0f16652080841ff88bddbe34e3074789120ba13df269a503` |
| ARM64 `Image` | `c1e27df303465bc4917bb24699f38792023a979c40728b58298b9b62815f000e` |
| `npu.ko` | `5e587db7065bc94755ed48f74bd8a904a1d37b243a2b8715701cbeef6e1459bb` |

`vmlinux` and `npu.ko` are ELF64 AArch64. The raw `Image` is identified as a
little-endian ARM64 boot executable with 4 KiB pages. Across `vmlinux` and
all 329 modules, 330 GNU build IDs were present and unique; the IDs for
`vmlinux` and `npu.ko` are respectively
`4bddda9c5d91b1c4857aafa02dbb69e11d2cc4b4` and
`2f850804d570522bb6324a1216e7aa19ed123a50`.

The receipt lists eight applied NPU patches. It separately records
`npu-shutdown-ownership-fix.patch` as an excluded hash-only input. No audio
patch is in the receipt or source delta. The NPU9 publication patch is not
applied to this source (an ordinary apply-check against this exact tree
succeeds); the separate NPU9/NPU10 follow-up changes and new audio change
are outside this artifact.

## Module inventory, flags, and same-build ABI

The 329 entries in `modules.order` have no duplicates and exactly match the
329 regular `.ko` files in the output tree. I independently used the existing
HCI candidate-preflight parser against these actual files and this build's
`Module.symvers` (SHA-256
`15fc69e815cb4da6cb3372f4b5141005ab2e770b0414b67f230f1b185bf03df7`). All
329 have `__versions`; all release prefixes match the expected kernel
release. There is one distinct vermagic flag suffix across the set and no
module fails the pinned normal-path `same_magic()` comparison. Across all
329, the scan found zero missing imported symbols, CRC mismatches, unknown
or ambiguous export CRCs, missing `module_layout` import records,
`module_layout` mismatches, or unverified `module_layout` records. The NPU
module has 234 versioned imports; its `module_layout` CRC `0x0e3c515c`
matches the same-build `Module.symvers` export.

This checks recorded module metadata against the same output tree; it is not
execution of the kernel module loader. The inspected output has 8,466 `.cmd`
records, including 5,066 clang compile/preprocess records: 5,039 target
`aarch64-linux-gnu`, 25 host-default build-helper records, and two
`arm-linux-gnueabi` records for the ARM64 `vdso32` objects. All 57 NPU C
records explicitly target `aarch64-linux-gnu` and include KCFI and shadow
call-stack instrumentation. The compile records also show array-bounds and
local-bounds sanitizers where configured; target flags include both
`pac-ret+leaf+bti` and intentional `none` variants.

## Evidence limits

This verifies one native-eight host output and its internal source/config,
toolchain, artifact, inventory, and symbol-version bindings. It does not
prove compatibility with the phone's current 325-module inventory, runtime
provider selection or load order, successful insertion, firmware or model
availability, NPU functionality, source-level lifecycle correctness,
reboot safety, or hardware acceptance. The receipt correctly keeps
`bootup_ready=false`, `bootup_authorized=false`, and
`device_or_deployment_action=false`. No additional build, package, publish,
phone, SSH, ADB, or hardware action was performed for this review.
