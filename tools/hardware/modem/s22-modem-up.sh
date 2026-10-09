#!/bin/sh
# Manual, on-demand modem bring-up for the S22 (no boot hook; run it by hand).
# - Gives cbd (it drops to uid 1001 with gid 0) session-only access to its
#   nodes, the way Android ueventd/init would. These changes are lost on reboot.
# - The radio partition is made readable by uid 1001 only and is never written.
# - cbd uses the private EFS copy under /srv/s22/android-rt/efs, never the real EFS.
# Rollback: kill the cbd process (see "stop" below) or `s22-reboot recovery`.
set -eu
RT=/srv/s22/android-rt
LOG=/srv/s22/state/modem/cbd.out
cbd_pid() { ps | awk '$0 ~ /[v]endor\/bin\/cbd/ {print $1}'; }

case "${1:-up}" in
stop)
    p=$(cbd_pid); [ -n "$p" ] && kill $p && echo "stopped cbd $p" || echo "cbd not running"
    exit 0 ;;
status)
    echo "modem_state: $(cat /sys/devices/platform/cpif/modem_state)"
    echo "cbd: $(cbd_pid || true)"
    exit 0 ;;
up) ;;
*) echo "usage: $0 [up|stop|status]" >&2; exit 2 ;;
esac

if [ -n "$(cbd_pid)" ]; then
    echo "cbd already running ($(cbd_pid)); modem_state $(cat /sys/devices/platform/cpif/modem_state)"
    exit 0
fi
mkdir -p "$(dirname "$LOG")"
chown root:root "$RT"; chmod 0755 "$RT"
for n in /dev/umts_*; do chown 1001:1001 "$n"; chmod 0660 "$n"; done
chown 1001:1001 /sys/power/wake_lock /sys/power/wake_unlock
chmod 0660 /sys/power/wake_lock /sys/power/wake_unlock
radio=$(readlink -f /dev/block/by-name/radio)
chown 1001 "$radio"; chmod 0400 "$radio"
mounted() { grep -q " $1 " /proc/mounts; }
mounted "$RT/dev"  || mount --bind /dev "$RT/dev"
mounted "$RT/proc" || mount -t proc proc "$RT/proc"
mounted "$RT/sys"  || mount -t sysfs sysfs "$RT/sys"
mkdir -p "$RT/mnt/vendor/efs"
mounted "$RT/mnt/vendor/efs" || mount --bind "$RT/efs" "$RT/mnt/vendor/efs"
chroot "$RT" /system/bin/linker64 /vendor/bin/cbd -d -t ss310 -P by-name/radio -bm -mm \
    -B umts_boot0 -D umts_ramdump0 -n /mnt/vendor/efs -o s -o v </dev/null >>"$LOG" 2>&1 &
echo "cbd started (pid $!); waiting for ONLINE"
i=0
while [ $i -lt 60 ]; do
    s=$(cat /sys/devices/platform/cpif/modem_state)
    [ "$s" = ONLINE ] && { echo "modem_state ONLINE after ${i}s; run: s22-modem"; exit 0; }
    sleep 1; i=$((i + 1))
done
echo "modem_state still $s after 60s (see $LOG and dmesg)" >&2
exit 1
