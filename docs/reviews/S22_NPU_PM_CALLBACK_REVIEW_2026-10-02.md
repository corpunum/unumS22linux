# S22 NPU PM callback host review — 2026-10-02

## Result and boundary

Independent review of the callback candidate at `4f9a2a1ae9dd9aa0f62dac073cf93aa489f930a8` (including the original callback commit `e118b3ac24b77144b35cb9978cb37914df98b3d5` and the exact null-clock follow-up). The PM/clock return-value, status-publication, and null-entry changes are supported by host execution of the exact pinned C bodies and compile at `-O0` and `-O2`. I found no new issue in those covered branches after the follow-up. The callback and its explicit host-test allowlist entry are reviewed as a source-only candidate.

This is not NPU BOOTUP, deployment, kernel-build, or device clearance. A separate pinned-source probe failure path can leave indeterminate clock pointers while continuing to register the hardware device; that remains a material BOOTUP/deployment blocker. Shutdown-error and recovery-policy gaps also remain. No phone, SSH, ADB, services, deployment, reboot, kernel build, firmware, module load, or BOOTUP action was performed.

## Source and patch reviewed

The target kernel source is pinned at `4e5c5ad7d950e4de0688b5663965f2075654b2ad`; the inspected clean derived worktree is `3fca50941422439b2019db2e4a3dc1016b2138a1`. The host test verified these six fixture hashes against that tree:

| Pinned file | SHA-256 |
| --- | --- |
| `drivers/vision/npu/core/npu-hw-device.c` | `14617a6f8e5b08e1bb169618daa8544f2680ad6709cb9f3b9730919d4dc8e16f` |
| `drivers/vision/npu/core/npu-clock.c` | `0e3d87104de1667bf8e3c197d2a85a0d61ba725b7290c3ed9ffaff58630672c5` |
| `drivers/vision/npu/core/npu-hw-device.h` | `43165437c7b6a4c50599c2677536376ab31579de0f5866c8b76e33ff7813e9c3` |
| `include/linux/pm_runtime.h` | `8a5982620fd46a59346c9568f9fcf57790509d81421b610000dd09a25c0daac1` |
| `include/linux/clk.h` | `8946bdd3c492f0204d3b6b051e4a9f3690a0f0741894758d3081e8f92cd332ea` |
| `drivers/vision/npu/core/npu-vertex.c` | `0e130ccedaebab85b2d6e78453a049610abed431c04ab630e4426a0f7aca077a` |

The callback patch changes `npu_hwdev_default_boot()` to use `pm_runtime_resume_and_get()`, stop before clock setup on a negative resume result, balance the successful PM acquisition after clock failure, preserve the primary clock error, and publish `NPU_HWDEV_STATUS_ERROR` rather than claiming ON or OFF on failure. Nonnegative PM resume/put results are normalized to success. The null-clock follow-up sets `ret = -EINVAL` inside the actual null-entry branch of `npu_clk_prepare_enable()`; it fixes the later-null stale-zero case after rolling back an earlier clock. The exact helper returns success for an empty list as before.

The pinned PM header confirms that `pm_runtime_resume_and_get()` balances a failed usage-count increment and normalizes nonnegative resume success, while `pm_runtime_put_sync()` decrements even when it returns an error. The pinned NPU clock helper rolls back clocks previously enabled in the same call. The candidate’s cleanup follows those contracts and does not retry a failed PM put.

`NPU_HWDEV_STATUS_ERROR` (`0x4`) already exists in the internal hw-device status enum; the field is a private `u32` and this patch changes no UAPI. The status is logged internally. However, `npu_hwdev_recovery_shutdown()` treats every nonzero status, including ERROR, as a reason to put init/boot references; there is no special ERROR recovery policy here. Recovery can therefore still encounter the separately known refcount/error-path hazards.

## Executed checks

I archived the exact candidate commit to `/tmp/s22-npu-callback-4f9a.YBiuKc` and ran the test against the clean derived source tree with these commands:

