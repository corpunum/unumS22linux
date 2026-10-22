#!/usr/bin/env python3
"""s22-netpolicy: pick the phone's internet uplink (Wi-Fi > mobile data > USB).

    s22-netpolicy run       the daemon (keepalive service "netpolicy")
    s22-netpolicy status    one-screen state (links, active uplink, routes, DNS)
    s22-netpolicy off|on    pause/resume (persistent flag); "off" also puts the
                            boot-time routes back (ecm0 default, metric 0)
    s22-netpolicy probe IF  one connectivity probe bound to IF (wlan0/rmnet0/ecm0)
    s22-netpolicy saver off|on|status   Wi-Fi saver kill switch (persistent flag)

Policy (knobs in CONFIG, overridable by /srv/s22/state/netpolicy/config.json):
* Wi-Fi is usable when wlan0 is operstate "up", has a DHCP default route and its
  bound probe (HTTP 204, DNS through the lease DNS on wlan0) has not failed
  probe_fail_n times in a row.
* Mobile data (s22-phoned /data/*, rmnet0) is brought up when Wi-Fi is not
  usable (mobile_mode "failover", the default) or kept up all the time
  ("always"; "off" never). After Wi-Fi comes back it stays up mobile_hold_s
  more, then it is released.
* USB ECM (ecm0, the Ally/rig cable) is a local link: its boot-time default
  route is removed. It carries internet only when nothing else is usable.
* The chosen uplink gets a policy default route at metric 50; the per-link
  routes stay as fallbacks (wlan0 600 from the DHCP hook, rmnet0 9000 from
  phoned). /etc/resolv.conf follows the uplink (its DNS first, then fallback).
Tailscale follows the kernel routes by itself (netmon); nothing to do there.

Wi-Fi saver (2026-10-22, issue #71). Out of Wi-Fi range wpa_supplicant keeps scanning for the
home SSID, and those scans wake the suspended phone (wlan_pci_wake_irq) every few seconds.
While the screen is off AND mobile data is the uplink AND Wi-Fi is not connected, for
wifi_saver_grace_s, Wi-Fi is "parked" (wifi_saver_method "disconnect": wpa_cli disconnect, so
wpa_supplicant stops scanning; "down": ip link set wlan0 down). Every wifi_saver_discover_every_s
one bounded discovery runs (disconnect: one manual scan, checked for a configured SSID;
down: link up for wifi_saver_discover_s); a known network found -> unpark. Screen on, uplink
not mobile, the kill switch, "off", a deadman restore (hold file) or daemon exit -> unpark
at once (wpa_cli reconnect + link up). Kill switch: s22-netpolicy saver off (or
"wifi_saver": false in config.json).
Logs transitions as JSON lines to /srv/s22/state/netpolicy/netpolicy.jsonl.
"""
import json, os, subprocess, sys, time, urllib.request

STATE_DIR = "/srv/s22/state/netpolicy"
FLAG = STATE_DIR + "/paused"
LOG = STATE_DIR + "/netpolicy.jsonl"
CFG_FILE = STATE_DIR + "/config.json"
STATUS = "/run/s22-netpolicy/status.json"
LEASE = "/srv/s22/hardware/wifi-private/lease.json"
RESOLV = "/etc/resolv.conf"
NET = "/sys/class/net/"
PHONED = "http://127.0.0.1:8095"
USB_GW = "10.55.0.1"
POLICY_METRIC = 50
DISPLAY = "/run/s22-display-state"
WPA_CTRL = "/run/wpa_supplicant-s22"
SAVER_OFF = STATE_DIR + "/wifi-saver-off"
SAVER_HOLD = "/run/s22-netpolicy/saver-hold"   # s22-net-deadman restore: no parking for SAVER_HOLD_S
SAVER_HOLD_S = 3600
SAVER_STATE = "/run/s22-netpolicy/saver.json"

