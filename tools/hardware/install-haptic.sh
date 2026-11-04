#!/bin/sh
# Install the s22-haptic daemon (CS40L26 vibration, /run/s22-haptic.sock) as the keepalive service
# "haptics", so key ticks and card buzzes work after every reboot. Native root, as root;
# docs/S22_HAPTICS.md. The live phone already has exactly this service (read 2026-11-04: the daemon was up
# since boot); this script reproduces it on a fresh install and is idempotent: an existing service with the
# same needle is kept, a running daemon is left alone (restart it yourself after a binary upgrade).
#   sh install-haptic.sh            install/upgrade the binary, ensure the keepalive service, start if absent
#   sh install-haptic.sh --remove   stop it, drop the keepalive service, remove the binary
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
BIN=/srv/s22/hardware/bin/s22-haptic
KA=/srv/s22/state/keepalive/config.json
NEEDLE="$BIN serve"
LOG=/srv/s22/state/haptics/daemon.out

daemon_pids() {   # every running daemon, also one started by hand without the python3 prefix
  for p in /proc/[0-9]*; do
    pid=${p#/proc/}
    { [ "$pid" = $$ ] || [ "$pid" = "$PPID" ]; } && continue      # never this installer or its caller
    c=$( { tr '\0' ' ' < "$p/cmdline"; } 2>/dev/null) || continue     # a process may exit meanwhile
    case "$c" in *"$NEEDLE"*) echo "$pid";; esac
  done
}

ka_edit() {  # $1 = add|remove
  [ -f "$KA" ] || { echo "no $KA, keepalive service not configured"; return 0; }
  cp "$KA" "$KA.bak-haptics-$(date +%Y%m%dT%H%M%S)"
  python3 - "$1" "$KA" "$BIN" <<'EOF'
import json, sys, os
op, path, binp = sys.argv[1:4]
needle = binp + " serve"
c = json.load(open(path))
svcs = c.setdefault("services", [])
defs = c.setdefault("service_defs", {})
same = [n for n, d in defs.items() if isinstance(d, dict) and d.get("needle") == needle]
if op == "add":
    name = same[0] if same else "haptics"           # keep an existing entry (the live phone has "haptics")
    defs.setdefault(name, {"argv": ["/usr/bin/python3", binp, "serve"], "needle": needle, "requires": binp,
                           "log": "/srv/s22/state/haptics/daemon.out", "start_wait_s": 3})
    if name not in svcs:
        svcs.append(name)
else:
    for name in same:
        defs.pop(name, None)
        if name in svcs:
            svcs.remove(name)
tmp = path + ".tmp"
open(tmp, "w").write(json.dumps(c, indent=1) + "\n")
os.replace(tmp, path)
print("keepalive config:", op, same or ["haptics"])
EOF
}

if [ "${1:-}" = --remove ]; then
  for pid in $(daemon_pids); do kill -TERM "$pid" && echo "stopped $pid"; done
  ka_edit remove
  rm -f /usr/local/bin/s22-haptic "$BIN"
  echo removed
  exit 0
fi

mkdir -p /srv/s22/state/haptics
[ -f "$BIN" ] && cp "$BIN" "$BIN.bak-$(date +%Y%m%dT%H%M%S)"
install -m 0755 "$HERE/s22-haptic.py" "$BIN"
ln -sf "$BIN" /usr/local/bin/s22-haptic
ka_edit add
running=$(daemon_pids)
if [ -n "$running" ]; then
  echo "daemon already running (pid $running); restart it to load a new binary"
else
  setsid nohup /usr/bin/python3 "$BIN" serve >>"$LOG" 2>&1 </dev/null &
  echo "installed; haptics pid $!"
fi
