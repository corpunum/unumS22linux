#!/bin/sh
# Install s22-charge-limit as a keepalive service (re-applies the battery charge limit at boot and when
# it drifts). Native root, as root. It sets NO limit by itself: after installing, choose one with
#   s22-charge-limit set 85      (stop charging at 85 %, charge again at 83 %; off = 100)
# Stage this directory under /srv/s22/ first (the root fs is small).
#   sh install-charge-limit.sh            install, add the keepalive service "chargelimit" (supervised
#                                     from the next keepalive start, i.e. boot), start it now
#   sh install-charge-limit.sh --remove   stop it, drop the keepalive service, remove the binary
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
BIN=/srv/s22/hardware/bin/s22-charge-limit
KA=/srv/s22/state/keepalive/config.json
NEEDLE="$BIN run"

stop_running() {
  for p in /proc/[0-9]*; do
    c=$(tr '\0' ' ' < "$p/cmdline" 2>/dev/null) || continue
    case "$c" in "/usr/bin/python3 $NEEDLE"*) kill -TERM "${p#/proc/}" && echo "stopped ${p#/proc/}";; esac
  done
}

ka_edit() {  # $1 = add|remove
  [ -f "$KA" ] || { echo "no $KA, keepalive service not configured"; return 0; }
  cp "$KA" "$KA.bak-chargelimit-$(date +%Y%m%dT%H%M%S)"
  python3 - "$1" "$KA" "$BIN" <<'EOF'
import json, sys, os
op, path, binp = sys.argv[1:4]
c = json.load(open(path))
svcs = c.setdefault("services", [])
defs = c.setdefault("service_defs", {})
if op == "add":
    if "chargelimit" not in svcs:
        svcs.append("chargelimit")
    defs["chargelimit"] = {"argv": ["/usr/bin/python3", binp, "run"], "needle": binp + " run",
                     "requires": binp, "log": "/srv/s22/state/charge-limit/daemon.out",
                     "start_wait_s": 5, "nice": 10}
else:
    if "chargelimit" in svcs:
        svcs.remove("chargelimit")
    defs.pop("chargelimit", None)
tmp = path + ".tmp"
open(tmp, "w").write(json.dumps(c, indent=1) + "\n")
os.replace(tmp, path)
print("keepalive config:", op, "chargelimit")
EOF
}

if [ "${1:-}" = --remove ]; then
  stop_running
  ka_edit remove
  rm -f /usr/local/bin/s22-charge-limit "$BIN"
  echo removed
  exit 0
fi

mkdir -p /srv/s22/state/charge-limit
[ -f "$BIN" ] && cp "$BIN" "$BIN.bak-$(date +%Y%m%dT%H%M%S)"
install -m 0755 "$HERE/s22-charge-limit.py" "$BIN"
ln -sf "$BIN" /usr/local/bin/s22-charge-limit
ka_edit add
stop_running
sleep 1
setsid nohup /usr/bin/python3 "$BIN" run >>/srv/s22/state/charge-limit/daemon.out 2>&1 </dev/null &
echo "installed; chargelimit pid $!"
echo "no limit is set until: s22-charge-limit set 85   (check: s22-charge-limit status)"