CONFIG = {
    "poll_s": 5,
    "mobile_mode": "failover",       # failover | always | off
    "mobile_hold_s": 120,            # keep mobile up this long after Wi-Fi is back
    "mobile_retry_s": 30,            # min gap between data-call attempts
    "probe_url": "http://connectivitycheck.gstatic.com/generate_204",
    "probe_every_s": 30,             # active uplink (and Wi-Fi while it is up)
    "probe_fail_n": 3,               # consecutive failed probes (2 tries each) before a link is unusable
    "probe_timeout_s": 5,
    "redial_after_fails": 3,         # mobile active but failing -> data down/up
    "redial_min_s": 300,
    "dns_fallback": ["1.1.1.1"],
    "manage_dns": True,
    "wifi_saver": True,
    "wifi_saver_method": "disconnect",   # disconnect | down
    "wifi_saver_grace_s": 60,            # screen off + mobile uplink this long before parking
    "wifi_saver_discover_every_s": 900,  # one bounded look for a known network while parked
    "wifi_saver_discover_s": 15,         # how long a discovery waits for scan results / association
}


def rd(p, d=""):
    try:
        with open(p) as f:
            return f.read().strip()
    except OSError:
        return d


def log(**kw):
    kw["t"] = round(time.time(), 3)
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(LOG, "a") as f:
            f.write(json.dumps(kw) + "\n")
    except OSError:
        pass


def load_cfg():
    c = dict(CONFIG)
    try:
        c.update(json.load(open(CFG_FILE)))
    except (OSError, ValueError):
        pass
    return c


def ip(*args):
    r = subprocess.run(("ip",) + args, capture_output=True, text=True, timeout=10)
    return r.returncode == 0, (r.stdout + r.stderr).strip()


def defaults():
    """[(dev, via, metric, line)] for every IPv4 default route in main."""
    ok, out = ip("route", "show", "default")
    res = []
    for line in out.splitlines() if ok else []:
        f = line.split()
        dev = f[f.index("dev") + 1] if "dev" in f else None
        via = f[f.index("via") + 1] if "via" in f else None
        metric = int(f[f.index("metric") + 1]) if "metric" in f else 0
        res.append((dev, via, metric, line.strip()))
    return res


def phoned(path, body=None, timeout=5):
    req = urllib.request.Request(PHONED + path, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"},
                                 method="GET" if body is None else "POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        try:
            return json.load(e)
        except Exception:  # noqa: BLE001
            return {"ok": False, "error": "http %d" % e.code}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)[:120]}


def lease():
    """The DHCP hook's lease for THIS boot, or None."""
    try:
        d = json.load(open(LEASE))
    except (OSError, ValueError):
        return None
    return d if d.get("boot_id") == rd("/proc/sys/kernel/random/boot_id") else None


def lease_dns():
    return list((lease() or {}).get("dns") or [])


def wifi_route_repair(lk):
    """wlan0 back up with its lease address but no default route (a link down/up drops
    the kernel route and udhcpc does not notice until T1): put back exactly the route
    the DHCP hook installs. Returns the ip args or None."""
    if lk["wifi"]["oper"] != "up" or lk["wifi"]["gw"]:
        return None
    l = lease()
    if not l or not l.get("gateway") or not l.get("address"):
        return None
    ok, out = ip("-4", "addr", "show", "dev", "wlan0")
    if " %s/" % l["address"] not in out:
        return None
    return ("route", "replace", "default", "via", l["gateway"], "dev", "wlan0", "metric", "600")


