#!/usr/bin/env python3
"""Pure logic for npu-accept (graph rules, tolerances, enn-run parsing, verdict).

No device access and no hard numpy/litert dependency here, so the host tests can
import it with `python3 -I -B`. Only describe_model() needs ai-edge-litert."""
from __future__ import annotations

import re
import statistics

# Observed on the Exynos 2200 NPU (evidence/npu-custom-20261022, issue #64):
# 1-D kernels up to 15 wide are correct, 17 is silently wrong, 32 fails to compile.
MAX_SAFE_1D_KERNEL = 15
CONV_OPS = {"CONV_2D", "DEPTHWISE_CONV_2D", "TRANSPOSE_CONV", "CONV_3D"}
NPU_DTYPES = {"INT8", "UINT8"}
DEFAULT_TOL = {"INT8": 1, "UINT8": 1}
STALL_MS = 1000.0          # a healthy tiny net is ~0.3-30 ms; the known failure mode is 4.3 s + zeros
DMESG_ALARM = re.compile(
    r"Unable to handle kernel|page fault|\bOops\b|BUG:|Call trace|"
    r"sysmmu.*fault\s*:\s*[1-9]|iommu.*fault|SMMU.*fault|"
    r"\bnpu\b.*(timeout|time out|abort|hang|watchdog expired)|"
    r"(timeout|abort).*\bnpu\b|\bnpu\b.*(fail|error)", re.I)


# ---------------------------------------------------------------- graph rules
def describe_model(path):
    """TFLite file -> plain dict (see validate_graph). Needs ai-edge-litert."""
    from ai_edge_litert import schema_py_generated as S
    m = S.ModelT.InitFromObj(S.Model.GetRootAsModel(open(path, "rb").read(), 0))
    ops = {v: k for k, v in S.BuiltinOperator.__dict__.items() if not k.startswith("_")}
    tys = {v: k for k, v in S.TensorType.__dict__.items() if not k.startswith("_")}
    g = m.subgraphs[0]

    def tdesc(i):
        t = g.tensors[i]
        q = t.quantization
        return {"name": (t.name or b"").decode(errors="replace"), "shape": [int(x) for x in t.shape],
                "dtype": tys.get(t.type, str(t.type)),
                "scale": [float(x) for x in q.scale] if q and q.scale is not None else [],
                "zero_point": [int(x) for x in q.zeroPoint] if q and q.zeroPoint is not None else []}
    out_ops = []
    for i, op in enumerate(g.operators):
        oc = m.operatorCodes[op.opcodeIndex]
        name = ops.get(max(oc.builtinCode, oc.deprecatedBuiltinCode), "CUSTOM" if oc.customCode else "?")
        e = {"index": i, "op": name, "kernel": None, "dilation": [1, 1]}
        if name in CONV_OPS and len(op.inputs) > 1 and op.inputs[1] >= 0:
            w = g.tensors[op.inputs[1]].shape            # [O,H,W,I] (depthwise: [1,H,W,C*M])
            if len(w) == 4:
                e["kernel"] = [int(w[1]), int(w[2])]
            o = op.builtinOptions
            if o is not None and hasattr(o, "dilationHFactor"):
                e["dilation"] = [int(o.dilationHFactor), int(o.dilationWFactor)]
        out_ops.append(e)
    return {"subgraphs": len(m.subgraphs), "ops": out_ops,
            "inputs": [tdesc(i) for i in g.inputs], "outputs": [tdesc(i) for i in g.outputs],
            "tensor_dtypes": sorted({tys.get(t.type, str(t.type)) for t in g.tensors}),
            "dynamic_shapes": any(t.shapeSignature is not None and any(int(x) < 0 for x in list(t.shapeSignature)[1:])
                                  for t in g.tensors)}


