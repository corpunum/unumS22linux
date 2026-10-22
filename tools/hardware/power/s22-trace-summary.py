#!/usr/bin/env python3
"""Summarise the phone's s22-trace JSONL (rig side).

    s22-trace-summary.py --pull [--host root@100.95.191.103] [--dir DIR]   fetch, then summarise
    s22-trace-summary.py FILE_OR_DIR...                                  summarise local copies
    options: --since ISO|EPOCH  --until ISO|EPOCH  --json

Drain is computed from the fuel gauge's raw SOC (0.01 % steps) between consecutive samples
while the battery is discharging, and attributed to the state the phone was in for the whole
interval: display on/off x uplink. An interval with a display or uplink change inside it goes
to "mixed". %/h needs >= 10 min in a bucket to be shown (shorter buckets are too noisy).
mA = %/h x charge_full (3327 mAh) / 100.
"""
import argparse, glob, gzip, json, os, subprocess, sys, time
from collections import Counter, defaultdict

CHARGE_FULL_MAH = 3327
HOST = "root@100.95.191.103"
REMOTE = "/srv/s22/state/trace"


def load(paths):
    recs = []
    files = []
    for p in paths:
        files += sorted(glob.glob(os.path.join(p, "trace.jsonl*"))) if os.path.isdir(p) else [p]
    for f in files:
        op = gzip.open if f.endswith(".gz") else open
        try:
            with op(f, "rt") as fh:
                for line in fh:
                    try:
                        recs.append(json.loads(line))
                    except ValueError:
                        pass
        except OSError:
            pass
    recs.sort(key=lambda r: r.get("t", 0))
    return recs


def pull(host, dest):
    os.makedirs(dest, exist_ok=True)
    tar = subprocess.run(["ssh", "-o", "ConnectTimeout=20", host, "cd %s && tar cf - trace.jsonl*" % REMOTE],
                         capture_output=True, timeout=180)
    if tar.returncode != 0:
        sys.exit("pull failed: %s" % tar.stderr.decode()[-300:])
    subprocess.run(["tar", "xf", "-", "-C", dest], input=tar.stdout, check=True)
    return dest


def ts(x):
    if x is None:
        return None
    try:
        return float(x)
    except ValueError:
        import datetime
        return datetime.datetime.fromisoformat(x).timestamp()   # naive = local time


def state_of(s):
    return "%s/%s" % ("off" if s.get("disp") == "off" else "on", (s.get("net") or {}).get("uplink") or "none")


