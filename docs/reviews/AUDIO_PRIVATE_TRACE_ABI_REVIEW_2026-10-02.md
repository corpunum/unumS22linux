# Independent private ABOX trace-ABI review — 2026-10-02

## Result

The frozen patch’s source logic is clear for the scoped ownership and ABI
review. It restores the pinned `struct abox_ipc` and `struct abox_data`
declarations and keeps the queue-side sequence in a private per-owner sidecar.
I found no source-level pointer-lifetime or list-unlink ordering defect.

The host-test claim for generic zero-sequence slot reuse is not established:
the generic send is filtered out of the production PCM-trigger send tracepoint,
and the scenario does not reuse the same physical queue slot. This is a test
coverage blocker to claiming that regression is validated, not a source defect
found in the frozen patch. Add an observable assertion on the extracted
`abox_ipc_queue_get()` sequence (or an equivalent forced-wrap sidecar check)
and a stale-sequence scenario where a reused slot receives sequence zero and
send tracing is enabled before dequeue. The composed pre-fix baseline should
fail that scenario and the private-sidecar patch should pass. Until then, treat
the generic-slot test/report wording as unsupported.

This source review does not clear native-module ABI compatibility. The prior
three-patch native artifact review recorded ten changed ABOX export CRCs and
six stale import instances; that inherited blocker was not rerun here. The
separately planned native build must compare the actual ABI5 module imports and
exports against the pinned native `Module.symvers` before any compatibility
claim.

## Frozen inputs and source review

I started from the clean prepared review worktree at `cf68ec43cb5b335e3843a032ec2add108e705fba`
and cherry-picked only author commit
`fc6e07d77472e95facb5dee7054b18ea27fdf287`. The author and review worktrees
matched byte-for-byte for all four frozen artifacts:

| Artifact | SHA-256 |
| --- | --- |
| `docs/research/AUDIO_IPC_TRACE_PRIVATE_ABI_2026-10-02.md` | `a63e523871c7df68af32960800d57709ddb8d3d05f378b7ec404785aac27dd11` |
| `tools/hardware/audio-ipc-trace-private-abi-harness.c` | `f100bc02069105d97484f3d5313a753e98d65417b5fbe9c39ffbd740d9d4932d` |
| `tools/hardware/audio-ipc-trace-private-abi.patch` | `2c85c2603a31f8a5535a21a2efdd60f6e3750174b6c706828f777a7bb109e1d6` |
| `tools/hardware/test-audio-ipc-trace-private-abi.py` | `e89a07899ad04830bcc9ee203de102f032746da7aec6b5c467c7b85c6618d3b6` |

The patch removes the sequence field from `abox_ipc` and changes only
`abox.c`/`abox.h`; it contains no `__GENKSYMS__`, CRC, or export suppression
logic. The test compares the exact pinned `abox_ipc` and `abox_data` source
declarations after composition. This is source-layout evidence only, not
compiled export-CRC evidence.

For queue insertion and dequeue, the source looks up the exact `abox_data *`
owner under RCU and retains the read-side section across the existing queue
spinlock and all sidecar slot accesses. An accepted put writes the slot even
when its sequence is zero; get copies the slot sequence to a local scalar and
clears the sidecar entry before advancing the FIFO head. The worker’s
`struct abox_ipc` scratch is invocation-local, and its send uses the copied
scalar after the RCU section has ended; no owner pointer is retained by the
publisher/send path. The same queue lock linearizes the message and metadata.

Owner publication initializes its fields before `rcu_assign_pointer()`.
Unlink and the `registered` transition are serialized by the registry
spinlock; both explicit unregister and the devm action call
`synchronize_rcu()` after dropping that lock when an entry was removed. A
fresh owner at a reused `abox_data` address is zero-initialized, while the old
sidecar cannot be freed until its grace period completes. The test separately
covers a held lookup delaying explicit unregister and sequential same-address
reuse; it does not combine those into one adversarial concurrent ABA test.

