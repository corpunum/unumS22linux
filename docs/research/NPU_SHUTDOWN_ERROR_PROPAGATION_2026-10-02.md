# NPU shutdown error propagation — 2026-10-02

Status: a narrow source patch and host extracted-C regression are prepared on
top of the exact six-patch profile. The result is source-composition evidence;
it is not a kernel build, module, runtime, firmware, or device acceptance.
BOOTUP readiness and authorization remain false.

## Pinned source and change

The Lineage source base is `4e5c5ad7d950e4de0688b5663965f2075654b2ad`. The
local derived fixture is clean at `3fca50941422439b2019db2e4a3dc1016b2138a1`;
the NPU inputs used by the loaders match the pinned source. The test builds the
verified loader union in a fresh temporary tree, applies the four reconciled
patches, then `npu-shutdown-lifecycle-profile.patch`, then this patch. Each
patch receives ordinary `git apply --check` followed by ordinary `git apply`.
No historical patch is rewritten, forced, or fuzzed. The frozen ownership
patch remains byte-identical and still reports its previously reviewed
`npu-vertex.c:313` overlap after the four-patch prefix.

`npu_device_shutdown()` previously assigned the return from
`__npu_device_early_close()` to `ret`, logged an error, then overwrote `ret`
with `proto_drv_close()` and later `npu_system_suspend()`. A successful later
callback therefore concealed an earlier failure. The new patch uses `ret` for
the first nonzero result and `ret2` for each subsequent callback. It preserves
the existing execution order: early close, protocol close, DHCP deinit, and
system suspend. It continues all those existing cleanup steps after an error
and reports the earliest error to the caller.

The inspected dependencies are significant to the limits of this change:

- `__npu_device_early_close()` can return a profiler-close error and then
  performs the scheduler early-close step.
- `proto_drv_close()` can return `-EBADR` when the protocol state cannot
  transition to `PROTO_DRV_STATE_PROBED`; that check returns before its normal
  worker and protocol cleanup. On its success path it terminates the AST
  worker and clears protocol open steps.
- `dsp_dhcp_deinit()` is a void cleanup between protocol close and suspend.
- `npu_system_suspend()` follows those steps and can itself return an error.

The first-error change does not alter the old continuation behavior. It does
not establish that continuing suspend is safe for every failed protocol state;
the test verifies preserved order and visible failure, not the protocol or
hardware state produced by those real callbacks.

## Caller and ownership evidence

The only direct driver call to `npu_device_shutdown()` is the boot-reference
final callback `__vref_shutdown()`. The callback is installed on
`vertex.boot_cnt`; its final put is used by close and boot-control paths.

The representative close path is now exercised through the actual extracted
`npu_vertex_close()`, `__vref_put()`, `__vref_shutdown()`, and
`npu_device_shutdown()` bodies. With protocol close returning `-EBADR`, the
pre-fix five-patch C reports success and continues to hardware shutdown,
session close, and open-reference release. With patch six, close receives
`-EBADR`, latches shutdown uncertainty, balances the vertex lock, and skips
hardware shutdown and session/open-reference freeing. The session object and
open ref remain. The already-started close has unregistered hardware, sent the
power notification, cleared the per-session POWER bit, and decremented the
boot ref to zero before the callback error is returned; this work does not
restore that ref or prove the remaining object lifetime.

Other source callers do not all provide the same quarantine contract. Normal
bootdown checks the ref-put result and latches shutdown uncertainty. The
normal-bootup unwind logs a failed put and sets the emergency bit, then
continues its existing hardware-shutdown unwind. The alternate
`__npu_vertex_bootup()` path has cleanup branches that ignore the ref-put
result. This patch makes the driver callback report its first error; it does
not redesign those caller paths or establish retention across VFS release,
session-manager teardown, or device/module removal.

## Regression and results

`tools/hardware/test-npu-shutdown-error-propagation.py` applies and checks all
six patches over the exact verified union. It compiles the actual extracted
shutdown and close C bodies with controlled host shims for early close,
protocol close, DHCP deinit, and system suspend. The regression has 30
scenario configurations per run: four pre-fix reproductions, nine final
shutdown/close cases, and the 17 existing shutdown-ownership cases (including
the combined POWER_NOTIFY close-order case). Each configuration runs at C
`-O0` and `-O2` with warnings as errors.

The pre-fix reproductions show early-close and protocol-close false success,
last-error rather than first-error precedence when suspend also fails, and the
close caller freeing the session after that false success. Final cases cover
all-success, each individual callback error, multiple errors and first-error
precedence, unchanged ordered teardown, the already-closed guard, and close
caller quarantine. The inherited 17 ownership cases also pass against final
extracted C.

The test passed with the explicit exact local fixture under `python3`,
`python3 -O`, and `PYTHONOPTIMIZE=1 python3`; the default public SHA-loader route
also passed once. The public route reported the 147,641-byte shutdown fixture
and 1,214,674-byte deduplicated union. BOOTUP preflight returned status 2 with
`artifact_preflight_pass=false`, `bootup_ready=false`, and
`bootup_authorized=false`; the two required firmware inputs were absent.

The local-mode commands were:

```sh
S22_NPU_PROBE_SOURCE_TREE=/home/corpunum/s22-workers/camera-kernel-build-20260927 \
S22_NPU_SHUTDOWN_SOURCE_TREE=/home/corpunum/s22-workers/camera-kernel-build-20260927 \
PYTHONDONTWRITEBYTECODE=1 python3 tools/hardware/test-npu-shutdown-error-propagation.py

S22_NPU_PROBE_SOURCE_TREE=/home/corpunum/s22-workers/camera-kernel-build-20260927 \
S22_NPU_SHUTDOWN_SOURCE_TREE=/home/corpunum/s22-workers/camera-kernel-build-20260927 \
PYTHONDONTWRITEBYTECODE=1 python3 -O tools/hardware/test-npu-shutdown-error-propagation.py

S22_NPU_PROBE_SOURCE_TREE=/home/corpunum/s22-workers/camera-kernel-build-20260927 \
S22_NPU_SHUTDOWN_SOURCE_TREE=/home/corpunum/s22-workers/camera-kernel-build-20260927 \
PYTHONOPTIMIZE=1 PYTHONDONTWRITEBYTECODE=1 python3 tools/hardware/test-npu-shutdown-error-propagation.py

env -u S22_NPU_PROBE_SOURCE_TREE -u S22_NPU_SHUTDOWN_SOURCE_TREE \
PYTHONDONTWRITEBYTECODE=1 python3 tools/hardware/test-npu-shutdown-error-propagation.py
```

The tested patch SHA-256 is
`08374e96792f24d1e0e4fbca594bfce35537af8acace9296b66f27b531564e43`; the
test SHA-256 is
`7af046d7330acaa5d54ff5a7a334f7acbe07b0ace6301194fa9b15704c251a70`.

All evidence is host-only extracted C with deterministic shims. It does not
validate Linux locking or memory ordering, the real protocol worker, real
system suspend, firmware, NPU hardware, module/session-manager lifetime, or
device behavior. No phone, SSH, ADB, service, inference, firmware, kernel
build, reboot, or push action was used.
