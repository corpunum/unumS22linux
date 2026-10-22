#!/usr/bin/env python3
"""tflite-cut.py list MODEL.tflite
   tflite-cut.py cut MODEL.tflite OUT.tflite FIRST_OP LAST_OP
Keep operators FIRST_OP..LAST_OP (inclusive) of subgraph 0; the new model input is
the first op's activation input and the output is the last op's output. Used to
drop glue ops (leading RESHAPE, trailing FC/SOFTMAX) that Samsung's on-device NPU
compiler mishandles; the cut-off ops run on the CPU (they are tiny).
Uses the flatbuffers object API shipped in ai-edge-litert (schema_py_generated)."""
import sys, flatbuffers
from ai_edge_litert import schema_py_generated as S
def load(p):
    return S.ModelT.InitFromObj(S.Model.GetRootAsModel(open(p, "rb").read(), 0))
names = {v: k for k, v in S.BuiltinOperator.__dict__.items() if not k.startswith("_")}
m = load(sys.argv[2]); g = m.subgraphs[0]
def opname(op): return names.get(max(m.operatorCodes[op.opcodeIndex].builtinCode, m.operatorCodes[op.opcodeIndex].deprecatedBuiltinCode), "?")
if sys.argv[1] == "list":
    for i, op in enumerate(g.operators):
        ins = [(t, list(g.tensors[t].shape)) for t in op.inputs if t >= 0]
        print(i, opname(op), "in", ins[0], "out", [(t, list(g.tensors[t].shape)) for t in op.outputs])
    print("inputs", list(g.inputs), "outputs", list(g.outputs))
    sys.exit()
out, a, b = sys.argv[3], int(sys.argv[4]), int(sys.argv[5])
g.operators = g.operators[a:b + 1]
g.inputs = [g.operators[0].inputs[0]]
g.outputs = [g.operators[-1].outputs[0]]
m.signatureDefs = []
bld = flatbuffers.Builder(1024)
bld.Finish(m.Pack(bld), file_identifier=b"TFL3")
open(out, "wb").write(bld.Output())
print("wrote", out, "ops", a, "..", b, "in", list(g.tensors[g.inputs[0]].shape), "out", list(g.tensors[g.outputs[0]].shape))
