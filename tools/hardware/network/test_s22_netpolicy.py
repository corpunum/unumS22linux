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
            "mobile": {"state": "up" if mobile else "down", "ipv4": "<cgnat-ip>" if mobile else None,
                       "dns": ["<carrier-ip>", "<carrier-ip>"], "modem": "ONLINE"},
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
        "nameserver <carrier-ip>\nnameserver <carrier-ip>\nnameserver 1.1.1.1\n"))
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
