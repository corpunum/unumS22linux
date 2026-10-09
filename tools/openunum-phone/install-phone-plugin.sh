#!/bin/sh
# Install the s22-phone OpenUnum plugin into the Arch chroot on the S22.
# Run ON THE PHONE (Alpine host root) after the repo's tools/ tree is there:
#   sh tools/openunum-phone/install-phone-plugin.sh
# Copies plugins/s22-phone into $C/root/.openunum/plugins/s22-phone (C=/mnt/omarchy-trial,
# override with C=...). Idempotent: re-running replaces the plugin files only;
# config ($C/root/.openunum/plugins.json) and bridge state
# ($C/root/.openunum/plugin-data/s22-phone/) are left untouched.
set -eu
C=${C:-/mnt/omarchy-trial}
SRC=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)/plugins/s22-phone
DEST_PARENT=$C/root/.openunum/plugins
DEST=$DEST_PARENT/s22-phone

[ -f "$SRC/plugin.json" ] && [ -f "$SRC/index.mjs" ] || { echo "source plugin missing: $SRC" >&2; exit 1; }
[ -d "$C/root" ] || { echo "chroot not found at $C (set C=...)" >&2; exit 1; }

mkdir -p "$DEST_PARENT"
# "_" prefix: the v2.10 loader skips _* dirs, so a half-copied tree is never discovered.
TMP=$DEST_PARENT/_s22-phone.installing.$$
rm -rf "$TMP"
mkdir -p "$TMP"
# Runtime files only; tests stay in the repo.
cp "$SRC/plugin.json" "$SRC/index.mjs" "$TMP/"
cp -R "$SRC/lib" "$TMP/lib"
[ -f "$SRC/README.md" ] && cp "$SRC/README.md" "$TMP/" || true
rm -rf "$DEST"
mv "$TMP" "$DEST"
chmod -R u=rwX,go=rX "$DEST"
mkdir -p "$C/root/.openunum/plugin-data/s22-phone"

echo "installed s22-phone plugin -> $DEST"
echo "activate:  s22-openunum stop && s22-openunum start   (plugins load at OpenUnum startup)"
echo "check:     curl -s http://127.0.0.1:18880/api/health ; grep -i 's22-phone' /srv/s22/state/openunum/server.log | tail"
echo "rollback:  rm -rf '$DEST' && s22-openunum stop && s22-openunum start"
echo "           (optional: rm -rf '$C/root/.openunum/plugin-data/s22-phone' and drop the \"s22-phone\" key from $C/root/.openunum/plugins.json)"
