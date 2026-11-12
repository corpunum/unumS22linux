# P4: the phone as the rig's backup uplink (plan, 2026-11-12, for corpunum-82)

Hub U-fe741e10. Nothing below is applied yet. Phone-side steps are mine; rig-side steps need sudo and go
through corpunum-82.

## What was found (read-only, 09:10 EET)

- **USB is not an option today.** The phone already exposes a CDC-ECM gadget (configfs `g1`,
  `ecm.usb0`, phone `ecm0` up), but it is **not plugged into the rig**: the rig's `lsusb` shows no
  Samsung device. The USB path needs the owner to plug the phone into the rig. It is then the better
  path, because it also survives a dead router.
- **LAN path works without cabling.** The rig and phone are on the same home LAN (rig `eno1`, phone
  `wlan0`). The common outage is "router up, internet down": the rig can then send its traffic to the
  phone's LAN address, and the phone NATs it out over LTE (`rmnet*`).
- **Phone NAT tooling.** The kernel has legacy iptables with NAT and MASQUERADE (no nftables). The chroot
  has `iptables-legacy`, which acts on the host because they share the network namespace. Today the
  tables are empty and `ip_forward=0`, so nothing on the phone depends on them.
- **Metric 900 alone will not fail over.** When the internet dies, the rig's DHCP default route on
  `eno1` (metric 100) stays in place, so a backup default at metric 900 would never be used. The
  failover must add a route that wins (metric 50) while the outage lasts, then remove it.

## Phone side (mine): `uplink.share {on}` in s22d

`on`:
1. Guards: battery at least 30 % or on external power; today's rmnet bytes under the 2 GB/day cap.
2. Mobile data up (netpolicy `always` while sharing; the previous mode is saved).
3. `ip_forward=1`. Policy routing sends forwarded packets from the rig's LAN address to LTE:
   `ip rule add from <rig-lan> lookup 177`, and `ip route add default dev <rmnet> table 177`.
4. `iptables-legacy`, in a chain of its own (`S22SHARE`), allows only the rig:
   - `FORWARD -s <rig-lan> -o rmnet+ -j ACCEPT`
   - `FORWARD -d <rig-lan> -i rmnet+ -m state --state ESTABLISHED,RELATED -j ACCEPT`
   - `FORWARD -i wlan0 -j DROP`
   - `nat POSTROUTING -s <rig-lan> -o rmnet+ -j MASQUERADE`
5. Audit line, then a watchdog: sharing turns itself off after 6 h, at the data cap, or when the
   battery guard fails, and texts the owner.

`off` undoes everything in reverse order: chain flushed and deleted, rule and table removed,
`ip_forward=0`, the netpolicy mode restored.

Remote access: `uplink.share` and `uplink.status` join `REMOTE_CAPS` behind a switch (default on,
owner toggle in Rig access).

**Rig interface:** both rig interfaces (`eno1` and `wlp195s0`) are on the phone's LAN. The phone
forwards only the address in `/srv/s22/state/uplink-share.json`, which is `eno1`'s, so every rig route
via the phone must use `dev eno1`. Traffic leaving by Wi-Fi would carry the other source address and be
dropped.

## Proof (one destination only, minutes, a few KB of LTE data)

Phone: `uplink.share on`. Rig, with sudo (the rest of the rig is untouched):

```sh
sudo ip route add 1.1.1.1/32 via <phone-lan-ip> dev eno1
curl -s https://1.1.1.1/cdn-cgi/trace | grep -E '^(ip|colo)='   # ip = the carrier's address, not the home WAN
sudo ip route del 1.1.1.1/32 via <phone-lan-ip> dev eno1        # rollback
```

Then phone: `uplink.share off`. Success = the trace shows a different public IP than without the
route, and the phone's counters show the bytes on rmnet.

## Rig failover (after the proof), sudo through corpunum-82

A root systemd unit `unum-uplink-failover.service` (script under `~/UnumOrchestrator/scripts/`), every
10 s:
- **Main uplink check:** pings bound to `eno1` (two targets). 3 failures in a row = down;
  2 successes in a row = up again.
- **Down:**
  1. `POST /v1/device/uplink.share {"on": true}` (bearer token from `~/.config/unum/s22-device.token`).
  2. `ip route add default via <phone-lan-ip> dev eno1 metric 50` (wins over the DHCP route at 100).
