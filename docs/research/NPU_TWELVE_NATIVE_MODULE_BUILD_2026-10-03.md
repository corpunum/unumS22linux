# NPU 12 native module build — 2026-10-03

## State

The single reviewed host-side target `drivers/vision/npu.ko` completed with
exit 0 and without a resource abort. The generated release, byte-identical
configuration, module artifact, and static import/export compatibility were
verified and recorded in the sanitized receipt. This is not evidence of module
loading, firmware response, BOOTUP, device/runtime acceptance, or deployment.
No image or package was built, no phone/SSH/ADB was used, and no module was
loaded. BOOTUP remains unauthorized.

The initially frozen wrapper at `98b1d0a` (wrapper SHA-256
`c4a25c93696217c1ae905df51bb57ece4ca652291b6f001a47c2ab23e8aca842`) was
not executed. Review found that it verified but failed to return the toolchain
mapping consumed by the build-phase receipt, and checked for generated
`kernel.release` immediately after `olddefconfig`. This follow-up preserves
that original commit, fixes both orchestration defects, and adds a
hardware-free test of the real `collect_plan()` and `execute_build()` paths.
It does not alter the pinned kernel source, config, toolchain, or build target.

The pinned kernel Makefile shows the direct dependency chain
`drivers/vision/npu.ko → single_modpost → modules_prepare → prepare → archprepare
→ include/config/kernel.release`; `olddefconfig` is a separate
configuration-only target. Absence of the generated release file after
`olddefconfig` is therefore expected. The wrapper now checks for a non-symlink
file with the exact pinned release only after the monitored NPU target exits
zero without a resource abort. This verifies generated output without
weakening release identity checks.

The new output directory, filtered Symvers output, and raw `olddefconfig` log
were created by the single authorized run and remain private build artifacts.
The wrapper at
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
HEAD was committed and clean before and after the single target compile.
Independent source review is reported cleared by the coordinator (review
identifier `74059b4d`); a separate reviewed host GO was recorded before the
execution. Neither review status nor compilation is runtime evidence.

## Configuration and symbol inputs

The preserved configuration export is
`/home/corpunum/s22-linux/builds/native-config-export-20261002.config`, 236,183
bytes, SHA-256
`d762d5fc71e369013ee36657d007063ce1f0faca9707d5f9ccfba2597b7fcd16`. The
existing native-ten output `.config` has the same hash. Required pinned values
include `CONFIG_EXYNOS_NPU=m`, `CONFIG_NPU_USE_BOOT_IOCTL=y`,
`CONFIG_MODVERSIONS=y`, `CONFIG_CFI_CLANG=y`, `CONFIG_LTO_NONE=y`,
`CONFIG_SHADOW_CALL_STACK=y`, `CONFIG_LOCALVERSION="-g4e5c5ad7d950"`, and
`CONFIG_LOCALVERSION_AUTO=n`. The expected generated release is
`5.10.260-g4e5c5ad7d950`. The new output `.config` matched the pinned export
hash both before and after `olddefconfig`. Its raw log was 869 bytes (SHA-256
`88a885d080fd145f312d6dd20b008e8341fe832670b5aa1e908f1e58ceccddc7`). The
generated `include/config/kernel.release` was checked after successful module
preparation/build and matched `5.10.260-g4e5c5ad7d950` (file SHA-256
`42baea336b74cd6cdbd1d3dbd3c9709c3fff772b21ba109458b388b72d00d961`).

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
That output was produced and verified: 17,282 rows, 1,108,457 bytes, SHA-256
`add620bc3a3732654f23161e4c966c7076b020568683f527402c6253cb59a334`. The prior native-ten filtered file is only a
read-only cross-check (hash
`add620bc3a3732654f23161e4c966c7076b020568683f527402c6253cb59a334`); it will
not be substituted for re-deriving the twelve build's pinned input.

## Pinned module-only wrapper and host execution

