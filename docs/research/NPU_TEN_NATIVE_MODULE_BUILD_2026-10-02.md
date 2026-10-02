# NPU 9+10 native module-only build — 2026-10-02

## Result

The pinned native Clang 18 toolchain compiled the NPU module from the exact
eight-patch source plus the reviewed publication-ownership and pre-authorization
drain patches. The new module passed static import-version checks against the
complete matching native-eight `Module.symvers` set. The build used a fresh
output tree and targeted only `drivers/vision/npu.ko`.

This is host compilation and ABI metadata evidence. It does not establish
module loading, firmware acceptance, BOOTUP, runtime behavior, or device
acceptance. The binary, raw configuration, build log, and resource trace remain
in the private host build directory; the repository receipt contains hashes
and summarized checks only.

## Source and patch composition

The build source is commit
`709ac38b573d092e300f3787d0d5ba97dd333c0a`, tree
`a1606229eeb84e55175fcd43fd9a6f2faff200ef`, directly based on native-eight
commit `872bffb8ea2ea657f94d10b866dc655b5718d6db` (tree
`417e4a55e222b99ecca0f198e081d2d808ef5628`). The new kernel worktree was clean
at build start and remained clean afterward.

The patch files were SHA-verified, then applied sequentially with
`git apply --check --whitespace=error-all` followed by plain `git apply`:

| Patch | SHA-256 | Source file changed |
|---|---|---|
| `npu-publication-ownership.patch` | `2e2e2de8a28c5b408bfa0661535c358070130340318f1d0eaa0efe38edc11078` | `drivers/vision/npu/core/npu-if-protodrv-mbox2.c` |
| `npu-publication-drain-ownership.patch` | `7137b797c9b4658e1ec7e7829054b11ed684e51552933203d5af45b7a9743709` | `drivers/vision/npu/core/npu-session.c` |

The exact patch headers and composed diff change these two files only. A prior
three-file expectation was corrected after checking both patch byte identities
and paths: the protocol-driver declaration and mailbox handling are in the
same `npu-if-protodrv-mbox2.c` file. No third source change was added.

## Configuration, symbol inputs, and command

The private native configuration export and the new output `.config` both have
SHA-256
`d762d5fc71e369013ee36657d007063ce1f0faca9707d5f9ccfba2597b7fcd16`.
Fresh-output `olddefconfig` reported `No change to .config`. The preserved
configuration selects `CONFIG_EXYNOS_NPU=m`,
`CONFIG_NPU_USE_BOOT_IOCTL=y`, `CONFIG_MODVERSIONS=y`,
`CONFIG_CFI_CLANG=y`, `CONFIG_LTO_NONE=y`, and
`CONFIG_SHADOW_CALL_STACK=y`; no config setting or binary vermagic was edited.

The matching native-eight `Module.symvers` is SHA-256
`15fc69e815cb4da6cb3372f4b5141005ab2e770b0414b67f230f1b185bf03df7` with
17,283 rows. Its NPU owner has one row:
`0x982f6fee vision_register_device drivers/vision/npu EXPORT_SYMBOL`, SHA-256
`d1c251e9556acde53f523b84521ce9d55ce16d531f1e37b54117873852bfc900`. The
existing guarded camera Symvers filter was used to remove that old NPU
self-export from the modpost dependency input. Its 17,282 retained rows have
SHA-256 `add620bc3a3732654f23161e4c966c7076b020568683f527402c6253cb59a334`.
The input contains exactly one `module_layout` record, CRC `0x0e3c515c`.

The fresh build used `-j1`, `V=1`, an empty inherited environment, and the
native recipe with `LLVM_IAS` and explicit `LD` unset. The build was monitored
with an 8 GiB available-memory stop and a 16 GiB free-disk stop. It completed
with exit status 0 and no resource abort.

```sh
/usr/bin/nice -n 10 /usr/bin/env -i \
  PATH=/usr/lib/llvm-18/bin:/usr/bin:/bin \
  LC_ALL=C LOCALVERSION= TMPDIR=/tmp \
  /usr/bin/make \
  -C /home/corpunum/s22-linux/builds/npu-native-ten-kernel-20261002 \
  O=/home/corpunum/s22-linux/builds/npu-native-ten-module-only-out-20261002 \
  ARCH=arm64 LLVM=1 CROSS_COMPILE=aarch64-linux-gnu- \
  input-symdump=/home/corpunum/s22-linux/builds/npu-native-ten-dependencies-20261002.symvers \
  -j1 V=1 drivers/vision/npu.ko
```

Kbuild ran modpost with `-m -E` and the filtered dependency file. The resulting
`modules-only.symvers` is the single NPU export row above; it is not a full
kernel module export inventory. Both patched C translation units appear in the
actual Clang command log, and no compiler or modpost diagnostics were found.

## Module artifact and import versions

The private output artifact is
`builds/npu-native-ten-module-only-out-20261002/drivers/vision/npu.ko`:

- 14,769,536 bytes, SHA-256
  `a221c110ca2833b499b1fc7fbd8d34fbef38a60673aa4a70f0e919d818021200`.
- AArch64 ELF64 relocatable module, GNU Build ID
  `aee59cd0b95ed49038d0712106ac15bb4c1e9293`.
- Native vermagic: `5.10.260-g4e5c5ad7d950 SMP preempt mod_unload modversions aarch64`.
- `.modinfo` and `__versions` are present. There are 234 imported-version
  records; their ordered SHA-256 is
  `99aef867af8439d5c5cf1954ff01cd3f70eff870c5e3dbc19c733b386f0aaf55`.

The pinned host preflight validator compared the module imports with the full
baseline `Module.symvers`: evidence is complete, with zero missing symbols,
CRC mismatches, unknown CRCs, or ambiguous CRCs. The module and baseline both
record the `module_layout` CRC `0x0e3c515c`. The new module's full ordered import
record hash equals the existing native-eight NPU module's hash, and all 234
records resolve against the baseline export set.

## Boundaries

The existing native-eight full output was used only as the ABI baseline. It
contains its prior `Image` and 329 modules. The new output contains one `.ko`
and no `Image`; no full image or other module set was rebuilt. No module was
installed, packaged, stripped, copied into firmware, deployed, or loaded. No
phone, SSH, ADB, BOOTUP, firmware, or device-runtime action occurred.

Patch 10 addresses only cancellation before publication authorization. The
committed-publication `publish_done` wait remains unbounded; broader close,
reopen, firmware/IRQ quiescence, and hardware liveness remain unresolved.
BOOTUP remains refused.

The sanitized machine-readable evidence is in
[`s22-npu-ten-native-module-build-20261002.json`](../../evidence/s22-npu-ten-native-module-build-20261002.json).
