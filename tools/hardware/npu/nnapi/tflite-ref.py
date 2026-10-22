#!/usr/bin/env python3
"""tflite-ref.py gen|ref MODEL.tflite DIR [N]
gen: write N deterministic random int8 inputs DIR/in_<i>.bin (model input shape).
ref: run the TFLite CPU *reference* kernels on each DIR/in_<i>.bin -> DIR/ref_<i>.bin
     (raw int8 output bytes) and print the output tensor's details.
Needs ai-edge-litert + numpy (rig venv)."""
import sys, glob, os, numpy as np
from ai_edge_litert.interpreter import Interpreter, OpResolverType
mode, model, d = sys.argv[1:4]
it = Interpreter(model_path=model, experimental_op_resolver_type=OpResolverType.BUILTIN_REF)
it.allocate_tensors()
i0, o0 = it.get_input_details()[0], it.get_output_details()[0]
if mode == "gen":
    n = int(sys.argv[4]) if len(sys.argv) > 4 else 10
    rng = np.random.default_rng(1234)
    os.makedirs(d, exist_ok=True)
    for k in range(n):
        rng.integers(-128, 128, size=i0["shape"], dtype=np.int8).tofile(f"{d}/in_{k}.bin")
    print("input", i0["shape"], i0["dtype"], i0["quantization"])
else:
    for f in sorted(glob.glob(f"{d}/in_*.bin")):
        x = np.fromfile(f, np.int8).reshape(i0["shape"])
        it.set_tensor(i0["index"], x); it.invoke()
        it.get_tensor(o0["index"]).tofile(f.replace("in_", "ref_"))
    print("output", o0["shape"], o0["dtype"], o0["quantization"])
