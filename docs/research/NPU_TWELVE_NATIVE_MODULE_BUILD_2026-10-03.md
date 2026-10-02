# NPU 11+12 native module build preparation — 2026-10-03

## State

This is a plan-only preparation record. The composed source tree and the
configuration, symbol, toolchain, command, and resource inputs have been
checked. No `olddefconfig`, native module compile, kernel/image build, package,
deployment, module load, phone/SSH/ADB access, or BOOTUP/runtime action has
been performed. The coordinator has not issued the one-module-build GO; stop
here until that explicit authorization.

The exact new output directory, filtered Symvers output, and raw
`olddefconfig` log are absent. The private wrapper at
`tools/hardware/build-npu-twelve-module-only.py` defaults to read-only plan
mode; execution requires `--execute`, the reviewed wrapper SHA, and an exact
source-and-wrapper-bound coordinator GO token. Its operation-specific
prelaunch gate is 12 GiB available memory plus 24 GiB free on the build
filesystem. It calls only the pinned monitor helper's `run_monitored_build`,
which retains its 8 GiB memory and 16 GiB disk abort limits. The helper's
separate 32 GiB full-profile initial disk constant remains unchanged; this
single-module wrapper does not call the full Image-profile orchestrator or
weaken its global policy. The 8 GiB gap between the operation start and
remaining-disk gates is a guard, not a storage reservation or a guarantee
against concurrent owner activity. No cleanup or resource reservation was
attempted.

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

## Pinned module-only wrapper (not executed)

The wrapper SHA-256 is
`c4a25c93696217c1ae905df51bb57ece4ca652291b6f001a47c2ab23e8aca842`. Its
plan-only path verifies the exact source HEAD/tree/parent and clean state,
config, both patch digests, the 13 native LLVM/GNU tools, Python/make/env/nice,
both helper hashes and versions, fresh output paths, and current operation
resource gate. It does not create output files. It was read-only exercised in
normal Python, `-O`, and `PYTHONOPTIMIZE=1` modes; all reported the same source
and helper identities, gate met, and output paths absent. A negative execution
check with the correct wrapper SHA but no coordinator token exited 2 before
creating either reserved output.

```sh
# Read-only identity, command, and resource plan; no files are created.
/usr/bin/env -i PATH=/usr/bin:/bin LC_ALL=C /usr/bin/python3 -I -B \
  /home/corpunum/s22-workers/npu-twelve-native-build-20261003/tools/hardware/build-npu-twelve-module-only.py \
  --plan-only

# Execution is prohibited until a separate reviewed coordinator GO. The
# coordinator must supply this exact wrapper SHA and the matching token.
/usr/bin/env -i PATH=/usr/bin:/bin LC_ALL=C /usr/bin/python3 -I -B \
  /home/corpunum/s22-workers/npu-twelve-native-build-20261003/tools/hardware/build-npu-twelve-module-only.py \
  --execute \
  --expect-wrapper-sha256 c4a25c93696217c1ae905df51bb57ece4ca652291b6f001a47c2ab23e8aca842 \
  --coordinator-go-token GO:NPU12-MODULE-ONLY:e9c3016233a72ceccb13e537f0b7ef72426582b9:c4a25c93696217c1ae905df51bb57ece4ca652291b6f001a47c2ab23e8aca842
```

On explicit GO, the wrapper rechecks all identities, paths, and the 12/24 GiB
operation gate before writing. It calls the guarded filter helper in an
exclusive fresh output transaction, verifies the expected 17,282-row filtered
hash, then copies the preserved config exclusively and invokes only
`olddefconfig`. Its stdout/stderr is retained verbatim at
`.../olddefconfig.log`; the before/after config SHA must match or module
compilation is refused. It also records filter output and each phase in a
private append-only `module-only-phase.jsonl` journal. There is no retry or
cleanup of partial outputs.

The only compilation argv is
`/usr/bin/nice -n 10 /usr/bin/env -i PATH=/usr/lib/llvm-18/bin:/usr/bin:/bin
LC_ALL=C LOCALVERSION= TMPDIR=/tmp /usr/bin/make -C
<pinned-source> O=<fresh-output> ARCH=arm64 LLVM=1
CROSS_COMPILE=aarch64-linux-gnu-
input-symdump=<pinned-filtered-symvers> -j1 V=1 drivers/vision/npu.ko`.
It is passed to the already-pinned
`build-npu-six-profile.py:run_monitored_build` implementation (helper SHA-256
`56f39759e4a098562cbafd634a659b007be2a540a4ada6234962711be026e5d5`), with
source set to the exact committed kernel tree, output set to the fresh module
directory, and child environment exactly `PATH`, `LC_ALL=C`, empty
`LOCALVERSION`, and `TMPDIR=/tmp`. It records the raw module build log,
resource samples, and phase receipt under that private output. The wrapper
fails closed if `resource_abort` is true even when the helper reports a zero
child exit, and converts any helper `SystemExit(0)` into nonzero failure. Both
olddefconfig and monitor-run build process groups use the pinned bounded
SIGINT/SIGTERM/SIGKILL cleanup helper; its confirmation is journaled. Success
is accepted only for a zero make exit without resource abort, a stable config
and source identity, exactly one `*.ko` at `drivers/vision/npu.ko`, and no
`Image`.

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

The wrapper's latest read-only point-in-time sample was
`2026-10-02T22:23:30Z`: 28,263,329,792 bytes available memory and
31,187,976,192 bytes free at `/home/corpunum/s22-linux/builds`. Both operation
start thresholds (12 GiB / 24 GiB) passed at that instant. An earlier host
sample at `22:01:44Z` also saw this build filesystem as the root ext4 mount;
it was below the *full Image-profile* 32 GiB initial-disk gate but above this
module-only 24 GiB gate. The coordinator's separate `/srv/s22` collector/PID1
namespace sample is a different destination and is not comparable to the
host's `/home/corpunum/s22-linux/builds` filesystem. This worker made no phone
access and did not treat either namespace's reading as superseding the other.
These are point-in-time readings only; the 16 GiB monitor threshold includes
no reservation against concurrent host or owner activity.

The sanitized machine-readable plan is
[`s22-npu-twelve-native-module-build-20261003.json`](../../evidence/s22-npu-twelve-native-module-build-20261003.json).
It records every build phase as `NOT_RUN`. Host source review and host tests
are not module compilation, module loading, firmware response, BOOTUP, or
device/runtime acceptance. BOOTUP is not authorized.
