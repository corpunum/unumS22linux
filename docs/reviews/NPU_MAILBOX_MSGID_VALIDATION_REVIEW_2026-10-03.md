# Independent NPU mailbox message-ID validation review

## Verdict

The bounded message-ID and ownership changes are supported by the pinned-source
host tests below, but NPU11 is **BLOCKED as a standalone patch**. For a
nonnegative invalid ID (`msg_id >= 64`), the new validator still calls
`fw_will_note(FW_LOGSIZE)` and ignores its result. In the pinned implementation,
if `fw_report.st_buf` is NULL, `fw_will_note()` returns `-ENOMEM` while still
holding `fw_report_lock` and with the IRQ state saved by `spin_lock_irqsave()`
not restored. The changed validator then returns normally. This is a concrete
lock/IRQ-state leak on that path, even though this review did not establish that
the NULL-buffer state and such a mailbox ID coincide at runtime. Do not accept
NPU11 alone until the separately authored diagnostic-lock unwind change and
its actual-C regression are frozen and independently reviewed.

This verdict is not a finding that the bounds or ownership fix is wrong. It is
also not kernel, firmware, mailbox-liveness, concurrency, device, or deployment
acceptance.

## Reviewed identities and provenance

The reviewed integration source is `2051427c20653efc024002a8486d10f20a46ba50`
(tree `66de98329a3f0932a3288d4c29e69970acc5eaee`). The author commit is
`021bb0c8c527005dc75280fda1f1803f91427bc6`; it is not an ancestor of the
integration commit, but the four NPU11 files in those two commits are byte
identical. The pinned production source is base
`4e5c5ad7d950e4de0688b5663965f2075654b2ad`, with clean derived fixture
`3fca50941422439b2019db2e4a3dc1016b2138a1` (tree
`5aad5cf1dbaa0f430377737141f0547e971b0a2d`). The source repository was
`camera-kernel-build-20260927`; its derived fixture was clean at review time.
The edited validator source hash is
`271dfe4f0b591a9a6d5a3f996e5fd2fe7a05d9b839513ce8691863bae79d3e2e`.

The integration-tree NPU11 file hashes are:

| File | SHA-256 |
| --- | --- |
| `docs/research/NPU_MAILBOX_MSGID_VALIDATION_2026-10-02.md` | `e975153aff5a8b44c52b2ad3c580d818752e89f430f79db37e35f028a92ec6a6` |
| `tools/hardware/npu-mailbox-msgid-validation-harness.c` | `9e544ed923de29ceb0f1dd4ccd9004acfad2dcfb85a5c17fed187b036db4bb1a` |
| `tools/hardware/npu-mailbox-msgid-validation.patch` | `c8366edfab42090ac09a6c366ad3c535a62e13384dd494ed685bbfe524a64d14` |
| `tools/hardware/test-npu-mailbox-msgid-validation.py` | `ee13b0275071f864402e3347e6230ec306abeb643e4a96d7cf428391ea86ecdf` |

The patch changes only `drivers/vision/npu/core/npu-util-msgidgen.c`. It changes
the validator to return invalid for IDs below zero or at least 64; callers now
avoid indexing in `msgid_claim`, `msgid_claim_get_ref`, and
`msgid_get_pt_type`. For a type-mismatched claim, it returns NULL before reading
the request reference or clearing the occupied bit. `msgid_get_pt_type()` uses
its existing `-1` unknown-type sentinel. `BUG_ON` checks for a NULL pool and
bad pool magic remain.

The fixed result path was traced in the pinned code: `get_msgid_type()` calls
`msgid_get_pt_type()`; `nw_rslt_manager()` and `fr_rslt_manager()` peek the
firmware message, invoke that type callback, and reject a nonmatching result
before `mbx_ipc_get_msg()` dequeues it. The NW and frame adapters call
`msgid_claim_get_ref()` with their respective expected types and only update a
request when the returned target is non-NULL. The test extracts and executes
these production function bodies, not text-only substitutes.

## Diagnostic blocker details

In the same pinned source, `drivers/vision/npu/core/npu-log.c` initializes
`fw_report.st_buf` to NULL (line 70). `fw_will_note()` takes `fw_report_lock`
at line 1763, returns at line 1766 when the pointer is NULL, and reaches its
normal unlock only at line 1802. The NPU11 validator calls this function for
nonnegative out-of-range IDs and ignores its return before returning invalid.
Thus, if that branch sees a NULL report buffer, it returns to the caller with
the lock held and IRQ state not restored; the old code subsequently hit
`BUG_ON(1)` for this high-ID condition, while NPU11 now returns without that
fatal stop. The host harness replaces `fw_will_note()` with a counter macro and
therefore cannot test this lock behavior.

The NULL state is source-defined, not hypothetical: it is the static
initializer; the DRAM-buffer allocation path can return on allocation failure
before calling `npu_fw_report_init()` (`npu-system.c:578-585`); and with
`CONFIG_EXYNOS_NPU_DRAM_FW_LOG_BUF` disabled the allocator is a success-valued
stub (`npu-system.c:619-621`) that does not initialize the report buffer.
`npu_fw_report_deinit()` also sets the pointer and size back to NULL/zero while
holding this lock (`npu-log.c:689-705`). The tree exposes this deinit function,
but this review did not establish an in-tree call site that overlaps a mailbox
result. The precise live route combining a NULL state with a high invalid
mailbox ID therefore remains unproven; this note does not claim observed
runtime deadlock. The source-level early returns under lock are sufficient to
block NPU11-alone approval until the paired fix and test cover them.

