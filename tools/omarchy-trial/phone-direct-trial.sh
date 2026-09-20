#!/bin/sh
# Bounded direct-display test; restore the known-good Alpine desktop on exit.
# Only existing graphics devices and read-only udev metadata are exposed.
set -eu
trial=/mnt/omarchy-trial
hypr_config=${S22_HYPR_CONFIG:-/root/hyprland-trial.lua}
display_patch=${S22_AQUAMARINE_PATCH:-0}
trial_seconds=${S22_TRIAL_SECONDS:-20}
debugger_stop=${S22_HYPR_DEBUGGER:-0}
model_chat=${S22_MODEL_CHAT:-0}
case "$display_patch" in 0|1) ;; *) exit 2 ;; esac
case "$trial_seconds" in 20|60|session) ;; *) exit 2 ;; esac
case "$debugger_stop" in 0|1) ;; *) exit 2 ;; esac
case "$model_chat" in 0|1) ;; *) exit 2 ;; esac
if [ "$model_chat" = 1 ]; then
    mountpoint -q /mnt/model-bench || exit 1
    test -x "$trial/usr/bin/python3" || exit 1
fi
preload=''
if [ "$debugger_stop" = 1 ]; then
    test -f "$trial/opt/s22-aquamarine/libdebugger-stop.so" || exit 1
    preload=/opt/s22-aquamarine/libdebugger-stop.so
fi
library_path=/usr/lib
if [ "$display_patch" = 1 ]; then
    test -f "$trial/opt/s22-aquamarine/libaquamarine.so.14" || exit 1
    library_path=/opt/s22-aquamarine:/usr/lib
fi
[ "$(cat /proc/1/comm)" = native-guardian ] || exit 1
mountpoint -q "$trial" || exit 1
[ -x "$trial/usr/bin/Hyprland" ] || exit 1
[ -f "$trial$hypr_config" ] || exit 1
[ -S /run/user/0/wayland-0 ] || exit 1
[ -x /usr/local/bin/start-weston-native ] || exit 1
old_weston=$(pidof weston)
case "$old_weston" in ''|*' '*) echo 'Need exactly one baseline Weston' >&2; exit 1 ;; esac
old_launcher=$(awk '/^PPid:/ {print $2}' "/proc/$old_weston/status")
tr '\000' ' ' < "/proc/$old_launcher/cmdline" | grep -q '/usr/local/bin/start-weston-native' || exit 1

mkdir -p "$trial/dev/dri" "$trial/dev/input" "$trial/dev/pts" "$trial/run/udev"
mount --bind /dev/dri "$trial/dev/dri"
mount --bind /dev/input "$trial/dev/input"
mount --bind /dev/pts "$trial/dev/pts"
test -e "$trial/dev/ptmx" || ln -s pts/ptmx "$trial/dev/ptmx"
mount --bind /run/udev "$trial/run/udev"
mount -o remount,bind,ro "$trial/run/udev"
if [ "$model_chat" = 1 ]; then
    mkdir -p "$trial/mnt/model-bench"
    mount --bind /mnt/model-bench "$trial/mnt/model-bench"
    mount -o remount,bind,ro "$trial/mnt/model-bench"
fi
seat_pid=''
desktop_stopped=0
cleanup() {
    trap - EXIT HUP INT TERM
    if [ -n "$seat_pid" ]; then kill "$seat_pid" 2>/dev/null || true; wait "$seat_pid" 2>/dev/null || true; fi
    if [ "$model_chat" = 1 ]; then umount "$trial/mnt/model-bench" || true; fi
    umount "$trial/run/udev" || true
    umount "$trial/dev/pts" || true
    umount "$trial/dev/input" || true
    umount "$trial/dev/dri" || true
    if [ "$desktop_stopped" = 1 ] && ! pidof weston >/dev/null; then
        nohup /usr/local/bin/start-weston-native >/run/weston-after-omarchy.log 2>&1 </dev/null &
        restored_pid=$!
        i=0
        while [ "$i" -lt 80 ]; do
            if [ -S /run/user/0/wayland-0 ] && pidof weston >/dev/null; then
                echo "Baseline Weston restored; launcher=$restored_pid"
                return
            fi
            i=$((i + 1))
            sleep 0.1
        done
        echo 'ERROR: baseline desktop restoration needs inspection' >&2
    fi
}
trap cleanup EXIT HUP INT TERM
desktop_stopped=1
# The launcher may defer its shell trap while the foreground keyboard/terminal
# controller is running. Stop its owned Weston too so those clients disconnect.
kill "$old_launcher" "$old_weston"
i=0
while kill -0 "$old_weston" 2>/dev/null; do
    [ "$i" -lt 80 ] || { echo 'Baseline Weston did not stop' >&2; exit 1; }
    i=$((i + 1))
    sleep 0.1
done

ulimit -c 0
chroot "$trial" /usr/bin/env -i HOME=/root PATH=/usr/bin:/bin SEATD_VTBOUND=0 \
    /usr/bin/seatd -u root -g root -l debug >/tmp/omarchy-seatd.log 2>&1 &
seat_pid=$!
i=0
while [ ! -S "$trial/run/seatd.sock" ]; do
    [ "$i" -lt 40 ] || exit 1
    kill -0 "$seat_pid" || exit 1
    i=$((i + 1))
    sleep 0.1
done

set +e
set --
if [ "$trial_seconds" != session ]; then
    set -- /usr/bin/timeout --signal=TERM --kill-after=3 "$trial_seconds"
fi
chroot "$trial" /usr/bin/env -i HOME=/root PATH=/usr/local/bin:/usr/bin:/bin \
    OMARCHY_PATH=/opt/omarchy-source XDG_RUNTIME_DIR=/run/user/0 LANG=C.UTF-8 TZ=Europe/Athens \
    XDG_SESSION_TYPE=wayland LIBSEAT_BACKEND=seatd SEATD_VTBOUND=0 \
    LD_LIBRARY_PATH="$library_path" AQ_S22_DISPLAY_ONLY="$display_patch" \
    LD_PRELOAD="$preload" S22_DEBUGGER_STOP="$debugger_stop" \
    AQ_DRM_DEVICES=/dev/dri/card1 LIBGL_ALWAYS_SOFTWARE=1 GALLIUM_DRIVER=llvmpipe \
    HYPRLAND_NO_CRASHREPORTER=1 \
    /usr/bin/dbus-run-session -- \
    "$@" \
    /usr/bin/Hyprland --i-am-really-stupid --config "$hypr_config"
result=$?
set -e
echo "Direct display trial exited $result"
exit "$result"
