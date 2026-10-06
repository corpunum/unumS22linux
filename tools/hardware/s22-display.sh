#!/bin/sh
# s22-display on|off|toggle|status — blank the S22 screen without DPMS.
#
# Hyprland's DPMS off detaches the CRTC, but this panel stack never sends the
# panel sleep-in (no panel/DSIM activity at all), so the command-mode OLED
# keeps showing a stale, half-updated frame from its own RAM.  Instead keep
# scanout running and composite pure black (OLED black = pixels off), drop the
# backlight to 0 and disable the touchscreen so pocket touches do nothing.
# Native root, as root (sysfs is read-only inside the chroot).
set -u
CHROOT=/mnt/omarchy-trial
STATE=/run/s22-display-state
BL=/sys/class/backlight/panel/brightness
SHADER=/root/s22-black.frag
H() { chroot "$CHROOT" /usr/bin/env XDG_RUNTIME_DIR=/run/user/0 /usr/bin/hyprctl -i 0 "$@" >/dev/null; }
cur=$(cat "$STATE" 2>/dev/null || echo on)

ensure_shader() {
  [ -s "$CHROOT$SHADER" ] && return 0
  cat > "$CHROOT$SHADER" <<'FRAG'
#version 300 es
precision highp float;
in vec2 v_texcoord;
uniform sampler2D tex;
out vec4 fragColor;
void main() { fragColor = vec4(0.0, 0.0, 0.0, 1.0); }
FRAG
}

off() {
  [ "$cur" = off ] && return 0
  ensure_shader
  cat "$BL" > /run/s22-display-brightness 2>/dev/null
  H eval "hl.config({ decoration = { screen_shader = \"$SHADER\" } })"
  H eval 'hl.device({ name = "sec_touchscreen-2", enabled = false })'
  echo 0 > "$BL"
  echo off > "$STATE"
}

on() {
  [ "$cur" = on ] && return 0
  H eval 'hl.device({ name = "sec_touchscreen-2", enabled = true })'
  H eval 'hl.config({ decoration = { screen_shader = "" } })'
  b=$(cat /run/s22-display-brightness 2>/dev/null); [ -n "$b" ] && [ "$b" -gt 0 ] || b=128
  echo "$b" > "$BL"
  echo on > "$STATE"
}

exec 7>/run/s22-display.lock; flock 7
case "${1:-toggle}" in
  on) on;; off) off;;
  toggle) if [ "$cur" = off ]; then on; else off; fi;;
  status) echo "$cur brightness=$(cat $BL)";;
  *) echo "usage: s22-display on|off|toggle|status" >&2; exit 2;;
esac
