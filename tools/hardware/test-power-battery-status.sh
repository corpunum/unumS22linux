#!/bin/sh
set -eu
tmp=$(mktemp -d /tmp/s22-battery-fixture.XXXXXX)
cleanup() {
  find "$tmp" \( -type f -o -type l \) -exec sh -c 'for p do unlink "$p"; done' sh {} +
  rmdir "$tmp/battery" "$tmp/missing" "$tmp"
}
trap cleanup EXIT
mkdir "$tmp/battery"
printf Battery > "$tmp/battery/type"
printf 100 > "$tmp/battery/capacity"
printf Full > "$tmp/battery/status"
printf 296 > "$tmp/battery/temp"
printf 4382000 > "$tmp/battery/voltage_now"
printf 15 > "$tmp/battery/current_now"
out=$(S22_POWER_SUPPLY_PATH="$tmp" tools/hardware/power-battery-status.sh --shell)
printf '%s\n' "$out" | grep -qx 'percentage	100%'
printf '%s\n' "$out" | grep -qx 'state	fully-charged'
printf '%s\n' "$out" | grep -qx 'temperature	29.6C'
printf '%s\n' "$out" | grep -qx 'rate	unknown'
printf 'battery fixture: ok\n'
missing="$tmp/missing"
mkdir "$missing"
if S22_POWER_SUPPLY_PATH="$missing" tools/hardware/power-battery-status.sh --shell | grep .; then
  echo 'battery missing fixture unexpectedly produced output' >&2
  exit 1
fi
printf 'battery missing fixture: ok\n'