def probe(dev, dns, cfg):
    """True if an HTTP 204 comes back with the TCP socket AND the DNS query bound to dev.
    Two attempts (link DNS, then link DNS + fallback): a single lost SYN/DNS packet on
    a busy Wi-Fi must not count as a dead link."""
    servers = [s for s in (dns or []) if s] or list(cfg["dns_fallback"])
    for attempt in (servers, servers + [s for s in cfg["dns_fallback"] if s not in servers]):
        args = ["curl", "-4", "-s", "-o", "/dev/null", "-w", "%{http_code}", "-m", str(cfg["probe_timeout_s"]),
                "--interface", dev, "--dns-interface", dev, "--dns-servers", ",".join(attempt),
                cfg["probe_url"]]
        try:
            r = subprocess.run(args, capture_output=True, text=True, timeout=cfg["probe_timeout_s"] + 4)
            if r.stdout.strip() == "204":
                return True
        except Exception:  # noqa: BLE001
            pass
    return False


def links(mobile):
    """Observed link facts (no probes)."""
    dflt = defaults()
    wl_gw = next((via for dev, via, m, _ in dflt if dev == "wlan0" and m != POLICY_METRIC and via), None)
    return {
        "wifi": {"oper": rd(NET + "wlan0/operstate"), "gw": wl_gw, "dns": lease_dns()},
        "mobile": {"state": mobile.get("state", "unknown"), "ipv4": mobile.get("ipv4"),
                   "dns": mobile.get("dns") or [], "oper": rd(NET + "rmnet0/operstate"),
                   "modem": mobile.get("modem_state"), "rx": mobile.get("rx_bytes"),
                   "tx": mobile.get("tx_bytes"), "act": mobile.get("act"), "nr": mobile.get("nr_nsa"),
                   "error": mobile.get("error")},
        "usb": {"oper": rd(NET + "ecm0/operstate"), "carrier": rd(NET + "ecm0/carrier") == "1",
                "udc": rd("/sys/class/udc/10b00000.dwc3/state")},
        "defaults": dflt,
    }


def wifi_present(lk):
    return lk["wifi"]["oper"] == "up" and bool(lk["wifi"]["gw"])


def mobile_present(lk):
    return lk["mobile"]["state"] == "up" and bool(lk["mobile"]["ipv4"])


def usb_present(lk):
    return lk["usb"]["oper"] == "up" and lk["usb"]["carrier"]


def choose(lk, fails, cfg):
    """Pure decision: (uplink, want_mobile). fails = {"wifi": n, "mobile": n}."""
    n = cfg["probe_fail_n"]
    wifi_ok = wifi_present(lk) and fails.get("wifi", 0) < n
    mob_ok = mobile_present(lk) and fails.get("mobile", 0) < n
    if wifi_ok:
        up = "wifi"
    elif mob_ok:
        up = "mobile"
    elif usb_present(lk):
        up = "usb"
    elif wifi_present(lk):
        up = "wifi"           # failing probes but nothing better: keep trying it
    elif mobile_present(lk):
        up = "mobile"
    else:
        up = None
    mode = cfg["mobile_mode"]
    want_mobile = mode == "always" or (mode == "failover" and not wifi_ok)
    return up, want_mobile


def desired_route(up, lk):
    """The policy default route (argv after 'ip route replace') for the uplink."""
    if up == "wifi" and lk["wifi"]["gw"]:
        return ("default", "via", lk["wifi"]["gw"], "dev", "wlan0", "metric", str(POLICY_METRIC))
    if up == "mobile":
        return ("default", "dev", "rmnet0", "metric", str(POLICY_METRIC))
    if up == "usb":
        return ("default", "via", USB_GW, "dev", "ecm0", "metric", str(POLICY_METRIC))
    return None


def route_plan(up, lk):
    """[ip args] that make the routing table match the policy."""
    want = desired_route(up, lk)
    want_dev = want[want.index("dev") + 1] if want else None
    plan = []
    for dev, via, metric, line in lk["defaults"]:
        drop = (metric == POLICY_METRIC and dev != want_dev) or (dev == "ecm0" and metric != POLICY_METRIC)
        if drop:
            # built from the parsed fields: the kernel's line can carry flags ("dead linkdown" on a
            # cable-less ecm0 after boot) that "ip route del" rejects, which repeated every poll
            args = ("route", "del", "default") + (("via", via) if via else ()) + ("dev", dev)
            plan.append(args + (("metric", str(metric)) if metric else ()))
    have = any(dev == want_dev and m == POLICY_METRIC and
               (want[1] != "via" or via == want[2]) for dev, via, m, _ in lk["defaults"]) if want else True
    if want and not have:
        plan.append(("route", "replace") + want)
    return plan