- **Up again:**
  1. `ip route del default via <phone-lan-ip> dev eno1 metric 50`.
  2. `uplink.share {"on": false}`.
- **Tailscale:** it rides whichever default route is active, so the rig↔phone link survives.
- **Rollback:** `systemctl disable --now unum-uplink-failover`, then
  `ip route del default via <phone-lan-ip> dev eno1 metric 50 2>/dev/null`, then `uplink.share off`.

## Rig failover as built (2026-11-12, 12:49 EET), after an Opus review

`tools/hardware/network/s22-uplink-failover.py` (+ tests, + `unum-uplink-failover.service`). It differs from
the sketch above in these points:

- **Probe:** ICMP and TCP 443 to two targets, marked (`fwmark 0x178`, rule 1178, table 178 with the DHCP
  gateway only), so the probe never rides the phone and no other traffic is pinned. Down = both fail.
- **No gateway on `eno1`** (cable out, no lease) is "not applicable", not an outage: the phone is not asked.
- **Entering failover:** only after `ip route get <phone> oif eno1` works and the phone answers ok to
  `uplink.share on` (100 s timeout; any uncertain answer is followed by `off`). Then the metric-50 route and
  `resolvectl dns eno1 <public resolvers>`, because the router's resolver is dead in an outage.
- **In failover:** the route is re-asserted every cycle; the phone's `uplink.status` is polled every 30 s
  and the rig stands down when the phone stopped sharing (6 h, 2 GB, battery) or is unreachable 3 times.
- **Leaving:** 6 good probes in a row and at least 5 minutes in failover; repeat failovers within an hour
  back off (60 s doubling to 900 s).
- **Normal mode** deletes the phone route every cycle, so a stale route cannot outlive a crash.
- **Restart during an outage** adopts the running failover if the phone still shares; otherwise it sweeps
  and always sends `off`.
- **Token:** a root-only copy in `/etc/unum/s22-device.token` (the unit cannot read the home directory).
- **DNS on the way back:** the resolvers that were on `eno1` before are saved and put back
  (`resolvectl revert` would wipe the router's address that NetworkManager had set).
- **Keepalive:** in normal mode the phone's `uplink.status` is read every 5 minutes. It keeps the direct
  Tailscale path over the LAN warm (the relay is unreachable in an outage) and notices early when the phone
  is unreachable or no longer has this rig configured.

### Install and rollback: `install-uplink-failover.sh`

`sudo sh tools/hardware/network/install-uplink-failover.sh install <checkout> <config.json> <token-file>`
copies the daemon, the cut-test script, the config, a root-only token copy and the unit; it enables nothing.
`sudo sh /usr/local/lib/unum/install-uplink-failover.sh rollback` (a copy is installed there, so rollback
needs no checkout) stops the unit, removes any cut rule, sweeps routes and DNS, and deletes the files; the
audit log stays. Installed disabled on the rig on 2026-11-12 by corpunum-82; `once` showed mode normal.

### Proving it without unplugging anything: `s22-uplink-cut-test.sh`

Run as root on the rig, only while the daemon is active (it refuses otherwise). It loads an nftables
`netdev` egress rule on `eno1` that matches the main gateway's MAC address, and a transient systemd timer
removes it (default 240 s, 1 s accuracy). Traffic to the LAN, to the phone, and through the phone passes.

1. `probe`: drops only the daemon's marked probe. The main line keeps working; the daemon still fails over.
2. `cut`: drops everything sent to the internet through the main gateway. A true outage until failover is up.

Both stages move ALL rig traffic to the phone (another public address, LTE data): open connections break at
failover and again at failback, and the rig stays on the phone for at least `min_failover_s`. Run it with the
trading engine flat or paused. NetworkManager's connectivity check may move traffic to Wi-Fi during `cut`;
that softens the test but does not affect the daemon.

Not yet proven on the rig: the marked probe under the unit's capabilities, and whether the request to the
phone gets through over Tailscale with the WAN cut (needs a direct LAN path). Both need one live test with
the router's WAN unplugged before the unit is enabled for good.

## Asks

1. Go for the phone-side `uplink.share` build (mine, no rig changes)? Recommended: yes, now.
2. Go for the one-destination proof? It needs the two sudo `ip route` lines above, run by you, plus a
   few KB of LTE data.
3. USB: should the owner plug the phone into the rig, so the USB path (which survives a dead router)
   can be added later?