def validate_graph(desc):
    """Return {'errors': [{'rule','detail'}], 'warnings': [...], 'op_histogram': {...}}.
    errors are known-bad-on-this-NPU; warnings are unverified or need care."""
    errors, warnings = [], []
    ops = desc.get("ops", [])
    hist = {}
    for o in ops:
        hist[o["op"]] = hist.get(o["op"], 0) + 1
    if desc.get("subgraphs", 1) != 1:
        errors.append({"rule": "single-subgraph", "detail": "%d subgraphs; the NNAPI path compiles subgraph 0 only" % desc["subgraphs"]})
    if desc.get("dynamic_shapes"):
        errors.append({"rule": "static-shapes", "detail": "dynamic tensor shapes cannot be compiled for the NPU"})
    bad = sorted(set(desc.get("tensor_dtypes", [])) - NPU_DTYPES - {"INT32", "INT64", "BOOL"})
    if bad:
        errors.append({"rule": "quantized-only", "detail": "tensor dtypes %s; the NPU path takes int8/uint8 (int32 bias)" % ",".join(bad)})
    for o in ops:
        k = o.get("kernel")
        if not k:
            continue
        kh, kw = k
        d = o.get("dilation") or [1, 1]
        if min(kh, kw) == 1 and max(kh, kw) > MAX_SAFE_1D_KERNEL:
            errors.append({"rule": "kernel-1d-max-15",
                           "detail": "op %d %s has a 1-D kernel %dx%d (width %d > %d): silently miscompiles on the NPU "
                                     "(K=17 wrong output, K=32 compile fails; issue #64)"
                                     % (o["index"], o["op"], kh, kw, max(kh, kw), MAX_SAFE_1D_KERNEL)})
        elif min(kh, kw) == 1 and max(kh, kw) >= 10:
            warnings.append({"rule": "kernel-1d-wide-unverified",
                             "detail": "op %d %s 1-D kernel width %d: <= 15 is not a guarantee; harness runs on 100->64 ch int8 models "
                                       "passed K=9 but failed K=11 and K=15 (stall + zeros), so the numeric check decides"
                                       % (o["index"], o["op"], max(kh, kw))})
        elif max(kh, kw) > MAX_SAFE_1D_KERNEL:
            warnings.append({"rule": "kernel-2d-large",
                             "detail": "op %d %s kernel %dx%d: wider than 15 and not covered by the sweep; numeric check decides"
                                       % (o["index"], o["op"], kh, kw)})
        if max(d) > 1 and min(kh, kw) == 1 and 1 < max(kh, kw) <= MAX_SAFE_1D_KERNEL \
                and (max(kh, kw) - 1) * max(d) + 1 > MAX_SAFE_1D_KERNEL:
            warnings.append({"rule": "dilated-extent", "detail": "op %d dilated extent %d > 15; unverified" %
                             (o["index"], (max(kh, kw) - 1) * max(d) + 1)})
    names = [o["op"] for o in ops]
    for i, n in enumerate(names):
        if n == "FULLY_CONNECTED":
            j = i - 1
            while j >= 0 and names[j] == "RESHAPE":
                j -= 1
            if j >= 0 and names[j] == "AVERAGE_POOL_2D":
                errors.append({"rule": "fc-after-avgpool",
                               "detail": "op %d FULLY_CONNECTED follows AVERAGE_POOL_2D: crashes the on-device compiler "
                                         "(SIGSEGV in the IR builder); cut the model before the pool/FC tail (tflite-cut.py)" % i})
    if "LEAKY_RELU" in names:
        warnings.append({"rule": "leaky-relu", "detail": "int8 LEAKY_RELU is not delegated by the NNAPI delegate; rewrite as PRELU (tflite-leaky2prelu.py)"})
    if names and names[0] in ("SOFTMAX", "RESHAPE"):
        warnings.append({"rule": "leading-" + names[0].lower(), "detail": "first op is %s; refused by the stock NpuSkip gate (the patched gates are used)" % names[0]})
    if not any(n in CONV_OPS or n == "FULLY_CONNECTED" for n in names):
        warnings.append({"rule": "no-matmul-op", "detail": "no conv/FC op; little to gain from the NPU"})
    return {"errors": errors, "warnings": warnings, "op_histogram": hist}


# ------------------------------------------------------------------ nnc scan
def nnc_sections(blob: bytes):
    return {"DSP_BINARY": b"DSP_BINARY" in blob, "NPU_BINARY": b"NPU_BINARY" in blob}


