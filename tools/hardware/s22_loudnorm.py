#!/usr/bin/env python3
"""s22_loudnorm: BS.1770 loudness measurement + normalisation for the S22 voice paths (pure Python).

The phone has no ffmpeg/sox/numpy, so this module does the job with the standard
library only.  Every WAV that is spoken on the phone (TTS answers, pre-rendered
filler clips, earcons) is brought to ONE target so voices and clips share a volume:

    target  -16 LUFS integrated   (override: S22_LOUDNESS_TARGET)
    ceiling  -2.0 dBFS sample peak (true peak stays <= -1.5 dBTP after the 48 kHz resample)

measure_lufs() is K-weighted, gated BS.1770-4 (400 ms blocks, 75 % overlap, -70 LUFS
absolute and -10 LU relative gate).  It matches ffmpeg's loudnorm "input_i" to within
about 0.3 LU on speech (checked in tests/evidence).  Clips shorter than one block, or
fully gated, fall back to the ungated K-weighted mean power.

To stay cheap on the phone CPU, only the first MAX_ANALYSE_S seconds are analysed
(a sentence is representative of the voice); the gain is applied to the whole file.
Peaks above the ceiling go through a smooth tanh limiter, so a high-crest voice
(Supertonic) can still reach the target.

CLI:  s22_loudnorm.py [--target LUFS] [--peak DBFS] [--measure] FILE.wav...   (in place)
"""
from __future__ import annotations

import array
import math
import os
import sys
import wave

TARGET_LUFS = float(os.environ.get('S22_LOUDNESS_TARGET', '-16'))
PEAK_CEIL_DB = -2.0
MAX_GAIN_DB = 20.0
MAX_ANALYSE_S = 6.0
ABS_GATE = -70.0


def _k_coeffs(fs: float):
    """K-weighting biquads (BS.1770 shelf + RLB high-pass) for any sample rate."""
    g, q, f0 = 3.999843853973347, 0.7071752369554196, 1681.974450955533
    a = 10 ** (g / 40)
    w0 = 2 * math.pi * f0 / fs
    alpha = math.sin(w0) / (2 * q)
    c = math.cos(w0)
    sa = 2 * math.sqrt(a) * alpha
    a0 = (a + 1) - (a - 1) * c + sa
    shelf = ((a * ((a + 1) + (a - 1) * c + sa)) / a0, (-2 * a * ((a - 1) + (a + 1) * c)) / a0,
             (a * ((a + 1) + (a - 1) * c - sa)) / a0,
             (2 * ((a - 1) - (a + 1) * c)) / a0, ((a + 1) - (a - 1) * c - sa) / a0)
    q2, f1 = 0.5003270373238773, 38.13547087602444
    k = math.tan(math.pi * f1 / fs)
    d = 1 + k / q2 + k * k
    hp = (1.0, -2.0, 1.0, 2 * (k * k - 1) / d, (1 - k / q2 + k * k) / d)
    return shelf, hp


def k_weight(x, fs: float) -> list:
    (b0, b1, b2, a1, a2), (c0, c1, c2, d1, d2) = _k_coeffs(fs)
    out = []
    ap = out.append
    x1 = x2 = y1 = y2 = 0.0
    u1 = u2 = v1 = v2 = 0.0
    for s in x:
        y = b0 * s + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2
        x2, x1 = x1, s
        y2, y1 = y1, y
        v = c0 * y + c1 * u1 + c2 * u2 - d1 * v1 - d2 * v2
        u2, u1 = u1, y
        v2, v1 = v1, v
        ap(v)
    return out


def measure_lufs(samples, fs: float, max_s: float = MAX_ANALYSE_S) -> float | None:
    """Integrated loudness in LUFS of mono float samples (-1..1), or None for silence."""
    n = min(len(samples), int(max_s * fs)) if max_s else len(samples)
    if n < int(0.02 * fs):
        return None
    y = k_weight(samples[:n], fs)
    blk = int(0.4 * fs)
    hop = int(0.1 * fs)
    sq = [v * v for v in y]
    if n < blk:
        z = sum(sq) / n
        return -0.691 + 10 * math.log10(z) if z > 1e-12 else None
    csum = [0.0]
    for v in sq:
        csum.append(csum[-1] + v)
    zs = [(csum[i + blk] - csum[i]) / blk for i in range(0, n - blk + 1, hop)]
    lz = lambda z: -0.691 + 10 * math.log10(z) if z > 0 else -999.0
    zs = [z for z in zs if lz(z) > ABS_GATE]
    if not zs:
        return None
    rel = lz(sum(zs) / len(zs)) - 10
    keep = [z for z in zs if lz(z) > rel] or zs
    return lz(sum(keep) / len(keep))


