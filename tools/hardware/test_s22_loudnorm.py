#!/usr/bin/env python3
"""Host-only tests for s22_loudnorm.py (BS.1770 measurement + normalisation) and the s22-say hook."""
import array
import importlib.util
import math
import os
import random
import re
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location('s22_loudnorm', HERE / 's22_loudnorm.py')
LN = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(LN)


def speechlike(seconds, fs, amp, seed=1):
    """Syllable-modulated band noise: bursts with pauses, crest factor like speech."""
    rnd = random.Random(seed)
    out, lp = array.array('h'), 0.0
    for i in range(int(seconds * fs)):
        env = max(0.0, math.sin(2 * math.pi * 3.5 * i / fs)) ** 2
        lp += 0.35 * (rnd.uniform(-1, 1) - lp)
        out.append(max(-32768, min(32767, int(amp * env * lp * 3))))
    return out


class Measure(unittest.TestCase):
    def test_997hz_sine_matches_bs1770_reference(self):
        # BS.1770: a 997 Hz sine at -20 dBFS peak reads -23.0 LUFS (K-weighting is ~0 dB there)
        fs = 48000
        x = [0.1 * math.sin(2 * math.pi * 997 * i / fs) for i in range(fs * 3)]
        self.assertAlmostEqual(LN.measure_lufs(x, fs, max_s=0), -23.0, delta=0.3)

    def test_silence_and_tiny_inputs_return_none(self):
        self.assertIsNone(LN.measure_lufs([0.0] * 48000, 48000))
        self.assertIsNone(LN.measure_lufs([0.1] * 10, 48000))

    def test_gain_changes_loudness_one_for_one(self):
        fs = 22050
        a = [s / 32768 for s in speechlike(4, fs, 3000)]
        b = [v * 2 for v in a]
        self.assertAlmostEqual(LN.measure_lufs(b, fs) - LN.measure_lufs(a, fs), 6.02, delta=0.05)


class Normalise(unittest.TestCase):
    def check(self, fs, amp, ch=1):
        mono = speechlike(5, fs, amp)
        pcm = array.array('h', [v for s in mono for v in (s,) * ch])
        out, info = LN.normalise_pcm(pcm, fs, ch)
        self.assertAlmostEqual(info['after_lufs'], LN.TARGET_LUFS, delta=0.4, msg=info)
        self.assertLessEqual(max(abs(v) for v in out), 32767 * 10 ** (LN.PEAK_CEIL_DB / 20) + 1)
        return info

    def test_quiet_and_loud_voices_land_on_one_target(self):
        for fs, amp in ((22050, 3000), (44100, 4000), (24000, 9000), (48000, 15000)):
            self.check(fs, amp)

    def test_stereo_earcon_scale(self):
        self.check(48000, 5000, ch=2)

    def test_silence_is_left_alone(self):
        pcm = array.array('h', [0] * 4000)
        out, info = LN.normalise_pcm(pcm, 22050)
        self.assertFalse(info['changed'])
        self.assertEqual(list(out), list(pcm))

    def test_file_roundtrip_in_place(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'a.wav'
            LN.write_wav(p, speechlike(3, 22050, 3000), 22050, 1)
            info = LN.normalise_file(p)
            self.assertTrue(info['changed'])
            m = LN.measure_file(p)
            self.assertEqual((m['rate'], m['channels']), (22050, 1))
            self.assertAlmostEqual(m['lufs'], LN.TARGET_LUFS, delta=0.5)
            self.assertFalse(list(Path(d).glob('*.tmp')))

    def test_limiter_is_monotonic_and_bounded(self):
        ceil = 32767 * 10 ** (LN.PEAK_CEIL_DB / 20)
        prev = 0
        for v in range(0, 60000, 500):
            o = LN._limit(v, ceil)
            self.assertGreaterEqual(o, prev)
            self.assertLessEqual(o, ceil)
            prev = o


class SayHook(unittest.TestCase):
    def test_s22_say_normalises_renders_but_not_wav_inputs(self):
        s = (HERE / 's22-say.sh').read_text()
        self.assertIn('loudnorm "$CHROOT$R"', s)            # render-only path
        self.assertIn('loudnorm "$CHROOT$WAV"', s)          # speak path, after neural/espeak synthesis
        self.assertEqual(len(re.findall(r'^\s*loudnorm "', s, re.M)), 2)   # --wav inputs are played as given
        self.assertIn('S22_LOUDNORM', s)

    def test_default_speech_level(self):
        s = (HERE / 's22-say.sh').read_text()
        self.assertIn('echo 6)}', s)
        self.assertIn('LVL=6', s)


if __name__ == '__main__':
    unittest.main()