The corrected wrapper SHA-256 is
`8dab1a2c0ea2b01555645717b46c0f1506ff951a60a9256669d90bae6859d61d`. Its
plan-only path verifies the exact source HEAD/tree/parent and clean state,
config, both patch digests, the 13 native LLVM/GNU tools, Python/make/env/nice,
both helper hashes and versions, fresh output paths, and current operation
resource gate. The returned plan now carries the verified toolchain mapping
used in the build-phase receipt. It does not create output files. It was
read-only exercised in normal Python (optimization level 0), `-O` (level 1),
and `PYTHONOPTIMIZE=1` (level 1); all reported the same source, helper, and
17 tool identities. Execution was separately authorized and used the same
preflighted identity and operation gate.
With the corrected wrapper SHA but no coordinator token, `--execute` returned
2 with `--execute requires the exact coordinator GO token`; both reserved
output paths remained absent.

`tools/hardware/test-build-npu-twelve-module-only.py` imports and executes the
actual `collect_plan()` and `execute_build()` implementations against
temporary paths. It controls `subprocess.Popen` and the pinned monitor seam;
any unexpected subprocess command fails the test, so no real `make` or Kbuild
can start. The suite passes in normal, `-O`, and effective `PYTHONOPTIMIZE=1`
modes. It covers toolchain propagation, the successful single-target path,
config and `olddefconfig` failures, initial and pre-build resource gates,
resource-abort with zero exit, `SystemExit(0)`, existing-output refusal, and
missing/wrong post-target `kernel.release`. In particular the fake
`olddefconfig` verifies that `kernel.release` is still absent and the fake
module-prepare boundary creates it only after the exact module target has been
handed to the monitor. These are orchestration tests, not Kbuild or module
compile evidence.

```sh
# Read-only identity, command, and resource plan; no files are created.
/usr/bin/env -i PATH=/usr/bin:/bin LC_ALL=C /usr/bin/python3 -I -B \
  /home/corpunum/s22-workers/npu-twelve-native-build-20261003/tools/hardware/build-npu-twelve-module-only.py \
  --plan-only

# The separately authorized execute invocation was run exactly once. Its full
# argv, token, and exit receipt are recorded in the JSON evidence; do not replay.
```

Before writing, the wrapper rechecked all identities, paths, and the 12/24 GiB
operation gate. It called the guarded filter helper in an exclusive fresh
output transaction, verified the expected 17,282-row filtered hash, then
copied the preserved config exclusively and invoked only
`olddefconfig`. Its stdout/stderr was retained verbatim at
`.../olddefconfig.log`; the before/after config SHA matched. It did not expect
`olddefconfig` to create `kernel.release`. It also recorded filter output and
each phase in a private append-only `module-only-phase.jsonl` journal. The
execution did not retry or clean partial outputs.

The only compilation argv is
`/usr/bin/nice -n 10 /usr/bin/env -i PATH=/usr/lib/llvm-18/bin:/usr/bin:/bin
LC_ALL=C LOCALVERSION= TMPDIR=/tmp /usr/bin/make -C
<pinned-source> O=<fresh-output> ARCH=arm64 LLVM=1
CROSS_COMPILE=aarch64-linux-gnu-
input-symdump=<pinned-filtered-symvers> -j1 V=1 drivers/vision/npu.ko`.
It was passed to the already-pinned
`build-npu-six-profile.py:run_monitored_build` implementation (helper SHA-256
`56f39759e4a098562cbafd634a659b007be2a540a4ada6234962711be026e5d5`), with
source set to the exact committed kernel tree, output set to the fresh module
directory, and child environment exactly `PATH`, `LC_ALL=C`, empty
`LOCALVERSION`, and `TMPDIR=/tmp`. It recorded the raw module build log,
resource samples, and phase receipt under that private output. The wrapper
fails closed if `resource_abort` is true even when the helper reports a zero
child exit, and converts any helper `SystemExit(0)` into nonzero failure. Both
olddefconfig and monitor-run build process groups use the pinned bounded
SIGINT/SIGTERM/SIGKILL cleanup helper; its confirmation is journaled. Success
was accepted only for a zero make exit without resource abort, the post-target
exact `kernel.release`, a stable config and source identity, exactly one
`*.ko` at `drivers/vision/npu.ko`, and no `Image`.

