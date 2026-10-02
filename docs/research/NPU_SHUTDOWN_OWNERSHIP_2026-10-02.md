# S22 NPU shutdown/recovery ownership repair

Status: host-tested source candidate pending independent review. NPU BOOTUP
remains refused. This work does not establish kernel-build, module-load, device,
runtime, or deployment acceptance. Independent Luna re-review and full-stack
reconciliation are required before promotion.

## Scope and source

The owned change is limited to `npu-device.h`, `npu-device.c`,
`npu-hw-device.c`, and `npu-vertex.c`, carried in
`tools/hardware/npu-shutdown-ownership-fix.patch`. It is tested on the pinned
driver source at base `4e5c5ad7d950e4de0688b5663965f2075654b2ad` and exact clean
derived fixture `3fca50941422439b2019db2e4a3dc1016b2138a1`. The harness verifies
all eight required source-file hashes, compares the NPU source against the
pinned base, and bounds public downloads to 128 KiB per file / 1 MiB total.

The patch is host-tested alone on temporary pinned source and after the existing
refcount-transaction and default-PM-callback patches, using plain `git apply`.
It does not edit those patches or source worktrees.

## Ownership trace and changed behavior

- `npu_device_recovery_close()` previously summed shutdown returns and then
  `BUG_ON`ed. It now runs suspend, hardware-ref shutdown, log close, and debug
  close in order; on the first error it returns that error, sets both emergency
  and sticky shutdown-uncertain state, leaves `NPU_DEVICE_STATE_OPEN` intact,
  and skips later stages. A second recovery attempt returns `-EALREADY` without
  retrying teardown.
- `npu_hwdev_recovery_shutdown()` refuses any non-DNC hardware device in
  `NPU_HWDEV_STATUS_ERROR` before issuing inverse ref callbacks, whether its
  counters are zero or nonzero. Zero init refs are skipped on later drain
  passes: the power-on status can remain set after a leaf's init refs have been
  released while another leaf still owns refs. An active device with zero boot
  refs is refused without a put. DNC remains excluded. Other put errors are
  returned immediately; the caller latches uncertainty, preventing retries.
- `npu_vertex_open()` checks the sticky latch both before locking and again
  under `vertex->lock`, before acquiring the open ref. The emergency path now
  checks the acquired operation ref's put result, releases that ref before
  recovery (the pinned order), and stops on either failure. Recovery errors no
  longer lead to session creation or a later cleanup/free. `check_emergency()`,
  `npu_device_open()`, and `npu_device_bootup()` treat the sticky latch as
  authoritative, so a cleared emergency bit cannot reopen or resume the device.
- Secure bootup/down and normal bootup/down recheck emergency/uncertain state
  after acquiring `vertex->lock`, before changing counts, calling hardware, or
  freeing secure memory. Both bootup wait loops repeat that check immediately
  after each mutex reacquisition. A late-latch refusal unlocks and returns
  without further callback, ref, counter, or session-memory work.
- `__npu_vertex_bootup()` similarly propagates the operation boot-ref put and
  recovery errors. The ref put remains before hardware recovery: exact extracted
  `__vref_put()` / `__vref_shutdown()` / `npu_device_shutdown()` execute early
  close, protocol close, DHCP deinit, and system suspend before hardware refs
  are shut down. Failure skips later session/open-ref cleanup. The regression
  checks this event order; it does not emulate MMIO or the underlying protocol.
- In `npu_vertex_close()`, an uncertainty check is repeated after locking. The
  pinned order is preserved: hardware-session unregistration / power notify,
  final boot-ref put, hardware shutdown, then `npu_session_close()` and open-ref
  put. A boot-ref or hardware-shutdown error latches uncertainty and returns
  while holding neither the vertex mutex nor an unsafe later free. Secure
  memory is not freed before successful hardware shutdown. `npu_session_close()`
  reaches `npu_session_undo_open()`, which unregisters the session ID and frees
  the session, so that call is deliberately skipped on uncertain shutdown.
- Secure bootdown now returns shutdown errors before clearing secure counters,
  freeing the session secure-memory descriptor, or clearing per-session POWER
  state. Secure bootup cleanup propagates a failed inverse shutdown and retains
  its buffer/session state under the latch. A failed resume is not followed by
  an inverse callback when no matching ref was committed. Normal bootdown
  propagates shutdown errors and does not decrement its session count on error;
  the wrapper changes POWER state only after success.

## Retention and remaining uncertainty

On a close-time hardware-shutdown failure, `npu_sessionmgr_unregHW()` and the
power notification may already have run. The session object and its manager ID
remain because `npu_session_close()` is skipped, and the open ref is retained;
the session is no longer counted as hardware-active. A VFS `.release` return
does not itself provide a retryable file handle. Lifetime through device/module
removal, session-manager teardown, and any outstanding work is not proven here.

On normal bootdown failure, the pinned protocol/device boot ref has already
been put before `npu_hwdev_shutdown()`. The per-session POWER bit and normal
count remain, while the boot-ref count may no longer match them. The sticky
latch blocks another teardown attempt, but full manager/remove behavior and
the lifetime of this inconsistent retained session remain unresolved. Failures
inside `npu_session_close()` after hardware shutdown success are also outside
this repair's proven retention path.

The historical `npu-session-lifecycle-fix.patch` overlaps `normal_bootup()` and
`npu_vertex_close()`; this candidate has not been reconciled or proven
compatible with it. A separately reviewed reconciliation is required before
any build or deployment. Probe/clock ownership, device removal, and the broader
module/session-manager lifetime are out of scope.

## Evidence

`tools/hardware/test-npu-shutdown-ownership.py` runs actual extracted driver C
with host shims (not a kernel build). A controlled lock hook latches sticky
uncertainty after simulated acquisition and exercises all four boot entrypoints
plus both bootup wait-loop reacquisitions. The pre-fix exact bodies reproduce
all six bypasses; on wait reacquisition the hook also simulates the waited count
reaching zero, which otherwise lets bootup proceed to the hardware callback.
Patched cases verify balanced shim locks and no subsequent modeled hardware,
protocol, ref, counter, or secure-memory work apart from those explicit hook
changes. They are not Linux concurrency or memory-ordering evidence.
Baseline reproductions cover the recovery
panic, caller panic, ERROR-state inverse puts (zero and nonzero counters),
close-before-shutdown free, normal false success, and secure cleanup/bootdown
false success. Patched tests cover ordinary recovery/close/secure-boot success,
first-error propagation, sticky no-retry poison, close retention and balanced
locks, failed secure resume with no inverse put, secure-memory retention,
wrapper state, and unequal multi-leaf init/boot counts (1 versus 2) with DNC
excluded. The protocol-before-hardware order is executed using the exact
ref-put, final boot callback, and device-shutdown bodies plus event-recording
shims.

Verification completed: C `-O0` and `-O2`; Python normal, `python3 -O`, and
`PYTHONOPTIMIZE=1`; bounded public pinned fixture (147,641 bytes) and local
exact-derived fixture. All harness runs passed, including standalone and
combined plain-`git apply` checks. Evidence remains host-only; no kernel build,
module load, device access, BOOP/ADB/SSH, or push was performed.
