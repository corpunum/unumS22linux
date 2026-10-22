#!/usr/bin/env python3
"""s22-autosleep: suspend-to-RAM while the S22 is locked and idle.

    s22-autosleep run            the daemon (keepalive service "autosleep")
    s22-autosleep off|on         disable/enable (persistent flag file; the daemon
                                 obeys within one poll, no restart needed)
    s22-autosleep status         flag, last cycles, totals
    s22-autosleep once SECONDS   one suspend now with an RTC alarm (test)

Policy (all knobs in CONFIG, overridable by /srv/s22/state/autosleep/config.json):
* Screen on (/run/s22-display-state != off): never suspend; cpufreq governors
  back to schedutil.
* Screen off: governors -> powersave at once (cheap win), then after
  lock_grace_s of continuous "off" + idle, suspend cycles start:
  each cycle arms the RTC for max_sleep_s (housekeeping wake) and writes
  "mem" to /sys/power/state (mem_sleep = deep). Any wakeup IRQ (Power key,
  modem mailbox, Wi-Fi WoW, RTC) resumes. After a wake the phone stays up at
  least awake_s, then re-checks the inhibitors.
* Inhibitors (any one keeps the phone awake): disable flag; files in
  /run/s22-autosleep/inhibit.d/ (newer than their content's seconds, or forever
  if empty); an SSH session (sshd-session process) when ssh_inhibits; a
  running ALSA PCM (voice loop, s22-say, recording); an active call in
  s22-phoned; recent CPU load above busy_pct; USB data host attached unless
  suspend_on_usb; an active kernel wakeup source (not in ignore_wakeup_sources).
* After a wake caused only by a modem/Wi-Fi IRQ the minimum awake time is net_awake_s
  (2.5 s) instead of awake_s, so mobile-data keepalive packets cost little (2026-10-22).
* Race-free with the kernel: when no ignored source is active it uses the
  /sys/power/wakeup_count handshake, so an SMS/packet that arrives just before
  suspend aborts it instead of being slept on.

Read-only toward everything except /sys/power/state, the RTC wakealarm,
cpufreq governors and its own state dir. Logs JSON lines to
/srv/s22/state/autosleep/autosleep.jsonl.
"""
import json, os, signal, subprocess, sys, time

STATE_DIR = "/srv/s22/state/autosleep"
FLAG = STATE_DIR + "/disabled"
LOG = STATE_DIR + "/autosleep.jsonl"
CFG_FILE = STATE_DIR + "/config.json"
INHIBIT_DIR = "/run/s22-autosleep/inhibit.d"
STATUS = "/run/s22-autosleep/status.json"
DISPLAY = "/run/s22-display-state"
RTC = "/sys/class/rtc/rtc0/wakealarm"
B = "/sys/class/power_supply/battery/"

CONFIG = {
    "lock_grace_s": 30,        # screen off this long before the first suspend
    "awake_s": 6,              # minimum awake time after every resume
    "net_awake_s": 2.5,        # ... after a wake caused only by the modem/Wi-Fi IRQ (keepalive-sized
                               # packet): long enough for tailscaled to answer, then back to sleep.
                               # Real traffic (ssh session, CPU load, wakeup source) re-inhibits anyway.
    "max_sleep_s": 900,        # RTC housekeeping wake (keepalive, Tailscale, battery)
    "poll_s": 2,
    "busy_pct": 25.0,          # mean CPU busy (all cores) over busy_window_s that inhibits
    "busy_window_s": 6,
    "ssh_inhibits": True,
    "suspend_on_usb": False,   # USB data host attached -> stay awake (rescue path, charging)
    "ignore_wakeup_sources": ["dwc3-otg", "usb_notify"],
    "locked_governor": "powersave",
    "unlocked_governor": "schedutil",
    "governors": True,
    "max_fail_backoff_s": 120,
}


def rd(p, d=""):
    try:
        with open(p) as f:
            return f.read().strip()
    except OSError:
        return d


