#!/bin/sh
# Select the S22 desktop compositor for the NEXT desktop start (native root).
#
#   s22-desktop-profile status          show selected + running profile
#   s22-desktop-profile sway-pixman     opt in to sway with the pixman renderer
#   s22-desktop-profile hyprland        Hyprland on llvmpipe (the default)
#   s22-desktop-profile --rollback      same as hyprland: removes /etc/s22-desktop
#
# start-persistent-desktop reads /etc/s22-desktop once at startup (boot or a
# supervisor restart). A missing/unknown value means Hyprland, and a sway
# start that fails before readiness falls back to Hyprland in the same run.
# This script never restarts the desktop itself.
set -eu
SEL=/etc/s22-desktop
C=/mnt/omarchy-trial
running() { python3 -c 'import json;print(json.load(open("/run/s22-persistent-ready.json")).get("desktop_profile","hyprland (pre-profile supervisor)"))' 2>/dev/null || echo unknown; }
case "${1:-status}" in
  status)
    echo "selected=$(cat $SEL 2>/dev/null || echo 'hyprland (default)') running=$(running)" ;;
  sway-pixman)
    [ -x $C/usr/bin/sway ] || { echo "sway is not installed in $C" >&2; exit 1; }
    [ -f $C/root/s22-sway-pixman.conf ] || { echo "missing $C/root/s22-sway-pixman.conf" >&2; exit 1; }
    grep -q "SWAY_PIXMAN_DESKTOP" /usr/local/bin/start-persistent-desktop ||
      { echo "start-persistent-desktop has no desktop profiles; deploy it first" >&2; exit 1; }
    printf 'sway-pixman\n' > $SEL.tmp && mv $SEL.tmp $SEL
    echo "selected sway-pixman (effective at the next desktop start; rollback: $0 --rollback)" ;;
  hyprland|--rollback)
    rm -f $SEL
    echo "selected hyprland (default; effective at the next desktop start)" ;;
  *) sed -n '2,13p' "$0"; exit 2 ;;
esac
