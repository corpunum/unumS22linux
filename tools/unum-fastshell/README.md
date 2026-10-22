# unum-fastshell: the S22's fast touch shell

A single static Rust binary (aarch64 musl, about 1.3 MB) that runs on the phone's native root. It replaces the Quickshell touch shell as the default. sway-pixman keeps running underneath. Phase 2 of the [fastui spike](../fastui/README.md).

Measurements and screenshots are in [`evidence/fastshell-20261021/`](../../evidence/fastshell-20261021/README.md); phase 2 (live camera, keyboard feedback, Settings through the device API, Home Agent card) in [`evidence/shell-phase2-20261022/`](../../evidence/shell-phase2-20261022/README.md).

To run it on another Linux phone (postmarketOS, Mobian, Arch Linux ARM), see [PORTING.md](PORTING.md): config file, auto-detection of the display, touchscreen, battery and thermal sensors, and what needs the S22 services.

## How it works

| Part | What it does |
|---|---|
| Display | Opens `card1` (exynos-drmdpu) **through seatd** as its own seat session, next to sway (session 1). Draws into two KMS dumb buffers and page-flips them. DSI-1 runs at 1080x2340@120. |
| Renderer | CPU, immediate-mode, with damage tracking. Every primitive is clipped to the damaged rectangles, and only those are copied to the back buffer. Rounded rectangles are anti-aliased.<br>Text is Noto Sans (Latin + Greek subset, vendored, OFL-1.1, see `fonts/OFL.txt`), rasterised with `ab_glyph` into a glyph cache.<br>Lists (chat, files, phone, text viewer) scroll kinetically. A scroll step moves the pixels in place and redraws only the newly exposed strip. |
| Touch | Reads `sec_touchscreen` evdev without grabbing it. Implements its own edge swipes with the same thresholds as s22-touchd:<br>• bottom edge up: home, or unlock;<br>• left/right edge inward: back;<br>• top edge down: apps. |
| Keyboard | Native on-screen keyboard: English QWERTY, Greek (with a tonos dead key) and a symbol layer. Native because squeekboard is a Wayland client of sway, and sway is inactive while the shell owns the panel. squeekboard is still used under sway for foot.<br>Every key press shows the key enlarged in a popup above the finger (always on) and sends a haptic `tick`. |
| Haptics | One non-blocking datagram to s22-haptic's `/run/s22-haptic.sock` (`src/haptic.rs`, [docs/S22_HAPTICS.md](../../docs/S22_HAPTICS.md)): `tick` on key-down, on the physical buttons' actions (volume, Power lock, chords, push-to-talk), on the shutter and on Settings toggles; `buzz` on the Agent card's STOP. Nothing is sent when vibration is off (`/srv/s22/state/haptics/config.json`, re-read every 10 s and after the Settings toggle). |
| Pages | **Home:** status chips (model/health, sessions and running missions from `:18880`, battery, thermal); the **Agent card** (below); app tiles; Apps / Lock.<br>**Agent chat:** session `touch`. `POST /api/chat`, then the `/api/chat/stream` SSE (content deltas, tool activity, `ask_user` options). Stop button.<br>**Phone:** read-only view of s22-phoned `:8095`.<br>**Camera** (`src/camera.rs`): a live viewfinder as soon as the page opens. The shell starts `s22-campreview stream --until-signal` as a child while the page shows (unlocked, panel on) and stops it (SIGTERM, full teardown) when it does not; it maps the child's shared-memory triple buffer and blits the newest 1020x766 (rear) / 912x684 (front) frame rotated and mirrored to 1080x1438. Shutter: SIGUSR1, the child writes the next 4 raws to `/tmp/s22-cam` while it keeps streaming, `s22-develop develop --stack` runs on the X2 core in the background, the thumbnail appears bottom-left (tap: the photo full size); the JPEG goes to the gallery. VIDEO mode: SIGUSR2 starts/stops an MJPEG AVI (382x510, half the preview, its own encoder thread) in the gallery; there is no H.264 encoder on the phone. Front/back switch restarts the child (~0.4 s). An autosleep inhibit file (`/run/s22-autosleep/inhibit.d/fastshell-camera`) is held while it streams or develops. Orientation per sensor: `<state_dir>/camera.json` (`{"rear":{"rotate":90},"front":{"rotate":270,"mirror":true}}`), Settings → "Camera … — hold to rotate".<br>**Files:** browses the chroot's `/root`; text and PNG preview.<br>**Settings** (`src/device.rs`, scrolls): toggles for Wi-Fi, Bluetooth, mobile data, calls (the s22d gate), vibration and auto-sleep; volume slider + mute, brightness slider, screen timeout (unlocked / locked), all through s22d's typed device API (`POST /v1/device/<id>` on `/run/s22d.sock`, [tools/s22d/DEVICE_API.md](../s22d/DEVICE_API.md)). Every row shows the state s22d read back (`device.status`, `volume.get`, `screen_timeout.get` on open, and again after each change) or the refusal (e.g. `would_strand`). Then the model switch (touchd, hold), screen off, sway desktop, camera orientation, back to the classic shell (hold), the Buttons section.<br>**Apps:** the switcher. **Lock** cover. |
| Foreign apps | **Terminal** focuses (or starts) `foot` under sway, makes sure squeekboard runs and shows it, then switches the seat to sway.<br>Any edge swipe under sway goes through s22-touchd as `gesture`, and the shell takes the panel back with `wtype -k XF86Switch_VT_<n>`. |
| Status bar | Clock, health dot, a **5G / 4G** pill from s22-phoned `/status` (every 20 s), battery, thermal, link. 5G = `radio.nr_nsa`, or `nr_last_seen` within 120 s (NSA keeps `act` = lte). |
| Agent protocol | `shell.sock` in s22-touchd's run dir, the same JSON-line protocol as unum-shell: `home back switcher open card confirm confirmResult lock unlock keyboard state perf`, plus `display`, `gesture`, `ping` and `send`.<br>Confirm answers go to `confirm/<id>.json`, so `s22-ui`, the OpenUnum `s22-ui` plugin and s22d keep working unchanged. |
| Voice | **Agent card** (Home, `src/agent.rs`): hold it to speak; `s22-assistant --card listen` records while held (the earcon only if nothing plays, else a haptic buzz; a tap records the fixed 6 s window, like the old Talk button) and prints the transcript. On release the agent takes over: `s22-assistant --card turn --text …` (session `voice`, fillers, spoken thinking, the -16 LUFS voice) prints its states, and the card shows "Thinking…", "Running a tool: <name>", "Speaking…" and the reply. Holding again while it works records the next utterance and queues it ("queued (n)"); turns run in order. **STOP** kills the turn's process group (s22-assistant, fillers, s22-say and the player) and calls `POST /api/chat/cancel` for `voice`, and drops the queue. Confirm cards from s22d (calls/SMS to numbers off the allowlist) render on top as before. The chat page's mic button still runs the classic one-shot `s22-assistant`. |
| Screen timeout | No touch for 60 s while unlocked: lock + panel off (the Power-press path). Lock cover lit with no touch for 15 s: panel off. Any touch or touchd call resets the clock. Never while the chat is streaming, an agent question/confirm is up, the Camera page is open, a Talk turn runs, or `/run/s22-say.lock` is held (the countdown restarts when the reason ends). Config `/srv/s22/state/fastshell/screen-timeout.json` `{"unlocked_s":60,"locked_s":15}`, 0 = never, re-read every 10 s; Settings has two buttons cycling 0/15/30/60/120/300 s. |
| Buttons | Reads the key nodes itself (found by key capabilities: `gpio_keys` = Volume up, the PMIC key node = Volume down + Power), without a grab, with CLOCK_MONOTONIC timestamps. A pure recogniser (`src/buttons.rs`, unit-tested) turns presses into actions:<br>• Volume up / down: speaker level ±1 **on key-down**, with an on-screen level bar. The level is s22-say's speech level (`/run/s22-say.level`, default 4), kept across s22-keepalive's mute guard; the amps (`Left/Right Digital PCM Volume`) are written by one ALSA ioctl only while something plays, so the resting state stays muted;<br>• hold Volume up 1 s: push-to-talk through `hooks/assistant` (`S22_PTT=hold`, release note in `ptt-release`, as s22-buttons); the step from the press is undone;<br>• hold Volume down 1 s: mute / unmute;<br>• Power: lock + panel off on release; with the panel off it wakes on key-down;<br>• Power twice (≤ 400 ms): camera;<br>• Power + Volume down (second key within 400 ms, any order): screenshot of the shell's own canvas to the OpenUnum gallery (`/root/.openunum/assets/generated/screenshot-<unix>.png`, also in Files) with a white flash; `grim` when sway shows;<br>• Power + Volume up: keep the screen on (toggle);<br>• any key with the panel off wakes it.<br>Hand-over: the shell writes its pid to `/run/s22-fastshell.buttons`; s22-buttons then only logs and keeps **Power held ≥ 1 s alone → `hooks/power-long`** (the safety path, independent of the shell). A Power press that overlapped a volume key never runs power-long. Settings → Buttons lists the bindings; its toggle (hold) hands the keys back to s22-buttons. |
| Screen power | s22-touchd's `display`/`power` (the Power key) is forwarded to the shell. It draws the lock cover, then sets DPMS on its own DRM fd, which takes the CRTC inactive and powers the panel off. While sway is showing, the shell answers `delegate` and `s22-display` does it as before. |

