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
[ -f "$f" ] || [ -L "$f" ] || alert "delivered sha file missing"
# Trust: the delivered file must be a regular file (not a symlink), owned by root or GUARD1_OWNER_UID, with no
# group/world write bits on it or its directory. The ssh login user that writes it MUST NOT be the agent user.
[ ! -L "$f" ] || alert "delivered sha file is a symlink"
okuid=${GUARD1_OWNER_UID:-0}
case "$okuid" in ""|*[!0-9]*) alert "bad GUARD1_OWNER_UID" ;; esac
fdir=$(dirname "$f")
for p in "$f" "$fdir"; do
  st=$(stat -c '%u %a' "$p" 2>/dev/null) || alert "cannot stat $p"
  u=${st% *}; m=${st#* }
  [ "$u" = 0 ] || [ "$u" = "$okuid" ] || alert "delivered sha path not owned by a trusted uid"
  [ $(( 0$m & 022 )) -eq 0 ] || alert "delivered sha path is group/world writable"
done
want=$(head -n1 "$f" 2>/dev/null) || alert "delivered sha file unreadable"
want=${want%"$(printf '\r')"}
printf '%s' "$want" | grep -Eq '^[0-9a-f]{40}$' || alert "delivered sha file does not hold a full sha"
[ "$tip" = "$want" ] || alert "device/base tip $tip does not match delivered release $want"
echo "guard1 ok: device/base $tip matches delivered release"
