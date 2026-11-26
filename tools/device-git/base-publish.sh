#!/bin/sh
# Rig side: publish new upstream to the -devices repo as device/base (never base), record the push in
# rig-pushed.txt, then deliver the released SHA to each device. U-2654d12c.
# Usage: base-publish.sh <upstream-ref> <target-remote> [device-list-file]
#   device list: one device name per line ('#' comments), or whitespace-separated DEVICE_LIST env. Names only.
# Env:
#   DEVICE_GIT_DIR      rig state dir (default $HOME/device-git); rig-pushed.txt lives here (rig-fetch reads it)
#   DEVICE_DELIVER_CMD  if set, run as: $DEVICE_DELIVER_CMD <device> <repo> <sha> (tests use a stub);
#                       default: ssh to <device> and write the SHA into DEVICE_RELEASE_DIR/base-release-<repo>.sha
#   DEVICE_RELEASE_DIR  device-side host-owned dir for the delivered SHA (default /var/lib/unum-git)
# Pushes fast-forward only: refuses (ALERT, exit 3) if the remote device/base is not an ancestor of the new sha.
# A failed delivery to one device is ALERT and exit 3 at the end; the other devices are still tried.
set -eu
ALERTS=0
die() { echo "base-publish: $*" >&2; exit 1; }
alert() { echo "ALERT: $*" >&2; ALERTS=1; }

[ $# -ge 2 ] && [ $# -le 3 ] || die "usage: base-publish.sh <upstream-ref> <target-remote> [device-list-file]"
UP=$1; REMOTE=$2; LIST=${3:-}
D=${DEVICE_GIT_DIR:-$HOME/device-git}
RELDIR=${DEVICE_RELEASE_DIR-/var/lib/unum-git}
case "$RELDIR" in *[!A-Za-z0-9._/-]*|"") die "bad DEVICE_RELEASE_DIR (allowed: A-Za-z0-9._/-, non-empty)" ;; esac
SHA=$(git rev-parse --verify -q "$UP^{commit}") || die "upstream ref '$UP' not found"
url=$(git remote get-url "$REMOTE" 2>/dev/null) || die "remote '$REMOTE' missing"
repo=$(basename "$url"); repo=${repo%.git}
case "$repo" in *[!A-Za-z0-9._-]*|"") die "bad repo name" ;; esac

# Resolve and validate the device list BEFORE anything is pushed or recorded.
if [ -n "$LIST" ]; then
  [ -f "$LIST" ] && [ -r "$LIST" ] || { alert "device list not found or unreadable: $LIST"; exit 3; }
  devs=$(sed 's/#.*//' "$LIST" | awk 'NF{print $1}') || { alert "device list unreadable"; exit 3; }
else
  devs=${DEVICE_LIST:-}
fi
[ -n "$devs" ] || { alert "no devices (give a device-list file or DEVICE_LIST)"; exit 3; }
set -f  # no glob expansion of device names
for dev in $devs; do
  case "$dev" in -*|*[!A-Za-z0-9._-]*|"") alert "bad device name in device list (nothing pushed)"; exit 3 ;; esac
done

# Fast-forward check against the remote device/base (fetched into a remote-tracking ref; nothing is rewritten).
lsr=$(git ls-remote "$REMOTE" refs/heads/device/base) || die "ls-remote of $repo failed"
cur=$(printf '%s\n' "$lsr" | awk 'NR==1{print $1}')
if [ -n "$cur" ] && [ "$cur" != "$SHA" ]; then
  git fetch -q "$REMOTE" "+refs/heads/device/base:refs/remotes/$REMOTE/device/base" || die "fetch of device/base failed"
  git merge-base --is-ancestor "$cur" "$SHA" \
    || { alert "$repo device/base $cur is not an ancestor of $SHA: refusing non-fast-forward publish"; exit 3; }
fi
if [ "$cur" != "$SHA" ]; then
  git push -q "$REMOTE" "$SHA:refs/heads/device/base" || { alert "$repo device/base push of $SHA failed"; exit 3; }
  echo "base-publish: $repo device/base -> $SHA"
else
  echo "base-publish: $repo device/base already at $SHA"
fi

# Record before delivery so rig-fetch guard 2 knows this sha is ours (append-only).
mkdir -p "$D"
echo "$repo device/base $SHA $(date -u +%Y-%m-%dT%H:%M:%SZ)" >> "$D/rig-pushed.txt"

deliver() { # <device> <repo> <sha>
  if [ -n "${DEVICE_DELIVER_CMD:-}" ]; then
    "$DEVICE_DELIVER_CMD" "$1" "$2" "$3"
  else
    # atomic: write .tmp then mv -f. The ssh login user must not be the agent user (guard 1 trusts this file).
    printf '%s\n' "$3" | ssh -o BatchMode=yes -o StrictHostKeyChecking=yes -- "$1" \
      "umask 077; cat > '$RELDIR/base-release-$2.sha.tmp' && mv -f '$RELDIR/base-release-$2.sha.tmp' '$RELDIR/base-release-$2.sha'"
  fi
}
n=0
for dev in $devs; do
  if deliver "$dev" "$repo" "$SHA" 2>/dev/null; then
    n=$((n + 1)); echo "base-publish: delivered $SHA to $dev"
  else
    alert "delivery of $SHA to $dev failed"
  fi
done
echo "base-publish: delivered to $n device(s)"
[ "$ALERTS" = 0 ] || exit 3
