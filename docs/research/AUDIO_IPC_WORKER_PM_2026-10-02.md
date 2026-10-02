# ABOX asynchronous IPC worker runtime-PM error path — 2026-10-02

## Finding and repair

The pinned `abox_process_ipc()` ignored the result of
`pm_runtime_get_sync(dev)`. If resume returned a negative error, the worker
still checked Calliope state, removed queued items, and called
`abox_ipc_send()`. The queue API had already returned local acceptance to the
FE/BE caller; it did not mean the ABOX runtime-resumed or the message was
delivered.

`tools/hardware/audio-ipc-worker-pm.patch` checks the result immediately. On a
negative result it emits a rate-limited `dev_err_ratelimited()` message with
only the worker name and errno, balances the get with
`pm_runtime_put_noidle(dev)`, and returns before Calliope gating, dequeue,
sender, last-busy update, or autosuspend put. A nonnegative return, including
the positive “already active” result, follows the existing worker path
unchanged.

The pinned `include/linux/pm_runtime.h` documents that `pm_runtime_get_sync()`
leaves the usage counter incremented even on error. The pinned
`drivers/base/power/runtime.c::__pm_runtime_resume()` confirms it increments
the counter when `RPM_GET_PUT` is set before calling resume. The same PM header
implements `pm_runtime_resume_and_get()` by using `pm_runtime_put_noidle()` on
a negative result; `pm_runtime_put_noidle()` only drops the usage reference,
while `pm_runtime_put_autosuspend()` requests the successful-resume idle path.
Thus the error branch uses the matching balance operation and does not claim
the device was resumed.

## Queue ownership and recovery boundary

The worker returns without consuming any accepted queue entry after PM resume
fails. It does not call `abox_failsafe_report()`: the pinned
`abox_failsafe.c` can `BUG_ON()` for DRAM debug mode, schedule report work, or
enter a silent-reset/power-management path. This PM error is logged without
invoking that unreviewed recovery behavior.

There is no worker retry or `queue_work()` from the error branch. The queue
remains FIFO and the cached trigger state remains the last locally accepted
direction from the preceding queue-linearized fix; the PM error neither
rolls it back nor speculates about firmware state. A later explicit IPC/FE/BE
callback can schedule the existing worker again. If PM then succeeds, the
worker drains the retained entries before the newer entry in FIFO order. Until
such a later request, the async caller cannot receive the worker's PM error:
its earlier success meant only that the local queue accepted the message.
For PCM triggers, this requires a changed direction: a duplicate FE/BE
request still returns before scheduling and cannot restart this retained
queue. The tested FE RESUME changes the preceding accepted BE STOP state.
Generic async IPC can also schedule the worker. This patch does not repair
the duplicate-direction restart gap.

For unrelated negative `abox_ipc_send()` results after successful resume, the
existing dequeue, failsafe-report, mark-busy, and autosuspend-put behavior is
unchanged. The PM fix adds no sender retry, queue ownership transfer, trigger
bit reset, firmware acknowledgement, or ABOX teardown/bind logic.

## Ordered source patching

Apply all three patches in order to the pinned source:

```sh
git apply --check tools/hardware/audio-ipc-observation-fix.patch
git apply tools/hardware/audio-ipc-observation-fix.patch
git apply --check tools/hardware/audio-ipc-error-path-2026-10-02.patch
git apply tools/hardware/audio-ipc-error-path-2026-10-02.patch
git apply --check tools/hardware/audio-ipc-worker-pm.patch
git apply tools/hardware/audio-ipc-worker-pm.patch
```

The source base is `4e5c5ad7d950e4de0688b5663965f2075654b2ad`; the exact
read-only derived tree is `3fca50941422439b2019db2e4a3dc1016b2138a1`. The PM
patch changes only `sound/soc/samsung/abox/abox.c` in the source tree. The
other two patches remain separate and immutable.

## Host verification

`tools/hardware/test-audio-ipc-worker-pm.py` reuses the existing capped,
SHA-256-pinned fixture loader. It adds the PM header, runtime core, device
logger, and ABOX failsafe source to the four pinned ABOX files and two
previously pinned ASoC caller files. Every source is capped at 256 KiB with
the loader's bounded fetch timeout; an explicit local tree must match the
derived HEAD, pinned commit blobs, and worktree files.

The harness compiles extracted production queue empty/full/put/get functions,
request/scheduler and PCM trigger APIs, FE `.trigger`, BE `.mute_stream`, the
worker send helper, and `abox_process_ipc()`. The PM shim matches the pinned
`get_sync`/`put_noidle` reference semantics; sender and failsafe stubs count
calls and record only trigger direction/error state, never message contents or
device pointers. It compiles at C `-O0` and `-O2`.

Before the PM patch, a negative resume deterministically reproduces the bug:
the extracted worker consumes FE START and BE STOP and calls the sender while
the host PM model remains inactive. After the patch, the same run verifies one
`put_noidle`, an unchanged usage count, no queue dequeue, no Calliope/sender or
failsafe calls, no successful-resume PM tail, a rate-limited errno log, and no
automatic retry. A later explicit FE RESUME with positive `get_sync` result
drains the retained START→STOP→RESUME FIFO and balances through one
autosuspend put. The callback must make exactly one additional `queue_work()`
call before the harness explicitly runs the worker; real Linux workqueue
scheduling remains outside this host shim. Separate success and post-resume sender-error cases verify
that existing send/failsafe behavior and PM reference balancing remain
unchanged.

Run each Python mode with the exact local derived tree and with the capped
public pinned fixtures:

```sh
python3 tools/hardware/test-audio-ipc-worker-pm.py --source-tree /home/corpunum/s22-workers/camera-kernel-build-20260927
python3 -O tools/hardware/test-audio-ipc-worker-pm.py --source-tree /home/corpunum/s22-workers/camera-kernel-build-20260927
PYTHONOPTIMIZE=1 python3 tools/hardware/test-audio-ipc-worker-pm.py --source-tree /home/corpunum/s22-workers/camera-kernel-build-20260927
python3 tools/hardware/test-audio-ipc-worker-pm.py
python3 -O tools/hardware/test-audio-ipc-worker-pm.py
PYTHONOPTIMIZE=1 python3 tools/hardware/test-audio-ipc-worker-pm.py
```

These are extracted-C host/source tests only. No kernel build, phone/SSH/ADB,
firmware operation, runtime trace, DMA measurement, or physical audio
acceptance was performed. Existing `hw_ptr=0` and disabled-RDMA observations
remain a separate unresolved issue; this patch does not identify why the
original stream stalled or prove later device progress.
