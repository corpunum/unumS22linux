#!/bin/sh
# Stage the opt-in sway-pixman desktop profile on the S22 (native root, as root).
# Does NOT select it and does NOT restart anything: Hyprland stays the default.
#
#   sh tools/touchui/phase2/install-sway-pixman.sh             install / update
#   sh tools/touchui/phase2/install-sway-pixman.sh --rollback  restore supervisor, drop selector
#
# Installs:
#   sway 1.12 + wlroots0.20 (+ libliftoff, vulkan-icd-loader, gnu-free-fonts; ~9 MB download,
#     ~15 MB installed) in the Arch chroot via s22-close-range-compat pacman --disable-sandbox
#   $C/root/s22-sway-pixman.conf
#   $C/opt/s22-wlroots/libwlroots-0.20.so      gamma-reset fix (tools/touchui/sway/patch-wlroots-gamma.py)
#                                              so the panel comes back after a sway output power-off
#   /srv/s22/hardware/bin/s22-display          Power key: sway path (swaymsg output power)
#   /usr/local/bin/start-persistent-desktop    (backup: .pre-desktop-profile) with /etc/s22-desktop
#   /srv/s22/hardware/bin/s22-desktop-profile  (status | sway-pixman | hyprland | --rollback)
# The touch shell + s22-touchd sway support come from tools/touchui/install-touchui.sh.
# Then:  s22-desktop-profile sway-pixman  and restart the desktop (recovery reboot).
set -eu
C=${C:-/mnt/omarchy-trial}
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
TOOLS=$(dirname "$(dirname "$HERE")")
SPD=/usr/local/bin/start-persistent-desktop
BK=$SPD.pre-desktop-profile
VULKAN=http://mirror.archlinuxarm.org/aarch64/extra/vulkan-icd-loader-1.4.363.0-1-aarch64.pkg.tar.xz

if [ "${1:-}" = --rollback ]; then
  rm -f /etc/s22-desktop
  if [ -f "$BK" ]; then cp -p "$BK" "$SPD"; echo "restored $SPD from $BK"; fi
  D=/srv/s22/hardware/bin/s22-display
  if [ -f "$D.pre-sway" ]; then cp -p "$D.pre-sway" "$D"; echo "restored $D"; fi
  echo "rollback done: Hyprland is the desktop at the next start (sway package left installed)"
  exit 0
fi

if [ ! -x $C/usr/bin/sway ]; then
  # The synced DB (2026-09-19) names vulkan-icd-loader 1.4.357, which the mirror no longer has.
  [ -e $C/usr/lib/libvulkan.so.1 ] || chroot $C /usr/local/libexec/s22-close-range-compat -- \
    timeout 300 pacman -U --noconfirm --needed --disable-sandbox "$VULKAN"
  chroot $C /usr/local/libexec/s22-close-range-compat -- \
    timeout 300 pacman -S --noconfirm --needed --disable-sandbox sway
fi
install -m 644 "$HERE/s22-sway-pixman.conf" $C/root/s22-sway-pixman.conf
python3 "$(dirname "$HERE")/sway/patch-wlroots-gamma.py" $C/usr/lib/libwlroots-0.20.so $C/opt/s22-wlroots/libwlroots-0.20.so ||
  echo "WARNING: wlroots fix not applied (unexpected wlroots build); Power key falls back to backlight-only under sway"
[ -f /srv/s22/hardware/bin/s22-display ] && [ ! -f /srv/s22/hardware/bin/s22-display.pre-sway ] &&
  cp -p /srv/s22/hardware/bin/s22-display /srv/s22/hardware/bin/s22-display.pre-sway
install -m 755 "$TOOLS/hardware/s22-display.sh" /srv/s22/hardware/bin/s22-display
[ -f "$BK" ] || cp -p "$SPD" "$BK"
install -m 755 "$TOOLS/persistence/start-persistent-desktop.py" "$SPD.new"
python3 -c "import ast,sys; ast.parse(open(sys.argv[1]).read())" "$SPD.new"
mv "$SPD.new" "$SPD"
install -m 755 "$HERE/s22-desktop-profile.sh" /srv/s22/hardware/bin/s22-desktop-profile
/srv/s22/hardware/bin/s22-desktop-profile status
echo "next: sh $TOOLS/touchui/install-touchui.sh   (touchd/shell sway support)"
echo "      sh $TOOLS/touchui/omarchy/install-omarchy-extras.sh   (keyboard bindings incl. sway)"
echo "      /srv/s22/hardware/bin/s22-desktop-profile sway-pixman   then a desktop restart"
