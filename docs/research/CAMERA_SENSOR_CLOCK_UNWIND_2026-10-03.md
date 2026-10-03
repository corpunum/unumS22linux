# Camera v10.1 sensor-clock error propagation and unwind

Status: host-extracted-C regression result for a narrow proposed patch. This is not a kernel build, device test, clock-provider acceptance, or authorization to boot or deploy.

## Pinned source and observed defect

The worktree for this note is `codex/s22-camera-sensor-clock-unwind-20261003`, based on `a9831c7ce10ea1eebe3ac7c1883071bea85a2a4e` (tree `4d2507d71c8e17ecf03a0e4a38cb529eb41bb99a`). The pinned original source is `/home/corpunum/s22-linux/lineage/android_kernel_samsung_s5e9925` at commit `4e5c5ad7d950e4de0688b5663965f2075654b2ad`, tree `5c46cbe12dadbcdb64eec4344c9e8ff0f8a75dee`. The already-derived camera tree is `/home/corpunum/s22-workers/camera-kernel-build-20260927` at `3fca50941422439b2019db2e4a3dc1016b2138a1`, tree `5aad5cf1dbaa0f430377737141f0547e971b0a2d`. Both source worktrees were clean when checked; neither was edited.

The target `drivers/media/platform/exynos/camera/ischain/is-v10_1_0/setup-is-sensor.c` is byte-identical in both trees (SHA-256 `be9ea9769e2b1678cea68f1a86514aa0ccd559bfa9316840a1a83268bfef1182`). The caller `drivers/media/platform/exynos/camera/is-device-sensor_v2.c` is pinned by SHA-256 `b88e203fa7ca7fdcf109f969d516601a9cba4702c98a3a0b338b3a6dbcf55265`; helper definitions in `setup-is.c` are pinned by SHA-256 `0e3668ca54f6d60abb8a22dc3a18d15b441fcf3c5154b2f0ee61e481d37b88b2`.

In the pinned baseline, the v10.1 cfg callback ignores all eight enable results (CSIS0–6 and CSIS DMA); its on/off helpers and their generic wrappers also discard lower-level errors. The actual `is_sensor_iclk_on()` caller calls cfg then on and sets `IS_SENSOR_ICLK_ON` only after both return zero. The off caller clears that bit only after the off callback succeeds. Since the generic wrappers returned zero regardless, an injected cfg or selected-clock failure was hidden from the caller, and an off failure could be hidden after partial teardown. The actual extracted baseline reproduces those behaviors.

## Proposed patch behavior

`tools/hardware/camera-sensor-clock-unwind.patch` changes only the v10.1 sensor ICLK functions and their three wrappers. It does not touch MCLK paths, shared clock helpers, other camera variants, or global clock ownership.

- `exynos9925_is_csi_gate()` returns the selected helper result. The cfg path rejects channels above 6 before touching clocks, acquires the seven CSIS gates and DMA in order, and on failure releases only earlier successful cfg acquisitions in reverse order while preserving the initiating error.
- The on path relies on the pinned caller's successful cfg-before-on sequence. It tracks the still-owned cfg gate votes locally while converting them to the selected gate. On a failure it releases the cfg DMA vote first, then best-effort releases remaining cfg gate votes in reverse order. The wrapper propagates the real result, so the caller does not set `IS_SENSOR_ICLK_ON` on failure.
- The off path rejects channels above 6 before clock operations, propagates selected-gate and DMA errors, and if DMA release fails after selected-gate release, attempts to reacquire that selected vote. It preserves the original error; an unsuccessful cleanup/restoration is logged as unknown clock state. The caller then retains its logical ON bit because the callback returned an error.

The local ownership bookkeeping is justified only for this observed caller sequence; it is not a general ownership registry. The regression fixture starts with one unrelated shared reference per modeled clock and verifies that success and injected failures do not consume those references. It also covers the unsupported channel-7 off path: although the lower-level gate switch has a case for 7, cfg/on reject values above 6, so this patch rejects 7 before releasing any clock.

## Test evidence

`tools/hardware/test-camera-sensor-clock-unwind.py` pins both source revisions and the three source-file hashes above, checks that the patch applies to both identical target blobs, extracts the actual baseline or patched C functions and actual caller into the C harness, and compiles/runs those extracted functions with host `cc` (`/usr/bin/cc`, GCC 13.3.0, `-std=gnu89 -Wall -Wextra -Werror -Wno-unused-parameter`) at C `-O0` and `-O2`.

The five Python tests passed in each invocation:

```text
CAMERA_EXPECT_PYTHONOPTIMIZE=0 python3 -I -B tools/hardware/test-camera-sensor-clock-unwind.py
CAMERA_EXPECT_PYTHONOPTIMIZE=1 python3 -O -I -B tools/hardware/test-camera-sensor-clock-unwind.py
CAMERA_EXPECT_PYTHONOPTIMIZE=1 PYTHONOPTIMIZE=1 python3 -B tools/hardware/test-camera-sensor-clock-unwind.py
```

Observed `sys.flags.optimize` values were `0`, `1`, and `1` respectively. In both C optimization levels, the baseline executable reproduced hidden cfg failures, invalid-channel false success, a hidden selected-clock failure, and a hidden off error. The patched executable passed injected failures at each of the eight cfg acquisitions, each of the seven selected-gate acquisitions, and each of the seven setup-gate disable positions; it checked reverse unwind order, first-error preservation, unsupported-channel no-touch behavior, failed cleanup/restoration diagnostics, shared-reference preservation, and actual caller ON-bit behavior. A model case deliberately allows a failing enable to leave its `physical_on` marker set after vote cleanup, to make the physical-state uncertainty visible.

## Limits and unresolved physical evidence

The pinned `is_enable()` returns the result of `clk_prepare_enable()`. The pinned `is_disable()` can return `-EINVAL` when its clock lookup is absent, before reaching `clk_disable_unprepare()`; that clock-disable API is `void`, and the helper returns zero after calling it. Accordingly, negative disable injections in this harness model the helper's lookup-failure path (no vote decrement), not a provider-reported physical disable failure. The enable-side `physical_on` case is an uncertainty model, not a claim that this provider behaves that way.

These are deterministic host shims around extracted C—not the kernel clock framework, real shared clock references, concurrent callers, or physical gate state. Successful cleanup in the model cannot prove physical rollback; failed cleanup is reported but cannot guarantee a safe device state. No kernel compilation, module load, phone access, clock measurement, streaming, or firmware action occurred. The previously recorded camera hardware trial remains `not_accepted`; this patch and these tests do not revise it.
