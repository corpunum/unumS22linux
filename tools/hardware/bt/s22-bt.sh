#!/bin/sh
# s22-bt up|down|status : manual persistent Bluetooth controller (no boot hook).
# up   : run bt-qca6490-hci-hold pinned to the X2 core (SCHED_FIFO 50) in the
#        background; hci0 stays registered until `down`.
# down : SIGTERM -> line-discipline detach, UART restore, chip power-off.
# No pairing/connection is done here; discovery: chroot ... s22-bt-scan.py.
set -u
P=/srv/s22/bt-hold/bt-qca6490-hci-hold
LOG=/srv/s22/state/bt/hold.log
PIDF=/run/s22-bt-hold.pid
alive() { [ -f "$PIDF" ] && kill -0 "$(cat "$PIDF")" 2>/dev/null; }
case "${1:-status}" in
up)
  alive && { echo "already up (pid $(cat $PIDF))"; exit 0; }
  [ -e /sys/class/bluetooth/hci0 ] && { echo "hci0 present without our holder; refusing" >&2; exit 1; }
  mkdir -p "$(dirname "$LOG")"
  python3 -c '
import os, sys, ctypes
os.sched_setaffinity(0, {7})
prm = ctypes.c_int(50)
ctypes.CDLL(None).syscall(119, 0, 1, ctypes.byref(prm))
p = sys.argv[1]
os.execv(p, [p, "--execute", "--allow-shared-wlan-rail", "--live-wlan-vote", "--uart", "/dev/ttySAC1",
             "--btpower", "/dev/btpower", "--btpower-rdev", "503:0"])
' "$P" >>"$LOG" 2>&1 </dev/null &
  echo $! > "$PIDF"
  i=0; while [ $i -lt 60 ]; do
    [ -e /sys/class/bluetooth/hci0 ] && { echo "hci0 up after ${i}s (pid $(cat $PIDF))"; exit 0; }
    alive || { echo "holder exited; see $LOG" >&2; tail -5 "$LOG" >&2; exit 1; }
    sleep 1; i=$((i + 1)); done
  echo "hci0 not up after 60s" >&2; exit 1 ;;
down)
  alive || { echo "not running"; rm -f "$PIDF"; exit 0; }
  kill "$(cat "$PIDF")"
  i=0; while alive && [ $i -lt 20 ]; do sleep 1; i=$((i + 1)); done
  alive && { echo "holder did not exit in 20s" >&2; exit 1; }
  rm -f "$PIDF"; tail -4 "$LOG"; ls /sys/class/bluetooth ;;
status)
  alive && echo "up (pid $(cat $PIDF))" || echo "down"
  ls /sys/class/bluetooth 2>/dev/null ;;
*) echo "usage: s22-bt up|down|status" >&2; exit 2 ;;
esac
