# ABOX PCM-trigger trace metadata without exported-layout changes

## Result

The fifth, source-only patch moves the observation sequence out of
`struct abox_ipc`, which is embedded in the ABI-visible `struct abox_data`.
The sidecar is preallocated once per ABOX owner during probe and holds one
sequence value per existing queue slot. It stores owner/list pointers for its
private registry, but no message payload, DMA address, firmware pointer, or
timing data; those addresses are not emitted in trace events. The pinned
`struct abox_ipc` and `struct abox_data` declarations are restored byte-for-byte
to the pinned source declarations; this is source-layout evidence, not a claim
that native export CRCs have been verified.

The worker's `struct abox_ipc` scratch is invocation-local rather than static.
This prevents two ABOX workqueues from overwriting one another's dequeued
message while sending. Queue insertion/dequeue order, queue retry behavior,
duplicate handling, scheduling, and IPC payload bytes are otherwise unchanged.

## Sidecar ownership and degraded diagnostics

The module-private owner registry is keyed by the exact `struct abox_data *`,
so different owners have separate slot arrays. The sidecar stores the owner
pointer and a linked-list pointer for its private registry, but neither address
is emitted in a trace event; it stores no message payload, DMA address, firmware
pointer, or timing data. Queue put/get look up an owner
under RCU and retain that read-side lifetime through the existing
`ipc_queue_lock` critical section; all sidecar slot writes, reads, and clears
are under that queue lock. Every successful put overwrites its slot, including
generic IPC with sequence zero, and dequeue copies then clears the slot. A
retired owner is unlinked under the registry spinlock and followed by
`synchronize_rcu()` outside that lock. The devm action is idempotent, so normal
remove can retire the owner after the existing IPC workqueue and component
stop, while devres later releases the sidecar allocation.

Sidecar allocation or devm-action registration failure is explicitly a
degraded-diagnostics condition, not a functional probe gate. Probe emits a
rate-limited warning and continues. For a valid-size queue request with no
registered owner, the queue path zeros the candidate sequence before publishing
a successful queue trace event; dequeued work consequently cannot emit a send
event that appears correlated. The existing invalid-size check precedes owner
lookup, so its failed-attempt trace can still carry a sequence and its negative
result; it is not a successful queue/send correlation. Functional queueing and
sending are not rejected or retried because tracing storage is unavailable.
The test injects both allocation and action-registration failure and verifies
successful enqueue/send with no queue/send correlation markers.

Unregistering while an entry is pending makes its later dequeue sequence zero;
the message still follows the existing worker send path. Re-registration at
the same `abox_data` address creates a fresh zeroed sidecar, so a delayed lookup
cannot inherit a retired owner's slot IDs. This change only fences sidecar
metadata. It does not claim to repair pre-existing early-probe workqueue or
global/subdriver reference teardown gaps; in particular, a later worker still
depends on the pre-existing lifetime of `abox_data` and its device.

## Executable source evidence

`tools/hardware/test-audio-ipc-trace-private-abi.py` ordinarily applies the
patches in this exact order:

1. `audio-ipc-observation-fix.patch`
2. `audio-ipc-error-path-2026-10-02.patch`
3. `audio-ipc-worker-pm.patch`
4. `audio-ipc-duplicate-rekick.patch`
5. `audio-ipc-trace-private-abi.patch`

It extracts the actual pinned queue, scheduler, FE/BE helpers, worker, and new
registry functions into a pthread host harness. The composed pre-fix baseline
reproduces the ABI-size change and a forced two-worker cross-owner message/trace
mismatch. The prior generic-zero test was insufficient because generic IPC is
filtered from the PCM-trigger send tracepoint and that scenario did not reuse
the same physical ring slot. The corrected C test directly asserts the actual
`abox_ipc_queue_get()` sequence for generic IPC, wraps the real producer and
consumer back to slot zero, then queues a zero-sequence PCM message with send
tracing enabled before dequeue. It also checks that dequeue clears the private
slot. The composed four-patch baseline passes this zero-sequence/reuse case; it
is not claimed as a baseline failure. Separate patched-source mutations that
skip the zero overwrite or dequeue clear must fail their dedicated assertion.
The zero-store mutation case seeds a nonzero test-only stale-slot sentinel after
the physical wrap; this isolates the overwrite guarantee even though the
normal clear path has already emptied the slot. The baseline comparison is not
mutated and passes the actual zero-sequence reuse scenario.
The patched C tests also cover queue-to-send correlation, allocation/action
failures, pending-queue retirement, same-address owner reuse, RCU-reader
retirement wait, and two different owners whose workers are deliberately
overlapped at send. Both C `-O0` and `-O2` are run for normal and mutation tests.

The source fixture is the capped pinned set at
`4e5c5ad7d950e4de0688b5663965f2075654b2ad`, or a verified local derived tree at
`3fca50941422439b2019db2e4a3dc1016b2138a1`. The local and public-fixture modes
are run under normal Python, `python -O`, and `PYTHONOPTIMIZE=1`.

The pthread RCU shim checks the intended ownership and wait ordering; it is not
kernel-RCU or weak-memory-model proof. The extracted C tests do not establish
Linux workqueue scheduling, runtime PM, firmware acceptance, DMA progress,
physical audio, or module ABI CRCs. A fresh native module compile and export
CRC/import check is still required before making an ABI compatibility claim.
No phone, SSH, ADB, firmware, device trace, or kernel build was used for this
host-only change.
