#!/bin/sh
# One-shot health check after a suspend/resume (read-only, no audio played).
# Prints one "name ok|FAIL detail" line per subsystem; exit 0 if all ok.
fail=0
r() { if [ "$2" = ok ]; then echo "$1 ok $3"; else echo "$1 FAIL $3"; fail=1; fi; }

ip4=$(ip -4 -o addr show dev wlan0 2>/dev/null | awk '{print $4}')
st=$(cat /sys/class/net/wlan0/operstate 2>/dev/null)
if [ "$st" = up ] && [ -n "$ip4" ] && ping -c1 -W3 -I wlan0 192.168.50.1 >/dev/null 2>&1; then r wifi ok "$ip4 gw-ping"; else r wifi bad "state=$st ip=$ip4"; fi

if timeout 15 tailscale ping -c 1 --timeout 10s 100.76.5.104 >/tmp/.tsp 2>&1; then r tailscale ok "$(tail -1 /tmp/.tsp)"; else r tailscale bad "$(tail -1 /tmp/.tsp)"; fi

ph=$(curl -s -m 8 http://127.0.0.1:8095/status)
ms=$(printf '%s' "$ph" | python3 -c 'import sys,json
try:
  d=json.load(sys.stdin); print(d.get("modem_state"), d.get("sim"), d.get("sms_ready"), d.get("registration",{}).get("reg_status"), d.get("registration",{}).get("act"))
except Exception as e: print("noresp")')
case "$ms" in "ONLINE READY True home"*|"ONLINE READY True roam"*) r modem ok "$ms";; *) r modem bad "$ms";; esac

if grep -q RainbowPrince /proc/asound/cards && [ -d /proc/asound/card0/pcm102p ]; then
  r audio ok "card0 present, abox $(cat /sys/devices/platform/18c50000.abox/power/runtime_status)"
else r audio bad "card or pcm node missing"; fi

if [ -e /sys/class/bluetooth/hci0 ]; then
  r bt ok "hci0 rfkill_soft=$(for x in /sys/class/rfkill/*; do [ "$(cat $x/name)" = hci0 ] && cat $x/soft; done)"
else r bt bad "no hci0"; fi

fpid=$(cat /run/s22-fastshell.pid 2>/dev/null)
if [ -n "$fpid" ] && [ "$(cat /proc/$fpid/comm 2>/dev/null)" = unum-fastshell ]; then r shell ok "pid $fpid display=$(cat /run/s22-display-state 2>/dev/null)"; else r shell bad "fastshell not running"; fi

if grep -q sec_touchscreen /proc/bus/input/devices; then
  ti=$(awk '/stm_ts/{s=0; for(i=2;i<=9;i++) s+=$i; print s; exit}' /proc/interrupts)
  r touch ok "evdev present, stm_ts irqs=$ti (physical touch not tested)"
else r touch bad "no sec_touchscreen"; fi

r usb ok "udc=$(cat /sys/class/udc/10b00000.dwc3/state)"
exit $fail
