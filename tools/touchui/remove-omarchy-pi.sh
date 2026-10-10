#!/bin/sh
# Remove the Omarchy desktop layer and the Pi agent terminal from the S22
# (owner, 2026-10-10: "keep only what we need, no multiple harnesses; maybe
# only OpenUnum"). Native root, as root, from a copy of the repo's tools/ tree:
#   sh tools/touchui/remove-omarchy-pi.sh             remove (idempotent)
#   sh tools/touchui/remove-omarchy-pi.sh --rollback  put everything back
#
# Nothing is deleted: every path is MOVED into $ARCHIVE (same filesystem,
# instant) and recorded in $ARCHIVE/manifest.tsv; --rollback moves it back.
# Kept: the Arch chroot and its packages, sway + the touch shell, OpenUnum,
# voice/converse, phoned, unumsearch, tailscale, Hyprland (fallback only, now
# with the plain tools/touchui/sway/hyprland-s22.lua instead of Omarchy).
set -eu
C=${C:-/mnt/omarchy-trial}
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
TOOLS=$(dirname "$HERE")
ARCHIVE=${ARCHIVE:-/srv/s22/archive/omarchy-pi-20261010}
MAN=$ARCHIVE/manifest.tsv
SPD=${SPD:-/usr/local/bin/start-persistent-desktop}

PATHS="
$C/opt/s22-pi
$C/opt/s22-pi-web
$C/opt/s22-ui
$C/opt/omarchy-source
$C/usr/local/bin/pi
$C/usr/local/bin/pi-research
$C/usr/local/bin/pi-rig
$C/usr/local/bin/pi-web-session
$C/usr/local/bin/omarchy-launch-tui
$C/usr/local/bin/omarchy-battery-status
$C/usr/local/bin/s22-kbd-bindings
$C/usr/local/bin/s22-kbd-bindings.pre-offfix
$C/opt/s22-touch/omarchy-keyboard.lua
$C/opt/s22-touch/omarchy-keyboard.lua.pre-offfix
$C/opt/s22-touch/sway-keyboard.conf
$C/root/.openunum/skills/custom/s22-omarchy-actions
$C/root/.config/s22/omarchy-keyboard.enabled
$C/root/hyprland-omarchy-ui.lua
$C/root/hyprland-omarchy-display.lua
$C/root/hyprland-trial.lua
/srv/s22/agent-web/enabled
"

pids_of() {   # exact command-line prefix match -> PIDs (never a name-wide kill)
  for p in /proc/[0-9]*; do
    [ -r "$p/cmdline" ] || continue
    case "$(tr '\0' ' ' 2>/dev/null < $p/cmdline)" in "$1"*) echo "${p#/proc/}";; esac
  done
  return 0
}

if [ "${1:-}" = --rollback ]; then
  [ -f "$MAN" ] || { echo "no manifest at $MAN" >&2; exit 1; }
  tac "$MAN" 2>/dev/null > "$MAN.rev" || awk '{a[NR]=$0} END{for(i=NR;i;i--)print a[i]}' "$MAN" > "$MAN.rev"
  while IFS='	' read -r src dst; do
    [ -e "$dst" ] || continue
    mkdir -p "$(dirname "$src")"
    [ -e "$src" ] && { echo "skip (exists): $src"; continue; }
    mv "$dst" "$src" && echo "restored $src"
  done < "$MAN.rev"
  rm -f "$MAN.rev" "$MAN"
  [ -f "$SPD.pre-noomarchy" ] && cp -p "$SPD.pre-noomarchy" "$SPD" && echo "restored $SPD"
  [ -f "$C/root/s22-sway-pixman.conf.pre-noomarchy" ] && cp -p "$C/root/s22-sway-pixman.conf.pre-noomarchy" "$C/root/s22-sway-pixman.conf"
  rm -f "$C/root/hyprland-s22.lua"
  echo "rolled back; the touch shell/s22-ui/plugin: sh $TOOLS/touchui/install-touchui.sh from the previous tree"
  echo "takes full effect at the next desktop start (s22-reboot recovery)"
  exit 0
fi

mkdir -p "$ARCHIVE"
touch "$MAN"
for src in $PATHS; do
  [ -e "$src" ] || continue
  dst=$ARCHIVE$src
  mkdir -p "$(dirname "$dst")"
  mv "$src" "$dst"
  printf '%s\t%s\n' "$src" "$dst" >> "$MAN"
  echo "archived $src"
done

# new configs (backups for --rollback)
[ -f "$SPD.pre-noomarchy" ] || cp -p "$SPD" "$SPD.pre-noomarchy"
install -m 755 "$TOOLS/persistence/start-persistent-desktop.py" "$SPD"
[ -f "$C/root/s22-sway-pixman.conf.pre-noomarchy" ] || cp -p "$C/root/s22-sway-pixman.conf" "$C/root/s22-sway-pixman.conf.pre-noomarchy"
install -m 644 "$TOOLS/touchui/phase2/s22-sway-pixman.conf" "$C/root/s22-sway-pixman.conf"
install -m 644 "$TOOLS/touchui/sway/hyprland-s22.lua" "$C/root/hyprland-s22.lua"

# stop the running Pi terminal (its binaries are archived): exact PIDs only
for p in $(pids_of "foot --app-id=org.omarchy.agent pi"); do kill "$p" && echo "stopped foot/pi pid $p"; done
for p in $(pids_of "/opt/s22-pi/"); do kill "$p" && echo "stopped pi pid $p"; done
[ -f /srv/s22/agent-web/start-agent-web.py ] && python3 /srv/s22/agent-web/start-agent-web.py --stop >/dev/null 2>&1 || true

echo "archive: $ARCHIVE ($(du -sh "$ARCHIVE" | cut -f1)); manifest: $MAN"
echo "next: sh $TOOLS/touchui/install-touchui.sh   (touch shell / s22-ui / OpenUnum plugin without the Pi app)"
echo "rollback: sh $HERE/remove-omarchy-pi.sh --rollback"
