#!/usr/bin/env python3
"""tflite-make-conv.py OUT.tflite H W CIN COUT KH KW [--seed N] [--padding same|valid]

Write a one-op int8 TFLite model (CONV_2D, fused ReLU, per-tensor weight
quantisation, deterministic random weights) for the NPU acceptance harness:
  3x3 conv, 32x32, 8 -> 16 ch   :  OUT 32 32 8 16 3 3     (the "own int8 conv")
  1-D conv of width K            :  OUT 1 148 100 64 1 K   (the issue #64 sweep)
Needs ai-edge-litert + numpy (host only). No TensorFlow."""
import sys
import numpy as np
import flatbuffers
from ai_edge_litert import schema_py_generated as S


def build(h, w, cin, cout, kh, kw, seed=7, padding="same"):
    rng = np.random.default_rng(seed)
    wq = rng.integers(-40, 41, size=(cout, kh, kw, cin), dtype=np.int8)
    bias = rng.integers(-2000, 2001, size=(cout,), dtype=np.int32)
    s_in, s_w = 0.05, 0.02
    # pick the output scale from the accumulator spread so outputs are not all saturated
    s_out = float(max(1.0, 0.35 * np.sqrt(kh * kw * cin) * 40 * 30 / 127) * s_in * s_w) * 4
    def q(scale, zp):
        return S.QuantizationParametersT(scale=[scale], zeroPoint=[zp])
    oh, ow = (h, w) if padding == "same" else (h - kh + 1, w - kw + 1)
    def tensor(name, shape, ttype, buf, quant):
        t = S.TensorT(); t.name = name.encode(); t.shape = list(shape); t.type = ttype
        t.buffer = buf; t.quantization = quant; return t
    m = S.ModelT(); m.version = 3; m.description = b"npu-accept test conv"
    m.buffers = []
    for d in (None, wq.tobytes(), bias.tobytes(), None):
        b = S.BufferT(); b.data = None if d is None else np.frombuffer(d, np.uint8).tolist()
        m.buffers.append(b)
    oc = S.OperatorCodeT(); oc.builtinCode = S.BuiltinOperator.CONV_2D
    oc.deprecatedBuiltinCode = S.BuiltinOperator.CONV_2D; oc.version = 3
    m.operatorCodes = [oc]
    g = S.SubGraphT(); g.name = b"main"
    g.tensors = [
        tensor("input", (1, h, w, cin), S.TensorType.INT8, 0, q(s_in, 0)),
        tensor("weights", (cout, kh, kw, cin), S.TensorType.INT8, 1, q(s_w, 0)),
        tensor("bias", (cout,), S.TensorType.INT32, 2, q(s_in * s_w, 0)),
        tensor("output", (1, oh, ow, cout), S.TensorType.INT8, 3, q(s_out, -128)),
    ]
    op = S.OperatorT(); op.opcodeIndex = 0; op.inputs = [0, 1, 2]; op.outputs = [3]
    op.builtinOptionsType = S.BuiltinOptions.Conv2DOptions
    o = S.Conv2DOptionsT(); o.padding = S.Padding.SAME if padding == "same" else S.Padding.VALID
    o.strideH = o.strideW = 1; o.dilationHFactor = o.dilationWFactor = 1
    o.fusedActivationFunction = S.ActivationFunctionType.RELU
    op.builtinOptions = o
    g.operators = [op]; g.inputs = [0]; g.outputs = [3]
    m.subgraphs = [g]
    bld = flatbuffers.Builder(1024); bld.Finish(m.Pack(bld), file_identifier=b"TFL3")
    return bytes(bld.Output())


if __name__ == "__main__":
    a = [x for x in sys.argv[1:]]
    seed, padding = 7, "same"
    if "--seed" in a: i = a.index("--seed"); seed = int(a[i + 1]); del a[i:i + 2]
    if "--padding" in a: i = a.index("--padding"); padding = a[i + 1]; del a[i:i + 2]
    if len(a) != 7: sys.exit(__doc__)
    out, nums = a[0], [int(x) for x in a[1:]]
    open(out, "wb").write(build(*nums, seed=seed, padding=padding))
    print("wrote", out)
