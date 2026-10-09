# NPU report mailbox close lifetime — 2026-10-03

## Result and boundary

The host-only source patch adds a separately published report-mailbox owner
for three readers that previously checked `interface.mbox_hdr` before taking
`interface.lock`: `fw_rprt_gather()`, `dbg_print_error()`, and
`npu_check_unposted_mbox()`. A reader pins the published header under a
statically initialized spinlock, uses that local pointer, then drops its
reference. Close first stops report-queue producers, releases the acquired
IRQs, and drains the queue. It then withdraws the report owner and waits for
active direct readers before calling `mailbox_deinit()` or clearing
`interface.mbox_hdr`.

The existing NPU13 `report_wq_lifecycle_lock` serializes probe/open/close, and
`report_interface_probed` rejects a second probe before
`mutex_init(&interface.lock)`. The new owner lock, wait queue, and reference
counter use static initialization; probe never resets them. The owner is
published only after `mailbox_init()` succeeds. If publication reports an
unexpected busy owner, open fails closed without deinitializing storage that
could still be pinned. That invariant-violation path can leave the newly
initialized mailbox for higher-level recovery; the existing lifecycle mutex
is expected to make it unreachable during a valid single-device lifecycle.

This is not a general mailbox lifetime conversion. Other command/response
paths still use the mutable `interface.mbox_hdr` publication directly,
including command/result mailbox readers and writers. `interface.addr`,
result/parser ownership, message-ID lifetime, mailbox DMA/device-removal
quiescence, and the NPU system/provider lifecycle remain outside this patch.

## Pinned public source composition

The runner fetches raw files from the public LineageOS kernel at
`4e5c5ad7d950e4de0688b5663965f2075654b2ad`; each response is redirect-checked,
SHA-256 checked, limited to 512 KiB, and fetched with a five-second timeout.
It applies the tracked patches in this order using ordinary
`git apply --check --whitespace=error-all` and `git apply`:

1. `NATIVE_EIGHT_PATCHES` in `build-npu-six-profile.py`, in its declared order;
   these reproduce the selected NPU12 source hashes below.
2. NPU12 `npu-mailbox-msgid-validation.patch`
   (`c8366edfab42090ac09a6c366ad3c535a62e13384dd494ed685bbfe524a64d14`), then
   `npu-fw-report-lock-unwind.patch`
   (`71c2fa44f0fe42bd94ee416fb09453185dd418b408ba15937ec3c787a5e9bf5d`).
3. NPU13 `npu-interface-open-unwind.patch`
   (`95e63b45d60e0a2611c1f2dcab4428e5658a03ff9954b8197d175ac6fec139cb`).
4. The current NPU14 `npu-system-resume-error-unwind.patch`
   (`f1af656f9e1b8ca2bf15e934031e0adf828c442267a17761ba64f7d7c611729a`),
   selected to `npu-system.c` and `npu-device.c`.
5. This report-owner patch
   (`9bf6eef33be3b8f2c49744e362dada21613a0969576ef78a725d356cdebb0060`),
   which is constrained to `npu-interface.c`.

Raw public inputs are:

| Source path | SHA-256 |
| --- | --- |
| `drivers/vision/npu/core/interface/hardware/npu-interface.c` | `c2deaa0abd990184b64373bb13983f048b925e0f5f421f6623de7de85f667108` |
| `drivers/vision/npu/core/npu-log.c` | `e98c22dc4d005b350d9c82bf067ada5b01a0059f766f13bce39594cc44172d5c` |
| `drivers/vision/npu/core/npu-util-msgidgen.c` | `271dfe4f0b591a9a6d5a3f996e5fd2fe7a05d9b839513ce8691863bae79d3e2e` |
| `drivers/vision/npu/core/npu-system.c` | `96eaa6bf1511f3e6414e3e376d62592229454a2ea1687d760b7bb8e5952b1a05` |
| `drivers/vision/npu/core/npu-device.c` | `98be21e422ca864cc971dfe6a78e71b292d7691cb625c1f029bf4502100644ab` |
| `drivers/vision/npu/core/npu-hw-device.c` | `14617a6f8e5b08e1bb169618daa8544f2680ad6709cb9f3b9730919d4dc8e16f` |
| `include/linux/refcount.h` | `82f75597f6899f61e9a2b4097ad6f4bf1ac53d8ace1113426e708f0da6316692` |
| `lib/refcount.c` | `b8d08fc1f8a678a54587149ff084b3ef15e9173df1dde1a0282027ac5b999d56` |

