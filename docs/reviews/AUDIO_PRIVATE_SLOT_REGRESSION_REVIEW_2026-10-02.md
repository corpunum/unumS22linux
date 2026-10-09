# Independent AUDIO5 private-slot regression review — 2026-10-02

## Scope and disposition

Reviewed author commit `7c41c13caca897bb5336e89638d1f2d907c02343` as
integrated at `3da3d065e80d5af7cee9987331f7762373183d36` in the isolated
`audio-abi-tests-review-20261002` worktree. The author and integrated versions
of the three AUDIO5 files are identical. The immutable patch under test remains
SHA-256
`2c85c2603a31f8a5535a21a2efdd60f6e3750174b6c706828f777a7bb109e1d6`.

Disposition: the correction closes the specific test-coverage gap from the
prior source review. The generic sequence-zero case now exercises the actual
extracted queue get and physical ring-slot reuse; it is not reported as a
four-patch baseline failure. I found one diagnostic-string mismatch noted
below, not a false-pass in the test conditions.

## Test behavior and mutation evidence

`test_actual_queue_get_zero_and_physical_slot_reuse()` first queues and dequeues
a traced PCM item in physical slot zero, checking the returned message/sequence
and directly checking that the private slot is cleared. It then disables both
tracepoints, advances generic IPC through the actual producer and consumer until
both ring indices wrap to zero, and checks the actual `abox_ipc_queue_get()`
sequence is zero for each generic entry. A zero-sequence PCM STOP reuses slot
zero while traces remain off. The test checks the private slot before dequeue,
then enables send tracing before the actual dequeue/process path and verifies
sequence zero, unchanged send-event count, and the PCM channel. Thus the test
does not rely on generic IPC appearing in the filtered PCM send trace.

The four-patch baseline passes this zero-sequence/physical-reuse scenario. At
both C optimization levels, its only expected failures remain the private
`struct abox_ipc` ABI-size change and the two-worker message/trace scratch
mismatch. Separately, the patched-source mutation omitting the zero overwrite
fails its slot and false-correlation checks; the mutation omitting dequeue clear
fails the direct clear check. The harness requires the exact dedicated failure
lines and failure count for each mutation. The zero-overwrite mutation seeds a
nonzero sentinel after the physical wrap, solely to make the omitted store
observable after the preceding clear; this is test instrumentation, not an
observed stale-slot runtime defect.

The mutation report text `actual get zero-overwrites the reused slot before
send tracing is enabled` is imprecise: the scenario sets send tracing enabled
before calling the actual get. The condition still checks for returned zero
and no resulting send event; this is a diagnostic label issue, not a weakened
assertion.

## Independent verification

The bounded script passed in all six Python/source-fixture combinations below.
Each invocation compiled and ran the composed baseline, patched harness, and
both isolated mutations at C `-O0` and `-O2`.

| Fixture | Python invocation | Effective optimization | Result |
| --- | --- | ---: | --- |
| Verified local derived tree `3fca50941422439b2019db2e4a3dc1016b2138a1` | normal | 0 | pass |
| Verified local derived tree | `python3 -O` | 1 | pass |
| Verified local derived tree | `PYTHONOPTIMIZE=1` | 1 | pass |
| Public pinned fixture `4e5c5ad7d950e4de0688b5663965f2075654b2ad` | normal | 0 | pass |
| Public pinned fixture | `python3 -O` | 1 | pass |
| Public pinned fixture | `PYTHONOPTIMIZE=1` | 1 | pass |

The derived tree was at its required commit and its four fixture files matched
the loader's pinned hashes. Public fixture fetches also passed its capped,
hash-checked loader. All temporary C builds were host-only. No kernel/module
build, candidate artifact execution, phone, SSH, ADB, firmware, or physical
audio action was performed.

I also checked the small Bluetooth note qualification at `216ab41`: it
accurately distinguishes the required `-I -S` flags from recommended but
unenforced `-B`, says help exits before the operational gate without preflight
or compilation, and preserves the boundary that startup hooks can run before
an in-program refusal when a caller ignores the documented flags. It does not
retroactively attest the historical helper or interpreter.

## Evidence boundary

This is extracted production C compiled against a pthread/kernel-API shim, not
a Linux-kernel execution or proof of kernel RCU, weak-memory, or workqueue
scheduling behavior. It does not establish exported symbol CRCs, module
compatibility, firmware acceptance, or physical audio. The corrected regression
test supports the narrow zero-sequence overwrite/clear and physical-slot reuse
claims only.
