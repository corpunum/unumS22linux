#!/usr/bin/env python3
"""kws-features.py OUTDIR WAV... : TF speech-commands style MFCC (ARM ML-zoo KWS
front end: 16 kHz, 1 s, 40 ms Hann window / 20 ms stride, FFT 1024, 40 mel bands
20-4000 Hz, log, DCT-II -> 10 coeffs = 49x10), re-implemented in numpy after
TensorFlow's audio_spectrogram + Mfcc ops. Each wav is resampled to 16 kHz and the
1 s window is centred on the loudest 100 ms. Writes OUTDIR/<name>.f32 (49x10
float32) plus quantised inputs for DS-CNN (<name>.dscnn.bin) and MicroNet
(<name>.micronet.bin)."""
import os, sys, wave, numpy as np
from math import gcd
from scipy.signal import resample_poly

def load16k(p):
    w = wave.open(p); sr = w.getframerate()
    x = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float64) / 32768.0
    if w.getnchannels() > 1: x = x.reshape(-1, w.getnchannels()).mean(1)
    if sr != 16000: g = gcd(16000, sr); x = resample_poly(x, 16000 // g, sr // g)
    e = np.convolve(x * x, np.ones(1600), "same"); c = int(np.argmax(e))
    x = np.pad(x, 8000); c += 8000
    return x[c - 8000:c + 8000]

def mfcc(x, sr=16000, win=640, hop=320, nfft=1024, nch=40, lo=20.0, hi=4000.0, ncoef=10):
    n = 1 + (len(x) - win) // hop
    hann = 0.5 - 0.5 * np.cos(2 * np.pi * np.arange(win) / win)
    frames = np.stack([x[i * hop:i * hop + win] * hann for i in range(n)])
    spec = np.abs(np.fft.rfft(frames, nfft)) ** 2            # (n, 513) magnitude squared
    L = nfft // 2 + 1; hz = 0.5 * sr / (L - 1)
    mel = lambda f: 1127.0 * np.log1p(f / 700.0)
    mlo, mhi = mel(lo), mel(hi); centers = mlo + (mhi - mlo) / (nch + 1) * np.arange(1, nch + 2)
    s0 = max(1, int(round(lo / hz))); s1 = min(L - 1, int(hi / hz))
    band = np.full(L, -2); wts = np.zeros(L); ch = 0
    for i in range(s0, s1 + 1):
        m = mel(i * hz)
        while ch < nch and centers[ch] < m: ch += 1
        band[i] = ch - 1
        wts[i] = (centers[0] - m) / (centers[0] - mlo) if ch == 0 else (centers[ch] - m) / (centers[ch] - centers[ch - 1])
    out = np.zeros((n, nch))
    for i in range(s0, s1 + 1):
        v = np.sqrt(spec[:, i]); wv = v * wts[i]; c = band[i]
        if c >= 0: out[:, c] += wv
        if c + 1 < nch: out[:, c + 1] += v - wv
    lg = np.log(np.maximum(out, 1e-12))
    k = np.arange(nch); dct = np.sqrt(2.0 / nch) * np.cos(np.pi / nch * np.outer(np.arange(ncoef), k + 0.5))
    return lg @ dct.T                                          # (49, 10)

def q(f, scale, zp): return np.clip(np.round(f / scale) + zp, -128, 127).astype(np.int8)

if __name__ == "__main__":
    od = sys.argv[1]; os.makedirs(od, exist_ok=True)
    for p in sys.argv[2:]:
        f = mfcc(load16k(p)).astype(np.float32); nm = os.path.splitext(os.path.basename(p))[0]
        f.tofile(f"{od}/{nm}.f32")
        q(f, 1.1021944284439087, 96).tofile(f"{od}/{nm}.dscnn.bin")
        q(f, 0.20109497010707855, -5).tofile(f"{od}/{nm}.micronet.bin")
    print(len(sys.argv) - 2, "feature sets ->", od)
