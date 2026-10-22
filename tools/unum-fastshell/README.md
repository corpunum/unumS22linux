# unum-fastshell: the S22's fast touch shell

A single static Rust binary (aarch64 musl, about 1.3 MB) that runs on the phone's native root. It replaces the Quickshell touch shell as the default. sway-pixman keeps running underneath. Phase 2 of the [fastui spike](../fastui/README.md).

Measurements and screenshots are in [`evidence/fastshell-20261021/`](../../evidence/fastshell-20261021/README.md).

## How it works

| Part | What it does |
|---|---|
| Display | Opens `card1` (exynos-drmdpu) **through seatd** as its own seat session, next to sway (session 1). Draws into two KMS dumb buffers and page-flips them. DSI-1 runs at 1080x2340@120. |
| Renderer | CPU, immediate-mode, with damage tracking. Every primitive is clipped to the damaged rectangles, and only those are copied to the back buffer. Rounded rectangles are anti-aliased.<br>Text is Noto Sans (Latin + Greek subset, vendored, OFL-1.1, see `fonts/OFL.txt`), rasterised with `ab_glyph` into a glyph cache.<br>Lists (chat, files, phone, text viewer) scroll kinetically. A scroll step moves the pixels in place and redraws only the newly exposed strip. |
| Touch | Reads `sec_touchscreen` evdev without grabbing it. Implements its own edge swipes with the same thresholds as s22-touchd:<br>• bottom edge up: home, or unlock;<br>• left/right edge inward: back;<br>• top edge down: apps. |
| Keyboard | Native on-screen keyboard: English QWERTY, Greek (with a tonos dead key) and a symbol layer. Native because squeekboard is a Wayland client of sway, and sway is inactive while the shell owns the panel. squeekboard is still used under sway for foot. |
| Pages | **Home:** live cards for OpenUnum model/health, sessions in the last hour and running missions (`:18880`), battery and thermal (sysfs); app tiles; Talk / Apps / Lock; last reply.<br>**Agent chat:** session `touch`. `POST /api/chat`, then the `/api/chat/stream` SSE (content deltas, tool activity, `ask_user` options). Stop button.<br>**Phone:** read-only view of s22-phoned `:8095`.<br>**Camera:** Rear/Front toggle; hold-to-capture through touchd `camera_capture` (`sensor`), which meters, takes a 4-frame burst and develops it with `s22-develop` (software ISP); the processed preview plus a line with exposure, gain, frames and the gallery JPEG name.<br>**Files:** browses the chroot's `/root`; text and PNG preview.<br>**Settings:** network, brightness slider (touchd), model switch (touchd, hold), screen off, sway desktop, back to the classic shell (hold).<br>**Apps:** the switcher. **Lock** cover. |
| Foreign apps | **Terminal** focuses (or starts) `foot` under sway, makes sure squeekboard runs and shows it, then switches the seat to sway.<br>Any edge swipe under sway goes through s22-touchd as `gesture`, and the shell takes the panel back with `wtype -k XF86Switch_VT_<n>`. |
| Status bar | Clock, health dot, a **5G / 4G** pill from s22-phoned `/status` (every 20 s), battery, thermal, link. 5G = `radio.nr_nsa`, or `nr_last_seen` within 120 s (NSA keeps `act` = lte). |
| Agent protocol | `shell.sock` in s22-touchd's run dir, the same JSON-line protocol as unum-shell: `home back switcher open card confirm confirmResult lock unlock keyboard state perf`, plus `display`, `gesture`, `ping` and `send`.<br>Confirm answers go to `confirm/<id>.json`, so `s22-ui`, the OpenUnum `s22-ui` plugin and s22d keep working unchanged. |
| Voice | **Talk** runs `/srv/s22/hardware/bin/s22-assistant` exactly like the button hook: fixed recording window, ASR, session `voice`, spoken answer. The last `assistant.jsonl` entry is then shown as a card. |
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
cargo test --offline --release                     # 37 tests: button chords/holds/double press + level + Settings section, screen-timeout policy, SSE/chunked parsing, touchd protocol, confirm file, keyboard, gestures, 5G badge, camera line
cargo run --release -- dump /tmp/shots             # offscreen PNG of every screen
flock ~/.cache/rig-heavy.lock env CARGO_TARGET_AARCH64_UNKNOWN_LINUX_MUSL_LINKER=rust-lld \
  cargo build --offline --release --target aarch64-unknown-linux-musl
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
