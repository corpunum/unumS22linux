#!/usr/bin/env bash
# Capture phone evidence to a new host directory. Never changes phone state.
set -euo pipefail
if [[ $# != 1 || -e "$1" ]]; then
    echo 'Usage: capture-phone-state.sh NEW_OUTPUT_DIRECTORY' >&2
    exit 2
fi
out=$1
mkdir -p "$out"
serial=DEVICE_SERIAL_REDACTED
date --iso-8601=seconds > "$out/host-time.txt"
adb devices -l > "$out/adb-devices.txt"
adb -s "$serial" shell 'head -n 40 /proc/boot_reset' > "$out/boot-reset.txt"
adb -s "$serial" shell 'id; uname -a; cat /proc/uptime; getprop ro.bootloader; getprop ro.lineage.version; getprop ro.adb.secure.recovery; getprop service.adb.root; cat /sys/class/power_supply/battery/capacity; cat /sys/class/power_supply/battery/temp' > "$out/identity.txt"
adb -s "$serial" shell 'cat /proc/mounts; cat /proc/modules' > "$out/mounts-modules.txt"
adb -s "$serial" shell 'cat /proc/cmdline; cat /proc/meminfo' > "$out/cmdline-memory.txt"
adb -s "$serial" shell 'ps -A -o PID,PPID,NAME' > "$out/processes.txt"
adb -s "$serial" shell 'dmesg' > "$out/dmesg.txt"
printf 'Captured %s\n' "$out"
