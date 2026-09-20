#!/usr/bin/env bash
# Read-only ABOX/ALSA readiness check. No sysfs writes, module operations, or
# audio I/O are performed.
set -u

echo '=== firmware path and required files ==='
fw_root=${ABOX_FW_ROOT:-/proc/1/root/vendor/firmware}
printf 'firmware_root=%s\n' "$fw_root"
cat /sys/module/firmware_class/parameters/path 2>/dev/null || true
for f in calliope_dram.bin calliope_sram.bin abox_tplg.bin abox_tplg.conf a2dpcom.bin; do
    if [[ -f "$fw_root/$f" ]]; then
        echo "PRESENT $fw_root/$f"
    else
        echo "MISSING $fw_root/$f"
    fi
done

echo '=== ABOX platform bindings ==='
for d in /sys/bus/platform/devices/18c50000.abox \
         /sys/bus/platform/devices/18c55000.abox-core \
         /sys/bus/platform/devices/0.abox-tplg \
         /sys/bus/platform/devices/sound; do
    [[ -e "$d" ]] || continue
    printf 'NODE %s driver=' "$d"
    readlink "$d/driver" 2>/dev/null || echo absent
    if [[ -r "$d/power/runtime_status" ]]; then
        printf 'runtime='; cat "$d/power/runtime_status"
    fi
done

echo '=== ALSA sysfs versus device nodes ==='
for d in /sys/class/sound/card* /sys/class/sound/controlC* /sys/class/sound/pcm*; do
    [[ -e "$d" ]] || continue
    printf '%s' "$d"
    [[ -r "$d/id" ]] && { printf ' id='; cat "$d/id"; }
    [[ -r "$d/dev" ]] && { printf ' dev='; cat "$d/dev"; }
done
ls -l /dev/snd 2>/dev/null || true
cat /proc/asound/cards /proc/asound/pcm /proc/asound/devices 2>/dev/null || true

echo '=== deferred-probe retry commands (NOT RUN) ==='
cat <<'EOF'
# Parent approval required after exact /vendor/firmware visibility:
# 1. echo on > /sys/devices/platform/18c50000.abox/power/control
# 2. inspect dmesg, /proc/asound/pcm, and /sys/class/sound
# 3. only if topology component/card remains absent, parent may serialize
#    unbind/bind of 0.abox-tplg and then sound; do not unbind 18c50000.abox.
EOF
