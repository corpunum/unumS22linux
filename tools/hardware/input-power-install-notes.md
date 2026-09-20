# Deployed native input/power pieces (2026-09-20)

`input-power-dpms.py` observes the physical power-key event without
`EVIOCGRAB`, so it does not replace kernel long-press or boot behavior. On a
short `KEY_POWER` press it runs only:

```text
hyprctl dispatch 'hl.dsp.dpms({ action = "toggle" })'
```

The native Lua binding is deployed; this standalone event daemon is retained
only as un-deployed source and must not be started alongside it.

`power-battery-status.sh --shell` is an install-ready sysfs fallback for the
existing `omarchy-battery-status` command when UPower is unavailable. It
reports kernel battery percentage/state and temperature when available. It
omits ambiguous voltage/current fields and does not write charge limits or
power controls. The deployed Panel.qml uses this fallback when UPower is
unavailable, while retaining UPower as the preferred source; profile controls
remain unclaimed.
