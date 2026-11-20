#!/bin/sh
# Rig side, read-only: fetch device/* (and base) from the two private -devices repos into bare mirrors under
# ~/device-git for review. Nothing is pushed. U-551cc9ec.
set -eu
D=${DEVICE_GIT_DIR:-$HOME/device-git}
fetch() { # <bare dir> <repo>
  [ -d "$D/$1" ] || git init -q --bare "$D/$1"
  git -C "$D/$1" remote get-url gh >/dev/null 2>&1 || git -C "$D/$1" remote add gh "https://github.com/corpunum/$2.git"
  git -C "$D/$1" -c credential.helper='!gh auth git-credential' fetch -q --prune gh \
    '+refs/heads/device/*:refs/remotes/gh/device/*' '+refs/heads/base:refs/remotes/gh/base'
  echo "== $2"; git -C "$D/$1" for-each-ref --format='%(objectname:short) %(refname:strip=3) %(subject)' refs/remotes/gh
}
fetch openunum-devices.git openunum-devices
fetch unumS22linux-devices.git unumS22linux-devices