def wr(p, v):
    with open(p, "w") as f:
        f.write(v)


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


def cpu_times():
    v = [int(x) for x in open("/proc/stat").readline().split()[1:8]]
    return sum(v) - v[3] - v[4], sum(v)


def set_governor(g):
    base = "/sys/devices/system/cpu/cpufreq"
    changed = False
    for p in sorted(os.listdir(base)):
        if p.startswith("policy"):
            f = "%s/%s/scaling_governor" % (base, p)
            if rd(f) != g:
                try:
                    wr(f, g)
                    changed = True
                except OSError:
                    pass
    return changed


def active_wakeup_sources():
    out = []
    for w in os.listdir("/sys/class/wakeup"):
        d = "/sys/class/wakeup/" + w
        if rd(d + "/active_time_ms", "0") not in ("0", ""):
            out.append(rd(d + "/name"))
    return out


def pcm_running():
    for card in os.listdir("/proc/asound"):
        if not card.startswith("card"):
            continue
        cd = "/proc/asound/" + card
        try:
            pcms = os.listdir(cd)
        except OSError:
            continue
        for p in pcms:
            if p.startswith("pcm"):
                try:
                    for s in os.listdir(cd + "/" + p):
                        if s.startswith("sub") and "RUNNING" in rd("%s/%s/%s/status" % (cd, p, s)):
                            return "%s/%s/%s" % (card, p, s)
                except OSError:
                    pass
    return None


def ssh_sessions():
    n = 0
    for pid in os.listdir("/proc"):
        if pid.isdigit():
            c = rd("/proc/%s/cmdline" % pid).replace("\0", " ")
            if c.startswith("sshd-session: ") and "[priv]" not in c and "[net]" not in c:
                n += 1
    return n


def calls_active():
    try:
        import urllib.request
        with urllib.request.urlopen("http://127.0.0.1:8095/status", timeout=3) as r:
            return bool(json.load(r).get("calls"))
    except Exception:  # noqa: BLE001  phoned down/slow: do not block sleep on it
        return False


def usb_host():
    return rd("/sys/class/udc/10b00000.dwc3/state") == "configured"


def inhibit_files():
    out = []
    try:
        names = os.listdir(INHIBIT_DIR)
    except OSError:
        return out
    now = time.time()
    for n in names:
        p = os.path.join(INHIBIT_DIR, n)
        try:
            ttl = rd(p)
            if ttl and now - os.path.getmtime(p) > float(ttl):
                os.unlink(p)
                continue
        except (OSError, ValueError):
            pass
        out.append(n)
    return out


def inhibitors(cfg, busy):
    why = []
    if os.path.exists(FLAG):
        why.append("disabled")
    why += ["file:" + n for n in inhibit_files()]
    if cfg["ssh_inhibits"]:
        n = ssh_sessions()
        if n:
            why.append("ssh:%d" % n)
    p = pcm_running()
    if p:
        why.append("pcm:" + p)
    if busy > cfg["busy_pct"]:
        why.append("busy:%.0f%%" % busy)
    if usb_host() and not cfg["suspend_on_usb"]:
        why.append("usb-host")
    act = [w for w in active_wakeup_sources() if w not in cfg["ignore_wakeup_sources"]]
    if act:
        why.append("wakeup:" + ",".join(act[:4]))
    if not why and calls_active():
        why.append("call")
    return why


class Alarm(Exception):
    pass


def _alarm(*_):
    raise Alarm()


def wakeup_count_handshake(cfg):
    """True = kernel will abort the suspend if a wakeup event arrives from now on.
    Skipped (None) while an ignored source (USB) is active: reading would block."""
    act = active_wakeup_sources()
    if any(w in cfg["ignore_wakeup_sources"] for w in act):
        return None
    signal.signal(signal.SIGALRM, _alarm)
    signal.alarm(2)
    try:
        cnt = rd("/sys/power/wakeup_count")
        signal.alarm(0)
    except Alarm:
        return False
    try:
        wr("/sys/power/wakeup_count", cnt)
        return True
    except OSError:
        return False


