#!/bin/sh
# Wi-Fi saver A/B windows (issue #71). Run detached on the phone:
#   setsid nohup sh saver-ab.sh MINUTES MODE... >/dev/null 2>&1 </dev/null &
# MODE = off | disconnect | down. Each window sets s22-netpolicy's config, then waits.
# Marks go to /srv/s22/state/netpolicy/saver-ab.log; s22-trace records everything else.
# Ends with the saver on, method disconnect (the default).
CFG=/srv/s22/state/netpolicy/config.json
LOG=/srv/s22/state/netpolicy/saver-ab.log
B=/sys/class/power_supply/battery
min=$1; shift
mark() { echo "$(date +%s) $(date -u +%FT%TZ) $* raw=$(cat $B/batt_read_raw_soc) disp=$(cat /run/s22-display-state) cyc=$(sed -n 's/.*"cycles": *\([0-9]*\).*/\1/p' /run/s22-autosleep/status.json)" >> "$LOG"; }
mark "start windows=$* minutes=$min pid=$$"
for m in "$@"; do
    case "$m" in
        off) echo '{"wifi_saver": false}' > "$CFG" ;;
        disconnect|down) echo "{\"wifi_saver\": true, \"wifi_saver_method\": \"$m\"}" > "$CFG" ;;
        *) continue ;;
    esac
    mark "window $m begin"
    # wall clock: a plain "sleep N" counts only awake time (CLOCK_MONOTONIC stops in suspend)
    end=$(( $(date +%s) + min * 60 ))
    while [ "$(date +%s)" -lt "$end" ]; do sleep 20; done
    mark "window $m end"
done
echo '{"wifi_saver": true, "wifi_saver_method": "disconnect"}' > "$CFG"
mark "done"
