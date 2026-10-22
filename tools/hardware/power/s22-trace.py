#!/usr/bin/env python3
"""s22-trace: lightweight reliability trace for long unplugged runs (keepalive service "trace").

    s22-trace run        the logger (one instance, flock)
    s22-trace status     last sample + file sizes
    s22-trace off|on     pause/resume sampling (persistent flag; the process keeps running)

Every SAMPLE_S (60 s of awake time; nothing runs while the phone is suspended) it writes one
"sample" record: battery (%, raw SOC 0.01 % steps, current_now mA, voltage mV, status, temp),
thermal zones (max + battery/BIG), memory, load, CPU busy since the last sample, display state,
uplink + link states, suspend counters (kernel success/fail, autosleep cycles/slept_s) and the
pids of the supervised daemons. Between samples it polls the display state and the uplink every
TICK_S and writes an "event" record at once when they change. At each sample it also tails the
other daemons' JSONL logs (netpolicy, autosleep, keepalive, turnguard, llama-ondemand) and copies
the interesting lines (failovers, restarts, turn start/end/timeout/cancel, errors) as events, so
one file holds the whole story. Autosleep cycles are folded into the sample counters, not copied.

Output: /srv/s22/state/trace/trace.jsonl, rotated at ROTATE_BYTES to trace.jsonl.N.gz (KEEP files;
~3 days of samples fit in a few MB). /srv is the big partition; the root fs is never written.
Budget: one sample reads ~40 small sysfs/proc files (<10 ms CPU); measured well under 1 % CPU.
"""
import fcntl, glob, gzip, json, os, shutil, sys, time

STATE = "/srv/s22/state/trace"
OUT = STATE + "/trace.jsonl"
FLAG = STATE + "/paused"
LOCK = "/run/s22-trace.lock"
SAMPLE_S = 60
TICK_S = 10
ROTATE_BYTES = 1 << 20
KEEP = 30
B = "/sys/class/power_supply/battery/"
DISPLAY = "/run/s22-display-state"
NETPOLICY_STATUS = "/run/s22-netpolicy/status.json"
AUTOSLEEP_STATUS = "/run/s22-autosleep/status.json"
NET = "/sys/class/net/"
TAILS = {
    "netpolicy": "/srv/s22/state/netpolicy/netpolicy.jsonl",
    "autosleep": "/srv/s22/state/autosleep/autosleep.jsonl",
    "keepalive": "/srv/s22/state/keepalive/keepalive.jsonl",
    "turnguard": "/srv/s22/state/turnguard/turnguard.jsonl",
    "llama": "/srv/s22/state/llama-ondemand/ondemand.jsonl",
}
DAEMONS = {  # name -> cmdline needle (pid change between samples = a restart)
    "openunum": "node src/server.mjs",
    "keepalive": "/srv/s22/hardware/bin/s22-keepalive",
    "netpolicy": "s22-netpolicy run",
    "autosleep": "s22-autosleep run",
    "llama": "s22-llama-ondemand serve",
    "turnguard": "s22-turnguard run",
    "phoned": "s22-phoned serve",
    "guardian": "s22-guardian serve",
    "tailscaled": "tailscaled",
    "wpa": "wpa_supplicant -D nl80211 -i wlan0",
}


def rd(p, d=""):
    try:
        with open(p) as f:
            return f.read().strip()
    except OSError:
        return d


def num(s, scale=1.0):
    try:
        return round(int(s) * scale, 3)
    except (TypeError, ValueError):
        return None


def battery():
    return {"cap": num(rd(B + "capacity")), "raw": num(rd(B + "batt_read_raw_soc")),
            "ma": num(rd(B + "current_now")), "mv": num(rd(B + "voltage_now"), 0.001),
            "st": rd(B + "status"), "temp": num(rd(B + "temp"), 0.1),
            "slate": rd(B + "batt_slate_mode") or None}


def thermal(zones):
    out = {}
    for name, path in zones:
        v = num(rd(path), 0.001)
        if v is not None:
            out[name] = round(v, 1)
    return out


def thermal_zones():
    z = []
    for d in sorted(glob.glob("/sys/class/thermal/thermal_zone*")):
        z.append((rd(d + "/type") or os.path.basename(d), d + "/temp"))
    return z


def meminfo():
    m = {}
    for line in rd("/proc/meminfo").splitlines():
        k, _, v = line.partition(":")
        if k in ("MemTotal", "MemAvailable", "SwapTotal", "SwapFree"):
            m[k] = int(v.split()[0]) // 1024
    return {"avail_mb": m.get("MemAvailable"), "swap_used_mb": (m.get("SwapTotal", 0) - m.get("SwapFree", 0)) or 0}


