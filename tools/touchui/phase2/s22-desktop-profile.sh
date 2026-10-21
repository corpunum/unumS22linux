#!/bin/sh
# Select the S22 desktop compositor for the NEXT desktop start (native root).
#
#   s22-desktop-profile status          show selected + running profile
#   s22-desktop-profile sway-pixman     opt in to sway with the pixman renderer
#                                       (and the Quickshell touch shell)
#   s22-desktop-profile fastshell       sway-pixman underneath + unum-fastshell
#                                       (KMS-direct touch shell) in front; takes
#                                       effect within seconds (s22-touchd)
#   s22-desktop-profile hyprland        Hyprland on llvmpipe (the default)
#   s22-desktop-profile --rollback      same as hyprland: removes /etc/s22-desktop
#
# fastshell keeps /etc/s22-desktop = sway-pixman (sway is the base compositor
# for foot and the recovery target) and selects the touch shell in
# /srv/s22/state/touchui/ui-backend. Back: `s22-desktop-profile sway-pixman`,
# or hold Power >= 1 s (hooks/power-long).
#
# start-persistent-desktop reads /etc/s22-desktop once at startup (boot or a
# supervisor restart). A missing/unknown value means Hyprland, and a sway
# start that fails before readiness falls back to Hyprland in the same run.
# This script never restarts the desktop itself.
set -eu
SEL=/etc/s22-desktop
C=/mnt/omarchy-trial
UIB=/srv/s22/state/touchui/ui-backend
FASTSHELL=/srv/s22/hardware/bin/unum-fastshell
shell_sel() { printf '%s\n' "$1" > $UIB.tmp && mv $UIB.tmp $UIB; }
stop_fastshell() {   # exact PID only
  pid=$(cat /run/s22-fastshell.pid 2>/dev/null)
  [ -n "$pid" ] && [ "$(cat /proc/$pid/comm 2>/dev/null)" = unum-fastshell ] && kill -TERM "$pid"
  return 0
}
running() { python3 -c 'import json;print(json.load(open("/run/s22-persistent-ready.json")).get("desktop_profile","hyprland (pre-profile supervisor)"))' 2>/dev/null || echo unknown; }
case "${1:-status}" in
  status)
    echo "selected=$(cat $SEL 2>/dev/null || echo 'hyprland (default)') running=$(running) touch_shell=$(cat $UIB 2>/dev/null || echo quickshell) fastshell_pid=$(cat /run/s22-fastshell.pid 2>/dev/null || echo none)" ;;
  fastshell)
    [ -x $FASTSHELL ] || { echo "missing $FASTSHELL (tools/unum-fastshell/install-fastshell.sh)" >&2; exit 1; }
    [ "$(cat $SEL 2>/dev/null)" = sway-pixman ] || { echo "fastshell runs on top of sway-pixman; select that first" >&2; exit 1; }
    shell_sel fastshell
    echo "selected fastshell on sway-pixman (s22-touchd switches within seconds; back: $0 sway-pixman or hold Power 1 s)" ;;
  sway-pixman)
    [ -x $C/usr/bin/sway ] || { echo "sway is not installed in $C" >&2; exit 1; }
    [ -f $C/root/s22-sway-pixman.conf ] || { echo "missing $C/root/s22-sway-pixman.conf" >&2; exit 1; }
    grep -q "SWAY_PIXMAN_DESKTOP" /usr/local/bin/start-persistent-desktop ||
      { echo "start-persistent-desktop has no desktop profiles; deploy it first" >&2; exit 1; }
    printf 'sway-pixman\n' > $SEL.tmp && mv $SEL.tmp $SEL
    if [ "$(cat $UIB 2>/dev/null)" = fastshell ]; then shell_sel quickshell; stop_fastshell; fi
    echo "selected sway-pixman (effective at the next desktop start; rollback: $0 --rollback)" ;;
  hyprland|--rollback)
    rm -f $SEL
    echo "selected hyprland (default; effective at the next desktop start)" ;;
  *) sed -n '2,13p' "$0"; exit 2 ;;
esac
