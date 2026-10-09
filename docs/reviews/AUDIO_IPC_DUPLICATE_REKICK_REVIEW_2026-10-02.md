# Independent ABOX duplicate re-kick review — 2026-10-02

## Result

PASS for the scoped source change: an explicit same-direction FE/BE callback
re-kicks an already nonempty IPC queue once, without adding a message,
changing the cached direction, or creating a false queue-insertion trace. An
empty-queue duplicate remains a no-op. The host test validates these branches
against the composed four-patch source at C `-O0` and `-O2`.

This review does not establish Linux workqueue execution or audio hardware
progress. The patch calls `queue_work()` and ignores its return, consistent
with the existing scheduler. The test counts the call; it does not establish
that the kernel queued or executed another worker invocation.

## Source and control-flow review

The prepared worktree was clean at author commit
`c3f2f079472ce7ebe48f0f8ef14e9bf7220e489a`. The local pinned source tree was
clean at `3fca50941422439b2019db2e4a3dc1016b2138a1`, with base commit
`4e5c5ad7d950e4de0688b5663965f2075654b2ad`.

The patch changes only the private `abox_ipc_queue_put()` helper and its
`abox_schedule_ipc()` call. That static helper has one production call site.
The new output is initialized to false before size validation; on a cached
direction duplicate, the helper sets it from `!__abox_ipc_queue_empty(data)`
while holding `ipc_queue_lock`. After the helper releases the lock, the
scheduler makes one `queue_work()` call only if the request was a duplicate
and the locked snapshot was nonempty, then exits the duplicate path.

The branch does not insert a queue entry, update the cached `enabled` value,
emit an insertion trace, retry `-EBUSY`, delay, or flush. The queue snapshot
and the call’s placement outside the lock are visible in the extracted
production C. FE trigger callbacks use the atomic, asynchronous specialized
path; BE `mute_stream` uses the non-atomic, asynchronous specialized path.
Both reach the same scheduler. Generic `abox_request_ipc()` passes no cached
trigger state, so repeated generic messages remain non-deduplicated; its
synchronous flush and atomic-plus-sync direct-send paths are unchanged.

Error and boundary paths remain consistent with the prior scheduler:

- `-EINVAL`: both output booleans are false before the validation return, so
  the scheduler retains its existing trace, queue-work, optional sync-flush,
  and `-EINVAL` break behavior. The new harness does not execute an invalid
  message-size scenario.
- `-EBUSY`: a nonduplicate full queue still follows the existing retry loop
  (atomic delay or non-atomic flush behavior). A same-direction duplicate is
  recognized before the full check; if entries are pending it gets one
  queue-work call, with no insertion retry. The host scenarios execute the
  atomic nonduplicate full-ring retry and the duplicate full-ring re-kick;
  the non-atomic full-ring retry is source-traced, not directly exercised.
- Empty duplicate: no queue-work, PM resume, state change, or insertion trace.
- Pending duplicate after PM failure: no PM call occurs in the callback; the
  retained FIFO is drained only when the test explicitly invokes the worker
  with a positive PM result. The tested retained order is START then STOP.

The pending flag is a point-in-time queue snapshot. A worker may drain the
observed entries before the later `queue_work()` call, leaving an empty worker
invocation. Because the duplicate path does not enqueue or copy a message,
that race cannot replay or reorder an IPC. If work is already pending,
`queue_work()` may return false; that return is ignored. The patch therefore
establishes one source-level call for an eligible duplicate, not a particular
execution count, timing, or unconditional progress guarantee. The research
note describes this boundary without claiming hardware acceptance.

## Harness and verification

The test loader reuses the pinned PM fixture helper. It verifies source blobs
and worktree files against the pinned base, caps each source at 256 KiB, and
limits optional public fetches to five seconds per file. The explicit local
source-tree path also checks the exact derived HEAD and base commit. I used
the verified local path; I did not run public fetch.

The harness applies and extracts the actual source in this order:

1. observation patch;
2. queue/state linearization patch;
3. runtime-PM error patch;
4. duplicate re-kick patch.

Its pre-fix build is the same composed source before patch four. It reproduced
exactly the expected missing re-kick assertions for pending BE STOP, pending
FE STOP, and a full-queue duplicate. The patched build passed all scenarios.
The extracted C includes the queue empty/full/put/get helpers, worker,
scheduler, generic and specialized request paths, and FE/BE callbacks. It
checks FIFO contents and indices, cached state, queue-trace calls, queue-work
and flush counts, PM calls, and sender order. `pthread_mutex_trylock()` checks
that queue-work and flush calls occur after the queue mutex is released.

I ran the exact `--source-tree /home/corpunum/s22-workers/camera-kernel-build-20260927`
command in normal Python, `python3 -O`, and `PYTHONOPTIMIZE=1 python3` modes;
all three exited 0. In each run, all four patch check/apply steps passed, the
pre-fix and patched extracted C compiled at `-O0` and `-O2`, the baseline
reported only the three expected failures, and the patched scenarios passed.
The Python runner uses explicit `require()` checks, not `assert`, so optimized
Python does not remove its checks. No `-I` mode was used.

The harness’s workqueue functions are counters: they do not model pending-bit
coalescing, a running worker, or real dispatch. Its mutex shim verifies call
placement in the extracted single-threaded paths, not Linux IRQ/spinlock or
concurrent interleavings. The point-in-time drain race is reasoned from the
source and documented; it is not executed in this harness. No full ABOX
composite build was run for patch four.

## Scope boundary

No phone, SSH, ADB, firmware, stream, trace-policy, kernel build, or powered
audio/DMA operation was performed. Firmware acknowledgement, DMA progress,
`hw_ptr`, and audible output remain unverified. The previously observed
`hw_ptr == 0` / disabled-RDMA state remains unresolved. No hardware acceptance
is claimed.
