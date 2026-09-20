#!/usr/bin/env bash
# Build a separate Alpine ARM64 Weston package-cache addon.
# This never writes the native rootfs tree or its archives.
set -Eeuo pipefail

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd)
BASE="$PROJECT_DIR/rootfs/alpine-arm64"
OUT="$PROJECT_DIR/rootfs/alpine-desktop-addon"
ARCHIVE="$PROJECT_DIR/rootfs/alpine-desktop-addon.tar.gz"
WORK="${S22_DESKTOP_WORK_DIR:-/tmp/s22-desktop-addon-build}"
SOURCE_DATE_EPOCH="${SOURCE_DATE_EPOCH:-1789819200}"
QEMU=$(command -v qemu-aarch64-static) || { echo 'missing qemu-aarch64-static' >&2; exit 1; }
for tool in sudo tar sha256sum mktemp; do command -v "$tool" >/dev/null || { echo "missing $tool" >&2; exit 1; }; done
[[ -d "$BASE" ]] || { echo "missing base rootfs: $BASE" >&2; exit 1; }
[[ ! -e "$OUT" ]] || { echo "refusing to overwrite $OUT" >&2; exit 1; }
[[ ! -e "$ARCHIVE" ]] || { echo "refusing to overwrite $ARCHIVE" >&2; exit 1; }
sudo -n true >/dev/null 2>&1 || { echo 'passwordless sudo is required' >&2; exit 1; }

PKGS=(weston weston-backend-drm weston-shell-desktop weston-terminal seatd seatd-openrc libdrm-tests font-dejavu)
STAGE=$(mktemp -d /tmp/s22-desktop-addon.XXXXXX)
cleanup() { sudo -n rm -rf -- "$STAGE"; }
trap cleanup EXIT

sudo -n cp -a "$BASE/." "$STAGE/"
sudo -n install -D -m 0755 "$QEMU" "$STAGE/usr/bin/qemu-aarch64-static"
sudo -n mkdir -p "$STAGE/tmp/addon-cache"
sudo -n chroot "$STAGE" /usr/bin/qemu-aarch64-static /sbin/apk update >/dev/null
sudo -n chroot "$STAGE" /usr/bin/qemu-aarch64-static /sbin/apk fetch --recursive \
  --output /tmp/addon-cache "${PKGS[@]}" >/tmp/s22-desktop-addon-fetch.log
sudo -n rm -f "$STAGE/usr/bin/qemu-aarch64-static"

mkdir -p "$OUT"
sudo -n cp -a "$STAGE/tmp/addon-cache/." "$OUT/"
sudo -n chown -R "$(id -un):$(id -gn)" "$OUT"
cat > "$OUT/README.txt" <<'EOF'
Alpine 3.24 ARM64 Weston desktop/diagnostic addon

This directory contains signed Alpine .apk files and does not modify the base
rootfs. Install on the target against the existing rootfs with networking
disabled after transferring this directory:

  apk add --root / --no-network /path/to/alpine-desktop-addon/*.apk
  rc-update add seatd default       # only for the seatd-daemon path

The CPU-rendered no-VT trial should use libseat's builtin backend:

  mkdir -p /run/user/0; chmod 700 /run/user/0
  export XDG_RUNTIME_DIR=/run/user/0
  export LIBSEAT_BACKEND=builtin
  export SEATD_VTBOUND=0
  weston --backend=drm-backend.so --renderer=pixman \
    --drm-device=/dev/dri/card1 --seat=seat0 --continue-without-input

The card1 choice comes from the current S22 diagnostic evidence and must be
rechecked on the target. This is not a GPU-acceleration claim. If a VT-backed
seat becomes available later, start `seatd -g video` and use
`LIBSEAT_BACKEND=seatd` instead. No phone boot or rendering test is included.
EOF
cat > "$OUT/manifest.txt" <<EOF
addon=alpine-desktop-addon
alpine_series=3.24
architecture=aarch64
source_date_epoch=$SOURCE_DATE_EPOCH
packages=${PKGS[*]}
mesa_dri_gallium=optional-separate-package-not-included
renderer=pixman_cpu
weston_backend=drm
vt_required=no_for_libseat_builtin_path
native_phone_tested=no
EOF
find "$OUT" -type f -exec touch --date="@$SOURCE_DATE_EPOCH" {} +
sudo -n tar --create --gzip --file "$ARCHIVE" --directory "$OUT" \
  --sort=name --mtime="@$SOURCE_DATE_EPOCH" --owner=0 --group=0 --numeric-owner .
sudo -n chown "$(id -un):$(id -gn)" "$ARCHIVE"
printf 'addon_bytes=%s\narchive_bytes=%s\narchive_sha256=%s\n' \
  "$(du -sb "$OUT" | awk '{print $1}')" \
  "$(stat -c '%s' "$ARCHIVE")" \
  "$(sha256sum "$ARCHIVE" | awk '{print $1}')"
