# NPU system-resume error propagation and unwind

## Result

This host-only correction addresses the reviewed ordering defect in NPU14:
when CPU-on (or non-BOOT_IOCTL STM-enable) returned an error with an uncertain
partial outcome, `npu_system_suspend()` could request image-loader shutdown
before it reached the uncertainty gate. The corrected path checks those
acquisition markers at the start of suspend and quarantines before interface,
firmware-loader, SoC, clock, or buffer teardown. It retains firmware and wake
ownership while the CPU/STM may still be live. Known-on CPU-off ordering is
unchanged.

The earlier NPU14 errno propagation and ownership guards remain. The
image-loader shutdown API is still `void`; this correction cannot observe or
claim provider completion. That gap, removal/release behavior, and static
buffer lifecycle remain unresolved, so this is not a fully cleared NPU power
path.

Evidence is pinned-source inspection and actual extracted-C execution with
controlled helper shims. No kernel build, firmware action, device run, or NPU
acceptance occurred. This follow-up is frozen for independent review; it is
not yet independently cleared.

## Pinned inputs and patch order

The raw pinned kernel base is
`4e5c5ad7d950e4de0688b5663965f2075654b2ad`. The runner now fetches four raw
source files from that exact public revision, under the existing 512-KiB file
cap and five-second fetch timeout, then composes only the selected source paths
in private temporary directories. It ordinarily applies the pinned
`NATIVE_EIGHT_PATCHES` from `build-npu-six-profile.py`, followed by NPU13 and
NPU14. Nonselected patch hunks are filtered with `git apply --include`; the
actual source worktrees are not modified.

The ordered selected-file composition inputs are pinned below. Rows 1–8 are
the existing native-eight builder profile; rows 9–10 are applied afterward
for this regression:

| Order | Patch | SHA-256 |
| --- | --- | --- |
| 1 | `npu-session-lifecycle-fix.patch` | `1554436cb6624c542f9e04ac22a3b3545e55f94c59d3025ee6bdc1ec43168251` |
| 2 | `npu-refcount-lifecycle-profile.patch` | `8385e4210a807f96f972757cd6ca74a8077b0127ab112d8b877d012ccdc3cb7b` |
| 3 | `npu-default-boot-callback-fix.patch` | `f5ce216e34df11d8c6adee4a99c36d63f73593cf379e29de3a9de828ec2ee1e7` |
| 4 | `npu-probe-unwind-fix.patch` | `d3e2e590d4d3c956b10c724a15db996dacd07def204332f50a1c0513f56b4948` |
| 5 | `npu-shutdown-lifecycle-profile.patch` | `b986e1896305fda55f1d702ed6f12dde646e4a84b3ce91009203b9d77b7a00e7` |
| 6 | `npu-shutdown-error-propagation.patch` | `08374e96792f24d1e0e4fbca594bfce35537af8acace9296b66f27b531564e43` |
| 7 | `npu-mailbox-missing-callback-reclaim.patch` | `f109b57381b3f2afcf2638b518f50db59ef8c9f784debd949488c74f8ea5c39b` |
| 8 | `npu-mailbox-debug-walk-bounds.patch` | `20c700bfa11f13836c76c88cca28a4f8dfa459e5cf146292a814880cd5850b29` |
| 9 | `npu-interface-open-unwind.patch` (NPU13) | `95e63b45d60e0a2611c1f2dcab4428e5658a03ff9954b8197d175ac6fec139cb` |
| 10 | `npu-system-resume-error-unwind.patch` (NPU14 predecessor) | `464a78b43f7ef0cc7e26b5f69075980460211f9c789d0a418b36d44be548968e` |

The current NPU14 patch keeps that filename and adds the early uncertainty
guard; its final SHA-256 is recorded in the host evidence receipt. The older
patch bytes in row 10 are retrieved from the frozen author commit and used only
as the fail-before control.

