#!/bin/bash
# Offline tests for base-publish.sh (rig) and guard1-check.sh (device). Throwaway bare repos, a stub for
# DEVICE_DELIVER_CMD instead of ssh, no network. U-2654d12c.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
export GIT_CONFIG_GLOBAL=/dev/null GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@x GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@x
export DEVICE_GIT_DIR="$T/rig" DEVICE_RELEASE_DIR="$T/rel" DELIVER_OUT="$T/delivered"
REMOTE_GIT="$T/remote/unumtest.git"
FAIL=0
pass() { echo "PASS $1"; }
fail() { echo "FAIL $1 (rc=$RC) stderr=[$ERR]"; FAIL=1; }
check() { if [ "$2" = 0 ]; then pass "$1"; else fail "$1"; fi; }

# deliver stub: <device> <repo> <sha> -> writes the sha into $DELIVER_OUT/<device>.<repo>
cat > "$T/deliver-stub.sh" <<'STUB'
#!/bin/sh
mkdir -p "$DELIVER_OUT" && printf '%s\n' "$3" > "$DELIVER_OUT/$1.$2"
STUB
chmod +x "$T/deliver-stub.sh"
export GUARD1_OWNER_UID=$(id -u)
export DEVICE_DELIVER_CMD="$T/deliver-stub.sh"
printf 'dev-a\n# a comment line\ndev-b\n' > "$T/devices.txt"

setup() { # fresh remote with device/base at c0; upstream checkout in $T/up on main at c0
  rm -rf "$T/remote" "$T/up" "$T/rig" "$T/rel" "$T/delivered" "$T/dev"
  mkdir -p "$T/remote"
  git init -q --bare "$REMOTE_GIT"
  git init -q -b main "$T/up"
  git -C "$T/up" remote add origin "$REMOTE_GIT"
  git -C "$T/up" commit -q --allow-empty -m c0
  C0=$(git -C "$T/up" rev-parse HEAD)
  git -C "$T/up" push -q origin "$C0:refs/heads/device/base"
}
remote_base() { git --git-dir="$REMOTE_GIT" rev-parse -q --verify refs/heads/device/base || true; }
pub() { # <args...>: run base-publish from the upstream checkout, capture stdout/stderr and rc
  OUT=$(cd "$T/up" && sh "$HERE/base-publish.sh" "$@" 2>"$T/err") && RC=0 || RC=$?
  ERR=$(cat "$T/err")
}
guard() { OUT=$(sh "$HERE/guard1-check.sh" "$@" 2>"$T/err") && RC=0 || RC=$?; ERR=$(cat "$T/err"); }

# 1. publish ok: device/base fast-forwards to the new upstream, base untouched, rig-pushed line, delivery
setup
git -C "$T/up" commit -q --allow-empty -m c1; C1=$(git -C "$T/up" rev-parse HEAD)
pub main origin "$T/devices.txt"
[ "$RC" = 0 ] && [ "$(remote_base)" = "$C1" ]; check "publish ok (device/base at new sha)" $?
[ -z "$(git --git-dir="$REMOTE_GIT" rev-parse -q --verify refs/heads/base || true)" ]; check "publish never touches base" $?
[ "$(cat "$T/delivered/dev-a.unumtest" 2>/dev/null)" = "$C1" ] && [ "$(cat "$T/delivered/dev-b.unumtest" 2>/dev/null)" = "$C1" ]; check "delivers sha to every listed device" $?
grep -Eq "^unumtest device/base $C1 [0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$" "$T/rig/rig-pushed.txt" \
  && [ "$(wc -l < "$T/rig/rig-pushed.txt")" = 1 ]; check "rig-pushed.txt line format <repo> <branch> <sha> <iso-time>" $?
NOLEAK_OUT="$OUT
$ERR
$(cat "$T/rig/rig-pushed.txt")"

