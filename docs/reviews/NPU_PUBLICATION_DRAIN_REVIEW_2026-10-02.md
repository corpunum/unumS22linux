# Independent review: NPU POWER_CTL pre-authorization drain

Date: 2026-10-02

Reviewed candidate: `394ecc49e36208781829741638365524291b17f4`

Patch 10: `tools/hardware/npu-publication-drain-ownership.patch`

Patch SHA-256: `7137b797c9b4658e1ec7e7829054b11ed684e51552933203d5af45b7a9743709`

## Decision and scope

Clear the patch's narrow source-level claim: a canceled POWER_CTL waiter may
leave the drain before publication authorization, while the stack waiter stays
registered until the caller unlinks it under the waiter lock. I found no patch
logic blocker at this boundary.

Block broader claims that this makes committed publication or driver shutdown
bounded, permits freeing state after a timeout, proves close/reopen quiescence,
or establishes firmware or hardware acceptance. Those outcomes are outside
this patch and the host harness.

## Ownership and ordering review

`begin_publish`, `authorize_publish`, `finish_publish`, and the response
callback look up the waiter by cookie under `npu_power_waiters_lock`; the
cancellation path uses its caller-owned stack waiter under that lock. The
protodrv adapters carry the cookie and request ID, not a pointer to the
caller's stack waiter. The callback also checks request ID and refuses to
modify a canceled waiter. The caller unlinks the waiter only after the drain
returns, under that same lock.

The lock gives two safe outcomes. If cancellation wins before authorization,
it sets `cancelled`; authorization then fails its `!cancelled` check, or finds
no waiter after unlink. The stale request returns to FREE without calling the
mailbox publisher. If authorization wins first, it sets
`publish_committed`; cancellation continues to the existing
`wait_for_completion(&waiter->publish_done)`. That wait is still unbounded.

The harness creates a later waiter with a fresh production-style cookie while
deliberately reusing the old request ID. Repeated old-cookie authorization,
callback, and finish calls leave that waiter unchanged. Cookie lookup, rather
than request ID alone, prevents stale events from attaching to it.

The composed source shows `npu_session_NW_CMD_POWER_NOTIFY()` and
`npu_session_close()` serialize on `session->global_lock`. A committed stuck
publisher can therefore keep POWER_NOTIFY holding that lock and delay session
close. `proto_drv_close()` calls `auto_sleep_thread_terminate()` before
destroying the NW LSM; that termination calls `kthread_stop()` and can also
wait indefinitely for a wedged AST publisher. The source and host test do not
establish an IRQ or firmware quiescence fence across close/reopen.

## Fixed host-regression allowlist review

I also reviewed commit
`58478242f696ce9b1dafbbfa700b74353e9252f2` read-only. It changes only
`tools/hardware/run-host-regressions.py` and
`tools/hardware/test-host-regression-runner.py`: the NPU drain test and the
existing audio worker-PM test are added to both runner path tuples and mirrored
in the policy test's independent expected tuple, in matching order. It adds no
test implementation, launcher, device command, credential, or discovery rule.

The runner still requires exact equality with its reviewed path inventory,
resolves only unique in-repository `tools/hardware/` or `tools/pi-web/` files,
rejects symlinks/path escapes, and invokes scripts one at a time with Python
`-I -B`. Its child environment is rebuilt from a small fixed set and excludes
inherited `S22_*`, token, and key variables; the policy test explicitly checks
those constraints and rejects substitution of a live-device script. The two
newly listed scripts are host-side extracted-C regressions. This is a static
allowlist review; I did not run the repository-wide suite as part of this
review.

## Independent evidence

At review start, the worktree was clean at the reviewed candidate. The explicit
source fixture was clean at
`/home/corpunum/s22-workers/camera-kernel-build-20260927@3fca50941422439b2019db2e4a3dc1016b2138a1`; its NPU files match pinned base
`4e5c5ad7d950e4de0688b5663965f2075654b2ad`. The loader verified per-file
SHA-256 pins and caps, patch hashes, and the ordered composition of patches
1–9 before applying patch 10. The resulting source diff was limited to
`npu-session.c`.

I ran the requested Python modes against that local fixture:

```sh
env S22_NPU_PROBE_SOURCE_TREE=/home/corpunum/s22-workers/camera-kernel-build-20260927 S22_NPU_SHUTDOWN_SOURCE_TREE=/home/corpunum/s22-workers/camera-kernel-build-20260927 python3 -I tools/hardware/test-npu-publication-drain-ownership.py
env S22_NPU_PROBE_SOURCE_TREE=/home/corpunum/s22-workers/camera-kernel-build-20260927 S22_NPU_SHUTDOWN_SOURCE_TREE=/home/corpunum/s22-workers/camera-kernel-build-20260927 python3 -I -O tools/hardware/test-npu-publication-drain-ownership.py
env S22_NPU_PROBE_SOURCE_TREE=/home/corpunum/s22-workers/camera-kernel-build-20260927 S22_NPU_SHUTDOWN_SOURCE_TREE=/home/corpunum/s22-workers/camera-kernel-build-20260927 PYTHONOPTIMIZE=1 python3 -B tools/hardware/test-npu-publication-drain-ownership.py
```

All three exited 0 and reported `PYTHON_OPTIMIZE` as 0, 1, and 1. Each run
compiled and executed the exact extracted nine-patch baseline and patch-10 C
units at C `-O0` and `-O2` with `cc` 13.3.0 and `-Wall -Wextra -Werror`.
The baseline reproduced the canceled, pre-authorization waiter remaining
linked while the caller and session-close lock wait for the paused AST. Patch
10 returned and unlinked the waiter, let the session-close lock proceed while
the AST remained paused, then rejected the old authorization without a
mailbox callback. Both variants also retained the explicit committed-post
stall reproduction. The baseline reproduction is an expected marker in a
successful test run, not a failing test process.

The extraction uses source-pinned waiter helpers, callback, POWER_CTL case,
mailbox operation, and message-ID functions. Source checks verify the patch
changes only the cancellation predicate and preserves the unbounded committed
completion wait. The concurrency controls use pthread mutexes/condition
variables and atomics; the close thread models only the shared session-lock
acquisition. These shims do not establish Linux spinlock/completion semantics,
kernel scheduling, full session teardown, IRQ/mailbox progress, or hardware
behavior. Python checks use explicit `check()` calls, so `-O` does not remove
the test gates. No public source fetch, kernel build, device operation, or
sanitizer run was part of this review.

Harness SHA-256: `951c7f7350562f0187c354d85348020f991ac20c2d1cd1b1f2b359036c850b2c`

Test SHA-256: `188163981c10938eb4958e969763b48509e267d90265c68f9a7e3c597d3120af`
