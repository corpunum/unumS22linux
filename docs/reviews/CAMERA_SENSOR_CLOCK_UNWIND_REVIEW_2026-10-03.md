# Independent review: v10.1 sensor-clock unwind

Verdict: **Blocked on retry ownership after failed off restoration.** The patch improves first-error propagation and synchronous cleanup, but the real caller has only one `IS_SENSOR_ICLK_ON` bit. After selected-clock restoration fails during off, that bit remains set although this caller no longer owns the selected clock vote. A later off retry can consume another caller's shared clock reference. The runtime-suspend caller also reports success after the off error, and the next resume skips clock setup because the bit is still set.

This is a source and extracted-C finding. The pinned `is_disable()` helper does not report provider disable failures: its only negative return is a missing clock lookup before `clk_disable_unprepare()`, which returns `void`. The negative disable injections below model that helper lookup error; they are not evidence of a clock-provider or physical gate failure. The normal device path and recovery from real hardware state remain unproven.

## Reviewed inputs

- Review worktree: `codex/s22-camera-sensor-clock-review-20261003`, based on `d18459a5591ec64322263fa1cd56aa36d164d153`; imported subject commit `766b84f436859f6952cf4f196716e9d218f918bc` contains only the four frozen author artifacts.
- Frozen author commit: `cfcd4a5d58ab918394ac337492b946126c650b31` (`Add host-tested camera clock unwind patch`). Patch SHA-256: `51138fbf4398faebf70da721a49c98c17fda4ae619aab7fe153f7f19fc288015`.
- Pinned source: `/home/corpunum/s22-linux/lineage/android_kernel_samsung_s5e9925`, commit `4e5c5ad7d950e4de0688b5663965f2075654b2ad`, tree `5c46cbe12dadbcdb64eec4344c9e8ff0f8a75dee`.
- Preserved derived source: `/home/corpunum/s22-workers/camera-kernel-build-20260927`, commit `3fca50941422439b2019db2e4a3dc1016b2138a1`, tree `5aad5cf1dbaa0f430377737141f0547e971b0a2d`.
- The original and derived worktrees were clean and their target, caller, and helper blobs matched. SHA-256 values: `setup-is-sensor.c` `be9ea9769e2b1678cea68f1a86514aa0ccd559bfa9316840a1a83268bfef1182`; `is-device-sensor_v2.c` `b88e203fa7ca7fdcf109f969d516601a9cba4702c98a3a0b338b3a6dbcf55265`; `setup-is.c` `0e3668ca54f6d60abb8a22dc3a18d15b441fcf3c5154b2f0ee61e481d37b88b2`.

## Source behavior

The patch changes the v10.1 ICLK callback path in `drivers/media/platform/exynos/camera/ischain/is-v10_1_0/setup-is-sensor.c` and its three generic ICLK wrappers. The actual caller is `is_sensor_iclk_on/off()` in `drivers/media/platform/exynos/camera/is-device-sensor_v2.c`; the actual helper contract is in `drivers/media/platform/exynos/camera/ischain/is-v10_1_0/setup-is.c`.

| Path | Source-proven behavior in the patch | Review result |
| --- | --- | --- |
| cfg | Rejects channels above 6 before clock calls; enables CSIS0–6 then DMA; on an enable error, releases only earlier successful CSIS acquisitions in reverse order and returns the initiating error. | Correct for this synchronous sequence. A failed cleanup is logged as unknown state, but no per-caller ownership survives for a later retry. |
| on | Actual caller invokes cfg before on. The patch releases the seven cfg gate votes while tracking which releases succeeded, enables the selected gate, and on error best-effort releases this invocation's DMA and still-owned gate votes. Generic wrappers now return the callback errors. | Correct for the tested synchronous ownership sequence. `IS_SENSOR_ICLK_ON` is set only after both callbacks succeed. |
| invalid channel | cfg, on, and off reject `channel > 6` before clock operations. Although the private gate switch has a case for 7, these callbacks do not reach it for channel 7. | No-touch invalid-channel handling is correct. |
| off | Releases the selected gate first, then DMA. A selected-gate lookup error returns before DMA. A DMA lookup error triggers a best-effort selected-gate reacquire and returns the original DMA error; restoration failure is logged as unknown state. | Does not preserve ownership if restoration fails; see blocker below. |

