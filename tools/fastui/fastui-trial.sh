#!/bin/sh
# Bounded, self-restoring panel hand-over for the fastui spike (native root).
#
# The running sway-pixman desktop (or Hyprland) is never stopped: like
# tools/touchui/phase2/p2-trial.sh, a placeholder libseat session
# (p2-seat-hold.py, session 2) is registered with seatd and the compositor is
# asked to switch to it (XF86Switch_VT_2 via wtype). seatd drops the
# compositor's DRM master and revokes its input fds; the test command then
# opens /dev/dri/card1 directly. At the end (command exit, deadline, or
# /run/fastui-stop) the command is stopped by exact PID, the holder exits,
# seatd re-activates the compositor, and a DPMS off/on cycle makes it repaint.
#
# usage: setsid fastui-trial.sh DURATION_S CMD [ARGS...] </dev/null >LOG 2>&1 &
#   DURATION_S <= 300. Extra env: FASTUI_GPU_CLIENT="cmd..." starts a second
#   command (the Bionic Vulkan client) 1 s after CMD.
set -u
DUR=${1:-60}; shift
[ "$DUR" -le 300 ] || DUR=300
C=/mnt/omarchy-trial
P2=$C/run/s22-p2
D=/srv/s22/state/fastui
mkdir -p "$D" "$P2"
rm -f /run/fastui-stop "$P2/stop" "$P2/hold.state"
log() { echo "$(date +%T) fastui-trial: $*"; }
ENVBASE="HOME=/root PATH=/usr/local/bin:/usr/bin:/bin XDG_RUNTIME_DIR=/run/user/0 LANG=C.UTF-8 TZ=Europe/Athens"
on_panel() {
  awk -v who="allocated by = $1" '/^plane\[[0-9]+\]: plane-0$/{f=1;next} f&&/^(plane|crtc)\[/{f=0}
    f&&index($0,who){ok=1} /^crtc\[/{c=1;next} c&&/active=1/{a=1} c&&/^[a-z]/{c=0}
    END{exit !(ok&&a)}' /sys/kernel/debug/dri/1/state 2>/dev/null
}
hstate() { cut -d' ' -f1 "$P2/hold.state" 2>/dev/null; }
alive() { [ -n "$1" ] && kill -0 "$1" 2>/dev/null && ! grep -q '^State:[[:space:]]*Z' "/proc/$1/status" 2>/dev/null; }
[ -d /sys/kernel/debug/dri ] || mount -t debugfs none /sys/kernel/debug
COMP=sway; pidof sway >/dev/null || COMP=Hyprland
on_panel "$COMP" || { log "$COMP is not on plane-0; refusing"; exit 1; }

HOLD=''; CMD=''; GPU=''
restore() {
  log "restore: begin"
  i=0; while alive "$CMD" && [ $i -lt 30 ]; do sleep 0.1; i=$((i+1)); done   # let it see /run/fastui-stop and report
  for p in "$GPU" "$CMD"; do
    if alive "$p"; then
      kill -TERM "$p" 2>/dev/null
      i=0; while alive "$p" && [ $i -lt 30 ]; do sleep 0.1; i=$((i+1)); done
      alive "$p" && { log "pid $p did not exit; SIGKILL"; kill -KILL "$p"; sleep 0.3; }
    fi
  done
  touch "$P2/stop"
  i=0; while alive "$HOLD" && [ $i -lt 30 ]; do sleep 0.1; i=$((i+1)); done
  alive "$HOLD" && { log "holder $HOLD still alive; SIGTERM"; kill -TERM "$HOLD"; sleep 1; }
  sleep 1
  n=0
  while ! on_panel "$COMP" && [ $n -lt 3 ]; do
    /srv/s22/hardware/bin/s22-display off >/dev/null 2>&1; sleep 0.7
    /srv/s22/hardware/bin/s22-display on >/dev/null 2>&1
    i=0; while ! on_panel "$COMP" && [ $i -lt 40 ]; do sleep 0.1; i=$((i+1)); done
    n=$((n+1))
  done
  if on_panel "$COMP"; then log "restore: $COMP owns the panel again (dpms cycles=$n)"
  else log "restore: WARNING $COMP not on plane-0; press power twice"; fi
  # The seat switch makes sway drop and re-add DSI-1; Quickshell then keeps a
  # placeholder screen and never re-maps its layer surfaces (black panel,
  # seen 2026-10-21). Restart only the touch shell: SIGTERM the exact
  # quickshell PID whose parent is s22-touchd, which respawns it within ~2 s.
  if [ "$COMP" = sway ]; then
    for d in /proc/[0-9]*; do
      [ "$(cat $d/comm 2>/dev/null)" = quickshell ] || continue
      pp=$(awk '/^PPid:/{print $2}' $d/status 2>/dev/null)
      tr '\0' ' ' < /proc/$pp/cmdline 2>/dev/null | grep -q '/srv/s22/hardware/bin/s22-touchd' || continue
      log "restore: restarting touch shell pid ${d#/proc/} (parent s22-touchd $pp)"
      kill -TERM "${d#/proc/}"
    done
    sleep 5
    chroot $C /usr/local/bin/s22-ui home >/dev/null 2>&1
  fi
  log "restore: done"
}

setsid chroot $C /usr/bin/env -i $ENVBASE LIBSEAT_BACKEND=seatd \
  /usr/bin/python3 /opt/s22-p2/p2-seat-hold.py /run/s22-p2/hold.state /run/s22-p2/stop \
  </dev/null >>"$D/hold.log" 2>&1 &
HOLD=$!
trap 'log "signal"; restore; exit 0' TERM INT HUP
i=0; while [ "$(hstate)" != inactive ] && [ "$(hstate)" != waiting ] && [ $i -lt 50 ]; do sleep 0.1; i=$((i+1)); done
sleep 0.3
W=$(ls $C/run/user/0 | grep -E '^wayland-[0-9]+$' | head -1)
chroot $C /usr/bin/env -i $ENVBASE WAYLAND_DISPLAY="$W" wtype -k XF86Switch_VT_2
i=0; while [ "$(hstate)" != active ] && [ $i -lt 50 ]; do sleep 0.1; i=$((i+1)); done
if [ "$(hstate)" != active ]; then log "switch did not happen (state=$(hstate)); aborting"; restore; exit 1; fi
log "placeholder active: $COMP switched away"

"$@" &
CMD=$!
log "cmd pid=$CMD: $*"
if [ -n "${FASTUI_GPU_CLIENT:-}" ]; then
  sleep 1
  sh -c "$FASTUI_GPU_CLIENT" &
  GPU=$!
  log "gpu client pid=$GPU"
fi
END=$(( $(date +%s) + DUR ))
while [ "$(date +%s)" -lt "$END" ]; do
  [ -e /run/fastui-stop ] && { log "stop requested"; break; }
  alive "$CMD" || { log "cmd exited"; break; }
  alive "$HOLD" || { log "holder exited early"; break; }
  sleep 0.5
done
restore
exit 0
