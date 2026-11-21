#!/bin/sh
# Rig side, read-only: fetch device/* (and base) from the two private -devices repos into bare mirrors under
# ~/device-git for review. Nothing is pushed. U-551cc9ec.
# Guard 2 (docs/DEVICE_GIT_CREDENTIALS.md "Rulesets and base sync"): after fetching, ALERT on stderr and exit 3 when
#  (a) gh/device/base is at a sha the rig did not push (not in $D/rig-pushed.txt: "<repo> <branch> <sha> <iso-time>",
#      append-only, written by the rig's push step; missing file = empty list),
#  (b) a device/<x> commit (not on gh/base, not in rig-pushed.txt) has a Device-Host:/Host: trailer naming another host
#      (Device-Host wins if present; commits with no trailer are reported as INFO only),
#  (c) device/base is not a fast-forward descendant of the last seen one ($D/state/<repo>.device-base; first run records).
# RIG_FETCH_URL_PREFIX is for tests only (default: the github URL).
set -eu
D=${DEVICE_GIT_DIR:-$HOME/device-git}
URLP=${RIG_FETCH_URL_PREFIX:-https://github.com/corpunum/}
ALERTS=0
alert() { echo "ALERT: $*" >&2; ALERTS=1; }

check() { # <bare dir> <repo>
  g="$D/$1"; repo=$2
  pushed=
  if [ -f "$D/rig-pushed.txt" ]; then
    pushed=$(awk -v r="$repo" '$1==r && $2=="device/base" {print $3}' "$D/rig-pushed.txt")
    allp=$(awk -v r="$repo" '$1==r {print $3}' "$D/rig-pushed.txt")
  else allp=; fi
  excl=
  for s in $allp; do git -C "$g" cat-file -e "$s^{commit}" 2>/dev/null && excl="$excl $s"; done
  base=$(git -C "$g" rev-parse -q --verify refs/remotes/gh/device/base 2>/dev/null || true)
  if [ -n "$base" ]; then
    # (a)
    ok=0; for s in $pushed; do [ "$s" = "$base" ] && ok=1; done
    [ $ok = 1 ] || alert "$repo device/base at $base was not pushed by the rig"
    # (c)
    mkdir -p "$D/state"; sf="$D/state/$repo.device-base"
    if [ -s "$sf" ]; then
      last=$(cat "$sf")
      if [ "$last" != "$base" ]; then
        if ! git -C "$g" cat-file -e "$last^{commit}" 2>/dev/null || ! git -C "$g" merge-base --is-ancestor "$last" "$base"; then
          alert "$repo device/base $base is not a fast-forward of previously seen $last"
        fi
      fi
    fi
    echo "$base" > "$sf"
  fi
  # (b)
  gbase=; git -C "$g" rev-parse -q --verify refs/remotes/gh/base >/dev/null 2>&1 && gbase=refs/remotes/gh/base
  for ref in $(git -C "$g" for-each-ref --format='%(refname)' 'refs/remotes/gh/device/'); do
    x=${ref#refs/remotes/gh/device/}
    [ "$x" = base ] && continue
    for c in $(git -C "$g" rev-list "$ref" ${gbase:+--not $gbase} ${excl:+--not $excl}); do
      t=$(git -C "$g" log -1 --format=%B "$c" | git interpret-trailers --parse)
      h=$(printf '%s\n' "$t" | awk -F: 'tolower($1)=="device-host"{sub(/^[^:]*:[ \t]*/,"");sub(/[ \t]+$/,"");print;exit}')
      [ -n "$h" ] || h=$(printf '%s\n' "$t" | awk -F: 'tolower($1)=="host"{sub(/^[^:]*:[ \t]*/,"");sub(/[ \t]+$/,"");print;exit}')
      if [ -z "$h" ]; then echo "INFO: $repo device/$x $c no host trailer"
      elif [ "$h" != "$x" ]; then alert "$repo device/$x commit $c has Host trailer '$h'"; fi
    done
  done
}

fetch() { # <bare dir> <repo>
  [ -d "$D/$1" ] || git init -q --bare "$D/$1"
  git -C "$D/$1" remote get-url gh >/dev/null 2>&1 || git -C "$D/$1" remote add gh "$URLP$2.git"
  git -C "$D/$1" -c credential.helper='!gh auth git-credential' fetch -q --prune gh \
    '+refs/heads/device/*:refs/remotes/gh/device/*' '+refs/heads/base:refs/remotes/gh/base'
  echo "== $2"; git -C "$D/$1" for-each-ref --format='%(objectname:short) %(refname:strip=3) %(subject)' refs/remotes/gh
  check "$1" "$2"
}
fetch openunum-devices.git openunum-devices
fetch unumS22linux-devices.git unumS22linux-devices
[ "$ALERTS" = 0 ] || exit 3
