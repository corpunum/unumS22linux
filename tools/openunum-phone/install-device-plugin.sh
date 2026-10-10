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
mkdir -p "$DEST_PARENT"
# "_" prefix: the loader skips _* dirs, so a half-copied tree is never discovered.
TMP=$DEST_PARENT/_s22-device.installing.$$
rm -rf "$TMP"
mkdir -p "$TMP"
cp "$SRC/plugin.json" "$SRC/index.mjs" "$TMP/"
cp -R "$SRC/lib" "$TMP/lib"
rm -rf "$DEST"
mv "$TMP" "$DEST"
chmod -R u=rwX,go=rX "$DEST"
echo "installed s22-device -> $DEST"
echo "requires s22d (tools/s22d/install-s22d.sh) to be running first"
echo "enable by adding \"s22-device\": {\"enabled\": true} to $C/root/.openunum/plugins.json"
echo "restart: s22-openunum stop && s22-openunum start"
echo "rollback: remove '$DEST' and restart OpenUnum; no daemon or phone data is modified"
