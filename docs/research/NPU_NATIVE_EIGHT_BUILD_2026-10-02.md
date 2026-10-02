# Native/HCI/camera-preserving NPU eight-patch build — 2026-10-02

## Scope and status

This profile adds an explicit `native-eight` choice to the guarded NPU build
helper; its default remains the existing six-patch profile, including its
source, config, output path, and receipt format. The native-eight profile
preserves the reviewed HCI/camera source baseline and applies only the eight
reviewed NPU patches below. It does not include audio changes, the NPU9 patch,
the frozen shutdown-ownership patch, or the separate close-range kernel patch.
The frozen ownership patch is hash-checked as an excluded input, not applied.

The first authorized attempt used the six-profile Clang 21 toolchain and
stopped at the byte-identical-config guard before `Image modules`. The
coordinator matched the native config to its HCI artifact manifest and
directed a profile-only correction to the actual Ubuntu Clang/LLD 18
toolchain. The Clang 18 profile is implemented and preflight-verified; its
one-job host attempt is pending a fresh coordinator GO. No compiler setting
is relaxed, and no packaging or device action occurred.

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
`npu-native-eight-out-clang18-` prefix. The first failed output
`npu-native-eight-out-20261002` and existing six output
`npu-six-patch-out-20261002` are neither selected nor overwritten. Resource
guards remain enabled and the proposed job count is one.

After coordinator GO, run from this worker repository:

```sh
python3 tools/hardware/build-npu-six-profile.py --profile native-eight
```

This selects source `$KERNEL_BUILD_ROOT/npu-native-eight-kernel-20261002`,
config `native-config-export-20261002.config`, and fresh output
`$KERNEL_BUILD_ROOT/npu-native-eight-out-clang18-20261002`. It first invokes
`make -C <source> O=<output> ARCH=arm64 LLVM=1 LLVM_IAS=1
CROSS_COMPILE=aarch64-linux-gnu- LD=/usr/lib/llvm-18/bin/ld.lld olddefconfig`,
with `/usr/lib/llvm-18/bin` prepended to `PATH`, then exactly one `make`
invocation with those same arguments plus `-j1 Image modules`.
There is no package, deployment, module installation, or phone action in the
command path. A changed config or resource-guard abort is a stop, not a retry
or a reason to relax settings.

Preflight regressions passed: 22 tests each with `python3`, `python3 -O`, and
`PYTHONOPTIMIZE=1 python3`; `py_compile` also passed. Coverage includes default
six/native-eight separation, config and patch SHA failure, ownership exclusion,
wrong compiler/version, LLVM/GNU tool hash mismatch, wrong parent/dirty or
off-stack trees, reversed patch order, cross-profile and reused output refusal,
and bounded process-group cleanup. A live identity check matched the pinned
Clang 18, LLD 18, ten LLVM helpers, and three GNU cross tools; source and d762
config gates passed.

The committed helper was invoked once as
`python3 tools/hardware/build-npu-six-profile.py --profile native-eight` at
worker commit `f6904535c12437d9d3d41987d25d5d8ed3f072cc`; its launch-time SHA-256
was `922be92616aee9836f102126e2a42aefe84b9ec2174b54312ce925cd85e66867`. The
execution session handle was `4798`. Resource checks passed at start (36.7 GB
available memory and 63,276,441,600 bytes free disk), and the selected output
path was fresh. The helper copied the exact d762 config to the new output and
ran its configured `olddefconfig` command. That command changed only these
five config records:

| Setting | Preserved native export | `olddefconfig` output |
|---|---|---|
| `CONFIG_CC_VERSION_TEXT` | Ubuntu Clang 18.1.3 | Pinned Android Clang 21.0.0 (r563880c) |
| `CONFIG_CLANG_VERSION` | `180103` | `210000` |
| `CONFIG_AS_VERSION` | `180103` | `210000` |
| `CONFIG_LLD_VERSION` | `180103` | `210000` |
| `CONFIG_HAS_LTO_CLANG` | unset | `y` |

The original config remains as `.config.old` (236,183 bytes, SHA-256
`d762d5fc71e369013ee36657d007063ce1f0faca9707d5f9ccfba2597b7fcd16`). The
generated output config remains private in the fresh output (236,421 bytes,
SHA-256 `ae005dd1b63af228db1b562eac0af1c7cc53231d02016f59af8e8a9e26ca60a5`),
along with `olddefconfig.log`; these files were not copied into this
repository. The helper exited status 2 with
`BUILD_PREFLIGHT_FAILED olddefconfig changed the preserved configuration;
build refused`. The `-j1 Image modules` make command was never launched. The
Clang 21 output is preserved for review and will not be reused by the corrected
profile.

## Evidence boundary

The `olddefconfig` Kbuild preparation target ran, but `Image modules` did not.
There are no generated kernel artifact hashes, build IDs, new kernel release,
module vermagic, or import-CRC results to report. The config/toolchain metadata
mismatch is preserved as a refusal, not normalized or retried. A future build
has been directed to use the manifest-matched Clang 18 toolchain and a distinct
fresh output; that invocation awaits its new coordinator GO. A host compile,
if later authorized, is not module-load, firmware, NPU BOOTUP, runtime,
device-acceptance, or deployment evidence.
Existing BOOTUP refusal remains in force: the publication drain still has an
unbounded `wait_for_completion(&waiter->publish_done)` if synchronous mailbox
publication never returns, and the existing ownership/liveness evidence does
not authorize hardware BOOTUP.
