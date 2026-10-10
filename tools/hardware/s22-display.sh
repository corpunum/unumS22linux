#!/bin/sh
# s22-display on|off|toggle|status — S22 screen power for the Power key.
#
# off: Hyprland DPMS off.  Kernel log shows the real panel path runs:
#      decon_disable -> mcd_drm_panel_disable (SLEEP_IN, display off) ->
#      panel_power_off (vdd regulators disabled).  The OLED is truly dark,
#      unlike backlight 0, which only dims it to the minimum level.  Touch input
#      is disabled while off.
# on:  DPMS on, then two full-screen repaints so a first frame that reached the
#      command-mode panel incomplete is overwritten (the root fix is synchronous
#      llvmpipe rasterization, LP_NUM_THREADS=0, set by start-persistent-desktop).
# Native root, as root.
set -u
CHROOT=/mnt/omarchy-trial
STATE=/run/s22-display-state
TOUCH=sec_touchscreen-2
H() { chroot "$CHROOT" /usr/bin/env XDG_RUNTIME_DIR=/run/user/0 /usr/bin/hyprctl -i 0 "$@" >/dev/null; }
cur=$(cat "$STATE" 2>/dev/null || echo on)
# Opt-in sway-pixman desktop (/etc/s22-desktop): wlroots 0.20 cannot re-enable
# this panel after `output power off` (the real modeset commit fails although
# its TEST_ONLY passes; phase 2 trial 2026-10-10), so under sway "off" is
# backlight 0 + touch events disabled, NOT a true panel power-off.
SWAY_SESSION=$CHROOT/run/s22-desktop/session
on_sway() { ! pidof Hyprland >/dev/null 2>&1 && pidof sway >/dev/null 2>&1 && [ -s "$SWAY_SESSION" ]; }
S() { chroot "$CHROOT" /usr/bin/env XDG_RUNTIME_DIR=/run/user/0 SWAYSOCK="$(sed -n 2p "$SWAY_SESSION")" /usr/bin/swaymsg "$@" >/dev/null; }

repaint() {  # force full damage: toggle a no-op screen shader
  H eval 'hl.config({ decoration = { screen_shader = "/root/s22-identity.frag" } })'
  sleep 0.15
  H eval 'hl.config({ decoration = { screen_shader = "" } })'
}

ensure_identity() {
  [ -s "$CHROOT/root/s22-identity.frag" ] && return 0
  cat > "$CHROOT/root/s22-identity.frag" <<'FRAG'
#version 300 es
precision highp float;
in vec2 v_texcoord;
uniform sampler2D tex;
out vec4 fragColor;
void main() { fragColor = texture(tex, v_texcoord); }
FRAG
}

off() {
  if on_sway; then
    S input type:touch events disabled
    b=$(cat /sys/class/backlight/panel/brightness)
    [ "$b" != 0 ] && echo "$b" > /run/s22-display-brightness
    echo 0 > /sys/class/backlight/panel/brightness
    echo off > "$STATE"; return
  fi
  H eval "hl.device({ name = \"$TOUCH\", enabled = false })"
  H dispatch 'hl.dsp.dpms({ action = "off" })'
  echo off > "$STATE"
}

on() {
  if on_sway; then
    S input type:touch events enabled
    b=$(cat /run/s22-display-brightness 2>/dev/null)
    if [ "$(cat /sys/class/backlight/panel/brightness)" = 0 ]; then echo "${b:-128}" > /sys/class/backlight/panel/brightness; fi
    echo on > "$STATE"; return
  fi
  ensure_identity
  H dispatch 'hl.dsp.dpms({ action = "on" })'
  H eval "hl.device({ name = \"$TOUCH\", enabled = true })"
  # the earlier black-shader implementation may have left these behind
  H eval 'hl.config({ decoration = { screen_shader = "" } })'
  b=$(cat /run/s22-display-brightness 2>/dev/null)
  if [ "$(cat /sys/class/backlight/panel/brightness)" = 0 ]; then echo "${b:-128}" > /sys/class/backlight/panel/brightness; fi
  sleep 0.4; repaint; sleep 0.4; repaint
  echo on > "$STATE"
}

exec 7>/run/s22-display.lock; flock 7
case "${1:-toggle}" in
  on) on;; off) off;;
  toggle) if [ "$cur" = off ]; then on; else off; fi;;
  status) echo "$cur brightness=$(cat /sys/class/backlight/panel/brightness) connector_dpms=$(cat /sys/class/drm/card1-DSI-1/dpms)";;
  *) echo "usage: s22-display on|off|toggle|status" >&2; exit 2;;
esac
