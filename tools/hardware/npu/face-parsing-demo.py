#!/usr/bin/env python3
"""Host side of the NPU face-parsing demo (stock PhotoParsing6_AWB_FACE_256 .nnc).

prep  IMAGE OUT.rgbp      -> 256x256 RGB, uint8, PLANAR (C,H,W) = the model's input layout
                             (interleaved HWC gives striped garbage).
render IMAGE OUT0.bin PNG -> output is float32 (6,64,64) logits (ch0 background, ch1 hair,
                             ch2 face skin, ...); writes image | hair | skin | argmax overlay.
On the phone:  ENN_OUT=/data/local/tmp/out/pp6 ENN_BIN=/bin/enn-run \\
  enn-chroot-run /data/models/stock/PhotoParsing6_...-35c.nnc 1 /data/local/tmp/face.rgbp
"""
import sys
import numpy as np
from PIL import Image


def prep(src, dst):
    im = Image.open(src).convert("RGB").resize((256, 256), Image.BILINEAR)
    np.asarray(im, np.uint8).transpose(2, 0, 1).copy().tofile(dst)


def render(src, out0, png):
    face = Image.open(src).convert("RGB").resize((256, 256), Image.BILINEAR)
    a = np.fromfile(out0, np.float32).reshape(6, 64, 64)
    pal = np.array([[0, 0, 0], [255, 170, 0], [0, 200, 255], [120, 255, 120], [255, 0, 255], [255, 255, 255]], np.uint8)
    ov = Image.blend(face, Image.fromarray(pal[a.argmax(0)]).resize((256, 256), Image.NEAREST), 0.45)

    def heat(k):
        x = a[k]
        t = ((x - x.min()) / (np.ptp(x) + 1e-6) * 255).astype(np.uint8)
        return Image.fromarray(t).resize((256, 256), Image.BILINEAR).convert("RGB")
    w = Image.new("RGB", (1024, 256))
    for i, im in enumerate((face, heat(1), heat(2), ov)):
        w.paste(im, (256 * i, 0))
    w.save(png)


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "prep":
        prep(sys.argv[2], sys.argv[3])
    elif len(sys.argv) == 5 and sys.argv[1] == "render":
        render(*sys.argv[2:])
    else:
        sys.exit(__doc__)
