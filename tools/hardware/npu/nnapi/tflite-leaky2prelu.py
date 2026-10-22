#!/usr/bin/env python3
"""tflite-leaky2prelu.py IN.tflite OUT.tflite
Rewrite every int8 LEAKY_RELU(alpha) as PRELU(x, alpha_const). TFLite's NNAPI
delegate does not hand int8 LEAKY_RELU to the driver, so a wav2letter-style net is
cut into one NPU partition per conv; PRELU it does delegate, and Samsung's compiler
takes it, so the whole graph becomes one NPU partition. alpha is stored as one int8
value 127 with scale alpha/127 (zero point 0): the same function, requantisation
rounding may differ by 1 LSB."""
import sys, numpy as np, flatbuffers
from ai_edge_litert import schema_py_generated as S
m = S.ModelT.InitFromObj(S.Model.GetRootAsModel(open(sys.argv[1], "rb").read(), 0))
BO = S.BuiltinOperator
def code(oc): return max(oc.builtinCode, oc.deprecatedBuiltinCode)
pr = next((i for i, oc in enumerate(m.operatorCodes) if code(oc) == BO.PRELU), None)
if pr is None:
    oc = S.OperatorCodeT(); oc.builtinCode = BO.PRELU; oc.deprecatedBuiltinCode = BO.PRELU; oc.version = 1
    m.operatorCodes.append(oc); pr = len(m.operatorCodes) - 1
g = m.subgraphs[0]; n = 0
for op in g.operators:
    if code(m.operatorCodes[op.opcodeIndex]) != BO.LEAKY_RELU: continue
    a = op.builtinOptions.alpha
    b = S.BufferT(); b.data = np.array([127], np.int8).view(np.uint8); m.buffers.append(b)
    t = S.TensorT(); t.shape = np.array([1], np.int32); t.type = S.TensorType.INT8
    t.buffer = len(m.buffers) - 1; t.name = f"prelu_alpha_{n}".encode()
    q = S.QuantizationParametersT(); q.scale = np.array([a / 127.0], np.float32); q.zeroPoint = np.array([0], np.int64)
    t.quantization = q; g.tensors.append(t)
    op.opcodeIndex = pr; op.inputs = np.array([op.inputs[0], len(g.tensors) - 1], np.int32)
    op.builtinOptionsType = S.BuiltinOptions.NONE; op.builtinOptions = None; n += 1
bld = flatbuffers.Builder(1024); bld.Finish(m.Pack(bld), file_identifier=b"TFL3")
open(sys.argv[2], "wb").write(bld.Output()); print("rewrote", n, "LEAKY_RELU ->", sys.argv[2])
