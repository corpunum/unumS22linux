#!/bin/sh
# Install s22-autosleep on the phone (native root, as root).
# Stage this directory under /srv/s22/power/ first (the root fs is small).
#   sh install-autosleep.sh            install binary + command, add the keepalive
#                                      service "autosleep" (picked up when keepalive
#                                      next starts, i.e. at boot), start it now.
#   sh install-autosleep.sh --remove   stop it, drop the keepalive service, remove.
# Kill switch without uninstalling: s22-autosleep off   (back on: s22-autosleep on)
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
BIN=/srv/s22/hardware/bin/s22-autosleep
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
  cp "$KA" "$KA.bak-autosleep-$(date +%Y%m%dT%H%M%S)"
  python3 - "$1" "$KA" "$BIN" <<'EOF'
import json, sys, os
op, path, binp = sys.argv[1:4]
c = json.load(open(path))
svcs = c.setdefault("services", [])
defs = c.setdefault("service_defs", {})
if op == "add":
    if "autosleep" not in svcs:
        svcs.append("autosleep")
    defs["autosleep"] = {"argv": ["/usr/bin/python3", binp, "run"], "needle": binp + " run",
                         "requires": binp, "log": "/srv/s22/state/autosleep/daemon.out",
                         "start_wait_s": 5}
else:
    if "autosleep" in svcs:
        svcs.remove("autosleep")
    defs.pop("autosleep", None)
tmp = path + ".tmp"
open(tmp, "w").write(json.dumps(c, indent=1) + "\n")
os.replace(tmp, path)
print("keepalive config:", op, "autosleep")
EOF
}

if [ "${1:-}" = --remove ]; then
  stop_running
  ka_edit remove
  rm -f /usr/local/bin/s22-autosleep "$BIN"
  for p in /sys/devices/system/cpu/cpufreq/policy*; do echo schedutil > "$p/scaling_governor"; done
  echo removed
  exit 0
fi

mkdir -p /srv/s22/state/autosleep
install -m 0755 "$HERE/s22-autosleep.py" "$BIN"
ln -sf "$BIN" /usr/local/bin/s22-autosleep
ka_edit add
stop_running
setsid nohup /usr/bin/python3 "$BIN" run >>/srv/s22/state/autosleep/daemon.out 2>&1 </dev/null &
echo "installed; daemon pid $!; $(s22-autosleep status | head -1)"
