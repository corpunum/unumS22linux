# NPU hwdev failed-reference finding — 2026-09-27

## Status: fix blocked; BOOTUP remains refused

Independent review rejected `59791b9` / integrated `1c41f09`. That attempted
unwind is preserved in history but removed from the active patch by this
correction. No NPU code from this wave was built or run on the phone.

The pinned kernel tree
`/home/corpunum/s22-linux/lineage/android_kernel_samsung_s5e9925` was clean at
`4e5c5ad7d950e4de0688b5663965f2075654b2ad`. Its `npu_hwdev_bootup()`
(`drivers/vision/npu/core/npu-hw-device.c:382-414`) ignores both
`npu_hw_ref_get()` results and returns its initially-zero `ret`. The helper in
`npu-hw-device.h:98-116` increments before the first callback and decrements
before the final callback. A failed first callback therefore leaves a count
incremented, and selected later references may also have been acquired, while
the caller can be told boot succeeded.

The attempted generic reverse-unwind hunk was independently rejected and
removed from the active candidate. Invoking `npu_hw_ref_put()` after a failed
first callback is not generally a safe inverse. On the pinned DSP path,
`npu_hwdev_dsp_init(true)` can fail in `dsp_system_load_binary()` or
`dsp_kernel_manager_open()` (`npu-hw-device.c:219-230`). Its false callback
calls `dsp_kernel_manager_close()` and then `npu_stm_disable()`
(`npu-hw-device.c:234-240`). Although the manager close is a no-op when
`dl_init` is zero, `npu_stm_disable()` decrements shared `npu_stm_data.enable_cnt`
without checking for a matching enable when firmware is loaded
(`npu-stm.c:698-711`). The failing bootup has not reached the later
`npu_stm_enable()` call in `npu-vertex.c:1215-1217` (or `1427-1428`). Thus a
generic final callback can decrement a shared count that belongs to another
active hardware path, or underflow the unsigned count. The same issue applies to
unwinding earlier successful init callbacks before this bootup reaches STM
enable. The outer unwind cannot claim that such rollback is safe.

The active patch deliberately does not modify `npu_hwdev_bootup()`; the
pre-existing ignored-error behavior remains unfixed. This avoids introducing
an unproven hardware inverse but does not resolve the underlying ownership
bug. A future fix needs callback-aware transactional failure cleanup and must
account for shared STM ownership; this bounded follow-up does not implement
that broader lifecycle change.

## Host reproducer

`tools/hardware/npu-hwdev-bootup-baseline.inc` plus
`npu-ownership-harness.c` retain the original narrow ignored-return reproducer.
The added GPL-2.0 fixture `npu-hwdev-callbacks-baseline.inc` contains pinned
source bodies for the ref helpers, DNC/NPU/DSP callbacks, `npu_stm_enable()` /
`npu_stm_disable()`, STM SFR counter helper, DSP manager close, and
`npu_hwdev_bootup()`. `npu-hwdev-callback-failure-harness.c` compiles those
actual bodies with explicit host shims. It reproduces:

- failed DSP manager-open acquisition: bootup returns success, the DSP and
  DNC counts remain acquired, and a generic put invokes two STM disables even
  though the failed DSP attempt did not enable STM; the unsigned count wraps
  to `UINT_MAX - 1`;
- with a prior NPU owner and a successful source-level STM enable, the generic
  failed-DSP put consumes one of that owner's two count units; later normal
  NPU/DNC teardown wraps the count to `UINT_MAX`;
- a concurrent get returns success while the first callback is still pending,
  then the first callback fails, leaving both increments present.

The harness invokes the exact checked-in fixture function bodies. It is still
a host reproduction only: Linux atomics, mutexes, firmware, command mapping,
STM hardware/MMIO, and the kernel lifetime model are represented by shims. It
does not prove device behavior or implement a fix. Hosted CI can run this
fixture without a kernel checkout. For optional fixture provenance checking,
`S22_NPU_CALLBACK_SOURCE_TREE` points to the pinned clean source tree; the test
compares each included function body byte-for-byte with commit
`4e5c5ad7d950e4de0688b5663965f2075654b2ad`.

The separate existing `S22_NPU_KERNEL_TREE` option expects a tree with the
candidate session/protodrv/vertex changes applied, not the pristine baseline
checkout; its wider source-contract check deliberately rejects the latter.
Independent review also compared the original baseline bootup fixture to the
pristine pinned function without running those candidate-tree checks. Do not
confuse that option with the clean-source callback-fixture check above.

The host preflight now reports the callback-error, retained-increment, ignored
parent-get-error, and unmatched shared-STM source facts (or `null` when those
source inputs are missing). Its readiness output separately keeps failed
first-acquire ownership, first-callback serialization, and shared STM
ownership kernel validation false. This makes the unresolved gap explicit;
the preflight still exits 2 and leaves BOOTUP refused.

The NPU lifecycle and preflight host suites passed normal and optimized Python
modes. The optional byte-for-byte callback-fixture check also passed against
the pinned clean source tree. The candidate patch still has no kernel build,
runtime, or device validation. The mailbox publication drain remains
unbounded if its synchronous publisher stalls. No result here supports BOOTUP
or NPU readiness.
