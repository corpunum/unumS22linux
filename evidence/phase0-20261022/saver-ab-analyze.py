#!/usr/bin/env python3
"""Per-window numbers for the Wi-Fi saver A/B run (issue #71).

    saver-ab-analyze.py DIR      DIR holds saver-ab.log, autosleep.jsonl and trace.jsonl* (pulled from the phone)

For each window (from saver-ab.log marks): raw-SOC drain over the whole window and over the
locked-only part (samples with the display off; display-on stretches are cut out), time asleep,
suspend cycles and the first wake reason of each cycle (wlan_pci_wake_irq vs modem mailbox ...),
plus whether Wi-Fi was parked. 1 raw step = 0.01 % of charge_full (3327 mAh).
"""
import glob, gzip, json, os, sys
from collections import Counter

CHARGE_FULL_MAH = 3327


def jl(path):
    op = gzip.open if path.endswith(".gz") else open
    out = []
    with op(path, "rt") as f:
        for line in f:
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
    return out


def wake_kind(w):
    if not w:
        return "none"
    for key, name in (("wlan_pci_wake", "wlan"), ("cp_mbox", "modem"), ("MAILBOX_CP2AP", "modem"),
                      ("rmnet", "modem"), ("umts_ipc", "modem"), ("vGPIO2PMU", "rtc/pmic"), ("cnss", "wlan"),
                      ("detected", "abort"), ("ABORT", "abort")):
        if key in w:
            return name
    return w.split()[-1][:30]


def main(d):
    marks = []
    for line in open(os.path.join(d, "saver-ab.log")):
        f = line.split()
        marks.append((float(f[0]), " ".join(f[2:])))
    wins = []
    for i, (t, m) in enumerate(marks):
        if m.startswith("window") and "begin" in m:
            end = next((t2 for t2, m2 in marks[i + 1:] if m2.startswith("window") and "end" in m2), None)
            if end:
                wins.append((m.split()[1], t, end))
    samples, events = [], []
    for p in sorted(glob.glob(os.path.join(d, "trace.jsonl*"))):
        for r in jl(p):
            (samples if r.get("k") == "sample" else events).append(r)
    samples.sort(key=lambda r: r["t"])
    disp_ev = sorted((e["t"], e.get("state")) for e in events if e.get("src") == "display")
    cycles = [r for r in jl(os.path.join(d, "autosleep.jsonl")) if r.get("event") == "cycle"]
    rows = []
    for mode, t0, t1 in wins:
        ss = [s for s in samples if t0 <= s["t"] <= t1]
        cy = [c for c in cycles if t0 <= c["t"] <= t1]
        on_s = 0.0
        locked_drop = 0.0
        locked_s = 0.0
        for a, b in zip(ss, ss[1:]):
            dt = b["t"] - a["t"]
            on_inside = a.get("disp") != "off" or b.get("disp") != "off" or any(
                a["t"] < t < b["t"] for t, _ in disp_ev)
            if on_inside:
                on_s += dt
                continue
            locked_s += dt
            locked_drop += (a["bat"]["raw"] - b["bat"]["raw"]) / 100.0
        wall = t1 - t0
        slept = sum(c.get("slept_s", 0) for c in cy)
        kinds = Counter(wake_kind((c.get("why_wake") or [""])[0]) for c in cy)
        parked = Counter(str((s.get("net") or {}).get("wifi_parked")) for s in ss)
        row = {"mode": mode, "wall_min": round(wall / 60, 1), "samples": len(ss),
               "display_on_min": round(on_s / 60, 1), "locked_min": round(locked_s / 60, 1),
               "locked_drop_pct": round(locked_drop, 2),
               "locked_pct_per_h": round(locked_drop / (locked_s / 3600), 2) if locked_s >= 600 else None,
               "cycles": len(cy), "asleep_pct_of_wall": round(100 * slept / wall, 1),
               "mean_sleep_s": round(slept / len(cy), 1) if cy else None,
               "wakes": dict(kinds.most_common()), "wifi_parked_samples": dict(parked)}
        if row["locked_pct_per_h"] is not None:
            row["locked_mA"] = round(row["locked_pct_per_h"] * CHARGE_FULL_MAH / 100)
        rows.append(row)
        print(json.dumps(row))
    return rows


if __name__ == "__main__":
    main(sys.argv[1])
