# Independent camera shutdown-quarantine review

Verdict: **PASS_LIMITED for the reviewed sequential shutdown paths and host regressions.** When the ICLK-UNKNOWN bit is already observable, the candidate now avoids the three specific shared-resource teardown escapes found in the prior review: cleanup/front-stop (including the registered reboot callback), platform sensor-thread/work teardown, and the queued instant-start worker's CSI stream-off. This is not a finding that camera shutdown, reboot, DMA, or physical clock state is generally safe. The earlier ICLK retry-ownership blocker remains open; this review only assesses the shutdown-quarantine addition and does not clear the broader candidate.

## Reviewed inputs

- Integrated candidate: `d06bb09bcb69fd1418863ae000181afa66fa1938` (`fix(camera): quarantine reboot worker stream-off`), worktree `codex/s22-camera-shutdown-review-20261003`.
- Patch SHA-256: `7808ac0cabc806270a0d07aa2bb974bfd0fc924d3fef33035ba50bf494e768bd`.
- Harness SHA-256: `000e62c7091673a6b1260358ce6641aac763a830827175417647b2a392573619`.
- Test runner SHA-256: `f8fb0f52263631415e5a1ea616bb45316dcf5494159f999062e2a2b4c8508fc3`.
- Pinned kernel source: commit `4e5c5ad7d950e4de0688b5663965f2075654b2ad`, tree `5c46cbe12dadbcdb64eec4344c9e8ff0f8a75dee`; read-only and clean during review. The six-sensor configuration and reboot-handler enable/registration were checked in the pinned source. The optional derived tree is commit `3fca50941422439b2019db2e4a3dc1016b2138a1`, tree `5aad5cf1dbaa0f430377737141f0547e971b0a2d`.
- The earlier `CAMERA_SENSOR_CLOCK_UNWIND_REVIEW_2026-10-03.md` remains unchanged and blocked on per-caller clock-vote ownership after failed restoration.

## Source-path assessment

`is_cleanup()` now checks all six sensor state words before its front-stop loop. If any sensor is quarantined, it sets `reboot=true` for each probed sensor while holding that sensor's `mutex_reboot`, then returns before any `is_sensor_front_stop()`. The registered reboot handler calls this same cleanup path; the pinned configuration enables its registration. The callback and global reboot policy are otherwise unchanged, so this is not a veto of system reboot or power removal.

`is_shutdown()` sets the core shutdown flag, invokes cleanup, and rescans the six sensors. On observed uncertainty it returns before `is_sensor_deinit_sensor_thread()` and both CIS `cancel_work_sync()` calls. The core-wide decision also retains healthy peers, which share the camera core's sensor/work set, rather than stopping them independently.

The queued `is_sensor_instanton()` worker now takes its existing `mutex_reboot`, checks the current sensor and every sensor in the core, and exits with `-EUCLEAN` through its no-start path if uncertainty is observed. This check precedes the `device->reboot` branch, whose baseline behavior calls CSI `s_stream(IS_DISABLE_STREAM)`, and precedes `is_sensor_start()`. Thus a worker queued behind a sequential guarded cleanup no longer performs that CSI stop for either the unknown sensor or a healthy peer whose reboot gate was closed.

The extracted-C fixture reproduced the baseline escapes in direct cleanup, the actual registered reboot callback, platform shutdown, and the queued worker after `reboot=true`. Patched checks covered one unknown plus one healthy/front-started peer, repeated cleanup and shutdown, queued workers for both sensors, and unchanged clean-state worker-start, cleanup/front-stop, and shutdown/deinit/cancel behavior. The fixture models mutexes as no-ops and hardware/work routines as counters; it is a control-flow regression, not a concurrency simulation.

## Independent checks

Using Python 3.12.3 and GCC 13.3.0, I independently ran the pinned nine-test suite in normal, `-O`, and effective `PYTHONOPTIMIZE=1` modes. All three runs passed 9/9 with observed `sys.flags.optimize` of 0, 1, and 1. Each run compiled and executed baseline and patched extracted shutdown/clock C at `-O0` and `-O2`; baseline reproductions and patched mixed-peer/repeat/clean-path checks passed.

I also forced the public pinned-source route while pointing the optional derived-root at an absent path. It passed all eight applicable tests; the derived-tree-only test skipped as expected. A single explicit wrong `CAMERA_CLOCK_SOURCE_TREE` override (the derived source tree supplied where the original pinned tree was required) was rejected before tests ran: the loader reported the supplied commit/tree `3fca5094…/5aad5cf1…` instead of required `4e5c5ad7…/5c46cbe1…` (exit 5 from unittest setup). No supplied path is accepted by name alone.

## Limits that remain

The UNKNOWN bit is set by a runtime-PM path without `mutex_reboot`; the new scans and `reboot=true` writes do not serialize with that writer. A transition concurrent with the initial cleanup scan can therefore race the preflight. The worker checks under its current sensor's reboot mutex, but that does not synchronize the UNKNOWN bit or peer state writers; work already past the guard is not drained by this change. Shutdown's second scan protects the later deinit/cancel sequence only if it sees uncertainty; it cannot undo front-stop work already performed before a racing bit was set.

No proof was made about already-running asynchronous work, DMA or physical clock/provider state, parent-device teardown, external system power removal, or real kernel lock/workqueue behavior. References retained by the broader candidate are a terminal quarantine policy, not a tested teardown solution. The host result does not clear the earlier retry-ownership blocker, authorize kernel compilation, or establish device/stream acceptance.

No kernel build, source-tree mutation, phone/SSH access, firmware action, module load, camera stream, reboot, or deployment occurred.
