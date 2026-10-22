#!/usr/bin/env python3
"""Who wakes up: per-thread context switches and CPU time over a window. Read-only.
usage: s22-wakers.py SECONDS [TOPN]"""
import os, sys, time

def snap():
    d = {}
    for p in os.listdir("/proc"):
        if not p.isdigit():
            continue
        try:
            for t in os.listdir("/proc/%s/task" % p):
                base = "/proc/%s/task/%s/" % (p, t)
                try:
                    st = open(base + "stat").read()
                    comm = st[st.index("(") + 1:st.rindex(")")]
                    f = st[st.rindex(")") + 2:].split()
                    cpu = int(f[11]) + int(f[12])
                    cs = 0
                    for line in open(base + "status"):
                        if line.startswith(("voluntary_ctxt_switches", "nonvoluntary_ctxt_switches")):
                            cs += int(line.split()[1])
                    pcomm = open("/proc/%s/comm" % p).read().strip()
                    d[(p, t)] = (pcomm, comm, cs, cpu)
                except (OSError, ValueError):
                    pass
        except OSError:
            pass
    return d

dur = float(sys.argv[1]); top = int(sys.argv[2]) if len(sys.argv) > 2 else 30
a = snap(); time.sleep(dur); b = snap()
rows = []
for k, v in b.items():
    if k in a:
        rows.append((v[2] - a[k][2], v[3] - a[k][3], k[0], k[1], v[0], v[1]))
rows.sort(reverse=True)
tot = sum(r[0] for r in rows)
print("window %.0fs, total switches %d (%.1f/s)" % (dur, tot, tot / dur))
print("%8s %6s %7s %7s  %s" % ("wake/s", "cpu_t", "pid", "tid", "process/thread"))
for r in rows[:top]:
    print("%8.2f %6d %7s %7s  %s/%s" % (r[0] / dur, r[1], r[2], r[3], r[4], r[5]))
