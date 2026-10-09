#!/bin/sh
# Install (or roll back) the S22 touch UI, Phase 1. Run ON THE PHONE as root, on the
# native (Alpine) root, from a copy of the repo's tools/ tree:
#   sh tools/touchui/install-touchui.sh             install / update (idempotent)
#   sh tools/touchui/install-touchui.sh --rollback  undo everything this installed
#
# Installs:
#   /srv/s22/hardware/bin/s22-touchd           host daemon: UI supervisor, gestures, control socket
#   $C/usr/local/bin/s22-ui                    action CLI inside the Arch chroot
#   $C/opt/s22-touch/*.qml                     the touch shell (separate Quickshell instance)
#   /srv/s22/buttons/hooks/power               Power key: lock cover + screen off / screen on
#   $C/root/.openunum/plugins/s22-ui           OpenUnum tools (active after an OpenUnum restart)
#   keepalive service "touchui"                s22-touchd supervised by s22-keepalive (next keepalive start)
#   gsettings a11y screen-keyboard-enabled=true  (squeekboard shows itself for text fields)
# It does NOT change Hyprland's config, the Omarchy shell, audio, or OpenUnum's source/config.
# Backups: /srv/s22/state/touchui/backup-<timestamp>/ (the rollback uses the newest one).
set -eu
C=${C:-/mnt/omarchy-trial}
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
TOOLS=$(dirname "$HERE")
STATE=/srv/s22/state/touchui
BIN=/srv/s22/hardware/bin
HOOK=/srv/s22/buttons/hooks/power
KCONF=/srv/s22/state/keepalive/config.json
PLUG=$C/root/.openunum/plugins/s22-ui

hypr_bus() {   # DBus session address of the running Hyprland session (for gsettings)
  for p in $(pidof Hyprland 2>/dev/null); do
    tr '\0' '\n' < /proc/$p/environ | sed -n 's/^DBUS_SESSION_BUS_ADDRESS=//p' && return 0
  done
  return 1
}
gs() { B=$(hypr_bus) || return 1; chroot "$C" /usr/bin/env XDG_RUNTIME_DIR=/run/user/0 DBUS_SESSION_BUS_ADDRESS="$B" gsettings "$@"; }

touchd_pids() {   # exact match: python3 running s22-touchd as a daemon (no client args)
  for p in /proc/[0-9]*; do
    [ "$(tr '\0' ' ' < $p/cmdline 2>/dev/null)" = "/usr/bin/python3 $BIN/s22-touchd " ] && echo "${p#/proc/}"
  done
  return 0
}

keepalive_conf() {  # $1 = add|remove
  [ -f "$KCONF" ] || { echo "no $KCONF, skipping keepalive"; return 0; }
  python3 - "$KCONF" "$1" "$BIN/s22-touchd" <<'PY'
import json, os, sys
path, mode, touchd = sys.argv[1:4]
cfg = json.load(open(path))
svcs = cfg.setdefault('services', [])
defs = cfg.setdefault('service_defs', {})
if mode == 'add':
    if 'touchui' not in svcs:
        svcs.append('touchui')
    defs['touchui'] = {'argv': ['/usr/bin/python3', touchd], 'needle': f'python3 {touchd}',
                       'requires': touchd, 'log': '/srv/s22/state/touchui/touchd.out', 'start_wait_s': 15}
else:
    cfg['services'] = [s for s in svcs if s != 'touchui']
    defs.pop('touchui', None)
tmp = path + '.touchui-tmp'
json.dump(cfg, open(tmp, 'w'), indent=2)
os.replace(tmp, path)
print('keepalive config:', mode, 'touchui')
PY
}

if [ "${1:-}" = "--rollback" ]; then
  B=$(ls -d "$STATE"/backup-* 2>/dev/null | sort | tail -1 || true)
  echo "rollback (backup: ${B:-none})"
  for p in $(touchd_pids); do echo "stop s22-touchd pid $p"; kill "$p"; done
  sleep 3
  for p in $(touchd_pids); do kill -9 "$p"; done
  if [ -n "$B" ] && [ -f "$B/power.hook" ]; then cp -p "$B/power.hook" "$HOOK"; else rm -f "$HOOK"; fi
  if [ -n "$B" ] && [ -f "$B/keepalive-config.json" ]; then cp -p "$B/keepalive-config.json" "$KCONF"; else keepalive_conf remove; fi
  if [ -n "$B" ] && [ -f "$B/gsettings-screen-keyboard" ]; then
    gs set org.gnome.desktop.a11y.applications screen-keyboard-enabled "$(cat "$B/gsettings-screen-keyboard")" || true
  fi
  rm -rf "$C/opt/s22-touch" "$PLUG" "$C/run/s22-touch"
  rm -f "$C/usr/local/bin/s22-ui" "$BIN/s22-touchd"
  # leave the keyboard visible, as the pre-touch-UI session did
  B2=$(hypr_bus) && chroot "$C" /usr/bin/env XDG_RUNTIME_DIR=/run/user/0 DBUS_SESSION_BUS_ADDRESS="$B2" gdbus call --session \
    --dest sm.puri.OSK0 --object-path /sm/puri/OSK0 --method sm.puri.OSK0.SetVisible true >/dev/null 2>&1 || true
  echo "rolled back. grim/wtype stay installed (remove: chroot $C pacman -R grim wtype)."
  echo "If the s22-ui OpenUnum plugin was loaded: s22-openunum stop && s22-openunum start"
  exit 0
