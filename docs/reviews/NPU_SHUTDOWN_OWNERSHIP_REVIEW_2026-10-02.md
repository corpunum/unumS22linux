# Independent NPU shutdown ownership review — 2026-10-02

## Disposition

**Blocked at author commit `3c6eaff70f720b682cc8d09e598fcaf2269df0bc`.** The
candidate adds a persistent shutdown-uncertainty latch, but four boot-control
paths can pass the latch check before waiting for `vertex->lock`, then proceed
after acquiring that lock without checking the latch again. A lock waiter can
therefore start more NPU work after the first thread has recorded an uncertain
shutdown. The normal bootup wait path has the same gap after it drops and
reacquires the mutex.

The author note calls this a “host-reviewed source candidate.” For this frozen
commit, the evidence supports **host-tested**; this independent review finds a
blocking source issue, so that status is premature. Do not publish or promote
`3c6eaff` as a cleared source candidate. NPU BOOTUP remains strictly refused.

## Blocking finding: sticky quarantine can be bypassed after locking

`check_emergency()` now rejects `NPU_DEVICE_ERR_STATE_SHUTDOWN_UNCERTAIN`, and
the candidate correctly rechecks that bit under `vertex->lock` in
`npu_vertex_open()` and `npu_vertex_close()`. `__npu_vertex_bootup()` also
rechecks after acquiring that lock. The secure and normal boot-control
functions do not:

- `npu_hwdev_secure_bootup()` checks before `mutex_lock_interruptible()` at
  combined-source `npu-vertex.c:1048–1055`, then may boot hardware.
- `npu_hwdev_secure_bootdown()` checks before locking at `1154–1161`, then may
  issue teardown and update session state.
- `npu_hwdev_normal_bootup()` checks before locking at `1205–1212`, then may
  boot hardware.
- `npu_hwdev_normal_bootdown()` checks before locking at `1303–1310`, then may
  send protocol commands, release refs, and shut hardware down.

For example, a boot-control call can pass its pre-lock check and wait behind a
normal bootdown. If that bootdown gets a shutdown error, it sets the sticky bit
while holding `vertex->lock` (`npu-vertex.c:1343–1347`) and then unlocks. The
waiting call acquires the mutex and enters its hardware path because it has no
post-lock latch check. A system-suspend failure in `npu_device_recovery_close()`
also sets the latch while preserving the device state; a queued bootup can then
continue into `npu_hwdev_bootup()`.

There is a second window in `npu_hwdev_normal_bootup()`: when it waits for
`secure_count` to drain, it releases and reacquires `vertex->lock`
(`npu-vertex.c:1214–1229`). If uncertainty is latched while it waits, the
successful reacquisition path still continues without rechecking. The secure
bootup wait for `normal_count` has the analogous unlock/relock path at
`1041–1080`.

The exact-C harness extraction list includes and sequentially exercises secure
bootup, secure bootdown, and normal bootdown (`test-npu-shutdown-ownership.py`
`extract_functions()` around lines 233–235). `npu_hwdev_normal_bootup()` is
stubbed in the harness (`PRELUDE` around line 545) rather than extracted. The
mutex shim only counts lock calls; it does not block or schedule a waiter. Its
passing result therefore does not test the late-latch interleaving in any of
these paths. The correction needs under-lock refusal in all four functions and
a recheck after each wait-path reacquisition, with tests proving the mutex is
balanced and no hardware callback, inverse ref, free, or count mutation occurs
after the latch is observed.

## What the frozen tests establish

I archived the exact author commit to
`/tmp/s22-npu-shutdown-review-20261002.91VROR` and inspected its frozen patch
and harness.
The patch SHA-256 is
`680e72b9807133da2728fbdb5fde4cfb4d4247d6f81d15bcd2350348a4e41718`; the
harness SHA-256 is
`3d7f8d0b8ad0b26f7c4b63ac133a6a096c051759f9b827f5194e519030a58619`.
The harness checks the eight pinned source-file hashes against LineageOS
`4e5c5ad7d950e4de0688b5663965f2075654b2ad`; the clean derived source worktree
was `3fca50941422439b2019db2e4a3dc1016b2138a1`.

The frozen harness passed against the local derived source in all three Python
modes:

```sh
S22_NPU_SHUTDOWN_SOURCE_TREE=/home/corpunum/s22-workers/camera-kernel-build-20260927 python3 /tmp/s22-npu-shutdown-review-20261002.91VROR/tools/hardware/test-npu-shutdown-ownership.py
S22_NPU_SHUTDOWN_SOURCE_TREE=/home/corpunum/s22-workers/camera-kernel-build-20260927 python3 -O /tmp/s22-npu-shutdown-review-20261002.91VROR/tools/hardware/test-npu-shutdown-ownership.py
S22_NPU_SHUTDOWN_SOURCE_TREE=/home/corpunum/s22-workers/camera-kernel-build-20260927 PYTHONOPTIMIZE=1 python3 /tmp/s22-npu-shutdown-review-20261002.91VROR/tools/hardware/test-npu-shutdown-ownership.py
```

I also ran it without a configured source tree. It fetched the immutable public
pinned fixture (147,641 bytes, within the harness bounds) and passed. Each run
compiled and ran the baseline and patched extracted-C cases at C `-O0` and
`-O2`. The baseline panic and false-success/error-state reproductions passed;
plain `git apply` succeeded for the ownership patch alone and for the combined
refcount-transaction, PM-callback, and ownership stack.

The harness does execute the extracted `__vref_put()` → `__vref_shutdown()` →
`npu_device_shutdown()` bodies in the powered-close case. Event hooks verify
that the boot-ref callback's early-close/protocol-close/DHCP-deinit/system
suspend sequence precedes the hardware-off call and session free. The
dependencies are host shims, so this is call-order evidence only. Other passing
cases cover first-error recovery behavior, ERROR-state no-put handling,
unequal per-leaf init/boot refs, DNC exclusion, balanced locks in the exercised
paths, secure-buffer retention, and normal/secure bootdown error propagation.

These results are host-only. They do not clear the missing concurrent-lock
case and do not establish kernel, lockdep, firmware, module, or device
behavior.

## Remaining limits

The exact source stack is not reconciled with the historical
`npu-session-lifecycle-fix.patch` or the full integration stack; that patch
overlaps `normal_bootup()` and `npu_vertex_close()`. The candidate also does not
prove retained object lifetime across VFS `.release`, session-manager teardown,
or device/module removal. On normal bootdown, the boot ref can already have
been put when hardware shutdown fails, while the per-session POWER state and
normal count remain; that count/ref mismatch and its lifetime are unresolved.
Failures inside `npu_session_close()` after hardware shutdown success are not
proven safe either.

One additional pinned callback boundary remains outside this patch: in
`npu_device_shutdown()` (`npu-device.c:813–840`), errors from
`__npu_device_early_close()` and `proto_drv_close()` are logged, then `ret` is
overwritten by later calls. `proto_drv_close()` can return `-EBADR` when its
state is not transitable (`npu-protodrv.c:2932–2935`). The harness's callback
sequence uses success shims for those dependencies, so it does not test this
error path. This is an existing source limitation, not the blocking regression
identified above.

No device access, SSH/ADB, firmware action, service or inference run, kernel
build, module load, or push was performed. Nothing in this review is NPU
BOOTUP, hardware, runtime, or deployment acceptance.
