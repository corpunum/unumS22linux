#!/bin/sh
# Run once inside the verified native Alpine session, after backing up CACHE.
# Ordinary files only: no formatting or block-device writes.
set -eu
set -o pipefail

die() { printf 'seed-cache-root: %s\n' "$*" >&2; exit 1; }
[ "$(readlink /proc/1/exe)" = /system/bin/native-guardian ] || die 'not native PID1'
[ "$(readlink -f /dev/block/by-name/cache)" = /dev/sda33 ] || die 'cache alias changed'
grep -qx 'PARTNAME=cache' /sys/dev/block/259:17/uevent || die 'cache identity changed'
[ "$(blockdev --getsize64 /dev/block/by-name/cache)" = 629145600 ] || die 'cache size changed'
blkid /dev/block/by-name/cache | grep -q 'UUID="4407af69-34ae-4723-bbd4-2242385f528e".*TYPE="ext4"' || die 'cache UUID/type changed'

if mountpoint -q /mnt/cache-ro; then umount /mnt/cache-ro; fi
[ ! -e /mnt/cache ] || [ -d /mnt/cache ] || die 'cache mount path not directory'
[ ! -L /mnt/cache ] || die 'cache mount path is symlink'
mkdir -p /mnt/cache
mountpoint -q /mnt/cache && die 'cache mount path already mounted'
mount -t ext4 -o rw /dev/block/by-name/cache /mnt/cache
[ ! -e /mnt/cache/s22-linux ] && [ ! -L /mnt/cache/s22-linux ] || die 'persistence folder already exists; refusing overwrite'
[ "$(df -Pk /mnt/cache | tail -n 1 | awk '{print $4}')" -gt 480000 ] || die 'insufficient free cache storage'
mkdir -m 0700 /mnt/cache/s22-linux
mkdir -m 0755 /mnt/cache/s22-linux/upper
mkdir -m 0700 /mnt/cache/s22-linux/work

# Explicit source paths exclude all mounted filesystems and transient payloads.
# Do not use a recursive archive over / or the firmware-reference host tree.
tar -cf - -C / bin etc home lib media opt root sbin srv usr var \
    artifact-manifest.txt rootfs-build-manifest.txt |
    tar -xf - -C /mnt/cache/s22-linux/upper
for path in dev proc sys run native mnt; do
    mkdir -p "/mnt/cache/s22-linux/upper/$path"
done
mkdir -m 1777 /mnt/cache/s22-linux/upper/tmp
printf '%s\n' 'Seeded from live native Alpine + Weston, 2026-09-19.' > /mnt/cache/s22-linux/upper/root/PERSISTENCE-TEST.txt
sync
du -sh /mnt/cache/s22-linux/upper
df -Pk /mnt/cache
printf '%s\n' 'Cache root seeded; current running root is still RAM-only.'
