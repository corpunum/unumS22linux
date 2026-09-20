#!/usr/bin/env bash
# Offline/read-only readiness audit for native r0s Bluetooth and ABOX audio.
# This script never copies firmware, loads modules, changes rfkill, or touches
# the phone. It only checks a staged vendor tree and local kernel artifacts.
set -euo pipefail

project_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
firmware_root=${1:-}
if [[ -z "$firmware_root" ]]; then
    echo "usage: $0 VENDOR_FIRMWARE_ROOT" >&2
    echo "example: $0 /path/to/vendor/firmware" >&2
    exit 2
fi
[[ -d "$firmware_root" ]] || { echo "missing firmware root: $firmware_root" >&2; exit 2; }

bt_required=(
    bt_nvm_loading_2nd.xml bt_nvm_loading.xml hpbtfw20.tlv hpbtfw21.tlv
    hpnv20.bin hpnv21.bab hpnv21g.bab htbtfw20.tlv htnv20.bin
)
wifi_required=(
    qca6490/amss20.bin qca6490/bdwlan.elf qca6490/bdwlan.elf1
    qca6490/bdwlan.elf10 qca6490/bdwlan.elf2 qca6490/bdwlang.elf
    qca6490/bdwlang.elf1 qca6490/bdwlang.elf10 qca6490/bdwlang.elf2
    qca6490/m3.bin qca6490/regdb.bin wlan/qcom_cfg.ini
)
audio_required=(abox_tplg.bin abox_tplg.conf a2dpcom.bin)

missing=0
check_group() {
    local label=$1; shift
    echo "[$label]"
    local rel
    for rel in "$@"; do
        if [[ -f "$firmware_root/$rel" ]]; then
            printf 'OK      %s\n' "$rel"
        else
            printf 'MISSING %s\n' "$rel"
            missing=1
        fi
    done
}
check_group bluetooth "${bt_required[@]}"
check_group wifi "${wifi_required[@]}"
check_group audio "${audio_required[@]}"

echo "[local kernel/module evidence]"
for rel in \
    lineage/r0s_module_load_order.txt \
    lineage/android_device_samsung_r0s/configs/init/init.r0s.rc \
    lineage/android_device_samsung_r0s/proprietary-files.txt \
    lineage/android_device_samsung_s5e9925-common/proprietary-files-device.txt; do
    [[ -f "$project_dir/$rel" ]] && echo "OK      $rel" || { echo "MISSING $rel"; missing=1; }
done

if (( missing )); then
    echo "NOT_READY: do not activate radios or audio; obtain the exact matching vendor closure first."
    exit 1
fi
echo "READY_INPUTS_ONLY: assets are present in the staged tree; runtime activation remains a separately approved step."
