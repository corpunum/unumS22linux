"""Host tests for s22-netpolicy (no phone needed): python3 -m pytest -q test_s22_netpolicy.py"""
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("netpolicy", Path(__file__).with_name("s22-netpolicy.py"))
N = importlib.util.module_from_spec(spec)
spec.loader.exec_module(N)
CFG = dict(N.CONFIG)


def check(cond):
    if not cond:          # not a bare assert: the host runner also runs python -O
        raise AssertionError("check failed")

BOOT = [("ecm0", "10.55.0.1", 0, "default via 10.55.0.1 dev ecm0"),
        ("wlan0", "192.168.50.1", 600, "default via 192.168.50.1 dev wlan0 metric 600")]


def lk(wifi=True, mobile=False, usb=True, defaults=BOOT):
    return {"wifi": {"oper": "up" if wifi else "down", "gw": "192.168.50.1" if wifi else None,
                     "dns": ["192.168.50.1"]},
            "mobile": {"state": "up" if mobile else "down", "ipv4": "100.64.33.189" if mobile else None,
                       "dns": ["203.0.113.15", "203.0.113.12"], "modem": "ONLINE"},
            "usb": {"oper": "up" if usb else "down", "carrier": usb, "udc": "configured"},
            "defaults": defaults}


def test_preference_order():
    check(N.choose(lk(), {}, CFG) == ("wifi", False))
    check(N.choose(lk(wifi=False, mobile=True), {}, CFG) == ("mobile", True))
    check(N.choose(lk(wifi=False), {}, CFG) == ("usb", True))          # USB only until mobile is up
    check(N.choose(lk(wifi=False, usb=False), {}, CFG) == (None, True))


def test_failing_wifi_probe_fails_over_to_mobile():
    check(N.choose(lk(mobile=True), {"wifi": 3}, CFG) == ("mobile", True))
    check(N.choose(lk(mobile=True), {"wifi": 2}, CFG)[0] == "wifi")


def test_mobile_modes():
    check(N.choose(lk(), {}, dict(CFG, mobile_mode="always"))[1] is True)
    check(N.choose(lk(wifi=False), {}, dict(CFG, mobile_mode="off"))[1] is False)


def test_route_plan_wifi_drops_usb_default_and_adds_policy_route():
    plan = N.route_plan("wifi", lk())
    check(("route", "del", "default", "via", "10.55.0.1", "dev", "ecm0") in plan)
    check(("route", "replace", "default", "via", "192.168.50.1", "dev", "wlan0", "metric", "50") in plan)


def test_route_plan_switch_to_mobile_and_idempotent():
    d = [("wlan0", "192.168.50.1", 50, "default via 192.168.50.1 dev wlan0 metric 50"),
         ("rmnet0", None, 9000, "default dev rmnet0 scope link metric 9000")]
    plan = N.route_plan("mobile", lk(wifi=False, mobile=True, defaults=d))
    check(plan == [("route", "del", "default", "via", "192.168.50.1", "dev", "wlan0", "metric", "50"),
                    ("route", "replace", "default", "dev", "rmnet0", "metric", "50")])
    done = [("rmnet0", None, 50, "default dev rmnet0 scope link metric 50"), d[1]]
    check(N.route_plan("mobile", lk(wifi=False, mobile=True, defaults=done)) == [])


def test_usb_only_uplink():
    plan = N.route_plan("usb", lk(wifi=False, defaults=[BOOT[0]]))
    check(plan[-1] == ("route", "replace", "default", "via", "10.55.0.1", "dev", "ecm0", "metric", "50"))


def test_dns_follows_uplink():
    check(N.resolv_for("mobile", lk(mobile=True), CFG).startswith(
        "nameserver 203.0.113.15\nnameserver 203.0.113.12\nnameserver 1.1.1.1\n"))
    check(N.resolv_for("wifi", lk(), CFG).startswith("nameserver 192.168.50.1\nnameserver 1.1.1.1\n"))
    check(N.resolv_for(None, lk(), CFG) is None)


def test_wifi_route_repair(monkeypatch):
    monkeypatch.setattr(N, "lease", lambda: {"gateway": "192.168.50.1", "address": "192.168.50.14"})
    monkeypatch.setattr(N, "ip", lambda *a: (True, "inet 192.168.50.14/24 brd 192.168.50.255 scope global wlan0"))
    l = lk(defaults=[])
    l["wifi"]["gw"] = None
    check(N.wifi_route_repair(l) == ("route", "replace", "default", "via", "192.168.50.1", "dev", "wlan0",
                                      "metric", "600"))
    check(N.wifi_route_repair(lk()) is None)                       # route present
    monkeypatch.setattr(N, "ip", lambda *a: (True, ""))            # address gone -> leave it to udhcpc
    check(N.wifi_route_repair(l) is None)


# ---- Wi-Fi saver (#71)

class _Rec:
    def __init__(self, scan=""):
        self.cmds = []
        self.scan = scan

    def wpa(self, *args, timeout=8):
        self.cmds.append(("wpa",) + args)
        if args[0] == "list_networks":
            return True, "network id / ssid / bssid / flags\n0\thomenet\tany\t[DISABLED]"
        if args[0] == "scan_results":
            return True, "bssid / frequency / signal level / flags / ssid\n" + self.scan
        return True, "OK"

    def ip(self, *args):
        self.cmds.append(("ip",) + args)
        return True, ""


SCFG = dict(CFG, wifi_saver_grace_s=60, wifi_saver_discover_every_s=900, wifi_saver_discover_s=15)


def _saver(tmp_state, scan=""):
    r = _Rec(scan)
    return N.WifiSaver(run_wpa=r.wpa, run_ip=r.ip, state_file=tmp_state), r


