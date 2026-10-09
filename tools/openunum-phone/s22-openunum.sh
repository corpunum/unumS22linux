#!/bin/sh
# OpenUnum on the S22: server + WebUI in the Arch chroot, brain = rig Halogen
# over Tailscale (see openunum.json). Manual start: s22-openunum [start|stop|status|log]
# WebUI: http://127.0.0.1:18880 on the phone (or ssh -L 18880:127.0.0.1:18880).
set -eu
C=/mnt/omarchy-trial
LOG=/srv/s22/state/openunum/server.log
pid() { ps | awk '$0 ~ /[n]ode src\/server\.mjs/ {print $1}'; }
case "${1:-status}" in
start)
  [ -n "$(pid)" ] && { echo "running $(pid)"; exit 0; }
  mkdir -p "$(dirname "$LOG")" "$C/root/.openunum/workspace"
  nohup chroot "$C" /usr/bin/env -i HOME=/root PATH=/opt/node/bin:/usr/local/bin:/usr/bin:/bin \
    OPENUNUM_HOME=/root/.openunum NODE_ENV=production OPENUNUM_FIRST_TOKEN_CAP_MS=900000 \
    sh -c 'cd /opt/openunum && exec node src/server.mjs' >>"$LOG" 2>&1 </dev/null &
  i=0; while [ $i -lt 90 ]; do
    curl -fsS -m 2 http://127.0.0.1:18880/api/health >/dev/null 2>&1 && { echo "up after ${i}s"; exit 0; }
    sleep 1; i=$((i + 1)); done
  echo "not healthy after 90s; see $LOG" >&2; exit 1 ;;
stop) p=$(pid); [ -n "$p" ] && kill $p && echo "stopped $p" || echo "not running" ;;
status) echo "pid: $(pid)"; curl -sS -m 3 http://127.0.0.1:18880/api/health | head -c 400; echo ;;
log) tail -n 50 "$LOG" ;;
*) echo "usage: $0 start|stop|status|log" >&2; exit 2 ;;
esac
