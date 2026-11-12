#!/bin/sh
# s22-uplink-cut-test: simulate a dead WAN on the rig for a few minutes, to prove s22-uplink-failover.
# Run as root on the rig. Nothing is unplugged: an nftables netdev table drops what the rig sends to the
# internet THROUGH THE MAIN GATEWAY (matched by that gateway's MAC address on the wired interface).
# Traffic to the LAN, to the phone, and through the phone (another MAC) is untouched.
#
#   s22-uplink-cut-test probe [SECONDS]   stage 1: drop only the daemon's marked probe (0x178). The main
#                                         line keeps working for everything else; the daemon still fails
#                                         over, so real traffic moves to the phone. Safest first run.
#   s22-uplink-cut-test cut [SECONDS]     stage 2: drop everything sent to the internet via the main
#                                         gateway. A true outage for the rig until failover is up.
#   s22-uplink-cut-test end               remove the rule now.
#   s22-uplink-cut-test status
#
# Both stages really fail the rig over: ALL rig traffic then leaves through the phone (LTE data, another
# public address), open connections break at failover and again at failback, and the rig stays on the phone
# for at least the daemon's min_failover_s. Run it with the trading engine flat or paused. Refuses unless the
# failover daemon is running.
#
# The rule removes itself: a transient systemd timer deletes the table after SECONDS (default 240, max 900),
# and the table also dies with a reboot. `end` is the manual rollback.
set -eu
DEV=${S22_CUT_DEV:-eno1}
TABLE=s22cut
UNIT=s22-uplink-cut-end
DAEMON=unum-uplink-failover
CONF=/etc/unum/uplink-failover.json
NFT=${NFT:-/usr/sbin/nft}
cmd=${1:-status}
secs=${2:-240}

end() {
  "$NFT" delete table netdev "$TABLE" 2>/dev/null || true
  # Keep the timer (the safety net) unless the rule is really gone.
  if "$NFT" list table netdev "$TABLE" >/dev/null 2>&1; then echo "rule still present; timer left armed" >&2; exit 1; fi
  systemctl stop "$UNIT.timer" 2>/dev/null || true
  systemctl reset-failed "$UNIT.service" "$UNIT.timer" 2>/dev/null || true
}

main_gw() {  # the DHCP default gateway on DEV; the daemon's own route via the phone is "proto boot", never dhcp
  ip -4 route show default dev "$DEV" proto dhcp | awk '{for (i = 1; i <= NF; i++) if ($i == "via") {print $(i + 1); exit}}'
}

arm() {  # $1 = nft rule body
  case "$secs" in ''|*[!0-9]*) echo "SECONDS must be a number" >&2; exit 2;; esac
  [ "$secs" -ge 30 ] && [ "$secs" -le 900 ] || { echo "SECONDS must be 30..900" >&2; exit 2; }
  # Without the daemon there is nothing to fail over to: the rig would just be offline.
  systemctl is-active --quiet "$DAEMON" || { echo "$DAEMON is not running; refusing" >&2; exit 1; }
  [ -s "$CONF" ] || { echo "$CONF is missing; refusing" >&2; exit 1; }
  gw=$(main_gw); [ -n "$gw" ] || { echo "no main gateway on $DEV" >&2; exit 1; }
  ping -c1 -W2 -I "$DEV" "$gw" >/dev/null 2>&1 || true
  mac=$(ip neigh show "$gw" dev "$DEV" | awk '{for (i = 1; i <= NF; i++) if ($i == "lladdr") {print $(i + 1); exit}}')
  [ -n "$mac" ] || { echo "no MAC address known for the main gateway" >&2; exit 1; }
  end
  # The timer first, so the rule can never outlive it.
  systemd-run --quiet --collect --unit="$UNIT" --on-active="$secs" --timer-property=AccuracySec=1s \
    "$NFT" delete table netdev "$TABLE"
  "$NFT" -f - <<NFT_EOF
table netdev $TABLE {
  chain egress {
    type filter hook egress device "$DEV" priority 0; policy accept;
    ether daddr != $mac accept
    $1
  }
}
NFT_EOF
  echo "armed: $cmd on $DEV for ${secs}s (auto-removed by $UNIT.timer; rollback: $0 end)"
}

case "$cmd" in
  probe) arm 'meta mark 0x178 counter drop' ;;
  cut) arm 'ip daddr { 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 169.254.0.0/16, 224.0.0.0/4, 255.255.255.255 } accept
    ip6 daddr { fe80::/10, ff00::/8, fc00::/7 } accept
    meta protocol { ip, ip6 } counter drop' ;;
  end) end; echo "removed" ;;
  status)
    "$NFT" list table netdev "$TABLE" 2>/dev/null || echo "no cut rule"
    systemctl list-timers "$UNIT.timer" --no-pager 2>/dev/null | sed -n 1,3p ;;
  *) sed -n 2,19p "$0" >&2; exit 2 ;;
esac