# ----------------------------------------------------------------- numerics
def tolerance_for(name, dtype, overrides=None, default=None):
    overrides = overrides or {}
    if name in overrides:
        return int(overrides[name])
    if "*" in overrides:
        return int(overrides["*"])
    return int(default if default is not None else DEFAULT_TOL.get(dtype, 0))


def parse_tol(items):
    """['out=2', '*=1'] or ['1'] -> dict."""
    d = {}
    for it in items or []:
        if "=" in it:
            k, v = it.rsplit("=", 1)
            d[k] = int(v)
        else:
            d["*"] = int(it)
    return d


def _vals(b: bytes, dtype):
    if dtype == "INT8":
        return [x - 256 if x > 127 else x for x in b]
    return list(b)


def diff_stats(ref: bytes, got: bytes, dtype="INT8"):
    """Element-wise |ref-got| for 8-bit tensors: max_abs, hist {delta: count}, n, size_ok."""
    if len(ref) != len(got):
        return {"size_ok": False, "n": len(ref), "got_n": len(got), "max_abs": None, "hist": {}, "exact": 0}
    hist = {}
    mx = 0
    for a, b in zip(_vals(ref, dtype), _vals(got, dtype)):
        d = abs(a - b)
        hist[d] = hist.get(d, 0) + 1
        if d > mx:
            mx = d
    return {"size_ok": True, "n": len(ref), "got_n": len(got), "max_abs": mx, "hist": dict(sorted(hist.items())),
            "exact": hist.get(0, 0)}


def merge_hist(hs):
    out = {}
    for h in hs:
        for k, v in h.items():
            out[k] = out.get(k, 0) + v
    return dict(sorted(out.items()))


def judge_tensor(stats_list, tol):
    """stats_list: diff_stats results over all inputs for one output tensor -> (ok, summary, reasons)."""
    if any(not s["size_ok"] for s in stats_list):
        return False, {"max_abs": None, "hist": {}, "n": 0, "tol": tol, "exact": 0, "over_tol": 0}, \
            ["output size differs from the CPU reference"]
    mx = max((s["max_abs"] for s in stats_list), default=0)
    hist = merge_hist(s["hist"] for s in stats_list)
    n = sum(s["n"] for s in stats_list)
    over = sum(v for k, v in hist.items() if k > tol)
    reasons = []
    if over:
        reasons.append("%d of %d values differ by more than %d (max |delta| %d)" % (over, n, tol, mx))
    return over == 0, {"max_abs": mx, "hist": hist, "n": n, "tol": tol, "exact": hist.get(0, 0), "over_tol": over}, reasons


def constant_output(b: bytes):
    return len(b) > 0 and len(set(b)) == 1


# ------------------------------------------------------------ enn-run output
def parse_enn_run(text: str):
    r = {"stages": {}, "buffers": [], "execute": None, "checksums": {}, "errors": []}
    for ln in text.splitlines():
        m = re.match(r"stage=(\w+) (.*)", ln)
        if m:
            st, rest = m.group(1), m.group(2)
            kv = dict(re.findall(r"(\w+)=(\S+)", rest))
            r["stages"][st] = kv
            if st == "execute" and "p50_ms" in kv:
                r["execute"] = {k: float(v) for k, v in kv.items()}
            if "rc" in kv and kv["rc"] != "0":
                r["errors"].append("stage %s rc=%s" % (st, kv["rc"]))
            continue
        m = re.match(r"buffer\[(\d+)\] (in|out)(\d+) size=(\d+)", ln)
        if m:
            r["buffers"].append({"dir": m.group(2), "index": int(m.group(3)), "size": int(m.group(4))})
            continue
        m = re.match(r"output\[(\d+)\] checksum=(\w+) nonzero_bytes=(\d+)", ln)
        if m:
            r["checksums"][int(m.group(1))] = {"checksum": m.group(2), "nonzero": int(m.group(3))}
    return r


