# Deployed native power/battery UI fix (2026-09-20)

The actual persistent targets are inside the Arch root, not the Alpine host:

```text
/srv/s22/arch/root/hyprland-omarchy-ui.lua
/srv/s22/arch/opt/s22-ui/shell-trial.json
/srv/s22/arch/root/.config/omarchy/shell.json
/srv/s22/arch/opt/omarchy-source/shell/plugins/panels/power/Panel.qml
```

Observed preimage SHA-256 values:

```text
root/hyprland-omarchy-ui.lua                         1104fbfbb5d33f9883e20d3ebf4b770b402163f847370d65788c2a118e3a3944
opt/s22-ui/shell-trial.json                          7a43f9a72d3e4b968628a1e6d735a16d69abf57b2fc01d341aa7d8b094c498b9
root/.config/omarchy/shell.json                      25ac5d33f96f6181f141c4b992becddc4875c810c6bc0ba0551bcfff05360089
opt/omarchy-source/shell/plugins/panels/power/Panel.qml 560e17a5144d3e2ea1e0ad36c2aec563909b535551e1e66e061f5fb25f1d4ec0
```

## Power key

The deployed phone-specific Hyprland Lua adds the native `XF86PowerOff`
binding using the pinned `hl.bind(key, dispatcher, opts)` and direct
`hl.dsp.dpms({ action = "toggle" })` API.
The deployed acceptance exercise is `tools/hardware/test-power-dpms.py
--exercise`; it validates the DPMS transition and model liveness only. The
standalone event daemon is retained as source but is not deployed or
recommended alongside the native binding. Physical power-key delivery remains
unproven.

## Battery panel

The deployed `shell-trial.json` and active `/root/.config/omarchy/shell.json`
contain `omarchy.power` with `showPercentage: true`. The deployed Panel.qml
probes both `BAT*` and lowercase `battery`, builds a fallback device from the
helper's percentage/state, and refreshes battery telemetry every 30 seconds
while the bar is closed. Native sysfs reported `/sys/class/power_supply/battery`
at 100%, Full, and 296 deci-degrees; UPower remains preferred when available.

The patch consists of the following actual changes:

1. Add the power entry with `showPercentage: true` to both template and
   active shell JSON.
2. Probe sysfs battery presence and construct the fallback device used by the
   icon/fraction/state properties.
3. Run a battery-only 30-second refresh while the bar is closed.
4. Install `tools/hardware/power-battery-status.sh` as
   `/usr/local/bin/omarchy-battery-status` so the existing panel `Process`
   receives `percentage`, `state`, `rate`, and `temperature` key/value data.
   It intentionally omits voltage/current because driver units were not
   proven by this audit.

The active file is `$XDG_CONFIG_HOME/omarchy/shell.json` (the live session
used `/root/.config`), not only `shell-trial.json`; the launcher does not
overwrite an existing active file. The shell provides a no-session-restart
reload: `quickshell ipc -p /opt/s22-ui/shell call shell reloadConfig`.
Use `listShellConfig` first to confirm the effective layout. A full
Quickshell/OSK launcher restart was required for changed QML/font state;
`reloadConfig` alone was insufficient even though it reported `ok` and showed
the new layout. The restart preserved Hyprland and the resident model.

The four preimage hashes above were verified before deployment. The later
keyboard-button addition is separate from the battery candidate: template
hash `c0dfb81cff1f33b93feca2d6c5c2e6d5a2abea044fc8cc654a28783ae25d4b44`,
active hash `c350d2bea2171c83b3212ac7b03680ed58f26975e9e3c0ee7728d66e304c1e6c`,
and patch files are
`tools/hardware/osk-bar-toggle*.patch`. Evidence in
`evidence/hardware-20260920/dpms-smoke.json` records DPMS `true -> false -> true`,
and `power-bar-ready.png` records the visible 54x26 battery widget. This
proves the compositor dispatch/UI path only; it does not prove a physical
power-button or finger event. Test in the Arch chroot with Quickshell running:
the bar must show a battery icon/percentage,
the panel must open, and a sysfs snapshot must agree with its percentage.
Do not claim profile-control or charging acceptance; the fallback is limited
to kernel battery telemetry.

## Host candidate and rollback

Readonly preimages were copied to the ignored
`rootfs/power-ui-candidate/originals/` tree. The candidate tree is under
`rootfs/power-ui-candidate/candidate/` and has these hashes:

```text
root/hyprland-omarchy-ui.lua                         4d85f72ab1bcd82e147b671adfdfa67d00a135ba6f0699b94d1b63d21bffbbec
 opt/s22-ui/shell-trial.json                          c00cd0e42e2eb1490939df516f2f07b0f109f6b3335c7744cbe1bfbd00cb0625
 root/.config/omarchy/shell.json                      fba1a166e4974f9e53dd9cabd4f47ff7583702c99b22b47da41560fb9a4675d6
 opt/omarchy-source/shell/plugins/panels/power/Panel.qml b7179b5745c507ce185ccf09e591a2abc887632d60c6e511e7c1a1f92e7ab0c6
 usr/local/bin/omarchy-battery-status                eb31e1600e3534c323daa6430be5646948afe7d75c46eb9a106a9062f0147e74
```

The candidate Lua binding uses the pinned direct
`hl.dsp.dpms({ action = "toggle" })` API; the candidate shell JSON parses successfully and the
candidate QML contains no suspend/reboot/poweroff action. Deploy only after
checking the four recorded live preimage hashes. Roll back by restoring the
matching files from `rootfs/power-ui-candidate/originals/` and remove the
added `omarchy.power` entry. The un-deployed event-daemon source remains
disabled; the native Lua binding is the sole deployed path.