The public-route checks reproduce the exact selected NPU12 input hashes after
the eight-patch stack. The optional clean composed fixture at
`e9c3016233a72ceccb13e537f0b7ef72426582b9` independently confirms these four
selected files; its NPU8-to-NPU12 history has no changes to them. NPU9–12 are
therefore not represented as an additional applied patch stack in this runner;
the selected-file hashes and optional history check establish the boundary.
The pinned `s5e9925_defconfig` selects `CONFIG_NPU_USE_BOOT_IOCTL=y`, with
`CONFIG_EXYNOS_IMGLOADER=m` and `CONFIG_EXYNOS_S2MPU=m`; the NPU DT node has
`samsung,imgloader-s2mpu-support`. The STM-enable test is the alternate
non-BOOT_IOCTL compile path, not the selected default configuration.

| Selected source | Raw pinned SHA-256 | NPU12 selected SHA-256 |
| --- | --- | --- |
| `npu-system.c` | `96eaa6bf1511f3e6414e3e376d62592229454a2ea1687d760b7bb8e5952b1a05` | same |
| `npu-device.c` | `98be21e422ca864cc971dfe6a78e71b292d7691cb625c1f029bf4502100644ab` | `a281fd35f2311951328d797824b8dbb165639bfc7977da30044bb764688cdc11` |
| `npu-hw-device.c` | `14617a6f8e5b08e1bb169618daa8544f2680ad6709cb9f3b9730919d4dc8e16f` | `b052462aa4919458a2b86c7ba0aed78bfdb938690cd58d5dcd01bdf0d75a312b` |
| `interface/hardware/npu-interface.c` | `c2deaa0abd990184b64373bb13983f048b925e0f5f421f6623de7de85f667108` | same |

The clean derived fixture (`3fca50941422439b2019db2e4a3dc1016b2138a1`) and
composed NPU12 fixture are optional corroboration: if present they are checked
for exact commit, cleanliness, and hashes; absent defaults are explicitly
skipped. An explicitly supplied missing or mismatched fixture remains fatal.
The default public path does not require either private worktree. An isolated
temporary-directory scope now covers all composition/apply work.

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

The follow-up correction checks CPU-on uncertainty, and STM-enable uncertainty
in a non-BOOT_IOCTL build, immediately after suspend entry—before the prior
interface-close and firmware-loader stages. The guard returns `-EUCLEAN` via
the existing error path, leaving firmware permission state, clocks/SoC state,
and the active wake source owned. That wake source is intentionally retained
because the CPU/STM may still be live; no assumption is made that releasing it
is independent. For a known-on CPU, the existing sequence still requests
image-loader shutdown before the first CPU-off attempt. An ambiguous CPU-off
retry does not repeat the already-issued shutdown because the `FW_LOAD` stage
has been consumed by that first request.

The quarantine can leave the wake source active indefinitely and block system
suspend until a separately reviewed recovery/removal path handles the retained
state. No timeout, automatic inverse, or automatic marker clearing is added.

For return-valued fallible inverses—interface close, STM disable, CPU off, and
log-buffer free—an atomic `test_and_set_bit()` claims the one attempt. Success
clears that uncertainty marker and then the completed-stage marker; failure
leaves ownership set and a later suspend returns a quarantine error without
retrying. This prevents duplicate sequential cleanup from repeating a
potentially partial refcount release. The separate void image-loader shutdown
call cannot support this success-only accounting and remains an explicit gap.

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

`tools/hardware/test-npu-system-resume-error-unwind.py` extracts actual source
bodies from the four selected files: system open/close/resume/suspend and
SoC transitions, device power/boot/runtime-PM callers, NPU12's
`npu_hwdev_default_boot()`, and the firmware log-buffer allocator. The runner
has a bounded public raw-source route; local derived and composed worktrees are
optional, exact-pin corroboration. All source composition occurs in a scoped
temporary directory with ordinary selected-path `git apply --check` followed
by `git apply`.

The new negative control compiles the frozen pre-correction NPU14 patch
(`4a22948184f101f2bd80d2f44eef44b46004b790`, patch SHA
`464a78b43f7ef0cc7e26b5f69075980460211f9c789d0a418b36d44be548968e`). In the
configured BOOT_IOCTL case, injected `CPU_ON=-ETIMEDOUT` with modeled partial
CPU-live state reproduces one image-loader shutdown-wrapper call before the
later quarantine. In the alternate runtime-PM/STM configuration, partial
STM-enable failure reproduces the same ordering with CPU and STM modeled live.
The corrected source passes both cases with zero loader shutdown/interface
close/SoC inverse calls, preserves the `FW_LOAD` and uncertainty markers, and
retains the wake source. Repeated suspend remains `-EUCLEAN` without retrying
or clearing ownership. A distinct known-on CPU-off error control still allows
the initial shutdown request exactly once, then blocks duplicate teardown.