## Switching and the way back

| Action | Command |
|---|---|
| Select | `s22-desktop-profile fastshell`<br>`/etc/s22-desktop` stays `sway-pixman`, because sway is the base compositor and the recovery target. The touch shell choice lives in `/srv/s22/state/touchui/ui-backend`, and s22-touchd switches within about 2 s. |
| Back to the classic shell | `s22-desktop-profile sway-pixman`, over SSH. |
| Back, on the phone | Hold **Power for at least 1 s**. `hooks/power-long` selects Quickshell and stops `unum-fastshell` by its exact PID; it does not depend on the shell itself. |
| Back, from the shell | Settings → "Classic shell (Quickshell) — hold". |

Automatic fallbacks:
- s22-touchd pings the shell every 10 s and stops it (exact PIDs) after 3 failed pings.
- 6 starts in 10 minutes (clean SIGTERM exits do not count) switch to Quickshell for 15 minutes.
- If the shell dies, its seatd connection closes and seatd gives the panel back to sway.

## Build, test, install

```sh
export PATH=$HOME/.cargo/bin:$PATH
cd tools/unum-fastshell
cargo test --offline --release                     # button chords/holds/double press + level + Settings section, config + hardware detection, screen-timeout policy, SSE/chunked parsing, touchd protocol, confirm file, keyboard, gestures, 5G badge, camera line
cargo run --release -- dump /tmp/shots             # offscreen PNG of every screen
flock ~/.cache/rig-heavy.lock env CARGO_TARGET_AARCH64_UNKNOWN_LINUX_MUSL_LINKER=rust-lld \
  cargo build --offline --release --target aarch64-unknown-linux-musl
# phase 2 also needs, next to the shell: /srv/s22/hardware/bin/s22-campreview (tools/hardware/camera/s22-campreview,
# same cross-build), s22-develop, s22-assistant with --card (tools/hardware/s22-assistant.py), s22d with /v1/device.
# Swap single files (backup, write .new, mv, SIGTERM the shell's exact pid; touchd restarts it).
# stage: unum-fastshell, install-fastshell.sh, power-long-hook.sh,
#        ../touchui/s22-touchd.py, ../touchui/phase2/s22-desktop-profile.sh
#        to /srv/s22/state/fastshell/tools/ on the phone (NOT /root: the root fs has ~20 MB free)
sh install-fastshell.sh --activate                 # backup, install, restart touchd, select fastshell
sh install-fastshell.sh --rollback                 # Quickshell + restore the newest backup
```

