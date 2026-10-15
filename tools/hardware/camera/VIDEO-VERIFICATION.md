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

## Deployment checkpoint

AArch64 musl release build and `cargo fmt --check` passed. Installed with phone installer (`--binary /tmp/s22d-camera-safe`). s22d SHA-256: `36a615c40ed411cc38a3a5d49c864d0268c593896cbede0bfdddf7245b2072d4`.

Backup: `/srv/s22/state/s22d/camera-video-backup-20261015T080132Z` (original s22d, s22-camera, keepalive config). Original s22d hash `ab0ee8fd822aa1d9fa03d8787907d7cff72324eba925de91c8b88b31abed99f3`. Existing s22-camera matches branch source and was left unchanged; identical sibling `s22-camera.py` and new `record-safe.py` installed for imports. Script hashes: s22-camera.py `e0f0d3f776bd8d15906e6a338b925ce9c79e074b4deb25ce9127359223ff2437`; record-safe.py `0d9385cee964c6fda619887f7887d60df9fb4942190ba869d0595e811cc38cc9`.

Post-deploy camera busy=false/cooldown_s=0, API answering, mute=0 and both PCM channels=0. Rollback: `sh /srv/s22/hardware/install-s22d.sh --rollback` (and remove the newly added Python siblings if rolling scripts back).

## First 2-second phone test (7-frame session budget)

POST rear/seconds=2/exposure_us=20000/gain=8 returned HTTP 200 in 13.85 seconds: 10 frames, 2 sessions, effective_fps=0.7315301433. No wedge; mute before/after=0. Output `vid-1792051326-rear.avi`, 603570 bytes, copied to `~/openunum-briefs/s22-camera-sample/`. Rig ffprobe: MJPEG 512x384, 5 fps, 2.000 seconds, 10/10 decoded frames; full ffmpeg null decode passed.

Two acceptance issues observed (not hidden): s22d response incorrectly reports fps=30 although file/default recorder fps=5; phone `/api/assets/list` excludes AVI although the AVI exists in the generated directory. Investigating before declaring gallery acceptance.
