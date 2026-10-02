# NPU 11+12 native module build preparation — 2026-10-03

## State

This is a plan-only preparation record. The composed source tree and the
configuration, symbol, toolchain, command, and resource inputs have been
checked. No `olddefconfig`, native module compile, kernel/image build, package,
deployment, module load, phone/SSH/ADB access, or BOOTUP/runtime action has
been performed. The coordinator has not issued the one-module-build GO; stop
here until that explicit authorization.

The exact new output directory, filtered Symvers output, and raw
`olddefconfig` log are absent. A fresh host resource sample is above the 8 GiB
available-memory and 16 GiB remaining-disk monitor thresholds, but below the
build helper's separate 32 GiB start-disk gate. The current host therefore is
not launch-ready under the helper's full preflight policy. Do not delete or
move unrelated files to force that gate; recheck resources after GO and stop
if the pinned gate is still unmet.

## Source composition

The source worktree is
`/home/corpunum/s22-linux/builds/npu-native-twelve-kernel-20261003`, branch
`codex/npu-native-twelve-kernel-20261003`. Its clean HEAD is
`e9c3016233a72ceccb13e537f0b7ef72426582b9`, tree
`f917408e1c3388c87a8ed8f0220e4f9c3c9c6a4e`, directly parented by the preserved
native-ten source commit `709ac38b573d092e300f3787d0d5ba97dd333c0a` (tree
`a1606229eeb84e55175fcd43fd9a6f2faff200ef`). Native ten is based on native
eight commit `872bffb8ea2ea657f94d10b866dc655b5718d6db` (tree
`417e4a55e222b99ecca0f198e081d2d808ef5628`). The committed composed delta
from native ten changes exactly these two source paths:

| Patch, applied in order | SHA-256 | Source path |
|---|---|---|
| `npu-mailbox-msgid-validation.patch` | `c8366edfab42090ac09a6c366ad3c535a62e13384dd494ed685bbfe524a64d14` | `drivers/vision/npu/core/npu-util-msgidgen.c` |
| `npu-fw-report-lock-unwind.patch` | `71c2fa44f0fe42bd94ee416fb09453185dd418b408ba15937ec3c787a5e9bf5d` | `drivers/vision/npu/core/npu-log.c` |

The patch files are unchanged from their reviewed author worktree. The source
HEAD is committed and clean; the module has not been compiled. Independent
source review is reported cleared by the coordinator (review identifier
`74059b4d`). That status is not runtime evidence and does not authorize a
build by itself.

## Configuration and symbol inputs

The preserved configuration export is
`/home/corpunum/s22-linux/builds/native-config-export-20261002.config`, 236,183
bytes, SHA-256
`d762d5fc71e369013ee36657d007063ce1f0faca9707d5f9ccfba2597b7fcd16`. The
existing native-ten output `.config` has the same hash. Required pinned values
include `CONFIG_EXYNOS_NPU=m`, `CONFIG_NPU_USE_BOOT_IOCTL=y`,
`CONFIG_MODVERSIONS=y`, `CONFIG_CFI_CLANG=y`, `CONFIG_LTO_NONE=y`,
`CONFIG_SHADOW_CALL_STACK=y`, `CONFIG_LOCALVERSION="-g4e5c5ad7d950"`, and
`CONFIG_LOCALVERSION_AUTO=n`. The expected release is
`5.10.260-g4e5c5ad7d950`. The new output `.config` does not yet exist; before
`olddefconfig`, its copied input must match the pinned export hash, and after
`olddefconfig` it must still match exactly or compilation is refused.

The matching native-eight full `Module.symvers` is
`/home/corpunum/s22-linux/builds/npu-native-eight-out-clang18-recipe-20261002/Module.symvers`,
1,108,525 bytes (17,283 rows), SHA-256
`15fc69e815cb4da6cb3372f4b5141005ab2e770b0414b67f230f1b185bf03df7`. It has
one `drivers/vision/npu` row, the existing
`0x982f6fee vision_register_device drivers/vision/npu EXPORT_SYMBOL`, with
SHA-256
`d1c251e9556acde53f523b84521ce9d55ce16d531f1e37b54117873852bfc900`; the
single `module_layout` record is `0x0e3c515c`. The pinned filter helper
`tools/hardware/prepare-camera-modpost-symvers.py` is SHA-256
`2404fab4a2ed469eab1930a7275c28fd3f5a0bbb1b23c88369338ca23cd26c67`. It
will exclude only owner `drivers/vision/npu`, require that exact one-row hash
and input hash, and write a new, exclusive output
`/home/corpunum/s22-linux/builds/npu-native-twelve-dependencies-20261003.symvers`.
That output is currently absent. The prior native-ten filtered file is only a
read-only cross-check (hash
`add620bc3a3732654f23161e4c966c7076b020568683f527402c6253cb59a334`); it will
not be substituted for re-deriving the twelve build's pinned input.

## Pinned execution plan (not run)

