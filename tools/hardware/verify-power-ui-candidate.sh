#!/bin/sh
# Host-only verification for the ignored candidate tree; never deploys files.
set -eu
repo=$(CDPATH= cd -- "$(dirname "$0")/../.." && pwd)
root=${1:-"$repo/rootfs/power-ui-candidate/candidate"}
sha() { sha256sum "$1" | awk '{print $1}'; }
test "$(sha "$root/root/hyprland-omarchy-ui.lua")" = 4d85f72ab1bcd82e147b671adfdfa67d00a135ba6f0699b94d1b63d21bffbbec
test "$(sha "$root/opt/s22-ui/shell-trial.json")" = c00cd0e42e2eb1490939df516f2f07b0f109f6b3335c7744cbe1bfbd00cb0625
test "$(sha "$root/root/.config/omarchy/shell.json")" = fba1a166e4974f9e53dd9cabd4f47ff7583702c99b22b47da41560fb9a4675d6
test "$(sha "$root/opt/omarchy-source/shell/plugins/panels/power/Panel.qml")" = b7179b5745c507ce185ccf09e591a2abc887632d60c6e511e7c1a1f92e7ab0c6
test "$(sha "$root/usr/local/bin/omarchy-battery-status")" = eb31e1600e3534c323daa6430be5646948afe7d75c46eb9a106a9062f0147e74
python3 -m json.tool "$root/opt/s22-ui/shell-trial.json" >/dev/null
python3 -m json.tool "$root/root/.config/omarchy/shell.json" >/dev/null
! rg -n 'suspend|reboot|poweroff|shutdown' "$root/opt/omarchy-source/shell/plugins/panels/power/Panel.qml"
rg -q 'hl\.bind\("XF86PowerOff", hl\.dsp\.dpms\(\{ action = "toggle" \}\)' "$root/root/hyprland-omarchy-ui.lua"
! rg -n 'dispatch dpms toggle|dsp\.exec_cmd\("hyprctl' "$root/root/hyprland-omarchy-ui.lua" "$repo/tools/hardware/input-power-dpms.py"
"$repo/tools/hardware/test-power-battery-status.sh"
echo 'power UI candidate: hashes, JSON, safety scan, and battery fixture verified'
