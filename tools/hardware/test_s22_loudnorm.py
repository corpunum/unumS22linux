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


class TrimPad(unittest.TestCase):
    """2026-11-04: every engine's render gets the same lead/tail silence (pause between chunks = one rhythm)."""
    fs = 48000

    def tone(self, seconds, amp=8000):
        return array.array('h', [int(amp * math.sin(2 * math.pi * 300 * i / self.fs))
                                 for i in range(int(seconds * self.fs))])

    def hiss(self, seconds, level=20):           # engine noise floor, far below the -50 dBFS silence floor
        rnd = random.Random(3)
        return array.array('h', [rnd.randint(-level, level) for _ in range(int(seconds * self.fs))])

    def edges(self, y):
        first = next(i for i, v in enumerate(y) if abs(v) > 300)
        last = max(i for i, v in enumerate(y) if abs(v) > 300)
        return first / self.fs, (len(y) - 1 - last) / self.fs

    def test_different_engine_silences_become_one_fixed_pad(self):
        for lead_s, tail_s in ((0.02, 0.05), (0.4, 0.6), (0.0, 0.0)):
            x = self.hiss(lead_s) + self.tone(0.5) + self.hiss(tail_s)
            y, info = LN.trim_pad(x, self.fs, 1, 60, 180)
            self.assertTrue(info['padded'])
            before, after = self.edges(y)
            self.assertAlmostEqual(before, 0.06, delta=0.012, msg=(lead_s, tail_s))
            self.assertAlmostEqual(after, 0.18, delta=0.012, msg=(lead_s, tail_s))
            self.assertTrue(all(v == 0 for v in y[:int(0.06 * self.fs)]))      # digital silence
            self.assertTrue(all(v == 0 for v in y[len(y) - int(0.18 * self.fs):]))

    def test_silent_input_is_returned_unchanged(self):
        x = self.hiss(0.3)
        y, info = LN.trim_pad(x, self.fs)
        self.assertIs(y, x)
        self.assertFalse(info['padded'])

    def test_stereo_frames_stay_aligned(self):
        mono = self.hiss(0.1) + self.tone(0.2) + self.hiss(0.1)
        st = array.array('h')
        for v in mono:
            st.extend((v, v))
        y, _ = LN.trim_pad(st, self.fs, 2, 50, 50)
        self.assertEqual(len(y) % 2, 0)
        self.assertEqual(list(y[0::2]), list(y[1::2]))

    def test_parse_pad(self):
        self.assertEqual(LN.parse_pad('60,180'), (60, 180))
        self.assertEqual(LN.parse_pad(' 80 '), (80, 80))
        for off in ('off', '', None, 'none'):
            self.assertIsNone(LN.parse_pad(off))
        for bad in ('a', '1,2,3', '-1', '1,99999'):
            with self.assertRaises(ValueError):
                LN.parse_pad(bad)

    def test_cli_pads_in_place_and_keeps_loudness(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / 'a.wav'
            LN.write_wav(f, self.hiss(0.5) + self.tone(1.0, 3000) + self.hiss(0.7), self.fs, 1)
            self.assertEqual(LN.main(['--pad', '60,180', str(f)]), 0)
            y, fs, ch = LN.read_wav(f)
            before, after = self.edges(y)
            self.assertAlmostEqual(before, 0.06, delta=0.012)
            self.assertAlmostEqual(after, 0.18, delta=0.012)
            self.assertAlmostEqual(LN.measure_file(f)['lufs'], LN.TARGET_LUFS, delta=0.6)
            LN.write_wav(f, self.hiss(0.5) + self.tone(1.0, 3000), self.fs, 1)
            LN.main([str(f)])                                   # no --pad: silence untouched (old callers)
            self.assertEqual(len(LN.read_wav(f)[0]), int(1.5 * self.fs))


if __name__ == '__main__':
    unittest.main()
