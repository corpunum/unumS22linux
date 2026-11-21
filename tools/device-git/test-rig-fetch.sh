#!/bin/sh
# Offline test for rig-fetch.sh guard 2: throwaway bare repos, RIG_FETCH_URL_PREFIX points at them.
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
export GIT_CONFIG_GLOBAL=/dev/null GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@x GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@x
export RIG_FETCH_URL_PREFIX="$T/remote/" DEVICE_GIT_DIR="$T/rig"
R=$T/remote; mkdir -p "$R"
FAIL=0
mk() { # <repo>: bare remote with base + device/base at c0, work clone in $T/w-<repo>
  git init -q --bare "$R/$1.git"; git init -q "$T/w-$1"; cd "$T/w-$1"
  git remote add o "$R/$1.git"; git checkout -q -b base
  git commit -q --allow-empty -m c0; git push -q o base:refs/heads/base base:refs/heads/device/base
}
cm() { git commit -q --allow-empty -m "$1${2:+

$2}"; }
reset() { rm -rf "$T/rig" "$T/remote" "$T/w-"*; mkdir -p "$R"; mk openunum-devices; mk unumS22linux-devices; }
push_rec() { # <repo> <sha> : rig records a push
  mkdir -p "$T/rig"; echo "$1 device/base $2 2026-01-01T00:00:00Z" >> "$T/rig/rig-pushed.txt"; }
run() { OUT=$(sh "$HERE/rig-fetch.sh" 2>"$T/err") && RC=0 || RC=$?; ERR=$(cat "$T/err"); }
expect() { # <name> <rc> <stderr-grep or ''>
  ok=1; [ "$RC" = "$2" ] || ok=0
  if [ -n "$3" ]; then printf '%s\n' "$ERR" | grep -q "$3" || ok=0; else [ -z "$ERR" ] || ok=0; fi
  if [ $ok = 1 ]; then echo "PASS $1 (rc=$RC)"; else echo "FAIL $1 (rc=$RC) stderr=[$ERR]"; FAIL=1; fi
}
W=$T/w-openunum-devices
head0() { git -C "$W" rev-parse HEAD; }

# clean: rig pushed device/base; device branch commit with matching host
reset; push_rec openunum-devices "$(git -C $W rev-parse HEAD)"; push_rec unumS22linux-devices "$(git -C $T/w-unumS22linux-devices rev-parse HEAD)"
cd "$W"; git checkout -q -b dev1 device/base 2>/dev/null || git checkout -q -b dev1; cm work "Host: ally"; git push -q o dev1:refs/heads/device/ally
run; expect "clean" 0 ""
run; expect "clean rerun (state recorded)" 0 ""

# no trailer is INFO only
cm plain; git push -q o dev1:refs/heads/device/ally; run
if [ "$RC" = 0 ] && printf '%s' "$OUT" | grep -q 'INFO.*no host trailer'; then echo "PASS no-trailer INFO (rc=0)"; else echo "FAIL no-trailer INFO rc=$RC"; FAIL=1; fi

# (a) device/base moved to unpushed sha
cd "$W"; git checkout -q dev1; cm sneaky "Device-Host: ally"; git push -q o dev1:refs/heads/device/base; run
expect "(a) unpushed device/base" 3 'device/base .* was not pushed'
# rig records it -> alert clears
push_rec openunum-devices "$(git rev-parse HEAD)"; run; expect "(a) cleared once rig lists sha" 0 ""

# (b) host mismatch (Device-Host takes precedence over Host)
reset; push_rec openunum-devices "$(git -C $W rev-parse HEAD)"; push_rec unumS22linux-devices "$(git -C $T/w-unumS22linux-devices rev-parse HEAD)"
cd "$W"; git checkout -q -b d2; cm evil "Host: s22"; git push -q o d2:refs/heads/device/ally; run
expect "(b) Host mismatch" 3 "device/ally commit .* Host trailer 's22'"
reset; push_rec openunum-devices "$(git -C $W rev-parse HEAD)"; push_rec unumS22linux-devices "$(git -C $T/w-unumS22linux-devices rev-parse HEAD)"
cd "$W"; git checkout -q -b d3; cm evil "Host: ally
Device-Host: s22"; git push -q o d3:refs/heads/device/ally; run
expect "(b) Device-Host wins over Host" 3 "Host trailer 's22'"

# (c) non-fast-forward device/base (rig lists both shas so only (c) fires)
reset; cd "$W"; A=$(head0); push_rec openunum-devices "$A"; push_rec unumS22linux-devices "$(git -C $T/w-unumS22linux-devices rev-parse HEAD)"
cm c1; B=$(head0); push_rec openunum-devices "$B"; git push -q o base:refs/heads/device/base; run; expect "(c) ff advance ok" 0 ""
git checkout -q -b alt "$A"; cm alt; C=$(head0); push_rec openunum-devices "$C"
# bare repos deny non-ff by default only if configured; force-update the remote directly
git push -q -f o alt:refs/heads/device/base; run; expect "(c) non-ff device/base" 3 'not a fast-forward'

[ $FAIL = 0 ] && echo "ALL PASS" || { echo "SOME FAILED"; exit 1; }
