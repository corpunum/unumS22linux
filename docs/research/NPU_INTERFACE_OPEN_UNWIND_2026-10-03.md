# NPU interface open/unwind error path — 2026-10-03

## Result and scope

This source-only patch repairs the report-workqueue allocation and partial
resource-unwind path in drivers/vision/npu/core/interface/hardware/npu-interface.c.
Before the change, npu_interface_open() assigned a failed allocation directly
to the global queue, jumped to a path that returned ret == 0, and did not
release successfully acquired IRQs or affinity hints. Its shared error loop
also cleared/freed all system->irq_num entries even if only a prefix had been
acquired. npu_interface_close() submitted work before checking whether the
queue existed. Its original pending-work diagnostic was guarded by an outer
mailbox check; the patch retains that safety while moving the header check
inside the pending branch so probe-only queues can be detached/drained safely.

The patch preserves the existing report worker and request ordering. It returns
-ENOMEM for either workqueue allocation failure, tracks IRQ and affinity
ownership locally until successful open, and unwinds only acquired entries.
report_wq_lifecycle_lock serializes probe/open/close; a second active open
returns -EBUSY, so committed static ownership counts cannot be overwritten by
an overlapping attempt. A single IRQ-safe publication lock protects every
producer's queue lookup/submission and queue install/detach. Detach is followed
by IRQ release/synchronization and then queue drain/destruction outside the
spinlock. No automatic retry, reset, MMIO modification, firmware access, or
device operation was added.

## Pinned-source evidence

Source baseline: Samsung source pin 4e5c5ad7d950e4de0688b5663965f2075654b2ad;
the exact target file SHA-256 is
c2deaa0abd990184b64373bb13983f048b925e0f5f421f6623de7de85f667108. The
clean local derived fixture was
/home/corpunum/s22-workers/camera-kernel-build-20260927 at
3fca50941422439b2019db2e4a3dc1016b2138a1; the preserved composed NPU12 source
was e9c3016233a72ceccb13e537f0b7ef72426582b9. Both have the same target-file
SHA. Ordinary git apply --check passed against the clean fixture and the
composed NPU12 source. The new patch changes only the target C file and is
ordered as patch 13, after the frozen NPU12 stack (including the msgid
validation and firmware-report lock-unwind patches); it does not modify those
prior patch bytes.

Relevant pinned source locations below refer to the unpatched file:

- npu_interface_probe() and npu_interface_open() allocate the initial
  "my work" queue and later replace its global pointer with the "rprt_manager"
  allocation at lines 470–554. The error loop frees every IRQ slot, not just
  acquired entries.
- npu_interface_close() queues before its queue-presence check, cancels/drains
  the worker, then clears every affinity and frees every IRQ at lines 556–593.
  Its original outer mailbox check prevents the diagnostic from dereferencing a
  NULL header; the final patch preserves this via a local guard.
- mailbox_isr1() (where that configuration uses the worker), mailbox_isr2(),
  and fw_rprt_manager() are the queue producers at lines 206–247 and 1221–27.
  They now use the same helper that holds the publication lock through
  queue_work().
- fw_rprt_gather() checks interface.mbox_hdr for NULL and advances the
  report-ring read pointer after gathering at lines 1165–1214. The open-time
  queue swap therefore detaches/drains the old queue, installs the new queue,
  then explicitly queues one catch-up pass. The host test verifies this
  schedule, not firmware-ring persistence on hardware.
- npu_system_resume() calls npu_interface_open() but its p_err path marks
  emergency and sets ret = 0 at lines 1747–1763. The patched function returns
  its error locally, but this upstream error swallowing remains unchanged.
- Separate direct callers invoke fw_rprt_gather() through the log callbacks
  at npu-log.c:1703–1714 and 1755–1766; those process-context diagnostic
  gathers are not fenced by the report workqueue publication lock. Existing
  NPU remove/system-release also has no call to npu_interface_close() in the
  inspected npu_device_remove() / npu_system_release() path. These are
  existing lifecycle gaps outside this one-file repair and are not claimed
  fixed.
- Pinned kernel/irq/devres.c:129–146 shows devm_free_irq() delegates to
  free_irq(). kernel/irq/manage.c:1916–1921 synchronizes in-flight hard IRQs
  before release. This justifies the order used here: detach prevents new queue
  submissions, IRQ release completes handlers that already entered, then
  draining can safely retire the published queue.