def wake_reason(since_mark):
    try:
        out = subprocess.run(["dmesg"], capture_output=True, text=True, timeout=5).stdout
    except Exception:  # noqa: BLE001
        return []
    i = out.rfind(since_mark)
    seg = out[i:] if i >= 0 else out[-6000:]
    rs = []
    for line in seg.splitlines():
        for key in ("Resume caused by ", "PM: Pending Wakeup Sources:"):
            if key in line:
                r = line.split(key, 1)[1].strip()
                if key.startswith("Resume") and r not in rs:
                    rs.append(r)
        if "Abort:" in line or "failed to suspend" in line:
            rs.append("ABORT " + line.split("] ", 2)[-1][:120])
    return rs[:6]


def net_counters():
    """rx/tx packets of rmnet0 and tailscale0 (diagnosis: which traffic ends a sleep)."""
    out = {}
    try:
        for line in open("/proc/net/dev").read().splitlines()[2:]:
            n, _, v = line.partition(":")
            n = n.strip()
            if n in ("rmnet0", "tailscale0", "wlan0"):
                f = v.split()
                out[n] = (int(f[1]), int(f[9]))
    except (OSError, ValueError, IndexError):
        pass
    return out


def net_only_wake(reasons):
    """True when every wake reason is a modem/Wi-Fi/mailbox IRQ (an incoming packet, not RTC/power key)."""
    rs = [x for x in reasons if not x.startswith("ABORT")]
    return bool(rs) and all(("cp_mbox" in x or "MAILBOX_CP2AP" in x or "wlan_pci_wake" in x) for x in rs)


def suspend_once(seconds, state="mem"):
    mark = "s22-autosleep-%d-%d" % (os.getpid(), int(time.time() * 1000))
    try:
        wr("/dev/kmsg", mark + "\n")
    except OSError:
        pass
    soc0 = rd(B + "batt_read_raw_soc")
    n0 = net_counters()
    wr(RTC, "0")
    wr(RTC, "+%d" % int(seconds))
    w0, m0 = time.time(), time.monotonic()
    err = None
    try:
        wr("/sys/power/state", state)
    except OSError as e:
        err = "%s" % e
    w1, m1 = time.time(), time.monotonic()
    try:
        wr(RTC, "0")
    except OSError:
        pass
    slept = max(0.0, (w1 - w0) - (m1 - m0))
    n1 = net_counters()
    nd = {k: [n1[k][0] - n0[k][0], n1[k][1] - n0[k][1]] for k in n1 if k in n0}
    return {"net_pk": nd, "slept_s": round(slept, 1), "wall_s": round(w1 - w0, 1), "err": err,
            "why_wake": wake_reason(mark), "soc0": soc0, "soc1": rd(B + "batt_read_raw_soc")}


def write_status(d):
    try:
        os.makedirs(os.path.dirname(STATUS), exist_ok=True)
        tmp = STATUS + ".tmp"
        wr(tmp, json.dumps(d))
        os.replace(tmp, STATUS)
    except OSError:
        pass


