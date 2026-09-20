#!/bin/sh
# Sysfs fallback for Omarchy's battery status command when UPower is absent.
set -eu
[ "${1:---shell}" = --shell ] || { echo 'Usage: power-battery-status.sh --shell' >&2; exit 2; }
base=${S22_POWER_SUPPLY_PATH:-/sys/class/power_supply}
bat=''
for d in "$base"/BAT* "$base"/battery; do
    [ -r "$d/type" ] && [ "$(cat "$d/type")" = Battery ] && { bat=$d; break; }
done
[ -n "$bat" ] || exit 0
readv() { [ -r "$bat/$1" ] && cat "$bat/$1" || true; }
percent=$(readv capacity); status=$(readv status); temp=$(readv temp)
[ "$status" = Full ] && status=fully-charged
[ "$status" = Charging ] && status=charging
[ "$status" = Discharging ] && status=discharging
[ -n "$percent" ] && printf 'percentage\t%s%%\n' "$percent"
printf 'state\t%s\n' "$(printf '%s' "$status" | tr '[:upper:]' '[:lower:]')"
# Samsung's current/voltage/power nodes have varied across kernel revisions;
# do not publish ambiguous raw fields or infer a watt value in this fallback.
printf 'rate\tunknown\n'
if [ -n "$temp" ]; then
    awk -v t="$temp" 'BEGIN { printf "temperature\t%.1fC\n", t / 10 }'
fi
