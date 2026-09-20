#!/bin/sh
# Explicit, reversible Arch userspace test on the already-running native S22.
# This script never flashes, formats, reboots, or exposes block devices.
set -eu
trial=/mnt/omarchy-trial

native_guard() {
    [ "$(cat /proc/1/comm)" = native-guardian ] || {
        echo 'Refusing: native S22 guardian is not PID1' >&2
        exit 1
    }
}

native_guard
case "${1:-}" in
    prepare)
        ! mountpoint -q "$trial" || { echo 'Trial already mounted' >&2; exit 1; }
        available=$(awk '/^MemAvailable:/ {print $2}' /proc/meminfo)
        [ "$available" -ge 4500000 ] || { echo 'Insufficient free RAM' >&2; exit 1; }
        mkdir -p "$trial"
        [ -z "$(ls -A "$trial")" ] || { echo 'Trial directory is not empty' >&2; exit 1; }
        mount -t tmpfs -o size=3G,nosuid,mode=0755 s22-arch-trial "$trial"
        echo 'RAM trial mounted. Stream the verified userspace archive here.'
        ;;
    mount-runtime)
        mountpoint -q "$trial" || exit 1
        [ -x "$trial/usr/bin/Hyprland" ] || exit 1
        mkdir -p "$trial/proc" "$trial/sys" "$trial/dev/shm" \
            "$trial/run/user/0" "$trial/run/host-runtime" "$trial/tmp"
        chmod 1777 "$trial/dev/shm" "$trial/tmp"
        chmod 700 "$trial/run/user/0"
        for pair in null:3 zero:5 random:8 urandom:9; do
            node=${pair%:*}
            minor=${pair#*:}
            [ -e "$trial/dev/$node" ] || mknod -m 666 "$trial/dev/$node" c 1 "$minor"
        done
        [ -e "$trial/dev/fd" ] || ln -s /proc/self/fd "$trial/dev/fd"
        mount -t proc -o ro,nosuid,nodev,noexec proc "$trial/proc"
        mount --bind /sys "$trial/sys"
        mount -o remount,bind,ro "$trial/sys"
        mount --bind /run/user/0 "$trial/run/host-runtime"
        mount -o remount,bind,ro "$trial/run/host-runtime"
        echo 'Read-only kernel metadata and existing Weston socket mounted; no block/DRM devices exposed.'
        ;;
    versions)
        mountpoint -q "$trial" || exit 1
        ulimit -c 0
        chroot "$trial" /usr/bin/env -i HOME=/root PATH=/usr/bin:/bin \
            XDG_RUNTIME_DIR=/run/user/0 \
            /usr/bin/bash -c 'cat /etc/os-release; uname -m; pacman -Q hyprland aquamarine quickshell mesa; Hyprland --version; quickshell --version'
        ;;
    config|probe)
        mountpoint -q "$trial/run/host-runtime" || exit 1
        [ -f "$trial/root/hyprland-trial.lua" ] || exit 1
        ulimit -c 0
        trial_mode=$1
        set --
        if [ "$trial_mode" = config ]; then set -- --verify-config; fi
        chroot "$trial" /usr/bin/env -i HOME=/root PATH=/usr/bin:/bin \
            OMARCHY_PATH=/opt/omarchy-source XDG_RUNTIME_DIR=/run/user/0 \
            XDG_CURRENT_DESKTOP=Weston XDG_SESSION_TYPE=wayland \
            WAYLAND_DISPLAY=/run/host-runtime/wayland-0 \
            LIBGL_ALWAYS_SOFTWARE=1 GALLIUM_DRIVER=llvmpipe \
            HYPRLAND_NO_CRASHREPORTER=1 HYPRLAND_TRACE=1 \
            /usr/bin/timeout --signal=TERM --kill-after=3 25 \
            /usr/bin/Hyprland --i-am-really-stupid --config /root/hyprland-trial.lua "$@"
        ;;
    unmount)
        # Never lazy-unmount: a busy trial is reported instead of hidden.
        for suffix in run/host-runtime sys proc; do
            if mountpoint -q "$trial/$suffix"; then umount "$trial/$suffix"; fi
        done
        if mountpoint -q "$trial"; then umount "$trial"; fi
        echo 'Temporary Arch RAM filesystem removed; installed Alpine unchanged.'
        ;;
    *) echo 'usage: phone-trial.sh prepare|mount-runtime|versions|config|probe|unmount' >&2; exit 2 ;;
esac
