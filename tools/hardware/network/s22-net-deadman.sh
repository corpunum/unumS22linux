#!/bin/sh
# s22-net-deadman: restore the phone's links after N seconds unless cancelled.
# Arm it BEFORE any test that takes Wi-Fi or USB away; it is not tied to the
# SSH session (setsid + nohup) and uses wall-clock time, so it also fires after
# a suspend (on the next wake).
#   s22-net-deadman arm SECONDS [label]   start a timer (one at a time)
#   s22-net-deadman cancel                cancel the armed timer
#   s22-net-deadman status
#   s22-net-deadman restore               restore now (what the timer runs)
# Restore = USB D+ reconnect, ecm0 up + 10.55.0.2/24, wlan0 up + wpa reconnect,
# Wi-Fi lease route back,
# drop the 192.0.2.0/24 test marker, un-pause s22-netpolicy (it then picks the
# best available uplink: Wi-Fi > mobile > USB). If there is still no default
# route it adds one itself: ecm0 if the cable has carrier, else mobile data via
# s22-phoned. Mobile routes are never removed. After 45 s without a Wi-Fi
# gateway it restarts wpa_supplicant by exact PID (keepalive starts it again).
D=/run/s22-net-deadman
LOG=/srv/s22/state/netpolicy/deadman.log
UDC=/sys/class/udc/10b00000.dwc3/soft_connect
PAUSE=/srv/s22/state/netpolicy/paused
mkdir -p "$D" /srv/s22/state/netpolicy
log() { echo "$(date -u +%FT%TZ) $*" >> "$LOG"; }

restore() {
    log "restore: start ($1)"
    [ -w "$UDC" ] && echo connect > "$UDC" 2>/dev/null
    ip link set ecm0 up 2>/dev/null
    ip addr replace 10.55.0.2/24 dev ecm0 2>/dev/null
    ip route replace 10.55.0.0/24 dev ecm0 2>/dev/null
    ip link set wlan0 up 2>/dev/null
    wpa_cli -i wlan0 reconnect >/dev/null 2>&1
    # a link down/up drops the kernel default route; put back the DHCP hook's route
    L=/srv/s22/hardware/wifi-private/lease.json
    gw=$(sed -n 's/.*"gateway": *"\([0-9.]*\)".*/\1/p' "$L" 2>/dev/null)
    addr=$(sed -n 's/.*"address": *"\([0-9.]*\)".*/\1/p' "$L" 2>/dev/null)
    if [ -n "$gw" ] && [ -n "$addr" ]; then
        i=0
        while [ $i -lt 20 ] && ! ip -4 addr show dev wlan0 | grep -q " $addr/"; do sleep 2; i=$((i + 2)); done
        ip -4 addr show dev wlan0 | grep -q " $addr/" && ip route replace default via "$gw" dev wlan0 metric 600 2>/dev/null
    fi
    ip route del blackhole 192.0.2.0/24 2>/dev/null
    # Never pause the policy or force the USB route: restore = best available link.
    rm -f "$PAUSE"
    sleep 10
    if ! ip route | grep -q "^default"; then
        if [ "$(cat /sys/class/net/ecm0/carrier 2>/dev/null)" = 1 ]; then
            ip route replace default via 10.55.0.1 dev ecm0 metric 50 2>/dev/null
        else
            curl -s -m 60 -X POST -H 'Content-Type: application/json' -d '{}' http://127.0.0.1:8095/data/up >/dev/null 2>&1
        fi
    fi
    log "restore: links done; routes: $(ip route | tr '\n' ';')"
    i=0
    while [ $i -lt 45 ]; do
        ip route | grep -q "default via .* dev wlan0" && { log "restore: wlan0 default back after ${i}s"; return 0; }
        sleep 3; i=$((i + 3))
    done
    for p in /proc/[0-9]*; do
        c=$(tr '\0' ' ' < "$p/cmdline" 2>/dev/null) || continue
        case "$c" in "/sbin/wpa_supplicant -D nl80211 -i wlan0 "*) kill -TERM "${p#/proc/}" && log "restore: restarted wpa_supplicant pid ${p#/proc/}";; esac
    done
}

case "${1:-}" in
arm)
    secs=${2:?seconds}; label=${3:-test}
    if [ -f "$D/armed" ] && kill -0 "$(cut -d' ' -f1 "$D/armed")" 2>/dev/null; then
        echo "already armed: $(cat "$D/armed")"; exit 1
    fi
    rm -f "$D/cancel"
    deadline=$(( $(date +%s) + secs ))
    setsid nohup sh "$0" _wait "$deadline" "$label" >/dev/null 2>&1 </dev/null &
    sleep 0.3
    echo "armed: $(cat "$D/armed" 2>/dev/null)"
    ;;
_wait)
    echo "$$ $2 $3" > "$D/armed"
    log "armed pid $$ label=$3 deadline=$2"
    while [ "$(date +%s)" -lt "$2" ]; do
        if [ -f "$D/cancel" ]; then log "cancelled label=$3"; rm -f "$D/armed" "$D/cancel"; exit 0; fi
        sleep 2
    done
    rm -f "$D/armed"
    restore "timer label=$3"
    ;;
cancel) touch "$D/cancel"; sleep 2.5; [ -f "$D/armed" ] && echo "still armed?" || echo cancelled ;;
status) [ -f "$D/armed" ] && echo "armed: $(cat "$D/armed") now=$(date +%s)" || echo "not armed"; tail -5 "$LOG" 2>/dev/null ;;
restore) restore manual ;;
*) sed -n 2,13p "$0"; exit 2 ;;
esac
