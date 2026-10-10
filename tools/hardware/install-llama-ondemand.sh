#!/bin/sh
# Switch the :8090 fallback model to on-demand loading (or roll back), S22 native root.
#   sh tools/hardware/install-llama-ondemand.sh             install
#   sh tools/hardware/install-llama-ondemand.sh --rollback  always-loaded llama-server again
# keepalive service "llama" becomes the s22-llama-ondemand proxy on :8090; the
# old llama-server definition is kept as service_defs.llama_backend (port 8091,
# started by the proxy, not by keepalive). Config backup: config.json.pre-ondemand.
set -eu
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
BIN=/srv/s22/hardware/bin
KCONF=/srv/s22/state/keepalive/config.json
KA=$BIN/s22-keepalive
pids_prefix() {
  for p in /proc/[0-9]*; do
    [ -r "$p/cmdline" ] || continue
    case "$(tr '\0' ' ' 2>/dev/null < $p/cmdline)" in "$1"*) echo "${p#/proc/}";; esac
  done
  return 0
}
restart_keepalive() {
  for p in $(pids_prefix "/usr/bin/python3 $KA"); do kill "$p"; done
  sleep 2
  ( cd / && setsid /usr/bin/python3 "$KA" </dev/null >>/srv/s22/state/keepalive/daemon.log 2>&1 & )
  sleep 2
}
stop_llama_servers() {   # the always-on one or the proxy's backend: exact PIDs
  for p in $(pids_prefix "/system/bin/llama-server -m /models/Qwen3.5-0.8B-Q4_0.gguf"); do kill "$p"; done
  sleep 3
}

if [ "${1:-}" = --rollback ]; then
  [ -f "$KCONF.pre-ondemand" ] || { echo "no $KCONF.pre-ondemand" >&2; exit 1; }
  cp -p "$KCONF.pre-ondemand" "$KCONF"
  restart_keepalive
  for p in $(pids_prefix "/usr/bin/python3 $BIN/s22-llama-ondemand serve"); do kill "$p"; done
  stop_llama_servers
  echo "rolled back: keepalive starts the always-loaded llama-server on :8090 again"
  exit 0
fi

[ -f "$KCONF.pre-ondemand" ] || cp -p "$KCONF" "$KCONF.pre-ondemand"
install -m 755 "$HERE/s22-llama-ondemand.py" "$BIN/s22-llama-ondemand"
python3 - "$KCONF" <<'PY'
import json, os, sys
p = sys.argv[1]
cfg = json.load(open(p))
defs = cfg.setdefault('service_defs', {})
old = defs.get('llama')
if old and 's22-llama-ondemand' not in ' '.join(old.get('argv', [])):
    defs['llama_backend'] = old
defs['llama'] = {'argv': ['/usr/bin/python3', '/srv/s22/hardware/bin/s22-llama-ondemand', 'serve'],
                 'needle': '/srv/s22/hardware/bin/s22-llama-ondemand serve',
                 'health': {'http': 'http://127.0.0.1:8090/health'},
                 'requires': '/srv/s22/hardware/bin/s22-llama-ondemand',
                 'log': '/srv/s22/state/llama-ondemand/serve.log', 'start_wait_s': 15}
if 'llama' not in cfg.setdefault('services', []):
    cfg['services'].append('llama')
json.dump(cfg, open(p + '.tmp', 'w'), indent=2)
os.replace(p + '.tmp', p)
PY
mkdir -p /srv/s22/state/llama-ondemand
restart_keepalive        # new config first, so the old keepalive cannot restart llama-server on :8090
stop_llama_servers       # frees :8090; keepalive starts the proxy on its next pass
echo "installed; status: python3 $BIN/s22-llama-ondemand status   rollback: sh $HERE/install-llama-ondemand.sh --rollback"
