# Independent NPU interface open/unwind review — 2026-10-03

## Verdict

Scoped source review: **clear; no blocking source defect found** in the one-file
NPU interface open/close workqueue and partial-IRQ unwind change. The review
does not establish a native kernel build, runtime behavior, firmware report
delivery, or device acceptance. The direct diagnostic-gather and remove /
unbind / reprobe lifetime gaps, plus the upstream resume error swallowing,
remain open and are not cleared by this result.

Reviewed immutable author commit `3f14b7ba1a09d2e7b1f71dff42984579d3eece16`
(tree `8cc9321629eb79e4e514a521c5bf43b2f3743f60`), imported unchanged into this
review worktree as `9802a9a8effe22ea8310113e9d9168be5ed83ae1` (tree
`384618352a7f14cda2dd35552ac4db519d08df26`). The reviewed code inputs were:

| Input | SHA-256 |
| --- | --- |
| `npu-interface-open-unwind.patch` | `95e63b45d60e0a2611c1f2dcab4428e5658a03ff9954b8197d175ac6fec139cb` |
| `npu-interface-open-unwind-harness.c` | `b9bf832f041ca5528d3b9a64fc37338b8313920d1f8c883dccf25de92bb66766` |
| `test-npu-interface-open-unwind.py` | `f094d43e3fbcd28ffc86b0f19b0909eaf8c2f916dbfa4f9190e491e39c4ba4a7` |
| author research note | `e5ef4da3e5aaa0d46917eff9d8f41375037a3102967aaf91fa51e9060e97efcf` |

## Source review

The exact target file is SHA-256
`c2deaa0abd990184b64373bb13983f048b925e0f5f421f6623de7de85f667108` in the
pinned public base `4e5c5ad7d950e4de0688b5663965f2075654b2ad`, the clean local
derived source `3fca50941422439b2019db2e4a3dc1016b2138a1`, and the composed
NPU12 source `e9c3016233a72ceccb13e537f0b7ef72426582b9`. The ordinary patch
check/apply route passed on the public and clean-local fixture matrix. An
additional read-only `git apply --check --whitespace=error-all` passed against
the composed NPU12 source. No source files in those kernel worktrees were
modified.

The state and cleanup paths are coherent for the reviewed single-interface
scope:

| Path | Review result |
| --- | --- |
| Probe queue allocation | `-ENOMEM`; no queue or probed state is published; mapped interface pointers are cleared. |
| Partial IRQ request failure | Releases only the successfully requested prefix; clears no unacquired affinity hints. |
| Partial affinity failure | Clears only the successfully installed affinity prefix and frees only successfully requested IRQs. |
| Active report queue allocation failure | Returns `-ENOMEM`, preserves the old queue until detach, then releases the acquired IRQ/affinity prefixes and drains the old queue. |
| Mailbox initialization failure | Preserves its original error return; destroys the unpublished queue, detaches the old queue, synchronizes/releases acquired IRQs, then drains the old queue. |
| Successful open | Commits counts only after mailbox initialization; drains the probe queue before publishing the active queue; schedules one catch-up scan after publication. A concurrent open returns `-EBUSY` without new acquisitions. |
| Close | Is serialized; detaches before IRQ release; uses the committed counts; `devm_free_irq()` reaches `free_irq()` and the pinned `free_irq()` path synchronizes handlers; drains/destroys only after IRQ release. Empty and repeated close paths do not free unowned IRQs or submit to a NULL queue. |

`report_wq_lock` covers every queue-pointer read/publication and the
`queue_work()` call itself. In the patched target the two mailbox ISR report
paths (`mailbox_isr1()` when configured to queue and `mailbox_isr2()`) and the
process-context `fw_rprt_manager()` all route through that helper; the patch
removes the other raw `queue_work(wq, ...)` call sites. The helper uses
`spin_lock_irqsave()` for the process/IRQ sharing boundary. Allocation,
`flush_workqueue()`, `destroy_workqueue()`, affinity teardown, and
`devm_free_irq()` occur outside that spinlock. Thus a producer either submits
before detach and is covered by the subsequent drain, or observes NULL after
detach. The lifecycle mutex serializes probe/open/close and their committed
ownership counts.