## Ownership and race boundary

The lifecycle mutex covers probe/open/close and the committed IRQ counts. A
producer takes report_wq_lock, observes the queue pointer, and submits while
still holding that lock. A detacher takes the same lock and clears the pointer.
Thus a racing producer either finishes submission before detach (and its work
is covered by the later flush), or observes NULL after detach. Flush and destroy
never run under the spinlock. Close detaches before freeing only the committed
affinity/IRQ prefixes; devm_free_irq() then synchronizes the actual IRQ
handlers before the queue is flushed and destroyed. The close diagnostic reads
mailbox pointers only when the header is non-NULL.

During successful probe-to-open queue replacement, producers can briefly see
NULL between old-queue detach and new-queue publication. The explicit
post-install work submission requests a scan after that gap. The actual report
gather reads the mailbox report ring; this source reasoning and the extracted-C
catch-up test do not prove physical firmware timing, losslessness, or DMA
progress.

The existing queue and work_report are module-global. This repair serializes
one interface lifecycle; it does not establish multi-device ownership. The
static probed flag also makes a second probe in the same loaded module return
-EBUSY; this is a deliberate guard against reinitializing a still-published
global queue, but the driver has no interface-specific remove hook in the
inspected path. Platform unbind/reprobe and late system-probe failures after
npu_interface_probe() need a separately scoped teardown integration. Direct
fw_rprt_gather() calls likewise remain outside the queue drain fence.

## Executable regression evidence

test-npu-interface-open-unwind.py uses the existing bounded
test-npu-candidate-stack.py source loader. The harness injects actual pinned
npu_interface_probe(), npu_interface_open(), npu_interface_close(), the
report queue helpers, and fw_rprt_manager() bodies. IRQ, affinity, mailbox,
mutex, workqueue, and scheduling primitives are controlled host shims. No
kernel build is part of the test.

The unpatched extracted C reproduces the source defects:

- Failed initial probe workqueue allocation returns success and a subsequent
  close submits through NULL.
- Failed second (active) workqueue allocation returns success, leaves the
  acquired IRQ/affinity state live, overwrites the probe queue pointer with
  NULL, and leaves the original queue orphaned; a subsequent close calls the
  shimmed queue_work() with NULL.
- A second IRQ request failure still clears affinity and calls devm_free_irq()
  for every configured slot, including unowned entries.

Patched extracted C verifies probe and active-queue allocation failures,
partial request/affinity failures, mailbox-init failure, successful publication,
duplicate-open -EBUSY, close-before-open with pending work and a NULL mailbox,
and repeated close. It checks exact acquired/released counts and ordering: an
unpublished new queue is drained independently, while a published queue is
drained only after IRQ release. Queue submission is asserted to be under the
publication lock; flush, destroy, affinity clear, and IRQ release are asserted
outside it. A controlled pthread interleaving holds a producer inside the
actual extracted publication helper while close is blocked acquiring the same
lock; after release, no submission reaches a detached/destroyed queue. A second
interleaving runs an explicit producer during the open queue-swap gap and
observes exactly one post-install catch-up submission to the new live queue.

Matrix completed for both the pinned public source fetch and the clean local
derived fixture: Python normal, python3 -O, and PYTHONOPTIMIZE=1 python3; each
invocation compiled and ran baseline plus patched actual C at -O0 and -O2.
All six Python invocations passed; each reported baseline reproductions and all
patched cases. The public fixture was SHA-verified by the existing loader. The
pthread, IRQ and workqueue shims are controlled host models; they are not Linux
hardirq, RCU, scheduler, workqueue implementation, memory-ordering, or physical
device evidence.

## Remaining limits

- No native kernel compile was run for this 13th source patch. The NPU12 native
  module build predates it and does not include it.
- No phone, SSH, ADB, NPU firmware, real IRQ, runtime-PM, DMA, or physical
  acceptance test was run. This is not a driver-working or BOOTUP claim.
- Upstream resume still swallows the open error after setting emergency state.
- Direct diagnostic gathers and remove/unbind teardown are not fenced here;
  broader interface lifetime repair needs traced call-site changes and review.
- The catch-up call closes the host-observed scheduling gap; firmware ring
  retention and actual report delivery remain unverified.