```sh
S22_NPU_PM_CALLBACK_SOURCE_TREE=/home/corpunum/s22-workers/camera-kernel-build-20260927 python3 /tmp/s22-npu-callback-4f9a.YBiuKc/tools/hardware/test-npu-default-boot-callback.py
S22_NPU_PM_CALLBACK_SOURCE_TREE=/home/corpunum/s22-workers/camera-kernel-build-20260927 python3 -O /tmp/s22-npu-callback-4f9a.YBiuKc/tools/hardware/test-npu-default-boot-callback.py
S22_NPU_PM_CALLBACK_SOURCE_TREE=/home/corpunum/s22-workers/camera-kernel-build-20260927 PYTHONOPTIMIZE=1 python3 /tmp/s22-npu-callback-4f9a.YBiuKc/tools/hardware/test-npu-default-boot-callback.py
```

All three exited 0. Each run compiled and executed baseline and patched exact C bodies at `-O0` and `-O2`. Baseline emitted the expected failures for masked PM-resume error, positive PM return handling, clock error with false ACTIVE status/retained PM reference, PM-put result handling, and later-null stale-zero status/reference handling. Patched C passed checks for PM-error balancing and short-circuiting, positive-result normalization, clock rollback and PM cleanup (including negative cleanup without retry), first and later null entries, empty clock list, clock-only failure, successful startup/powerdown pairing, and negative PM put/unknown status without retry. The first-null baseline is undefined and deliberately has no asserted result; the patched first-null path deterministically returns `-EINVAL`.

The script’s standalone and combined source-stack checks passed. I additionally ran `git apply --check --whitespace=error-all` for the callback patch against the clean derived source, then applied the NPU ref-transaction patch to a temporary exact-source copy and checked/applied the callback patch in that combined stack with `--whitespace=error-all`; both passed. This is patch compatibility only, not a combined runtime test or kernel build. The callback harness extracts and executes the PM, clock, and leaf callback bodies; it does not execute the combined ref-transaction candidate. It therefore does not re-prove parent locking, poison/error propagation, matching-abort or STM unwind behavior, or caller-loop safety. Those concerns remain covered only by the separate source review in `S22_DRIVER_HOST_REVIEW_2026-10-02.md`, not by this callback harness.

The coordinator’s pending integration diff adds exactly `tools/hardware/test-npu-default-boot-callback.py` to the explicit `REVIEWED_HOST_TEST_PATHS` and `HOST_TESTS` sequences, with the same path added to the runner test’s `EXPECTED_HOST_TEST_PATHS`. It does not add discovery, change CI workflows, or alter optimization exclusions; `HostTest`’s default keeps the new script optimization-safe. I tested the runner-policy script from a temporary overlay containing those dirty runner files and the exact callback test from `4f9a2a1`: 7/7 passed normally and 7/7 passed under `python3 -O -I -B`. I did not run the full host suite on the pending integration tree; its callback source fetch uses only bounded unauthenticated requests to immutable public sources when no local fixture path is supplied, so unavailable network is reported as exit 77 and the runner treats that as failure.

These checks establish host behavior of the tested C bodies and runner policy only. They do not establish Linux runtime-PM, common-clock framework, lockdep, kernel build, firmware/STM/MMIO, or physical-device behavior.

## Remaining blockers and disposition

The pinned `npu_clk_get()` allocates `clocks->clocks` with non-zeroing `devm_kmalloc()` and fills entries incrementally. If `devm_clk_get()` fails, it returns without initializing the failed/current and later pointer slots. `npu_hwdev_probe()` only logs a warning for that failure and continues registering the hw-device. A later default-boot call can therefore encounter indeterminate clock pointers that are not necessarily NULL; the callback patch’s null-entry check and harness do not prove this path safe. This is a pre-existing source issue, not a regression from the candidate, and needs separate probe-error unwind/validation work before any BOOTUP or deployment claim.

Other known caller limits remain: normal and secure bootdown discard shutdown errors; secure-bootup memory-failure cleanup also discards a shutdown error; recovery close can turn a propagated recovery-shutdown error into `BUG_ON`. The callback’s ERROR status represents uncertain state, not proof that hardware is off. The coordinator’s source-only candidate may be integrated with these limitations kept explicit, but NPU BOOTUP and deployment remain refused pending the separate probe/caller-policy work and real hardware acceptance evidence.