fi

for f in touchui/s22-touchd.py touchui/s22-ui.py touchui/power-hook.sh touchui/shell/shell.qml openunum-phone/plugins/s22-ui/index.mjs; do
  [ -f "$TOOLS/$f" ] || { echo "missing $TOOLS/$f" >&2; exit 1; }
done
[ -d "$C/root" ] || { echo "chroot not found at $C" >&2; exit 1; }
chroot "$C" sh -c 'command -v grim >/dev/null' || echo "WARNING: grim missing in the chroot (screenshots). Install with:
  chroot $C /usr/local/libexec/s22-close-range-compat -- pacman -S --needed --disable-sandbox grim wtype
  (plain pacman spins forever in the kernel on this vendor kernel: close_range + Landlock sandbox)"

TS=$(date +%Y%m%d-%H%M%S)
BK=$STATE/backup-$TS
mkdir -p "$BK"
[ -f "$HOOK" ] && cp -p "$HOOK" "$BK/power.hook"
[ -f "$KCONF" ] && cp -p "$KCONF" "$BK/keepalive-config.json"
gs get org.gnome.desktop.a11y.applications screen-keyboard-enabled > "$BK/gsettings-screen-keyboard" 2>/dev/null || rm -f "$BK/gsettings-screen-keyboard"
for f in "$BIN/s22-touchd" "$C/usr/local/bin/s22-ui"; do [ -f "$f" ] && cp -p "$f" "$BK/"; done
[ -d "$C/opt/s22-touch" ] && cp -a "$C/opt/s22-touch" "$BK/opt-s22-touch"
echo "backup: $BK"

install -m 755 "$TOOLS/touchui/s22-touchd.py" "$BIN/s22-touchd"
install -m 755 "$TOOLS/touchui/s22-ui.py" "$C/usr/local/bin/s22-ui"
mkdir -p "$C/opt/s22-touch"
cp "$TOOLS"/touchui/shell/*.qml "$C/opt/s22-touch/"
mkdir -p "$(dirname "$HOOK")"
install -m 755 "$TOOLS/touchui/power-hook.sh" "$HOOK"
keepalive_conf add
gs set org.gnome.desktop.a11y.applications screen-keyboard-enabled true || echo "gsettings: no Hyprland session, skipped"

# OpenUnum plugin (same pattern as s22-phone): "_" prefix keeps a half-copied tree invisible to the loader
mkdir -p "$C/root/.openunum/plugins"
TMP=$C/root/.openunum/plugins/_s22-ui.installing.$$
rm -rf "$TMP"; mkdir -p "$TMP"
cp "$TOOLS/openunum-phone/plugins/s22-ui/plugin.json" "$TOOLS/openunum-phone/plugins/s22-ui/index.mjs" "$TMP/"
cp -R "$TOOLS/openunum-phone/plugins/s22-ui/lib" "$TMP/lib"
rm -rf "$PLUG"; mv "$TMP" "$PLUG"; chmod -R u=rwX,go=rX "$PLUG"

# (re)start the daemon so it runs the new code; s22-keepalive takes over supervision at its next start
for p in $(touchd_pids); do kill "$p"; done
sleep 2
for p in $(touchd_pids); do kill -9 "$p"; done
mkdir -p "$STATE"
( cd / && setsid /usr/bin/python3 "$BIN/s22-touchd" </dev/null >>"$STATE/touchd.out" 2>&1 & )
sleep 3
echo "s22-touchd pid(s): $(touchd_pids | tr '\n' ' ')"
echo "OpenUnum tools: active after  s22-openunum stop && s22-openunum start"
echo "rollback:       sh $HERE/install-touchui.sh --rollback"
