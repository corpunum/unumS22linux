#!/bin/sh
# Bounded, self-restoring compositor trial on the S22 panel (native root).
#
# Hands the panel from the running Hyprland session to sway (pixman renderer)
# for at most DURATION seconds, then hands it back. Hyprland, its supervisor
# (start-persistent-desktop), seatd, OpenUnum and the chroot mounts are never
# stopped: Hyprland is only switched away from via seatd (non-VT session
# switch to the p2-seat-hold.py placeholder) and re-activated when the
# placeholder exits.
#
# usage: setsid p2-trial.sh [DURATION_S] </dev/null >LOG 2>&1 &
#   early stop:  touch /srv/s22/state/touchui/phase2/stop-request
# Survives SSH loss (run it setsid'd). Kills only the exact PIDs/PGIDs it
# started; never kills by name.
set -u
DUR=${1:-600}
[ "$DUR" -le 600 ] || DUR=600
C=/mnt/omarchy-trial
D=/srv/s22/state/touchui/phase2
P2=$C/run/s22-p2                 # /run/s22-p2 inside the chroot
REQ=$D/stop-request
mkdir -p "$D" "$P2"
rm -f "$REQ" "$P2/stop" "$P2/hold.state" "$C/run/s22-desktop/session"
log() { echo "$(date +%T) p2-trial: $*"; }
ENVBASE="HOME=/root PATH=/usr/local/bin:/usr/bin:/bin XDG_RUNTIME_DIR=/run/user/0 LANG=C.UTF-8 TZ=Europe/Athens"

