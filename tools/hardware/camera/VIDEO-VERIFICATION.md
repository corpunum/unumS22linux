# Camera video verification — 2026-10-15

Draft PR: #53, branch `s22/camera-video-20261013`.

## Host checkpoint

Inspected `git log -6`, `cf6eea1`, and `1de036e` before deployment. Base: `df6964a`.

Passed:

- `python3 tools/hardware/camera/test_record_safe.py` — 12 tests.
- `python3 tools/hardware/camera/test_s22_camera.py` — 71 tests.
- `cd tools/s22d && ~/.cargo/bin/cargo test` — 99 tests.
- `cd tools/s22d && ~/.cargo/bin/cargo clippy -- -D warnings` — no warnings.

Initial phone check: `camera/status` busy=false, cooldown_s=0; ecm0 and tailscale0 up; mute level and both PCM channels 0. No existing camera userspace D-state process. Recorder not yet deployed at this checkpoint. `s22-phoned` is outside this task and must not be changed.

## Acceptance

Pending backed-up deployment and sequential 2-second/5-second rear recording tests (exposure 20000 us, gain 8). Require gallery AVI entries, rig-side ffprobe and full decode. If the camera client wedges, allow only the requested recovery reboot sequence: max-frames-per-session 7 → 5 → 3, at most two reboots, then stop and document.
