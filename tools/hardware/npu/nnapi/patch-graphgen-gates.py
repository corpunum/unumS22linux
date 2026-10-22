#!/usr/bin/env python3
"""Patch Samsung GraphGen's two model gates in PRIVATE copies of the vendor libs
(never committed; vendor binaries stay out of git).

The Exynos 2200 NNAPI support library only compiles multi-op graphs that match a
whitelist of benchmark architectures (ResNet/InceptionV3/MobileNetV2-V3/VGG/DPED/
PyNET/CRNN/LSTM/SRCNN checkers keyed by op count) -> "This model is not surpported!".
A second gate (NpuSkip::isNeedSkip) refuses graphs whose first layer is
Eltwise/Concat/TanH/DepthToSpace/ReLU/ReLU6/Softmax/Reshape/Pooling/ResizeBilinear.
The NPU compiler behind them is generic. Patch:
  ann_s::AnnModelSupported::checkModel(Model const&) -> return true
  ann_s::AnnModelSupported::isSupported(Model const&, string const&) -> return true
  NpuSkip::isNeedSkip(shared_ptr<GNetwork>)          -> return false
Usage: patch-graphgen-gates.py DIR   (DIR holds libgraphgen_ann_import_s.so and
libgraphgen_intermediate_process.so; patched in place; expects the stock bytes).
"""
import hashlib, struct, sys
d = sys.argv[1]
P = [
    ("libgraphgen_ann_import_s.so", 0x142e6c, "ff0304d1fd7b0fa9", 0x52800020),  # mov w0,#1
    ("libgraphgen_ann_import_s.so", 0x1430c4, "fc0f1ef8fd7b01a9", 0x52800020),  # isSupported -> true
    ("libgraphgen_intermediate_process.so", 0x375914, "fc0f1ef8fd7b01a9", 0x52800000),  # mov w0,#0
]
RET = 0xd65f03c0
for name, off, expect, mov in P:
    p = f"{d}/{name}"
    b = bytearray(open(p, "rb").read())
    if b[off:off + 8].hex() != expect:
        sys.exit(f"{name}: unexpected bytes {b[off:off+8].hex()} at {off:#x} (want {expect})")
    b[off:off + 8] = struct.pack("<II", mov, RET)
    open(p, "wb").write(b)
    print(name, hex(off), "patched", hashlib.sha256(b).hexdigest()[:16])
