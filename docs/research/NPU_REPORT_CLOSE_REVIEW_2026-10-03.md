# Independent NPU report-close review — 2026-10-03

## Verdict

**PASS_LIMITED** for the stated report-ring direct-reader lifetime claim in the
host-extracted source and regressions. The composed candidate fences the three
reviewed report readers against mailbox close, and the public-source C matrix
passed in all required Python modes. This is not Linux-kernel, firmware, or
hardware acceptance.

I reviewed the frozen author change (`3b006ed3637985f66fedbb2ee830a2798436632e`),
its report and host receipt, and the final patched interface identified by
SHA-256 `3e1c9198f8317beb05f0e978f08c270fbc6e25cb73224784ccd92e959a0c4ef7`.
The report-owner patch is `9bf6eef33be3b8f2c49744e362dada21613a0969576ef78a725d356cdebb0060`.

## Ownership and lock review

`fw_rprt_gather()`, `dbg_print_error()`, and `npu_check_unposted_mbox()` take a
report-owner pin before accessing the mailbox and use their pinned local
pointer. The two direct `npu-log.c` callers finish gathering before acquiring
`fw_report_lock`; the second caller releases it before its profiler call. The
pin is dropped after the mailbox access, including the profiler's shared error
cleanup path. The profiler preserves its existing `GFP_ATOMIC` and conditional
`interface.lock` policy in interrupt context. `dbg_print_error()` retains its
pre-existing unconditional mutex policy; the selected source had no callsite,
so the C harness invokes it directly.

The owner pointer and refcount transition together under the statically
initialized owner spinlock. Probe does not reset those objects, and the
one-time probe guard rejects reprobe before `interface.lock` is reinitialized.
Open publishes only after `mailbox_init()` succeeds. Close serializes lifecycle
transitions with `report_wq_lifecycle_lock`, queues final work, detaches the
queue, clears affinity hints, frees/synchronizes IRQs, drains/destroys the
workqueue, detaches the report owner, waits for readers, then deinitializes the
mailbox and clears the interface pointer. The inspected queue helper, three
mailbox IRQ handlers, and report worker do not take the lifecycle mutex; the
direct readers do not take it either. Close does not hold the owner spinlock,
queue spinlock, `interface.lock`, or `fw_report_lock` over the reader wait.
This is source-level ordering evidence, not lockdep or a kernel scheduling
test.

The publication-busy path fails open with `-EBUSY` and unwinds queue/IRQ
resources. It deliberately skips `mailbox_deinit()` so it cannot free storage
that might still be published. The lifecycle mutex makes this an invariant
failure in the intended single-device sequence; the branch is checked in
source but is not directly induced by the regression. It may retain initialized
mailbox storage for higher-level recovery. The mailbox-init error path is
executed and publishes no owner. Duplicate probe/open, reopen, close-before-
open, and repeated close are exercised.

The report-owner covers only the report-ring storage used by these readers.
Command/response readers and writers that use `interface.mbox_hdr`, result and
parser ownership, report-store buffers, DMA quiescence, and provider/removal
ordering remain outside this change.

## Refcount result and negative control

Each passing runner invocation fetched and hash-checked the pinned public
`include/linux/refcount.h` and `lib/refcount.c`, extracted the decrement/test,
decrement, and warning-saturation bodies, and executed them in the host harness
with controlled C11/pthread atomic and warning shims. In the pinned source,
`refcount_dec()` fetch-subtracts and warns when the old value is at most one;
`refcount_warn_saturate()` then writes `REFCOUNT_SATURATED`. That makes
`refcount_dec()` on the sole publication reference leave the close wait
condition unsatisfied. By contrast, `refcount_dec_and_test()` on one takes the
`old == 1` success branch and returns true without warning.

The pre-fix control changes only the extracted detach call back to
`refcount_dec()`. At publication count one it observes one warning, a saturated
counter, and a still-false zero-reference condition. Its mock wait escape is
compiled only for that negative-control executable. That executable calls
detach directly, checks the unsatisfied condition, and does not treat a close
return as progress. The fixed liveness executables have no escape macro; their
wait uses the condition-variable shim and blocks until the reader drops its
pin. The no-reader close reaches zero and deinitializes once. That successful
return also excludes the warning path in this harness, which would saturate
the count and leave the fixed wait blocked.