The native-eight/NPU12 selected-path composition is checked against:

| Source path after native-eight | SHA-256 |
| --- | --- |
| `npu-system.c` | `96eaa6bf1511f3e6414e3e376d62592229454a2ea1687d760b7bb8e5952b1a05` |
| `npu-device.c` | `a281fd35f2311951328d797824b8dbb165639bfc7977da30044bb764688cdc11` |
| `npu-hw-device.c` | `b052462aa4919458a2b86c7ba0aed78bfdb938690cd58d5dcd01bdf0d75a312b` |
| `npu-interface.c` | `c2deaa0abd990184b64373bb13983f048b925e0f5f421f6623de7de85f667108` |

After NPU13, the interface SHA is
`3d142e00fc77148f5c215c1c105ea6e7a6d036ece82e4872f2556dd97132e6ba`.
NPU14 does not alter that interface file. The final report-owner interface
SHA is `3e1c9198f8317beb05f0e978f08c270fbc6e25cb73224784ccd92e959a0c4ef7`.
No private Git history or
`git-show` fixture is used by the required test path. An optional clean NPU12
tree can be supplied with `--composed-source-tree` or
`S22_NPU_REPORT_COMPOSED_SOURCE_TREE`; an absent tree is skipped, while a
supplied tree with the wrong HEAD, dirty state, or selected-file hash fails.

## Reader/caller trace and lock ordering

The direct log paths are not workqueue-only: the pinned `fw_will_note_to_kernel()`
and `fw_will_note()` bodies in `npu-log.c` call
`npu_log.log_ops->fw_rprt_gather()` directly (the call sites are the
NPU12-composed lines 1713 and 1767). Each call completes the gather before
taking `fw_report_lock`; `fw_will_note()` releases that lock before calling
`npu_check_unposted_mbox()` for its mailbox diagnostics. The owner spinlock is
held only to acquire/release publication references, never across
`interface.lock`, `fw_report_lock`, workqueue draining, `free_irq()`, or
`mailbox_deinit()`.

The publication sentinel must be released with `refcount_dec_and_test()`, not
`refcount_dec()`: the pinned public `__refcount_dec()` performs an atomic
fetch-sub and treats `old <= 1` as `REFCOUNT_DEC_LEAK`, after which
`refcount_warn_saturate()` sets the counter to `REFCOUNT_SATURATED`. Therefore
`refcount_dec()` on the sole publication reference never permits the drain
condition to become zero. The fixed detach uses `refcount_dec_and_test()` and
performs any wake only after releasing the owner spinlock.
An earlier patch candidate (`47647d18bb869af639f11a6d9b00b578db10a01a2bcb2c7b7cdd95805b9aa2c3`)
used `refcount_dec()` here; it is superseded and must not be treated as a
working ownership fix.

Close retains NPU13's `report_wq_lifecycle_lock` as a sleeping lifecycle
serialization mutex while it detaches IRQ/workqueue producers and waits for
the report-owner references. The pinned `npu_report_queue_work()` helper,
mailbox IRQ producers, and `__rprt_manager()` worker body do not acquire that
lifecycle mutex; the runner checks those exact composed source bodies. The
owner spinlock and reader's `interface.lock` are both released before queue or
reader drain. Thus the held lifecycle mutex excludes concurrent probe/open/
close transitions without creating a lock cycle with the IRQ, worker, or
direct-reader paths. This is a source-level lock-order audit, not lockdep or
runtime proof.

`fw_rprt_gather()` and `dbg_print_error()` pin before their existing
`interface.lock` acquisition and use only the pinned header for the report
ring. `npu_check_unposted_mbox()` pins before allocating or reading its
channels. Its pre-existing `if (!in_interrupt()) mutex_lock/unlock(&interface.lock)`
policy is unchanged: the report pin adds no sleeping mutex to interrupt
context, where the function already uses `GFP_ATOMIC`. The
`dbg_print_error()` mutex was already unconditional; a scan of the selected
NPU core found its definition but no callsite, so the regression invokes it
directly to protect the exposed source path without changing its lock policy.

The report pin covers mailbox report-ring storage only. Calls that still read
command/response controls through `interface.mbox_hdr`, and report/profile
buffer-store ownership in `npu-log.c`, are not claimed safe by this patch.

## Executable host regression