def map_buffers(buffers, model_in_sizes, model_out_sizes):
    """Match model tensors to .nnc buffers by byte size (the SL may add extra CPU-glue I/O).
    Returns (in_map, out_cands, problems, notes). in_map[j] = nnc input index feeding model input j
    (all other nnc inputs get zeros; with several same-size inputs the last is used, glue comes first).
    out_cands[j] = list of nnc output indices that could hold model output j (several of the same
    size are all dumped and the caller picks the one that matches the reference, noted in `notes`)."""
    ins = [b for b in buffers if b["dir"] == "in"]
    outs = [b for b in buffers if b["dir"] == "out"]
    problems, notes = [], []
    in_map, out_cands = [], []
    for j, sz in enumerate(model_in_sizes):
        same = [b["index"] for b in ins if b["size"] == sz]
        if not same:
            problems.append("no input buffer of %d bytes for model tensor %d" % (sz, j))
            in_map.append(None)
            continue
        if len(same) > 1:
            notes.append("input %d: %d nnc inputs of %d bytes, feeding the last" % (j, len(same), sz))
        in_map.append(same[-1])
    for j, sz in enumerate(model_out_sizes):
        same = [b["index"] for b in outs if b["size"] == sz]
        if not same:
            problems.append("no output buffer of %d bytes for model tensor %d" % (sz, j))
        elif len(same) > 1:
            notes.append("output %d: %d nnc outputs of %d bytes, best match to the CPU reference is used" % (j, len(same), sz))
        out_cands.append(same)
    return in_map, out_cands, problems, notes


def pick_output(cands, diffs):
    """cands: nnc output indices; diffs: {idx: total |delta| over all inputs}. Lowest wins (ties: last)."""
    return min(cands, key=lambda i: (diffs[i], -i)) if cands else None


def latency_summary(runs):
    """runs: parsed 'execute' dicts (one per enn-run process, each over n iterations)."""
    runs = [r for r in runs if r]
    if not runs:
        return None
    tot = sum(r["n"] for r in runs)
    return {"processes": len(runs), "iterations": int(tot),
            "p50_ms": statistics.median(r["p50_ms"] for r in runs),
            "p99_ms": max(r["p99_ms"] for r in runs), "max_ms": max(r["max_ms"] for r in runs),
            "min_ms": min(r["min_ms"] for r in runs),
            "mean_ms": sum(r["mean_ms"] * r["n"] for r in runs) / tot,
            "first_ms_median": statistics.median(r["first_ms"] for r in runs)}


def new_dmesg_alarms(before: str, after: str):
    seen = set(before.splitlines())
    return [ln for ln in after.splitlines()
            if ln not in seen and DMESG_ALARM.search(ln) and not re.search(r"fault\s*:\s*0\b", ln)]


# ------------------------------------------------------------------- verdict
def build_verdict(v):
    """v: dict with the sections filled in by the CLI; sets v['accepted'] and v['reasons'].
    A step that should have run but did not counts against acceptance."""
    reasons = []
    g = v.get("graph")
    if g is None:
        reasons.append("graph validation did not run")
    else:
        for e in g["errors"]:
            if v.get("graph_rules") == "off":
                v.setdefault("ignored_graph_errors", []).append(e)
            else:
                reasons.append("graph rule %s: %s" % (e["rule"], e["detail"]))
    c = v.get("compile")
    if c is None:
        if not reasons:
            reasons.append("compile did not run")
    else:
        if not c.get("ok"):
            reasons.append("compile failed: %s" % c.get("error", "unknown"))
        if c.get("dsp_binary"):
            reasons.append("compiled .nnc contains a DSP_BINARY section (crashed the phone before); refused")
        if c.get("ok") and c.get("partitions", 1) != 1:
            reasons.append("%s NPU partitions: CPU ops sit between them, end-to-end result cannot be verified by this harness" % c.get("partitions"))
    n = v.get("numeric")
    if n is not None:
        for name, t in n["tensors"].items():
            if not t["ok"]:
                reasons.extend("output %s: %s" % (name, r) for r in (t["reasons"] or ["failed tolerance check"]))
        if n.get("n_inputs", 0) < 1:
            reasons.append("no inputs were compared")
        reasons.extend(n.get("problems", []))
    elif c is not None and c.get("ok") and not c.get("dsp_binary") and c.get("partitions", 1) == 1:
        reasons.append("numeric comparison did not run")
    d = v.get("determinism")
    if d is not None and not d["ok"]:
        reasons.append("non-deterministic: %s" % d["detail"])
    lat = v.get("latency")
    if lat is not None and lat["max_ms"] > STALL_MS:
        reasons.append("execution stall: max %.0f ms (> %.0f ms; the known failure mode is ~4300 ms with all-zero output)" % (lat["max_ms"], STALL_MS))
    mem = v.get("memory")
    if mem is not None and mem.get("dmesg_alarms"):
        reasons.append("kernel log alarms during the run: %d (first: %s)" % (len(mem["dmesg_alarms"]), mem["dmesg_alarms"][0][:160]))
    rec = v.get("recovery")
    if rec is not None and not rec["ok"]:
        reasons.append("NPU not usable afterwards: %s" % rec["detail"])
    v["reasons"] = reasons
    v["accepted"] = not reasons
    return v