def _limit(v: float, ceil: float) -> float:
    """Smooth limiter: identity below 0.7*ceil, tanh knee up to ceil."""
    knee = 0.7 * ceil
    a = abs(v)
    if a <= knee:
        return v
    o = knee + (ceil - knee) * math.tanh((a - knee) / (ceil - knee))
    return o if v > 0 else -o


def apply_gain(pcm: array.array, gain: float, ceil_db: float = PEAK_CEIL_DB) -> array.array:
    ceil = 32767 * 10 ** (ceil_db / 20)
    peak = max((abs(s) for s in pcm), default=0)
    if peak * gain <= ceil:
        return array.array('h', [int(round(s * gain)) for s in pcm])
    knee = 0.7 * ceil
    return array.array('h', [int(round(v if abs(v) <= knee else _limit(v, ceil)))
                             for v in (s * gain for s in pcm)])


def read_wav(path):
    with wave.open(str(path), 'rb') as w:
        if w.getsampwidth() != 2:
            raise ValueError('16-bit PCM only')
        ch, fs = w.getnchannels(), w.getframerate()
        raw = w.readframes(w.getnframes())
    pcm = array.array('h')
    pcm.frombytes(raw[:len(raw) // 2 * 2])
    if sys.byteorder == 'big':
        pcm.byteswap()
    return pcm, fs, ch


def write_wav(path, pcm: array.array, fs: int, ch: int) -> None:
    if sys.byteorder == 'big':
        pcm = array.array('h', pcm)
        pcm.byteswap()
    with wave.open(str(path), 'wb') as w:
        w.setnchannels(ch)
        w.setsampwidth(2)
        w.setframerate(fs)
        w.writeframes(pcm.tobytes())


def _mono_float(pcm: array.array, ch: int) -> list:
    if ch == 1:
        return [s / 32768.0 for s in pcm]
    return [sum(pcm[i:i + ch]) / (ch * 32768.0) for i in range(0, len(pcm) - ch + 1, ch)]


def normalise_pcm(pcm: array.array, fs: int, ch: int = 1, target: float | None = None,
                  ceil_db: float = PEAK_CEIL_DB):
    """-> (new_pcm, info). info: before_lufs, after_lufs (estimated), gain_db, peak_db."""
    target = TARGET_LUFS if target is None else target
    lufs = measure_lufs(_mono_float(pcm, ch), fs)
    peak = max((abs(s) for s in pcm), default=0)
    info = {'before_lufs': None if lufs is None else round(lufs, 2),
            'peak_db': round(20 * math.log10(peak / 32768), 2) if peak else None}
    if lufs is None or peak == 0:
        info.update(gain_db=0.0, after_lufs=info['before_lufs'], changed=False)
        return pcm, info
    gain_db = max(-30.0, min(MAX_GAIN_DB, target - lufs))
    out = apply_gain(pcm, 10 ** (gain_db / 20), ceil_db)
    after = measure_lufs(_mono_float(out, ch), fs)
    if after is not None and abs(after - target) > 0.25 and gain_db < MAX_GAIN_DB:   # limiter ate some level
        gain_db = min(MAX_GAIN_DB, gain_db + (target - after))
        out = apply_gain(pcm, 10 ** (gain_db / 20), ceil_db)
        after = measure_lufs(_mono_float(out, ch), fs)
    info.update(gain_db=round(gain_db, 2), after_lufs=None if after is None else round(after, 2), changed=True)
    return out, info


def normalise_file(path, target: float | None = None, ceil_db: float = PEAK_CEIL_DB, out=None) -> dict:
    pcm, fs, ch = read_wav(path)
    new, info = normalise_pcm(pcm, fs, ch, target, ceil_db)
    if info['changed']:
        dst = out or path
        tmp = f'{dst}.norm.tmp'
        write_wav(tmp, new, fs, ch)
        os.replace(tmp, dst)
    return info


def measure_file(path) -> dict:
    pcm, fs, ch = read_wav(path)
    peak = max((abs(s) for s in pcm), default=0)
    lufs = measure_lufs(_mono_float(pcm, ch), fs, max_s=0)
    return {'lufs': None if lufs is None else round(lufs, 2),
            'peak_db': round(20 * math.log10(peak / 32768), 2) if peak else None,
            'rate': fs, 'channels': ch, 'seconds': round(len(pcm) / ch / fs, 2)}


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('files', nargs='+')
    ap.add_argument('--target', type=float, default=None)
    ap.add_argument('--peak', type=float, default=PEAK_CEIL_DB)
    ap.add_argument('--measure', action='store_true', help='report only (full-length analysis)')
    a = ap.parse_args(argv)
    rc = 0
    for f in a.files:
        try:
            info = measure_file(f) if a.measure else normalise_file(f, a.target, a.peak)
            print(f, info, flush=True)
        except Exception as e:      # a bad file must never break speech
            print(f, 'error', repr(e), file=sys.stderr)
            rc = 1
    return rc


if __name__ == '__main__':
    sys.exit(main())
