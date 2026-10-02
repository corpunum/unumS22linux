# NPU mailbox publication and drain liveness — 2026-10-02

## Result

Added a host-only regression that composes the exact six-patch source profile,
then compiles and executes the actual extracted POWER_CTL waiter, protodrv,
mailbox, and message-ID allocator C. The stalled-callback case confirms the
caller remains inside cancellation/drain while synchronous publication is
held, the canceled stack waiter stays registered under its publisher lease,
and the protocol entry is not transitioned while the callback still reads
its request. Releasing the synthetic callback lets the publisher finish and
the caller drain safely. This is bounded host evidence of the ownership order;
it does not establish that a real mailbox callback will return.

The unresolved liveness defect remains: `npu_power_wait_cancel_and_drain()`
uses an unbounded `wait_for_completion(&waiter->publish_done)` after a
publisher has begun. If synchronous `nw_post_request()` never returns, the
caller cannot leave that drain. This protects the stack waiter from
use-after-return, but can pin the session POWER_NOTIFY path (which holds
`session->global_lock`) and prevent close/teardown from progressing. No timeout
or early-free workaround is proposed: freeing the waiter while a publisher or
callback can still reference it would trade the liveness stall for a
use-after-free. A safe cancellation boundary for a genuinely non-returning
synchronous mailbox call is not established here.

The same path review found a separate, narrow message-ID leak when the mailbox
ops table exists but its `nw_post_request` member is absent. After
`msgid_issue_save_ref()` marks a valid slot occupied and saves `src`, the
missing-member branch logs and returns zero without claiming that slot. The
existing `ret <= 0` cleanup handles callback failures but is bypassed by that
early return. Under `CONFIG_NPU_USE_BOOT_IOCTL`, the optional seventh patch
`tools/hardware/npu-mailbox-missing-callback-reclaim.patch`, which claims the
valid issued ID immediately before the existing return. This repairs only
that missing-callback pool leak; it does not repair the unbounded drain, clear
the allocator's `ref`/`pt_type` fields, or alter other callback races. The
patch is separate from and does not edit the frozen six-patch stack; it should
receive independent review before integration.

The claim is deliberately compiled out without `CONFIG_NPU_USE_BOOT_IOCTL`.
In that legacy branch POWER_DOWN overwrites `msgid` with zero before the
callback check, so claiming there could release another request's slot 0 (or
claim slot 0 after allocator exhaustion). The patch does not repair or change
that legacy behavior. A second translation-unit copy of the actual mailbox C
is compiled with BOOT_IOCTL undefined and exercises both an occupied slot 0
and an exhausted pool, asserting no claim occurs.

## Source path and ownership evidence

The tested path is the exact extracted code, not a Python behavioral model:

1. `npu_session_wait_power_request()` creates a stack waiter, registers it
   under the waiter spinlock, queues a POWER_CTL request with
   `req.session = NULL`, then waits for a response.
2. `nw_mgmt_op_get_request()` assigns the protocol request ID. The POWER_CTL
   case begins a publication lease and authorizes it under the waiter lock,
   releases that lock, then calls `__mbox_nw_ops_put(entry)` synchronously.
3. `npu_nw_mbox_ops_put()` issues a message ID and stores the protocol entry
   reference before calling the optional synchronous `nw_post_request()`.
   A successful callback leaves the ID occupied for its response; a
   nonpositive callback result claims it once.
4. On response timeout, `npu_power_wait_cancel_and_drain()` marks the waiter
   canceled. If publishing is still true, it waits for `publish_done`; the
   publisher calls finish only after the mailbox call returns. The caller
   copies/removes the waiter and returns only after drain.

The harness gates the actual extracted callback at the synchronous call,
allows the actual extracted waiter timeout/cancellation path to run, then
checks before releasing the gate that the caller has not returned, the stack
waiter remains registered and canceled/publishing/committed, and no LSM entry
transition has occurred. It also sends late and duplicate responses and
verifies they do not revive the canceled waiter. After callback release, it
checks the caller returns with timeout, the callback path finishes, and the
LSM/message-ID outcomes match success versus callback failure. Separate race
cases exercise timeout before request-ID assignment, before publish begin, and
between begin and authorize.

The harness uses pthread/list/completion/queue/mailbox shims. It models the
LSM transition as a recorded state change; it does not model kernel slab
deallocation, kernel lock ordering or memory barriers, IRQ context, device
references, mailbox MMIO, or real scheduler behavior. The request entry has
static storage in this host shim. Thus the test demonstrates production C
ordering and waiter retention while the host callback is held, not a kernel
proof that storage cannot be freed under every real worker/teardown race.

