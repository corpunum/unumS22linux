#!/usr/bin/env python3
"""Host-only tests for s22_voice.py: parity with the rig's JavaScript (voice/parity.json) and the filler bank."""
import importlib.util
import json
import tempfile
import unittest
import wave
from pathlib import Path

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location('s22_voice', HERE / 's22_voice.py')
V = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(V)
P = json.loads((HERE / 'voice' / 'parity.json').read_text())


class Parity(unittest.TestCase):
    def test_enhance(self):
        for c in P['enhance']:
            self.assertEqual(V.enhance_for_voice(c['in']), c['out'], c['in'][:40])
            self.assertEqual(V.enhance_for_voice(c['in'], lang='el'), c['outEl'], c['in'][:40])
            self.assertEqual(V.enhance_for_voice(c['in'], style='light', seed='abc'), c['light'], c['in'][:40])

    def test_strip(self):
        for c in P['strip']:
            self.assertEqual(V.strip_for_speech(c['in']), c['out'], c['in'][:40])

    # The JS patterns contain final sigmas but are matched against sigma-folded text, so
    # these Greek search phrases never match there; the port folds the patterns (a fix).
    JS_FINAL_SIGMA_BUG = {'πες μου για την Αθήνα': 'search', 'Ποιος είναι ο πρωθυπουργός': 'search'}

    def test_classify(self):
        for c in P['classify']:
            want = self.JS_FINAL_SIGMA_BUG.get(c['in'], c['category'])
            self.assertEqual(V.classify_question(c['in']), want, c['in'])
            self.assertEqual(V.filler_lang(c['in']), c['lang'], c['in'])

    def test_thinking(self):
        for c in P['thinking']:
            self.assertEqual(V.summarize_thinking(c['in']), c['summary'], c['in'])
            self.assertEqual(V.thinking_to_spoken(c['in'], 0), c['spoken0'], c['in'])
            self.assertEqual(V.thinking_to_spoken(c['in'], 1), c['spoken1'], c['in'])


class Behaviour(unittest.TestCase):
    def test_no_bullets_spoken(self):
        out = V.reply_to_speech('Verification warning: x\n- one\n- two\n**bold** 21°C')
        self.assertNotIn('-', out.replace('Wi-Fi', ''))
        self.assertNotIn('*', out)
        self.assertNotIn('Verification', out)
        self.assertIn('degrees Celsius', out)
        self.assertTrue(out.startswith('First'))

    def test_pick_never_repeats(self):
        for n in (2, 3, 11):
            for last in range(n):
                for r in (0.0, 0.5, 0.999):
                    self.assertNotEqual(V.pick_clip_index(n, last, lambda r=r: r), last)
        self.assertEqual(V.pick_clip_index(1, 0), 0)
        self.assertEqual(V.pick_clip_index(0), -1)

    def test_filler_bank(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / 'vf'
            man = {'clips': {'en': {'search': [{'n': i, 'phrase': f'p{i}', 'durationSec': 1} for i in (1, 2, 3)],
                                    'general': [{'n': 1, 'phrase': 'g', 'durationSec': 1}, {'n': 2, 'phrase': 'g2', 'durationSec': 1}]}}}
            for cat, ns in (('search', (1, 2, 3)), ('general', (1, 2))):
                (root / 'en' / cat).mkdir(parents=True)
                for n in ns:
                    with wave.open(str(root / 'en' / cat / f'{n}.wav'), 'wb') as w:
                        w.setnchannels(1); w.setsampwidth(2); w.setframerate(8000); w.writeframes(b'\0\0' * 80)
            (root / 'manifest.json').write_text(json.dumps(man))
            bank = V.FillerBank(root, chroot_prefix=d, state_file=Path(d) / 'last.json')
            seen = []
            for _ in range(12):
                path, info = bank.pick('what is the weather today')
                self.assertEqual(info['category'], 'search')
                self.assertTrue(path.startswith('/vf/en/search/'), path)
                seen.append(info['n'])
            self.assertTrue(all(a != b for a, b in zip(seen, seen[1:])), seen)
            path, info = bank.pick('hmm', second=True)
            self.assertEqual(info['category'], 'general')
            path, info = bank.pick('γεια σου')           # no el clips -> None, no crash
            self.assertIsNone(path)

    def test_thinking_speaker(self):
        t = [0.0]
        sp = V.ThinkingSpeaker(min_gap_s=4, now=lambda: t[0])
        out = sp.push('Let me check the battery level first. Then I will report it back to you.\n\n')
        self.assertTrue(out and out.startswith('Hmm, let me think'))
        t[0] = 1.0
        self.assertIsNone(sp.push('Second paragraph is here and long enough.\n\n'))
        t[0] = 5.0
        self.assertTrue(sp.push('Third paragraph long enough to say.\n\n'))
        sp.answer_started()
        t[0] = 20.0
        self.assertIsNone(sp.push('More thinking that should be silent.\n\n'))


if __name__ == '__main__':
    unittest.main()
