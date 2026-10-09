# Independent NPU shutdown-error propagation review — 2026-10-02

## Scope and result

Reviewed frozen author commit `1df641e4ae5c6c60a41e11d63a1e9f7670c73e51`
in `/home/corpunum/s22-workers/npu-shutdown-error-20261002`; it was clean at
inspection. The source fixture was clean at
`/home/corpunum/s22-workers/camera-kernel-build-20260927`, commit
`3fca50941422439b2019db2e4a3dc1016b2138a1`, with the tested NPU inputs
verified against base `4e5c5ad7d950e4de0688b5663965f2075654b2ad`.

No blocker was found in the bounded first-error propagation and close-caller
review. This is extracted source-C evidence only, not kernel-build, module,
firmware, runtime, or device acceptance. BOOTUP remains refused.

The patch SHA-256 is
`08374e96792f24d1e0e4fbca594bfce35537af8acace9296b66f27b531564e43`; the
test SHA-256 is
`7af046d7330acaa5d54ff5a7a334f7acbe07b0ace6301194fa9b15704c251a70`.
The patch adds only `npu-device.c` after the exact five-patch lifecycle
profile. The test verifies ordinary `git apply --check` and `git apply` for
all six ordered patches, no historical patch rewrite/force/fuzz, and that
patch six changes only `npu-device.c`. The frozen shutdown-ownership patch
SHA and its known ordinary-apply overlap at `npu-vertex.c:313` remain intact.

## Independent execution

Ran the frozen test in normal, `-O`, and `PYTHONOPTIMIZE=1` modes with the
exact local fixture, then once in default public-source mode:

```sh
S22_NPU_PROBE_SOURCE_TREE=/home/corpunum/s22-workers/camera-kernel-build-20260927 \
S22_NPU_SHUTDOWN_SOURCE_TREE=/home/corpunum/s22-workers/camera-kernel-build-20260927 \
PYTHONDONTWRITEBYTECODE=1 python3 -B tools/hardware/test-npu-shutdown-error-propagation.py

S22_NPU_PROBE_SOURCE_TREE=/home/corpunum/s22-workers/camera-kernel-build-20260927 \
S22_NPU_SHUTDOWN_SOURCE_TREE=/home/corpunum/s22-workers/camera-kernel-build-20260927 \
PYTHONDONTWRITEBYTECODE=1 python3 -B -O tools/hardware/test-npu-shutdown-error-propagation.py

S22_NPU_PROBE_SOURCE_TREE=/home/corpunum/s22-workers/camera-kernel-build-20260927 \
S22_NPU_SHUTDOWN_SOURCE_TREE=/home/corpunum/s22-workers/camera-kernel-build-20260927 \
PYTHONOPTIMIZE=1 PYTHONDONTWRITEBYTECODE=1 python3 -B tools/hardware/test-npu-shutdown-error-propagation.py

env -u S22_NPU_PROBE_SOURCE_TREE -u S22_NPU_SHUTDOWN_SOURCE_TREE \
PYTHONDONTWRITEBYTECODE=1 python3 -B tools/hardware/test-npu-shutdown-error-propagation.py
```

All four completed with exit 0. The public run fetched the pinned base
successfully and reported the 147,641-byte shutdown subset and 1,214,674-byte
source union. Every mode reported all six patch check/apply steps, the frozen
overlap, the pre-fix baseline reproduction, final propagation/quarantine,
and inherited ownership markers. The tested scenario set has 30 cases per
run—four pre-fix, nine final, and 17 inherited—each executed at C `-O0` and
`-O2` with warnings as errors.

The baseline is extracted from the exact five-patch profile before patch six;
it is not a Python model. It reproduces early-close and protocol-close false
success, later-suspend error replacing an earlier error, and a representative
close caller freeing the session after the masked protocol error. Against the
final extracted C, all-success returns zero; an individual callback failure
survives later success; the earliest of multiple errors wins; a suspend error
is returned when it is first; and the already-closed guard still rejects
without callbacks. Event assertions retain the existing order: early close,
protocol close, DHCP deinit, then system suspend. The new code records later
results separately and does not return early after the first failure.

## Caller ownership and limits

The representative `npu_vertex_close()` path is extracted together with
`__vref_put()`, `__vref_shutdown()`, and `npu_device_shutdown()`. With
protocol-close returning `-EBADR`, patch six lets the error reach close. Close
continues its already-started protocol sequence, then latches shutdown
uncertainty and emergency state, balances the vertex mutex, and skips hardware
shutdown, session close, and open-reference release. The session and open ref
remain; however, the boot ref was already decremented to zero before its
final-callback error returned, while `normal_count` remains one and the
per-session POWER bit has been cleared. This review does not claim the boot
reference is restored or that this state is safe across VFS release,
session-manager teardown, or device/module removal.

The test's callback shims establish return precedence and call order, not the
real protocol-worker or suspend behavior. In particular, real
`proto_drv_close()` can fail before its normal cleanup, and this change still
continues to DHCP deinit and system suspend; the test does not establish that
continuation is safe for every real failed protocol state. Linux locking and
memory ordering, firmware, hardware, module lifetime, and runtime remain
untested. Preflight after patch six returned status 2 with
`artifact_preflight_pass=false`, `bootup_ready=false`, and
`bootup_authorized=false`; required firmware inputs were absent. No phone,
SSH, ADB, service, inference, firmware, kernel build, reboot, or push action
was performed.

## Fixed-CI wiring review

Reviewed wiring commit `da658ded6d62a8362127b0c1bee73c0591cff5a8`. It adds
only this explicit test path to `REVIEWED_HOST_TEST_PATHS`, `HOST_TESTS`, and
`EXPECTED_HOST_TEST_PATHS` (three insertions across two files). The test blob
in that commit matches the reviewed SHA above. No dynamic discovery, skip
policy, runner logic, exclusions, or live-device paths changed. The separate
host-suite/policy execution remains with the coordinator.
