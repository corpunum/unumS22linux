#!/bin/sh
# Install unum-fastshell on the phone (native root, as root). Run from a staging
# dir that holds: unum-fastshell (aarch64 musl binary), s22-touchd.py,
# s22-desktop-profile.sh, power-long-hook.sh, and optionally s22-buttons.py
# (the button daemon that hands the keys to the shell, 2026-10-22).
#
#   sh install-fastshell.sh             install files (backup first); shell unchanged
#   sh install-fastshell.sh --activate  install + select fastshell (s22-desktop-profile fastshell)
#   sh install-fastshell.sh --rollback  classic shell, restore the newest backup, remove the hook
#
# s22-touchd is restarted by exact PID so it loads the new code; s22-keepalive
# starts it again (the touch shell restarts with it).
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
BIN=/srv/s22/hardware/bin
STATE=/srv/s22/state/fastshell
BK=$STATE/backup-$(date +%Y%m%d-%H%M%S)
HOOK=/srv/s22/buttons/hooks/power-long
mkdir -p $STATE

touchd_restart() {
  for d in /proc/[0-9]*; do
    [ "$(cat $d/comm 2>/dev/null)" = python3 ] || continue
    tr '\0' ' ' < $d/cmdline 2>/dev/null | grep -q "^/usr/bin/python3 $BIN/s22-touchd $" || continue
    echo "restarting s22-touchd pid ${d#/proc/}"
    kill -TERM "${d#/proc/}"
  done
}

# s22-buttons is started once by start-persistent-desktop (no supervisor):
# stop the running one by exact PID and start the installed file the same way.
buttons_restart() {
  for d in /proc/[0-9]*; do
    [ "$(cat $d/comm 2>/dev/null)" = python3 ] || continue
    tr '\0' ' ' < $d/cmdline 2>/dev/null | grep -q "^/usr/bin/python3 $BIN/s22-buttons $" || continue
    echo "restarting s22-buttons pid ${d#/proc/}"
    kill -TERM "${d#/proc/}"
  done
  sleep 0.5
  setsid /usr/bin/python3 $BIN/s22-buttons </dev/null >>/srv/s22/buttons/daemon.log 2>&1 &
}

case "${1:-}" in
  --rollback)
    printf 'quickshell\n' > /srv/s22/state/touchui/ui-backend
    pid=$(cat /run/s22-fastshell.pid 2>/dev/null || true)
    [ -n "$pid" ] && [ "$(cat /proc/$pid/comm 2>/dev/null)" = unum-fastshell ] && kill -TERM "$pid"
    last=$(ls -d $STATE/backup-* 2>/dev/null | tail -1)
    [ -n "$last" ] || { echo "no backup"; exit 1; }
    cp -p "$last/s22-touchd" $BIN/s22-touchd
    cp -p "$last/s22-desktop-profile" $BIN/s22-desktop-profile
    if [ -e "$last/power-long" ]; then cp -p "$last/power-long" $HOOK; else rm -f $HOOK; fi
    if [ -e "$last/s22-buttons" ]; then cp -p "$last/s22-buttons" $BIN/s22-buttons; buttons_restart; fi
    rm -f $BIN/unum-fastshell
    touchd_restart
    echo "rolled back from $last"; exit 0 ;;
esac

mkdir -p $BK
cp -p $BIN/s22-touchd $BK/
cp -p $BIN/s22-desktop-profile $BK/
[ -e $HOOK ] && cp -p $HOOK $BK/ || true
[ -e $BIN/unum-fastshell ] && cp -p $BIN/unum-fastshell $BK/ || true
[ -e $BIN/s22-buttons ] && cp -p $BIN/s22-buttons $BK/ || true
install -m 755 "$HERE/unum-fastshell" $BIN/unum-fastshell.new && mv $BIN/unum-fastshell.new $BIN/unum-fastshell
install -m 755 "$HERE/s22-touchd.py" $BIN/s22-touchd
install -m 755 "$HERE/s22-desktop-profile.sh" $BIN/s22-desktop-profile
install -m 755 "$HERE/power-long-hook.sh" $HOOK
if [ -e "$HERE/s22-buttons.py" ]; then
  # first, so the old daemon never handles keys the new shell also handles
  install -m 755 "$HERE/s22-buttons.py" $BIN/s22-buttons.new && mv $BIN/s22-buttons.new $BIN/s22-buttons
  buttons_restart
fi
echo "installed (backup in $BK)"
touchd_restart
if [ "${1:-}" = --activate ]; then
  sleep 3
  $BIN/s22-desktop-profile fastshell
fi
