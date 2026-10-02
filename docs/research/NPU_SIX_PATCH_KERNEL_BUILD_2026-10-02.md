# NPU six-patch kernel build — 2026-10-02

## Scope

This receipt records a host-only kernel build of the reviewed six-patch NPU
profile. It starts from the exact pinned Lineage source commit and commits the
six ordered patch inputs in a separate clean kernel worktree. The output is a
local build artifact; it is not a recovery image and was not staged or
deployed. This clean profile is the pinned base plus the six NPU patches only.
It deliberately does not include HCI, close-range, camera, or audio kernel
changes from other profiles and is not a replacement for the working native
image. No phone, SSH, ADB, firmware, service, reboot, recovery, or deployment
operation is part of this task; boot and ramdisk packaging were not changed.

The frozen shutdown-ownership patch is recorded as an input identity check but
is not in the six-patch series. Its reviewed ordinary-apply overlap at
`npu-vertex.c:313` remains unchanged.

## Exact source and patch inputs

The base source is Lineage commit
`4e5c5ad7d950e4de0688b5663965f2075654b2ad`. The isolated kernel worktree is
`$KERNEL_BUILD_ROOT/npu-six-patch-kernel-20261002`; the six-patch
commit is `e5af0ba1cefc959094d03e1a136b8e33ff938b2a`, a direct child of the
pinned base. Ordinary `git apply --check` and `git apply` succeeded in this
order:

| Order | Patch | SHA-256 |
|---:|---|---|
| 1 | `npu-session-lifecycle-fix.patch` | `1554436cb6624c542f9e04ac22a3b3545e55f94c59d3025ee6bdc1ec43168251` |
| 2 | `npu-refcount-lifecycle-profile.patch` | `8385e4210a807f96f972757cd6ca74a8077b0127ab112d8b877d012ccdc3cb7b` |
| 3 | `npu-default-boot-callback-fix.patch` | `f5ce216e34df11d8c6adee4a99c36d63f73593cf379e29de3a9de828ec2ee1e7` |
| 4 | `npu-probe-unwind-fix.patch` | `d3e2e590d4d3c956b10c724a15db996dacd07def204332f50a1c0513f56b4948` |
| 5 | `npu-shutdown-lifecycle-profile.patch` | `b986e1896305fda55f1d702ed6f12dde646e4a84b3ce91009203b9d77b7a00e7` |
| 6 | `npu-shutdown-error-propagation.patch` | `08374e96792f24d1e0e4fbca594bfce35537af8acace9296b66f27b531564e43` |

The separate frozen ownership patch SHA-256 is
`a8af77122b4049fd38e21adfd01a8577d3e8f9bef1f68d0cfa091a5884a4d9f3`.
The build tool replays all six patches into a temporary Git index and requires
that tree to match the clean committed kernel source tree.

## Configuration and toolchain

The preserved `.config` is
`$KERNEL_BUILD_ROOT/close-range-clang21-llvm1-O-20260922/.config`,
SHA-256 `a147841a53f5b10c366a759d0e83525996a0ec5d8227a103b020cf2111400f9e`.
It retains `CONFIG_LTO_CLANG_THIN=y`, `CONFIG_CFI_CLANG=y`,
`CONFIG_MODVERSIONS=y`, `CONFIG_SHADOW_CALL_STACK=y`,
`CONFIG_EXYNOS_NPU=m`, `CONFIG_NPU_USE_HW_DEVICE=y`, and
`CONFIG_NPU_USE_BOOT_IOCTL=y`. The fresh output's `olddefconfig` left the
config byte-identical before compilation began.

The existing local toolchain is
`$KERNEL_BUILD_ROOT/toolchain-clang-r563880c-20260922/repo/clang-r563880c/bin`.
Clang reports Android Clang 21.0.0 based on r563880c; its SHA-256 is
`af0f25ca6818aed54c1cab03dc591acd549f385b8447148326a413e3e59c22b7` and
`ld.lld` SHA-256 is
`784146955ed87545385bf5c89b3b920ca7fe3ac83e034c3e6c53783ce544adf1`.
The verified LLVM helper payloads (resolved aliases share one payload hash)
were:

| Helpers | SHA-256 |
|---|---|
| `clang` wrapper | `af0f25ca6818aed54c1cab03dc591acd549f385b8447148326a413e3e59c22b7` |
| `clang-21` | `7202556a0ecae7ab00c67c1221e502692c7a46cf1531262fc62f597820078eef` |
| `ld.lld` / `lld` | `784146955ed87545385bf5c89b3b920ca7fe3ac83e034c3e6c53783ce544adf1` |
| `llvm-ar` / `llvm-ranlib` | `9833ebe9c5cb6be4711e667959cb70bf30434c8045dc135667c9f87b0d531b26` |
| `llvm-nm` | `96bc0865c29acfe30b45d84b4acca27ac14340657ffc6dbea58797f3853be6f1` |
| `llvm-objcopy` / `llvm-strip` | `1cdde2768f3c94aa5db19361f6857ffe80a1c3c7f87574b64df5da256c4ac959` |
| `llvm-objdump` | `68736b054c3d7035474e10b827908417b4d92e22b25bb1aa773f0add7f324b1f` |
| `llvm-readelf` / `llvm-readobj` | `5104576a3518575cf1887c2afa9249bbd0dc175cb9dc0f2af0d430fe0cb20bbe` |
| `llvm-size` | `50ac8d28bd266f5117de9c8199c84b8ddbaf6994a063f7a38d619fa373a750dd` |

