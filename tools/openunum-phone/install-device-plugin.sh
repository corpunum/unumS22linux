#!/bin/sh
# Run on the S22 native host. Install the daemon-backed mobile edition plugin into the OpenUnum chroot.
set -eu
C=${C:-/mnt/omarchy-trial}
SRC=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)/plugins/s22-device
DEST_PARENT=$C/root/.openunum/plugins
DEST=$DEST_PARENT/s22-device
[ -f "$SRC/plugin.json" ] && [ -f "$SRC/index.mjs" ] || { echo "source plugin missing: $SRC" >&2; exit 1; }
[ -d "$C/root" ] || { echo "chroot not found at $C (set C=...)" >&2; exit 1; }
[ -d /srv/s22 ] || { echo "refusing install: /srv/s22 marker missing" >&2; exit 1; }
mkdir -p "$DEST"
cp "$SRC/plugin.json" "$SRC/index.mjs" "$DEST/"
mkdir -p "$DEST/lib"
cp "$SRC/lib/plugin-base.mjs" "$DEST/lib/"
chmod 644 "$DEST/plugin.json" "$DEST/index.mjs"
echo "installed s22-device -> $DEST"
echo "enable by adding \"s22-device\": {\"enabled\": true} to $C/root/.openunum/plugins.json"
echo "restart: s22-openunum stop && s22-openunum start"
echo "rollback: remove '$DEST' and restart OpenUnum; no daemon or phone data is modified"
