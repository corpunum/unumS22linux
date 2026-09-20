#!/usr/bin/env bash
# Read-only post-activation probe; run through tools/s22-ssh.
set -euo pipefail
root=${1:-/proc/1/root}
echo '== Wi-Fi read-only acceptance probe =='
printf 'firmware_root='; cat /sys/module/firmware_class/parameters/path 2>/dev/null || echo unknown
printf 'wlan_class='
for d in /sys/class/ieee80211/*; do [[ -e "$d" ]] && printf '%s ' "${d##*/}"; done
echo
printf 'interfaces='; awk -F: 'NR > 2 {gsub(/^ +| +$/, "", $1); if ($1 != "lo" && $1 != "ecm0") print $1}' /proc/net/dev
printf 'qca_firmware_files='
for f in "$root/vendor/firmware/qca6490/"*; do [[ -f "$f" ]] && printf '%s ' "${f##*/}"; done
echo
echo 'NOTE: association/traffic requires separately authorized private credentials; this probe prints neither.'