The underflow control executes the extracted decrement/test and warning
bodies; it saturates instead of allowing false zero/free. The overflow control
uses the harness's C11 `refcount_inc_not_zero` shim, not an extracted kernel
increment helper. The pinned public increment source was reviewed for its
saturate-and-warn behavior, but the harness's overflow execution is shim
evidence, not execution of that Linux helper. Likewise, no host shim proves
Linux atomic ordering, spinlock semantics, waitqueue behavior, or hard-IRQ
scheduling.

## Independent execution

The mandatory public composition starts from LineageOS source commit
`4e5c5ad7d950e4de0688b5663965f2075654b2ad`, applies the pinned native-eight,
NPU12, NPU13, NPU14, and report-owner patches, and reproduces the expected
final interface hash above. Both pinned refcount source files were part of
each run. The optional report-source fixture was absent throughout these
passing runs; public composition was not skipped.

| Invocation | Python optimize | Extracted C compile/run jobs | Result |
| --- | ---: | ---: | --- |
| `python3 tools/hardware/test-npu-report-close-lifetime.py --expect-opt 0` | 0 | 6 | PASS |
| `python3 -O tools/hardware/test-npu-report-close-lifetime.py --expect-opt 1` | 1 | 6 | PASS |
| `PYTHONOPTIMIZE=1 python3 -B tools/hardware/test-npu-report-close-lifetime.py --expect-opt 1` | 1 | 6 | PASS |

The 18 C jobs each covered baseline races, the pre-fix no-reader refcount
control, and the fixed composition at `-O0` and `-O2`. Baseline direct-note
and interrupt-context profiler races were reproduced using barriers and
stopped before host undefined behavior. Fixed scenarios covered pre-probe and
pre-open refusal, mailbox-init failure, duplicate probe/open, both direct log
callers, debug reader, interrupt-context profiler pin lifetime, post-detach
refusal, reopen, repeated close, no-reader close, and refcount underflow and
saturation handling.

I also checked explicit optional-fixture rejection. A supplied absent path and
a supplied tree with the wrong HEAD both failed after the mandatory public
composition completed; neither was counted as a passing regression run. One
earlier attempt to validate the absent path failed during the bounded public
fetch (`Network is unreachable`) before fixture validation or compilation. It
is retained as a failed invocation in the receipt, separately from the later
expected rejections and successful matrix.

## Host-runner integration review

I reviewed coordinator commit `71575f1d4440f3a394fdb53fd8e2d67eec49c6a0`
read-only. It adds the report-close and imgloader-shutdown scripts at matching
positions in the runner's fixed `REVIEWED_HOST_TEST_PATHS` and `HOST_TESTS`
tuples, with the same additions in the independent policy-test tuple. The
runner remains a sequential, explicit allowlist; no test discovery or live
device command was added. Its child environment remains the same fixed eight
keys, uses isolated Python (`-I -B`), and omits inherited `S22_*`, token, and
key variables.

All seven runner-policy tests passed in normal, `-O`, and
`PYTHONOPTIMIZE=1` modes (21 passes). The fixed tuples contain 66 normal and
63 optimization-safe invocations. I did not execute the full 129-script host
matrix; the coordinator ran that separately. The added imgloader script has a
fixed optional source-tree fallback; that tree was present, clean, and at its
expected source commit in this environment. It is read-only corroboration, not
test discovery or a device path; absent CI environments continue through the
public source route.

The coordinator's saved ARM64 frontend result is separate evidence and was
not rerun or counted here. This review did not build a module or Image, link,
run modpost, verify symbol CRCs, stage firmware, access a provider, or operate
hardware. No phone, SSH, ADB, USB, or BOOTUP operation occurred; production
BOOTUP remains refused.

## Remaining limit

The kernel `wait_event()` has no deadline. If a pinned reader never returns,
close can wait indefinitely. The process timeouts bound the host tests only;
they are failures, not evidence that kernel close would progress. This storage
ownership change does not establish liveness against a permanently stuck
reader, nor does it authorize freeing the mailbox after a timeout.
