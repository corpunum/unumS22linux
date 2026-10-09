# S22 touch UI, Phase 1

This directory makes the existing Hyprland + Omarchy session on the Galaxy S22 usable by finger. The owner and the agent share one action surface.

The design follows the Phase 1 plan in `openunum-briefs/s22-mobile-ui-research-20261008.md`. Evidence is in `evidence/touchui-20261009/`.

| Piece | Where it runs | What it does |
|---|---|---|
| `s22-touchd.py` → `/srv/s22/hardware/bin/s22-touchd` | native root, root | **UI supervisor:** starts and restarts the touch shell while Hyprland runs.<br>**Gestures:** reads `sec_touchscreen` (`/dev/input/event7`) without grabbing it.<br>**Control socket:** `/run/s22-touch/ctl.sock`, one JSON request per connection, fixed command allowlist. |
| `shell/*.qml` → `/opt/s22-touch/` (chroot) | Arch chroot, own Quickshell instance | The home screen with big tiles and status cards, the pages, notification cards, the confirm dialog, the lock cover, and a frame-time probe.<br>Qt software scene graph; no blur, no animations. |
| `s22-ui.py` → `/usr/local/bin/s22-ui` (chroot) | chroot | The CLI over the socket. The QML, scripts and humans all use it. |
| `power-hook.sh` → `/srv/s22/buttons/hooks/power` | native root | Runs on a short press of the Power key (`s22-buttons` hook):<br>• screen on: lock cover, then DPMS off;<br>• screen off: DPMS on.<br>Falls back to `s22-display toggle` if touchd is down. |
| `../openunum-phone/plugins/s22-ui` | phone OpenUnum | The same actions as agent tools: `ui_open_app`, `ui_home`, `ui_show_card`, `ui_confirm`, `ui_keyboard`, `ui_screen`, `ui_state`, `ui_screenshot`. |

## Home screen

**Tiles:**
- **Agent chat:** typed chat with the phone's OpenUnum in session `touch`.
- **Phone:** read-only view of `s22-phoned`: modem, SIM, network, SMS inbox, calls. Sending stays with the gated agent tools.
- **Camera:** rear still through the reviewed `s22-camera`, behind a hold-to-confirm button.
- **Files:** finger-sized browser with image and text preview.
- **Terminal:** a `foot` shell, with the keyboard revealed.
- **Settings:**
  - Wi-Fi status;
  - brightness slider;
  - volume shown as *pinned at 0* (read-only, with the live mute check);
  - model switch between Luna and the local 0.8B (:8090), as a hold button.

  The model switch writes s22-keepalive's pin file, so keepalive pins the new choice instead of fighting it. Choosing Luna removes the file.

**Status cards:** current model and OpenUnum health, sessions active in the last hour and running missions, battery (level, state, temperature), and the hottest CPU/GPU thermal zone.

**Bottom row:** Pi agent, Apps (the switcher), Keyboard. **Lock** sits at the top right.

## Gestures

Gestures are single-finger edge swipes. A second finger cancels them, because multi-touch belongs to the apps.

| Swipe | Action |
|---|---|
| Bottom edge (bottom 3.5 %), up ≥ 12 % | Home; on the lock cover it unlocks |
| Left or right edge, inward ≥ 10 % | Back: page → tile grid → hide home; from an app it opens home |
| Top edge (top 3 %), down ≥ 12 % | App switcher |

The daemon does not grab the device, so Hyprland still delivers the same touch to whatever is under the finger. An edge swipe that starts on a keyboard key can therefore also press that key.

## Keyboard

`org.gnome.desktop.a11y.applications screen-keyboard-enabled=true`, so squeekboard shows itself for any text-input-v3 field. Qt and GTK do this; foot does not.

The touch shell also reveals the keyboard explicitly in two cases:
- when a text field gets focus;
- 1.5 s after Terminal or Pi agent opens, because terminal focus hides an immediate reveal.

Home hides the keyboard. The home window respects squeekboard's exclusive zone, so it sits above the keyboard.

## Lock and Power

A short Power press with the screen on does this:
1. The lock cover appears: clock, battery, model, pending cards and any pending agent question.
2. `s22-display off` runs: DPMS off, touch disabled.

The next press turns the screen on with the cover still up. Swipe up anywhere by at least 22 % of the height, or use the bottom-edge swipe, to unlock.

This is a soft lock that guards against pocket touches; it is not an authentication lock. `ext-session-lock` is deliberately not used, because a crashed locker would leave Hyprland on its "lock screen died" screen.

`s22-keepalive` is untouched. Its config gains a `touchui` service, which takes effect at keepalive's next start.