def run():
    os.makedirs(INHIBIT_DIR, exist_ok=True)
    import fcntl
    lockf = open("/run/s22-autosleep/lock", "w")
    try:
        fcntl.flock(lockf, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("s22-autosleep: another daemon holds /run/s22-autosleep/lock", file=sys.stderr)
        return 1
    def _term(*_):
        set_governor(load_cfg()["unlocked_governor"])
        sys.exit(0)
    signal.signal(signal.SIGTERM, _term)
    cfg = load_cfg()
    log(event="start", pid=os.getpid(), cfg=cfg)
    off_since = None
    awake_until = 0.0
    fail_backoff = 5.0
    hist = []  # (busy, total) samples for the busy window
    tot = {"cycles": 0, "slept_s": 0.0, "aborts": 0, "since": time.time()}
    last_cfg = time.monotonic()
    gov_state = None
    last_why = None   # logged when the inhibitor set changes (diagnosis of 0-cycle nights)
    while True:
        if time.monotonic() - last_cfg > 30:
            cfg, last_cfg = load_cfg(), time.monotonic()
        now = time.monotonic()
        hist.append((now, cpu_times()))
        hist = [h for h in hist if now - h[0] <= cfg["busy_window_s"] + 0.5]
        (_, (b0, t0)), (_, (b1, t1)) = hist[0], hist[-1]
        busy = 100.0 * (b1 - b0) / max(1, t1 - t0)
        disp = rd(DISPLAY, "on")
        if disp != "off":
            off_since = None
            last_why = None
            if cfg["governors"] and gov_state != "unlocked":
                set_governor(cfg["unlocked_governor"]); gov_state = "unlocked"
            write_status({"state": "screen-on", "t": time.time(), **tot})
            time.sleep(cfg["poll_s"]); continue
        if off_since is None:
            off_since = now
        if os.path.exists(FLAG):
            if gov_state == "locked":
                set_governor(cfg["unlocked_governor"]); gov_state = "unlocked"
        elif cfg["governors"] and gov_state != "locked":
            set_governor(cfg["locked_governor"]); gov_state = "locked"
        if now - off_since < cfg["lock_grace_s"] or now < awake_until:
            # screen off but still in the grace / post-wake window: say so, so a
            # stale "screen-on" status never lingers (2026-10-22)
            write_status({"state": "locked-grace" if now - off_since < cfg["lock_grace_s"] else "post-wake",
                          "t": time.time(), **tot})
            time.sleep(cfg["poll_s"]); continue
        why = inhibitors(cfg, busy)
        if why != last_why:
            log(event="inhibit", why=why or [])
            last_why = why
        if why:
            write_status({"state": "awake", "inhibit": why, "t": time.time(), **tot})
            time.sleep(cfg["poll_s"]); continue
        hs = wakeup_count_handshake(cfg)
        if hs is False:
            awake_until = time.monotonic() + 2
            continue
        r = suspend_once(cfg["max_sleep_s"])
        r.update(event="cycle", handshake=hs, busy=round(busy, 1))
        tot["cycles"] += 1
        tot["slept_s"] = round(tot["slept_s"] + r["slept_s"], 1)
        if r["err"]:
            tot["aborts"] += 1
            awake_until = time.monotonic() + fail_backoff
            fail_backoff = min(cfg["max_fail_backoff_s"], fail_backoff * 2)
        else:
            fail_backoff = 5.0
            awake_until = time.monotonic() + (cfg["net_awake_s"] if net_only_wake(r["why_wake"]) else cfg["awake_s"])
        log(**r)
        hist = []
        write_status({"state": "resumed", "last": r, "t": time.time(), **tot})


def status():
    print("autosleep:", "DISABLED (s22-autosleep on to enable)" if os.path.exists(FLAG) else "enabled")
    print(rd(STATUS, "{}"))
    print("suspend_stats: success=%s fail=%s" % (rd("/sys/power/suspend_stats/success"), rd("/sys/power/suspend_stats/fail")))
    try:
        lines = open(LOG).read().splitlines()[-8:]
        for l in lines:
            print(l)
    except OSError:
        pass


def main(argv):
    cmd = argv[1] if len(argv) > 1 else "status"
    if cmd == "run":
        return run() or 0
    elif cmd == "off":
        os.makedirs(STATE_DIR, exist_ok=True)
        wr(FLAG, time.strftime("%F %T\n"))
        set_governor(CONFIG["unlocked_governor"])
        log(event="disabled")
        print("autosleep disabled (persistent). Re-enable: s22-autosleep on")
    elif cmd == "on":
        try:
            os.unlink(FLAG)
        except FileNotFoundError:
            pass
        log(event="enabled")
        print("autosleep enabled")
    elif cmd == "once":
        print(json.dumps(suspend_once(int(argv[2]) if len(argv) > 2 else 20)))
    else:
        status()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