`test-npu-report-close-lifetime.py` injects the actual composed C bodies for
probe/open/close, the owner helpers, the three readers, and both direct
`npu-log.c` callers into `npu-report-close-lifetime-harness.c`. pthread
mutexes/conditions substitute for kernel locks and wait queues; IRQ,
workqueue, allocator, mailbox, MMIO-copy, and logging operations are controlled
stubs. Logging and payload output are suppressed. The runner injects the exact
pinned `__refcount_sub_and_test()`, `__refcount_dec_and_test()`,
`refcount_dec_and_test()`, `__refcount_dec()`, `refcount_dec()`, and
`refcount_warn_saturate()` function bodies. Their atomic operations, acquire
fence, warning output, and host refcount storage are controlled C11/pthread
shims; this remains userspace execution, not the Linux kernel implementation
or runtime.

Condition-variable barriers stop a reader after its actual pre-lock check or
owner pin and before its next mailbox access. The baseline composition reaches
close first: the direct-reader shim observes the mailbox deinitialized and
pointer cleared, then exits before dereferencing the stale global pointer to
avoid invoking host undefined behavior. The profiler shim similarly records
that close deinitialized the mailbox while the interrupt-context reader was
paused in its MMIO-copy stub; it does not dereference the stale pointer. In the
patched composition, close reaches the owner wait with the publication
reference withdrawn and the reader reference active. The test verifies that
`mailbox_deinit()` and global pointer clearing have not occurred, tests that
post-detach readers refuse, then releases the reader and joins both threads.
These outcomes come from barrier state, not elapsed-time guesses.

An explicit pre-fix negative control changes only the extracted detach call back
to `refcount_dec()`, starts at one publication ref with no readers, and runs
the pinned helper/warning bodies. It reproduces the saturated counter and
unsatisfied wait condition. To keep this regression bounded, that negative
control's wait stub reports the pending condition and returns; it does not
pretend that kernel `wait_event()` completes or that close has liveness. The
fixed no-reader case then proves the production-extracted
`refcount_dec_and_test()` path reaches zero and deinitializes once.

The fixed C scenarios cover pre-probe, before-open, mailbox-open-error,
duplicate-probe, duplicate-open, reopen, repeated close, both direct note
callers, debug reader, interrupt-context profiler reader, post-detach refusal,
counter saturation/underflow, and caller/close progress. Baseline, pre-fix
refcount, and fixed actual C compile and run at each of `-O0` and `-O2`.
Together the six compile/run jobs per invocation passed with `sys.flags.optimize`
0, 1 (`python3 -O`), and 1 (`PYTHONOPTIMIZE=1 python3 -B`), for 18 compile/run
jobs total. A supplied
nonexistent optional source path was rejected after the mandatory public
composition. C child runs have 20-second process bounds and `RLIMIT_CORE=0`;
timeouts fail and do not count as progress evidence.

Earlier bounded public fetch attempts remain failed attempts: one returned
`Network is unreachable` before compilation, and a separate first five-second
fetch of `lib/refcount.c` timed out. Later fresh bounded public fetches matched
all pinned hashes and the full matrices passed; those failures are not
reclassified as successful runs.
An intermediate normal-mode runner also failed compilation under `-Werror`
because the pre-fix wait-observer variable was unused in baseline C; the
template was corrected and the final baseline/pre-fix/fixed matrix passed.

The tests use condition variables for race control. Their outer compile/run
timeouts are only a bounded test-process leash; a timed-out process is a
failure, never evidence that close made progress. `wait_event()` has no
deadline in the kernel patch: if a pinned reader never exits, close can wait
indefinitely. This change addresses storage ownership, not timeout policy or
liveness, and it does not authorize BOOTUP.

## Limits

These are host-extracted-C results, not kernel-runtime evidence. The harness
does not establish real Linux spinlock/refcount memory ordering, hard-IRQ
scheduling, lockdep behavior, actual `free_irq()` synchronization, mailbox DMA
quiescence, firmware progress/acceptance, hardware safety, or system/provider
removal ordering. It does not prove that `mailbox_deinit()` alone retires every
non-report user of the mailbox. No phone, SSH, ADB, USB, firmware/provider,
TrustZone, BOOTUP, module build, or Image build was used. The coordinator may
separately reported an ARM64 front-end-only syntax check with status 0 for the
exact final interface SHA above. That check has a separate coordinator receipt
and is not part of this worker host matrix or receipt.
