#!/bin/sh
# Phone-side install for unum-shell. Run as root on the native Alpine root.
#   sh install-unum-shell.sh --binary /path/to/unum-shell     copy the binary into the chroot (does NOT switch to it)
#   sh install-unum-shell.sh --activate                       switch s22-touchd to unum-shell
#   sh install-unum-shell.sh --rollback                       switch back to Quickshell
# Quickshell stays installed and untouched at all times; rollback is a one-line profile file.
set -eu
C=${C:-/mnt/omarchy-trial}
PROFILE=/srv/s22/state/touchui/ui-backend
DEST=$C/usr/local/bin/unum-shell
case "${1:-}" in
  --binary)
    SRC=${2:?--binary needs a path}
    [ -x "$SRC" ] || { echo "missing executable $SRC" >&2; exit 1; }
    [ -d "$C/usr/local/bin" ] || { echo "chroot not found at $C" >&2; exit 1; }
    [ -f "$DEST" ] && cp -p "$DEST" "$DEST.prev" || true
    install -m 755 "$SRC" "$DEST.new" && mv "$DEST.new" "$DEST"
    chroot "$C" /usr/local/bin/unum-shell --version
    echo "installed $DEST; still on $(cat "$PROFILE" 2>/dev/null || echo quickshell). Switch with --activate."
    ;;
  --activate)
    [ -x "$DEST" ] || { echo "install the binary first (--binary)" >&2; exit 1; }
    pgrep -f s22-touchd >/dev/null || echo "warning: s22-touchd is not running; it applies the profile when it starts" >&2
    mkdir -p "$(dirname "$PROFILE")"
    echo unum-shell > "$PROFILE"
    echo "profile set: s22-touchd stops Quickshell and starts unum-shell within a few seconds."
    echo "rollback: sh $0 --rollback"
    ;;
  --rollback)
    mkdir -p "$(dirname "$PROFILE")"
    echo quickshell > "$PROFILE"
    echo "profile set to quickshell: s22-touchd restores the Quickshell touch shell within a few seconds."
    ;;
  *)
    echo "usage: $0 --binary PATH | --activate | --rollback" >&2; exit 2 ;;
esac