During the successful probe-to-active queue swap, a producer may observe the
intentional detached gap. The code installs the replacement queue and submits
one catch-up pass afterward. This closes the source-level scheduling gap; it
does not prove firmware ring retention, DMA progress, or delivery on hardware.

The source-only design intentionally retains a single module-global interface.
`report_interface_probed` remains set after close, so another probe on the same
loaded module returns `-EBUSY`; the author documents this as a guard until a
separate teardown/reprobe integration exists. Open can still follow a
probe-only close because the probed state remains true. Those two transitions
are simple in source but are not both dynamically exercised by the current
harness.

## Test review and results

I independently ran `tools/hardware/test-npu-interface-open-unwind.py` six
times. All passed:

| Fixture route | Python mode | Extracted C |
| --- | --- | --- |
| Pinned public-source fetch | normal | baseline and patched C at `-O0` and `-O2` |
| Pinned public-source fetch | `python3 -O` | baseline and patched C at `-O0` and `-O2` |
| Pinned public-source fetch | `PYTHONOPTIMIZE=1` | baseline and patched C at `-O0` and `-O2` |
| Clean derived local fixture | normal | baseline and patched C at `-O0` and `-O2` |
| Clean derived local fixture | `python3 -O` | baseline and patched C at `-O0` and `-O2` |
| Clean derived local fixture | `PYTHONOPTIMIZE=1` | baseline and patched C at `-O0` and `-O2` |

The effective environment-optimization runs used `python3 -B` (without `-I`)
and asserted `sys.flags.optimize == 1`. Runtime was Python 3.12.3 and the C
compiler was GCC 13.3.0. Every invocation verified the pinned source bytes and
ordinary `git apply --check` / apply. Baseline false-success on probe/active
queue allocation and over-free after partial IRQ acquisition were reproduced;
the patched cases for those failures, affinity failure, mailbox failure,
successful open, duplicate open, close-before-open, repeated close, producer
versus close, and the queue-swap catch-up all passed.

The test extracts the actual pinned report helpers and probe/open/close/
`fw_rprt_manager()` function bodies. It statically checks both ISR bodies route
to the helper and checks raw queue submission is absent outside it. Its
controlled pthread overlap dynamically exercises `fw_rprt_manager()` against
close; it does **not** execute the extracted ISR functions or the actual
`__rprt_manager()` / `fw_rprt_gather()` callback. The pthread mutex,
IRQ-affinity, IRQ-release, mailbox, and workqueue shims are ordering models,
not Linux hardirq, free-IRQ, scheduler, or workqueue implementations. In
particular, the test's `flush_workqueue()` shim does not model a callback
already executing. These are explicit host-test limits, not failures of the
source-level lock ordering inspected above.

Useful non-blocking follow-up coverage would exercise duplicate probe and
probe-only-close-then-open transitions, and execute actual ISR entry points in
the extracted harness. No blocking missing test was found for the narrow
allocation/partial-acquisition defect repair, but the review does not treat the
existing host model as proof of Linux concurrency behavior.

## Explicitly unresolved scope

- `npu-log.c` directly invokes `fw_rprt_gather()` from two diagnostic paths.
  Those calls bypass the report-queue detach/drain fence; `fw_rprt_gather()`
  checks `interface.mbox_hdr` before taking `interface.lock`, while close
  clears the pointer without that mutex. Lifetime synchronization for that
  direct path is not fixed or cleared here.
- `npu_system_release()` does not call `npu_interface_close()`. A probe
  succeeding before a later `npu_system_probe()` failure also has no
  interface-specific rollback at that caller. Unbind/reprobe and late probe
  teardown remain unresolved.
- `npu_system_resume()` still converts an `npu_interface_open()` failure to
  success after marking emergency state. The local error is preserved by this
  patch, but caller result propagation is not repaired.
- No native kernel/module build, device access, SSH/ADB, firmware/IRQ/PM/DMA
  runtime test, package, or NPU BOOTUP was performed or authorized by this
  review.