# 1b. device list from DEVICE_LIST env instead of a file
setup; git -C "$T/up" commit -q --allow-empty -m c1
OUT=$(cd "$T/up" && DEVICE_LIST="dev-c" sh "$HERE/base-publish.sh" main origin 2>"$T/err") && RC=0 || RC=$?
[ "$RC" = 0 ] && [ -f "$T/delivered/dev-c.unumtest" ]; check "device list from DEVICE_LIST env" $?

# 2. non-ff refused: remote device/base holds a commit that is not an ancestor of the new upstream
setup
git -C "$T/up" checkout -q -b alt "$C0"; git -C "$T/up" commit -q --allow-empty -m alt
X=$(git -C "$T/up" rev-parse HEAD); git -C "$T/up" push -q origin "$X:refs/heads/device/base"
git -C "$T/up" checkout -q main; git -C "$T/up" commit -q --allow-empty -m c1
pub main origin "$T/devices.txt"
[ "$RC" = 3 ] && printf '%s' "$ERR" | grep -q '^ALERT: .*non-fast-forward' && [ "$(remote_base)" = "$X" ] \
  && [ ! -s "$T/rig/rig-pushed.txt" ] && [ ! -e "$T/delivered/dev-a.unumtest" ]; check "non-ff publish refused (ALERT, exit 3, nothing recorded or delivered)" $?
NOLEAK_OUT="$NOLEAK_OUT
$OUT
$ERR"

# 8. hardening: refused inputs, nothing pushed
setup; git -C "$T/up" commit -q --allow-empty -m c1
for bad in '*' '-oProxyCommand=x' 'a;b' 'a$b'; do
  OUT=$(cd "$T/up" && DEVICE_LIST="$bad" sh "$HERE/base-publish.sh" main origin 2>"$T/err") && RC=0 || RC=$?; ERR=$(cat "$T/err")
  [ "$RC" = 3 ] && [ "$(remote_base)" = "$C0" ] && [ ! -e "$T/rig/rig-pushed.txt" ] && [ ! -e "$T/delivered" ]; check "bad device name '$bad' refused before push" $?
done
printf 'dev-a\n-bad\n' > "$T/devbad.txt"
pub main origin "$T/devbad.txt"
[ "$RC" = 3 ] && [ "$(remote_base)" = "$C0" ] && [ ! -e "$T/delivered/dev-a.unumtest" ]; check "leading-dash name in list file refused, nothing delivered" $?
for bad in "/x';id;'" "" "/a b" '/a$b'; do
  OUT=$(cd "$T/up" && DEVICE_RELEASE_DIR="$bad" sh "$HERE/base-publish.sh" main origin "$T/devices.txt" 2>"$T/err") && RC=0 || RC=$?; ERR=$(cat "$T/err")
  [ "$RC" != 0 ] && [ "$(remote_base)" = "$C0" ]; check "bad DEVICE_RELEASE_DIR '$bad' refused" $?
done
pub main origin "$T/nonexistent.txt"
[ "$RC" != 0 ] && [ "$(remote_base)" = "$C0" ] && [ ! -e "$T/rig/rig-pushed.txt" ]; check "missing device list refused before push" $?
: > "$T/empty.txt"; pub main origin "$T/empty.txt"
[ "$RC" != 0 ] && [ "$(remote_base)" = "$C0" ] && [ ! -e "$T/rig/rig-pushed.txt" ]; check "empty device list refused before push" $?

# guard 1 fixtures: a device repo with device/base at D0
rm -rf "$T/dev"; git init -q "$T/dev"; git -C "$T/dev" commit -q --allow-empty -m d0
D0=$(git -C "$T/dev" rev-parse HEAD); git -C "$T/dev" branch -q device/base
mkdir -p "$T/rel"; chmod 755 "$T/rel"; printf '%s\n' "$D0" > "$T/rel/match.sha"
printf '%s\n' "0123456789abcdef0123456789abcdef01234567" > "$T/rel/other.sha"; chmod 644 "$T/rel/match.sha" "$T/rel/other.sha"

# 3. guard1 match
guard "$T/dev" "$T/rel/match.sha"
[ "$RC" = 0 ] && printf '%s' "$OUT" | grep -q 'guard1 ok'; check "guard1 match (exit 0)" $?
NOLEAK_OUT="$NOLEAK_OUT
$OUT
$ERR"