`is_enable()` returns `clk_prepare_enable()` errors. `is_disable()` looks up the `struct clk *`, returns `-EINVAL` only if that pointer is absent, then calls void `clk_disable_unprepare()` and returns zero. Therefore the patch can propagate acquisition errors, but its negative disable branch is lookup/lifecycle handling only; it cannot observe a provider-reported disable error or prove the gate physically turned off. The harness's `physical_on` case is explicitly an uncertainty model.

## Retry blocker

The actual caller clears `IS_SENSOR_ICLK_ON` only when `iclk_off()` returns zero (`is-device-sensor_v2.c:492-526`). After the patch's DMA-off error and failed selected-gate restoration, the selected caller vote has already been released, but the callback returns an error and leaves that bit set. The bit cannot distinguish “selected vote restored” from “selected vote absent.”

I added one bounded negative control in a temporary copy of the extracted-C fixture; no repository harness or kernel source was changed. Starting with one unrelated shared reference per modeled clock, it runs successful on, injects a DMA lookup failure after selected release, and injects a selected-gate enable failure during restoration. The first off returns `-EINVAL`, leaves the ON bit set, and leaves only the unrelated selected-gate reference. A second off retry returns success while reducing that selected reference from one to zero. This follows the real callback and caller code; the fake clock counter makes shared-reference consumption observable. A real `clk_disable_unprepare()` has aggregate clock-framework accounting, not this callback's per-sensor ownership token, so that retry may decrement another user's reference or attempt an unbalanced disable.

The PM wrapper compounds the state mismatch. `is_sensor_runtime_suspend()` logs an `is_sensor_iclk_off()` error at lines 3980–3982 but returns 0 at line 4001. On a later runtime resume, `is_sensor_iclk_on()` sees the retained ON bit and returns success without calling cfg/on at lines 457–489. Thus PM sees a successful suspend, resume does not reconcile the partial clock state, and a later suspend can retry off against a vote this caller no longer owns.

There is a related retry gap after failed cfg/on cleanup: the patch records remaining votes only in local variables and reports unknown state when cleanup fails. The real on caller leaves `IS_SENSOR_ICLK_ON` clear on that error. A subsequent runtime-resume attempt starts cfg again without knowing whether earlier votes remain. The fixture proves the immediate residual vote and preserved shared counter, but does not establish how often this helper lookup failure can occur after a successful cfg under a stable clock table. Treat it as an ownership gap for the modeled/lifecycle error path, not a claim about normal provider behavior.

## Independent host evidence

`git apply --check` passed against the preserved derived tree. I independently ran the frozen five-test Python suite three times with Python 3.12.3 and `/usr/bin/cc` (GCC 13.3.0):

| Invocation | Observed Python flags | Result |
| --- | --- | --- |
| `CAMERA_EXPECT_PYTHONOPTIMIZE=0 python3 -I -B tools/hardware/test-camera-sensor-clock-unwind.py` | isolated=1, no_site=0, optimize=0, `PYTHONOPTIMIZE` unset | 5/5 passed |
| `CAMERA_EXPECT_PYTHONOPTIMIZE=1 python3 -O -I -B tools/hardware/test-camera-sensor-clock-unwind.py` | isolated=1, no_site=0, optimize=1, `PYTHONOPTIMIZE` unset | 5/5 passed |
| `CAMERA_EXPECT_PYTHONOPTIMIZE=1 PYTHONOPTIMIZE=1 python3 -B tools/hardware/test-camera-sensor-clock-unwind.py` | isolated=0, no_site=0, optimize=1, `PYTHONOPTIMIZE=1` | 5/5 passed |

Each run checks the pinned source revisions and hashes, extracts the actual baseline or patched functions plus actual ICLK caller, compiles them with `-std=gnu89 -Wall -Wextra -Werror -Wno-unused-parameter` at C `-O0` and `-O2`, and executes them. The baseline reproduces hidden acquisition/off failures and invalid-channel behavior; the patched fixture covers all eight cfg acquisitions, all seven selected-gate acquisitions, all seven setup-gate disable positions, cleanup/restoration failure reporting, shared-reference preservation in its model, and ON-bit updates. The extra retry control passed at C `-O0` and printed `NEGATIVE_CONTROL: retry consumed the remaining shared selected-clock reference`.

These checks are deterministic host shims around extracted C. They do not compile the kernel, extract or execute the runtime PM wrappers, exercise the Linux clock framework/provider, measure physical gates, or prove a device transition. No phone, SSH, ioctl, firmware, inference, module load, kernel build, or repack action occurred. The previously recorded camera trial remains `not_accepted`; this review does not change it.