The callback immediately before `fw_will_note()` takes `fw_report_lock` is
`fw_rprt_gather()` (`npu-interface.c:1165-1214`). It takes `interface.lock`,
reads mailbox ring pointers/segment metadata, copies report data, and passes
chunks to `npu_fw_report_store()`. That store has the same NULL return without
unlocking `fw_report_lock` (`npu-log.c:1039-1073`), so a nonempty gather can
encounter the lock leak before `fw_will_note()` reaches its own check. I did
not validate the firmware-controlled ring offsets/pointers or establish
whether a given invalid result coincides with report data. Separately,
`npu_fw_report_store()` computes `remain = st_size - wr_pos` before acquiring
the lock (line 1042), so its snapshot can be stale under concurrency and wraps
as `size_t` in the zero-sized NULL state; it returns before using that value in
the NULL case. These adjacent bounds/race questions are not fixed or proved
safe by NPU11. `fw_will_note_to_kernel()` has the analogous direct
NULL-return-without-unlock path (`npu-log.c:1703-1753`), and
`npu_fw_profile_store()` has one under its separate `fw_profile_lock`
(`npu-log.c:1076-1111`). They corroborate a broader unwind concern but are not
additional NPU11 validator call sites.

The exact `npu-log.c`, `npu-system.c`, and `npu-interface.c` files inspected
were byte-identical between the pinned base and clean derived fixture. Their
derived-fixture SHA-256 hashes are respectively
`e98c22dc4d005b350d9c82bf067ada5b01a0059f766f13bce39594cc44172d5c`,
`96eaa6bf1511f3e6414e3e376d62592229454a2ea1687d760b7bb8e5952b1a05`, and
`c2deaa0abd990184b64373bb13983f048b925e0f5f421f6623de7de85f667108`. These
diagnostic files are independently hash-pinned in this review note, not among
the eight-file fixture set checked by the NPU11 harness.

## Test assessment and results

The test pins eight actual source files by SHA-256. Its public route uses the
existing bounded fetcher at the exact base commit (raw GitHub URL, 5-second
timeout, no redirects, 512-KiB per-file cap, then SHA verification). Its local
route requires the clean derived commit and checks the selected files against
the pinned base/derived revisions. The local repository was clean and at the
recorded derived HEAD. The patch is applied to the actual validator bytes using
ordinary `git apply --check --whitespace=error-all` and `git apply`.

I ran the test for both public and clean-local fixtures in all three Python
modes: normal (`sys.flags.optimize == 0`), `python3 -O` (`== 1`), and
`PYTHONOPTIMIZE=1 python3` without `-I` (`== 1`). All six runs passed. Each
compiled and ran baseline and patched extracted production C at C `-O0` and
`-O2`. A parallel public-fetch attempt first timed out; serial bounded retries
then passed all three public modes. No failed fetch was counted as a pass.

Coverage includes `-1`, `INT_MIN`, `64`, and `INT_MAX` through claim, reference
claim, and type lookup; a guard around the pool; invalid-ID nonmutation; valid
IDs 0..63; exhaustion; duplicate and unoccupied claims; wrong-type NW/frame
ownership; valid adapter updates; and invalid/wrong-type firmware result
rejection before dequeue. Baseline C reproduces the prior invalid-ID `BUG_ON`,
wrong-type reference return/release, wrong-type adapter mutation, and manager
failure before dequeue. The shim catches `BUG_ON` with `setjmp`/`longjmp`; this
is not an actual kernel panic. Its sequential atomics, fake mailbox, and
`fw_will_note` counter do not model Linux memory ordering, IRQ/lock state,
concurrent publication, ABA/reuse, firmware, or hardware. The wire `mid` is
`u32` while the callback parameter is `int`; the high-bit narrowing case is
host-compiler behavior here, not an ARM64 build result.

The retained mismatch `WARN_ON()` remains potentially fatal when
`panic_on_warn=1`; the source's warning path reaches `check_panic_on_warn()`.
The patch fails closed on mismatched ownership, but does not establish that all
malformed input is panic-free. Since rejected mailbox entries remain
undequeued, these tests establish no eventual progress or recovery for a
persistently invalid queue head. The pool's occupancy-before-fields
publication ordering is unchanged and concurrency/ABA are out of scope.

## CI wiring

I inspected `db23c885fd8f1825aa77117b387d8cee40bdcd4e` read-only. It adds the
NPU11 test exactly once to the fixed `REVIEWED_HOST_TEST_PATHS`, once to
`HOST_TESTS`, and once to the separately pinned expected path tuple. The diff
does not add test discovery or change runner guard behavior; `HostTest` uses
the existing optimization-safe default. On the review worktree at 2051427, the
existing `test-host-regression-runner.py` policy suite passed 7/7. The root
reported 7/7 policy tests in both normal and `-O` modes after db23c88, and a
56-normal/53-optimized (109 invocation) repository inventory. Those updated
post-db23c88 figures were coordinator-run, not rerun from this worktree.

## Scope boundary

No kernel build, phone, ADB/SSH, firmware, BOOTUP, or device test was performed.
The only approval supported here is for the bounded source/test behavior other
than the blocking `fw_will_note()` NULL-buffer unwind interaction. NPU11 alone
remains **BLOCKED** pending the separately reviewed paired diagnostic fix and
an actual-C regression of its NULL-buffer lock-unwind path.
