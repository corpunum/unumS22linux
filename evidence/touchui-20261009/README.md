# S22 touch UI, Phase 1: device evidence (2026-10-09)

Boot id `25d961f5-…` (the lead2 boot; no reboot). Every screenshot was taken on the phone with `grim` through Hyprland screencopy, at 1080x2340.

Safety:
- The phone stayed muted throughout: volume level 0 and both CS35L41 `Digital PCM Volume` at 0, checked before and after.
- No process was killed by pattern; only exact PIDs were used.
- s22-keepalive, s22-phoned, the :8090 GPU server and OpenUnum stayed up.
- OpenUnum restarted once, with `s22-openunum stop/start`, to load the `s22-ui` plugin. The model stayed `openai/gpt-6-luna`. No OpenUnum source files were copied.

Code: `tools/touchui/` (see its README) and `tools/openunum-phone/plugins/s22-ui/`.

## What is live

| Piece | State |
|---|---|
| `s22-touchd` (native root) | Running. It supervises the touch shell, runs gestures from `/dev/input/event7` (ABS 0..4095 on both axes) and serves the control socket. It is in the keepalive config as service `touchui`, which takes effect at keepalive's next start. |
| Touch shell (`/opt/s22-touch`, its own Quickshell) | Running. It is hidden until it is summoned by a gesture, the CLI or the agent. |
| `s22-ui` CLI (chroot) | Installed. |
| Power hook (`/srv/s22/buttons/hooks/power`) | Installed. |
| squeekboard a11y auto-show | `screen-keyboard-enabled=true` |
| OpenUnum `s22-ui` plugin | Loaded: `plugin_loaded name=s22-ui`, with the plugin base taken from `/opt/openunum/src`. |
| grim and wtype (chroot) | Installed. |

## Verified on the device

Taps and swipes were injected into the real touchscreen evdev node. That is the same input path as a finger, but it is not a finger.

| Check | Result |
|---|---|
| Bottom-edge swipe up → home; left and right edge → back; top-edge swipe down → switcher | All four OK (`measure/touchd.log` gesture events). |
| Tap the Settings tile, then the ‹ Back button | Page `settings`, then back to the grid. |
| Tap the chat text field → keyboard appears automatically → tap `h`, `i`, then Send | Keyboard shown and the home window shrank above it (03). "hi" was sent; Luna replied "Hi! What can I help you with?" (04). |
| `s22-ui card` and `s22-ui confirm`, tapping Yes | Card shown (10). The confirm returned `{"answer": "yes"}` (11). |
| The agent drives the UI: an OpenUnum turn calls `ui_show_card` then `ui_confirm`, and a synthetic tap answers Yes | The tool runs returned `ui_show_card ok`, then `ui_confirm answer yes`. The agent replied "The answer was yes." (12). |
| Power hook press 1 → lock cover + DPMS off; press 2 → on; swipe up → unlocked | 0.65 s for lock plus off and 3.0 s for on (`s22-display`'s two repaint passes). The swipe-up unlock works, and so does the bottom-edge swipe (13). |
| Every page renders | 01–09 and 14. |
| `install-touchui.sh --rollback` | Removed everything; keepalive config and gsettings restored, only the Omarchy Quickshell left. The reinstall then worked. |

## Measurements

The 5-minute windows are in `measure/`, captured with `cpumeas.sh` (per-PID utime+stime deltas) and `fbrate.py` (DRM debugfs plane-0 framebuffer changes, sampled every ~5 ms).

| Metric | Value |
|---|---|
| Touch shell CPU, hidden / home shown (300 s each) | **0.0 % / 0.6 %** of one core |
| Touch shell RSS, hidden / shown | 56 / 66 MB |
| s22-touchd CPU / RSS | 0.0 % / 15 MB |
| Hyprland CPU, before the touch UI / home hidden / home shown | 97.8 % / 97.0 % / 98.2 % of one core (pre-existing; see below) |
| Client frame-callback rate (`s22-ui perf 10`, FrameAnimation) | 62.5 fps, mean 16.0 ms, p50 16.2 ms, p95 16.6 ms, max 23.3 ms |
| **Real scanout updates** (plane-0 framebuffer changes) | **≈3.2 per second**, both idle and animating |
| Hyprland debug overlay | 1 FPS, average render time 680 ms (15) |
| Gesture → UI action (touchd → `quickshell ipc`) | ~220 ms |
| MemAvailable at the start / after install and tests | 1.38 GB / 1.25 GB |

**The big finding:** before this work, Hyprland on llvmpipe (`LP_NUM_THREADS=0`) already used one core continuously. It renders a frame in about 0.3–0.7 s, so the panel shows only about 2–3 new frames per second. The 60 Hz frame callbacks clients get are not frames on the glass.

The touch UI adds almost nothing to this, but every visual reaction (a tap highlight, a page change) can lag by up to about 1 s. Disabling colour management (`render:cm_enabled=false`) did not change it, so it was restored.

Fixing this needs a cheaper compositor path: Phase 2 (phoc or sway with `WLR_RENDERER=pixman`), or the GPU/RADV work.

## Problems found and handled

- **Qt software scene graph black-surface bug.** A layer surface that is mapped after Quickshell starts gets its first frame unpainted (reproduced with a 10-line test shell). The workaround is to change the window colour by one step five times, every 700 ms, after it becomes visible (`kick` in `shell.qml`). The window must be opaque for this to work.
- **pacman hangs in the kernel.** Plain `pacman -S`, and even `--disable-sandbox`, spins forever in the kernel (`close_range`). Two such processes, PIDs 26277 and 27720, are unkillable and stay until the next reboot. They are confined to CPU 0 at nice 19.

  `s22-close-range-compat -- pacman -U --disable-sandbox …` worked. The installer prints this recipe.
- **Camera client.** `/srv/s22/hardware/bin/s22-camera` was the pre-review client (sha 13863c83). It is now the reviewed one, sha 3f1375f6, and the old one is kept as `.pre-review`. Camera results are in `openunum-briefs/s22-camera-20261009/`.

## Owner physical test (still needed)

1. **Finger:**
   - Swipe up from the very bottom edge → home appears.
   - Tap each tile.
   - Swipe in from the left or right edge → back.
   - Swipe down from the top edge → Open apps.
2. **Keyboard:** Agent chat → tap the text field → the keyboard appears. Type, then Send.
3. **Power key:**
   - Short press → the lock cover appears, then the screen goes off.
   - Press again → the screen comes on with the lock cover.
   - Swipe up → unlocked.

   Also check the panel shows no stale or half-drawn frame.
4. **Settings:** drag the brightness slider; the volume reads "Muted (pinned at 0)".

   Do not use the model buttons unless you want the switch: they hold-to-confirm and persist through keepalive's pin.
5. **Terminal tile:** a foot shell opens and the keyboard follows after about 1.5 s.

**Rollback** (phone, native root): `sh /srv/s22/state/touchui/repo/tools/touchui/install-touchui.sh --rollback`, then `s22-openunum stop && s22-openunum start` to unload the plugin.