# plane-0 scans out a framebuffer allocated by COMM and the CRTC is active
on_panel() {
  awk -v who="allocated by = $1" '/^plane\[[0-9]+\]: plane-0$/{f=1;next} f&&/^(plane|crtc)\[/{f=0}
    f&&index($0,who){ok=1} /^crtc\[/{c=1;next} c&&/active=1/{a=1} c&&/^[a-z]/{c=0}
    END{exit !(ok&&a)}' /sys/kernel/debug/dri/1/state 2>/dev/null
}
hypr_on_panel() { on_panel Hyprland; }
uptime_s() { cut -d' ' -f1 /proc/uptime; }
hstate() { cut -d' ' -f1 "$P2/hold.state" 2>/dev/null; }
alive() { [ -n "$1" ] && kill -0 "$1" 2>/dev/null; }

HOLD=''; SWAY=''
restore() {
  log "restore: begin"
  if alive "$SWAY"; then
    kill -TERM -"$SWAY" 2>/dev/null || kill -TERM "$SWAY" 2>/dev/null
    i=0; while alive "$SWAY" && [ $i -lt 50 ]; do sleep 0.1; i=$((i+1)); done
    alive "$SWAY" && { log "sway pgid $SWAY did not exit; SIGKILL"; kill -KILL -"$SWAY" 2>/dev/null; sleep 0.5; }
  fi
  rm -f "$C/run/s22-desktop/session"
  touch "$P2/stop"
  i=0; while alive "$HOLD" && [ $i -lt 30 ]; do sleep 0.1; i=$((i+1)); done
  alive "$HOLD" && { log "holder $HOLD still alive; SIGTERM"; kill -TERM "$HOLD"; sleep 1; }
  alive "$HOLD" && { log "holder $HOLD still alive; SIGKILL"; kill -KILL "$HOLD"; }
  # A re-activated Hyprland re-commits its last buffer but only renders on
  # new damage (and the trial compositor may have disabled the CRTC): cycle
  # DPMS (the power-key path, s22-display) so it modesets and draws a fresh
  # full frame. Always at least once; up to 3 times until plane-0 is back.
  sleep 1
  S=$(ls "$C/run/user/0/hypr" 2>/dev/null | head -1)
  n=0
  while { [ $n -eq 0 ] || ! hypr_on_panel; } && [ $n -lt 3 ] && [ -n "$S" ]; do
    H="chroot $C /usr/bin/env -i $ENVBASE HYPRLAND_INSTANCE_SIGNATURE=$S hyprctl"
    $H dispatch 'hl.dsp.dpms({ action = "off" })' >/dev/null 2>&1; sleep 0.5
    $H dispatch 'hl.dsp.dpms({ action = "on" })' >/dev/null 2>&1
    i=0; while ! hypr_on_panel && [ $i -lt 50 ]; do sleep 0.1; i=$((i+1)); done
    n=$((n+1))
  done
  if hypr_on_panel; then log "restore: Hyprland owns the panel again (dpms cycles=$n)"
  else log "restore: WARNING Hyprland not on plane-0 / CRTC inactive; tap the screen or press power twice"; fi
  log "restore: done"
}

pidof Hyprland >/dev/null || { log "no Hyprland running; refusing"; exit 1; }
[ -d /sys/kernel/debug/dri ] || mount -t debugfs none /sys/kernel/debug
hypr_on_panel || { log "Hyprland is not on plane-0; refusing"; exit 1; }
[ "${S22_P2_NO_SWAY:-0}" = 1 ] || [ -x $C/usr/bin/sway ] || { log "sway not installed in the chroot"; exit 1; }

# 1. placeholder seat session (session 2)
setsid chroot $C /usr/bin/env -i $ENVBASE LIBSEAT_BACKEND=seatd \
  /usr/bin/python3 /opt/s22-p2/p2-seat-hold.py /run/s22-p2/hold.state /run/s22-p2/stop \
  </dev/null >>"$D/hold.log" 2>&1 &
HOLD=$!
trap 'log "signal"; restore; exit 0' TERM INT HUP
i=0; while [ "$(hstate)" != inactive ] && [ "$(hstate)" != waiting ] && [ $i -lt 50 ]; do sleep 0.1; i=$((i+1)); done
sleep 0.5
log "holder pid=$HOLD state=$(hstate)"

# 2. ask Hyprland to switch to session 2
W=$(ls $C/run/user/0 | grep -E '^wayland-[0-9]+$' | head -1)
chroot $C /usr/bin/env -i $ENVBASE WAYLAND_DISPLAY="$W" wtype -k XF86Switch_VT_2
i=0; while [ "$(hstate)" != active ] && [ $i -lt 50 ]; do sleep 0.1; i=$((i+1)); done
if [ "$(hstate)" != active ]; then log "switch to placeholder did not happen (state=$(hstate)); aborting"; restore; exit 1; fi
log "placeholder active: Hyprland disabled"

if [ "${S22_P2_NO_SWAY:-0}" = 1 ]; then
  log "probe mode: holding the panel for $DUR s without a compositor"; sleep "$DUR"; restore; exit 0
fi

# 3. sway on the panel, pixman renderer, devices opened directly (noop seat)
# Same wlroots choice as start-persistent-desktop (gamma-reset fix if staged).
WLRLIB=/usr/lib; [ -f $C/opt/s22-wlroots/libwlroots-0.20.so ] && WLRLIB=/opt/s22-wlroots:/usr/lib
T0=$(uptime_s)
setsid chroot $C /usr/bin/env -i $ENVBASE XDG_SESSION_TYPE=wayland LIBSEAT_BACKEND=noop \
  WLR_RENDERER=pixman WLR_DRM_DEVICES=/dev/dri/card1 WLR_BACKENDS=drm,libinput \
  LD_LIBRARY_PATH=$WLRLIB ${S22_P2_EXTRA_ENV:-} \
  /usr/bin/dbus-run-session -- /usr/bin/sway ${S22_P2_SWAY_ARGS:-} -c /root/s22-sway-pixman.conf \
  </dev/null >>"$D/sway.log" 2>&1 &
SWAY=$!
log "sway pgid=$SWAY"
i=0; while [ $i -lt 200 ]; do
  on_panel sway && break
  alive "$SWAY" || break; sleep 0.05; i=$((i+1)); done
# The touch shell is normally supervised by s22-touchd, which stays on the
# (still running, inactive) Hyprland session during a trial, so start a trial
# instance here. S22_P2_SHELL points at a staged shell dir to test QML changes.
i=0; while [ ! -s "$C/run/s22-desktop/session" ] && [ $i -lt 100 ]; do sleep 0.1; i=$((i+1)); done
SWAYSOCK_IN=$(sed -n 2p "$C/run/s22-desktop/session" 2>/dev/null)
chroot $C /usr/bin/env -i $ENVBASE SWAYSOCK="$SWAYSOCK_IN" swaymsg exec \
  "env QT_QUICK_BACKEND=software QSG_RHI_BACKEND=software QT_QPA_PLATFORM=wayland QT_QPA_PLATFORMTHEME=generic NO_AT_BRIDGE=1 QT_ACCESSIBILITY=0 QS_DISABLE_FILE_WATCHER=1 QS_NO_RELOAD_POPUP=1 QT_QUICK_CONTROLS_STYLE=Basic nice -n 5 quickshell -n -p ${S22_P2_SHELL:-/opt/s22-touch}/shell.qml" >/dev/null 2>&1
log "first sway frame on panel after $(echo "$(uptime_s) $T0" | awk '{printf "%.2f", $1-$2}') s (alive=$(alive "$SWAY" && echo y || echo n))"

# 4. watchdog
END=$(( $(date +%s) + DUR ))
while [ "$(date +%s)" -lt "$END" ]; do
  [ -e "$REQ" ] && { log "stop requested"; break; }
  alive "$SWAY" || { log "sway exited early"; break; }
  alive "$HOLD" || { log "holder exited early"; break; }
  sleep 1
done
restore
exit 0
