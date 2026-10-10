#!/bin/sh
# s22-kbd-bindings: switch the curated hardware-keyboard bindings on or off (chroot, root).
#   s22-kbd-bindings status | on | off
# "on" creates the flag file and loads /opt/s22-touch/omarchy-keyboard.lua into the
# running Hyprland. "off" removes the flag; bindings clear at the next desktop restart. Default (no flag file) is off.
# Under the opt-in sway-pixman desktop the same combos come from
# /opt/s22-touch/sway-keyboard.conf (swaymsg bindsym); there "off" unbinds live (safe in sway).
set -eu
LUA=/opt/s22-touch/omarchy-keyboard.lua
FLAG=/root/.config/s22/omarchy-keyboard.enabled
export XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR:-/run/user/0}
if [ -z "${HYPRLAND_INSTANCE_SIGNATURE:-}" ]; then
  HYPRLAND_INSTANCE_SIGNATURE=$(ls -t "$XDG_RUNTIME_DIR/hypr" 2>/dev/null | head -1)
  export HYPRLAND_INSTANCE_SIGNATURE
fi

count() { hyprctl -j binds 2>/dev/null | grep -c '"description": *"s22: ' || true; }

SWAYCONF=${S22_SWAY_KBD_CONF:-/opt/s22-touch/sway-keyboard.conf}
SWAYCOUNT=${S22_SWAY_KBD_COUNT:-/run/s22-kbd-sway.count}
hypr_live() { [ -S "$XDG_RUNTIME_DIR/hypr/${HYPRLAND_INSTANCE_SIGNATURE:-none}/.socket.sock" ]; }
if ! hypr_live && [ -z "${SWAYSOCK:-}" ] && [ -s "${S22_DESKTOP_SESSION:-/run/s22-desktop/session}" ]; then
  SWAYSOCK=$(sed -n 2p "${S22_DESKTOP_SESSION:-/run/s22-desktop/session}")
fi
if ! hypr_live && [ -n "${SWAYSOCK:-}" ]; then
  export SWAYSOCK
  binds() { grep '^bindsym ' "$SWAYCONF"; }
  case "${1:-status}" in
    on)
      [ -f "$SWAYCONF" ] || { echo "missing $SWAYCONF" >&2; exit 1; }
      mkdir -p "$(dirname "$FLAG")"; : > "$FLAG"
      n=0
      binds > /tmp/s22-kbd-sway.$$
      while IFS= read -r line; do swaymsg -- "$line" >/dev/null && n=$((n+1)); done < /tmp/s22-kbd-sway.$$
      rm -f /tmp/s22-kbd-sway.$$
      echo "$n" > "$SWAYCOUNT"
      echo "on: $n s22 keyboard bindings active (sway)"
      ;;
    off)
      rm -f "$FLAG"
      binds | sed 's/^bindsym \([^ ]*\) .*/unbindsym \1/' > /tmp/s22-kbd-sway.$$
      while IFS= read -r line; do swaymsg -- "$line" >/dev/null 2>&1 || true; done < /tmp/s22-kbd-sway.$$
      rm -f /tmp/s22-kbd-sway.$$ "$SWAYCOUNT"
      echo "off: flag removed; s22 keyboard bindings unbound (sway)"
      ;;
    status)
      if [ -f "$FLAG" ]; then f=on; else f=off; fi
      echo "flag: $f   active s22 keyboard bindings: $(cat "$SWAYCOUNT" 2>/dev/null || echo 0) (sway)"
      ;;
    *) echo "usage: s22-kbd-bindings status|on|off" >&2; exit 2 ;;
  esac
  exit 0
fi

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
    # No live unbind: it crashes Hyprland 0.56 (see omarchy-keyboard.lua). The bindings are
    # only loaded when the flag exists, so they are gone after the next desktop restart.
    echo "off: flag removed; $(count) s22 keyboard bindings stay active until the next desktop restart"
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