def cpu_times():
    try:
        with open("/proc/stat") as f:
            v = [int(x) for x in f.readline().split()[1:8]]
        return sum(v) - v[3] - v[4], sum(v)
    except (OSError, ValueError):
        return 0, 0


def links():
    def link(n):
        return {"oper": rd(NET + n + "/operstate") or None, "carrier": rd(NET + n + "/carrier") or None}
    np = {}
    try:
        np = json.load(open(NETPOLICY_STATUS))
    except (OSError, ValueError):
        pass
    saver = np.get("wifi_saver") or {}
    return {"uplink": np.get("uplink"), "np_age_s": round(time.time() - np.get("t", 0)) if np else None,
            "wlan0": link("wlan0"), "rmnet0": link("rmnet0"), "ecm0": link("ecm0"),
            "udc": rd("/sys/class/udc/10b00000.dwc3/state") or None, "wifi_parked": saver.get("parked")}


def suspend():
    a = {}
    try:
        a = json.load(open(AUTOSLEEP_STATUS))
    except (OSError, ValueError):
        pass
    return {"ok": num(rd("/sys/power/suspend_stats/success")), "fail": num(rd("/sys/power/suspend_stats/fail")),
            "as_state": a.get("state"), "as_cycles": a.get("cycles"), "as_slept_s": a.get("slept_s"),
            "as_aborts": a.get("aborts"), "inhibit": a.get("inhibit")}


def daemon_pids():
    found = {}
    for p in os.listdir("/proc"):
        if not p.isdigit():
            continue
        try:
            with open("/proc/%s/cmdline" % p, "rb") as f:
                c = f.read().replace(b"\0", b" ").decode("utf-8", "replace")
        except OSError:
            continue
        for name, needle in DAEMONS.items():
            if needle in c and name not in found and "s22-trace" not in c:
                found[name] = int(p)
    return found


def interesting(src, d):
    """Which tailed log lines become trace events (None = skip)."""
    ev = str(d.get("event", ""))
    if src == "autosleep":
        return ev in ("start", "disabled", "enabled") or "error" in ev
    if src == "netpolicy":
        return ev in ("uplink", "mobile_up", "mobile_down", "mobile_redial", "start", "error", "dns_error",
                      "paused", "resumed", "wifi_route_repair") or ev.startswith("wifi_saver")
    if src == "keepalive":
        return ev in ("restart", "restart_limited", "start", "root_low", "held", "released") or "error" in ev
    if src == "turnguard":
        return ev not in ("poll",)
    if src == "llama":
        return ev in ("load", "unload", "load_failed", "load_timeout", "request", "request_abort",
                      "retry_budget", "orphan_killed", "stuck_backend", "start") or "error" in ev
    return False


class Tail:
    def __init__(self, path):
        self.path = path
        try:
            st = os.stat(path)
            self.ino, self.pos = st.st_ino, st.st_size   # start at the end: only new lines
        except OSError:
            self.ino, self.pos = None, 0

    def lines(self, limit=400):
        try:
            st = os.stat(self.path)
        except OSError:
            return []
        if st.st_ino != self.ino or st.st_size < self.pos:  # rotated/truncated
            self.ino, self.pos = st.st_ino, 0
        if st.st_size == self.pos:
            return []
        out = []
        try:
            with open(self.path, "rb") as f:
                f.seek(self.pos)
                data = f.read(min(st.st_size - self.pos, 1 << 20))
            cut = data.rfind(b"\n") + 1
            self.pos += cut
            for raw in data[:cut].splitlines()[-limit:]:
                try:
                    out.append(json.loads(raw))
                except ValueError:
                    pass
        except OSError:
            pass
        return out


class Writer:
    def __init__(self, path=OUT, rotate=ROTATE_BYTES, keep=KEEP):
        self.path, self.rotate, self.keep = path, rotate, keep

    def write(self, rec):
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            if os.path.exists(self.path) and os.path.getsize(self.path) > self.rotate:
                self.roll()
            with open(self.path, "a") as f:
                f.write(json.dumps(rec, separators=(",", ":")) + "\n")
        except OSError:
            pass

    def roll(self):
        olds = sorted(glob.glob(self.path + ".*.gz"), key=lambda p: int(p.rsplit(".", 2)[-2]))
        n = int(olds[-1].rsplit(".", 2)[-2]) + 1 if olds else 1
        with open(self.path, "rb") as src, gzip.open("%s.%d.gz" % (self.path, n), "wb") as dst:
            shutil.copyfileobj(src, dst)
        os.unlink(self.path)
        for p in olds[:max(0, len(olds) + 1 - self.keep)]:
            os.unlink(p)


