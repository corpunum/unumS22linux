# Touch UI Phase 2: sway with the pixman renderer vs Hyprland on llvmpipe

Trial run on the phone on 2026-10-10 with owner approval. The measurements are in [`results-20261010.jsonl`](results-20261010.jsonl) and the panel screenshots are in [`shots/`](shots/).

## Result

| Metric (same phone, same session, 120 Hz mode, scale 2) | Hyprland 0.56 + llvmpipe | sway 1.12 + wlroots 0.20 pixman |
|---|---|---|
| Panel fps, touch-shell animation (`touch perf`) | **1.4** (frame p50 888 ms) | **62.6** (p50 16.4 ms, p95 20.6 ms) |
| Panel fps, idle desktop | 1.4–1.8: it redraws all the time at ~660–800 ms per frame | 0 (truly idle) |
| Panel fps, 50 Hz terminal output | n/a: already saturated | 25.5 (p50 39 ms), sway CPU 10.9 % |
| Compositor CPU, idle | **98 % of a core** | **0.0–0.2 %** |
| Compositor CPU, animation | 97.8 % | 9.6 % |
| Compositor RSS | 273–309 MB | 37–64 MB |
| Tap → frame, squeekboard key | first flip p50 328 ms, second 613 ms (the response is in one of them) | **p50 24 ms** (max 35 ms) |
| Tap → page open, home tile | 0.35–1.15 s | 38–204 ms (p50 46 ms) |
| System CPU (all 8 cores) | 16.5–16.7 % | 5.4 % idle, 10 % animating |
| BIG-cluster temperature | 38–39 °C idle | 35–36 °C (fell 42 → 35 °C in the first sway minute) |
| Time to first frame on the panel | n/a | 0.46–0.55 s after launch |
| Install cost | – | ~9 MB download, ~15 MB installed (sway, wlroots0.20, libliftoff, vulkan-icd-loader, gnu-free-fonts); no library upgrades |

Panel fps counts scanout framebuffer changes on plane-0 of exynos-drmdpu (DRM debugfs), so it is what the panel really received. The animation is the touch shell's own `perf` overlay, the same QML on both compositors.

**Why Hyprland is slow:** every Hyprland frame repaints the full 1080×2340 buffer through llvmpipe on one thread, which takes about 660–890 ms. Damage tracking is already on (`debug:damage_tracking = 2`), but the GL path still redraws the whole buffer. `LP_NUM_THREADS > 0` produced half-drawn frames on this command-mode panel (owner photo 2026-10-06), and changing it needs a desktop restart, so it was not re-tested. A 60 Hz cap cannot help when the renderer manages 1–2 fps, and Hyprland has no internal render-resolution option. The pixman renderer composites only the damaged regions into linear dumb buffers, which is what this display accepts.

### What works under sway (pixman)

| Area | Status |
|---|---|
| Touch shell | Home, pages, Files and Settings all work over layer-shell ([screenshot](shots/sway-touch-home.png)). |
| Agent cards | Overlay layer works ([screenshot](shots/sway-agent-card.png)). |
| squeekboard | Works through the virtual keyboard and input method. |
| Typing into the chat page | Works after one change: sway does not give an OnDemand layer keyboard focus on a touch tap, so `shell.qml` takes Exclusive focus on the chat page when `SWAYSOCK` is set ([screenshot](shots/sway-chat-typing.png)). Under Hyprland it stays OnDemand. |
| Edge gestures | s22-touchd detects them, because it reads evdev and is compositor-independent. |
| UI routing to the sway session | s22-touchd now finds a sway session through `/run/s22-desktop/session`, so routing works in the profile. During a trial, Hyprland is still preferred. |
| App switcher | Fixed in [sway/](../sway/README.md): it lists windows from wlr-foreign-toplevel under sway. |
| Omarchy bar (`/opt/s22-ui`) | Not started under sway; it is Hyprland-specific. The touch shell's sway status strip replaces it ([sway/](../sway/README.md)). |
| Screen off (Power key) | Fixed in [sway/](../sway/README.md): wlroots failed to re-enable the panel because of a gamma reset on a CRTC that has no gamma table. A one-instruction fixed copy of wlroots is now used, so this is a real DPMS power-off. |

