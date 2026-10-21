#!/bin/sh
# audio-abox-recover.sh: recover the ABOX DSP after a Calliope firmware fault,
# the way the stock HAL does (abox_recovery: write "CALLIOPE" to
# /proc/abox/failsafe/reset). Without the HAL (failsafe "service" off) a crashed
# Calliope keeps ABOX powered with stale QoS requests and stays OFFLINE; the
# reset does a PMU silent reset, clears the requests, and the next use reloads
# the firmware. Verified 2026-10-21: OFFLINE -> ONLINE/suspended in < 1 s.
set -u
S=$(cat /proc/abox/failsafe/online)
echo "before: $S runtime=$(cat /sys/devices/platform/18c50000.abox/power/runtime_status)"
[ "$S" = OFFLINE ] || [ "${1:-}" = --force ] || { echo "ABOX online; nothing to do"; exit 0; }
printf CALLIOPE > /proc/abox/failsafe/reset
i=0; while [ $i -lt 10 ] && [ "$(cat /proc/abox/failsafe/online)" = OFFLINE ]; do sleep 0.5; i=$((i+1)); done
echo "after: $(cat /proc/abox/failsafe/online) runtime=$(cat /sys/devices/platform/18c50000.abox/power/runtime_status)"
