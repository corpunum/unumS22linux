# Independent review: camera clock ownership revision

Verdict: **BLOCKED for lifecycle-complete clock-ownership safety.** The revision fixes the earlier modeled shared-vote retry: after selected-gate restoration fails, it latches an unknown-ownership state and refuses later clock callbacks and normal sensor/video teardown. But pinned reboot and platform-shutdown routes still enter camera stop/deinitialization without consulting that latch. Those routes can issue sensor and CSI stream-off operations while clock ownership is explicitly unknown. This is a source-proven missing guard, not proof of a particular physical failure; the revision therefore cannot establish safe shutdown/reboot or camera acceptance.

## Frozen inputs and scope

- Review worktree: `/home/corpunum/s22-workers/camera-clock-ownership-review-20261003`, branch `codex/s22-camera-clock-ownership-review-20261003`. It began at `1784035960d264ab8406049ab11d397568c84546`, with the unchanged historical blocked review `33a3e0e49517a1f9c79f66bae0dffc7e721a5b81` and the exact revision under review `296441ec81caf3e2ed8f4d2a2764c3e9efee8056` imported as `877dea5b0bfbd9462ffebcb0d6b26ee7e8a7145f`. The imported tree is `69043907d422f9cd751a477248ed760fe8da4981`.
- Frozen author inputs are byte-identical to the revision commit. Their SHA-256 values are recorded in the JSON evidence receipt. No author source, test, research note, or historical review was edited.
- Pinned original kernel source: commit `4e5c5ad7d950e4de0688b5663965f2075654b2ad`, tree `5c46cbe12dadbcdb64eec4344c9e8ff0f8a75dee`. The preserved derived source remains at `3fca50941422439b2019db2e4a3dc1016b2138a1`, tree `5aad5cf1dbaa0f430377737141f0547e971b0a2d`. Both were clean and read-only.
- Review was limited to the frozen patch, harness, test, research note, and pinned source/callers. No phone, SSH, device, camera operation, kernel build, module load, or repack occurred.

## Findings

### F1 — The retry/shared-vote ownership bug is corrected in the reviewed path

The previous blocked review demonstrated that after a selected-gate release, DMA lookup failure, and failed selected-gate restoration, a second off could consume a remaining shared selected-gate vote. The revision's clock callbacks return errors through their wrappers; the caller latches `IS_SENSOR_ICLK_UNKNOWN` on `-EUCLEAN`, preserves the ON bit on failed off, and rejects subsequent on/off before invoking the callbacks. Probe/open/close, the ordinary system- and runtime-PM paths, and direct/ischain video open/close also consult the latch. The retry sequence is exercised by the revised extracted-C fixture, including an unrelated modeled shared reference; repeated off leaves that reference untouched after quarantine.

The review also confirmed the revised static ownership checks cover direct sensor and ischain/nonleader close cases, idempotent per-context retention, and early video-open rejection. `is_resource_open()` only looks up and returns the resource pointer in the pinned source; it does not acquire a resource count before that early rejection.

This is host-model evidence, not clock-provider evidence. In the pinned `setup-is.c:194-215`, `is_disable()` can return a negative value for a missing clock lookup before calling `clk_disable_unprepare()`; that API is void and the helper otherwise returns zero. The injected disable failure therefore models lookup failure, not a provider-reported disable error or a measured physical gate transition.

### F2 — Enabled shutdown/reboot paths bypass the unknown-ownership quarantine (blocking)

The new latch guards in the patch do not cover the existing cleanup entry points. In pinned `is-core.c:1597-1623`, `is_cleanup()` visits probed sensors and calls `is_sensor_front_stop(device, true)` whenever `IS_SENSOR_FRONT_START` is set; it has no unknown-ICLK check. The platform driver installs `is_shutdown` at `is-core.c:1625-1702`; `is_shutdown()` calls `is_cleanup()` and then deinitializes sensor threads and cancels work, also without checking the latch. The reboot route is compiled in this source (`include/is-common-config.h:36`), registered at `is-resourcemgr.c:1549-1551`, and its handler calls the same unguarded `is_cleanup()` at `is-resourcemgr.c:1385-1405`.

