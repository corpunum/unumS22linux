# Independent NPU12 module-build prelaunch review — 2026-10-03

## Verdict

**BLOCKED before Kbuild.** The frozen wrapper has two deterministic defects
that prevent a successful run, and the frozen change contains no focused
tests of its actual execution orchestration. Do not issue the one-module
build GO against this wrapper. No `olddefconfig`, module compile, full kernel
build, package, phone access, or device action was performed in this review.

This is a host-build prelaunch review only. It does not clear module ABI,
current-phone compatibility, module loading, firmware, NPU BOOTUP, runtime, or
hardware acceptance.

## Reviewed identities and inputs

- Frozen wrapper author commit:
  `98b1d0a29060098ecfb06890dabb7c34431008cd` (parent
  `7753fd357d6d401822dce173506980ed5d69bad7`), tree
  `be3970e0122f32fee3c8bf820a0cc11420189d68`.
- Wrapper SHA-256:
  `c4a25c93696217c1ae905df51bb57ece4ca652291b6f001a47c2ab23e8aca842`.
- Independent review worktree is based on `c247d675d0a294103042419d0d350ef67856299d`;
  the exact author preparation and wrapper commits were cherry-picked there.
- Author preparation note SHA-256:
  `960cf91897864c041686b32a6532cb9c1862d40e3f4004313869c7a3c5225012`.
- Author machine-readable plan SHA-256:
  `8ccd4c2784869e73ec8b86a933ca95bef57df41884bb51388a4634ac8affb48e`.
- Pinned monitor-helper SHA-256:
  `56f39759e4a098562cbafd634a659b007be2a540a4ada6234962711be026e5d5`.
- Pinned Symvers-filter helper SHA-256:
  `2404fab4a2ed469eab1930a7275c28fd3f5a0bbb1b23c88369338ca23cd26c67`.

The source worktree is clean at `e9c3016233a72ceccb13e537f0b7ef72426582b9`,
tree `f917408e1c3388c87a8ed8f0220e4f9c3c9c6a4e`, directly parented by
`709ac38b573d092e300f3787d0d5ba97dd333c0a`. The committed delta contains
exactly `drivers/vision/npu/core/npu-log.c` and
`drivers/vision/npu/core/npu-util-msgidgen.c`. The wrapper verifies that exact
HEAD/tree/parent, clean state, and two-path delta before it can produce a
plan.

The pinned exported config is 236,183 bytes with SHA-256
`d762d5fc71e369013ee36657d007063ce1f0faca9707d5f9ccfba2597b7fcd16`. It
contains `CONFIG_LTO_NONE=y` (not ThinLTO), CFI, SCS, MODVERSIONS, the NPU
module and required NPU options, and `CONFIG_EXYNOS_NPU_DRAM_FW_LOG_BUF=y`.
The wrapper also pins this entire config by hash; its explicit required-line
list does not name the DRAM log option, but the exact full-file hash covers
that value.

The full native-eight `Module.symvers` input is pinned to SHA-256
`15fc69e815cb4da6cb3372f4b5141005ab2e770b0414b67f230f1b185bf03df7` and
17,283 rows. The filter is constrained to remove the single pinned old
`drivers/vision/npu` row and check `module_layout` CRC `0x0e3c515c`; its
expected 17,282-row output hash is
`add620bc3a3732654f23161e4c966c7076b020568683f527402c6253cb59a334`. The
filtered output was absent during this review; it was not generated.

The wrapper verifies 13 pinned LLVM/GNU compiler tools plus pinned
`python3`, `make`, `nice`, and `env` (17 executable byte identities in total),
and checks the exact Clang 18.1.3 and LLD 18.1.3 first version lines. Each
plan-mode invocation succeeded through these checks. The plan receipt
currently drops the returned toolchain summary, however, which is related to
the execution blocker below. Source Git is invoked by fixed path
`/usr/bin/git` but is not itself content-hashed; source identity is checked
against fixed commit/tree/delta pins.

## Reproduced read-only checks

The frozen wrapper's `--plan-only` path returned 0 in normal, optimized, and
effective environment-optimized Python modes. The environment-optimized
invocation was checked with `sys.flags.optimize == 1` and did not use `-I`,
which would ignore `PYTHONOPTIMIZE`. All three plans reported the pinned
wrapper/source, clean source, and absent output paths; both the output
directory and filtered-Symvers path remained absent.

