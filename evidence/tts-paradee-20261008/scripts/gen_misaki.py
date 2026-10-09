"""Paradee reference path (misaki G2P + onnxruntime python). usage: gen_misaki.py SENTENCES OUTDIR THREADS"""
import sys, time, wave, numpy as np
from paradee import Paradee
sents = [l.strip() for l in open(sys.argv[1]) if l.strip()]
out, th = sys.argv[2], int(sys.argv[3])
M = "/home/corpunum/models/paradee"
t = time.perf_counter(); tts = Paradee(model_path=f"{M}/onnx/paradee_int8.onnx", config_path=f"{M}/config.json", threads=th)
print(f"load {time.perf_counter()-t:.2f}s", file=sys.stderr)
tts("Warm up.")
tot_a = tot_g2p = tot_onnx = 0
with open(f"{out}/phonemes.txt", "w") as pf:
    for i, s in enumerate(sents, 1):
        t0 = time.perf_counter(); chunks = list(tts._chunks(s)); t1 = time.perf_counter()
        a = np.concatenate([tts.generate_from_phonemes(c) for c in chunks]); t2 = time.perf_counter()
        tot_g2p += t1 - t0; tot_onnx += t2 - t1; tot_a += len(a) / 24000
        pf.write(" | ".join(chunks) + "\n")
        with wave.open(f"{out}/{i:02d}.wav", "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(24000); w.writeframes((np.clip(a, -1, 1) * 32767).astype("<i2").tobytes())
print(f"threads={th} audio={tot_a:.1f}s g2p={tot_g2p:.2f}s onnx={tot_onnx:.2f}s RTF_onnx={tot_onnx/tot_a:.4f} RTF_total={(tot_g2p+tot_onnx)/tot_a:.4f}")
