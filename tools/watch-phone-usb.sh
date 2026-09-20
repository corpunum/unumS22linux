#!/usr/bin/env bash
# Observe only; no reset or recovery action is performed.
set -euo pipefail
duration=${1:-180}
if [[ ! "$duration" =~ ^[0-9]+$ ]]; then exit 2; fi
deadline=$((SECONDS + duration))
last=''
while (( SECONDS < deadline )); do
    state=$(adb devices -l; lsusb | rg 'ID (04e8:|18d1:|1d6b:0104)' || true)
    if [[ "$state" != "$last" ]]; then
        date --iso-8601=seconds
        printf '%s\n' "$state"
        last=$state
    fi
    sleep 2
done
date --iso-8601=seconds
echo 'Observation interval complete; no device actions performed.'
