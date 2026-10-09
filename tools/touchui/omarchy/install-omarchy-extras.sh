#!/bin/sh
# Install (or roll back) the S22 Omarchy extras. Run ON THE PHONE as root, on the native
# (Alpine) root, from a copy of the repo's tools/ tree:
#   sh tools/touchui/omarchy/install-omarchy-extras.sh             install / update (idempotent)
#   sh tools/touchui/omarchy/install-omarchy-extras.sh --rollback  undo everything this installed
#
# Installs (all inside the Arch chroot $C):
#   $C/root/.openunum/skills/custom/s22-omarchy-actions/SKILL.md   instruction skill for the phone agent
#   $C/opt/s22-touch/omarchy-keyboard.lua                         curated hardware-keyboard bindings (OFF)
#   $C/usr/local/bin/s22-kbd-bindings                             on|off|status helper for them
# It does NOT edit Hyprland's config, restart anything, or bind any key. The keyboard
# bindings stay off until someone runs `s22-kbd-bindings on`.
# OpenUnum discovers SKILL.md folders at runtime, so no OpenUnum restart is needed.
# Backups: /srv/s22/state/omarchy-extras/backup-<timestamp>/ (rollback uses the newest one).
set -eu
C=${C:-/mnt/omarchy-trial}
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
STATE=${STATE:-/srv/s22/state/omarchy-extras}
SKILL=$C/root/.openunum/skills/custom/s22-omarchy-actions
LUA=$C/opt/s22-touch/omarchy-keyboard.lua
HELPER=$C/usr/local/bin/s22-kbd-bindings
FLAG=$C/root/.config/s22/omarchy-keyboard.enabled

in_chroot() { chroot "$C" /usr/bin/env -i PATH=/usr/local/bin:/usr/bin:/bin HOME=/root XDG_RUNTIME_DIR=/run/user/0 "$@"; }

if [ "${1:-}" = "--rollback" ]; then
  B=$(ls -d "$STATE"/backup-* 2>/dev/null | sort | tail -1 || true)
  echo "rollback (backup: ${B:-none})"
  # unbind first if someone switched the bindings on
  if [ -x "$HELPER" ]; then in_chroot s22-kbd-bindings off || true; fi
  rm -f "$FLAG"
  rm -rf "$SKILL"
  rm -f "$LUA" "$HELPER"
  if [ -n "$B" ]; then
    [ -f "$B/SKILL.md" ] && mkdir -p "$SKILL" && cp -p "$B/SKILL.md" "$SKILL/SKILL.md"
    [ -f "$B/omarchy-keyboard.lua" ] && cp -p "$B/omarchy-keyboard.lua" "$LUA"
    [ -f "$B/s22-kbd-bindings" ] && cp -p "$B/s22-kbd-bindings" "$HELPER"
    true
  fi
  echo "rolled back."
  exit 0
fi

for f in SKILL.md omarchy-keyboard.lua s22-kbd-bindings.sh; do
  [ -f "$HERE/$f" ] || { echo "missing $HERE/$f" >&2; exit 1; }
done
[ -d "$C/root" ] || { echo "chroot not found at $C" >&2; exit 1; }
[ -d "$C/opt/s22-touch" ] || { echo "touch UI (PR #19) not installed: $C/opt/s22-touch missing" >&2; exit 1; }

TS=$(date +%Y%m%d-%H%M%S)
BK=$STATE/backup-$TS
mkdir -p "$BK"
[ -f "$SKILL/SKILL.md" ] && cp -p "$SKILL/SKILL.md" "$BK/"
[ -f "$LUA" ] && cp -p "$LUA" "$BK/"
[ -f "$HELPER" ] && cp -p "$HELPER" "$BK/"
true
echo "backup: $BK"

mkdir -p "$SKILL"
install -m 644 "$HERE/SKILL.md" "$SKILL/SKILL.md"
install -m 644 "$HERE/omarchy-keyboard.lua" "$LUA"
install -m 755 "$HERE/s22-kbd-bindings.sh" "$HELPER"
echo "installed: $SKILL/SKILL.md, $LUA, $HELPER"
echo "keyboard bindings: $(in_chroot s22-kbd-bindings status 2>/dev/null || echo 'status unavailable (no Hyprland session)')"
echo "rollback: sh $HERE/install-omarchy-extras.sh --rollback"
