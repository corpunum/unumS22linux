#!/bin/sh
# Device side, read-only (never fetch or pull). Guard 1 (docs/DEVICE_GIT_CREDENTIALS.md "Rulesets and base sync"):
# the device/base tip must equal the release SHA the rig delivered into a host-owned file. Otherwise the device
# stops: ALERT line on stderr, exit 3. U-2654d12c.
# Usage: guard1-check.sh <repo dir> <delivered-sha-file>
set -eu
[ $# -eq 2 ] || { echo "usage: guard1-check.sh <repo dir> <delivered-sha-file>" >&2; exit 2; }
repo=$1; f=$2
alert() { echo "ALERT guard1: $*" >&2; exit 3; }

[ -d "$repo" ] || alert "repo dir missing"
tip=$(git -C "$repo" rev-parse -q --verify refs/heads/device/base 2>/dev/null) || tip=
[ -n "$tip" ] || alert "device/base missing in repo"
[ -f "$f" ] || alert "delivered sha file missing"
want=$(tr -d ' \t\r\n' < "$f" 2>/dev/null) || alert "delivered sha file unreadable"
printf '%s' "$want" | grep -Eq '^[0-9a-f]{40}$' || alert "delivered sha file does not hold a full sha"
[ "$tip" = "$want" ] || alert "device/base tip $tip does not match delivered release $want"
echo "guard1 ok: device/base $tip matches delivered release"
