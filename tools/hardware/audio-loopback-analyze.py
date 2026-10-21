#!/usr/bin/env python3
"""audio-loopback-analyze.py: per-250 ms RMS, dominant frequency and the
1 kHz-band / 1.5-5 kHz-median ratio of a mono 48 kHz loopback WAV.
usage: audio-loopback-analyze.py IN.wav [TARGET_HZ]"""
import sys, wave
import numpy as np
w = wave.open(sys.argv[1]); target = float(sys.argv[2]) if len(sys.argv) > 2 else 1000.0
r = w.getframerate(); x = np.frombuffer(w.readframes(w.getnframes()), '<i2').astype(float) / 32768
for s in np.arange(0, len(x) / r - 0.25, 0.25):
    seg = x[int(s * r):int((s + 0.25) * r)]
    sp = np.abs(np.fft.rfft(seg * np.hanning(len(seg)))); fr = np.fft.rfftfreq(len(seg), 1 / r)
    k = np.argmax(sp[5:]) + 5; b = (fr > target * 0.9) & (fr < target * 1.1); nb = (fr > 1500) & (fr < 5000)
    print(f't={s:4.2f} rms={np.sqrt(np.mean(seg**2)):.4f} peak={fr[k]:6.0f}Hz '
          f'target/noise={sp[b].max() / np.median(sp[nb]):8.1f}')
