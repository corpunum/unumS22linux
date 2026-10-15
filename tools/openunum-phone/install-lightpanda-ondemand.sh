#!/bin/sh
# Install the loopback CDP proxy into the Arch chroot. Run on the phone's
# Alpine host; this only copies files and never starts Lightpanda/OpenUnum.
set -eu
C=${C:-/mnt/omarchy-trial}
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
SRC=$HERE/lightpanda-ondemand.mjs
DEST=$C/opt/openunum/tools/lightpanda-ondemand.mjs
[ -f "$SRC" ] || { echo "launcher missing: $SRC" >&2; exit 1; }
[ -x "$C/opt/node/bin/node" ] || { echo "Node.js missing in chroot: $C/opt/node/bin/node" >&2; exit 1; }
[ -x "$C/opt/lightpanda/lightpanda" ] || { echo "Lightpanda missing in chroot: $C/opt/lightpanda/lightpanda" >&2; exit 1; }
mkdir -p "$(dirname "$DEST")"
install -m 755 "$SRC" "$DEST"
echo "installed $DEST"
echo "Run inside chroot: OPENUNUM_HOME=/root/.openunum node $DEST serve"
echo "Configure OpenUnum's CDP endpoint as http://127.0.0.1:9222; backend Lightpanda is started on first CDP request and stopped after 600 idle seconds."
