#!/usr/bin/env python3
"""Summarise a s22-power-sample CSV: mean/median current, charge_counter slope, %/h."""
import csv, statistics, sys
for path in sys.argv[1:]:
    rows = list(csv.DictReader(open(path)))
    if len(rows) < 2:
        print(path, "too few rows"); continue
    cur = [int(r["cur_mA"]) for r in rows]
    t = [float(r["t"]) for r in rows]
    cc = [int(r["cc_uAh"]) for r in rows]
    hrs = (t[-1] - t[0]) / 3600
    mean = statistics.mean(cur)
    slope = (cc[-1] - cc[0]) / 1000 / hrs if hrs > 0 else 0
    full = 3327.0  # charge_full mAh on this battery
    soc = ""
    if "raw_soc" in rows[0] and hrs > 0:
        ds = (int(rows[-1]["raw_soc"]) - int(rows[0]["raw_soc"])) / 100.0
        soc = " | raw_soc %+.2f%% = %.0f mA avg (%.2f %%/h)" % (ds, ds / 100 * full / hrs, ds / hrs)
    busy = statistics.mean(float(r["busy_pct"]) for r in rows)
    irq = statistics.mean(float(r["irq_s"]) for r in rows)
    print("%s: n=%d %.1f min  current mean %.0f mA median %.0f  | cc_raw slope %.0f (units unclear, not mA)  | %.2f %%/h (from current) | busy %.1f%% irq/s %.0f | cap %s->%s" % (
        path, len(rows), hrs * 60, mean, statistics.median(cur), slope, -mean / full * 100, busy, irq, rows[0]["cap"], rows[-1]["cap"]) + soc)
