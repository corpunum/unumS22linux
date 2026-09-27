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
active hardware path, or take it below zero. The same issue applies to
unwinding earlier successful init callbacks before this bootup reaches STM
enable. The outer unwind cannot claim that such rollback is safe.

The active patch deliberately does not modify `npu_hwdev_bootup()`; the
pre-existing ignored-error behavior remains unfixed. This avoids introducing
an unproven hardware inverse but does not resolve the underlying ownership
bug. A future fix needs callback-aware transactional failure cleanup and must
account for shared STM ownership; this bounded follow-up does not implement
that broader lifecycle change.

## Host reproducer

`tools/hardware/npu-hwdev-bootup-baseline.inc` is a verbatim fixture of the
pinned baseline function. `test-npu-session-lifecycle.py` compiles it with
`npu-ownership-harness.c`, whose reference helper mirrors the pinned
increment/decrement-before-callback order. The regression reproduces a boot or
init callback failure being ignored and the associated references remaining
held. A separate helper case models the pinned STM decrement and demonstrates
why blindly invoking the generic init final callback after a failed get can
change shared state. When `S22_NPU_KERNEL_TREE` is set, the test also compares
the fixture with the source function. These are host shims and a reproducer,
not execution of Linux callbacks or proof of a physical unwind.

The NPU lifecycle host suite has passed normal and optimized Python modes. The
candidate patch still has no kernel build, runtime, or device validation. The
mailbox publication drain remains unbounded if its synchronous publisher
stalls. No result here supports BOOTUP or NPU readiness.
