#!/bin/sh
# Install s22-turnguard (hard per-turn limits for the phone OpenUnum, issue #75) on the phone (native root, as root).
# Stage this directory under /srv/s22/ first (the root fs is small).
#   sh install-turnguard.sh            install, add the keepalive service "turnguard" (supervised
#                                  from the next keepalive start, i.e. boot), start it now
#   sh install-turnguard.sh --remove   stop it, drop the keepalive service, remove the binary
# Pause without uninstalling: s22-turnguard off   (back: s22-turnguard on)
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
BIN=/srv/s22/hardware/bin/s22-turnguard
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
  cp "$KA" "$KA.bak-turnguard-$(date +%Y%m%dT%H%M%S)"
  python3 - "$1" "$KA" "$BIN" <<'EOF'
import json, sys, os
op, path, binp = sys.argv[1:4]
c = json.load(open(path))
svcs = c.setdefault("services", [])
defs = c.setdefault("service_defs", {})
if op == "add":
    if "turnguard" not in svcs:
        svcs.append("turnguard")
    defs["turnguard"] = {"argv": ["/usr/bin/python3", binp, "run"], "needle": binp + " run",
                     "requires": binp, "log": "/srv/s22/state/turnguard/daemon.out",
                     "start_wait_s": 5}
else:
    if "turnguard" in svcs:
        svcs.remove("turnguard")
    defs.pop("turnguard", None)
tmp = path + ".tmp"
open(tmp, "w").write(json.dumps(c, indent=1) + "\n")
os.replace(tmp, path)
print("keepalive config:", op, "turnguard")
EOF
}

if [ "${1:-}" = --remove ]; then
  stop_running
  ka_edit remove
  rm -f /usr/local/bin/s22-turnguard "$BIN"
  echo removed
  exit 0
fi

mkdir -p /srv/s22/state/turnguard
[ -f "$BIN" ] && cp "$BIN" "$BIN.bak-$(date +%Y%m%dT%H%M%S)"
install -m 0755 "$HERE/s22-turnguard.py" "$BIN"
ln -sf "$BIN" /usr/local/bin/s22-turnguard
ka_edit add
stop_running
sleep 1
setsid nohup /usr/bin/python3 "$BIN" run >>/srv/s22/state/turnguard/daemon.out 2>&1 </dev/null &
echo "installed; turnguard pid $!"
