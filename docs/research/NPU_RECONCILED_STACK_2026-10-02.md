# NPU refcount/lifecycle source profile — 2026-10-02

Status: four-patch source composition and host C regressions passed. This is a
reviewable source-only profile, not a kernel-build, deployment, BOOTUP, or NPU
acceptance result. The separate shutdown-ownership patch is excluded; no
full-stack build or device/runtime validation is claimed.

## Exact source and patch inputs

The test reuses the bounded fixture loader and pinned SHA-256 manifest from
`test-npu-candidate-stack.py` / `test-npu-probe-unwind.py`. Target source is
LineageOS `android_kernel_samsung_s5e9925` commit
`4e5c5ad7d950e4de0688b5663965f2075654b2ad`, verified against the clean
read-only derived worktree at
`3fca50941422439b2019db2e4a3dc1016b2138a1`. Per-file fixture limit is 512 KiB
and public requests are bounded to five seconds. A configured source tree must
match the exact derived source; unavailable public source returns 77, while
missing or changed inputs fail closed.

The applied order is:

| Order | Patch | SHA-256 |
| --- | --- | --- |
| 1 | `npu-session-lifecycle-fix.patch` | `1554436cb6624c542f9e04ac22a3b3545e55f94c59d3025ee6bdc1ec43168251` |
| 2 | `npu-refcount-lifecycle-profile.patch` | `8385e4210a807f96f972757cd6ca74a8077b0127ab112d8b877d012ccdc3cb7b` |
| 3 | `npu-default-boot-callback-fix.patch` | `f5ce216e34df11d8c6adee4a99c36d63f73593cf379e29de3a9de828ec2ee1e7` |
| 4 | `npu-probe-unwind-fix.patch` | `d3e2e590d4d3c956b10c724a15db996dacd07def204332f50a1c0513f56b4948` |

The historical `npu-refcount-transaction-fix.patch` remains unchanged at SHA-256
`09414c886c37e55e23767f33fcdba19f5ab8099a6df3a2d5618972449d64a935`.
Its original ordered-stack regression is preserved: lifecycle followed by
that patch still fails at `npu-vertex.c:1224`, and the reverse ordering still
fails at `npu-vertex.c:1188`. The new profile changes no historical patch.

## Reconciliation of the refcount hunks

The profile copies both canonical refcount diff sections for
`npu-hw-device.c` and `npu-hw-device.h` verbatim. It retains the complete
transactional reference implementation and device call-site changes: locked
first/final ownership, failure poisoning and propagation, overflow/underflow
checks, explicit init-abort callbacks, and parent rollback. The regression
compares the two profile diff sections directly with the canonical patch,
compares the resulting entire header, checks all 18 extracted legacy
helper/callback C bodies, and verifies the boot/init abort registrations and
both `npu_hw_ref_setup()` calls in the combined probe body. Later probe changes
are allowed in that body but must preserve those exact refcount effects.

The only canonical refcount section not included is its `npu-vertex.c` hunk.
That hunk adds an unconditional `mutex_unlock(&vertex->lock)` at the baseline
`p_err` label and moves the old label. The preceding lifecycle patch replaces
that entire `npu_hwdev_normal_bootup()` function: it tracks `lock_held` across
the secure-count wait, routes failures through reverse-order cleanup, and
exits through `out_unlock`, unlocking only when the function still owns the
mutex. Its `p_err_check` label is gone. Applying the baseline unlock repair to
that replacement would be stale and could double-unlock. The profile therefore
does not modify `npu-vertex.c`; the test byte-compares that file immediately
after lifecycle application with the final composed source.

This is a context-profile reconciliation, not authority to drop the vertex
repair in unrelated source profiles. The actual extracted final-C caller is
compiled and executed with checked-error, secure-timeout, successful warm-boot,
and reverse POWER_NOTIFY/ref/shutdown unwind cases. Each case verifies lock
acquisition/release balance, no invalid unlock, and mutex availability on
return.

## Executed host regression

The test applies all four frozen patch inputs to a temporary exact-source copy
using, for each patch, plain `git apply --check --whitespace=error-all`
followed by `git apply --whitespace=error-all`. It does not use force, fuzz,
`--3way`, or path filtering for the candidate series. Negative tests make the
actual required-input reader reject a missing profile and a SHA-tampered
profile; the order validator rejects swapped lifecycle/profile inputs before
any fixture source changes. Separately, the canonical legacy patch is applied
to a comparison-only fixture with explicit `--include` for its C/H paths; that
isolates the exact expected legacy C/H result and does not filter the candidate
series or suppress its known vertex hunk.

Run from this worktree with the clean local derived source tree:

```sh
S22_NPU_PROBE_SOURCE_TREE=/home/corpunum/s22-workers/camera-kernel-build-20260927 python3 tools/hardware/test-npu-reconciled-stack.py
S22_NPU_PROBE_SOURCE_TREE=/home/corpunum/s22-workers/camera-kernel-build-20260927 python3 -O tools/hardware/test-npu-reconciled-stack.py
S22_NPU_PROBE_SOURCE_TREE=/home/corpunum/s22-workers/camera-kernel-build-20260927 PYTHONOPTIMIZE=1 python3 tools/hardware/test-npu-reconciled-stack.py
```

All three Python modes passed. Each run compiled and executed the 11 existing
actual-refcount-C scenarios and five extracted `npu_hwdev_normal_bootup()`
caller scenarios at both C `-O0` and `-O2`. The helper scenarios cover
concurrent first-reference success/failure, abort versus generic STM teardown,
parent rollback/error propagation, underflow, poisoned final failure and
recovery stop. The caller scenarios cover low-level boot error, vertex-ref
error, failed POWER_NOTIFY with inverse/registered-HW cleanup, the bounded
secure-count timeout, and successful warm boot; all check balanced locking.
The caller harness reuses the existing test's actual-C shims and adapts only
callback prototypes/cleanup stubs to the lifecycle function's return-valued
service APIs.

After all four patches, the existing host preflight still exits 2 with
`artifact_preflight_pass=false`, `bootup_ready=false`, and
`bootup_authorized=false`. AIE firmware and DSP relocation-rule fixtures were
absent and passed as unavailable inputs, not synthesized. The historical
expected-blocker check also still reproduces the canonical unprofiled
vertex-hunk failure.

## Evidence limits and remaining work

This establishes exact fixture identity, application of the four source
patches in order, host execution of extracted C under pthread/service shims,
and preserved BOOTUP refusal. It does not establish Linux mutex/atomic/common
clock behavior, lockdep results, whole-kernel compile/link correctness, module
loading, firmware/STM/MMIO success, hardware behavior, or inference. The new
shutdown-ownership patch was neither included nor inferred safe from this
composition; its review and integration remain separate. NPU BOOTUP and
deployment remain unauthorized and unproven.
