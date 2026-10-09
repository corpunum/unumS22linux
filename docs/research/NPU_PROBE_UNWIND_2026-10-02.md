# NPU clock acquisition and probe unwind — 2026-10-02

Status: host-tested source patch; not built, deployed, or device-tested.

Target: LineageOS `android_kernel_samsung_s5e9925` base `4e5c5ad7d950e4de0688b5663965f2075654b2ad`. The source fixture was the clean derived worktree at `3fca50941422439b2019db2e4a3dc1016b2138a1`; the test verifies the pinned file hashes before extracting or compiling any driver code.

## Change

The patch makes clock-list acquisition transactional. Both `npu_clk_get()` and the new `npu_clk_get_optional()` initialize `struct npu_clocks` to `{ NULL, 0 }`, allocate zeroed name and clock arrays, retain OF and provider error codes, and restore the empty state before returning any error. A failed `devm_clk_get()` returns its original error, including `-EPROBE_DEFER`. Earlier successful `devm_clk_get()` references are left to the platform probe's devres unwind; the error path does not manually put them.

`npu_hwdev_probe()` uses required `npu_clk_get()` only for a non-DVFS `CLKCTRL` hwdev (the pinned DNC node, type `0x03`); all other types use the optional variant. This follows the actual type/use contract: DNC owns the explicitly declared `dnc_noc` clock, while the pinned NPU/DSP (`0x07`), CL1 (`0x04`), MIF/INT (`0x08`) hwdevs omit a direct list. An empty list is safe for those types: clock enable/disable iterates `clk_count`, so zero clocks produce no clock operation, and the NPU/DSP/auxiliary blocks' clocking is not represented by a direct per-hwdev list in this DT. A failed lookup/provider now returns from probe before `pm_runtime_enable()`, `dev_set_drvdata()`, or adding the device to `g_hwdev_list`. A successful probe's existing remove path calls `npu_clk_put()` once. That releases managed references without disabling clocks that were never enabled.

The optional behavior is deliberately scoped to hardware-device nodes and by type. DNC's absent required property is rejected with `-EINVAL`. For optional types, only a truly absent property (`of_find_property()` returns NULL) is treated as an empty list. The exact pinned `of_property_count_strings()` implementation distinguishes absent (`-EINVAL`), present with no value/empty (`-ENODATA`), and malformed unterminated string (`-EILSEQ`); the helper preserves those present-property errors rather than normalizing them to absence. Provider errors on optional lists are also fatal and retain their original errno, including `-EPROBE_DEFER`.

The shared `npu_clk_get()` API remains strict when `clock-names` is absent. This preserves the other callers: `npu_core_probe()` is guarded by `CONFIG_NPU_CORE_DRIVER` (disabled in the pinned S5E9925 defconfig, with no `npucore-id` node in the pinned DT), and the system clock caller is under `#ifndef CONFIG_NPU_USE_BOOT_IOCTL` (the pinned defconfig enables that option). The system node itself has no `clock-names`, so changing the shared API to accept absence could silently alter other build configurations.

## Executed host checks

`tools/hardware/test-npu-probe-unwind.py` fetches only bounded public source fixtures when no local source tree is configured, verifies each SHA-256, and exits `77` if the pinned source is unavailable. For this run it verified the clean local derived source against the pinned tree and compiled the exact extracted OF helper, clock acquisition/release, and hwdev probe/remove bodies with host-only DT, provider, PM, and devres shims.

The baseline reproducer initializes ordinary `devm_kmalloc()` shim memory with a fixed `0xa5` poison. A provider defer at the middle clock then deterministically shows the old probe returning success, enabling PM, publishing drvdata, registering the device, and retaining the poisoned failed/later clock slots. This controlled allocator makes the indeterminate pointer observable; `0xa5` is not claimed to be the phone's runtime memory contents.

Patched C cases cover pinned OF helper errno behavior; strict DNC-absent rejection and empty direct-list acceptance for NPU/DSP/CL1/MIF/INT; each probe allocation failure; present no-data and malformed DT through required and optional probe paths; first, middle, and last provider errors plus optional-list `-EPROBE_DEFER`; unexpected NULL provider output; empty state after failure; no PM or global registration on failure; and devm release exactly once on failed-probe unwind and successful remove. The shim also verifies no disable call occurs for a clock that was acquired but never enabled.

That matrix contains 28 distinct patched input scenarios (including the parameterized first/middle/last and allocation-failure cases), each executed at both `-O0` and `-O2` for 56 patched scenario executions per Python invocation. The baseline deterministic repro runs at both C optimization levels as well.

Patch application checks passed in temporary copies, both standalone and after `npu-refcount-transaction-fix.patch` plus `npu-default-boot-callback-fix.patch`. These are patch-compatibility checks, not a combined kernel build or combined-driver execution.

Commands executed:

```sh
S22_NPU_PROBE_SOURCE_TREE=/home/corpunum/s22-workers/camera-kernel-build-20260927 python3 tools/hardware/test-npu-probe-unwind.py
S22_NPU_PROBE_SOURCE_TREE=/home/corpunum/s22-workers/camera-kernel-build-20260927 python3 -O tools/hardware/test-npu-probe-unwind.py
S22_NPU_PROBE_SOURCE_TREE=/home/corpunum/s22-workers/camera-kernel-build-20260927 PYTHONOPTIMIZE=1 python3 tools/hardware/test-npu-probe-unwind.py
```

Each command runs baseline and patched extracted C at `-O0` and `-O2`, passes the standalone/combined apply checks, and runs the explicit Python checks without relying on Python `assert` statements.

## Evidence limits

This is source and host-shim evidence only. The shim models managed-reference cleanup after a failed probe, but does not execute the Linux driver core/devres implementation. No kernel build, firmware operation, module load, NPU BOOTUP, phone/SSH/ADB access, deployment, or hardware experiment was performed. The pre-existing shutdown/recovery policy and PM callback review remain separate work.
