#!/bin/sh
# Install s22-netpolicy (uplink policy) + s22-net-deadman on the phone (native root, as root).
# Stage this directory under /srv/s22/network-policy/ first (the root fs is small).
#   sh install-netpolicy.sh            install binary + command, add the keepalive
#                                      service "netpolicy" (picked up when keepalive
#                                      next starts, i.e. at boot), start it now.
#   sh install-netpolicy.sh --remove   stop it, drop the keepalive service, remove.
# Kill switch without uninstalling: s22-netpolicy off   (back on: s22-netpolicy on)
# off also restores the boot-time routes (ecm0 default metric 0).
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
BIN=/srv/s22/hardware/bin/s22-netpolicy
KA=/srv/s22/state/keepalive/config.json
NEEDLE="$BIN run"

stop_running() {
  for p in /proc/[0-9]*; do
    c=$(tr '\0' ' ' < "$p/cmdline" 2>/dev/null) || continue
    case "$c" in "/usr/bin/python3 $NEEDLE"*|"python3 $NEEDLE"*) kill -TERM "${p#/proc/}" && echo "stopped ${p#/proc/}";; esac
  done
}

ka_edit() {  # $1 = add|remove
  [ -f "$KA" ] || { echo "no $KA, keepalive service not configured"; return 0; }
  cp "$KA" "$KA.bak-netpolicy-$(date +%Y%m%dT%H%M%S)"
  python3 - "$1" "$KA" "$BIN" <<'EOF'
import json, sys, os
op, path, binp = sys.argv[1:4]
c = json.load(open(path))
svcs = c.setdefault("services", [])
defs = c.setdefault("service_defs", {})
if op == "add":
    if "netpolicy" not in svcs:
        svcs.append("netpolicy")
    defs["netpolicy"] = {"argv": ["/usr/bin/python3", binp, "run"], "needle": binp + " run",
                         "requires": binp, "log": "/srv/s22/state/netpolicy/daemon.out",
                         "start_wait_s": 5}
else:
    if "netpolicy" in svcs:
        svcs.remove("netpolicy")
    defs.pop("netpolicy", None)
tmp = path + ".tmp"
open(tmp, "w").write(json.dumps(c, indent=1) + "\n")
os.replace(tmp, path)
print("keepalive config:", op, "netpolicy")
EOF
}

if [ "${1:-}" = --remove ]; then
  stop_running
  ka_edit remove
  python3 "$BIN" off >/dev/null 2>&1 || true
  rm -f /usr/local/bin/s22-netpolicy "$BIN"
  echo removed
  exit 0
fi

mkdir -p /srv/s22/state/netpolicy
STAMP=$(date +%Y%m%dT%H%M%S)
for f in "$BIN" /srv/s22/hardware/bin/s22-net-deadman; do [ -f "$f" ] && cp "$f" "$f.bak-$STAMP"; done
install -m 0755 "$HERE/s22-netpolicy.py" "$BIN"
install -m 0755 "$HERE/s22-net-deadman.sh" /srv/s22/hardware/bin/s22-net-deadman
ln -sf /srv/s22/hardware/bin/s22-net-deadman /usr/local/bin/s22-net-deadman
ln -sf "$BIN" /usr/local/bin/s22-netpolicy
ka_edit add
stop_running
setsid nohup /usr/bin/python3 "$BIN" run >>/srv/s22/state/netpolicy/daemon.out 2>&1 </dev/null &
echo "installed; daemon pid $!; $(s22-netpolicy status | head -1)"
