---
name: s22-omarchy-actions
description: Window, workspace and Omarchy-shell actions that actually work on the S22 phone's Hyprland session, run with shell_run (hyprctl dispatch / quickshell ipc). Use when asked to switch workspace, close/fullscreen/float a window, focus or move windows, open the Omarchy menu or dismiss notifications on the phone.
---

# Omarchy actions on the S22 phone

The phone runs the pinned Omarchy Lua config for Hyprland 0.56, with **all Omarchy key bindings turned off**. You cannot press them, but you can run what they do with `shell_run`. This list comes from the pinned bindings in `/opt/omarchy-source/default/hypr/bindings/*.lua`, checked against the phone on 2026-10-09.

**Prefer the `s22-ui` tools** for home, open-app, cards, confirm, keyboard and screen, and **prefer `s22-*` tools** for audio, brightness, display power and camera. Use this skill only for window, workspace and Omarchy-shell actions.

## Environment (prefix every command)

```sh
export XDG_RUNTIME_DIR=/run/user/0 WAYLAND_DISPLAY=wayland-1
export HYPRLAND_INSTANCE_SIGNATURE=$(ls -t /run/user/0/hypr | head -1)
```

Read state first. These are read-only:
- `hyprctl -j activeworkspace`
- `hyprctl -j clients`
- `hyprctl -j workspaces`

## Hyprland actions: `hyprctl dispatch '<Lua>'`

This Hyprland takes Lua dispatchers. The legacy form (`hyprctl dispatch workspace 2`) is only a fallback, the way Omarchy's own scripts use it: `hyprctl dispatch '<lua>' || hyprctl dispatch <legacy>`.

| Omarchy binding | Action | Lua dispatch | Legacy fallback |
|---|---|---|---|
| SUPER+1…0 | Switch to workspace N | `hl.dsp.focus({ workspace = "2" })` | `workspace 2` |
| SUPER+TAB / SHIFT+TAB | Next / previous existing workspace | `hl.dsp.focus({ workspace = "e+1" })` / `"e-1"` | `workspace e+1` |
| SUPER+CTRL+TAB | Former workspace | `hl.dsp.focus({ workspace = "previous" })` | `workspace previous` |
| SUPER+SHIFT+N | Move window to workspace N | `hl.dsp.window.move({ workspace = "2" })` | `movetoworkspace 2` |
| SUPER+SHIFT+ALT+N | Move window silently | `hl.dsp.window.move({ workspace = "2", follow = false })` | `movetoworkspacesilent 2` |
| SUPER+S | Toggle scratchpad | `hl.dsp.workspace.toggle_special("scratchpad")` | `togglespecialworkspace scratchpad` |
| SUPER+ALT+S | Window to scratchpad | `hl.dsp.window.move({ workspace = "special:scratchpad", follow = false })` | `movetoworkspacesilent special:scratchpad` |
| SUPER+arrows | Focus left/right/up/down | `hl.dsp.focus({ direction = "l" })` (`r` `u` `d`) | `movefocus l` |
| SUPER+SHIFT+arrows | Swap window | `hl.dsp.window.swap({ direction = "l" })` | `swapwindow l` |
| ALT+TAB | Next window | `hl.dsp.window.cycle_next()` | `cyclenext` |
| SUPER+F | Fullscreen (toggle) | `hl.dsp.window.fullscreen({ mode = "fullscreen" })` | `fullscreen 0` |
| SUPER+ALT+F | Full width (maximize) | `hl.dsp.window.fullscreen({ mode = "maximized" })` | `fullscreen 1` |
| SUPER+T | Toggle floating | `hl.dsp.window.float({ action = "toggle" })` | `togglefloating` |
| SUPER+J | Toggle split | `hl.dsp.layout("togglesplit")` | `layoutmsg togglesplit` |
| SUPER+P | Pseudo-tile | `hl.dsp.window.pseudo()` | `pseudo` |
| SUPER+G | Toggle group | `hl.dsp.group.toggle()` | `togglegroup` |
| SUPER+code:20/21 | Resize active window | `hl.dsp.window.resize({ x = -100, y = 0, relative = true })` | `resizeactive -100 0` |
| **SUPER+W** | **Close window (destructive)** | `hl.dsp.window.close()` | `killactive` |

Before you close a window, call `ui_confirm` and go ahead only on `"yes"`. To close a window that is not focused, use `hl.dsp.window.close({ window = "address:0x…" })` with the address from `hyprctl -j clients`.