This is not a merely theoretical alternative cleanup name: `is_sensor_front_stop()` calls `is_sensor_stop()` and then the CSI subdevice's `s_stream(IS_DISABLE_STREAM)` (`is-device-sensor_v2.c:3776-3837\)). `is_sensor_stop()` can call the module's `s_stream(false)` and `is_itf_stream_off()` (`:869-958\)). Thus, if a sensor is front-started when ownership becomes unknown and shutdown/reboot follows, those hardware-facing stop actions are still reachable without the patch's quarantine guard. The sources do not prove the exact electrical outcome or that these stop actions are harmful; they do prove that the patch does not refuse or reconcile them. Consequently safe stream quiescence, DMA safety, and shutdown/reboot teardown are unestablished. The research note discloses this gap, but the candidate remains blocked for any lifecycle-safety claim until that path is addressed and tested.

### F3 — VFS/V4L2 retention is intentionally terminal, not a general lifetime or quiescence guarantee

The pinned VFS `__fput()` invokes the file release callback but ignores its integer return before dropping the file-operations reference and freeing the file (`fs/file_table.c:255-300\)). V4L2's release path unconditionally drops its video-device reference after the driver release callback; the final device release invokes `video_device_release()`, which frees the video node (`drivers/media/v4l2-core/v4l2-dev.c:150-205,438-466`). Returning `-EUCLEAN` alone therefore cannot veto close. The revision's per-context latch, one `get_device()`, and one `__module_get()` are a coherent terminal-retention response to those wrapper puts; the repeat-direct-call test checks that the same context does not accumulate pins.

That path retains the context, queue and buffer ownership by bypassing normal VB2/context cleanup. It deliberately does not recover or release them, and it does not prove in-flight work or DMA has stopped. A device reference pins the video-device release object, not arbitrary parent allocations. In this exact pinned source, `is_core` is allocated by `is_probe()`, freed only on its error path, embeds the sensor and representative video nodes, and the platform driver has no `.remove` callback (`is-core.c:1313-1317,1588-1595,1694-1703\)); this supports only the inspected source arrangement, not changed or external teardown. The unguarded shutdown/reboot stop path in F2 remains the decisive lifecycle blocker.

## Independent host checks

The revised suite passed 8/8 in each local invocation: normal Python, `-O`, and environment-driven `PYTHONOPTIMIZE=1`; observed optimization flags were 0, 1, and 1. With the public loader forced and the private-derived path deliberately absent, 7 tests passed and only the optional private-derived apply check skipped. The actual baseline and patched extracted-C tests still ran on that public-source path. Supplying the review worktree as a deliberately wrong source fixture failed during setup with a source-identity mismatch (zero tests; exit 5), rather than silently falling back to public fixtures.

Across those four executions, the suite compiled and ran actual extracted baseline and patched functions at C `-O0` and `-O2` (16 compile/run cases total). The baseline printed its expected failure-reproduction marker. Patched cases passed vote-unwind, callback propagation, invalid-channel no-touch, shared-vote retry quarantine, caller state-bit behavior, PM/open/close guards, and mocked retention checks. Compiler flags were `-std=gnu89 -Wall -Wextra -Werror -Wno-unused-parameter -Wno-unused-function -Wno-unused-but-set-variable` plus each optimization level.

The lifetime harness substitutes shims for Linux VFS/V4L2/VB2, clock framework/provider, pin/refcount behavior, and sensor/CSI operations. Static assertions inspect those pinned sources but do not execute real release, PM, shutdown, reboot, or hardware callbacks. The successful host matrix therefore does not clear F2, establish safe DMA quiescence, or change the earlier camera trial's `not_accepted` result.

## Review disposition

Keep the previous blocked review intact as history. This revision is a meaningful correction to the specific shared-vote retry defect, but **do not treat it as lifecycle-safe, device-tested, accepted, or deployment-authorizing**. The next source correction should address the unknown-ICLK shutdown/reboot route and provide a focused regression for its no-stop/no-cleanup behavior; host evidence alone will still not establish physical clock or DMA state.
