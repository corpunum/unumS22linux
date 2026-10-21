#!/bin/sh
# native-root-reclaim.sh: free space on the S22 native root overlay (upper dir on
# the 583 MB CACHE partition) WITHOUT deleting anything: files are copied to
# /srv/s22/archive/native-root-<date>/, verified by sha256, then
#  - LINK items are replaced by a symlink to the copy (path keeps resolving),
#  - MOVE items (backups, caches) are removed from / after the verified copy.
# Run on the native root as root. Prints df before/after and writes MANIFEST.
set -eu
DEST=${1:-/srv/s22/archive/native-root-$(date +%Y%m%d)}
LINK="/usr/lib/libLLVM.so.22.1 /usr/lib/libgallium-26.1.6.so /usr/lib/libvulkan_radeon.so"
MOVE="/usr/local/bin/unumsearch.v0.1.2.bak /usr/local/bin/unumsearch.v0.1.5.bak /usr/local/bin/unumsearch.v0.1.6.bak /var/cache/apk /usr/lib/debug /root/.cache/mesa_shader_cache"
echo "before: $(df -k / | tail -1)"
mkdir -p "$DEST"; M=$DEST/MANIFEST; : >> "$M"
copy_verified() {  # $1 = file
  mkdir -p "$DEST$(dirname "$1")"
  cp -p "$1" "$DEST$1"
  a=$(sha256sum "$1" | cut -d' ' -f1); b=$(sha256sum "$DEST$1" | cut -d' ' -f1)
  [ "$a" = "$b" ] || { echo "sha mismatch $1" >&2; rm -f "$DEST$1"; return 1; }
  echo "$a $1" >> "$M"
}
for f in $LINK; do
  [ -f "$f" ] && [ ! -L "$f" ] || { echo "skip link $f"; continue; }
  copy_verified "$f"
  ln -s "$DEST$f" "$f.s22link.$$" && mv -f "$f.s22link.$$" "$f"
  echo "linked $f -> $DEST$f"
done
for p in $MOVE; do
  [ -e "$p" ] && [ ! -L "$p" ] || { echo "skip move $p"; continue; }
  if [ -d "$p" ]; then
    find "$p" -type f | while read -r f; do copy_verified "$f"; done
    rm -rf "$p"; mkdir -p "$p"        # keep an empty dir (apk/mesa expect it)
  else
    copy_verified "$p"; rm -f "$p"
  fi
  echo "moved $p"
done
sync
echo "after: $(df -k / | tail -1)"
