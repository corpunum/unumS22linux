# NPU hwdev reference-unwind follow-up — 2026-09-27

## Finding and narrow fix

The pinned kernel tree at
`/home/corpunum/s22-linux/lineage/android_kernel_samsung_s5e9925` was clean at
`4e5c5ad7d950e4de0688b5663965f2075654b2ad`. In its `npu_hwdev_bootup()`
(`drivers/vision/npu/core/npu-hw-device.c:382-418`), both loops ignored the
return from `npu_hw_ref_get()` and returned the initially-zero `ret`. The
helper in `npu-hw-device.h:98-113` increments the count before invoking the
first-reference callback. Therefore a failed first callback leaves an
increment owned by this call, and later selected-device references may also
have been acquired. The enclosing normal-boot caller can otherwise treat the
operation as successful, with no matching unwind for the partial acquisition.

The follow-up adds error propagation and per-call `boot_hids` / `init_hids`
masks to the candidate patch. A get is recorded before branching on its
callback result, balancing the increment even when the first callback failed.
On error, it releases partial init references in reverse order, followed by
boot references in reverse order. Cleanup continues after a failed final
callback, marks device emergency state, and returns the original acquisition
error. On success, the prior order—acquire all selected boot references, then
selected init references—is unchanged. `hids` remains the selector; unselected
devices are not touched.

## Executable host evidence

`tools/hardware/test-npu-session-lifecycle.py` now extracts the exact added
`npu_hwdev_bootup()` C function from the patch and compiles it with
`tools/hardware/npu-ownership-harness.c`. The shims implement the pinned
increment-before-callback and decrement-before-final-callback semantics, with
injectable failures. Regressions verify:

* a failed first boot callback is balanced without dropping an already-owned
  reference from another caller;
* a later init callback failure unwinds the failing and prior init refs, then
  boot refs, in reverse order and only for the requested `hids`;
* a final-callback cleanup failure does not replace the original init error,
  does not stop later releases, and sets emergency state; and
* successful acquisition keeps exactly the selected references.

The host suite passed in normal and optimized Python modes, including the
previous waiter-helper C harness and Python reference model. This executes
extracted candidate C with shims; it does not execute the Linux driver and
proves no kernel scheduling, callback,
hardware, or device-runtime behavior. No kernel build or hardware operation
was performed. The source tree was not modified; `git apply --check --verbose`
accepted the entire candidate patch against the pinned tree. The optional
`S22_NPU_KERNEL_TREE` checks expect a candidate tree with the patch applied and
were not run against the pristine baseline.

## Readiness boundary

This addresses the concrete ignored-reference-error path, not the existing
mailbox publication-drain liveness limitation. The waiter's publication drain
is still unbounded if the synchronous publisher stalls; BOOTUP remains refused.
Independent review and a later properly authorized candidate validation are
still required. No synthetic host result is a claim of NPU hardware function.
