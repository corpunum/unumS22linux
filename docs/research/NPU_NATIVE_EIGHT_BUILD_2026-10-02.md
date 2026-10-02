# Native/HCI/camera-preserving NPU eight-patch build — 2026-10-02

## Scope and status

This profile adds an explicit `native-eight` choice to the guarded NPU build
helper; its default remains the existing six-patch profile, including its
source, config, output path, and receipt format. The native-eight profile
preserves the reviewed HCI/camera source baseline and applies only the eight
reviewed NPU patches below. It does not include audio changes, the NPU9 patch,
the frozen shutdown-ownership patch, or the separate close-range kernel patch.
The frozen ownership patch is hash-checked as an excluded input, not applied.

Two guarded attempts reached `olddefconfig` and stopped at the byte-identical
config guard before `Image modules`. The first used Android Clang 21 rather
than the native compiler and changed compiler metadata. The second used the
manifest-matched Ubuntu Clang 18 but passed `LLVM_IAS=1`, enabling the Kconfig
capability symbol `CONFIG_HAS_LTO_CLANG=y`. Inspection found the HCI build
recipe omits both `LLVM_IAS` and explicit `LD`; a bounded `olddefconfig` check
with those recipe variables left d762 unchanged. The native profile now mirrors
that recipe while the six-profile command remains unchanged. Its new frozen
output path and profile-specific tests are ready for coordinator review. No
compile, packaging, or device action occurred.

The task selected `gpt-6-luna` with reasoning `max` as the configured worker
setting. This is recorded as `explicitly_configured`; no backend
self-identification or independent runtime attestation is claimed.

## Source identities and provenance

The pinned Lineage base is
`4e5c5ad7d950e4de0688b5663965f2075654b2ad`. The preserved source baseline is
the clean camera worktree commit
`3fca50941422439b2019db2e4a3dc1016b2138a1`, a direct descendant of that base.
Its three commits are:

| Commit | Change |
|---|---|
| `f52cbbd7e2783d529e1e5742d94e0fd64889bbdf` | Restore HCI socket operations (`net/bluetooth/hci_sock.c`) |
| `104e6b98153f9f00d571ec780687398c7a263057` | Unwind failed camera PHY LDO acquire (`drivers/media/platform/exynos/camera/is-resourcemgr.c`) |
| `3fca50941422439b2019db2e4a3dc1016b2138a1` | Check camera sensor resume result (same camera source file) |

The resulting isolated kernel worktree is
`$KERNEL_BUILD_ROOT/npu-native-eight-kernel-20261002`, branch
`codex/npu-native-eight-kernel-20261002`. The exact eight-patch source commit
is `872bffb8ea2ea657f94d10b866dc655b5718d6db`, directly on `3fca509...`; its
tree is `417e4a55e222b99ecca0f198e081d2d808ef5628`. The committed worktree is
clean. Ordinary sequential `git apply --check --whitespace=error-all` and
`git apply --whitespace=error-all` succeeded for all eight inputs, and the
builder independently replayed the ordered stack into a temporary index and
matched the exact source tree. The commit changes eleven files, all under
`drivers/vision/npu/`; it has no `fs/file.c`, audio, or NPU9 change.

The patch order and SHA-256 identities are:

| Order | Patch | SHA-256 |
|---:|---|---|
| 1 | `npu-session-lifecycle-fix.patch` | `1554436cb6624c542f9e04ac22a3b3545e55f94c59d3025ee6bdc1ec43168251` |
| 2 | `npu-refcount-lifecycle-profile.patch` | `8385e4210a807f96f972757cd6ca74a8077b0127ab112d8b877d012ccdc3cb7b` |
| 3 | `npu-default-boot-callback-fix.patch` | `f5ce216e34df11d8c6adee4a99c36d63f73593cf379e29de3a9de828ec2ee1e7` |
| 4 | `npu-probe-unwind-fix.patch` | `d3e2e590d4d3c956b10c724a15db996dacd07def204332f50a1c0513f56b4948` |
| 5 | `npu-shutdown-lifecycle-profile.patch` | `b986e1896305fda55f1d702ed6f12dde646e4a84b3ce91009203b9d77b7a00e7` |
| 6 | `npu-shutdown-error-propagation.patch` | `08374e96792f24d1e0e4fbca594bfce35537af8acace9296b66f27b531564e43` |
| 7 | `npu-mailbox-missing-callback-reclaim.patch` | `f109b57381b3f2afcf2638b518f50db59ef8c9f784debd949488c74f8ea5c39b` |
| 8 | `npu-mailbox-debug-walk-bounds.patch` | `20c700bfa11f13836c76c88cca28a4f8dfa459e5cf146292a814880cd5850b29` |

