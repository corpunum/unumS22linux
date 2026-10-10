# sway-pixman desktop: fixes for the three phase 2 blockers (2026-10-10)

The phase 2 numbers are in [../phase2/README.md](../phase2/README.md). This change fixes the three blockers that stood in the way of making sway the default desktop. All three were then re-verified on the panel in a 10-minute sway trial, using the same self-restoring `p2-trial.sh` watchdog.

## 1. Real panel power-off/on (Power key)

**Symptom:** after `swaymsg output DSI-1 power off` (or `disable`), `power on` failed with sway's `Backend commit failed`. The panel stayed dark until sway restarted.

**Root cause:** this is a wlroots 0.20 problem, not a kernel or panel one.

- CRTC 184 on exynos-drmdpu has no `GAMMA_LUT` property, and its legacy gamma size is 0 (read with `drmModeGetCrtc` and `drmModeObjectGetProperties`).
- When wlroots re-enables an output, `wlr_scene` re-applies the gamma LUT, so the commit carries a colour transform of NULL ("reset to identity").
- On a CRTC without `GAMMA_LUT`, wlroots resets the gamma through the legacy API. `drm_legacy_crtc_set_gamma(size=0)` finds a gamma size of 0 and returns `false` without logging anything.
- strace confirms this: the TEST_ONLY atomic commit returns 0, the real commit creates its mode blob, and then no `DRM_IOCTL_MODE_ATOMIC` is ever issued.
- Hyprland/aquamarine does not use this path, which is why its DPMS works.

**Fix:**

- Resetting gamma to identity on a CRTC with no gamma table is a successful no-op. In `backend/drm/legacy.c`, `if (size == 0) return false;` becomes `return true;`.
- `patch-wlroots-gamma.py` applies exactly that one-instruction change (`mov w0,#0` becomes `mov w0,#1` at file offset 0x6a718) to a **private copy** of Arch Linux ARM's `wlroots0.20 0.20.2-1`. The script checks the SHA-256 of the input and the output, and checks the surrounding instruction bytes.
- The copy goes to `/opt/s22-wlroots/libwlroots-0.20.so`. The stock `/usr/lib` library is never touched.
- `start-persistent-desktop` (and `p2-trial.sh`) start sway with `LD_LIBRARY_PATH=/opt/s22-wlroots:/usr/lib` when the copy exists. This is the same pattern as `/opt/s22-aquamarine` for Hyprland.
- If a future wlroots update changes the library, the patch tool refuses to run and sway uses the stock library. `s22-display` then falls back to backlight 0 with touch disabled, so the screen always comes back.

**Verified on the panel:**

- `s22-display off` (sway path): connector `dpms=Off`, CRTC `enable=0 active=0`, and taps produce no frames because touch is disabled.
- `s22-display on`: `dpms=On`, the CRTC is active, and the next tap reaches the panel in 23–27 ms.
- 3 more off/on cycles with no `Backend commit failed`.

## 2. App switcher under sway

- `shell.qml` gets `windows()`, `findToplevel()`, `focusToplevel()` and `closeToplevel()`. Under sway they use Quickshell's `ToplevelManager` (wlr-foreign-toplevel: `appId`, `title`, `activate()`, `close()`). Under Hyprland the Hyprland IPC path is unchanged.
- `SwitcherPage.qml` uses these functions instead of importing `Quickshell.Hyprland`.
- Tested on the panel:
  - The list shows `org.omarchy.agent` and `s22.terminal` ([screenshot](shots/v2-switcher.png)).
  - Tapping a row focuses that window (checked through the `swaymsg -t get_tree` focus).
  - Hold-Close closes it.
  - The Terminal tile launches or focuses `s22.terminal`.

## 3. Status under sway

- The Omarchy bar is Hyprland-specific, so it is not started under sway.
- Under sway the touch shell shows a 28 px status strip on the top layer, with an exclusive zone so apps and the home grid sit below it ([screenshot](shots/v2-strip.png)). It shows:
  - the time;
  - the agent model, with a ● or ○ health dot;
  - the network: `wifi`, `usb` or `cell`, plus `· ts` when Tailscale is up (operstate of wlan0, ecm0, rmnet0 and tailscale0);
  - the battery %.
- Tapping the strip toggles home.
- It refreshes every 15 s (clock) and 30 s (status), only under sway. Over 10 idle minutes that added about 0.02 panel fps and 0 % sway CPU.

## Also adapted

| Area | Change |
|---|---|
| Keyboard bindings | `omarchy/sway-keyboard.conf` has the same 32 curated combos as `omarchy-keyboard.lua`, as sway commands. `s22-kbd-bindings` detects sway and binds or unbinds live (unbinding is safe in sway). The sway config re-applies the bindings at start when the flag is set. Omarchy-shell actions (menu, emojis, clipboard, notifications) remain Hyprland-only. Verified: Super+Return opens foot, Super+W closes it, and after `off`, Super+Return does nothing. |
| Chat typing | Exclusive focus (from phase 2), re-verified ([screenshot](shots/v2-chat.png)). |
| Cards and confirm | The overlay works on sway ([screenshot](shots/v2-confirm.png)), and tapping "No" answered the request. |
| Gestures | s22-touchd detected home, back and switcher during the trial. In a trial they are routed to the inactive Hyprland session, which is preferred by design; under the sway profile they route to sway through `/run/s22-desktop/session`. |

## 10-minute trial, idle after the checks

[`results-trial-v2-20261010.jsonl`](results-trial-v2-20261010.jsonl):

| Period | sway CPU | Panel fps | System CPU | BIG-cluster temperature |
|---|---|---|---|---|
| Minutes 1–3 (during the checks) | 1–10 % | 2–4 | 7–9 % | 31–33 °C |
| Minutes 4–9 (idle) | 0.0 % | 0.02 | 6.5 % | 31–32 °C |

sway RSS stayed at 39–51 MB. Over the 10 minutes the temperature was flat at 31–33 °C. Hyprland then came back automatically. The phone was muted before and after.