On-phone tools (native root):

| Tool | Use |
|---|---|
| `fs-tap-latency.py X Y N` | Injected taps. Times touch-down → first fb change (highlight) and touch-up → fb change (page). Add `--touchd` for Quickshell. |
| `fs-inject.py tap\|swipe ...` | Injected taps and swipes. |
| `fs-key.py MS` | Injects a Power press into `sec-pmic-key`; s22-buttons then runs its real hooks. |
| `fs-button-latency.py vol up\|down \| power \| combo power+voldown` | Injects key presses and times key-down/up → amp control, speech level, playback start, display state, CRTC active, plane fb change. |
| `unum-fastshell bench N` | Offscreen full-frame timings per page. |
| `fs-ctl.py FN [ARG…]` | One call on `shell.sock` without touchd's allow-list: `screenshot half` (540x1170 PNG of the shell's own canvas into the gallery), `agent_say TEXT` (an utterance for the Agent card, queued when busy), `perf` (now with `camera`: viewfinder fps on the panel, publish→flip, shutter→thumbnail), `open camera`, `state`. |
| `s22-touchd ui perf` | The shell's own touch → flip-complete and render/copy statistics. |

Logs:
- `/mnt/omarchy-trial/run/s22-touch/shell.log` (the shell's stderr);
- `/srv/s22/state/fastshell/latency.jsonl` (every press/page/key, touch → flip).

## Known gaps

- Not tested on the phone:
  - the Talk button: it plays audio, and another worker was running audio loopback tests;
  - the model switch and the "classic shell" button from inside the shell (the profile command and the Power long-press were tested);
  - real finger latency, photon time and power draw.
- No app switcher thumbnails: Apps is a page list plus "Sway desktop".
- Single-line text field, cursor only at the end, no text selection. No emoji glyphs (the font is Latin + Greek).
- Render time on the phone is about 8 ms p50 live, against about 1 ms offscreen. That is probably CPU clock ramp-up from idle; it was not investigated.
- A Samsung Vulkan producer for camera/video on a separate plane (from the spike's recommendation) is not built.
