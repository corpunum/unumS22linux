#!/bin/sh
# Phone-side deploy / rollback for s22d. Run as root on the native Alpine root.
#   sh install-s22d.sh [--binary /path/to/s22d]    install and register with s22-keepalive
#   sh install-s22d.sh --rollback                  restore the previous binary and keepalive config
# The binary is the static aarch64 musl build from CI (artifact "s22d-aarch64-unknown-linux-musl").
set -eu
BIN=/srv/s22/hardware/bin/s22d
STATE=/srv/s22/state/s22d
CONF=/srv/s22/state/keepalive/config.json
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

stop_s22d() {
  for p in /proc/[0-9]*; do
    cmd=$(tr '\0' ' ' < "$p/cmdline" 2>/dev/null || true)
    case "$cmd" in "$BIN"*) kill "${p#/proc/}" 2>/dev/null || true;; esac
  done
  sleep 1
}

if [ "${1:-}" = --rollback ]; then
  backup=$(ls -d "$STATE"/backup-* 2>/dev/null | sort | tail -1 || true)
  stop_s22d
  if [ -n "$backup" ] && [ -f "$backup/keepalive-config.json" ]; then
    cp -p "$backup/keepalive-config.json" "$CONF"
  else
    python3 - "$CONF" <<'PY'
import json, os, sys
p = sys.argv[1]
if os.path.exists(p):
    c = json.load(open(p))
    c['services'] = [x for x in c.get('services', []) if x != 's22d']
    c.get('service_defs', {}).pop('s22d', None)
    t = p + '.tmp'
    json.dump(c, open(t, 'w'), indent=2)
    os.replace(t, p)
PY
  fi
  if [ -n "$backup" ] && [ -f "$backup/s22d" ]; then cp -p "$backup/s22d" "$BIN"; else rm -f "$BIN"; fi
  rm -f /run/s22d.sock
  echo "s22d rollback complete (keepalive picks up its config on the next pass)"
  exit 0
fi

SRC=${S22D_BINARY:-}
[ "${1:-}" = --binary ] && SRC=${2:?--binary needs a path}
[ -n "$SRC" ] || SRC=$HERE/s22d
[ -x "$SRC" ] || { echo "missing executable $SRC (pass --binary or set S22D_BINARY)" >&2; exit 1; }
[ -f "$CONF" ] || { echo "missing keepalive config $CONF" >&2; exit 1; }
"$SRC" --version >/dev/null || { echo "$SRC does not run on this machine" >&2; exit 1; }

mkdir -p "$STATE" "$(dirname "$BIN")"
BK="$STATE/backup-$(date +%Y%m%d-%H%M%S)"; mkdir -p "$BK"
cp -p "$CONF" "$BK/keepalive-config.json"
[ -f "$BIN" ] && cp -p "$BIN" "$BK/s22d" || true
stop_s22d
install -m 755 "$SRC" "$BIN"
python3 - "$CONF" "$BIN" <<'PY'
import json, os, sys
p, b = sys.argv[1:]
c = json.load(open(p))
sv = c.setdefault('services', [])
defs = c.setdefault('service_defs', {})
if 's22d' not in sv:
    sv.append('s22d')
defs['s22d'] = {
    'argv': [b],
    'needle': b,
    'requires': b,
    'health': {'tcp': ['127.0.0.1', 8766]},
    'log': '/srv/s22/state/s22d/s22d.log',
    'start_wait_s': 10,
}
t = p + '.s22d-tmp'
json.dump(c, open(t, 'w'), indent=2)
os.replace(t, p)
PY
# keepalive owns supervision; start once now so the answer below is meaningful.
setsid "$BIN" </dev/null >>"$STATE/s22d.log" 2>&1 &
sleep 2
if curl -fs --max-time 5 http://127.0.0.1:8766/v1/capabilities >/dev/null 2>&1; then
  echo "s22d is answering on 127.0.0.1:8766"
else
  echo "s22d did not answer; read $STATE/s22d.log" >&2
fi
echo "installed $BIN. rollback: sh $HERE/install-s22d.sh --rollback"