def resolv_for(up, lk, cfg):
    if up == "wifi":
        s = list(lk["wifi"]["dns"]) or [lk["wifi"]["gw"]]
    elif up == "mobile":
        s = list(lk["mobile"]["dns"])
    elif up == "usb":
        s = [USB_GW]
    else:
        return None
    out = []
    for x in s + list(cfg["dns_fallback"]):
        if x and x not in out and len(out) < 3:
            out.append(x)
    return "".join("nameserver %s\n" % x for x in out) + \
        "options timeout:2 attempts:2\n# s22-netpolicy uplink=%s\n" % up


def write_status(st):
    try:
        os.makedirs(os.path.dirname(STATUS), exist_ok=True)
        tmp = STATUS + ".tmp"
        with open(tmp, "w") as f:
            json.dump(st, f, indent=1)
        os.replace(tmp, STATUS)
    except OSError:
        pass


def legacy_routes():
    """What the phone had before this policy: ecm0 default (metric 0), no policy routes."""
    for dev, via, metric, line in defaults():
        if metric == POLICY_METRIC:
            ip("route", "del", *line.split())
    if rd(NET + "ecm0/operstate") == "up":
        ip("route", "replace", "default", "via", USB_GW, "dev", "ecm0")


def wpa(*args, timeout=8):
    """wpa_cli on the phone's control socket; (ok, output)."""
    try:
        r = subprocess.run(("wpa_cli", "-p", WPA_CTRL, "-i", "wlan0") + args, capture_output=True, text=True,
                           timeout=timeout)
        out = (r.stdout + r.stderr).strip()
        return r.returncode == 0 and "FAIL" not in out, out
    except Exception as e:  # noqa: BLE001
        return False, str(e)[:120]


def known_ssids(list_networks_out):
    """SSIDs configured in wpa_supplicant (wpa_cli list_networks: id / ssid / bssid / flags)."""
    out = set()
    for line in list_networks_out.splitlines()[1:]:
        f = line.split("\t")
        if len(f) >= 2 and f[0].strip().isdigit() and f[1]:
            out.add(f[1])
    return out


def seen_ssids(scan_results_out):
    """SSIDs in wpa_cli scan_results (bssid / freq / signal / flags / ssid)."""
    out = set()
    for line in scan_results_out.splitlines()[1:]:
        f = line.split("\t")
        if len(f) >= 5 and f[4]:
            out.add(f[4])
    return out


def saver_hold_active(now=None):
    try:
        return (now or time.time()) - os.path.getmtime(SAVER_HOLD) < SAVER_HOLD_S
    except OSError:
        return False


def saver_eligible(cfg, display, up, wifi_up, paused, killed, hold):
    """Pure: should Wi-Fi be parked now (before the grace period)? (bool, reason-if-not)."""
    if not cfg.get("wifi_saver", True) or killed:
        return False, "disabled"
    if paused:
        return False, "paused"
    if hold:
        return False, "deadman_hold"
    if display != "off":
        return False, "screen_on"
    if up != "mobile":
        return False, "uplink_%s" % up
    if wifi_up:
        return False, "wifi_up"
    return True, ""