def summarise(recs, since=None, until=None):
    recs = [r for r in recs if (since is None or r.get("t", 0) >= since) and (until is None or r.get("t", 0) <= until)]
    samples = [r for r in recs if r.get("k") == "sample"]
    events = [r for r in recs if r.get("k") == "event"]
    out = {"records": len(recs), "samples": len(samples), "events": len(events)}
    if not samples:
        return out
    out["from"] = time.strftime("%F %T UTC", time.gmtime(samples[0]["t"]))
    out["to"] = time.strftime("%F %T UTC", time.gmtime(samples[-1]["t"]))
    out["span_h"] = round((samples[-1]["t"] - samples[0]["t"]) / 3600, 2)
    change_t = sorted(e["t"] for e in events if e.get("src") in ("display", "uplink"))
    buckets = defaultdict(lambda: {"s": 0.0, "dsoc": 0.0, "slept": 0.0, "n": 0})
    gaps = []
    for a, b in zip(samples, samples[1:]):
        dt = b["t"] - a["t"]
        if dt <= 0:
            continue
        if dt > 1800:
            gaps.append([time.strftime("%F %T UTC", time.gmtime(a["t"])), round(dt / 60)])
        ba, bb = a.get("bat") or {}, b.get("bat") or {}
        if ba.get("st") != "Discharging" or bb.get("st") != "Discharging" or ba.get("slate") == "1":
            key = "charging/other"
        elif any(a["t"] < t < b["t"] for t in change_t) or state_of(a) != state_of(b):
            key = "mixed"
        else:
            key = state_of(a)
        k = buckets[key]
        k["s"] += dt
        k["n"] += 1
        k["slept"] += float((b.get("cyc") or {}).get("slept") or 0)
        if ba.get("raw") is not None and bb.get("raw") is not None and key != "charging/other":
            k["dsoc"] += (ba["raw"] - bb["raw"]) / 100.0
    drain = {}
    for key, k in sorted(buckets.items()):
        h = k["s"] / 3600
        row = {"hours": round(h, 2), "intervals": k["n"], "soc_drop_pct": round(k["dsoc"], 2),
               "asleep_pct": round(100 * k["slept"] / k["s"], 1) if k["s"] else None}
        if key != "charging/other" and k["s"] >= 600:
            row["pct_per_h"] = round(k["dsoc"] / h, 2)
            row["mA"] = round(k["dsoc"] / h * CHARGE_FULL_MAH / 100)
        drain[key] = row
    out["drain_by_state"] = drain
    out["gaps_over_30min"] = gaps
    b0, b1 = samples[0].get("bat") or {}, samples[-1].get("bat") or {}
    out["battery"] = {"start_pct": b0.get("cap"), "end_pct": b1.get("cap"), "start_raw": b0.get("raw"),
                      "end_raw": b1.get("raw")}
    temps = [max((s.get("therm") or {}).values() or [0]) for s in samples]
    mems = [(s.get("mem") or {}).get("avail_mb") for s in samples if (s.get("mem") or {}).get("avail_mb")]
    busy = [s.get("busy") or 0 for s in samples]
    out["thermal_max_c"] = max(temps) if temps else None
    out["mem_avail_min_mb"] = min(mems) if mems else None
    out["cpu_busy_mean_pct"] = round(sum(busy) / len(busy), 1) if busy else None
    wake = Counter()
    for s in samples:
        wake.update((s.get("cyc") or {}).get("wake") or {})
    out["suspend"] = {"cycles": sum((s.get("cyc") or {}).get("n", 0) for s in samples),
                      "aborts": sum((s.get("cyc") or {}).get("aborts", 0) for s in samples),
                      "slept_h": round(sum((s.get("cyc") or {}).get("slept", 0) for s in samples) / 3600, 2),
                      "wake_top": wake.most_common(6)}
    ev = Counter()
    restarts = Counter()
    turns = Counter()
    errors = Counter()
    for e in events:
        src, rec = e.get("src"), e.get("rec") or {}
        name = rec.get("event")
        if src == "uplink":
            ev["uplink_changes"] += 1
        elif src == "netpolicy" and name in ("mobile_up", "mobile_down", "mobile_redial"):
            ev[name] += 1
        elif src == "netpolicy" and str(name).startswith("wifi_saver"):
            ev[name] += 1
        elif src == "keepalive" and name == "restart":
            restarts[rec.get("service")] += 1
        elif src == "pids":
            for n in e.get("restarted") or []:
                restarts["pid:" + n] += 1
        elif src == "turnguard":
            turns[name] += 1
        elif src == "llama" and name in ("request_abort", "retry_budget", "orphan_killed", "stuck_backend",
                                         "load", "unload"):
            turns["llama_" + name] += 1
        elif src == "trace" and e.get("what") == "start":
            ev["trace_starts"] += 1
        if "error" in str(name) or e.get("what") == "error" or name in ("load_failed", "restart_limited"):
            errors["%s:%s" % (src, name or e.get("what"))] += 1
    boots = []
    for e in events:
        if e.get("src") == "trace" and e.get("what") == "start" and e.get("boot_id") not in [x[1] for x in boots]:
            boots.append((time.strftime("%F %T UTC", time.gmtime(e["t"])), e.get("boot_id")))
    out["boots_seen"] = len(boots)
    out["boot_starts"] = [b[0] for b in boots]
    out["network"] = dict(ev)
    out["restarts"] = dict(restarts)
    out["turns"] = dict(turns)
    out["errors"] = dict(errors)
    return out


def text(o):
    lines = ["trace %s .. %s  (%s h, %s samples, %s events)" % (o.get("from"), o.get("to"), o.get("span_h"),
                                                               o.get("samples"), o.get("events"))]
    bt = o.get("battery") or {}
    lines.append("battery %s%% -> %s%%" % (bt.get("start_pct"), bt.get("end_pct")))
    lines.append("drain by state (display/uplink):")
    for k, v in (o.get("drain_by_state") or {}).items():
        lines.append("  %-16s %6.2f h  drop %5.2f %%  %s  asleep %s%%" % (
            k, v["hours"], v["soc_drop_pct"],
            ("%5.2f %%/h (%d mA)" % (v["pct_per_h"], v["mA"])) if "pct_per_h" in v else "   (too short)   ",
            v.get("asleep_pct")))
    s = o.get("suspend") or {}
    lines.append("suspend: %s cycles, %s aborts, %s h asleep; top wakes %s" % (
        s.get("cycles"), s.get("aborts"), s.get("slept_h"), s.get("wake_top")))
    lines.append("boots seen: %s (trace started at %s)" % (o.get("boots_seen"), o.get("boot_starts")))
    lines.append("network: %s" % o.get("network"))
    lines.append("restarts: %s" % o.get("restarts"))
    lines.append("agent turns: %s" % o.get("turns"))
    lines.append("errors: %s" % o.get("errors"))
    lines.append("max temp %s C, min MemAvailable %s MB, mean CPU busy %s %%" % (
        o.get("thermal_max_c"), o.get("mem_avail_min_mb"), o.get("cpu_busy_mean_pct")))
    if o.get("gaps_over_30min"):
        lines.append("gaps > 30 min between samples (start, minutes): %s" % o["gaps_over_30min"])
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="*")
    ap.add_argument("--pull", action="store_true")
    ap.add_argument("--host", default=HOST)
    ap.add_argument("--dir", default=os.path.expanduser("~/s22-trace"))
    ap.add_argument("--since")
    ap.add_argument("--until")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    paths = list(a.paths)
    if a.pull:
        paths.append(pull(a.host, a.dir))
    if not paths:
        ap.error("give files/dirs or --pull")
    o = summarise(load(paths), ts(a.since), ts(a.until))
    print(json.dumps(o, indent=1) if a.json else text(o))
    return 0


if __name__ == "__main__":
    sys.exit(main())
