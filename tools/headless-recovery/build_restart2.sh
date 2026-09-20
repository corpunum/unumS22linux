#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
OUT="$ROOT/builds/headless_recovery_restart2_aarch64"
SRC="$ROOT/tools/headless-recovery/restart2.c"
aarch64-linux-gnu-gcc -static -O2 -Wall -Wextra -Werror -std=c11 -D_GNU_SOURCE \
  -o "$OUT.tmp" "$SRC"
mv "$OUT.tmp" "$OUT"
chmod 0755 "$OUT"
file "$OUT"
readelf -l "$OUT" | grep -E 'INTERP|Requesting' && exit 1 || true
echo "output=$OUT"
