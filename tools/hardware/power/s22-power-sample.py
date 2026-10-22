#!/usr/bin/env python3
"""Sample battery drain and what is awake. Read-only.

usage: s22-power-sample.py OUT.csv SECONDS [INTERVAL]
Columns: t, current_now mA (Samsung sign: + charging, - discharging),
current_avg mA, voltage mV, charge_counter uAh, capacity %, status, slate,
display, load1, cpu busy % since last sample, irq/s, ctxt/s, per-policy cur MHz,
cpuidle deepest-state residency share per CPU group.
"""
import os, sys, time

B = "/sys/class/power_supply/battery/"

def rd(p, d=""):
    try:
        with open(p) as f:
            return f.read().strip()
    except OSError:
        return d

def stat():
    cpu = irq = ctxt = 0
    busy = tot = 0
    for line in open("/proc/stat"):
        if line.startswith("cpu "):
            v = [int(x) for x in line.split()[1:]]
            tot = sum(v[:8]); busy = tot - v[3] - v[4]
        elif line.startswith("intr "):
            irq = int(line.split()[1])
        elif line.startswith("ctxt "):
            ctxt = int(line.split()[1])
    return busy, tot, irq, ctxt

def idle_res():
    out = []
    for c in range(8):
        d = "/sys/devices/system/cpu/cpu%d/cpuidle" % c
        r = []
        i = 0
        while os.path.isdir("%s/state%d" % (d, i)):
            r.append(int(rd("%s/state%d/time" % (d, i), "0")))
            i += 1
        out.append(r)
    return out

def main():
    out, dur = sys.argv[1], float(sys.argv[2])
    iv = float(sys.argv[3]) if len(sys.argv) > 3 else 5.0
    pols = sorted(p for p in os.listdir("/sys/devices/system/cpu/cpufreq") if p.startswith("policy"))
    f = open(out, "a")
    if f.tell() == 0:
        f.write("t,cur_mA,avg_mA,mV,cc_uAh,raw_soc,cap,status,slate,display,load1,busy_pct,irq_s,ctxt_s,"
                + ",".join(p + "_MHz" for p in pols) + ",deep_pct\n")
    t0 = time.time()
    pb, pt, pi, pc = stat(); pr = idle_res(); ptime = time.time()
    while time.time() - t0 < dur:
        time.sleep(iv)
        b, t, i, c = stat(); r = idle_res(); now = time.time(); dt = now - ptime
        busy = 100.0 * (b - pb) / max(1, t - pt)
        deep = []
        for cpu in range(8):
            if r[cpu] and pr[cpu]:
                deep.append((r[cpu][-1] - pr[cpu][-1]) / (dt * 1e6))
        deep_pct = 100.0 * sum(deep) / max(1, len(deep))
        row = [
            "%.1f" % now,
            str(int(rd(B + "current_now", "0"))), str(int(rd(B + "current_avg", "0"))),
            str(int(rd(B + "voltage_now", "0")) // 1000), rd(B + "charge_counter"), rd(B + "batt_read_raw_soc"), rd(B + "capacity"),
            rd(B + "status"), rd(B + "batt_slate_mode"), rd("/run/s22-display-state", "?"),
            rd("/proc/loadavg").split()[0], "%.1f" % busy, "%.0f" % ((i - pi) / dt), "%.0f" % ((c - pc) / dt),
        ] + [str(int(rd("/sys/devices/system/cpu/cpufreq/%s/scaling_cur_freq" % p, "0")) // 1000) for p in pols] + ["%.1f" % deep_pct]
        f.write(",".join(row) + "\n"); f.flush()
        pb, pt, pi, pc, pr, ptime = b, t, i, c, r, now

if __name__ == "__main__":
    main()
