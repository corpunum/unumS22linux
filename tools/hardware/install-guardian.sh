#!/bin/sh
# Install (or roll back) s22-guardian on the S22 native root, as root.
#   sh tools/hardware/install-guardian.sh             install / update
#   sh tools/hardware/install-guardian.sh --rollback  undo
# Installs /srv/s22/hardware/bin/s22-guardian, the hold-aware s22-keepalive
# (backup: s22-keepalive.pre-guardian) and the keepalive service "guardian";
# restarts only s22-keepalive (exact PID; services it supervises keep running).
set -eu
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
BIN=/srv/s22/hardware/bin
KCONF=/srv/s22/state/keepalive/config.json
KA=$BIN/s22-keepalive

keepalive_pids() {
  for p in /proc/[0-9]*; do
    [ "$(tr '\0' ' ' < $p/cmdline 2>/dev/null)" = "/usr/bin/python3 $KA " ] && echo "${p#/proc/}"
  done
  return 0
}
guardian_pids() {
  for p in /proc/[0-9]*; do
    [ "$(tr '\0' ' ' < $p/cmdline 2>/dev/null)" = "/usr/bin/python3 $BIN/s22-guardian serve " ] && echo "${p#/proc/}"
  done
  return 0
}
conf() {   # conf add|remove : the keepalive service entry
  python3 - "$KCONF" "$1" <<'EOF'
import json, sys
path, op = sys.argv[1], sys.argv[2]
cfg = json.load(open(path))
svcs = cfg.setdefault('services', [])
defs = cfg.setdefault('service_defs', {})
if op == 'add':
    if 'guardian' not in svcs:
        svcs.append('guardian')
    defs['guardian'] = {'argv': ['/usr/bin/python3', '/srv/s22/hardware/bin/s22-guardian', 'serve'],
                        'needle': '/srv/s22/hardware/bin/s22-guardian serve',
                        'health': {'file': '/run/s22-guardian/status.json'},
                        'requires': '/srv/s22/hardware/bin/s22-guardian',
                        'log': '/srv/s22/state/guardian/serve.log', 'nice': 5, 'start_wait_s': 15}
else:
    cfg['services'] = [s for s in svcs if s != 'guardian']
    defs.pop('guardian', None)
tmp = path + '.tmp'
json.dump(cfg, open(tmp, 'w'), indent=2)
import os; os.replace(tmp, path)
EOF
}
restart_keepalive() {
  for p in $(keepalive_pids); do kill "$p"; done
  sleep 2
  for p in $(keepalive_pids); do kill -9 "$p"; done
  ( cd / && setsid /usr/bin/python3 "$KA" </dev/null >>/srv/s22/state/keepalive/daemon.log 2>&1 & )
  sleep 3
  echo "s22-keepalive pid(s): $(keepalive_pids | tr '\n' ' ')"
}

if [ "${1:-}" = --rollback ]; then
  [ -x "$BIN/s22-guardian" ] && python3 "$BIN/s22-guardian" release || true
  conf remove
  for p in $(guardian_pids); do kill "$p"; done
  [ -f "$KA.pre-guardian" ] && cp -p "$KA.pre-guardian" "$KA"
  rm -f "$BIN/s22-guardian"
  restart_keepalive
  echo "guardian rolled back"
  exit 0
fi

[ -f "$KA.pre-guardian" ] || cp -p "$KA" "$KA.pre-guardian"
install -m 755 "$HERE/s22-guardian.py" "$BIN/s22-guardian"
install -m 755 "$HERE/s22-keepalive.py" "$KA"
mkdir -p /srv/s22/state/guardian
conf add
restart_keepalive
echo "guardian: keepalive starts it within one pass; status: python3 $BIN/s22-guardian --once"
echo "rollback: sh $HERE/install-guardian.sh --rollback"