The actual start gate was recorded at 22:53:51Z with 25,870,323,712 bytes
available memory and 30,746,574,848 bytes free on the host `builds` filesystem.
The monitor's six samples reached minima of 24,155,299,840 bytes available
memory and 30,556,766,208 bytes free disk, above its unchanged 8 GiB / 16 GiB
abort thresholds. The target ran once from 22:53:57Z to 22:54:57Z (60 seconds);
the wrapper exited 0 and recorded `resource_abort=false`.

The only module artifact is
`builds/npu-native-twelve-module-only-out-20261003/drivers/vision/npu.ko`:
14,796,512 bytes, SHA-256
`03e1ad6403265d39e1d6c48d31c1defad3aad984cd972f8fe80f8ee0c4eaa621`, AArch64
ELF64, GNU Build ID `59da71800d33e0c7dac1936908ffab47ec99584c`, and native
vermagic `5.10.260-g4e5c5ad7d950 SMP preempt mod_unload modversions aarch64`.
There is exactly one `.ko` and no `Image`. Its 234 ordered import-version
records hash to `99aef867af8439d5c5cf1954ff01cd3f70eff870c5e3dbc19c733b386f0aaf55`,
identical to the native-ten NPU module. The pinned host validator found zero
missing, mismatched, unknown, or ambiguous import CRCs against the native-eight
baseline Symvers; both module and baseline record `module_layout` CRC
`0x0e3c515c`. The one exported symbol is `vision_register_device`, with CRC
`0x982f6fee`, matching the baseline. These are static host-side compatibility
checks, not execution of the kernel module loader.

Raw build, filter, resource, and phase logs remain only in the private build
output. They were not copied into this repository; the sanitized receipt
records their sizes, hashes, paths, and verifier/tool identities.

After the build, a separate source audit identified an unfixed path in
`drivers/vision/npu/core/interface/hardware/npu-interface.c`: a
`report_workqueue` allocation failure in `npu_interface_open()` may return
success after IRQ requests without releasing those IRQs. Workqueue/IRQ/report
callback ownership and teardown need a separately reviewed repair and fresh
build. This unresolved defect blocks deployment/runtime acceptance; it was not
changed in the committed source or built artifact.

## Resource and evidence boundary

At the actual operation start (`2026-10-02T22:53:51Z`), the wrapper observed
25,870,323,712 bytes available memory and 30,746,574,848 bytes free at
`/home/corpunum/s22-linux/builds`. Both operation thresholds (12 GiB / 24 GiB)
passed then. During the target, six monitor samples had minima of
24,155,299,840 bytes available memory and 30,556,766,208 bytes free disk,
above the pinned monitor's 8 GiB / 16 GiB abort limits. These are point-in-time
host readings, not reservations or guarantees against concurrent activity.

An earlier host sample at `22:01:44Z` saw the builds filesystem below the
*full Image-profile* 32 GiB initial-disk gate but above this module-only 24 GiB
gate. The coordinator's `/srv/s22` collector/PID1 namespace is a distinct
destination; its readings are not comparable to this host's
`/home/corpunum/s22-linux/builds` filesystem. This worker did not access the
phone or use one namespace's reading to supersede the other.

The sanitized machine-readable receipt is
[`s22-npu-twelve-native-module-build-20261003.json`](../../evidence/s22-npu-twelve-native-module-build-20261003.json).
It separates completed host compile/static compatibility evidence from
unperformed module loading, firmware response, BOOTUP, and device/runtime
acceptance. BOOTUP is not authorized.
