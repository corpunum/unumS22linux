# NPU reference transaction follow-up — 2026-10-02

## Result and boundary

This is a limited source patch candidate for the NPU hardware-device reference
helpers. It serializes each reference count, publishes a first reference only
after its callback returns success, propagates helper/parent errors, refuses
underflow, and keeps failed final teardown poisoned instead of claiming the
resource was released. It does not establish that every hardware callback is
failure-atomic. In particular, an uncertain leaf boot failure retains its
parent boot reference; there is no automatic cleanup/retry for that retained
state.

NPU BOOTUP remains refused. The existing host preflight, authorization, and
hardware/runtime gates are unchanged. No kernel build, module load, phone
access, BOOTUP, firmware operation, or device acceptance was performed.

The preflight was rerun from this isolated worker tree and exited 2 with
`bootup_ready: false`, `bootup_authorized: false`, and `device_access: false`.
Its private artifact/config inputs were absent, and lifecycle, firmware,
recovery, and owner-authorization gates remained false.

## Pinned source and patch

The patch targets upstream LineageOS kernel commit
`4e5c5ad7d950e4de0688b5663965f2075654b2ad` and applies with ordinary
`git apply --check` to its `npu-hw-device.h` and `npu-hw-device.c`. The test
fetches those two public raw files at that immutable commit, caps each response
at 128 KiB with a 5-second timeout (256 KiB total), and verifies these
SHA-256 values before extracting or compiling any C:

| File | SHA-256 |
| --- | --- |
| `drivers/vision/npu/core/npu-hw-device.h` | `43165437c7b6a4c50599c2677536376ab31579de0f5866c8b76e33ff7813e9c3` |
| `drivers/vision/npu/core/npu-hw-device.c` | `14617a6f8e5b08e1bb169618daa8544f2680ad6709cb9f3b9730919d4dc8e16f` |

An optional `S22_NPU_REFCOUNT_SOURCE_TREE` path supports offline provenance
checks. It is accepted only at derived commit
`3fca50941422439b2019db2e4a3dc1016b2138a1`, with a clean tree, unchanged NPU
files relative to `4e5c5ad`, and the same two hashes. A public fetch outage
prints `SKIP actual-C transaction test` and exits 77; it is not reported as an
actual-C pass. A hash mismatch or patch-application failure is a test failure.

## Narrow behavior change

The helper mutex is per reference counter and is held across its first/final
callback, so concurrent get/put operations on that counter cannot observe a
callback in flight. A successful first callback publishes count 1; later gets
increment under the lock. An initial callback error leaves count 0 and stores
the error, refusing retry. For the final put, count stays 1 until teardown
succeeds. A final callback error preserves count 1 and stores the error, so
later get/put calls return it without silently reacquiring or repeating a
possibly partial teardown. A put at count zero or below returns `-EINVAL`; an
increment at `INT_MAX` returns `-EOVERFLOW`.

Parent get failures are propagated before the leaf callback runs. Parent put
errors are propagated by both boot close and init deinit. If a leaf boot-on
callback fails, its already-acquired parent reference is deliberately retained:
the pinned `npu_hwdev_default_boot()` can perform `pm_runtime_get_sync()` and
then fail clock enable, leaving partial power. The transaction helper cannot
prove that dropping the parent is safe. The leaf counter is poisoned at zero;
the retained parent count records the dependency. This is conservative
retention, not a complete recovery path.

Init rollback is separate from ordinary teardown. DNC and NPU init-on are
no-ops, so their pre-STM abort callbacks are no-ops. A successful DSP init
abort closes only the manager acquisition; it does not call the generic DSP
init(false), which also decrements the shared STM count. A failed manager open
does not call manager close because the source has not established ownership.
Ordinary shutdown still uses the original matching final callbacks.

`npu_hwdev_bootup()` now returns get/init errors, releases previously
successful references on failure, and uses the source-specific init abort
path before releasing selected boot references after an init error. Shutdown
returns the first init/boot put error. Recovery shutdown returns a put error
instead of repeating its `while (refcount)` loop forever on a poisoned final
reference. Cleanup can still be partial when a later callback fails; the
retained counts and error are reported, not erased.

## Callback and caller limits

The count protocol is truthful only relative to callback return values. The
unchanged `npu_hwdev_default_boot()` has a callback-level error defect:
`pm_runtime_get_sync()` can return a positive success value, which this code
logs as an error, and the later clock result overwrites the PM result. Thus a
PM failure followed by successful clock enable can be returned as success;
the ref helper cannot poison an error the callback hides. The status field is
also set active after the sequence regardless of an earlier failure. This
callback needs separate source repair and exact-C tests before callback-result
truth can be treated as hardware-state truth. The current host test injects a
mock leaf callback that reports a partial failure; it does not execute or
validate PM, clocks, or device state.

On the current NPU/DSP graph, child reference callbacks acquire only the DNC
parent; DNC has no parent. The normal/secure boot and bootdown wrappers hold
`vertex->lock` while calling the hardware-device functions, and the inspected
runtime-PM callbacks are no-ops that do not reacquire that lock or a reference
mutex. No reverse DNC-to-child callback path was found. This source trace
shows no cycle in the present graph, but is not lockdep or runtime evidence and
does not establish safety for future callbacks.

Some callers still discard propagated shutdown errors: secure and normal
bootdown ignore `npu_hwdev_shutdown()` results, and one secure-bootup memory
failure cleanup also ignores its shutdown result (`npu-vertex.c`). The
`npu_vertex_close()` paths inspect it. `npu_device_recovery_close()` adds the
recovery-shutdown result but then calls `BUG_ON(1)` on any error. Those caller
policies remain outside this narrow patch and need independent review before
claiming end-to-end error handling.

## Host regression evidence

`tools/hardware/test-npu-refcount-transaction.py` extracts the exact pinned C
functions, compiles the unpatched baseline and patch-applied versions with
pthread and device-service shims, and runs both `-O0` and `-O2` C builds. The
baseline produces expected failures for callback publication, ignored parent
init error, underflow, shutdown error propagation, and a concurrent get that
returns success before the first callback fails. Patched C passes tests for:

- serialization of concurrent first get on success and failure;
- parent acquire-error propagation and child callback suppression;
- a partial leaf boot callback error retaining its parent through the caller's
  earlier-reference rollback;
- parent final boot/init errors propagating through child close/deinit;
- DSP manager-open failure without generic STM decrement, and successful
  manager-only abort;
- underflow refusal, final-error poisoning/no retry, and recovery-loop stop;
- ordinary paired STM teardown remaining distinct from pre-STM DNC abort.

The script passed in normal Python mode, `python -O`, and
`PYTHONOPTIMIZE=1`; the optimized-environment run also used the optional
verified local-source path. These are exact-source C-function tests under
host shims, not a kernel build or runtime. Linux lock semantics, PM/clock
effects, firmware/STM/MMIO, callback context, and actual hardware behavior
remain untested. The rejected generic inverse-unwind approach is not used.