## How the trial works (`p2-trial.sh`)

Hyprland, its supervisor, seatd, OpenUnum and the chroot mounts are **never stopped**. A plain Hyprland exit would make `start-persistent-desktop` tear the session down and exec rescue Weston.

The trial uses the seat instead. seatd's seat is not VT-bound, so seatd supports switching between client sessions:

1. `p2-seat-hold.py` registers as session 2 through libseat (ctypes; it spawns nothing).
2. `wtype -k XF86Switch_VT_2` makes Hyprland call `libseat_switch_session(2)`. seatd drops Hyprland's DRM master and revokes its input devices.
3. sway starts with `LIBSEAT_BACKEND=noop WLR_RENDERER=pixman WLR_DRM_DEVICES=/dev/dri/card1` and opens the devices directly.
4. At the deadline (at most 600 s), on `touch /srv/s22/state/touchui/phase2/stop-request`, or when sway or the holder dies, the watchdog SIGTERMs sway's own process group, then releases the holder. seatd re-activates Hyprland, and a DPMS off/on cycle (the power-key path) makes Hyprland draw a fresh frame. The watchdog runs `setsid`, so it does not depend on SSH.

It was run six times (4.5 min, 1 min, 20 s and three short screen-power probes). Each time Hyprland came back with the same PID and the same clients, and no trial process was left behind. The phone stayed muted, checked before and after every run.

Tools (native root unless noted):

| File | Purpose |
|---|---|
| `p2-trial.sh [SECONDS]` | Bounded trial with automatic return to Hyprland. Set `S22_P2_NO_SWAY=1` for a handover-only probe, and `S22_P2_SHELL=/opt/...` to try staged QML. |
| `p2-seat-hold.py` | Placeholder libseat session (runs in the chroot at `/opt/s22-p2`). |
| `p2-measure.py SECONDS comm,comm` | Panel fps and frame times from debugfs, plus CPU %, RSS and temperatures. Read-only. |
| `p2-tap-latency.py X Y [N]` | Injects taps into `sec_touchscreen` and times the first and second panel flips. |
| `s22-sway-pixman.conf` | Phone sway config. Goes to `/root/s22-sway-pixman.conf` in the chroot. |
| `install-sway-pixman.sh [--rollback]` | Installs sway, the config, the profile-aware supervisor and `s22-desktop-profile`. It does not select the profile. |
| `s22-desktop-profile.sh` | `status`, `sway-pixman`, `hyprland`, or `--rollback`; writes `/etc/s22-desktop`. |

## Switching (prepared, NOT enabled)

`start-persistent-desktop` reads `/etc/s22-desktop` when it starts:

- Missing or unknown: `hyprland`, which is today's behaviour.
- `sway-pixman`: sway on pixman, with the clients foot, pi and squeekboard. The touch shell is supervised by s22-touchd, and `s22-display` handles the Power key through swaymsg (backlight-only off, see the gap above).

If sway fails before it is ready, the supervisor falls back to Hyprland in the same run instead of going to rescue Weston. `--rollback` removes the selector and restores `start-persistent-desktop.pre-desktop-profile`.

Deployment, once the owner approves:

```sh
sh tools/touchui/install-touchui.sh                  # s22-touchd + shell.qml with sway support
install -m 755 tools/hardware/s22-display.sh /srv/s22/hardware/bin/s22-display   # sway Power-key path
sh tools/touchui/phase2/install-sway-pixman.sh       # sway, config, supervisor, selector tool
/srv/s22/hardware/bin/s22-desktop-profile sway-pixman
s22-reboot recovery                                  # desktop restart
# back out: s22-desktop-profile --rollback (or install-sway-pixman.sh --rollback) + reboot
```