The installed `aarch64-linux-gnu-` cross tools were present locally; no
toolchain was downloaded. The helper script verifies those GNU tools are on
`PATH`; their payloads are not pinned by the script.

## Build command and result

The command uses a fresh output tree and one build job:

```sh
make -C "$KERNEL_BUILD_ROOT/npu-six-patch-kernel-20261002" \
  O="$KERNEL_BUILD_ROOT/npu-six-patch-out-20261002" \
  ARCH=arm64 LLVM=1 LLVM_IAS=1 CROSS_COMPILE=aarch64-linux-gnu- \
  LD="$KERNEL_BUILD_ROOT/toolchain-clang-r563880c-20260922/repo/clang-r563880c/bin/ld.lld" \
  -j1 Image modules
```

The command exited 0 after about 3340 seconds. Fresh-output `olddefconfig`
preserved the exact input `.config` SHA; resource-monitor samples ranged from
13.1 to 24.7 GiB `MemAvailable` and 64.2 to 76.0 GiB free disk. The generated
kernel release is `5.10.260-ge5af0ba1cefc`; `modules.order` contains 329
entries. The raw output tree remains private under
`$KERNEL_BUILD_ROOT/npu-six-patch-out-20261002`.

| Artifact | Bytes | SHA-256 |
|---|---:|---|
| `Image` | 33,513,984 | `86e56e2c65e17c9e8df7c826696c9fd143f089bc305393e1b8b9ad1a8c1eabd6` |
| `vmlinux` | 586,724,072 | `2fb35d4ac9b023cdc6a83650966fb59e3899b921a6ef3a434674fcb097fc1c34` |
| `drivers/vision/npu.ko` | 17,323,960 | `639e5a3947fe4db360ba21bc3ec3b2f2bc7734fccf24489a4ceeb4b4f05aa2d1` |
| `Module.symvers` | 1,108,525 | `979104ac6a1c4e6279ea0597ef3bdccb1118d730b0c085302e90bc558b53689d` |

The NPU module reports vermagic
`5.10.260-ge5af0ba1cefc SMP preempt mod_unload modversions aarch64` and has
`.modinfo` and `__versions` sections. Its GNU build ID is
`8ca6aecea1647a746a27be52b047afd9ea32f844`; `vmlinux` build ID is
`146a0f11148bd8a46514ecee6f1c09622593f79f`. The module's 234 imported-symbol
CRC rows all match this build's `Module.symvers` (zero missing/mismatched); in
particular, `module_layout` is `0x0e3c515c` in both. This establishes only
same-build MODVERSIONS correspondence. It does not establish compatibility
with the phone's loaded modules.

Generated `include/generated/compile.h` SHA-256 is
`d8d72714c870b8665f01635e561a4713f415ba8d17e2894a9293515fa837b344`; generated
`include/generated/utsrelease.h` SHA-256 is
`5037e4822a36260976b1248b3173f67ccc0eb011b42d7106232e753ad6f5aac2`. Host and
user text embedded by the generated compile header is intentionally omitted.
The Kbuild timestamp was not normalized or pinned, so generated identities
and hashes are timestamp-sensitive; no reproducibility claim is made.

The actual launched helper source SHA-256 was
`13cec8b3b0cbb28f45856a374d843702c8e79cc7c80c286b3b19525b19aa3de1`; it alone
controlled the active build. The initial post-launch helper version
`5eb0900960fa687be99ca2e4fdc617d1ea2ecec6859c02780c06086973de46cd` was
reviewed but not executed; review found that it could return after the direct
make leader exited while a same-group descendant survived. Two new isolated,
real-subprocess regressions reproduced that failure before the fix: one with
the leader exiting on SIGINT and one with the leader already reaped. The final
future-run helper hashes group presence through bounded SIGINT/SIGTERM/SIGKILL
stages, reports false if disappearance is not confirmed, and preserves the
original monitor exception. Its reviewed source SHA-256 is
`c9122938aba78a7ce9a8af0242de7666aa69d81dc42f0d7d04e6bf8472e72998`. All 17
focused safety tests pass both normally and under `python -O`; the two real
process-group regressions pass after the fix. This helper change did not
restart or rerun Kbuild. The raw generated build receipt contains no helper
SHA, so the separate launch phase record is the evidence for the executed
`13cec8b3…` bytes; no completion-time file hash is presented as executed code.

## Evidence boundary

This successful `Image modules` result is host build evidence only. It does
not establish module loading, firmware readiness, NPU BOOTUP, shutdown safety
on hardware, a usable runtime, device acceptance, or compatibility with the
phone's loaded modules. These standalone profile artifacts are not authorized
to replace or be installed over the working native image. `bootup_ready=false`
and `bootup_authorized=false`; no deployment or phone action is authorized by
this build.
