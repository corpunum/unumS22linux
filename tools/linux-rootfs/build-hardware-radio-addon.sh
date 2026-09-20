#!/usr/bin/env bash
# Fetch signed Alpine ARM64 radio/audio tools into a separate addon.
# This never installs into the base rootfs or onto the phone.
set -Eeuo pipefail

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
project_dir=$(CDPATH= cd -- "$script_dir/../.." && pwd)
base="$project_dir/rootfs/alpine-arm64"
out="$project_dir/rootfs/hardware-radio-addon"
archive="$project_dir/rootfs/hardware-radio-addon.tar.gz"
work_root="${S22_RADIO_WORK_DIR:-/tmp/s22-hardware-radio-addon}"
qemu=$(command -v qemu-aarch64-static) || { echo 'missing qemu-aarch64-static' >&2; exit 1; }
for tool in sudo tar sha256sum mktemp; do command -v "$tool" >/dev/null || { echo "missing $tool" >&2; exit 1; }; done
[[ -d "$base" ]] || { echo "missing base rootfs: $base" >&2; exit 1; }
[[ ! -e "$out" ]] || { echo "refusing to overwrite $out" >&2; exit 1; }
[[ ! -e "$archive" ]] || { echo "refusing to overwrite $archive" >&2; exit 1; }
sudo -n true >/dev/null 2>&1 || { echo 'passwordless sudo is required' >&2; exit 1; }

# bluez supplies bluetoothd/btattach; alsa-utils supplies amixer/aplay/arecord.
# iw and wpa_supplicant are included because QCA Wi-Fi and BT share staging and
# the parent agent requested one signed, offline-transferable diagnostic addon.
packages=(bluez bluez-openrc alsa-utils iw wpa_supplicant)
stage=$(mktemp -d "$work_root.XXXXXX")
cleanup() { sudo -n rm -rf -- "$stage"; }
trap cleanup EXIT

sudo -n cp -a "$base/." "$stage/"
sudo -n install -D -m 0755 "$qemu" "$stage/usr/bin/qemu-aarch64-static"
sudo -n mkdir -p "$stage/tmp/addon-cache"
sudo -n chroot "$stage" /usr/bin/qemu-aarch64-static /sbin/apk update >/tmp/s22-hardware-radio-addon-update.log
sudo -n chroot "$stage" /usr/bin/qemu-aarch64-static /sbin/apk fetch --recursive \
  --output /tmp/addon-cache "${packages[@]}" >/tmp/s22-hardware-radio-addon-fetch.log
sudo -n rm -f "$stage/usr/bin/qemu-aarch64-static"

mkdir -p "$out"
sudo -n cp -a "$stage/tmp/addon-cache/." "$out/"
sudo -n chown -R "$(id -un):$(id -gn)" "$out"
(cd "$out" && sha256sum -- *.apk > apk-sha256.txt)
cat > "$out/README.txt" <<'EOF'
Alpine 3.24 ARM64 hardware-radio diagnostic addon

Packages were fetched recursively from the configured signed Alpine
repositories. This directory is an offline package cache only; it was not
installed into the base rootfs or phone.

The package set is: bluez, bluez-openrc, alsa-utils, iw, wpa_supplicant.
Phone-side activation remains separately serialized and approved. In
particular, do not start bluetoothd, btattach, rfkill, amixer, aplay, or
arecord until the exact vendor firmware is staged and the parent agent has
approved the test.
EOF
cat > "$out/manifest.txt" <<EOF
addon=hardware-radio-addon
alpine_series=3.24
architecture=aarch64
packages=${packages[*]}
source_date_epoch=${SOURCE_DATE_EPOCH:-1789819200}
phone_installed=no
phone_activated=no
EOF
source_date_epoch=${SOURCE_DATE_EPOCH:-1789819200}
find "$out" -type f -exec touch --date="@$source_date_epoch" {} +
tar --create --gzip --file "$archive" --directory "$out" \
  --sort=name --mtime="@$source_date_epoch" --owner=0 --group=0 --numeric-owner .
printf 'addon_bytes=%s\narchive_bytes=%s\narchive_sha256=%s\n' \
  "$(du -sb "$out" | awk '{print $1}')" \
  "$(stat -c '%s' "$archive")" \
  "$(sha256sum "$archive" | awk '{print $1}')"
