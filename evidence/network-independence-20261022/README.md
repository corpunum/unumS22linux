# S22 network independence — 2026-10-22

Question from the owner: does the phone work on its own SIM data, with no Wi-Fi and no USB, and stay
reachable from the rig over Tailscale?

## Answers

| Question | Answer | Numbers |
|---|---|---|
| 1. Internet over mobile data only? | **Yes** | Data call up in < 1 s (`internet.vodafone.gr`, <cgnat-ip>/32, carrier DNS <carrier-ip>/<carrier-ip>). HTTP 204 with socket *and* DNS bound to rmnet0; public IP <carrier-ip> (Vodafone) vs <home-public-ip> on Wi-Fi; ping 1.1.1.1 avg 28 ms; 2 MB download at 46 Mbit/s. LTE B3 + NR n78 (5G NSA). |
| 2. Reachable over Tailscale via mobile data? | **Yes**, direct (not even DERP) | Rig `tailscale ping` via <carrier-ip> in 35–56 ms; ICMP over Tailscale avg 42–46 ms, 0 % loss; SSH round trip 0.75–0.81 s. Gap at failover: about 6 s of pings. |
| 3. Everything keeps working with USB unplugged? | **Yes** | Simulated (USB D+ soft-disconnect; the Ally logged "USB disconnect") and then **for real** (owner unplugged at ~06:10 UTC, `udc=not attached`, PHY LDO off). Wi-Fi, Tailscale, SSH, mobile failover, shell, SIP voice listener, ABOX card, BT hci0, modem ONLINE + SMS ready, keepalive, autosleep all fine. Worst case no USB + no Wi-Fi: reachable over mobile + Tailscale. |
| Suspend on mobile only | **Reachable** | The modem wakes the AP for incoming Tailscale packets (`cp_mbox_irq` wake, `rmnet0` wakeup source). A rig `tailscale ping` to the sleeping phone answered in 0.42 s and 0.54 s. |

Mobile data used by all of this: about **6.1 MB** (5.1 MB down, 1.0 MB up, 2 MB of it the speed test).

## What changed on the phone

* **`s22-netpolicy`** (new keepalive service, `tools/hardware/network/s22-netpolicy.py`):
  * uplink Wi-Fi > mobile data > USB;
  * the chosen uplink gets a default route at metric 50; the per-link routes stay as fallbacks
    (wlan0 600 from the DHCP hook, rmnet0 9000 from phoned);
  * the boot-time `default via 10.55.0.1 dev ecm0` (metric 0) is removed. ecm0 is a local link and
    carries internet only when nothing else works;
  * mobile data comes up through s22-phoned when Wi-Fi is unusable (`mobile_mode` failover) and is
    released 120 s after Wi-Fi is back;
  * Wi-Fi is "usable" when wlan0 is up with a DHCP route and a bound probe (HTTP 204, two tries,
    DNS through the lease DNS) has not failed 3 times in a row;
  * `/etc/resolv.conf` follows the uplink (its DNS first, then 1.1.1.1);
  * repairs the Wi-Fi default route after a link down/up (the kernel drops it and udhcpc does not notice).
  * One command: `s22-netpolicy status`. Kill switch: `s22-netpolicy off` (restores the boot routes) / `on`.
* **`s22-net-deadman`** (`tools/hardware/network/s22-net-deadman.sh`): a setsid timer, armed before
  every test, that restores the links unless cancelled. Restore = USB D+ reconnect, ecm0 address,
  wlan0 up, the Wi-Fi lease route, un-pause netpolicy, and if there is still no default route
  then ecm0 (only with carrier) or a mobile data call. It never removes mobile routes.
  It was tested with a harmless marker route (fired after 20 s, then cancelled once).
* Keepalive config on the phone gained `netpolicy` (read at keepalive start, i.e. next boot; the daemon
  was started now by the installer). The repo copy `tools/hardware/keepalive-config.s22.json` matches.

## Timeline (UTC)

