#!/bin/sh
# Phone-side deploy / rollback. Run as root on native Alpine root.
set -eu
BIN=/srv/s22/hardware/bin/s22d
STATE=/srv/s22/state/s22d
CONF=/srv/s22/state/keepalive/config.json
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if [ "${1:-}" = --rollback ]; then
  backup=$(ls -d "$STATE"/backup-* 2>/dev/null | sort | tail -1 || true)
  for p in /proc/[0-9]*; do
    cmd=$(tr '\0' ' ' < "$p/cmdline" 2>/dev/null || true)
    case "$cmd" in *"$BIN"*) kill "${p#/proc/}" 2>/dev/null || true;; esac
  done
  sleep 1
  if [ -n "$backup" ] && [ -f "$backup/keepalive-config.json" ]; then cp -p "$backup/keepalive-config.json" "$CONF"; else
    python3 - "$CONF" <<'PY'
import json,os,sys
p=sys.argv[1]
if os.path.exists(p):
 c=json.load(open(p)); c['services']=[x for x in c.get('services',[]) if x!='s22d']; c.get('service_defs',{}).pop('s22d',None)
 t=p+'.tmp'; json.dump(c,open(t,'w'),indent=2); os.replace(t,p)
PY
  fi
  [ -n "$backup" ] && [ -f "$backup/s22d" ] && cp -p "$backup/s22d" "$BIN" || rm -f "$BIN"
  rm -f /run/s22d.sock
  echo "s22d rollback complete; restart s22-keepalive when convenient"
  exit 0
fi
SRC=${S22D_BINARY:-$HERE/dist/s22d-aarch64-unknown-linux-musl}
[ -x "$SRC" ] || { echo "missing executable $SRC" >&2; exit 1; }
[ -f "$CONF" ] || { echo "missing keepalive config $CONF" >&2; exit 1; }
mkdir -p "$STATE" "$(dirname "$BIN")"
BK="$STATE/backup-$(date +%Y%m%d-%H%M%S)"; mkdir -p "$BK"
cp -p "$CONF" "$BK/keepalive-config.json"; [ -f "$BIN" ] && cp -p "$BIN" "$BK/s22d" || true
install -m 755 "$SRC" "$BIN"
python3 - "$CONF" "$BIN" <<'PY'
import json,os,sys
p,b=sys.argv[1:]; c=json.load(open(p)); sv=c.setdefault('services',[]); defs=c.setdefault('service_defs',{})
if 's22d' not in sv: sv.append('s22d')
defs['s22d']={'argv':[b], 'needle':b, 'requires':b, 'log':'/srv/s22/state/s22d/s22d.log', 'start_wait_s':10}
t=p+'.s22d-tmp'; json.dump(c,open(t,'w'),indent=2); os.replace(t,p)
PY
# Launch once; keepalive owns subsequent supervision.
setsid "$BIN" </dev/null >>"$STATE/s22d.log" 2>&1 &
sleep 1
if ! grep -q '127.0.0.1:8766' /proc/net/tcp /proc/net/tcp6 2>/dev/null; then echo "started; verify API on phone and review $STATE/s22d.log"; fi
echo "installed. rollback: sh $HERE/install-s22d.sh --rollback"
