"""Host tests for s22-trace and s22-trace-summary (no phone needed)."""
import gzip
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(file))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


T = load("s22_trace", "s22-trace.py")
S = load("s22_trace_summary", "s22-trace-summary.py")


def check(cond, msg="check failed"):
    if not cond:          # not a bare assert: the host runner also runs python -O
        raise AssertionError(msg)


class ListWriter:
    def __init__(self):
        self.recs = []

    def write(self, rec):
        self.recs.append(rec)


class TraceTests(unittest.TestCase):
    def test_tail_reads_only_new_lines_and_follows_rotation(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "x.jsonl")
            Path(p).write_text('{"event":"old"}\n')
            t = T.Tail(p)
            check(t.lines() == [])
            with open(p, "a") as f:
                f.write('{"event":"a"}\n{"event":"b"}\n{"event":"partial"')
            check([x["event"] for x in t.lines()] == ["a", "b"])
            os.replace(p, p + ".1")
            Path(p).write_text('{"event":"c"}\n')
            check([x["event"] for x in t.lines()] == ["c"])

    def test_writer_rotates_to_gzip_and_keeps_n(self):
        with tempfile.TemporaryDirectory() as d:
            w = T.Writer(os.path.join(d, "trace.jsonl"), rotate=200, keep=3)
            for i in range(80):
                w.write({"i": i, "pad": "x" * 40})
            gz = sorted(Path(d).glob("trace.jsonl.*.gz"))
            check(1 <= len(gz) <= 3, gz)
            with gzip.open(gz[-1], "rt") as f:
                check(json.loads(f.readline())["pad"] == "x" * 40)

    def test_interesting_filters(self):
        check(T.interesting("netpolicy", {"event": "uplink"}))
        check(T.interesting("netpolicy", {"event": "wifi_saver_park"}))
        check(not T.interesting("netpolicy", {"event": "probe"}))
        check(not T.interesting("autosleep", {"event": "cycle"}))
        check(T.interesting("keepalive", {"event": "restart"}))
        check(T.interesting("turnguard", {"event": "timeout_cancel"}))
        check(T.interesting("llama", {"event": "request_abort"}))

    def test_sample_folds_autosleep_cycles_and_detects_restarts(self):
        tr = T.Tracer(writer=ListWriter(), clock=lambda: 1000.0)
        tr.tails = {}
        tr.fold_autosleep({"event": "cycle", "slept_s": 3.0, "why_wake": ["170 wlan_pci_wake_irq"], "err": None})
        tr.fold_autosleep({"event": "cycle", "slept_s": 2.0, "why_wake": [], "err": "EBUSY"})
        tr.pids = {"netpolicy": 1, "openunum": 2}
        orig = T.daemon_pids
        T.daemon_pids = lambda: {"netpolicy": 5, "openunum": 2}
        try:
            tr.sample()
        finally:
            T.daemon_pids = orig
        kinds = [(r["k"], r.get("src")) for r in tr.w.recs]
        check(("event", "pids") in kinds, kinds)
        s = [r for r in tr.w.recs if r["k"] == "sample"][0]
        check(s["cyc"]["n"] == 2 and s["cyc"]["slept"] == 5.0 and s["cyc"]["aborts"] == 1, s["cyc"])
        check(s["cyc"]["wake"] == {"wlan_pci_wake_irq": 1}, s["cyc"])
        pe = [r for r in tr.w.recs if r.get("src") == "pids"][0]
        check(pe["restarted"] == ["netpolicy"], pe)
        check(tr.cycles_since["n"] == 0)


def sample(t, raw, disp="off", uplink="mobile", st="Discharging", slept=0.0):
    return {"k": "sample", "t": t, "bat": {"raw": raw, "st": st, "cap": raw // 100}, "disp": disp,
            "net": {"uplink": uplink}, "cyc": {"n": 1, "slept": slept, "aborts": 0, "wake": {"x": 1}},
            "therm": {"BIG": 40.0}, "mem": {"avail_mb": 900}, "busy": 1.0}


class SummaryTests(unittest.TestCase):
    def test_drain_by_state_and_mixed_intervals(self):
        recs = [sample(0 + 60 * i, 9000 - 2 * i, slept=30) for i in range(31)]          # 30 min off/mobile
        base = recs[-1]["t"]
        recs.append({"k": "event", "t": base + 30, "src": "display", "state": "on"})
        recs += [sample(base + 60 * (i + 1), 8940 - 10 * (i + 1), disp="on") for i in range(20)]
        recs.append({"k": "event", "t": base + 100, "src": "keepalive", "rec": {"event": "restart", "service": "openunum"}})
        recs.append({"k": "event", "t": base + 110, "src": "turnguard", "rec": {"event": "timeout_cancel"}})
        o = S.summarise(recs)
        d = o["drain_by_state"]
        check(abs(d["off/mobile"]["pct_per_h"] - 1.2) < 0.01, d)         # 0.02 %/min
        check(d["off/mobile"]["asleep_pct"] == 50.0, d)
        check("mixed" in d and d["mixed"]["intervals"] == 1, d)
        check(abs(d["on/mobile"]["pct_per_h"] - 6.0) < 0.01, d)
        check(o["restarts"] == {"openunum": 1} and o["turns"] == {"timeout_cancel": 1}, o)
        check("off/mobile" in S.text(o))

    def test_charging_is_not_drain(self):
        recs = [sample(60 * i, 5000 + i, st="Charging") for i in range(20)]
        d = S.summarise(recs)["drain_by_state"]
        check(list(d) == ["charging/other"] and "pct_per_h" not in d["charging/other"], d)

    def test_load_reads_plain_and_gz(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, "trace.jsonl").write_text(json.dumps(sample(120, 10)) + "\n")
            with gzip.open(os.path.join(d, "trace.jsonl.1.gz"), "wt") as f:
                f.write(json.dumps(sample(60, 11)) + "\n")
            r = S.load([d])
            check([x["t"] for x in r] == [60, 120], r)


if __name__ == "__main__":
    unittest.main()
