#!/bin/sh
set -eu

# Candidate-only launcher.  It does not install files, start services, or
# invoke the Omarchy installer.  Run from the staged trial session only.
: "${WAYLAND_DISPLAY:?start this from the staged Hyprland Wayland session}"
: "${OMARCHY_PATH:?set this to the existing full /opt/omarchy-source path}"
: "${OMARCHY_UI_CANDIDATE:?set this to the absolute path of this directory}"

export XDG_CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"
# This trial has no desktop portals, accessibility bus or audio stack.
export NO_AT_BRIDGE=1 GTK_USE_PORTAL=0 QT_ACCESSIBILITY=0
export QT_QPA_PLATFORMTHEME=generic
mkdir -p "$XDG_CONFIG_HOME/omarchy"
if [ ! -e "$XDG_CONFIG_HOME/omarchy/shell.json" ]; then
  cp "$OMARCHY_UI_CANDIDATE/shell-trial.json" "$XDG_CONFIG_HOME/omarchy/shell.json"
fi

# Keep the candidate session awake while it is being manually exercised.  The
# prior state is restored on exit; restoration has not been tested on-device.
stay_awake="$HOME/.local/state/omarchy/indicators/stay-awake"
had_stay_awake=0
[ -e "$stay_awake" ] && had_stay_awake=1
mkdir -p "$(dirname "$stay_awake")"
touch "$stay_awake"

# Start the OSK as a user process; it will remain harmless if the compositor
# lacks the virtual-keyboard/layer-shell globals and report that condition.
squeekboard >/tmp/s22-squeekboard.log 2>&1 &
osk_pid=$!
reveal_pid=''
cleanup() {
  kill "$osk_pid" 2>/dev/null || true
  if [ -n "$reveal_pid" ]; then kill "$reveal_pid" 2>/dev/null || true; fi
  if [ "$had_stay_awake" -eq 0 ]; then rm -f "$stay_awake"; fi
}
trap cleanup EXIT INT TERM

# Terminal input does not automatically reveal Squeekboard on this backend.
# Use its introspected public D-Bus method, bounded while the owner starts.
i=0
while [ "$i" -lt 10 ]; do
  if gdbus call --session --dest sm.puri.OSK0 --object-path /sm/puri/OSK0 \
      --method sm.puri.OSK0.SetVisible true >/dev/null 2>&1; then break; fi
  kill -0 "$osk_pid" 2>/dev/null || break
  i=$((i + 1))
  sleep 0.5
done

# Foot's initial focus event can hide an earlier visibility request.
# Reveal again after terminal and shell startup have settled.
(
  sleep 3
  gdbus call --session --dest sm.puri.OSK0 --object-path /sm/puri/OSK0 \
    --method sm.puri.OSK0.SetVisible true >/dev/null 2>&1 || true
) &
reveal_pid=$!

QS_DISABLE_FILE_WATCHER=1 QS_NO_RELOAD_POPUP=1 \
  quickshell -n -p "$OMARCHY_UI_CANDIDATE/shell"
