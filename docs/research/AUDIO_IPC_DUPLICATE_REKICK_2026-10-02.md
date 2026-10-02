# ABOX duplicate-callback re-kick — 2026-10-02

## Finding and source change

The earlier queue/state linearization patch made an FE/BE trigger duplicate an idempotent local success: under `ipc_queue_lock`, it recognizes that the requested direction already matches the cached direction and returns without inserting or tracing another message. The separate runtime-PM repair correctly leaves accepted FIFO entries intact after a negative worker resume and balances the failed `pm_runtime_get_sync()` reference. Together, however, these changes exposed a liveness gap: an explicit same-direction FE `trigger` or BE `mute_stream` callback while that FIFO remained nonempty took the duplicate early-exit and did not call `queue_work()`. Since the failed PM worker did not retry itself, the retained FIFO had no callback-driven re-kick for that duplicate request.

The fourth patch adds a `duplicate_pending` output to the private queue insertion helper. While holding the existing `ipc_queue_lock`, the helper snapshots whether the queue is nonempty at the same point it recognizes a duplicate. The scheduler then makes exactly one call to its existing `queue_work()` after the helper has released the lock, and only when both `duplicate` and that snapshot are true. It still exits the duplicate path without publishing a new message, changing the cached direction, tracing an insertion, retrying a full queue, or flushing work. Empty-queue duplicates remain no-ops. Nonduplicate insertion, retry, and sync behavior and the generic IPC API are unchanged.

This is an explicit-callback re-kick, not an automatic retry policy. A worker PM failure still has no self-reschedule, reset, failsafe, or guessed ABOX recovery path. The queue lock provides a point-in-time pending snapshot; the subsequent `queue_work()` is intentionally outside the lock. A worker may drain the observed FIFO between those operations, so an extra work invocation can find an empty queue, or the workqueue can coalesce the call with work already pending/running. Neither case inserts, reorders, or replays a queue entry. This change claims one source-level `queue_work()` call per eligible duplicate callback, not a particular Linux workqueue execution count or timing.

## Patch order and verification

The standalone source patch is intended to compose after these immutable patches, in this exact order:

1. `tools/hardware/audio-ipc-observation-fix.patch`
2. `tools/hardware/audio-ipc-error-path-2026-10-02.patch` (queue/state linearization)
3. `tools/hardware/audio-ipc-worker-pm.patch` (failed runtime-resume handling)
4. `tools/hardware/audio-ipc-duplicate-rekick.patch` (this change)

`tools/hardware/test-audio-ipc-duplicate-rekick.py` starts from pinned ABOX and required PM/ASoC fixtures, checks their pinned hashes, uses ordinary `git apply --check` and `git apply` for all four patches, and extracts the production queue, worker, scheduler, specialized trigger, FE trigger, and BE mute functions into a pthread host harness. The harness instruments `queue_work()` and `flush_work()` with a mutex-trylock assertion so these calls cannot be hidden inside the queue critical section. At both C `-O0` and `-O2`, the composed pre-fix source is required to fail exactly the two pending FE/BE duplicate re-kick cases and the full-queue duplicate re-kick case; patched extracted C must pass.

Covered cases include: a failed PM resume retaining mixed FE START then BE STOP FIFO entries without an automatic retry; each same-direction FE and BE STOP explicitly scheduling the retained FIFO once without a duplicate enqueue or false insertion trace; a successful later worker draining only the retained START→STOP order; an empty duplicate causing no `queue_work()` or PM activity; a full-ring duplicate causing one re-kick without insertion/retry/delay/flush; unchanged nonduplicate full-ring retries; and unchanged generic duplicate-message, synchronous-flush, and atomic-direct-send behavior.

The fixture loader accepts only the pinned base commit, caps each public source response at 256 KiB with a 5-second timeout, and verifies SHA-256 before extracting code. The local comparison tree is the clean derived source at `3fca50941422439b2019db2e4a3dc1016b2138a1`, checked against the same pinned base files.

## Evidence boundary

This is source-level and host-shim evidence only. It verifies the selected production-C branches and queue ownership under the modeled mutex. It does not prove Linux workqueue scheduling/execution timing, runtime PM behavior on the phone, firmware acceptance or completion, physical DMA progress, `hw_ptr`, or audible output. The previously observed `hw_ptr == 0` / DMA-stalled state remains unresolved; this patch makes no hardware-acceptance claim. No IPC payload contents, DMA addresses, firmware/device pointers, phone access, amplifier/gain writes, or ABOX teardown/bind/reset operations are involved.
