#!/bin/sh
# s22-kbd-bindings: switch the curated hardware-keyboard bindings on or off (chroot, root).
#   s22-kbd-bindings status | on | off
# "on" creates the flag file and loads /opt/s22-touch/omarchy-keyboard.lua into the
# running Hyprland. "off" removes the flag and unbinds. Default (no flag file) is off.
set -eu
LUA=/opt/s22-touch/omarchy-keyboard.lua
FLAG=/root/.config/s22/omarchy-keyboard.enabled
export XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR:-/run/user/0}
if [ -z "${HYPRLAND_INSTANCE_SIGNATURE:-}" ]; then
  HYPRLAND_INSTANCE_SIGNATURE=$(ls -t "$XDG_RUNTIME_DIR/hypr" 2>/dev/null | head -1)
  export HYPRLAND_INSTANCE_SIGNATURE
fi

count() { hyprctl -j binds 2>/dev/null | grep -c '"description": *"s22: ' || true; }

case "${1:-status}" in
  on)
    [ -f "$LUA" ] || { echo "missing $LUA" >&2; exit 1; }
    mkdir -p "$(dirname "$FLAG")"
    : > "$FLAG"
    hyprctl eval "dofile(\"$LUA\")" >/dev/null
    echo "on: $(count) s22 keyboard bindings active"
    ;;
  off)
    rm -f "$FLAG"
    hyprctl eval 'if s22_kbd_off then s22_kbd_off() end' >/dev/null
    echo "off: $(count) s22 keyboard bindings active"
    ;;
  status)
    if [ -f "$FLAG" ]; then f=on; else f=off; fi
    echo "flag: $f   active s22 keyboard bindings: $(count)"
    ;;
  *)
    echo "usage: s22-kbd-bindings status|on|off" >&2
    exit 2
    ;;
esac
