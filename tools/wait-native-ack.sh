#!/usr/bin/env bash
# Recover an automatically rebooted native trial without touching boot targets.
set -euo pipefail
out=${1:?Provide an evidence directory}
mkdir -p "$out"
deadline=$((SECONDS + 600))
while (( SECONDS < deadline )); do
    if ip route get 10.55.0.2 2>/dev/null | rg -q 'dev enx027322000001 src 10.55.0.1'; then
        if ssh -o BatchMode=yes -o ConnectTimeout=2 -o ConnectionAttempts=1 \
            -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
            root@10.55.0.2 \
            'test "$(readlink /proc/1/exe)" = /system/bin/native-guardian || exit 1; touch /run/native-ready; cat /proc/uptime; readlink /proc/1/exe; cat /native/handoff.log' \
            > "$out/ack-result.txt" 2> "$out/ack-stderr.txt"; then
            date --iso-8601=seconds | tee "$out/ack-host-time.txt"
            echo 'Native guardian verified; RAM readiness ACK sent over the direct USB link.'
            exit 0
        fi
    fi
    sleep 2
done
echo 'No native SSH endpoint returned within 600 seconds.'
exit 1
