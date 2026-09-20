#!/bin/sh
# Host-only Arch Linux ARM + Hyprland/Omarchy configuration staging.
#
# This script never invokes the Omarchy installer and has no device/ADB path.
# It creates a disposable rootfs under /tmp by default. The generic Arch ARM
# image supplies userspace only; the S22 kernel and boot integration remain
# intentionally out of scope.
# Historical extraction prototype: --install-graphics did not finish without
# manual QEMU/pacman compatibility steps. See docs/OMARCHY_TRIAL.md and the
# recorded package logs. Prefer the verified archived trial userspace; this
# helper is NOT an unattended complete Omarchy builder.
set -eu

mirror=${S22_ARCH_MIRROR:-https://ca.us.mirror.archlinuxarm.org}
archive=${S22_ARCH_ARCHIVE:-/tmp/s22-omarchy-arch/ArchLinuxARM-aarch64-latest.tar.gz}
out=${S22_OMARCHY_ROOTFS:-/tmp/s22-omarchy-arch/rootfs}
source_tree=${S22_OMARCHY_SOURCE:-/home/corpunum/s22-linux/tools/omarchy-trial/omarchy-source}
install_graphics=0

usage() {
    cat >&2 <<'EOF'
usage: stage-omarchy-arch.sh [--install-graphics]

Extract the verified generic Arch Linux ARM AArch64 userspace and seed only
the Omarchy source/configuration tree. --install-graphics additionally uses
the staged ARM pacman under qemu-aarch64-static to install Hyprland, Aquamarine,
Foot, seatd, and the Hyprland portal. It does not run an Omarchy installer.
EOF
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --install-graphics) install_graphics=1 ;;
        -h|--help) usage; exit 0 ;;
        *) usage; exit 2 ;;
    esac
    shift
done

command -v sudo >/dev/null 2>&1 || { echo 'stage: sudo is required for root-owned archive metadata' >&2; exit 127; }
command -v qemu-aarch64-static >/dev/null 2>&1 || { echo 'stage: qemu-aarch64-static is required' >&2; exit 127; }
test -r "$archive" || { echo "stage: missing $archive" >&2; exit 1; }
test -r "$archive.md5" || { echo "stage: missing $archive.md5" >&2; exit 1; }
test -r "$archive.sig" || { echo "stage: missing $archive.sig" >&2; exit 1; }
test -d "$source_tree" || { echo "stage: missing Omarchy source $source_tree" >&2; exit 1; }

archive_dir=${archive%/*}
(cd "$archive_dir" && md5sum -c "${archive##*/}.md5")

# Import only the official ARM build key from the downloaded Arch ARM keyring.
# The fingerprint is checked before accepting the detached rootfs signature.
keyring_pkg=${S22_ARCH_KEYRING:-$archive_dir/archlinuxarm-keyring.pkg.tar.xz}
test -r "$keyring_pkg" || curl -fLsS "$mirror/aarch64/core/archlinuxarm-keyring-20240419-2-any.pkg.tar.xz" -o "$keyring_pkg"
key_home=$(mktemp -d /tmp/s22-arch-key.XXXXXX)
trap 'rm -rf "$key_home"' EXIT HUP INT TERM
tar -xOf "$keyring_pkg" usr/share/pacman/keyrings/archlinuxarm.gpg > "$key_home/archlinuxarm.gpg"
gpg --batch --homedir "$key_home" --import "$key_home/archlinuxarm.gpg" >/dev/null
fingerprint=$(gpg --batch --homedir "$key_home" --with-colons --fingerprint 68B3537F39A313B3E574D06777193F152BDBE6A6 | awk -F: '$1 == "fpr" {print $10; exit}')
test "$fingerprint" = 68B3537F39A313B3E574D06777193F152BDBE6A6 || {
    echo "stage: unexpected Arch ARM signing fingerprint: $fingerprint" >&2
    exit 1
}
gpg --batch --homedir "$key_home" --verify "$archive.sig" "$archive"

if [ -e "$out" ] && [ "$(find "$out" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null)" ]; then
    echo "stage: refusing to overwrite non-empty $out" >&2
    exit 1
fi
sudo install -d -m 0755 "$out"

# The S22 already supplies its kernel, firmware, and modules. Omitting these
# generic-machine payloads keeps this disposable userspace smaller while
# retaining Arch's normal userspace, systemd, pacman, and SSH configuration.
sudo tar --numeric-owner --xattrs --acls \
    --exclude='./boot/*' \
    --exclude='./usr/lib/firmware/*' \
    --exclude='./usr/lib/modules/*' \
    -xpf "$archive" -C "$out"

# This binary is only for host-side ARM chroot package installation and is
# removed before any artifact is copied elsewhere.
sudo install -m 0755 "$(command -v qemu-aarch64-static)" "$out/usr/bin/qemu-aarch64-static"

# Keep upstream source/configuration separate from system package files. This
# is a configuration trial, not the Omarchy installer or a fake package repo.
sudo install -d -m 0755 "$out/opt/omarchy-source" "$out/root/.config"
sudo cp -a "$source_tree/." "$out/opt/omarchy-source/"
sudo rm -rf "$out/root/.config/hypr"
sudo cp -a "$source_tree/config/hypr" "$out/root/.config/hypr"

if [ "$install_graphics" -eq 1 ]; then
    # The generated rootfs is disposable, so making resolv.conf usable for
    # pacman is safe. No host /etc file or mount namespace is changed.
    sudo rm -f "$out/etc/resolv.conf"
    sudo cp -L /etc/resolv.conf "$out/etc/resolv.conf"
    run_arm() { sudo chroot "$out" /usr/bin/qemu-aarch64-static "$@"; }
    run_arm /usr/bin/bash /usr/bin/pacman-key --init
    run_arm /usr/bin/bash /usr/bin/pacman-key --populate archlinuxarm
    # This path remains a staging prototype; QEMU can require the documented
    # pacman sandbox compatibility option. Never weaken signature checking.
    timeout 1200 sudo chroot "$out" /usr/bin/qemu-aarch64-static /usr/bin/pacman -Syu --noconfirm \
        hyprland aquamarine foot seatd quickshell xdg-desktop-portal-hyprland
fi

sudo rm -f "$out/usr/bin/qemu-aarch64-static"
sudo du -sh "$out"
printf 'staged rootfs: %s\n' "$out"
printf 'configuration: %s/root/.config/hypr\n' "$out"
printf 'graphics install: %s\n' "$([ "$install_graphics" -eq 1 ] && echo yes || echo no)"