The pinned device-core source is clean at commit
`3c11bdda6ba6ba27fb4eb7e2cb096d98c514d6d3`, tree
`8aac19a6621ed74db63960d0f4af84557a20404c`. `devm_add_action_or_reset()` calls
the action on registration failure (`include/linux/device.h:259-268`).
`devm_kzalloc()` adds its resource to the devres tail, then this patch adds the
action resource; `release_nodes()` drops `devres_lock` before invoking
callbacks and releases nodes in reverse (`drivers/base/devres.c:122-127,
245-253, 506-525, 827-845`). Thus the action/grace period runs before the
earlier sidecar allocation is freed. Device-core driver removal invokes
`.remove` before `devres_release_all()` (`drivers/base/dd.c:584-592,
1185-1190`). The ABOX remove hook also orders `destroy_workqueue()`, component
unregistration, and explicit owner unregister in that order.

The injected allocation/action-registration failures return an error to probe,
which emits a rate-limited warning and continues without a published sidecar.
The queue path zeroes the candidate sequence when no owner exists, preserving
functional IPC while preventing a false queue/send pair. This patch does not
repair the pre-existing broader `abox_data`/device reference, global pointer,
probe-error, or workqueue teardown contracts; no whole-unbind lifetime claim is
made.

## Independent host verification

The extracted test was run against both source routes:

- Local derived tree `3fca50941422439b2019db2e4a3dc1016b2138a1`, which the
  loader verified against base `4e5c5ad7d950e4de0688b5663965f2075654b2ad` and
  pinned file hashes.
- Bounded public fetch of the same pinned base and four required files; all
  requests completed and hash validation passed (no unavailable fetch was
  counted as a pass).

For each route I ran normal Python (`sys.flags.optimize == 0`), `python -O`
(`== 1`), and `PYTHONOPTIMIZE=1 python` (`== 1`, without `-I`). All six
invocations exited 0. Each composed the observation, queue-linearization,
PM, duplicate-rekick, and ABI-private patches in that exact order; extracted
the queue, scheduler, FE/BE helpers, worker, and owner registry; and compiled
baseline and patched actual-C harnesses at `-O0` and `-O2`. Every baseline
reproduced exactly the asserted ABI-size and cross-owner shared-worker-scratch
failures. Every patched harness passed with `-std=c11 -pthread -Wall -Wextra
-Werror`. Allocation and devm-action failure, queue/send correlation, toggle,
retirement, same-address reuse, RCU wait, FIFO/duplicate, and two-owner
barrier scenarios passed where enabled.

The two-owner barrier places both worker threads at send after dequeue, and
the test uses atomic updates for shared queue, PM, clock, and send counters;
trace-event arrays/counts are mutex-protected, with assertions after joins.
I found no unrelated unsynchronized shim-counter increment in that scenario.
The test harness is a pthread shim, however, not kernel RCU or weak-memory
proof. Its explicit lifetime test intentionally holds the shim read lock over
a condition wait and cannot establish legal kernel RCU scheduling.

The generic-slot assertions at
`tools/hardware/audio-ipc-trace-private-abi-harness.c:302-342` are the concrete
coverage gap: the composed source only emits a send event for PCM-playback
trigger messages (`audio-ipc-observation-fix.patch:176-179`), so absence of a
send event for `IPC_SYSTEM` says nothing about the sequence passed to the
worker. That test queues one PCM message at ring index 0, drains it, then
queues generic IPC at the next index; it does not force physical slot reuse.
Directly assert the actual get output or inspect a forced-wrap sidecar slot,
and exercise a zero-sequence PCM message with send tracing enabled only before
dequeue to make stale-correlation regressions observable.

No full kernel/module build, device access, phone, SSH, ADB, HCI, firmware,
packaging, or hardware acceptance was performed. These results permit only a
host/source review and do not establish module CRC compatibility, loader
acceptance, runtime scheduling, PM, firmware, DMA, or physical audio behavior.