## Install, test and roll back (on the phone, native root)

```sh
sh tools/touchui/install-touchui.sh             # backs up to /srv/s22/state/touchui/backup-<ts>/
s22-openunum stop && s22-openunum start         # load the s22-ui OpenUnum tools
sh tools/touchui/install-touchui.sh --rollback  # restores the newest backup, removes everything
```

To try it from inside the chroot:

```sh
s22-ui home
s22-ui open settings
s22-ui card "Title" "Body" --ttl 20
s22-ui confirm "Proceed?" --timeout 60
s22-ui display off
s22-ui screenshot /tmp/s.png
s22-ui perf 10
```

grim is needed for screenshots. On this vendor kernel, plain `pacman` spins forever in the kernel (close_range + Landlock sandbox), and the stuck process cannot be killed. Use:

```sh
chroot /mnt/omarchy-trial /usr/local/libexec/s22-close-range-compat -- pacman -S --needed --disable-sandbox grim wtype
```

## Known limits

- **Hyprland's render rate:** Hyprland on llvmpipe uses one full CPU core all the time, even before this UI. The panel receives only about 2–3 new frames per second: Hyprland's own overlay showed about 680 ms average render time.

  Taps register immediately, but what is drawn lags by up to about 1 s. Fixing this means changing the compositor or renderer: Phase 2 (pixman under phoc/sway) or the GPU work.
- **First frame never painted:** on this stack, Qt's software scene graph does not paint the first frame of a layer surface that is mapped after start-up. Every touch-shell window therefore changes its colour by one step, a few times after it appears (the repaint "kick" in `shell.qml`).

## Omarchy extras (`omarchy/`)

The phone runs the pinned Omarchy Lua config with all of Omarchy's key bindings turned off.

### What we checked

These facts were checked read-only on the phone on 2026-10-09:
- Only workspace 1 is in use.
- `jq`, `uwsm`, systemd, `wl-copy` and `slurp` are missing in the chroot, so almost every `omarchy-*` script fails.
- `omarchy-shell` reports "not running", because the Omarchy shell instance lives at `/opt/s22-ui/shell`, not `$OMARCHY_PATH/shell`. Calling `quickshell ipc -n -p /opt/s22-ui/shell call …` directly works.
- `hyprctl dispatch '<Lua>'` is how Omarchy's own scripts dispatch on this Hyprland, 0.56.

### What we built and what we left out

Each piece of an "Omarchy action bridge" was kept only if it is useful and nothing simpler already does the job:

| Piece | Decision | Why |
|---|---|---|
| Agent skill `s22-omarchy-actions` (`omarchy/SKILL.md`) | **keep** | The agent already has `shell_run`. What it lacks is knowing which commands work here:<br>• the Lua `hyprctl dispatch` forms;<br>• the direct shell-IPC path;<br>• the list of things that cannot work.<br>Instruction-only, so no new code path. |
| Curated hardware-keyboard bindings (`omarchy/omarchy-keyboard.lua`, `s22-kbd-bindings`) | **keep, off by default** | Useful when a Bluetooth or USB keyboard is attached. It uses Omarchy's combos and holds 39 binds, all hyprctl dispatchers or shell IPC, with no `omarchy-*` scripts. It is loaded at runtime with `hyprctl eval`, so Hyprland's config is not edited. |
| OpenUnum `omarchy_actions` / `omarchy_run` tools | drop | They duplicate `shell_run` plus the skill. The existing `s22-ui` tools already cover home, apps, confirm and screen. |
| Catalog generator and executor over all ~205 bindings | drop | Fewer than a third of the bindings can work on the phone, and the skill lists those. A generator would mostly catalogue broken entries. |
| Multi-finger gestures (3-finger workspace/close, long-press, pinch) | drop | Only one workspace is used. The edge swipes already cover home, back and the switcher. Closing a window is in the switcher and the agent. Multi-finger input belongs to the apps. |
| "Commands" tile and palette in the touch shell | drop | The useful targets are already tiles (Terminal, Apps). It would cost an extra llvmpipe-rendered page for little gain. |

### Install and use

Install on the phone, on the native root. The installer is idempotent, backs up existing files first, and binds nothing:

```sh
sh tools/touchui/omarchy/install-omarchy-extras.sh
sh tools/touchui/omarchy/install-omarchy-extras.sh --rollback
```

Inside the chroot, for a hardware keyboard:

```sh
s22-kbd-bindings status
s22-kbd-bindings on
s22-kbd-bindings off
```

After a Hyprland restart, run `s22-kbd-bindings on` again. The flag file survives the restart, but the runtime binds do not.