The frozen `npu-shutdown-ownership-fix.patch` identity is
`a8af77122b4049fd38e21adfd01a8577d3e8f9bef1f68d0cfa091a5884a4d9f3`; it is
verified but excluded because this profile is only the reviewed eight-patch
stack, not the separate ownership/refcount profile.

### `close_range` boundary

The three preserved HCI/camera commits modify `hci_sock.c` and
`is-resourcemgr.c`; inspection of their changed paths found no `fs/file.c`
change. The separate `close-range-kernel-fix.patch` explicitly changes
`fs/file.c` and has SHA-256
`57bf1751dada3271478474472aa38b1c4d10fedbe3c9c0548769efeb654be6fc`; that
patch is not part of the eight-patch profile. In particular,
the old six-profile config directory's `close-range-...` name is not evidence
that its kernel source contains the close-range patch, nor is it evidence for
this new native profile. The Pi-scoped userspace compatibility wrapper remains
separate. No close-range kernel inclusion is inferred from a directory label.

## Preserved native configuration and toolchain

The exact current native config was recovered by the coordinator through a
bounded, read-only export of `/proc/config.gz`; no phone write occurred. The
compressed input was 51,478 bytes with SHA-256
`61adbadbdcff453fa04cb896087a173f0e828a745296750f06f6e2d44b79346f`. Its
236,183-byte decompressed host-only export has SHA-256
`d762d5fc71e369013ee36657d007063ce1f0faca9707d5f9ccfba2597b7fcd16`. The
export remains at its private local build path and the raw config is not
included in this repository or the public receipt. The builder requires this
exact SHA; it has no fallback to the six-profile `a147...` config.

The config pins
`CONFIG_LOCALVERSION="-g4e5c5ad7d950"` with
`CONFIG_LOCALVERSION_AUTO` disabled, matching the reviewed native release
`5.10.260-g4e5c5ad7d950`. It retains the exported settings, including
`CONFIG_SHADOW_CALL_STACK=y`, `CONFIG_LTO_NONE=y`, `CONFIG_CFI_CLANG=y`,
`CONFIG_MODVERSIONS=y`, the Bluetooth/QCA HCI options,
`CONFIG_VIDEO_EXYNOS_PABLO_ISP=m`, and the Exynos NPU/BOOT_IOCTL options. The
profile does not edit `.config` or change any of those hardening settings:
`LTO_NONE` remains as exported, while CFI, MODVERSIONS, and shadow call stack
remain enabled. It does not patch binary vermagic. Fresh-output `olddefconfig`
must leave the config byte-identical or the build stops before compilation.

The HCI artifact manifest at
`$KERNEL_BUILD_ROOT/bt-hci-loader-compatible-20260924-repro/manifest.json`
records the same d762 config SHA, a complete build, and Ubuntu Clang/LLD 18.1.3.
The corrected native-eight profile selects only `/usr/lib/llvm-18/bin`, with
exact first-line versions `Ubuntu clang version 18.1.3 (1ubuntu1)` and
`Ubuntu LLD 18.1.3 (compatible with GNU linkers)`. Clang SHA-256 is
`8ef402d453d1ba4902e4ee0f0f847f6cfa01400c95aa43c24e97818b9c0e3f45`; `ld.lld`
SHA-256 is
`7ad9a0e8fe6d0e79b71172d731e33872c0274e49fceb7b516d774876d5a58ade`. The
profile checks resolved helper aliases and pins all ten LLVM helpers plus the
existing three GNU cross tools before Kbuild:

| Tool/payload | SHA-256 |
|---|---|
| `clang` | `8ef402d453d1ba4902e4ee0f0f847f6cfa01400c95aa43c24e97818b9c0e3f45` |
| `ld.lld` / `lld` | `7ad9a0e8fe6d0e79b71172d731e33872c0274e49fceb7b516d774876d5a58ade` |
| `llvm-ar` / `llvm-ranlib` | `eedd2efbdee80acf60e17e10adeddf31be0347226245f935be95efa3ae00ec79` |
| `llvm-nm` | `3f85dd567c2806f2c031e317871e7145f858072fc9595aba4dc33d2e3a671402` |
| `llvm-objcopy` / `llvm-strip` | `f52b9997b3c5019b4b3043e12b1ae2e821df67996ca344921c234c89c4d23e34` |
| `llvm-objdump` | `4f98b86448d23bd1f858c50e93fbfc799f1c3640961a95e3b6c89d229a1b91bb` |
| `llvm-readelf` / `llvm-readobj` | `8ed942a8c33f191480253ff7f236b7e49966c7441d12063d49dce9743aba9a6d` |
| `llvm-size` | `401e6835686b116baeaa8b98a5ebee54fbe66148b4a3fcda5436ef243a3653f3` |
| `aarch64-linux-gnu-gcc` | `cd90adc7801f4595267f61a5d25bd3a0c6beb2f9f1f107ab919a97a12972dc9a` |
| `aarch64-linux-gnu-ld` | `7c903ac277dd1f5c4397277db865f12d8239fe45782f71afbeb3d42180a4b1ae` |
| `aarch64-linux-gnu-nm` | `96dbed79b11f6cc13b060dd5ca705a277bb5bdecd714df1c470ffaafb2513727` |

Resolved paths, sizes, and SHA-256 identities are captured in the build phase
and receipt. Android Clang 21/r563880c remains exclusive to the default six
profile; its source/config defaults and receipt format remain unchanged.

## Preflight and proposed command

Before compilation, the helper verifies the profile's direct parent and clean
worktree, the exact ordered patch tree and hashes, the private config hash and
required settings, the profile-specific Clang/LLD version and hashes, helper
hashes, and a fresh output path under `$KERNEL_BUILD_ROOT/builds` with the
`npu-native-eight-out-clang18-` prefix. The failed outputs
`npu-native-eight-out-20261002` and `npu-native-eight-out-clang18-20261002`,
the olddefconfig-only check output `npu-native-eight-olddefconfig-check-20261002`,
and existing six output `npu-six-patch-out-20261002` are neither selected nor
overwritten. Resource guards remain enabled and the proposed job count is one.

After coordinator GO, run from this worker repository:

```sh
python3 tools/hardware/build-npu-six-profile.py --profile native-eight
```

This selects source `$KERNEL_BUILD_ROOT/npu-native-eight-kernel-20261002`,
config `native-config-export-20261002.config`, and fresh output
`$KERNEL_BUILD_ROOT/npu-native-eight-out-clang18-recipe-20261002`. It sets
`LOCALVERSION=` and prepends `/usr/lib/llvm-18/bin` to `PATH`, then invokes
`make -C <source> O=<output> ARCH=arm64 LLVM=1
CROSS_COMPILE=aarch64-linux-gnu- olddefconfig`. The native profile deliberately
omits `LLVM_IAS` and explicit `LD`, matching the HCI manifest's recipe; with
`LLVM=1`, Kbuild selects `ld.lld` and its Makefile retains integrated assembly
when `LLVM_IAS` is unset. Before creating the output, the helper refuses if
any of `LLVM_IAS`, `LD`, `CC`, `AS`, `MAKEFLAGS`, `MFLAGS`, `GNUMAKEFLAGS`, or
`MAKEOVERRIDES` is inherited, so omitted variables cannot be silently
reintroduced from the parent environment. It then runs exactly one make
invocation with those same arguments plus `-j1 Image modules`.
There is no package, deployment, module installation, or phone action in the
command path. A changed config or resource-guard abort is a stop, not a retry
or a reason to relax settings.

