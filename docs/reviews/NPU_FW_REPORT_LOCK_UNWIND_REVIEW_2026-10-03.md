# NPU firmware report lock-unwind review

## Verdict

The four-site NULL-buffer unlock patch is source-correct for the reviewed
returns, and the frozen follow-up closes the identified host-test gap. No
blocking source or required-test finding remains for this narrow host-source
review. This is not kernel build, firmware, runtime, device, or BOOTUP
acceptance; the earlier standalone NPU message-ID review remains recorded
separately.

## Reviewed inputs

- Published base: `5bbb4e90914dd253518e273b37ee43773610b449`.
- Lock-unwind author commit: `3c3b4ee00ca7734383d95d67aa84dcee21e98e01`.
- Test-only follow-up: `bedc49af01a82a8e27f3268e8a7b86670d544300`.
- Independent review worktree after that cherry-pick: `9754bd7e71e9802f28403eafb5f417f773d7606b`.
- Production patch SHA-256: `71c2fa44f0fe42bd94ee416fb09453185dd418b408ba15937ec3c787a5e9bf5d` (unchanged by the test-only follow-up).
- Reviewed harness SHA-256: `7f2d56dfb6aef1241b1a0cfa366949b7d65d2488b6c697ec7a43d781d52d3d47`.
- Reviewed test SHA-256: `3183e5a5ce47bb2d08726ef95735435d55a757f9df12f2ef9d23021c5922e8b1`.
- Local source fixture: clean derived tree `3fca50941422439b2019db2e4a3dc1016b2138a1`; pinned base `4e5c5ad7d950e4de0688b5663965f2075654b2ad`.

The patch changes exactly the four reviewed NULL-buffer returns in pinned
`npu-log.c`: `npu_fw_report_store()` restores `fw_report_lock`,
`npu_fw_profile_store()` restores `fw_profile_lock`, and both note functions
restore `fw_report_lock` before returning their existing `-ENOMEM`. The lock
and saved IRQ flags match each acquisition. No success path or API changes.
The pinned source hashes used by the existing bounded fixture loader are
recorded in the accompanying research note; ordinary `git apply --check` and
`git apply` were exercised for this patch and the existing message-ID patch.

## Independent test results

I cherry-picked only the frozen follow-up’s harness, test, and research-note
changes. Its production-patch file hash remains the same as above. The added
high-ID fixture now drives the extracted actual path with a nonempty synthetic
report-ring segment:
`msgid_get_pt_type()` → `fw_will_note()` → `fw_rprt_gather()` → actual
`npu_fw_report_store()` body with NULL storage. It asserts one store entry,
mailbox read-pointer advancement, interface-mutex release, both report-lock
exits, and preservation of incoming IRQ state. Each of the four NULL returns
is also exercised with both initially enabled and disabled IRQ state. The
baseline composition records the leaked lock and attempted recursive acquire
before reaching its pinned `BUG_ON`; the patched composition returns `-1`
without the lock leak.

All six independent Python/source-route runs passed:

| Source route | Python mode | Result |
| --- | --- | --- |
| Public pinned source, SHA-verified loader | normal | PASS |
| Public pinned source, SHA-verified loader | `-O` | PASS |
| Public pinned source, SHA-verified loader | `PYTHONOPTIMIZE=1`, no `-I` | PASS |
| Clean local derived fixture | normal | PASS |
| Clean local derived fixture | `-O` | PASS |
| Clean local derived fixture | `PYTHONOPTIMIZE=1`, no `-I` | PASS |

For the environment-optimized invocations, `sys.flags.optimize` was verified
as `1`. Each run compiled and executed baseline and patched extracted C at
`-O0` and `-O2`; thus all 24 C compile/run combinations passed. The local tree
was clean at the pinned derived commit. The public source route succeeded in
all three runs; an earlier author note that public fetch had not been retried
after a DNS failure is stale for this review.

I also inspected CI allowlist commit
`e46e3296c45b24392fdb100afb0c98342877a1f2`: it adds the test once to each of
the fixed runner tuples and the expected-path tuple, with no dynamic discovery
or guard changes. The coordinator reported the policy tests passing in normal
and optimized modes (7 each); I did not independently run the complete host
regression suite.

## Limits and remaining risks

The test uses host spinlock, IRQ, mutex, mailbox-ring, and logging shims, not
kernel lock primitives or lockdep. On the baseline composed path, the shim
records the recursive spinlock acquisition and continues deterministically to
the `BUG_ON`; a real raw-spinlock path may hang there. The test is not evidence
of real kernel scheduling or IRQ semantics.

`fw_rprt_gather()` still ignores the store error and advances its report
read-pointer, so report bytes can be discarded when storage is absent. The
patch fixes lock/IRQ unwinding only. Pre-lock size/write-position reads,
buffer underflow/bounds, firmware-ring validation, callback/error propagation,
teardown/concurrent access, ABA, and publication/liveness remain outside this
review. No NPU kernel/module build, firmware operation, phone, SSH, ADB, device,
or BOOTUP action was performed or authorized.