class WifiSaver:
    """Park/unpark Wi-Fi scanning (see module doc). Commands go through self.run_wpa / self.run_ip
    so host tests can record them."""

    def __init__(self, run_wpa=None, run_ip=None, state_file=SAVER_STATE):
        self.run_wpa = run_wpa or wpa
        self.run_ip = run_ip or ip
        self.state_file = state_file
        self.parked = False
        self.method = None
        self.parked_at = None
        self.eligible_since = None
        self.next_discover = 0.0
        self.discover_until = None
        self.discoveries = 0
        self.reason = ""

    def snapshot(self):
        return {"parked": self.parked, "method": self.method, "parked_at": self.parked_at,
                "next_discover": round(self.next_discover, 1) if self.parked else None,
                "discovering": self.discover_until is not None, "discoveries": self.discoveries,
                "reason": self.reason}

    def save(self):
        try:
            os.makedirs(os.path.dirname(self.state_file), exist_ok=True)
            tmp = self.state_file + ".tmp"
            with open(tmp, "w") as f:
                json.dump(self.snapshot(), f)
            os.replace(tmp, self.state_file)
        except OSError:
            pass

    def park(self, method, now, cfg):
        if method == "down":
            ok, out = self.run_ip("link", "set", "wlan0", "down")
        else:
            method = "disconnect"
            ok, out = self.run_wpa("disconnect")
        self.parked, self.method, self.parked_at = True, method, now
        self.next_discover = now + cfg["wifi_saver_discover_every_s"]
        self.discover_until = None
        log(event="wifi_saver_park", method=method, ok=ok, out=out[:120])
        self.save()

    def unpark(self, reason, now=None):
        """Always restore both ways (link up + reconnect), whatever parked it."""
        ok1, out1 = self.run_ip("link", "set", "wlan0", "up")
        ok2, out2 = self.run_wpa("reconnect")
        was = self.method
        self.parked, self.method, self.parked_at = False, None, None
        self.discover_until = None
        self.eligible_since = None
        self.reason = reason
        log(event="wifi_saver_unpark", reason=reason, was=was, ok=ok1 and ok2, out=(out1 + " " + out2)[:120])
        self.save()

    def start_discovery(self, now, cfg):
        self.discoveries += 1
        if self.method == "down":
            ok, out = self.run_ip("link", "set", "wlan0", "up")      # wpa_supplicant scans + connects by itself
        else:
            ok, out = self.run_wpa("scan")                           # one manual scan; disconnected = no autoconnect
        self.discover_until = now + cfg["wifi_saver_discover_s"]
        log(event="wifi_saver_discover", method=self.method, ok=ok, out=out[:80])

    def finish_discovery(self, now, cfg, wifi_up):
        self.discover_until = None
        self.next_discover = now + cfg["wifi_saver_discover_every_s"]
        if self.method == "down":
            if wifi_up or rd(NET + "wlan0/carrier") == "1":
                self.unpark("discovered", now)
                return
            ok, out = self.run_ip("link", "set", "wlan0", "down")
            log(event="wifi_saver_discover_done", method="down", found=False, ok=ok)
        else:
            _, nets = self.run_wpa("list_networks")
            _, res = self.run_wpa("scan_results")
            found = sorted(known_ssids(nets) & seen_ssids(res))
            log(event="wifi_saver_discover_done", method="disconnect", found=bool(found), n_seen=len(seen_ssids(res)))
            if found:
                self.unpark("discovered", now)
                return
            self.run_wpa("disconnect")      # idempotent; re-asserts the park if wpa_supplicant was restarted
        self.save()

    def step(self, now, cfg, eligible, why_not, wifi_up):
        """One policy tick. eligible/why_not from saver_eligible()."""
        if not eligible:
            self.eligible_since = None
            if self.parked:
                self.unpark(why_not, now)
            return
        if not self.parked:
            if self.eligible_since is None:
                self.eligible_since = now
            if now - self.eligible_since >= cfg["wifi_saver_grace_s"]:
                self.park(cfg.get("wifi_saver_method", "disconnect"), now, cfg)
            return
        if cfg.get("wifi_saver_method", "disconnect") != self.method and self.discover_until is None:
            self.unpark("method_change", now)      # re-parks with the new method after the grace period
            return
        if self.discover_until is not None:
            if now >= self.discover_until:
                self.finish_discovery(now, cfg, wifi_up)
        elif now >= self.next_discover:
            self.start_discovery(now, cfg)