The existing NPU14 controls also cover swallowed pre-correction resume errors,
interface/CPU/STM inverse failures, allocator partial ownership, direct
bootup/runtime-PM callers, and refcount outcomes. Those earlier source
controls remain separate from the new ordering regression.

Each runner invocation compiles and runs 12 actual extracted-C fixtures:
pre-NPU14 baseline, frozen pre-correction NPU14, and corrected NPU14 at C
`-O0` and `-O2` for both BOOT_IOCTL and runtime-PM/STM configurations. The
public-source and local-fixture routes both passed in all three Python modes:

| Source route | Python mode | `sys.flags.optimize` | Result |
| --- | --- | ---: | --- |
| Public pinned fetch | normal | 0 | 12 C compile/run jobs passed |
| Public pinned fetch | `-O` | 1 | 12 C compile/run jobs passed |
| Public pinned fetch | `PYTHONOPTIMIZE=1` | 1 | 12 C compile/run jobs passed |
| `--local-only` | normal | 0 | 12 C compile/run jobs passed |
| `--local-only` | `-O` | 1 | 12 C compile/run jobs passed |
| `--local-only` | `PYTHONOPTIMIZE=1` | 1 | 12 C compile/run jobs passed |

Total: 72 host C compile/run jobs. Public raw fetch succeeded at the pinned
`4e5c5ad7d950e4de0688b5663965f2075654b2ad` URL. The local run additionally
verified the clean derived and composed NPU12 fixtures and their selected-path
history; default public execution does not require those private paths.
The optional-fixture selector also skipped simulated absent default paths while
keeping public composition active; explicitly supplied missing derived or
composed paths failed closed.
Compiler: `cc (Ubuntu 13.3.0-6ubuntu2~24.04.1)`; `/usr/bin/cc` SHA-256
`1b99826121ae6682a634e5efe09bd3e3df58ce58e0b28f849114ab5b89139c26`.

These host shims are not a kernel environment: image-loader/provider calls,
S2MPU permission release, CPU/STM state, wakeup-source semantics, clocks,
PM-core scheduling, and hardware are modeled counters/results. No real provider
failure or transition is executed. The test does not validate kernel locking,
firmware completion, DMA quiescence, or physical state.

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
  cannot be represented by this interface. The pinned `imgloader_shutdown()`
  provider tries to release S2MPU FW permission and returns early on a reported
  release error; its caller cannot observe that result. The known-on suspend
  path still clears `FW_LOAD` after issuing the void call, so provider-failure
  ownership remains unresolved. This correction only prevents the call before
  CPU/STM uncertainty quarantine; it does not manufacture a completion result.
  The pinned NPU image-loader ops set `.shutdown = NULL`, and its notify helper
  is a no-op; a counted wrapper invocation is not evidence of a remote NPU OFF
  event.
  Firmware bytes may have been partially populated by a failed load, but
  CPU/STM have not yet been enabled at that point; reload behavior is not
  tested here.
- `npu_device_remove()`/`npu_system_release()` are outside this correction.
  The reviewed source shows release ignores some provider/resource errors and
  does not use these resume-stage quarantine checks. No unbind/remove/module
  unload behavior was run; this remains a separate lifetime blocker.
- `npu_device_runtime_suspend()` performs its existing core-clock-on action
  before calling system suspend. The test verifies cleanup errno propagation,
  but does not model clock-framework reference semantics on PM-core retries or
  change that existing caller action.
- The bitfield guards protect the audited stage transitions; this is not a
  complete serialization redesign for every system/device callback. Real PM
  callback ordering and all userspace/control-path concurrency remain
  unverified.
- No NPU14 kernel module compile, symbol/layout comparison, package build,
  firmware execution, device/ADB/SSH access, or real-device BOOTUP test was
  run. Any such validation requires a separate review and authorization.
