# Native input, power, and camera readiness

The deployed native session shows `sec_touchscreen` and power/volume key
devices enumerated, battery/thermal sysfs readable, and ISP/MFC/JPEG/scaler
V4L2 nodes registered. Physical finger/touch and physical-button acceptance
remain unproven; only compositor dispatch and synthetic exercise were tested.

The bounded read-only probe is:

```sh
python3 /usr/local/bin/input-power-readiness.py --camera
python3 /usr/local/bin/input-power-readiness.py --events 10
```

`--events` opens `/dev/input/event*` read-only and reports observed kernel
events; it never injects or grabs a device. A real physical tap and one power
or volume-button press must be identified by device/name and event code before
claiming physical acceptance. Synthetic input is not sufficient.

The readiness probe is inventory/events only; it has no display-mutating
option. The bounded DPMS exercise is separately covered by the tested
`tools/hardware/test-power-dpms.py --exercise`, which uses the deployed native
Lua dispatcher and does not prove a physical button event.

The probe also reports raw power-supply fields (`capacity`, `status`, `health`,
`online`, voltage/current where exposed) and thermal-zone millidegrees. These
are telemetry snapshots, not charging-cycle or battery-life acceptance.

Camera mode is inventory-only: it lists `/dev/video*` and names from
`/sys/class/video4linux`; it intentionally does not open a camera, stream,
capture, or access microphone data.

## Host-only collector tests

Run `python3 tools/hardware/test-input-power-readiness.py` for focused tests
against temporary synthetic sysfs and device roots. The tests cover input,
power, thermal, and camera inventory plus bounded event observation, and fail
if event handling opens a write-capable descriptor, writes an event, or grabs
one. They do not access a phone or establish physical-device acceptance.

## Deployed acceptance evidence (2026-09-20)

The native Lua binding uses the pinned direct dispatcher
`hl.dsp.dpms({ action = "toggle" })`. The controlled DPMS exercise passed
`true -> false -> true` while the resident model remained healthy; this is
not proof of a physical power-key event. The accepted bar geometry was
`omarchy.power width=54 height=26 visible=true`, with the battery percentage
shown after the JetBrains Mono Nerd Font fix.

At 19:13 the deployed bar also gained a persistent `Keyboard` command button.
It invokes the existing Squeekboard D-Bus `SetVisible true` method without a
daemon or focus grab. A synthetic touchscreen event at normalized `(0.92,
0.011)` / raw `(3767,45)` successfully revealed the OSK; no physical tap was
claimed. The button was validated without rebooting afterward.

The complete Quickshell process had to be restarted for changed QML and font
state to take effect. `reloadConfig`/layout IPC alone was insufficient: the
effective layout changed but the widget stayed width zero until the existing
launcher was restarted. The restart was scoped to Quickshell/OSK; Hyprland
and the resident model were preserved.

Evidence: `evidence/hardware-20260920/dpms-smoke.json`,
`evidence/hardware-20260920/power-bar-ready.png`, and
`evidence/hardware-20260920/post-panic-persistence.txt`. A later Wi-Fi trial
caused a kernel panic, after which the native recovery guardian returned
automatically and persistence evidence confirmed desktop, model, stride state,
and UI autostart. This is not Wi-Fi acceptance.

Fonts were restored from the locally signed package using tracked
`tools/hardware/60-s22-monospace.conf`; the installed
`JetBrainsMonoNerdFont-Regular.ttf` SHA-256 is
`1c680e8cde9fcf8b88a5605ce8d1fb94dd3fb15841f7ca7bf4c55664855e5611` (the full
package receipt is retained by parent deployment evidence). Do not infer
physical touch, button, camera capture, or charging acceptance from this UI.