| Time | Step | Result | File |
|---|---|---|---|
| 05:44 | dead-man self-test (marker route, 20 s) and cancel test | fired, marker removed; cancel works | — |
| 05:44 | `s22-phoned mobile-data up` | rmnet0 <cgnat-ip>, in < 1 s | 01 |
| 05:45 | bound curl/DNS/ping/2 MB over rmnet0 | all OK | 02 |
| 05:47 | netpolicy installed | ecm0 default removed, uplink wifi | — |
| 05:52 | wlan0 down (mobile already up) | uplink mobile after 3 s; rig ping gap ~6 s; direct path via <carrier-ip> | 03–06 |
| 05:53 | wlan0 up | wpa reassociated, but no default route: link down/up drops it, udhcpc did not notice. A manual `kill -USR1` renew made udhcpc decline its own lease (ARP check) and take .14. Wi-Fi back 05:56 | 07 |
| — | fix: netpolicy re-adds the lease route; probe hardened (`-4`, two tries, 3 fails) | 8 host tests | — |
| 05:59:56 | wlan0 down, **mobile cold** | data call + uplink mobile 3 s later; Tailscale ping back after ~6 s | 08 |
| 06:00:21 | wlan0 up | route repaired + uplink wifi 8.5 s later (re-association time); mobile released after 120 s; Tailscale path back to LAN | 09 |
| 06:03:58 | USB soft-disconnect | Ally: `USB disconnect`; everything healthy over Wi-Fi | 11, 12 |
| 06:04:56 | + wlan0 down (no USB, no Wi-Fi) | uplink mobile; ping 0 % loss avg 46 ms; health all OK | 13 |
| 06:06–06:11 | idle on mobile (awake) | 158 KB in 307 s ≈ **1.9 MB/h** (Tailscale + agents' background) | 14 |
| ~06:10 | **owner physically unplugged USB** | `udc=not attached`; still reachable | 15, 16 |
| 06:12–06:16 | autosleep on mobile only | sleeps; incoming Tailscale ping wakes it via the modem in 0.4–0.5 s | 15 |
| 06:17 | wlan0 brought up again | the home SSID is out of range (phone left home); stays on mobile, Wi-Fi scanning | 16 |

## Dependencies on USB/ecm0 (audit)

| Thing | Before | Now |
|---|---|---|
| Default route | `default via 10.55.0.1 dev ecm0` metric 0 (all internet + Tailscale's direct path went via the Ally's NAT) | removed by netpolicy; ecm0 is used only when it is the sole link |
| resolv.conf | `10.55.0.1` second | follows the uplink |
| Tailscale | used whichever default route was active | follows the routes; endpoints for Wi-Fi, mobile and ecm0 are all advertised |
| Listening services | SSH 0.0.0.0; OpenUnum/8092/SIP bound to the Tailscale IP | nothing binds 10.55.0.2 |
| s22-clock | HTTP date from gstatic, then neverssl, then `http://10.55.0.1` | unchanged (the USB fallback is last) |
| BT | ecm0 carrier gate | already removed before this work (hci-hold patch) |
| s22-autosleep | `usb-host` inhibitor | `suspend_on_usb` is true. **Finding:** after a D+ soft disconnect the UDC state still reads `configured`; only a physical unplug gives `not attached`. |
| Charging | — | `usb/online` read 0 even with the Ally attached (the Ally did not charge it); after the unplug the battery shows Discharging 100 % |
| fastshell | shows "online over USB (ecm0)" text when Wi-Fi is down | display text only, not a dependency |

## Suspend on mobile: tradeoff

Wake on incoming packets works over rmnet. The modem's mailbox wakes the AP often (cycles of 0.3–45 s
seen), so it sleeps less than on Wi-Fi. Battery dropped 0.32 % in about 220 s of mostly-asleep time,
roughly 5 %/h. That is a rough figure, not a clean measurement (the Wi-Fi figure was 1.1 %/h).
Reachability is verified, so no suspend cap was added. A clean battery-only drain run on mobile is the next measurement.

## Not done / caveats

* Keepalive reads its config at start. netpolicy runs now (started by the installer), and keepalive will supervise it from the next boot.
  This is the same path that was used for autosleep, and autosleep was verified across a reboot.
* Wi-Fi only knows the home SSID; other networks need adding to `wpa_supplicant.conf`.
* Captive-portal Wi-Fi: the probe expects HTTP 204, so a portal counts as "not usable" and mobile is kept. That is intended.
