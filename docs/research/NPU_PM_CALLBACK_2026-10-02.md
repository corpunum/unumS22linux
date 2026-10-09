# NPU default-boot PM/clock callback follow-up — 2026-10-02

## Result and boundary

This limited source candidate repairs return-value and status publication in
`npu_hwdev_default_boot()`. PM resume now uses the pinned
`pm_runtime_resume_and_get()` helper, so negative resume errors are balanced
and stop the callback before clocks, while nonnegative resume success is
normalized to zero. A clock-enable failure retains its primary error, relies
only on the pinned NPU clock helper's own rollback, and releases only the PM
reference acquired by this callback. Successful PM put values are normalized
to zero. A negative PM put remains an error and does not publish the device as
off.

Any failure sets the existing `NPU_HWDEV_STATUS_ERROR` value. That status
means the power/clock state may be partial or unknown; it does not prove the
device is off. A failed matching PM put still decrements the PM usage count
under the pinned API contract, but the failed suspend/idle result leaves
hardware state uncertain. After clock-enable failure, a negative PM cleanup
result is logged once; the original clock error remains the callback result.
This patch does not retry the release, claim failure atomicity, or assert that
the retained/partially transitioned hardware is safe to use.

NPU BOOTUP remains refused. Existing host preflight, authorization, and
runtime gates were not changed. No kernel build, module load, firmware work,
phone access, BOOTUP, deployment, or device acceptance was performed.

## Source contracts and patch

The target is upstream LineageOS kernel commit
`4e5c5ad7d950e4de0688b5663965f2075654b2ad`. Its relevant NPU files are
unchanged in the clean derived source commit
`3fca50941422439b2019db2e4a3dc1016b2138a1`.

The pinned `pm_runtime_resume_and_get()` calls `__pm_runtime_resume()` and
returns zero for any nonnegative result. If resume fails, it calls
`pm_runtime_put_noidle()` to balance the usage count before returning the
negative error. By contrast, `pm_runtime_get_sync()` leaves its usage count
incremented even on failure. The pinned `pm_runtime_put_sync()` decrements the
usage count even when it returns an error, so the callback must preserve that
error and must not retry the same put.

The pinned `npu_clk_prepare_enable()` rolls back earlier clocks in that call
when a later clock fails. `clk_prepare_enable()` also unprepares the current
clock if its enable fails. Therefore the callback releases its one successful
PM get after a clock-enable failure only after this known clock rollback. It
does not invent a generic inverse for the partially transitioned leaf device
or unwind parent ownership here.

The callback patch also sets `ret = -EINVAL` in the exact null-clock-entry
branch of `npu_clk_prepare_enable()`. This assignment is inside the branch,
not just an initializer: after an earlier clock succeeds, a later null entry
must not return the stale zero after rolling that earlier clock back. An empty
clock list still returns success as before. With a null first entry, the old
function's returned `ret` was uninitialized; the baseline does not assert a
particular value for that undefined case, while patched exact-C tests verify a
deterministic `-EINVAL`.

The host regression fetches six public files at the immutable pinned commit,
caps each response at 128 KiB (768 KiB maximum total), uses a five-second
request timeout per file, rejects redirects, and checks SHA-256 before
extracting functions or applying patches:

| File | SHA-256 |
| --- | --- |
| `drivers/vision/npu/core/npu-hw-device.c` | `14617a6f8e5b08e1bb169618daa8544f2680ad6709cb9f3b9730919d4dc8e16f` |
| `drivers/vision/npu/core/npu-clock.c` | `0e3d87104de1667bf8e3c197d2a85a0d61ba725b7290c3ed9ffaff58630672c5` |
| `drivers/vision/npu/core/npu-hw-device.h` | `43165437c7b6a4c50599c2677536376ab31579de0f5866c8b76e33ff7813e9c3` |
| `include/linux/pm_runtime.h` | `8a5982620fd46a59346c9568f9fcf57790509d81421b610000dd09a25c0daac1` |
| `include/linux/clk.h` | `8946bdd3c492f0204d3b6b051e4a9f3690a0f0741894758d3081e8f92cd332ea` |
| `drivers/vision/npu/core/npu-vertex.c` | `0e130ccedaebab85b2d6e78453a049610abed431c04ab630e4426a0f7aca077a` |

An optional `S22_NPU_PM_CALLBACK_SOURCE_TREE` supports offline testing. It is
accepted only at the derived commit above with a clean tree, identical fixture
files relative to `4e5c5ad`, and matching hashes. If public source retrieval is
unavailable, the test explicitly prints `SKIP actual-C PM callback test` and
exits 77; this is not a passing actual-C test. A hash or patch mismatch fails.

The test checks `git apply --check` for this callback patch alone and for the
combined source stack with `npu-refcount-transaction-fix.patch` applied first.
It executes the callback against the separately patch-applied exact source
bodies. The combined stack check proves application compatibility, not a
combined end-to-end runtime or a kernel build.

## Host regression evidence

`tools/hardware/test-npu-default-boot-callback.py` extracts the exact pinned C
bodies for the PM helpers, common-clock helpers, NPU clock helper, and
`npu_hwdev_default_boot()`. It compiles baseline and patched C harnesses with
host PM/clock shims at `-O0` and `-O2`. Baseline execution emits expected
`BASELINE_FAIL` reproductions for:

- negative PM resume masked by later successful clock setup;
- positive PM resume success returned as nonzero;
- clock-enable error followed by ACTIVE status publication and retained PM
  usage;
- positive PM put success returned as nonzero;
- negative PM put followed by false PWR_CLK_OFF publication;
- a later null clock entry returning stale zero after rolling back an earlier
  successful clock, causing false ACTIVE publication and retaining the PM
  reference.

The patched exact C passes checks for:

- negative PM resume balancing its usage increment, stopping before clocks,
  and publishing ERROR rather than ACTIVE;
- positive PM resume normalization and successful ACTIVE publication;
- a later clock-enable error causing the pinned clock rollback, exactly one
  matching PM put on both successful and failed cleanup, preservation of the
  primary clock error and ERROR status, and one recorded negative cleanup
  result without retry;
- clock-only failure making no PM inverse call;
- successful mixed PM/clock startup and positive-success powerdown pairing;
- negative PM put preserving its error, honoring decrement-on-error, and
  publishing ERROR instead of claiming off, with no second put.
- first-null and later-null clock entries returning `-EINVAL`, avoiding ACTIVE
  publication and balancing exactly one PM acquisition; the later-null case
  also rolls back the earlier clock;
- an empty clock list remaining a successful no-op.

The harness models helper dependencies, PM usage counts, and clocks with host
shims. It is an exact-C regression test, not Linux runtime-PM, clock framework,
lockdep, kernel-build, firmware, or physical-device evidence. Callback ERROR
poisoning through the separately tested ref transaction layer and caller
recovery policy are not re-proven as an integrated system by this test.

The public pinned-source run passed in normal Python mode. The verified local
derived-source run passed in normal mode, `python -O`, and
`PYTHONOPTIMIZE=1`; each run compiled and executed both C optimization levels
and checked the callback patch alone plus the combined source patch stack.

## Remaining gate

The earlier transaction work intentionally retains parent ownership when a
child boot callback fails because the leaf path is not generally failure
atomic. This callback candidate does not change that policy. Callers that
ignore shutdown errors, recovery paths that BUG on recovery-close errors, and
source-level PM/clock state truth beyond the host-tested branches remain
outside this patch. Their review is required before any integration or runtime
claim. The unchanged preflight continues to refuse BOOTUP; no action in this
worktree authorizes hardware execution.