def _tmp():
    import tempfile, os
    return os.path.join(tempfile.mkdtemp(), "saver.json")


def test_saver_eligibility():
    e = lambda **kw: N.saver_eligible(SCFG, kw.get("display", "off"), kw.get("up", "mobile"), kw.get("wifi", False),
                                      kw.get("paused", False), kw.get("killed", False), kw.get("hold", False))
    check(e() == (True, ""))
    check(e(display="on") == (False, "screen_on"))
    check(e(up="wifi")[0] is False and e(up=None)[0] is False)
    check(e(wifi=True) == (False, "wifi_up"))
    check(e(killed=True) == (False, "disabled") and e(hold=True) == (False, "deadman_hold"))
    check(N.saver_eligible(dict(SCFG, wifi_saver=False), "off", "mobile", False, False, False, False)[0] is False)


def test_saver_parks_after_grace_and_unparks_on_screen_on(monkeypatch):
    monkeypatch.setattr(N, "log", lambda **kw: None)
    s, r = _saver(_tmp())
    s.step(1000, SCFG, True, "", False)
    check(not s.parked and r.cmds == [])
    s.step(1059, SCFG, True, "", False)
    check(not s.parked)
    s.step(1060, SCFG, True, "", False)
    check(s.parked and r.cmds == [("wpa", "disconnect")])
    s.step(1100, SCFG, False, "screen_on", False)
    check(not s.parked and r.cmds[-2:] == [("ip", "link", "set", "wlan0", "up"), ("wpa", "reconnect")])
    # the grace period starts again after an unpark
    s.step(1101, SCFG, True, "", False)
    check(not s.parked)


def test_saver_down_method(monkeypatch):
    monkeypatch.setattr(N, "log", lambda **kw: None)
    monkeypatch.setattr(N, "rd", lambda p, d="": "0")
    s, r = _saver(_tmp())
    cfg = dict(SCFG, wifi_saver_method="down", wifi_saver_grace_s=0)
    s.step(0, cfg, True, "", False)
    check(s.parked and s.method == "down" and r.cmds == [("ip", "link", "set", "wlan0", "down")])
    s.step(900, cfg, True, "", False)                    # discovery: link up for 15 s
    check(r.cmds[-1] == ("ip", "link", "set", "wlan0", "up") and s.discover_until == 915)
    s.step(915, cfg, True, "", False)                    # nothing associated -> down again
    check(s.parked and r.cmds[-1] == ("ip", "link", "set", "wlan0", "down") and s.next_discover == 1815)


def test_saver_discovery_finds_known_network(monkeypatch):
    monkeypatch.setattr(N, "log", lambda **kw: None)
    s, r = _saver(_tmp(), scan="aa:bb\t2412\t-60\t[WPA2-PSK-CCMP][ESS]\tcafe\n")
    cfg = dict(SCFG, wifi_saver_grace_s=0)
    s.step(0, cfg, True, "", False)
    s.step(900, cfg, True, "", False)
    check(r.cmds[-1] == ("wpa", "scan"))
    s.step(916, cfg, True, "", False)                    # only an unknown SSID: stay parked
    check(s.parked and s.next_discover == 1816 and r.cmds[-1] == ("wpa", "disconnect"))
    r.scan = "aa:bb\t2412\t-60\t[WPA2-PSK-CCMP][ESS]\thomenet\n"
    s.step(1816, cfg, True, "", False)
    s.step(1832, cfg, True, "", False)
    check(not s.parked and s.reason == "discovered" and r.cmds[-1] == ("wpa", "reconnect"))


def test_saver_method_change_and_restart_recovery(monkeypatch):
    monkeypatch.setattr(N, "log", lambda **kw: None)
    st = _tmp()
    s, r = _saver(st)
    s.step(0, dict(SCFG, wifi_saver_grace_s=0), True, "", False)
    check(s.parked)
    s.step(5, dict(SCFG, wifi_saver_grace_s=0, wifi_saver_method="down"), True, "", False)
    check(not s.parked and s.reason == "method_change")
    s.step(10, dict(SCFG, wifi_saver_grace_s=0, wifi_saver_method="down"), True, "", False)
    check(s.parked and s.method == "down")
    r2 = _Rec()
    check(N.saver_recover_at_start(r2.wpa, r2.ip, st) is True)          # daemon died while parked
    check(("wpa", "reconnect") in r2.cmds and ("ip", "link", "set", "wlan0", "up") in r2.cmds)
    check(N.saver_recover_at_start(r2.wpa, r2.ip, st) is False)         # already restored


def test_saver_ssid_parsers():
    check(N.known_ssids("network id / ssid / bssid / flags\n0\thome net\tany\t\n1\tx\tany\t[CURRENT]")
          == {"home net", "x"})
    check(N.seen_ssids("bssid / frequency / signal level / flags / ssid\naa\t1\t-1\t[ESS]\t\nbb\t1\t-1\t[ESS]\tz")
          == {"z"})


class _MonkeyPatch:
    """Minimal stand-in so the host runner can execute this file without pytest."""
    def __init__(self):
        self.undo = []

    def setattr(self, obj, name, value):
        self.undo.append((obj, name, getattr(obj, name)))
        setattr(obj, name, value)

    def restore(self):
        for obj, name, value in reversed(self.undo):
            setattr(obj, name, value)


if __name__ == "__main__":
    import inspect
    n = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            mp = _MonkeyPatch()
            try:
                fn(mp) if inspect.signature(fn).parameters else fn()
            finally:
                mp.restore()
            n += 1
    print("ok: %d netpolicy tests" % n)
