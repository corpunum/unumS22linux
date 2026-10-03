# NPU system-resume error propagation and unwind

## Result

This host-only change repairs the pinned NPU system resume path that converted
any later firmware, clock, SoC, or interface error into success. The resume
caller now receives the first resume error, while a best-effort suspend unwinds
only recorded successful stages. If cleanup itself fails, that cleanup error
is logged separately and the resume error remains the return value. Hardware
transitions whose outcome cannot be inferred from an error return are
quarantined; the patch does not guess an inverse or retry a failed inverse.

This is source-level and extracted-C evidence only. It is not a kernel build,
firmware validation, device run, or NPU acceptance result.

## Pinned inputs and patch order

The raw pinned kernel base is `4e5c5ad7d950e4de0688b5663965f2075654b2ad`.
The clean derived fixture is
`/home/corpunum/s22-workers/camera-kernel-build-20260927` at
`3fca50941422439b2019db2e4a3dc1016b2138a1`; the composed NPU12 source is
`/home/corpunum/s22-linux/builds/npu-native-twelve-kernel-20261003` at
`e9c3016233a72ceccb13e537f0b7ef72426582b9`. All are read-only inputs to this
work. The NPU14 test checks the four NPU12 patch digests and their established
order, then ordinarily applies the NPU13 patch and this patch to an isolated
copy. It does not reapply NPU12 patches to the already-composed source tree.

The verified NPU12 stack pins are:

| Order | Patch | SHA-256 |
| --- | --- | --- |
| 1 | `npu-session-lifecycle-fix.patch` | `1554436cb6624c542f9e04ac22a3b3545e55f94c59d3025ee6bdc1ec43168251` |
| 2 | `npu-refcount-transaction-fix.patch` | `09414c886c37e55e23767f33fcdba19f5ab8099a6df3a2d5618972449d64a935` |
| 3 | `npu-default-boot-callback-fix.patch` | `f5ce216e34df11d8c6adee4a99c36d63f73593cf379e29de3a9de828ec2ee1e7` |
| 4 | `npu-probe-unwind-fix.patch` | `d3e2e590d4d3c956b10c724a15db996dacd07def204332f50a1c0513f56b4948` |
| 5 | `npu-interface-open-unwind.patch` (NPU13) | `95e63b45d60e0a2611c1f2dcab4428e5658a03ff9954b8197d175ac6fec139cb` |
| 6 | `npu-system-resume-error-unwind.patch` (NPU14) | `464a78b43f7ef0cc7e26b5f69075980460211f9c789d0a418b36d44be548968e` |

The relevant composed source identities were checked before extraction:

| Source | SHA-256 |
| --- | --- |
| `drivers/vision/npu/core/npu-system.c` | `96eaa6bf1511f3e6414e3e376d62592229454a2ea1687d760b7bb8e5952b1a05` |
| raw/derived `drivers/vision/npu/core/npu-device.c` | `98be21e422ca864cc971dfe6a78e71b292d7691cb625c1f029bf4502100644ab` |
| composed NPU12 `npu-device.c` | `a281fd35f2311951328d797824b8dbb165639bfc7977da30044bb764688cdc11` |
| composed NPU12 `drivers/vision/npu/core/npu-hw-device.c` | `b052462aa4919458a2b86c7ba0aed78bfdb938690cd58d5dcd01bdf0d75a312b` |
| composed NPU12 `drivers/vision/npu/core/interface/hardware/npu-interface.c` | `c2deaa0abd990184b64373bb13983f048b925e0f5f421f6623de7de85f667108` |

`npu-system.c` is byte-identical in raw, derived, and composed12 source. The
NPU13 interface patch is applied before NPU14 in an isolated temporary copy;
`git apply --check --whitespace=error-all` and ordinary `git apply` pass. The
source worktrees themselves were not modified.

## Source behavior repaired

The pinned `npu_system_resume()` set a global emergency bit after failure and
then assigned `ret = 0`. Its callers could therefore proceed as if hardware
resume had succeeded. The new path preserves the initiating errno and invokes
`npu_system_suspend()` to unwind known completed stages. It logs an unwind
failure independently instead of replacing the first error.

The change adds explicit per-stage uncertainty markers around CPU-on and
STM-enable. Those helpers may return an error after partially changing
hardware/refcount state, so an error does not authorize CPU-off or STM-disable.
The marker makes SoC suspend return an error without issuing that guessed
inverse; the parent SoC, clock, firmware-memory, and wake-lock ownership remains
visible. Resume, open, and close reject residual stage state rather than
clearing it or freeing memory underneath uncertain hardware.

Successful cleanup clears a stage only after its inverse succeeds. Before each
fallible inverse—interface close, STM disable, CPU off, and log-buffer free—an
atomic `test_and_set_bit()` claims the one attempt. Success clears that
uncertainty marker and then the completed-stage marker. Failure leaves the
uncertainty marker and ownership state set; a later suspend returns a
quarantine error without calling the inverse again. This prevents duplicate
sequential cleanup from repeating a potentially partial refcount release.

Runtime PM acquisition in `__npu_device_power_on()` changes from
`pm_runtime_get_sync()` to the pinned kernel's `pm_runtime_resume_and_get()`.
The kernel header documents the latter as balancing its usage reference if
resume fails and returning normalized success. The actual runtime callback is
tested to ensure a failed resume does not run its success-only clock-off path.
The composed NPU12 `npu_hwdev_default_boot()` callback is also extracted and
tested: failed PM resume and later clock failure publish ERROR and balance the
acquired PM reference; successful on/off transitions balance one reference.

