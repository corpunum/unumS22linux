#!/usr/bin/env python3
"""Tone loopback helper (stdlib only).

gen OUT.wav F1[,F2..] SECONDS_EACH AMPLITUDE | analyze IN.wav F1[,F2..]
"""
import math, struct, sys, wave
if sys.argv[1] == 'gen':
    # gen OUT FREQ[,FREQ2...] SECONDS_EACH AMPLITUDE  (tones back to back)
    out, fs, sec, amp = sys.argv[2], [float(x) for x in sys.argv[3].split(',')], float(sys.argv[4]), float(sys.argv[5])
    with wave.open(out, 'wb') as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(48000)
        frames = bytearray()
        for f in fs:
            for i in range(int(48000 * sec)):
                v = int(32767 * amp * math.sin(2 * math.pi * f * i / 48000))
                frames += struct.pack('<hh', v, v)
        w.writeframes(bytes(frames))
else:
    path, fs = sys.argv[2], [float(x) for x in sys.argv[3].split(',')]
    with wave.open(path) as w:
        ch, sw, sr, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
        raw = w.readframes(n)
    fmt = {2: 'h', 4: 'i'}[sw]
    vals = struct.unpack('<%d%s' % (len(raw) // sw, fmt), raw)
    for c in range(ch):
        x = vals[c::ch]; full = float(2 ** (8 * sw - 1))
        win = 4800; res = []
        for s in range(0, len(x) - win, win):
            seg = x[s:s + win]
            rms = math.sqrt(sum(v * v for v in seg) / win) / full
            def g(freq):
                k = 2 * math.cos(2 * math.pi * freq / sr); s1 = s2 = 0.0
                for v in seg:
                    s0 = v + k * s1 - s2; s2, s1 = s1, s0
                return math.sqrt(max(s1 * s1 + s2 * s2 - k * s1 * s2, 0)) / win / full
            res.append((round(s / sr, 1), round(rms, 4)) + tuple(round(g(f) * 1e4, 1) for f in fs))
        print('ch%d (t, rms, %s x1e-4)' % (c, fs))
        for r in res: print(' ', r)
