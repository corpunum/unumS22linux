# Independent ABOX IPC runtime-PM review — 2026-10-02

## Result

PASS for the scoped negative-resume worker fix and the tested changed-direction
retry path. The composed source leaves accepted IPCs and cached PCM trigger
state intact on PM failure, balances the failed PM reference, and avoids the
sender and failsafe path. This is host/source evidence only; it does not accept
audio hardware or DMA behavior.

One recovery limit remains: after a failed worker resume, a same-direction
FE/BE duplicate returns before `queue_work()`. A changed-direction trigger or
generic asynchronous IPC can restart the retained queue. The tested FE RESUME
follows queued START→STOP and changes the cached direction. The adjacent
research note records this limit.

## Source and patch review

I started from the clean prepared branch at author commit
`fdf0834f14e79e3bdfa99203a67787f1b9b2770a`. The source tree used by the test
was clean at derived HEAD
`3fca50941422439b2019db2e4a3dc1016b2138a1`, containing pinned base commit
`4e5c5ad7d950e4de0688b5663965f2075654b2ad`.

The PM patch records `pm_runtime_get_sync()`'s return and branches on `ret < 0`
immediately. In the pinned `include/linux/pm_runtime.h`, `get_sync` documents
that it leaves the usage count incremented on errors and calls
`__pm_runtime_resume(..., RPM_GET_PUT)`. Pinned `runtime.c` increments that
count before attempting resume. `pm_runtime_put_noidle()` uses
`atomic_add_unless(..., -1, 0)` to drop the reference without requesting idle
or autosuspend; the pinned `pm_runtime_resume_and_get()` uses this same
negative-result balance. The four PM fixture hashes in the test matched the
derived tree:

| Pinned source | SHA-256 |
| --- | --- |
| `include/linux/pm_runtime.h` | `8a5982620fd46a59346c9568f9fcf57790509d81421b610000dd09a25c0daac1` |
| `drivers/base/power/runtime.c` | `569c8d962a549f86f22af0800c1ba6fa337fddb280b623128fb960b563539d04` |
| `include/linux/dev_printk.h` | `0ddbf1de2356d80047cda83b0bcfcebbfe57dda5410ab5283c86c3ac84ab1695` |
| `sound/soc/samsung/abox/abox_failsafe.c` | `29df1a56899cee66b98c4945d53fef51462833a854ff3de37a83fa907f579f79` |

On the negative branch, the code logs the worker and errno through
`dev_err_ratelimited()`, balances once with `put_noidle()`, and returns before
Calliope gating, queue dequeue, sender invocation, last-busy marking, or
autosuspend. The nonnegative path, including positive “already active” returns,
continues into the original worker body. The rate-limited macro is present in
the pinned logger header and expands through a static rate-limit state and
`__ratelimit()`; ABOX source also uses the same logger family. The host harness
stubs the macro, so it checks the call and message arguments rather than
executing the kernel rate limiter.

The early return also avoids `abox_failsafe_report()`. The pinned failsafe
implementation contains a `BUG_ON()` branch, deferred report work, and a
silent-reset path. The PM failure branch invokes none of them. A post-resume
negative sender result still follows the preexisting dequeue/failsafe and
autosuspend behavior.

The composed test applies observation, queue-linearization, then PM patches.
The queue patch publishes the trigger entry and updates cached direction while
holding the queue lock. Since the PM branch returns before queue access, it
preserves both that FIFO and its last accepted direction. For the changed
direction test, the later FE RESUME is appended behind retained START and STOP;
the updated harness asserts that the callback adds one `queue_work()` call
before it explicitly invokes the host worker. The pinned Makefile includes
`abox.o` and `abox_rdma.o` in the same `snd-soc-samsung-abox.o` composite, so
the new internal request helper needs no exported symbol.

## Independent host verification

The fixture loader checked ten source files against pinned commit blobs and
worktree contents, with a 256 KiB cap per file. The four added PM sources above
join the existing four ABOX fixtures and two ASoC caller fixtures. The optional
public-fetch path is bounded by the loader to five seconds and a capped body;
I did not run that network path.

After reviewing and cherry-picking only follow-up commits `58fb9e6` (explicit
`queue_work()` assertion) and `7943413` (duplicate-direction limit wording), I
ran the exact local `--source-tree` command in normal Python, `python3 -O`, and
`PYTHONOPTIMIZE=1 python3` modes. All three exited 0. In each mode:

- all three `git apply --check` plus apply steps passed in observation → queue
  linearization → PM order;
- C `-O0` and `-O2` baseline builds reproduced only the six expected PM-error
  failures, including dequeue/send while the PM model was inactive;
- patched C `-O0` and `-O2` passed failed-reference balance, retained FIFO and
  trigger state, no negative-path sender/failsafe/autosuspend, the explicit
  callback's `queue_work()` call, START→STOP→RESUME send order after a positive
  PM result, and unchanged post-resume sender-error handling.

The Python checks use explicit `require()` calls rather than `assert`, so
optimized Python modes retain their checks. The C harness extracts pinned
production queue, scheduler, request, FE/BE callback, sender-helper, and worker
functions. Its `queue_work()` shim counts the call but does not model Linux
workqueue dispatch or races; the harness manually invokes the worker afterward.
Likewise, the PM shim models the pinned usage-count behavior rather than
executing kernel PM core code. No full kernel/module build was run.

## Limits

The retained queue retry is conditional for PCM callbacks: a same-direction
FE/BE duplicate is suppressed before the scheduling call and cannot restart
the queue. The passing retry scenario uses a changed-direction FE RESUME. No
sender retry or PM-error return to the earlier asynchronous caller is added;
that caller's success still means local queue acceptance only. Firmware
acknowledgement and FE/BE/DMA progress remain outside this evidence.

No phone, SSH, ADB, firmware, stream, trace-policy, kernel build, or powered
audio/DMA operation was performed. The prior `hw_ptr=0` and disabled-RDMA
observations remain unresolved. No hardware acceptance is claimed.