def saver_recover_at_start(run_wpa=None, run_ip=None, state_file=SAVER_STATE):
    """A previous daemon died while Wi-Fi was parked (SIGKILL, crash): give Wi-Fi back."""
    try:
        st = json.load(open(state_file))
    except (OSError, ValueError):
        return False
    if st.get("parked"):
        s = WifiSaver(run_wpa, run_ip, state_file)
        s.method = st.get("method")
        s.unpark("daemon_start")
        return True
    return False


class Policy:
    def __init__(self):
        self.fails = {"wifi": 0, "mobile": 0}
        self.last_probe = {}
        self.uplink = None
        self.since = time.time()
        self.wifi_ok_since = None
        self.last_dial = 0.0
        self.last_redial = 0.0
        self.paused_seen = None
        self.saver = WifiSaver()

    def probe_due(self, name, cfg, now):
        return now - self.last_probe.get(name, 0) >= cfg["probe_every_s"]

    def do_probe(self, name, dev, dns, cfg, now):
        ok = probe(dev, dns, cfg)
        self.last_probe[name] = now
        prev = self.fails.get(name, 0)
        self.fails[name] = 0 if ok else prev + 1
        if ok != (prev == 0):
            log(event="probe", link=name, ok=ok, fails=self.fails[name])
        return ok

    def tick(self, cfg):
        now = time.time()
        paused = os.path.exists(FLAG)
        if paused != self.paused_seen:
            log(event="paused" if paused else "resumed")
            self.paused_seen = paused
        mobile = phoned("/data/status")
        lk = links(mobile)
        if not paused:
            fix = wifi_route_repair(lk)
            if fix:
                ok, out = ip(*fix)
                log(event="wifi_route_repair", args=list(fix), ok=ok, out=out[:160])
                lk = links(mobile)
        if paused:
            if self.saver.parked:
                self.saver.unpark("paused", now)
            st = {"paused": True, "t": now, "links": lk}
            write_status(st)
            return st
        # Probes: Wi-Fi whenever present (so a broken AP is noticed), mobile only while it is the uplink.
        if wifi_present(lk):
            if self.probe_due("wifi", cfg, now) or (self.fails["wifi"] and now - self.last_probe["wifi"] >= 8):
                self.do_probe("wifi", "wlan0", lk["wifi"]["dns"] or [lk["wifi"]["gw"]], cfg, now)
        else:
            self.fails["wifi"] = 0
            self.last_probe.pop("wifi", None)
        if mobile_present(lk) and self.uplink == "mobile":
            if self.probe_due("mobile", cfg, now) or (self.fails["mobile"] and now - self.last_probe["mobile"] >= 8):
                self.do_probe("mobile", "rmnet0", lk["mobile"]["dns"], cfg, now)
        elif not mobile_present(lk):
            self.fails["mobile"] = 0
            self.last_probe.pop("mobile", None)
        up, want_mobile = choose(lk, self.fails, cfg)
        wifi_ok = up == "wifi" and self.fails["wifi"] < cfg["probe_fail_n"]
        if wifi_ok:
            self.wifi_ok_since = self.wifi_ok_since or now
        else:
            self.wifi_ok_since = None
        # Mobile data call management.
        mstate = lk["mobile"]["state"]
        if want_mobile and mstate != "up" and lk["mobile"]["modem"] == "ONLINE" \
                and now - self.last_dial >= cfg["mobile_retry_s"]:
            self.last_dial = now
            r = phoned("/data/up", {}, timeout=60)
            log(event="mobile_up", ok=bool(r.get("ok")), ipv4=r.get("ipv4"), error=r.get("error"))
        elif not want_mobile and mstate == "up" and cfg["mobile_mode"] != "always" and \
                self.wifi_ok_since and now - self.wifi_ok_since >= cfg["mobile_hold_s"]:
            r = phoned("/data/down", {}, timeout=30)
            log(event="mobile_down", ok=bool(r.get("ok")), rx=lk["mobile"]["rx"], tx=lk["mobile"]["tx"],
                error=r.get("error"))
        elif up == "mobile" and self.fails["mobile"] >= cfg["redial_after_fails"] and \
                now - self.last_redial >= cfg["redial_min_s"]:
            self.last_redial = now
            phoned("/data/down", {}, timeout=30)
            r = phoned("/data/up", {}, timeout=60)
            self.fails["mobile"] = 0
            log(event="mobile_redial", ok=bool(r.get("ok")), error=r.get("error"))
        if mstate != "up" and (want_mobile or mobile.get("state") is None):
            # re-read after a dial so routes/DNS follow in the same tick
            lk = links(phoned("/data/status"))
            up, _ = choose(lk, self.fails, cfg)
        for args in route_plan(up, lk):
            ok, out = ip(*args)
            log(event="route", args=list(args), ok=ok, out=out[:160])
        if cfg["manage_dns"]:
            want = resolv_for(up, lk, cfg)
            if want and rd(RESOLV) + "\n" != want:
                try:
                    # Rewrite in place, never rename: the Arch chroot (/mnt/omarchy-trial) bind-mounts
                    # this file, and a rename leaves the bind on the old inode with the home router's
                    # DNS. On 2026-10-22 that broke every cloud model call on mobile data.
                    with open(RESOLV, "r+" if os.path.exists(RESOLV) else "w") as f:
                        f.seek(0)
                        f.write(want)
                        f.truncate()
                except OSError as e:
                    log(event="dns_error", error=str(e))
        try:
            eligible, why_not = saver_eligible(cfg, rd(DISPLAY, "on"), up, wifi_present(lk), False,
                                               os.path.exists(SAVER_OFF), saver_hold_active(now))
            self.saver.step(now, cfg, eligible, why_not, wifi_present(lk))
        except Exception as e:  # noqa: BLE001  the saver must never stop the uplink policy
            log(event="wifi_saver_error", error=repr(e)[:200])
        if up != self.uplink:
            log(event="uplink", old=self.uplink, new=up, after_s=round(now - self.since, 1),
                fails=dict(self.fails), mobile=mstate)
            self.uplink, self.since = up, now
        st = {"paused": False, "t": now, "uplink": up, "since": self.since, "want_mobile": want_mobile,
              "fails": dict(self.fails), "last_probe": dict(self.last_probe), "links": lk,
              "mobile_mode": cfg["mobile_mode"], "wifi_saver": self.saver.snapshot()}
        write_status(st)
        return st