The planned fresh output is
`/home/corpunum/s22-linux/builds/npu-native-twelve-module-only-out-20261003`;
it was absent at the recorded preflight. After coordinator GO only, recheck
source/config/helper/tool hashes, absence of every reserved output, and the
resource gate. Run the guarded filter helper with `/usr/bin/python3 -I` and an
empty environment, then create the mode-0700 output and copy the exact config
export to `.config`. Run only the commands below; preserve stdout/stderr from
`olddefconfig` verbatim at `.../olddefconfig.log`, compare config SHA before
and after, and refuse the module command on any mismatch.

```sh
/usr/bin/env -i PATH=/usr/bin:/bin LC_ALL=C /usr/bin/python3 -I \
  /home/corpunum/s22-workers/npu-msgid-validation-20261002/tools/hardware/prepare-camera-modpost-symvers.py \
  --input /home/corpunum/s22-linux/builds/npu-native-eight-out-clang18-recipe-20261002/Module.symvers \
  --output /home/corpunum/s22-linux/builds/npu-native-twelve-dependencies-20261003.symvers \
  --exclude-owner drivers/vision/npu \
  --expected-input-sha256 15fc69e815cb4da6cb3372f4b5141005ab2e770b0414b67f230f1b185bf03df7 \
  --expected-excluded-count 1 \
  --expected-excluded-sha256 d1c251e9556acde53f523b84521ce9d55ce16d531f1e37b54117873852bfc900 \
  --expected-module-layout-crc 0x0e3c515c

/usr/bin/env -i PATH=/usr/lib/llvm-18/bin:/usr/bin:/bin LC_ALL=C \
  LOCALVERSION= TMPDIR=/tmp \
  /usr/bin/make -C /home/corpunum/s22-linux/builds/npu-native-twelve-kernel-20261003 \
  O=/home/corpunum/s22-linux/builds/npu-native-twelve-module-only-out-20261003 \
  ARCH=arm64 LLVM=1 CROSS_COMPILE=aarch64-linux-gnu- olddefconfig

/usr/bin/nice -n 10 /usr/bin/env -i \
  PATH=/usr/lib/llvm-18/bin:/usr/bin:/bin LC_ALL=C LOCALVERSION= TMPDIR=/tmp \
  /usr/bin/make -C /home/corpunum/s22-linux/builds/npu-native-twelve-kernel-20261003 \
  O=/home/corpunum/s22-linux/builds/npu-native-twelve-module-only-out-20261003 \
  ARCH=arm64 LLVM=1 CROSS_COMPILE=aarch64-linux-gnu- \
  input-symdump=/home/corpunum/s22-linux/builds/npu-native-twelve-dependencies-20261003.symvers \
  -j1 V=1 drivers/vision/npu.ko
```

The module argv above will be passed to the already-pinned
`build-npu-six-profile.py:run_monitored_build` implementation (helper SHA-256
`56f39759e4a098562cbafd634a659b007be2a540a4ada6234962711be026e5d5`), with
source set to the exact committed kernel tree, output set to the fresh module
directory, and child environment exactly `PATH`, `LC_ALL=C`, empty
`LOCALVERSION`, and `TMPDIR=/tmp`. It records the raw module build log,
resource samples, and phase receipt under that private output. Its remaining
resource abort limits are 8 GiB available memory and 16 GiB free disk; its
full-build preflight additionally requires 12 GiB available memory and 32 GiB
free disk. The preflight below is not a substitute for the coordinator GO.

At the later authorized run, record actual prelaunch UTC time/resources,
source HEAD/tree and cleanliness immediately before and after, config SHA
before/after `olddefconfig`, filter receipt, helper/interpreter/wrapper hashes,
exact argv/environment, and raw log paths. Then verify only the requested
`drivers/vision/npu.ko` exists in the fresh output (no `Image` or other module
set), and reconcile its import/version data and export rows against the pinned
native-eight and native-ten records. Until those results exist, ABI/build
acceptance is `NOT_RUN`; the previous build's 234 imports and one-export result
are expectations to check, not claims about this source.

## Current preflight snapshot and evidence boundary

At `2026-10-02T22:01:44Z`, read-only inspection found 23,861,620,736 bytes
available memory and 32,119,492,608 bytes free on the filesystem containing
`/srv` and the build output. This is above the monitor's 8 GiB/16 GiB remaining
limits and the helper's 12 GiB memory start limit, but below its 32 GiB disk
start limit (`34,359,738,368` bytes). Here `/srv` resolves to the root ext4
filesystem, unlike an earlier coordinator snapshot that reported a separate
`/srv` capacity; this snapshot takes precedence for a later launch check.
No filesystem cleanup was attempted.

The sanitized machine-readable plan is
[`s22-npu-twelve-native-module-build-20261003.json`](../../evidence/s22-npu-twelve-native-module-build-20261003.json).
It records every build phase as `NOT_RUN`. Host source review and host tests
are not module compilation, module loading, firmware response, BOOTUP, or
device/runtime acceptance. BOOTUP is not authorized.