# 4. guard1 mismatch
guard "$T/dev" "$T/rel/other.sha"
[ "$RC" = 3 ] && printf '%s' "$ERR" | grep -Eq '^ALERT guard1: '; check "guard1 mismatch (ALERT guard1, exit 3)" $?
NOLEAK_OUT="$NOLEAK_OUT
$ERR"

# 5. guard1 missing delivered file
guard "$T/dev" "$T/rel/nope.sha"
[ "$RC" = 3 ] && printf '%s' "$ERR" | grep -Eq '^ALERT guard1: .*missing'; check "guard1 missing file (ALERT guard1, exit 3)" $?
NOLEAK_OUT="$NOLEAK_OUT
$ERR"

# 6. guard1 device/base missing
rm -rf "$T/dev2"; git init -q "$T/dev2"; git -C "$T/dev2" commit -q --allow-empty -m d0
guard "$T/dev2" "$T/rel/match.sha"
[ "$RC" = 3 ] && printf '%s' "$ERR" | grep -Eq '^ALERT guard1: device/base missing'; check "guard1 device/base missing (ALERT guard1, exit 3)" $?

# 9. guard1 trust checks
cp "$T/rel/match.sha" "$T/rel/ok.sha"; chmod 644 "$T/rel/ok.sha"
guard "$T/dev" "$T/rel/ok.sha"; [ "$RC" = 0 ]; check "guard1 plain owned 0644 file accepted" $?
ln -s match.sha "$T/rel/link.sha"
guard "$T/dev" "$T/rel/link.sha"
[ "$RC" = 3 ] && printf '%s' "$ERR" | grep -Eq '^ALERT guard1: '; check "guard1 symlinked file (exit 3)" $?
for m in 664 646 666; do
  cp "$T/rel/match.sha" "$T/rel/w$m.sha"; chmod $m "$T/rel/w$m.sha"
  guard "$T/dev" "$T/rel/w$m.sha"
  [ "$RC" = 3 ] && printf '%s' "$ERR" | grep -Eq '^ALERT guard1: '; check "guard1 file mode $m (exit 3)" $?
done
mkdir -p "$T/relw"; cp "$T/rel/match.sha" "$T/relw/m.sha"; chmod 777 "$T/relw"
guard "$T/dev" "$T/relw/m.sha"; [ "$RC" = 3 ]; check "guard1 world-writable directory (exit 3)" $?
GUARD1_OWNER_UID=4242424 guard "$T/dev" "$T/rel/ok.sha"; [ "$RC" = 3 ]; check "guard1 untrusted owner uid (exit 3)" $?
printf '%s\n' "${D0:0:20} ${D0:20}" > "$T/rel/ws.sha"; chmod 644 "$T/rel/ws.sha"
guard "$T/dev" "$T/rel/ws.sha"; [ "$RC" = 3 ]; check "guard1 inner whitespace (exit 3)" $?
printf '%s\n' "${D0:0:12}" > "$T/rel/short.sha"; chmod 644 "$T/rel/short.sha"
guard "$T/dev" "$T/rel/short.sha"; [ "$RC" = 3 ]; check "guard1 short sha (exit 3)" $?
printf '%s\r\n' "$D0" > "$T/rel/crlf.sha"; chmod 644 "$T/rel/crlf.sha"
guard "$T/dev" "$T/rel/crlf.sha"; [ "$RC" = 0 ]; check "guard1 trailing CRLF tolerated" $?

# 7. no hostname or IP strings in any tool output or the rig state file
if printf '%s\n' "$NOLEAK_OUT" | grep -Eq '([0-9]{1,3}\.){3}[0-9]{1,3}|@|ssh://|github\.com'; then fail "no hostname/IP in outputs"; else pass "no hostname/IP in outputs"; fi

[ $FAIL = 0 ] && echo "ALL PASS" || { echo "SOME FAILED"; exit 1; }
