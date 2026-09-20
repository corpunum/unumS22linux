#!/usr/bin/env bash
set -Eeuo pipefail

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
OUT="$ROOT/builds"
mkdir -p "$OUT"

CC=${AARCH64_CC:-aarch64-linux-gnu-gcc}
CFLAGS=(-static -O2 -Wall -Wextra -Werror -std=c11 -D_GNU_SOURCE)

PREFIX=native_handoff
if [[ ${1:-} == --prefix ]]; then
  [[ $# -eq 2 ]] || { echo "usage: $0 [--prefix NAME]" >&2; exit 2; }
  PREFIX=$2
fi
[[ $PREFIX == *[!A-Za-z0-9_-]* || -z $PREFIX ]] && {
  echo "invalid output prefix: $PREFIX" >&2
  exit 2
}

WRAPPER="$OUT/${PREFIX}_init_wrapper_aarch64"
GUARDIAN="$OUT/${PREFIX}_guardian_aarch64"

"$CC" "${CFLAGS[@]}" -o "$WRAPPER.tmp" \
  "$ROOT/tools/native-handoff/init-wrapper.c"
mv "$WRAPPER.tmp" "$WRAPPER"

"$CC" "${CFLAGS[@]}" -o "$GUARDIAN.tmp" \
  "$ROOT/tools/native-handoff/native-guardian.c"
mv "$GUARDIAN.tmp" "$GUARDIAN"

chmod 0755 "$WRAPPER" "$GUARDIAN"
file "$WRAPPER" "$GUARDIAN"
readelf -l "$WRAPPER" "$GUARDIAN" \
  | grep -E 'INTERP|Requesting' && { echo 'unexpected dynamic interpreter' >&2; exit 1; } || true
sha256sum "$WRAPPER" "$GUARDIAN"