The latest plan sample was captured at `2026-10-02T22:34:24Z` on
`/home/corpunum/s22-linux/builds`:

| Resource | Observed | Operation start gate | Result |
|---|---:|---:|---|
| `MemAvailable` | 27,229,917,184 B | 12,884,901,888 B (12 GiB) | pass |
| Free build-filesystem space | 31,029,723,136 B | 25,769,803,776 B (24 GiB) | pass |

An `--execute` attempt with the correct frozen wrapper SHA and an incorrect GO
token returned 2 with the expected refusal; neither reserved output appeared.
I did not try the valid token or enter `execute_build`.

The target `drivers/vision/npu.ko` matches the composite module target declared
by the pinned `drivers/vision/Makefile`; `input-symdump` is consumed by
`scripts/Makefile.modpost`. The build argv is a single `-j1 V=1` target, uses
`LLVM=1`, `ARCH=arm64`, the pinned GNU cross prefix and filtered symvers, and
does not pass `LLVM_IAS` or an explicit `LD` override. Both olddefconfig and
the module command use the explicit child environment; the latter is invoked
through pinned `nice` and `/usr/bin/env -i`. The module command targets no
`Image` or whole-module set.

The operation-specific 12-GiB/24-GiB start gate is scoped to one module target;
the full-profile helper's 32-GiB start constant is not changed. The reused
monitor remains pinned at 8-GiB available memory / 16-GiB free-space abort
limits and bounded 30/15/5-second process-group cleanup stages. This is a
reasonable narrower gate for a single module-only target, but is a point-in-
time precondition, not a storage reservation or protection from concurrent
owner activity. On failure, partial outputs are intentionally preserved and
the wrapper does not retry or delete them; exclusive output creation keeps a
later run from silently reusing them.

## Blocking defects

1. **The frozen plan omits `toolchain`, then execution indexes the missing
   field.** `collect_plan()` assigns `toolchain = verify_toolchain()` at line
   338, but does not include that value in its returned dictionary (lines
   345–374). `execute_build()` later evaluates `plan["toolchain"]` while
   building `phase_info` at line 568. If earlier gates are satisfied and the
   release check is bypassed or repaired, this raises `KeyError` after the
   filter/config/olddefconfig work and before the monitored module command is
   dispatched. The plan output also lacks the tool identities that the
   verifier computed.

2. **The fresh-output `kernel.release` check is before its Kbuild generator.**
   After `olddefconfig`, the wrapper requires
   `OUTPUT/include/config/kernel.release` at lines 530–532. In the pinned
   kernel source, `scripts/kconfig/Makefile` maps `olddefconfig` to the Kconfig
   `conf --olddefconfig` simple target; it does not depend on `prepare` or
   `archprepare`. The top-level `Makefile` defines `include/config/kernel.release`
   but makes it an `archprepare` prerequisite (lines 1351–1370), reached via
   the later `prepare` sequence. The output is explicitly fresh, so this file
   is not present merely because `.config` was copied or olddefconfig ran.
   As written, the wrapper refuses before the only module Kbuild target. No
   Kbuild command was run to reach this failure; this finding is from the
   pinned Makefile dependency path.

3. **No frozen test exercises the execution orchestration.** The two frozen
   commits add the builder and plan/evidence documents, but no focused test
   file or fixed-CI entry. Read-only plan and bad-token checks do not cover
   `execute_build()` dispatch. Before a later build review, add focused
   synthetic subprocess/filesystem tests that reach the module-dispatch path
   without Kbuild and exercise failed config/resource gates, resource-abort
   propagation, monitor `SystemExit(0)` handling, and owned process-group
   cleanup/reporting. Do not use a full kernel build as a test.

The source-and-wrapper-bound GO string is printed in plan output and is
deterministically derivable from the source and wrapper hashes. Treat it as an
explicit command-line acknowledgment/interlock, not secret authentication or
role enforcement; coordinator authorization remains external.

## Evidence boundary

The plan being launch-ready only means the read-only source, config, helper,
tool, output-absence, and point-in-time resource checks passed. These blocking
defects mean the frozen wrapper is not approved for the single host module
build. No `olddefconfig`, kernel build, module compilation, package, device or
phone operation, firmware action, or BOOTUP action occurred.
