#!/usr/bin/env python3
"""s22-netpolicy: pick the phone's internet uplink (Wi-Fi > mobile data > USB).

    s22-netpolicy run       the daemon (keepalive service "netpolicy")
    s22-netpolicy status    one-screen state (links, active uplink, routes, DNS)
    s22-netpolicy off|on    pause/resume (persistent flag); "off" also puts the
                            boot-time routes back (ecm0 default, metric 0)
    s22-netpolicy probe IF  one connectivity probe bound to IF (wlan0/rmnet0/ecm0)

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
            plan.append(("route", "del") + tuple(line.split()))
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
                    with open(RESOLV + ".netpolicy", "w") as f:
                        f.write(want)
                    os.replace(RESOLV + ".netpolicy", RESOLV)
                except OSError as e:
                    log(event="dns_error", error=str(e))
        if up != self.uplink:
            log(event="uplink", old=self.uplink, new=up, after_s=round(now - self.since, 1),
                fails=dict(self.fails), mobile=mstate)
            self.uplink, self.since = up, now
        st = {"paused": False, "t": now, "uplink": up, "since": self.since, "want_mobile": want_mobile,
              "fails": dict(self.fails), "last_probe": dict(self.last_probe), "links": lk,
              "mobile_mode": cfg["mobile_mode"]}
        write_status(st)
        return st


def run():
    os.makedirs(STATE_DIR, exist_ok=True)
    log(event="start", pid=os.getpid())
    p = Policy()
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
        run()
    elif cmd == "status":
        return status()
    elif cmd == "off":
        os.makedirs(STATE_DIR, exist_ok=True)
        open(FLAG, "w").close()
        legacy_routes()
        print("paused; boot-time routes restored")
    elif cmd == "on":
        try:
            os.unlink(FLAG)
        except FileNotFoundError:
            pass
        print("resumed")
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
