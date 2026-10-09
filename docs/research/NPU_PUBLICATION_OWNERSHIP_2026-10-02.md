# NPU ambiguous POWER_CTL publication ownership — 2026-10-02

## Result

Added a narrow ninth patch for an ambiguous-success case in the pinned NPU
mailbox path. In the composed BOOT_IOCTL power-wait route, the NPU hardware
callback writes a POWER_CTL record and advances the mailbox write pointer
before it waits for the interrupt-status bit. If that wait returns
`-EWOULDBLOCK`, the firmware may still consume the already-published record.
The old adapter reclaimed its message ID and left the request retryable,
allowing the next request to reuse the ID and a late response to be attributed
to the wrong protocol entry.

The new branch retains the ID and returns a positive “still in flight” result
only when all three identities match: the result is exactly
`-EWOULDBLOCK`, the request is `NPU_NW_CMD_POWER_CTL` with the composed
`npu_session_save_power_result` waiter callback, and the registered
`nw_post_request` callback is exactly `nw_req_manager`. The existing worker
then moves the request to `PROCESSING` and does not republish it. Other
callback errors and pre-commit failures keep their previous reclaim/retry
behavior. The patch also adds the callback declaration under
`CONFIG_NPU_USE_BOOT_IOCTL`; it changes no BOOTUP refusal or shutdown code.

This is an ownership decision, not proof that firmware accepted the command.
When the response never arrives, its fixed-pool ID and referenced request
entry remain retained. At the pinned capacity of 64 IDs, new posts are refused
until an ID is returned. The patch does not release an entry while its
message ID remains owned.

## Exact callback provenance

The base camera source has `npu_session_save_result`; that is not the callback
used by the reviewed power-wait patches. The test first applies the actual
eight-patch order, then verifies that the composed POWER_CTL request assigns
`npu_session_save_power_result`, the callback ignores an already-canceled
waiter, and the hardware ops table has one `.nw_post_request = nw_req_manager`
registration and one NW post callsite. It also checks the protocol's waiter
identity at both request admission and publication. This distinction avoids
classifying an errno from an unrelated base-source callback as evidence that
the power-wait ring was committed.

In the pinned hardware path, `nw_req_manager` constructs `COMMAND_POWER_CTL`
and calls `npu_set_cmd`. `npu_set_cmd` calls `mbx_ipc_put` before
`__send_interrupt`. The actual producer's pre-commit errors are `-EPARAM`,
`-EALIGN`, `-EINVAL`, and `-ERESOURCE`; it contains no `-EWOULDBLOCK`. It
copies message and command bytes before publishing `ctrl->wptr`. The
POWER_CTL interrupt-wait branch can return `-EWOULDBLOCK` only after its
finite decrementing poll and `dbg_dump_mbox()`. The exact callback pointer,
command kind, and composed waiter function guards are therefore all required;
errno spelling by itself is insufficient (`EWOULDBLOCK` aliases `EAGAIN` on
the host).

The target defconfig selects `CONFIG_NPU_USE_BOOT_IOCTL=y`, mailbox v9, and
command ABI v10. The pinned clear-bit poll limit is 10,000 iterations. That is
a source-level poll bound, not a real-time duration or end-to-end callback
bound. The eighth patch bounds the diagnostic walk used by the dump; it does
not prove that the complete synchronous callback always returns.

## Lifetime and liveness limits

The source and extracted-C checks cover the single AST worker's serialized
order: it checks `PROCESSING` replies before handling `REQUESTED` work. A
response made available at the write-pointer commit is therefore picked up on
the next serialized pass after the successful ownership transition. The
actual response adapter releases the message ID before the protocol handler
checks the entry state. For a timed-out request, the exact completion
STUCKED/FREE decision block retains the entry in `STUCKED`; a later reply
releases only the ID and does not move that entry back to a reusable list.
The test also executes the composed result callback against a canceled waiter
while the request ID remains retained and verifies that it does not complete
the canceled waiter.

The source publisher, `npu_set_cmd`, and `__send_interrupt` do not acquire
`interface.lock`. The test establishes serialization only for the actual
single AST processing/requested sequence and uses bounded host shims for
mailbox bytes, MMIO status, the LSM list, waiter state, and scheduler-facing
hooks. It does not model IRQ scheduling, real kernel locks/barriers, slab
deallocation, device references, or concurrent hardware behavior. In
particular, it does not simulate close or claim that an entry is safe across
close/reopen.

The known drain-liveness defect remains unchanged:
`npu_power_wait_cancel_and_drain()` waits without a timeout for
`waiter->publish_done` while synchronous publication is outstanding. Freeing
the waiter after an invented timeout would be unsafe while the publisher can
still reference it. `proto_drv_close()` joins the AST before destroying the
NW LSM, but this source audit finds no msgid invalidation at close; reopen
reinitializes the pool and resets mailbox pointers. No firmware/IRQ
quiescence fence across close/reopen was established. These checks do not
resolve the unbounded drain or prove whole-callback, close, or hardware
liveness.

## Regression and evidence boundary

`tools/hardware/test-npu-publication-ownership.py` loads SHA-verified pinned
source through the existing capped fixture helpers, supports their explicit
local-source route, applies all eight reviewed patches in order, and verifies
that the six-patch composition byte-matches the clean source tree at
`e5af0ba1cefc959094d03e1a136b8e33ff938b2a`. It then applies this candidate
patch in an ephemeral fixture and checks that only
`npu-if-protodrv-mbox2.c` changes. The C harness template is
`tools/hardware/npu-publication-ownership-harness.c`; it injects the pinned
producer, allocator, mailbox adapter, request/response handlers, interrupt
publisher, POWER_CTL manager, canceled-waiter callback, and STUCKED transition
code rather than maintaining a second behavioral model.

The baseline reproduces late-response ID misattribution, loss of a response
available at commit, timeout without retained ownership, and another mailbox
commit after the single recycled ID is reused. The patched executable checks
late replies both after the `PROCESSING` transition and when the response is
available before that transition, no republish on a repeated worker entry,
pre-commit `-ERESOURCE` retry, non-hardware callback `-EWOULDBLOCK`, canceled
waiter handling, STUCKED entry retention, and the 64-ID exhaustion boundary.
Both baseline and patched source C compile with `-Wall -Wextra -Werror` and
run at `-O0` and `-O2`; the Python checks are also run normally, with `-O`, and
with `PYTHONOPTIMIZE=1`.

Evidence is limited to exact extracted target C plus deterministic host
shims. No full kernel build, phone/SSH/ADB operation, BOOTUP, firmware
acceptance, or device test was performed. BOOTUP remains refused; no result
here changes that authority boundary.

Pinned base: `4e5c5ad7d950e4de0688b5663965f2075654b2ad`.
Explicit local derived source: clean
`3fca50941422439b2019db2e4a3dc1016b2138a1`.

Candidate patch SHA-256: `2e2e2de8a28c5b408bfa0661535c358070130340318f1d0eaa0efe38edc11078`.
