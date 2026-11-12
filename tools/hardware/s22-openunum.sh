#!/bin/sh
# OpenUnum on the S22: server + WebUI in the Arch chroot. Installed as /srv/s22/hardware/bin/s22-openunum
# and started by s22-keepalive.   s22-openunum start|stop|status|log
#
# Since 2026-11-12 (hub U-8cdacbb0) the server does NOT run as root. A chroot is no security boundary for
# uid 0 (it could read /proc/<native pid>/root/srv/s22/state/owner-session and flip the device switches),
# and this kernel has no PID or user namespaces. So the server runs as the chroot user "unumagent" with no
# capabilities, an empty bounding set and no_new_privs: it cannot ptrace or look through root processes,
# cannot mount, mknod or load modules, and setuid binaries give it nothing.
# tools/hardware/openunum-agent-user.sh creates the user and fixes the ownership; `check` proves the boundary.
# Emergency only: `touch /srv/s22/state/openunum/run-as-root` starts it as root again (and logs it loudly).
set -eu
C=/mnt/omarchy-trial
LOG=/srv/s22/state/openunum/server.log
AGENT=unumagent
ESCAPE=/srv/s22/state/openunum/run-as-root
# OPENUNUM_FIRST_TOKEN_CAP_MS: first-token window for openai-compatible routes (the local :8090 model).
pid() { ps | awk '$0 ~ /[n]ode src\/server\.mjs/ {print $1}'; }
case "${1:-status}" in
start)
  [ -n "$(pid)" ] && { echo "running $(pid)"; exit 0; }
  mkdir -p "$(dirname "$LOG")"
  if [ -e "$ESCAPE" ]; then
    echo "$(date -Is) WARNING: starting OpenUnum as ROOT because $ESCAPE exists" >>"$LOG"
    DROP=
  else
    U=$(chroot "$C" id -u "$AGENT" 2>/dev/null) || { echo "no user $AGENT in the chroot: run openunum-agent-user.sh apply" >&2; exit 1; }
    G=$(chroot "$C" id -g "$AGENT")
    DROP="/usr/sbin/setpriv --reuid $U --regid $G --clear-groups --inh-caps=-all --bounding-set=-all --no-new-privs"
  fi
  # shellcheck disable=SC2086
  nohup chroot "$C" $DROP /usr/bin/env -i HOME=/root PATH=/opt/node/bin:/usr/local/bin:/usr/bin:/bin \
    OPENUNUM_HOME=/root/.openunum NODE_ENV=production OPENUNUM_FIRST_TOKEN_CAP_MS=120000 \
    sh -c 'cd /opt/openunum && exec node src/server.mjs' >>"$LOG" 2>&1 </dev/null &
  i=0; while [ $i -lt 90 ]; do
    curl -fsS -m 2 http://127.0.0.1:18880/api/health >/dev/null 2>&1 && { echo "up after ${i}s"; exit 0; }
    sleep 1; i=$((i + 1)); done
  echo "not healthy after 90s; see $LOG" >&2; exit 1 ;;
stop) p=$(pid); [ -n "$p" ] && kill $p && echo "stopped $p" || echo "not running" ;;
status) echo "pid: $(pid)"; for p in $(pid); do grep -E '^(Uid|CapEff|NoNewPrivs):' /proc/$p/status | tr '\n\t' '  '; echo; done
  curl -sS -m 3 http://127.0.0.1:18880/api/health | head -c 400; echo ;;
log) tail -n 50 "$LOG" ;;
*) echo "usage: $0 start|stop|status|log" >&2; exit 2 ;;
esac
