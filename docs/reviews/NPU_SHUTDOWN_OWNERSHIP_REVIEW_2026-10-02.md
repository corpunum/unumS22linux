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

## Follow-up review — `f2d9a09da6aed6fc39013b637fc65ed4ca850d22`

**The post-lock quarantine bypass identified at `3c6eaff` is cleared in the
two tested application profiles: standalone on the pinned source, and atop
the refcount-transaction + PM-callback patches.** I found no new source-logic
blocker in the four boot-control lock-entry checks and two wait-loop
reacquisition checks reviewed here. The exact five-patch integration profile
has a separate application blocker noted below. This is not deployment
clearance and does not permit NPU BOOTUP.

The frozen follow-up commit was archived to
`/tmp/s22-npu-shutdown-followup-review-20261002.WjvGrj`. The ownership patch
SHA-256 is
`a8af77122b4049fd38e21adfd01a8577d3e8f9bef1f68d0cfa091a5884a4d9f3`; the
updated harness SHA-256 is
`e556600c1cf087b82d3bd9a067161d752a82a734bce57ec8304e535dacdba570`. Against
the pinned LineageOS source and the clean derived source fixture recorded
above, the combined source places `check_emergency_vctx()` after lock
acquisition in secure bootup (`npu-vertex.c:1056`), secure bootdown (`1172`),
normal bootup (`1228`), and normal bootdown (`1336`). Secure bootup's wait-loop
reacquisition checks at `1069`, and normal bootup's checks at `1241`; each
error path unlocks before returning.

The revised harness extracts the actual `npu_hwdev_normal_bootup()` body as
well as the other three functions. Its lock hook deterministically latches
shutdown uncertainty immediately after the selected lock acquisition. For
the two wait-loop cases, that hook also simulates the relevant opposing count
reaching zero, so execution reaches the reacquisition check. On the unpatched
pinned bodies, all six exact bypasses reproduce: four post-entry-lock paths
and both wait reacquisitions. With the follow-up patch, each path refuses
further work, releases every acquired shim lock, and records no modeled
hardware, reference, session, power, allocation/free, or device/session-state
change beyond the explicit uncertainty/count changes made by the test hook.
Normal and secure ownership/count state, secure-buffer ownership, and the
normal boot ref are checked on the applicable cases.

The local-source harness passed under `python3`, `python3 -O`, and
`PYTHONOPTIMIZE=1 python3`; each run compiled baseline and patched extracted C
at host C `-O0` and `-O2`. The public pinned-source fixture also passed and
reported 147,641 bounded bytes. The harness applies the ownership patch alone
and the refcount-transaction + PM-callback + ownership stack with
`git apply --check` followed by plain `git apply`; I independently repeated
those applications against an isolated archive of the clean derived source.
These are deterministic extracted-C host-shim/control-flow results. The
injection is not actual concurrent scheduling and says nothing about Linux
memory ordering, lockdep, or kernel runtime behavior.

A separate coordinator cross-check of the exact five-patch profile reports
that the frozen ownership patch fails `git apply --check` at
`drivers/vision/npu/core/npu-vertex.c:313`, after the preceding four patches.
The coordinator reports byte-equality checks on the merged loader sources and
the ownership patch against the frozen Git object. I did not independently
rerun that five-patch profile; this is an integration-context blocker, not a
new regression in the six corrected paths above. No fuzzing, forced
application, or hunk omission was used. Accordingly, the source-candidate/WIP
clearance here is limited to the standalone and refcount-transaction +
PM-callback profiles; the five-patch stack remains blocked and unreconciled.

All prior limitations remain in force and exclude deployment: the historical
session-lifecycle patch and full integration stack remain unreconciled;
retained object lifetime across VFS `.release`, session-manager teardown, and
device/module removal is unproven; normal bootdown can leave the ref/count
mismatch after a hardware-shutdown error; failures inside
`npu_session_close()` are not covered; and `npu_device_shutdown()` still masks
the early-close/protocol-close callback errors described above. No full Linux,
lockdep, firmware, or hardware validation was done. Therefore this follow-up
supports only a bounded source-candidate/WIP publication claim—not a working
NPU, safe deployment, runtime acceptance, or permission to attempt NPU
BOOTUP.