def render_markdown(v):
    L = ["# NPU acceptance: %s -> %s" % (v["model"]["name"], "ACCEPTED" if v["accepted"] else "REJECTED"), ""]
    L.append("- model sha256 `%s`, graph rules %s, run %s" % (v["model"]["sha256"][:16], v.get("graph_rules", "on"), v.get("started", "")))
    if v["reasons"]:
        L.append("- reasons:")
        L.extend("  - " + r for r in v["reasons"])
    if v.get("ignored_graph_errors"):
        L.append("- graph errors ignored (rules off): " + "; ".join(e["rule"] for e in v["ignored_graph_errors"]))
    g = v.get("graph")
    if g:
        L += ["", "## 1 Graph", "ops: " + ", ".join("%s x%d" % kv for kv in sorted(g["op_histogram"].items()))]
        L += ["- warning `%s`: %s" % (w["rule"], w["detail"]) for w in g["warnings"]]
        L += ["- error `%s`: %s" % (e["rule"], e["detail"]) for e in g["errors"]]
    c = v.get("compile")
    if c:
        L += ["", "## 2 Compile", "ok=%s partitions=%s nnc=%s bytes sections=%s time=%.1fs" % (
            c.get("ok"), c.get("partitions"), c.get("nnc_bytes"), c.get("sections"), c.get("seconds", 0))]
    n = v.get("numeric")
    if n:
        L += ["", "## 3 NPU vs CPU reference (%d inputs: %d random, %d edge, %d real)" % (
            n["n_inputs"], n["n_random"], n["n_edge"], n["n_real"])]
        for name, t in n["tensors"].items():
            L.append("- `%s` tol %d: max |delta| %s, %d/%d exact, histogram %s -> %s" % (
                name, t["tol"], t["max_abs"], t.get("exact", 0), t["n"],
                ", ".join("%s:%d" % kv for kv in list(t["hist"].items())[:8]), "ok" if t["ok"] else "FAIL"))
        for name, t in n["tensors"].items():
            if t.get("worst_inputs") and not t["ok"]:
                L.append("- `%s` worst inputs: %s" % (name, ", ".join("%s (%s)" % (e["input"], e["max_abs"]) for e in t["worst_inputs"])))
        if n.get("argmax_agree") is not None:
            L.append("- top-1 agreement: %s" % n["argmax_agree"])
    d = v.get("determinism")
    if d:
        L += ["", "## 4 Determinism and latency", "determinism: %s - %s" % ("ok" if d["ok"] else "FAIL", d["detail"])]
        lat = v.get("latency")
        if lat:
            L.append("latency over %d iterations: p50 %.2f ms, p99 %.2f ms, max %.2f ms, mean %.3f ms (first call median %.2f ms)" % (
                lat["iterations"], lat["p50_ms"], lat["p99_ms"], lat["max_ms"], lat["mean_ms"], lat["first_ms_median"]))
    m, rec = v.get("memory"), v.get("recovery")
    if m or rec:
        L += ["", "## 5 Memory and recovery"]
        if m:
            L.append("peak RSS of the ENN process %s kB; MemAvailable before/after %s/%s kB; new kernel-log alarms: %d" % (
                m.get("max_rss_kb"), m.get("memavail_before_kb"), m.get("memavail_after_kb"), len(m.get("dmesg_alarms", []))))
        if rec:
            L.append("canary conv before/after: %s (%s)" % ("same output" if rec["ok"] else "FAIL", rec["detail"]))
    return "\n".join(L) + "\n"