class Tracer:
    def __init__(self, writer=None, clock=time.time):
        self.w = writer or Writer()
        self.clock = clock
        self.zones = thermal_zones()
        self.tails = {k: Tail(v) for k, v in TAILS.items()}
        self.cpu = cpu_times()
        self.pids = {}
        self.display = None
        self.uplink = None
        self.parked = None
        self.cycles_since = {"n": 0, "slept": 0.0, "aborts": 0, "wake": {}}

    def emit(self, kind, **kw):
        kw["t"] = round(self.clock(), 1)
        kw["k"] = kind
        self.w.write(kw)

    def fold_autosleep(self, d):
        if d.get("event") != "cycle":
            return
        c = self.cycles_since
        c["n"] += 1
        c["slept"] += float(d.get("slept_s") or 0)
        if d.get("err"):
            c["aborts"] += 1
        for w in (d.get("why_wake") or [])[:1]:
            key = str(w).split()[-1][:40] if w else "?"
            c["wake"][key] = c["wake"].get(key, 0) + 1

    def tick(self):
        """Cheap poll: display/uplink/parked changes become events at once."""
        disp = rd(DISPLAY, "on")
        lk = links()
        if disp != self.display:
            self.emit("event", src="display", state=disp, was=self.display, bat=battery())
            self.display = disp
        if lk["uplink"] != self.uplink:
            self.emit("event", src="uplink", uplink=lk["uplink"], was=self.uplink)
            self.uplink = lk["uplink"]
        if lk["wifi_parked"] != self.parked:
            self.emit("event", src="wifi_parked", parked=lk["wifi_parked"], was=self.parked)
            self.parked = lk["wifi_parked"]
        return disp, lk

    def sample(self):
        disp, lk = self.tick()
        for src, tail in self.tails.items():
            for d in tail.lines():
                if src == "autosleep":
                    self.fold_autosleep(d)
                if interesting(src, d):
                    self.emit("event", src=src, rec=d)
        b0, t0 = self.cpu
        self.cpu = cpu_times()
        busy = round(100.0 * (self.cpu[0] - b0) / max(1, self.cpu[1] - t0), 1)
        pids = daemon_pids()
        restarted = sorted(n for n, p in pids.items() if self.pids.get(n) not in (None, p))
        gone = sorted(n for n in self.pids if n not in pids)
        if restarted or gone:
            self.emit("event", src="pids", restarted=restarted, gone=gone,
                      old={n: self.pids.get(n) for n in restarted + gone}, new={n: pids.get(n) for n in restarted})
        self.pids = pids
        c = self.cycles_since
        self.emit("sample", bat=battery(), therm=thermal(self.zones), mem=meminfo(),
                  load=rd("/proc/loadavg").split()[:3], busy=busy, disp=disp, net=lk, susp=suspend(),
                  cyc={"n": c["n"], "slept": round(c["slept"], 1), "aborts": c["aborts"], "wake": c["wake"]},
                  pids=pids, up=rd("/proc/uptime").split()[0])
        self.cycles_since = {"n": 0, "slept": 0.0, "aborts": 0, "wake": {}}


def run():
    os.makedirs(STATE, exist_ok=True)
    lockf = open(LOCK, "w")
    try:
        fcntl.flock(lockf, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("s22-trace: already running", file=sys.stderr)
        return 1
    try:
        os.nice(10)
    except OSError:
        pass
    tr = Tracer()
    tr.emit("event", src="trace", what="start", pid=os.getpid(), boot_id=rd("/proc/sys/kernel/random/boot_id"))
    next_sample = 0.0
    while True:
        now = time.monotonic()           # awake time: a suspend does not count toward SAMPLE_S
        try:
            if os.path.exists(FLAG):
                pass
            elif now >= next_sample:
                tr.sample()
                next_sample = now + SAMPLE_S
            else:
                tr.tick()
        except Exception as e:  # noqa: BLE001  never die (keepalive would restart us anyway)
            tr.emit("event", src="trace", what="error", error=repr(e)[:200])
            next_sample = now + SAMPLE_S
        time.sleep(TICK_S)


def status():
    for p in sorted(glob.glob(OUT + "*")):
        print("%8d  %s" % (os.path.getsize(p), p))
    try:
        with open(OUT, "rb") as f:
            f.seek(max(0, os.path.getsize(OUT) - 8192))
            last = [l for l in f.read().splitlines() if b'"k":"sample"' in l][-1:]
        for l in last:
            print(l.decode())
    except (OSError, IndexError):
        pass
    print("paused" if os.path.exists(FLAG) else "active")
    return 0


def main(argv):
    cmd = argv[1] if len(argv) > 1 else "status"
    if cmd == "run":
        return run() or 0
    if cmd == "off":
        os.makedirs(STATE, exist_ok=True)
        open(FLAG, "w").close()
        print("trace paused")
        return 0
    if cmd == "on":
        try:
            os.unlink(FLAG)
        except FileNotFoundError:
            pass
        print("trace active")
        return 0
    if cmd == "status":
        return status()
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
