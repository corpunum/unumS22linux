#!/usr/bin/env bash
# Observe a native trial without acknowledging it or issuing any reset.
set -euo pipefail
if [[ $# != 1 || -e "$1" ]]; then
    echo 'Usage: observe-native-boot.sh NEW_EVIDENCE_DIRECTORY' >&2
    exit 2
fi
serial=${S22_ADB_SERIAL:?Set S22_ADB_SERIAL to the locally verified phone serial}
out=$(realpath -m "$1")
mkdir -p "$out"
deadline=$((SECONDS + 210))
last=''
captured_linux=0
captured_recovery=0
ssh_options=(-o BatchMode=yes -o ConnectTimeout=2 -o ConnectionAttempts=1
    -o StrictHostKeyChecking=accept-new -o "UserKnownHostsFile=$out/known_hosts")
while (( SECONDS < deadline )); do
    state=$(adb devices -l; lsusb | rg 'ID (04e8:|18d1:|1d6b:0104)' || true)
    if [[ "$state" != "$last" ]]; then
        { date --iso-8601=seconds; printf '%s\n' "$state"; } | tee -a "$out/usb-timeline.txt"
        last=$state
    fi
    if (( ! captured_linux )) && ip -4 addr show dev enx027322000001 2>/dev/null | rg -q '10.55.0.1/24'; then
        if ssh "${ssh_options[@]}" root@10.55.0.2 \
            'id; uname -a; cat /etc/alpine-release; cat /proc/uptime; readlink /proc/1/exe; cat /proc/1/cmdline; echo; ps -ef; cat /proc/1/root/native/handoff.log; head -12 /proc/boot_reset; test ! -e /run/native-ready && echo HOST_ACK_NOT_SENT' \
            > "$out/linux-state.txt" 2> "$out/ssh-stderr.txt"; then
            captured_linux=1
            date --iso-8601=seconds | tee "$out/linux-reachable-at.txt"
            echo 'Linux state captured; deliberately NOT sending host readiness ACK.'
        fi
    fi
    if (( SECONDS > 45 && ! captured_recovery )) && adb -s "$serial" get-state >/dev/null 2>&1; then
        if adb -s "$serial" shell 'test -f /native/handoff.log && cat /native/handoff.log; id; readlink /proc/1/exe; head -12 /proc/boot_reset; cat /proc/uptime' \
            > "$out/recovery-state.txt" 2>&1; then
            captured_recovery=1
            date --iso-8601=seconds | tee "$out/recovery-reachable-at.txt"
            echo 'Recovery ADB is reachable.'
        fi
    fi
    sleep 2
done
printf 'Observation finished: linux_seen=%s recovery_seen=%s. No ACK or reset issued.\n' \
    "$captured_linux" "$captured_recovery"