Preflight regressions passed: 24 tests each with `python3`, `python3 -O`, and
`PYTHONOPTIMIZE=1 python3`; `py_compile` also passed. Coverage includes default
six/native-eight separation, config and patch SHA failure, ownership exclusion,
wrong compiler/version, LLVM/GNU tool hash mismatch, wrong parent/dirty or
off-stack trees, reversed patch order, cross-profile and reused output refusal,
profile-specific exact make arguments and environment, inherited native build
override refusal, and bounded process-group cleanup. A live identity check
matched the pinned Clang 18, LLD 18, ten LLVM helpers, and three GNU cross
tools; source and d762 config gates passed. The active shell had none of the
eight refused names set and had no inherited `LOCALVERSION` value.

The first Clang 21 attempt used worker commit
`f6904535c12437d9d3d41987d25d5d8ed3f072cc` and launch-time helper SHA-256
`922be92616aee9836f102126e2a42aefe84b9ec2174b54312ce925cd85e66867` in session
`4798`. It changed compiler/version config records and set
`CONFIG_HAS_LTO_CLANG=y`; the output
`npu-native-eight-out-20261002` is preserved and not reused. No `Image modules`
target ran.

The second guarded attempt used worker commit
`f9756516800fd225e2e77639c766e18cc2a26503`, helper SHA-256
`be249211fb197ce065d4a4f8770b8d5e0443b739b1fcabddec87fa003f07328a`, and
command `python3 -I -B tools/hardware/build-npu-six-profile.py --profile
native-eight` (execution session `45923`). The output was fresh and the live
source, d762 config, Clang 18/LLD 18, ten LLVM helper, and three GNU tool gates
passed. `olddefconfig` changed only these config records:

| Setting | Preserved native export | Clang 18 `LLVM_IAS=1` output |
|---|---|---|
| `CONFIG_HAS_LTO_CLANG` | unset | `y` |
| LTO choice records | `CONFIG_LTO_NONE=y` | remains `CONFIG_LTO_NONE=y`; full/thin unset records emitted |

The exact input remains `.config.old` (236,183 bytes, SHA-256
`d762d5fc71e369013ee36657d007063ce1f0faca9707d5f9ccfba2597b7fcd16`). The
derived output config SHA-256 is
`3070889600076df1a1ae847f79f8d2ff5a2c195f79ef3ef3c94ab9b197d79d6c`. The
output and `olddefconfig.log` remain private under
`npu-native-eight-out-clang18-20261002`; they are not copied into this
repository. The helper exited status 2 with
`BUILD_PREFLIGHT_FAILED olddefconfig changed the preserved configuration;
build refused`. The `-j1 Image modules` make command was never launched.

To isolate the cause, the exact d762 config was copied to the fresh
`npu-native-eight-olddefconfig-check-20261002` scratch output and only the HCI
manifest recipe variables were used with `olddefconfig` as the target:
`env PATH=/usr/lib/llvm-18/bin:$PATH LOCALVERSION= make -C <source> O=<scratch>
ARCH=arm64 LLVM=1 CROSS_COMPILE=aarch64-linux-gnu- olddefconfig`. No `LLVM_IAS`
or explicit `LD` argument was supplied. Kbuild reported `No change to
.config`; the resulting config SHA remained d762. Source `arch/Kconfig`
defines `HAS_LTO_CLANG` only when `test $(LLVM_IAS) -eq 1`, while the top-level
Makefile uses integrated assembly when `LLVM_IAS` is unset (`ifneq` against
zero). Thus the prior override affected a capability symbol, not the selected
LTO mode; `CONFIG_LTO_NONE=y` was preserved. This reproduces the HCI recipe's
configuration behavior without starting compilation.

## Evidence boundary

Both guarded attempts stopped before the `Image modules` target. There are no
generated kernel artifact hashes, build IDs, new kernel release, module
vermagic, or import-CRC results to report. Both failure outputs and the bounded
scratch output remain preserved. The corrected helper matches the native HCI
recipe and keeps the exact-config guard; it awaits coordinator review and a new
GO before one host compile. A host compile, if authorized, is not module-load,
firmware, NPU BOOTUP, runtime, device-acceptance, or deployment evidence.
Existing BOOTUP refusal remains in force: the publication drain still has an
unbounded `wait_for_completion(&waiter->publish_done)` if synchronous mailbox
publication never returns, and the existing ownership/liveness evidence does
not authorize hardware BOOTUP.
