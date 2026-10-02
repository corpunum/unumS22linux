# NPU POWER_CTL pre-authorization drain ownership — 2026-10-02

## Result

Patch 10 narrows the stack-waiter drain at one source-proven boundary. If
timeout/cancellation wins the waiter spinlock before publication is authorized,
`npu_power_wait_cancel_and_drain()` now returns even if `publishing` is true.
The existing unbounded `publish_done` wait remains unchanged once
`publish_committed` is true. The patch does not add a timeout, free memory still
owned by a publisher, alter BOOTUP refusal, or claim mailbox/firmware acceptance.

This addresses one avoidable wait: the AST can be paused after `begin_publish`
but before `authorize_publish`, while the session caller holds
`session->global_lock`. Previously the caller waited for `publish_done` even
though authorization was still revocable. Now it marks the waiter canceled,
returns from the pre-authorization drain, unlinks the stack waiter under the
same lock, and releases the session lock. If the paused AST later resumes,
its authorization lookup fails and the POWER_CTL entry returns to FREE without
calling the mailbox publisher.

The change is linearized by `npu_power_waiters_lock`:

- If cancellation acquires it first, `cancelled=true` is visible to
  `npu_session_power_wait_authorize_publish()`, which refuses authorization.
  The caller then unlinks the waiter. Publisher begin/authorize/finish adapters
  carry only the cookie and request ID; they do not retain a waiter pointer.
- If authorization acquires it first, it sets `publish_committed=true`.
  Cancellation follows the old drain path and waits for synchronous publication
  to finish. This path is still unbounded.

The response callback and finish helper each look up by cookie under the same
lock. After the caller unlinks a pre-authorized waiter, a late callback,
authorization attempt, or finish attempt finds no old waiter. The executable
regression places a newer waiter in the registry with a fresh production-style
cookie and deliberately reuses the old request ID; stale old-cookie operations
must leave the newer waiter untouched.

## Exact source and test

`tools/hardware/test-npu-publication-drain-ownership.py` composes the pinned,
SHA-verified first nine patches in order, then applies patch 10 to an ephemeral
source fixture and verifies `npu-session.c` is the only changed source file.
It checks that patch 10 changes only the cancellation predicate and preserves
the unbounded committed-publication completion wait. Its generated C contains
the actual extracted waiter helpers/request function and actual protodrv
POWER_CTL branch. The supplemental C test body is
`tools/hardware/npu-publication-drain-ownership-harness.c`.

The C harness pauses the actual publisher between begin and authorization,
allows the actual waiter timeout path to run, and compares the nine-patch
baseline with patch 10. In both cases it verifies that stale authorization and
late callback/finish operations do not cause a mailbox post or mutate a later
same-request-ID waiter. The session caller and close thread share the
source-audited `global_lock`; this is a bounded host lock shim demonstrating
that the pre-authorization caller can release that lock while the AST is still
paused. It does not compile or simulate all of `npu_session_close()` teardown.

Baseline reproduction: a canceled, publishing-but-uncommitted waiter remains
registered while the caller waits on `publish_done`; the caller keeps
`global_lock`, so the session-close lock acquisition also waits until the AST
resumes and fails authorization. Patch 10 passes the same pause: caller returns
and unlinks, the session-close lock acquisition succeeds while the AST is
still paused, then the old publisher resumes, fails authorization, and finishes
without a callback.

Verification used the clean explicit source fixture
`/home/corpunum/s22-workers/camera-kernel-build-20260927@3fca50941422439b2019db2e4a3dc1016b2138a1`
against pinned base `4e5c5ad7d950e4de0688b5663965f2075654b2ad`. The source
loader enforces its existing per-file and aggregate caps and also supports its
SHA-verified public-fixture fetch route. For each local-source Python mode
below, the baseline and patch-10 C units compiled and ran at both C `-O0` and
`-O2` with
`-Wall -Wextra -Werror`.

```sh
S22_NPU_PROBE_SOURCE_TREE=/home/corpunum/s22-workers/camera-kernel-build-20260927 \
S22_NPU_SHUTDOWN_SOURCE_TREE=/home/corpunum/s22-workers/camera-kernel-build-20260927 \
python3 -I tools/hardware/test-npu-publication-drain-ownership.py

S22_NPU_PROBE_SOURCE_TREE=/home/corpunum/s22-workers/camera-kernel-build-20260927 \
S22_NPU_SHUTDOWN_SOURCE_TREE=/home/corpunum/s22-workers/camera-kernel-build-20260927 \
python3 -I -O tools/hardware/test-npu-publication-drain-ownership.py

S22_NPU_PROBE_SOURCE_TREE=/home/corpunum/s22-workers/camera-kernel-build-20260927 \
S22_NPU_SHUTDOWN_SOURCE_TREE=/home/corpunum/s22-workers/camera-kernel-build-20260927 \
PYTHONOPTIMIZE=1 python3 -B tools/hardware/test-npu-publication-drain-ownership.py
```

The script reports `sys.flags.optimize`, so the `PYTHONOPTIMIZE=1` run is
distinguishable from normal execution. `python -I` is not used for that run,
because isolated Python ignores environment-provided optimization.

The unset-local-path/public-fetch route was attempted separately but could not
fetch its pinned source fixture because the environment returned
`Network is unreachable`; it is not counted as a passing run. The explicit
clean local fixture route above was hash-verified and passed.

## Limits retained

This is not an end-to-end liveness fix. Once authorization commits, the caller
still waits indefinitely if synchronous publication never returns. That can
keep `npu_session_NW_CMD_POWER_NOTIFY()` holding `session->global_lock`, so a
same-session close can remain blocked. Separately, `proto_drv_close()` stops
and joins the AST with `kthread_stop()` before destroying the NW LSM; a wedged
committed publisher can therefore stall driver close as well. The reviewed
source does not establish a firmware/IRQ quiescence fence across close/reopen,
so no heap/refcount redesign or reclamation after a guessed timeout is safe to
claim here.

The test's pthread, list, completion, queue, and close-lock shims do not prove
Linux lock/memory-ordering behavior, kernel worker scheduling, IRQ behavior,
mailbox progress, firmware response, session teardown, or reopen safety. No
kernel build, device, phone, SSH/ADB, BOOTUP, deployment, or hardware acceptance
was performed. BOOTUP refusal and the nine prior patch files/hashes remain
unchanged.

Patch: `tools/hardware/npu-publication-drain-ownership.patch`

Patch SHA-256: `7137b797c9b4658e1ec7e7829054b11ed684e51552933203d5af45b7a9743709`

Test SHA-256: `188163981c10938eb4958e969763b48509e267d90265c68f9a7e3c597d3120af`
