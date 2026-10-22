#!/usr/bin/env python3
"""Host tests for the npu-accept harness logic: graph rules, tolerances, enn-run parsing, verdict."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import unittest

LIB = Path(__file__).resolve().parents[2] / "tools/hardware/npu/npu_accept_lib.py"
SPEC = importlib.util.spec_from_file_location("npu_accept_lib", LIB)
assert SPEC is not None and SPEC.loader is not None
L = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = L
SPEC.loader.exec_module(L)


def conv(i, kh, kw, op="CONV_2D", dil=(1, 1)):
    return {"index": i, "op": op, "kernel": [kh, kw], "dilation": list(dil)}


def desc(*ops, dtypes=("INT8", "INT32")):
    return {"subgraphs": 1, "ops": list(ops), "tensor_dtypes": list(dtypes), "dynamic_shapes": False}


def rules(d):
    return [e["rule"] for e in L.validate_graph(d)["errors"]]


class GraphRules(unittest.TestCase):
    def test_wide_1d_kernels_rejected_both_orientations(self) -> None:
        for kh, kw in ((1, 17), (17, 1), (1, 32), (48, 1)):
            self.assertEqual(rules(desc(conv(0, kh, kw))), ["kernel-1d-max-15"], (kh, kw))

    def test_1d_kernel_boundary_15_ok_16_rejected(self) -> None:
        self.assertEqual(rules(desc(conv(0, 1, 15))), [])
        self.assertEqual(rules(desc(conv(0, 1, 16))), ["kernel-1d-max-15"])
        self.assertEqual(rules(desc(conv(0, 1, 7), conv(1, 1, 1))), [])

    def test_small_2d_kernels_ok_large_2d_only_warns(self) -> None:
        self.assertEqual(rules(desc(conv(0, 3, 3), conv(1, 10, 4, "DEPTHWISE_CONV_2D"))), [])
        r = L.validate_graph(desc(conv(0, 17, 17)))
        self.assertEqual(r["errors"], [])
        self.assertEqual([w["rule"] for w in r["warnings"]], ["kernel-2d-large"])

    def test_reason_text_names_op_and_issue(self) -> None:
        e = L.validate_graph(desc(conv(3, 1, 32)))["errors"][0]
        self.assertIn("op 3", e["detail"])
        self.assertIn("32", e["detail"])
        self.assertIn("#64", e["detail"])

    def test_fc_after_avgpool_crash_rule(self) -> None:
        pool = [{"index": 0, "op": "AVERAGE_POOL_2D", "kernel": None}, {"index": 1, "op": "RESHAPE", "kernel": None},
                {"index": 2, "op": "FULLY_CONNECTED", "kernel": None}]
        self.assertEqual(rules(desc(*pool)), ["fc-after-avgpool"])
        self.assertEqual(rules(desc(pool[0], pool[1])), [])          # pool + reshape alone is fine (MicroNet tail)
        self.assertEqual(rules(desc(conv(0, 3, 3), pool[2])), [])

    def test_dtype_and_structure_rules(self) -> None:
        self.assertEqual(rules(desc(conv(0, 3, 3), dtypes=("FLOAT32",))), ["quantized-only"])
        self.assertEqual(rules(desc(conv(0, 3, 3), dtypes=("UINT8", "INT32"))), [])
        d = desc(conv(0, 3, 3)); d["subgraphs"] = 2
        self.assertEqual(rules(d), ["single-subgraph"])
        d = desc(conv(0, 3, 3)); d["dynamic_shapes"] = True
        self.assertEqual(rules(d), ["static-shapes"])

    def test_warnings_leaky_relu_and_histogram(self) -> None:
        r = L.validate_graph(desc(conv(0, 3, 3), {"index": 1, "op": "LEAKY_RELU", "kernel": None}))
        self.assertEqual([w["rule"] for w in r["warnings"]], ["leaky-relu"])
        self.assertEqual(r["op_histogram"], {"CONV_2D": 1, "LEAKY_RELU": 1})


class Numerics(unittest.TestCase):
    def test_diff_stats_int8_signed_wraparound(self) -> None:
        ref = bytes([0x7F, 0x80, 0x00, 0xFF])        # 127, -128, 0, -1
        got = bytes([0x7F, 0x7F, 0x01, 0x00])        # 127, 127, 1, 0
        s = L.diff_stats(ref, got, "INT8")
        self.assertEqual(s["hist"], {0: 1, 1: 2, 255: 1})
        self.assertEqual(s["max_abs"], 255)
        s = L.diff_stats(ref, got, "UINT8")
        self.assertEqual(s["max_abs"], 255)

    def test_size_mismatch_never_ok(self) -> None:
        s = L.diff_stats(b"\x00\x00", b"\x00", "INT8")
        ok, _, why = L.judge_tensor([s], 1)
        self.assertFalse(ok)
        self.assertIn("size", why[0])

    def test_tolerance_default_and_overrides(self) -> None:
        self.assertEqual(L.tolerance_for("o", "INT8"), 1)
        self.assertEqual(L.tolerance_for("o", "UINT8"), 1)
        self.assertEqual(L.tolerance_for("o", "INT8", L.parse_tol(["o=3"])), 3)
        self.assertEqual(L.tolerance_for("p", "INT8", L.parse_tol(["o=3"])), 1)
        self.assertEqual(L.tolerance_for("p", "INT8", L.parse_tol(["0"])), 0)
        self.assertEqual(L.tolerance_for("p", "INT8", L.parse_tol(["*=2", "p=0"])), 0)

    def test_judge_boundary_exactly_at_tolerance(self) -> None:
        ref = bytes(10)
        for tol, got, expect in ((1, bytes([1] * 10), True), (1, bytes([2] + [0] * 9), False), (0, bytes([1] + [0] * 9), False),
                                 (2, bytes([2] + [0] * 9), True)):
            ok, summ, _ = L.judge_tensor([L.diff_stats(ref, got, "INT8")], tol)
            self.assertEqual(ok, expect, (tol, list(got)))
            self.assertEqual(summ["tol"], tol)

    def test_judge_merges_histograms_across_inputs(self) -> None:
        a = L.diff_stats(bytes(4), bytes([0, 1, 0, 0]))
        b = L.diff_stats(bytes(4), bytes([1, 1, 0, 0]))
        ok, summ, _ = L.judge_tensor([a, b], 1)
        self.assertTrue(ok)
        self.assertEqual(summ["hist"], {0: 5, 1: 3})
        self.assertEqual(summ["n"], 8)
        self.assertEqual(summ["exact"], 5)

    def test_constant_output_detector(self) -> None:
        self.assertTrue(L.constant_output(bytes(8)))
        self.assertFalse(L.constant_output(bytes([0, 1])))
        self.assertFalse(L.constant_output(b""))


ENN_LOG = """stage=initialize rc=0 t=0.048
stage=open_model rc=0 t=0.128
stage=allocate rc=0 inputs=2 outputs=2 t=0.128
buffer[0] in0 size=12 nwhc=1x1x1x12 bytes=12 type=9 label=a
buffer[1] in1 size=490 nwhc=1x1x49x10 bytes=490 type=9 label=IFM
buffer[2] out0 size=1000 nwhc=1x1x1x1000 bytes=1000 type=9 label=x
buffer[3] out1 size=12 nwhc=1x12x1x1 bytes=12 type=9 label=y
stage=execute n=200 first_ms=1.42 mean_ms=0.508 p50_ms=0.58 p99_ms=1.42 min_ms=0.29 max_ms=1.9 per_s=1967.4
output[0] checksum=81a2bde73ce3dded nonzero_bytes=16384
output[1] checksum=0000000000000001 nonzero_bytes=3
stage=release rc=0
stage=close rc=0
stage=deinit rc=0
"""


class EnnRun(unittest.TestCase):
    def test_parse(self) -> None:
        r = L.parse_enn_run(ENN_LOG)
        self.assertEqual(len(r["buffers"]), 4)
        self.assertEqual(r["execute"]["p50_ms"], 0.58)
        self.assertEqual(r["execute"]["n"], 200)
        self.assertEqual(r["checksums"][0]["checksum"], "81a2bde73ce3dded")
        self.assertEqual(r["errors"], [])
        self.assertEqual(L.parse_enn_run("stage=execute iter=3 rc=-1\nstage=open_model rc=5 t=1")["errors"],
                         ["stage execute rc=-1", "stage open_model rc=5"])

    def test_buffer_mapping_extra_cpu_glue_io(self) -> None:
        bufs = L.parse_enn_run(ENN_LOG)["buffers"]
        im, oc, problems, notes = L.map_buffers(bufs, [490], [12])
        self.assertEqual((im, oc, problems), ([1], [[1]], []))
        self.assertEqual(notes, [])
        im, oc, problems, notes = L.map_buffers(bufs, [12], [1000])
        self.assertEqual((im, oc), ([0], [[0]]))
        self.assertEqual(L.pick_output([2, 3], {2: 50, 3: 4}), 3)
        self.assertEqual(L.pick_output([2, 3], {2: 4, 3: 4}), 3)
        im, oc, problems, notes = L.map_buffers(bufs, [490], [999])
        self.assertEqual(oc, [[]])
        self.assertIn("no output buffer of 999", problems[0])
        two = bufs + [{"dir": "out", "index": 2, "size": 12}]
        self.assertEqual(L.map_buffers(two, [490], [12])[1], [[1, 2]])
        self.assertIn("best match", L.map_buffers(two, [490], [12])[3][0])

    def test_latency_summary(self) -> None:
        a = {"n": 100, "first_ms": 2.0, "mean_ms": 1.0, "p50_ms": 0.9, "p99_ms": 3.0, "min_ms": 0.5, "max_ms": 4.0}
        b = {"n": 100, "first_ms": 1.0, "mean_ms": 2.0, "p50_ms": 1.1, "p99_ms": 2.0, "min_ms": 0.4, "max_ms": 5.0}
        s = L.latency_summary([a, b, None])
        self.assertEqual((s["iterations"], s["p99_ms"], s["max_ms"], s["min_ms"]), (200, 3.0, 5.0, 0.4))
        self.assertAlmostEqual(s["p50_ms"], 1.0)
        self.assertAlmostEqual(s["mean_ms"], 1.5)
        self.assertIsNone(L.latency_summary([None]))

    def test_dmesg_alarms_only_new_and_real(self) -> None:
        old = "[1.0] page fault at 0xdead\n[2.0] noise"
        new = old + "\n[3.0] SysMMU alloc num : 10711, lv2_alloc : 100, fault : 0\n[4.0] sysmmu: fault : 2\n" \
                    "[5.0] npu: command timeout\n[6.0] s3c2410-wdt keepalive!\n[7.0] Unable to handle kernel NULL pointer"
        got = L.new_dmesg_alarms(old, new)
        self.assertEqual(len(got), 3)
        self.assertTrue(all("fault : 0" not in g and "keepalive" not in g for g in got))
        self.assertEqual(L.new_dmesg_alarms(new, new), [])
        touch = "[9.0] stm_ts_spi spi2.0: [sec_input] fail_cnt:0 Cal_flag:1"      # 'input' contains 'npu'
        self.assertEqual(L.new_dmesg_alarms("", touch), [])
        self.assertEqual(len(L.new_dmesg_alarms("", "[9.0] npu: exec failed")), 1)


class NncAndVerdict(unittest.TestCase):
    def test_dsp_binary_scan(self) -> None:
        self.assertTrue(L.nnc_sections(b"xxNPU_BINARYyyDSP_BINARYzz")["DSP_BINARY"])
        s = L.nnc_sections(b"..NPU_BINARY..")
        self.assertEqual((s["DSP_BINARY"], s["NPU_BINARY"]), (False, True))

    def good(self):
        t = {"ok": True, "reasons": [], "max_abs": 1, "hist": {0: 9, 1: 1}, "n": 10, "tol": 1, "exact": 9}
        return {"model": {"name": "m", "sha256": "0" * 64}, "graph_rules": "on",
                "graph": {"errors": [], "warnings": [], "op_histogram": {"CONV_2D": 1}},
                "compile": {"ok": True, "partitions": 1, "sections": {}, "seconds": 1.0, "nnc_bytes": 5},
                "numeric": {"n_inputs": 3, "n_random": 2, "n_edge": 1, "n_real": 0, "tensors": {"o": t}, "problems": [], "argmax_agree": None},
                "determinism": {"ok": True, "detail": "same"},
                "latency": {"max_ms": 2.0, "p50_ms": 0.3, "p99_ms": 1.0, "mean_ms": 0.4, "iterations": 5, "first_ms_median": 1.0},
                "memory": {"dmesg_alarms": [], "max_rss_kb": 1, "memavail_before_kb": 1, "memavail_after_kb": 1},
                "recovery": {"ok": True, "detail": "same"}}

    def test_accept_and_each_failure_flips_it(self) -> None:
        v = L.build_verdict(self.good())
        self.assertTrue(v["accepted"])
        self.assertEqual(v["reasons"], [])
        cases = {
            "graph rule": lambda v: v["graph"]["errors"].append({"rule": "kernel-1d-max-15", "detail": "x"}),
            "compile failed": lambda v: v["compile"].update(ok=False, error="boom"),
            "DSP_BINARY": lambda v: v["compile"].update(dsp_binary=True),
            "partitions": lambda v: v["compile"].update(partitions=2),
            "differ by more": lambda v: v["numeric"]["tensors"]["o"].update(ok=False, reasons=["3 of 10 values differ by more than 1"]),
            "non-deterministic": lambda v: v["determinism"].update(ok=False, detail="d"),
            "stall": lambda v: v["latency"].update(max_ms=4300.0),
            "kernel log": lambda v: v["memory"].update(dmesg_alarms=["page fault"]),
            "not usable": lambda v: v["recovery"].update(ok=False, detail="canary"),
            "no inputs": lambda v: v["numeric"].update(n_inputs=0),
            "numeric comparison did not run": lambda v: v.pop("numeric"),
        }
        for key, mutate in cases.items():
            x = self.good(); mutate(x); L.build_verdict(x)
            self.assertFalse(x["accepted"], key)
            self.assertTrue(any(key in r for r in x["reasons"]), (key, x["reasons"]))

    def test_graph_rules_off_ignores_graph_errors_but_numeric_still_decides(self) -> None:
        x = self.good()
        x["graph_rules"] = "off"
        x["graph"]["errors"].append({"rule": "kernel-1d-max-15", "detail": "x"})
        L.build_verdict(x)
        self.assertTrue(x["accepted"])
        self.assertEqual(len(x["ignored_graph_errors"]), 1)
        x = self.good()
        x["graph_rules"] = "off"
        x["graph"]["errors"].append({"rule": "kernel-1d-max-15", "detail": "x"})
        x["numeric"]["tensors"]["o"].update(ok=False, reasons=["99 of 10 values differ by more than 1 (max |delta| 255)"])
        L.build_verdict(x)
        self.assertFalse(x["accepted"])

    def test_graph_rejected_before_compile(self) -> None:
        x = {"model": {"name": "m", "sha256": "0" * 64}, "graph_rules": "on", "compile": None,
             "graph": {"errors": [{"rule": "kernel-1d-max-15", "detail": "wide"}], "warnings": [], "op_histogram": {}}}
        L.build_verdict(x)
        self.assertFalse(x["accepted"])
        self.assertEqual(len(x["reasons"]), 1)

    def test_report_and_json_render(self) -> None:
        v = L.build_verdict(self.good())
        md = L.render_markdown(v)
        self.assertIn("ACCEPTED", md)
        self.assertIn("histogram", md)
        v["numeric"]["tensors"]["o"]["ok"] = False
        v = L.build_verdict(v)
        self.assertIn("REJECTED", L.render_markdown(v))
        json.dumps(v)


if __name__ == "__main__":
    unittest.main()