`npu_system_alloc_fw_dram_log_buf()` is extracted directly from pinned
`npu-system.c`. A first allocation failure now propagates and releases only
the acquired wake lock when neither global buffer was allocated. If either
file-static buffer is present when initialization reports failure, the patch
marks the global-buffer owner uncertain. The wake lock is still released, but
the residual marker blocks another resume/open and prevents close from freeing
the memory underlying a potentially live global parser pointer. There is no
automatic retry or guessed free.

## Regression evidence

The runner is `tools/hardware/test-npu-system-resume-error-unwind.py`; it
extracts the actual pinned function bodies for `npu_system_open()`,
`npu_system_close()`, `npu_system_soc_resume()`,
`npu_system_soc_suspend()`, `npu_system_resume()`, `npu_system_suspend()`,
`__npu_device_power_on()`, `npu_device_bootup()`,
`npu_device_runtime_suspend()`, `npu_device_runtime_resume()`, the actual
NPU12 `npu_hwdev_default_boot()`, and the actual
`npu_system_alloc_fw_dram_log_buf()` body. It compiles those bodies with
controlled host shims for hardware/helper operations and executes baseline
and patched variants at C `-O0` and `-O2`.

Baseline assertions reproduce the swallowed runtime firmware error and false
success-only clock action; bootup's resume-error path closing memory while the
CPU remains on; CPU-off and STM-disable errors being masked while later
cleanup proceeds; interface-close error being masked while SoC teardown
continues; and the first/second allocator failures being reported as success.
The bootup caller test also reproduces close of memory after the second global
buffer allocation failed. Patched assertions confirm that this partial
allocator state is quarantined and the caller cannot close its backing memory.
Patched assertions check original errno propagation, rollback of confirmed
stages, acquired-reference balance, no guessed inverse after uncertain
CPU-on/STM-enable, no lower teardown after interface/CPU/STM cleanup failure,
one-shot quarantine on repeated interface/STM/CPU/log-buffer cleanup, close
blocked while ownership remains, and success plus a fresh resume after a fully
successful suspend.

The public source loader was attempted once after implementation edits but
timed out during the bounded response read before any compile jobs began. That
attempt is not counted as a pass. The exact raw-pinned system/device file bytes
were then loaded from the clean derived fixture using explicit `--local-only`
mode; its commit, cleanliness, path hashes, and equality to pinned SHA-256
values are checked. The local source/ordinary-apply/C matrix passed in all
three Python modes:

| Invocation | Result |
| --- | --- |
| `python3 tools/hardware/test-npu-system-resume-error-unwind.py --local-only` | 8 C compile/run jobs passed |
| `python3 -O tools/hardware/test-npu-system-resume-error-unwind.py --local-only` | 8 C compile/run jobs passed |
| `PYTHONOPTIMIZE=1 python3 tools/hardware/test-npu-system-resume-error-unwind.py --local-only` | 8 C compile/run jobs passed |

Each invocation covers baseline+patched at C `-O0` and `-O2` for both the
boot-ioctl caller and runtime-PM caller. That is 24 host compile/run jobs in
total across the three Python modes. Compiler: `cc (Ubuntu 13.3.0-6ubuntu2~24.04.1)`;
`/usr/bin/cc` SHA-256 `1b99826121ae6682a634e5efe09bd3e3df58ce58e0b28f849114ab5b89139c26`.

The local shim models selected helper results and counters; it is not a
kernel environment. The actual hardware interface open/close, firmware loader,
SoC CPU/STM operations, clock framework, IRQ/workqueue behavior, PM-core
scheduling, and physical transitions are represented by controlled shims in
this harness. NPU13 interface cleanup is applied in the composed source but
its interface body is not extracted into this NPU14 test. These tests do not
establish real kernel locking, firmware completion, DMA quiescence, or hardware
state.

## Remaining ownership and acceptance limits

- `npu_system_free_fw_dram_log_buf()` is currently a TODO that returns zero in
  this configuration. Its injected `-ENOMEM` is a fault-injection test of the
  new propagation/quarantine branch, not a currently observed hardware error.
- The firmware report/profile buffers are file-static, and their allocator can
  leave the first buffer allocated if the second allocation fails. The
  matching free routine is unimplemented; this patch does not free or transfer
  that global storage. Cross-device ownership and lifecycle of those static
  buffers are not established.
- `npu_fw_test_initialize()` currently returns zero after installing a
  file-static handler pointer. The harness
  exercises the real allocator body but does not claim independent recovery
  for hypothetical partial failures inside firmware-test initialization.
- `npu_imgloader_shutdown()` has no return value, so its completion/failure
  cannot be represented by this interface. Firmware bytes may have been
  partially populated by a failed load, but CPU/STM have not yet been enabled
  at that point; reloading is still firmware-path behavior, not tested here.
- `npu_device_runtime_suspend()` performs its existing core-clock-on action
  before calling system suspend. The test verifies cleanup errno propagation,
  but does not model clock-framework reference semantics on PM-core retries or
  change that existing caller action.
- The bitfield guards protect the audited stage transitions; this is not a
  complete serialization redesign for every system/device callback. Real PM
  callback ordering and all userspace/control-path concurrency remain
  unverified.
- No NPU14 kernel module compile, symbol/layout comparison, package build,
  firmware execution, device/ADB/SSH access, or real-device BOOTUP test was run. Any such
  validation requires a separate reviewed authorization.