The exact `msgid_issue()` C is retained with `CONFIG_DSP_USE_VS4L` enabled,
and the test exercises NPU-half, DSP-half, unclassified-session, and
NULL-session allocation paths. The generated host unit selects
`CONFIG_NPU_USE_BOOT_IOCTL`, `CONFIG_NPU_MAILBOX_VERSION=9`, and
`CONFIG_DSP_USE_VS4L` for the reviewed target configuration. Its four-slot
message-ID pool is deliberately scaled for exhaustion testing; it is not the
production pool size. NPU/DSP ID constants and `MSGID_POOL_MAGIC` are read
from exact SHA-verified headers passed through the source loader.

## Pinned inputs and verification

The pinned base is `4e5c5ad7d950e4de0688b5663965f2075654b2ad`. The local
derived fixture at
`/home/corpunum/s22-workers/camera-kernel-build-20260927` is clean at
`3fca50941422439b2019db2e4a3dc1016b2138a1`; the tested NPU files are verified
against the pinned base. Added-source SHA-256 inputs are:

- `npu-if-protodrv-mbox2.c` —
  `d13247bd90ab27dd58cfe9241ec1a07460ab3b353d86a268f8a6fadefc6ff755`
- `npu-util-msgidgen.c` —
  `271dfe4f0b591a9a6d5a3f996e5fd2fe7a05d9b839513ce8691863bae79d3e2e`
- `npu-util-msgidgen.h` —
  `ff3cd551102edf433e3ef0c372ee2b8ed2cd8caf672ac00853d3ea9a134736a8`

The test verified the existing six patch hashes, applied that complete stack
with ordinary `git apply --check` and `git apply`, then applied the separate
seventh patch in a temporary source tree. The seventh patch changed only
`npu-if-protodrv-mbox2.c`. Baseline and patched extracted C both compile with
`-Wall -Wextra -Werror` at C `-O0` and `-O2`.

Results in each local-source Python mode (`python3`, `python3 -O`, and
`PYTHONOPTIMIZE=1 python3`) included:

- Baseline reproduction: repeated missing-callback submissions exhaust the
  deliberately four-slot pool.
- Patched result: repeated absent-callback submissions reclaim every issued
  slot; successful callback still retains its slot; zero/negative callback
  results reclaim exactly once; exhausted allocation does not post or claim an
  invalid ID.
- Legacy no-BOOT_IOCTL branch: the claim remains excluded; POWER_DOWN's
  overwritten zero does not release slot 0 when another request owns it, and
  allocator exhaustion does not trigger a slot claim. The older legacy
  missing-callback behavior remains otherwise unchanged.
- Actual source C: NPU/DSP/general/NULL allocator branches; queue request's
  NULL session; cancellation ordering; stalled synchronous callback; ignored
  late response; and missing-callback publication completion.
- Preflight remained refused: status 2,
  `artifact_preflight_pass=false`, `bootup_ready=false`, and
  `bootup_authorized=false`; local AIE and DSP relocation firmware fixtures
  were absent.

The default public SHA-loader route also passed with both local source-tree
variables unset. It identified the pinned base and reported the bounded
147,641-byte shutdown-loader subset; the source union was 1,234,384 bytes.

Local-source commands:

```sh
S22_NPU_PROBE_SOURCE_TREE=/home/corpunum/s22-workers/camera-kernel-build-20260927 \
S22_NPU_SHUTDOWN_SOURCE_TREE=/home/corpunum/s22-workers/camera-kernel-build-20260927 \
python3 tools/hardware/test-npu-publication-liveness-c.py

S22_NPU_PROBE_SOURCE_TREE=/home/corpunum/s22-workers/camera-kernel-build-20260927 \
S22_NPU_SHUTDOWN_SOURCE_TREE=/home/corpunum/s22-workers/camera-kernel-build-20260927 \
python3 -O tools/hardware/test-npu-publication-liveness-c.py

S22_NPU_PROBE_SOURCE_TREE=/home/corpunum/s22-workers/camera-kernel-build-20260927 \
S22_NPU_SHUTDOWN_SOURCE_TREE=/home/corpunum/s22-workers/camera-kernel-build-20260927 \
PYTHONOPTIMIZE=1 python3 tools/hardware/test-npu-publication-liveness-c.py

env -u S22_NPU_PROBE_SOURCE_TREE -u S22_NPU_SHUTDOWN_SOURCE_TREE \
python3 tools/hardware/test-npu-publication-liveness-c.py
```

Patch SHA-256: `f109b57381b3f2afcf2638b518f50db59ef8c9f784debd949488c74f8ea5c39b`;
test SHA-256: `485b6150fd9be7f36a3a20632724fb77f23e8351d50286a92fdac1af59879831`.
The test does not create or publish kernel fixture blobs. This is host source
and extracted-C evidence only: no kernel build, module, service, inference,
firmware, phone, SSH/ADB, deployment, reboot, or device acceptance was run.
The six-patch profile's BOOTUP refusal remains unchanged.
