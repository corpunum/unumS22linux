#!/usr/bin/env python3
"""Host-only tests for s22-assistant.py (no device access)."""
import importlib.util, json, tempfile, unittest
from pathlib import Path
from unittest import mock

SPEC = importlib.util.spec_from_file_location('s22_assistant', Path(__file__).with_name('s22-assistant.py'))
MOD = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(MOD)
CFG = dict(MOD.DEFAULTS)


class PureTests(unittest.TestCase):
    def test_parse_asr_takes_json_text(self):
        out = ('OfflineRecognizerConfig(...)\nCreating recognizer ...\n/tmp/a.wav\n'
               '{"lang": "", "emotion": "", "event": "", "text": " What time is it?", '
               '"timestamps": [], "tokens": []}\n----\nElapsed seconds: 0.1\n')
        self.assertEqual(MOD.parse_asr(out), 'What time is it?')
        self.assertEqual(MOD.parse_asr('no json here'), '')

    def test_asr_argv_pins_big_cores(self):
        argv = MOD.asr_argv('parakeet', '/tmp/x.wav')
        self.assertEqual(argv[:3], ['taskset', '-c', '4-7'])
        self.assertTrue(any('parakeet' in a and a.endswith('model.int8.onnx') for a in argv))
        self.assertEqual(argv[-1], '/tmp/x.wav')
        self.assertTrue(any('moonshine-merged-decoder' in a for a in MOD.asr_argv('moonshine', 'w')))
        with self.assertRaises(ValueError):
            MOD.asr_argv('nope', 'w')

    def test_stop_deadline(self):
        self.assertIsNone(MOD.stop_deadline(10.0, None, 11.0, CFG))
        # released while (or right after) the cue played: fixed window
        self.assertEqual(MOD.stop_deadline(10.0, 10.4, 10.5, CFG), 10.0 + CFG['fixed_s'])
        self.assertEqual(MOD.stop_deadline(10.0, 9.5, 10.5, CFG), 10.0 + CFG['fixed_s'])
        # held while speaking: stop shortly after the release
        self.assertAlmostEqual(MOD.stop_deadline(10.0, 14.0, 14.05, CFG), 14.0 + CFG['tail_s'])
        self.assertEqual(MOD.stop_deadline(10.0, 14.0, 20.0, CFG), 20.0)

    def test_clean_for_speech(self):
        md = ('# Weather\n\n**Athens** tomorrow:\n- Sunny, `24 C`\n- See [the forecast](https://x.y/z)\n'
              '```\ncode()\n```\n| a | b |\n|---|---|\nMore at https://example.com now')
        s = MOD.clean_for_speech(md)
        for bad in ('#', '**', '`', '](', 'https', '---', '\n'):
            self.assertNotIn(bad, s)
        self.assertIn('Athens tomorrow:', s)
        self.assertIn('Sunny, 24 C. See the forecast', s)
        self.assertIn('(code omitted)', s)
        self.assertIn('a link', s)
        self.assertEqual(MOD.clean_for_speech('snake_case stays'), 'snake_case stays')
        self.assertEqual(MOD.clean_for_speech('High 26°C, 40% rain, 70 °F'),
                         'High 26 degrees Celsius, 40 percent rain, 70 degrees Fahrenheit')

    def test_summarize(self):
        self.assertEqual(MOD.summarize('Short.', 100), ('Short.', False))
        text = ' '.join(f'Sentence number {i} is here.' for i in range(40))
        spoken, cut = MOD.summarize(text, 120)
        self.assertTrue(cut)
        self.assertTrue(spoken.endswith('The full answer is in the voice chat.'))
        self.assertLessEqual(len(spoken.split(' The full')[0]), 120)
        spoken, cut = MOD.summarize('word ' * 200, 50)
        self.assertTrue(cut and len(spoken) < 120)

    def test_load_config_file_and_env(self):
        with tempfile.TemporaryDirectory() as td:
            Path(td, 'assistant.json').write_text(json.dumps({'fixed_s': 8, 'session': 'v2'}))
            with mock.patch.dict(MOD.os.environ, {'S22_ASR_ENGINE': 'moonshine'}):
                cfg = MOD.load_config(Path(td))
        self.assertEqual((cfg['fixed_s'], cfg['session'], cfg['engine']), (8, 'v2', 'moonshine'))
        self.assertEqual(cfg['max_s'], MOD.DEFAULTS['max_s'])


class RunTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory(); self.base = Path(self.td.name)
        self.chroot = mock.patch.object(MOD, 'CHROOT', self.base / 'chroot'); self.chroot.start()
        self.a = MOD.Assistant(dict(CFG), speak=False, base=self.base)
        self.a.ensure_agent = lambda: self.a.agent_ready.update(ok=True, ms=1)
        self.said = []
        self.a.say = lambda text='', wav=None, wait=True: self.said.append(text or wav)

    def tearDown(self):
        self.chroot.stop(); self.td.cleanup()

    def log(self):
        return [json.loads(l) for l in (self.base / 'assistant.jsonl').read_text().splitlines()]

    def test_text_round_trip_is_spoken_and_logged(self):
        self.a.ask = lambda text: '**Four.** Two plus two is four.'
        self.assertEqual(self.a.run(text='what is two plus two'), 0)
        self.assertEqual(self.said, ['Four. Two plus two is four.'])
        rec = self.log()[-1]
        self.assertEqual((rec['result'], rec['transcript'], rec['reply_chars']),
                         ('ok', 'what is two plus two', 31))
        self.assertIn('total_ms', rec)

    def test_empty_transcript(self):
        self.a.transcribe = lambda wav: ''
        self.assertEqual(self.a.run(wav='/tmp/x.wav'), 0)
        self.assertEqual(self.said, ["Sorry, I didn't catch that."])
        self.assertEqual(self.log()[-1]['result'], 'empty')

    def test_unreachable_agent_is_spoken(self):
        self.a.ensure_agent = lambda: self.a.agent_ready.update(ok=False, ms=5)
        self.assertEqual(self.a.run(text='hello'), 1)
        self.assertEqual(self.said, ["Sorry, I couldn't reach the assistant."])
        self.assertIn('ConnectionError', self.log()[-1]['error'])

    def test_ask_polls_pending(self):
        calls = []
        replies = iter([(202, {'pending': True}), (200, {'pending': True}),
                        (200, {'pending': False, 'completed': True, 'reply': 'Done.'})])
        def http(method, path, body=None, timeout=30):
            calls.append((method, path, body)); return next(replies)
        self.a._http = http
        with mock.patch.object(MOD.time, 'sleep'):
            self.assertEqual(self.a.ask('hi'), 'Done.')
        self.assertEqual(calls[0][0], 'POST')
        self.assertEqual(calls[0][2]['sessionId'], 'voice')
        self.assertEqual(calls[0][2]['context'][0]['content'], MOD.VOICE_CONTEXT)
        self.assertTrue(calls[1][1].startswith('/api/chat/pending?sessionId=voice'))

    def test_ask_falls_back_to_history_when_consumed(self):
        replies = iter([(202, {'pending': True}), (200, {'pending': False}),
                        (200, {'messages': [{'role': 'user', 'content': 'hi'},
                                            {'role': 'assistant', 'content': 'From history.'}]})])
        self.a._http = lambda *a, **k: next(replies)
        with mock.patch.object(MOD.time, 'sleep'):
            self.assertEqual(self.a.ask('hi'), 'From history.')


if __name__ == '__main__':
    unittest.main()
