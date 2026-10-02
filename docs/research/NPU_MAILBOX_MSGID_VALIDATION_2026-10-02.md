# NPU mailbox message-ID validation

This source-only change hardens the pinned NPU request-ID pool against
firmware-visible mailbox message IDs outside its 64-entry range and prevents a
claim made by the wrong request path from releasing the mapped request. It is
limited to `drivers/vision/npu/core/npu-util-msgidgen.c`; it does not change the
pool layout, allocation policy, normal successful claims, or mailbox retry and
dequeue policy.

## Pinned source path and defect

The source base is
`4e5c5ad7d950e4de0688b5663965f2075654b2ad`; the clean derived fixture is
`3fca50941422439b2019db2e4a3dc1016b2138a1`. The edited validator source is
byte-identical at those two revisions before this patch.

In the hardware interface, `nw_rslt_manager()` and `fr_rslt_manager()` first
peek an incoming firmware mailbox record, pass its `msg.mid` to the registered
`get_msgid_type()` callback, and return `FALSE` for a nonmatching type before
calling `mbx_ipc_get_msg()`. The protocol-driver callback calls
`msgid_get_pt_type()`. Thus a negative or out-of-range firmware message ID
reaches the pool validator before the mailbox is dequeued. The host regression
executes these extracted result-manager, callback, and pool functions together
and verifies that a rejected ID remains unconsumed and does not reclaim an
unrelated occupied request.

The pinned S5E9925 defconfig selects mailbox version 9 and command version 10;
the selected production `struct message.mid` is `u32`, while the registered
type callback accepts `int`. The extracted fixture preserves that unsigned
mailbox field and tests high-bit values that convert to negative callback
inputs on the host compiler, in addition to ordinary values 64 and `INT_MAX`.

After a result is accepted by that type gate, the actual NW and frame mailbox
adapters call `msgid_claim_get_ref()` with `PROTO_DRV_REQ_TYPE_NW` or
`PROTO_DRV_REQ_TYPE_FRAME`, respectively. They mutate the associated request
only when the returned target is non-NULL. The pinned helper previously warned
on a type mismatch but still read the reference, cleared `occupied`, and
returned the reference. The change keeps the existing warning and returns
`NULL` before reading the request reference or changing occupancy. That leaves
legitimate ownership intact for its matching path instead of reclaiming it on
a mismatched claim.

## Behavior

The validation helper still treats a NULL pool or wrong pool magic as an
internal invariant violation (`BUG_ON`). A firmware-supplied ID below zero or
at least `NPU_MAX_MSG_ID_CNT` now emits a warning and returns an invalid result
before any pool indexing. For nonnegative out-of-range IDs it also retains the
pinned `fw_will_note(FW_LOGSIZE)` diagnostic; negative IDs did not use that
report path in the pinned code. Consequently,
`msgid_claim()` is a no-op for that invalid ID, `msgid_claim_get_ref()` returns
`NULL` without clearing a slot, and `msgid_get_pt_type()` returns its existing
unknown-type sentinel `-1`. The hardware result managers then reject the
unknown type before consuming the mailbox record. The patch does not reset,
retry, free, or otherwise guess at request ownership.

The type mismatch path retains `WARN_ON()` for diagnostics. The ownership
fail-closed behavior is tested; this is not a claim that every malformed result
is panic-free under all kernel configurations. In particular,
`panic_on_warn` can make the retained warning fatal. A mismatched type also
does not establish why the mismatch occurred: it can indicate an ordering,
publication, or reuse race, and is not evidence that firmware selected a
particular request type maliciously.

## Host evidence

`tools/hardware/test-npu-mailbox-msgid-validation.py` uses the existing
bounded NPU pinned-source loader, verifies the exact source hashes, applies the
new patch with ordinary `git apply --check` and `git apply`, then compiles the
actual extracted production C at `-O0` and `-O2`. The C fixture includes the
allocator, validator, `msgid_get_pt_type()` wrapper, both firmware result type
gates, and both NW/frame mailbox adapters. Small host shims stand in for kernel
atomics, mailbox operations, log macros, and `BUG_ON`; the baseline `BUG_ON`
path is caught with `setjmp`/`longjmp`, not by executing a kernel panic.
The wrong-type adapter cases use caller-shaped objects with deliberately
inconsistent pool type metadata. This avoids an incompatible host-C pointer
dereference while exercising the real callers' null gate and ownership
behavior.

Coverage includes invalid IDs `-1`, `INT_MIN`, `64`, and `INT_MAX` through the
claim, ref-claim, and type-lookup paths; adjacent/magic/occupied-slot guards;
all valid IDs `0..63`; exhaustion and its `-1` return; duplicate and
unoccupied claims; mismatched NW/frame expectations; following valid claims;
and actual firmware result paths that reject invalid/wrong-type IDs before
mailbox dequeue. It also checks that valid NW/frame result claims continue to
update and release their correctly typed requests.

The pool still publishes occupancy before its type/reference fields, and this
patch does not solve broader publication, concurrency, or ABA/reuse races. The
host atomics are sequential and do not model Linux atomic memory ordering,
interrupt concurrency, mailbox hardware, firmware, or NPU execution. No phone,
ADB/SSH, kernel build, device run, or BOOTUP was performed. These tests provide
source-level behavior evidence only, not firmware or hardware acceptance.

## Run receipt

The final matrix passed for both the bounded public pinned source fixture and
the explicit clean local derived fixture
`/home/corpunum/s22-workers/camera-kernel-build-20260927@3fca50941422439b2019db2e4a3dc1016b2138a1`:
normal Python, `python3 -O`, and `PYTHONOPTIMIZE=1`, each compiling and running
both pre-fix and patched actual-C harnesses at C `-O0` and `-O2`. The public
fixture came from the existing bounded SHA-verified loader; it was available
for this run. No unavailable fetch was counted as a pass.