def run():
    os.makedirs(STATE_DIR, exist_ok=True)
    import fcntl, signal
    os.makedirs(os.path.dirname(STATUS), exist_ok=True)
    lockf = open(os.path.dirname(STATUS) + "/lock", "w")
    try:
        fcntl.flock(lockf, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("s22-netpolicy: another daemon holds the lock", file=sys.stderr)
        return 1
    log(event="start", pid=os.getpid())
    if saver_recover_at_start():
        log(event="wifi_saver_recovered")
    p = Policy()

    def _term(*_):
        if p.saver.parked:
            p.saver.unpark("daemon_exit")
        sys.exit(0)
    signal.signal(signal.SIGTERM, _term)
    while True:
        cfg = load_cfg()
        try:
            p.tick(cfg)
        except Exception as e:  # noqa: BLE001  never die: keepalive would restart us anyway
            log(event="error", error=repr(e)[:300])
        time.sleep(cfg["poll_s"])


def status():
    st = {}
    try:
        st = json.load(open(STATUS))
    except (OSError, ValueError):
        print("no status (daemon not running?)")
    age = time.time() - st.get("t", 0)
    lk = st.get("links") or links(phoned("/data/status"))
    w, m, u = lk["wifi"], lk["mobile"], lk["usb"]
    print("netpolicy: %s  uplink=%s for %ss  mobile_mode=%s  (status %.0fs old)" % (
        "PAUSED" if st.get("paused") or os.path.exists(FLAG) else "active", st.get("uplink"),
        int(time.time() - st.get("since", time.time())), st.get("mobile_mode"), age))
    print("  wifi   wlan0  %-7s gw=%s dns=%s fails=%s" % (w["oper"], w["gw"], ",".join(w["dns"]),
                                                       (st.get("fails") or {}).get("wifi")))
    print("  mobile rmnet0 %-7s ip=%s dns=%s act=%s%s rx=%s tx=%s fails=%s" % (
        m["state"], m["ipv4"], ",".join(m["dns"]), m["act"], "+5G" if m.get("nr") else "", m["rx"], m["tx"],
        (st.get("fails") or {}).get("mobile")))
    print("  usb    ecm0   %-7s carrier=%s udc=%s (local link)" % (u["oper"], u["carrier"], u["udc"]))
    sv = st.get("wifi_saver") or {}
    print("  wifi saver: %s%s parked=%s method=%s discoveries=%s last=%s" % (
        "OFF (kill switch) " if os.path.exists(SAVER_OFF) else "",
        "HOLD (deadman) " if saver_hold_active() else "", sv.get("parked"), sv.get("method"),
        sv.get("discoveries"), sv.get("reason")))
    print("  routes: " + " | ".join(line for *_, line in defaults()))
    print("  dns:    " + " ".join(l.split()[1] for l in rd(RESOLV).splitlines() if l.startswith("nameserver")))
    try:
        ts = subprocess.run(["tailscale", "status", "--peers=false", "--json"], capture_output=True,
                            text=True, timeout=5)
        s = json.loads(ts.stdout).get("Self", {})
        print("  tailscale: online=%s relay=%s addrs=%s" % (s.get("Online"), s.get("Relay"),
                                                           ",".join(s.get("Addrs") or [])[:120]))
    except Exception:  # noqa: BLE001
        pass
    return 0


def main(argv):
    cmd = argv[1] if len(argv) > 1 else "status"
    if cmd == "run":
        return run() or 0
    elif cmd == "status":
        return status()
    elif cmd == "off":
        os.makedirs(STATE_DIR, exist_ok=True)
        open(FLAG, "w").close()
        saver_recover_at_start()
        legacy_routes()
        print("paused; boot-time routes restored")
    elif cmd == "on":
        try:
            os.unlink(FLAG)
        except FileNotFoundError:
            pass
        print("resumed")
    elif cmd == "saver" and len(argv) > 2 and argv[2] in ("off", "on", "status"):
        if argv[2] == "off":
            os.makedirs(STATE_DIR, exist_ok=True)
            open(SAVER_OFF, "w").close()
            # the daemon unparks within one poll; do it here too in case it is not running
            saver_recover_at_start()
            print("wifi saver off (persistent); Wi-Fi scanning restored")
        elif argv[2] == "on":
            try:
                os.unlink(SAVER_OFF)
            except FileNotFoundError:
                pass
            print("wifi saver on")
        else:
            print(rd(SAVER_STATE, "{}"), "kill switch" if os.path.exists(SAVER_OFF) else "")
    elif cmd == "probe" and len(argv) > 2:
        dev = argv[2]
        lk = links(phoned("/data/status"))
        dns = {"wlan0": lk["wifi"]["dns"], "rmnet0": lk["mobile"]["dns"], "ecm0": [USB_GW]}.get(dev, [])
        ok = probe(dev, dns, load_cfg())
        print("%s: %s" % (dev, "ok" if ok else "FAIL"))
        return 0 if ok else 1
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv) or 0)