On a phone with one monitor, the monitor actions are no-ops: focus or move to another monitor, and moving a workspace to a monitor. Leave the display layout alone. Do not use `monitor-scaling`, the internal-display toggle, or `hyprctl keyword monitor`: they can blank or shrink the only screen.

## Omarchy shell (bar, menu, notifications): `quickshell ipc`

The Omarchy shell runs as `/opt/s22-ui/shell`. **`omarchy-shell …` fails here with "omarchy-shell is not running"**, because it looks for the shell under `$OMARCHY_PATH/shell`. Call the shell directly instead:

```sh
quickshell ipc -n -p /opt/s22-ui/shell call -- <target> <function> [args]
quickshell ipc -n -p /opt/s22-ui/shell show          # list targets and functions (read-only)
```

| Omarchy binding | Action | Call |
|---|---|---|
| SUPER+SPACE | Omarchy menu | `shell toggle omarchy.menu '{"menu":"root"}'` |
| SUPER+ALT+SPACE | Apps menu | `shell toggle omarchy.menu '{"menu":"apps"}'` |
| SUPER+CTRL+E | Emojis | `shell toggle omarchy.emojis '{}'` |
| SUPER+CTRL+V | Clipboard manager | `shell toggle omarchy.clipboard '{}'` |
| SUPER+CTRL+A / SUPER+CTRL+P / SUPER+CTRL+ALT+D | Audio / Power / Calendar panels | `shell toggle omarchy.audio '{}'` (`omarchy.power`, `omarchy.clock`) |
| SUPER+comma | Dismiss last notification | `notifications dismissOne` |
| SUPER+SHIFT+comma | Dismiss all notifications | `notifications dismissAll` |
| SUPER+ALT+comma | Invoke last notification | `notifications invokeLast` |
| SUPER+SHIFT+ALT+comma | Notification history | `notifications showHistory` |
| SUPER+CTRL+comma | Do-not-disturb toggle | `notifications toggleDnd` |
| SUPER+CTRL+1…9 | Bar panel N (right side) | `shell togglePanelAt right 1` |

The menu opens, but many of its entries run `omarchy-*` scripts that cannot work here (see below). Use the menu for the owner to look at, not as your own way to act.

## What does NOT work on the phone (don't try)

Rendering is CPU-only, and the chroot has no systemd, `uwsm`, `jq`, `wl-copy`, `slurp`, `brightnessctl`, `wpctl`, `playerctl`, `hyprsunset`, `btop`, `nvim` or `tmux`. So these do not work:

- **Almost every `omarchy-*` script.** They need `jq`, `uwsm-app` or `systemctl --user`. This covers close-all, window pop, width, transparency, workspace-layout toggle, notification-send, keybindings menu, screenshot, OCR, screen recording, reminders, nightlight, idle, monitor scaling, and all `omarchy-launch-*` except the phone's own `omarchy-launch-tui` adapter.
- **Browsers and web apps,** which Omarchy launches with SUPER+SHIFT+B, ChatGPT, YouTube, WhatsApp and the like. They are not installed and are far too heavy for llvmpipe.
- **The Omarchy audio, volume, media and brightness keys.** The phone is **muted on purpose** and audio is pinned at 0 by `s22-keepalive`. Never change volume.
- **Omarchy's lock** (`omarchy-system-lock`). For the soft lock, use `ui_screen` with `lock`.
- **Copy, paste and cut** (SUPER+C/V/X). These are Lua closures in Omarchy. The only stand-in is key synthesis into the focused window, `wtype -M ctrl c -m ctrl`, and in foot it is `ctrl+shift+c`. Use it only if the owner asks.
- **Terminal** (SUPER+RETURN). `omarchy-launch-terminal` needs uwsm. Use `ui_open_app` with `terminal`, or run `foot`.

## Hardware keyboard

A curated set of these bindings can be switched on when a Bluetooth or USB keyboard is attached. It is off by default:
- `s22-kbd-bindings on|off|status` turns it on, off, or reports its state.
- The file is `/opt/s22-touch/omarchy-keyboard.lua`.

Run `status` first. Turn the set on only if the owner asks.

## Under the sway-pixman desktop

If `/etc/s22-desktop` is `sway-pixman` (`pidof sway`, no Hyprland), `hyprctl` does nothing. Use `swaymsg` with the SWAYSOCK from line 2 of `/run/s22-desktop/session` instead. The equivalents of the window and workspace actions are listed in `/opt/s22-touch/sway-keyboard.conf`, for example `swaymsg kill` and `swaymsg workspace number 2`. The Omarchy-shell actions (menu, emojis, clipboard, notifications) do not exist under sway.
