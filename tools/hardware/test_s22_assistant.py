#!/usr/bin/env python3
"""Host-only tests for s22-assistant.py (no device access)."""
import importlib.util, json, tempfile, unittest
from pathlib import Path
from unittest import mock

SPEC = importlib.util.spec_from_file_location('s22_assistant', Path(__file__).with_name('s22-assistant.py'))
MOD = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(MOD)
CFG = dict(MOD.DEFAULTS, asr='phone')   # host tests never reach the rig


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
        self.a.say = lambda text='', wav=None, wait=True, filler=False: self.said.append(text or wav)

    def tearDown(self):
        self.chroot.stop(); self.td.cleanup()

    def log(self):
        return [json.loads(l) for l in (self.base / 'assistant.jsonl').read_text().splitlines()]

    def test_text_round_trip_is_spoken_and_logged(self):
        self.a.ask = lambda text, still_working=True: '**Four.** Two plus two is four.'
        self.assertEqual(self.a.run(text='what is two plus two'), 0)
        self.assertEqual(self.said, ['Four. Right… Two plus two is four.'])   # chatty filler (seeded)
        rec = self.log()[-1]
        self.assertEqual((rec['result'], rec['transcript'], rec['reply_chars']),
                         ('ok', 'what is two plus two', 31))
        self.assertIn('total_ms', rec)

    def test_list_answer_is_spoken_as_prose(self):
        self.a.ask = lambda text, still_working=True: 'You have:\n- 80% battery\n- Wi-Fi on\n- Bluetooth off'
        self.assertEqual(self.a.run(text='status'), 0)
        spoken = self.said[0]
        self.assertTrue(spoken.startswith('You have. First, 80 percent battery.'), spoken)
        self.assertIn('And finally, bluetooth off.', spoken)
        self.assertNotIn('- ', spoken)

    def test_filler_plays_while_waiting_and_finishes_before_answer(self):
        import time as _t
        order = []

        class Proc:
            def __init__(s, label): s.label = label; s.t = _t.time()
            def poll(s): return None if _t.time() - s.t < 0.25 else 0
            def wait(s, timeout=None):
                _t.sleep(max(0, 0.25 - (_t.time() - s.t))); order.append(('done', s.label))

        def say(text='', wav=None, wait=True, filler=False):
            order.append(('say', wav or text)); return Proc(wav or text) if not wait else None
        class Bank:
            n = 0
            def pick(s, q, second=False, lang=None):
                s.n += 1; return f'/f{s.n}.wav', {'category': 'search', 'second': second}
        def slow_ask(text, still_working=True):
            _t.sleep(1.3); return 'Answer.'
        cfg = dict(CFG, filler_delay_s=0.2, filler_second_s=0.5, filler_repeat_s=10, speak_thinking=False)
        a = MOD.Assistant(cfg, speak=True, base=self.base)
        a.say = say; a.ask = slow_ask; a.filler_bank = lambda: Bank()
        self.assertEqual(a.ask_with_fillers('what is the weather'), 'Answer.')
        kinds = [e['kind'] for e in a.rec['fillers']]
        self.assertEqual(kinds[:2], ['filler', 'filler'])
        self.assertTrue(a.rec['fillers'][0]['at_s'] >= 0.2)
        self.assertEqual(order[0], ('say', '/f1.wav'))
        self.assertEqual([o for o in order if o[0] == 'say'], [('say', '/f1.wav'), ('say', '/f2.wav')][:len([o for o in order if o[0] == 'say'])])
        self.assertGreaterEqual(len([o for o in order if o[0] == 'say']), 2)

    def test_long_answer_is_split_and_ordered(self):
        calls = []
        class Proc:
            def wait(s, timeout=None): calls.append('first-done')
        a = MOD.Assistant(dict(CFG), speak=True, base=self.base)
        a._render = lambda text, name: (calls.append(('render', name)) or f'/tmp/{name}')
        a.say = lambda text='', wav=None, wait=True, filler=False: (calls.append(('say', wav or text)), Proc() if not wait else None)[1]
        long_text = 'This is the first sentence of the answer. ' + 'And the rest goes on for a good while longer. ' * 4
        a.speak_answer(long_text.strip())
        n = len(MOD.speech_chunks(long_text.strip()))
        self.assertGreater(n, 2)
        expected = [('render', 'answer1.wav'), ('say', '/tmp/answer1.wav')]
        for i in range(2, n + 1):           # chunk i renders while chunk i-1 plays, then plays after it
            expected += [('render', f'answer{i}.wav'), 'first-done', ('say', f'/tmp/answer{i}.wav')]
        self.assertEqual(calls, expected + ['first-done'])
        calls.clear()
        a.speak_answer('Short answer.')
        self.assertEqual(calls, [('render', 'answer1.wav'), ('say', '/tmp/answer1.wav'), 'first-done'])

    def test_speech_chunks_short_first_then_never_longer_than_the_one_playing(self):
        text = 'This is the first sentence of the answer. ' + ' '.join(f'Sentence number {i} goes on for a while.' for i in range(12))
        chunks = MOD.speech_chunks(text)
        self.assertEqual(chunks[0], 'This is the first sentence of the answer.')
        for prev, nxt in zip(chunks, chunks[1:]):
            self.assertLessEqual(len(nxt), max(50, len(prev)))
        self.assertGreater(len(chunks), 2)
        self.assertEqual(' '.join(chunks), text.strip())
        self.assertEqual(MOD.speech_chunks('Short.'), ['Short.'])
        long_first = ('Well, when you think about it carefully, the long first sentence keeps going, and going, '
                      'with many clauses, until the very end of the line. Then a short one.')
        chunks = MOD.speech_chunks(long_first)
        self.assertLessEqual(len(chunks[0]), 70)
        self.assertEqual(' '.join(chunks), long_first)

    def test_slow_first_render_is_covered_by_gap_fillers(self):
        import time as _t
        played = []

        class Proc:
            def __init__(s): s.t = _t.time()
            def poll(s): return None if _t.time() - s.t < 0.1 else 0
            def wait(s, timeout=None): pass

        class Bank:
            def pick(s, q, second=False, lang=None):
                played.append((q, second, lang)); return f'/g{len(played)}.wav', {'category': 'general', 'lang': lang}
        a = MOD.Assistant(dict(CFG, gap_filler_s=0.2, gap_fillers_max=2), speak=True, base=self.base)
        a.filler_bank = lambda: Bank()
        a.question = 'Τι ώρα είναι;'
        a.say = lambda text='', wav=None, wait=True, filler=False: Proc()
        a._render = lambda text, name: (_t.sleep(0.9), f'/tmp/{name}')[1]
        self.assertEqual(a._render_covered('Είναι δώδεκα.', 'answer1.wav'), '/tmp/answer1.wav')
        self.assertEqual(played, [('Τι ώρα είναι;', True, 'el')] * 2)        # capped at gap_fillers_max
        self.assertEqual([f['kind'] for f in a.rec['fillers']], ['gap', 'gap'])
        played.clear()
        a._render = lambda text, name: f'/tmp/{name}'                          # fast render: no filler
        a._render_covered('x', 'answer1.wav')
        self.assertEqual(played, [])

    def test_reply_language_hint_follows_the_utterance(self):
        calls = []
        a = MOD.Assistant(dict(CFG), speak=False, base=self.base)
        a._http = lambda m, path, body=None, timeout=30: (calls.append(body), (200, {'reply': 'ok'}))[1]
        a.ask('Τι ώρα είναι;', still_working=False)
        self.assertEqual(calls[-1]['context'][1]['content'], MOD.REPLY_LANGUAGE['el'])
        a.ask('what time is it', still_working=False)
        self.assertEqual(calls[-1]['context'][1]['content'], MOD.REPLY_LANGUAGE['en'])
        a.lang = 'el'                                     # the rig ASR heard Greek (even in Latin letters)
        a.ask('ti ora einai', still_working=False)
        self.assertEqual(calls[-1]['context'][1]['content'], MOD.REPLY_LANGUAGE['el'])
        self.assertEqual(calls[-1]['context'][0]['content'], MOD.VOICE_CONTEXT)

    def test_greek_fixed_phrases(self):
        self.assertEqual(MOD.text_lang('Γεια σου τι κάνεις'), 'el')
        self.assertEqual(MOD.text_lang('hello OpenUnum'), 'en')
        self.assertTrue(MOD.summarize('Μια πρόταση. ' * 80, 100, 'el')[0].endswith(MOD.PHRASES['el']['more']))
        self.assertEqual(MOD.phrase('moment', 'el'), 'Μισό λεπτό.')
        self.assertEqual(MOD.phrase('moment', None), 'One moment.')

    def test_transcribe_prefers_rig_and_falls_back_to_phone(self):
        class RV:
            class RigUnavailable(Exception):
                pass
            ok = True
            @staticmethod
            def load_config(): return {}
            @classmethod
            def transcribe(cls, path, cfg):
                if not cls.ok:
                    raise cls.RigUnavailable('down')
                return {'text': 'Καλημέρα', 'lang': 'el', 'ms': 900}
        a = MOD.Assistant(dict(CFG, asr='rig'), speak=False, base=self.base)
        with mock.patch.object(MOD, 'RV', RV), \
                mock.patch.object(MOD.subprocess, 'run', lambda *x, **k: mock.Mock(
                    returncode=0, stdout='{"text": "good morning"}', stderr='')):
            self.assertEqual(a.transcribe('/tmp/x.wav'), 'Καλημέρα')
            self.assertEqual((a.lang, a.rec['asr_engine']), ('el', 'rig-whisper'))
            RV.ok = False
            self.assertEqual(a.transcribe('/tmp/x.wav'), 'good morning')
            self.assertEqual((a.lang, a.rec['asr_engine'], a.rec['asr_rig_error']), (None, 'parakeet', 'down'))

    def test_split_first_chunk(self):
        f = MOD.split_first_chunk
        self.assertEqual(f('Short answer here.'), ('Short answer here.', ''))
        long_a = 'For a good night of sleep, keep your room cool and dark, put screens away before bed, and try a calming routine. What time are you going to bed tonight?'
        first, rest = f(long_a)
        self.assertEqual(first, 'For a good night of sleep, keep your room cool and dark,')
        self.assertTrue(rest.startswith('put screens'))
        s = 'One short sentence that is long enough to count. ' + 'And a second one that goes on and on for a while. ' * 3
        self.assertEqual(f(s.strip())[0], 'One short sentence that is long enough to count.')
        no_break = 'word ' * 60
        self.assertEqual(f(no_break.strip()), (no_break.strip(), ''))

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



class CardTests(unittest.TestCase):
    """The Agent card modes (--card listen / --card turn): JSON event lines, no waiting on playback."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory(); self.base = Path(self.td.name)
        self.patches = [mock.patch.object(MOD, k, v) for k, v in {
            'CHROOT': self.base / 'chroot', 'BASE': self.base, 'LOCK': self.base / 'a.lock',
            'CARD_LISTEN_LOCK': self.base / 'l.lock', 'SAY_LOCK': self.base / 'say.lock',
            'CARD_REC_STOP': self.base / 'stop', 'CARD_REC_READY': self.base / 'ready'}.items()]
        for p in self.patches:
            p.start()
        self.events = []
        self.emit = mock.patch.object(MOD, 'emit', lambda **kw: self.events.append(kw)); self.emit.start()
        self.hap = mock.patch.object(MOD, 'haptic', lambda w: self.events.append({'haptic': w})); self.hap.start()

    def tearDown(self):
        for p in self.patches + [self.emit, self.hap]:
            p.stop()
        self.td.cleanup()

    def test_card_cue_never_waits_for_a_running_answer(self):
        a = MOD.Assistant(dict(CFG), speak=True, base=self.base); a.card = True
        said = []
        a.say = lambda text='', wav=None, wait=True, filler=False: said.append((wav, wait))
        with mock.patch.object(MOD, 'say_busy', lambda: True):
            a.cue()
        self.assertEqual(said, [])
        self.assertEqual(self.events, [{'haptic': 'buzz'}])
        with mock.patch.object(MOD, 'say_busy', lambda: False):
            a.cue()
        self.assertEqual(said, [(f'{MOD.ASR_DIR}/listen.wav', False)])

    def test_say_busy_reads_the_s22_say_lock(self):
        import fcntl
        self.assertFalse(MOD.say_busy())
        with open(MOD.SAY_LOCK, 'w') as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            import subprocess, sys
            code = ('import importlib.util,sys;from pathlib import Path;'
                    f'S=importlib.util.spec_from_file_location("m",{str(Path(MOD.__file__))!r});M=importlib.util.module_from_spec(S);'
                    f'S.loader.exec_module(M);M.SAY_LOCK=Path({str(MOD.SAY_LOCK)!r});print(M.say_busy())')
            out = subprocess.run([sys.executable, '-I', '-c', code], capture_output=True, text=True).stdout.strip()
        self.assertEqual(out, 'True')

    def test_card_listen_prints_the_transcript(self):
        with mock.patch.dict(MOD.os.environ, {}), mock.patch.object(MOD.Assistant, 'record', lambda self, wav: 2.5), \
             mock.patch.object(MOD.Assistant, 'transcribe', lambda self, wav: 'turn on the wifi'):
            self.assertEqual(MOD.card_listen(dict(CFG), str(self.base / 'rel')), 0)
        self.assertEqual(self.events[-1]['transcript'], 'turn on the wifi')
        self.assertIn({'state': 'transcribing', 'record_s': 2.5}, self.events)
        rec = json.loads((self.base / 'assistant.jsonl').read_text().splitlines()[-1])
        self.assertEqual((rec['mode'], rec['result']), ('card-listen', 'ok'))

    def test_card_turn_states_in_order(self):
        def fake_tools(session, stop, out=None, api=None):
            MOD.emit(state='tool', name='phone_device')
        spoken = []
        with mock.patch.object(MOD, 'tool_events', fake_tools), \
             mock.patch.object(MOD.Assistant, 'ensure_agent', lambda self: self.agent_ready.update(ok=True)), \
             mock.patch.object(MOD.Assistant, 'ask_with_fillers', lambda self, t: 'Wi-Fi is on now.'), \
             mock.patch.object(MOD.Assistant, 'speak_answer', lambda self, s: spoken.append(s)):
            self.assertEqual(MOD.card_turn(dict(CFG), 'turn on the wifi'), 0)
        states = [e.get('state') for e in self.events]
        self.assertEqual(states[0], 'thinking')
        self.assertIn('tool', states)
        self.assertEqual(states[-3:], ['reply', 'speaking', 'done'])
        self.assertEqual([e for e in self.events if e.get('state') == 'reply'][0]['text'], 'Wi-Fi is on now.')
        self.assertEqual(len(spoken), 1)

    def test_card_turn_error_is_reported(self):
        said = []
        with mock.patch.object(MOD, 'tool_events', lambda *a, **k: None), \
             mock.patch.object(MOD.Assistant, 'ensure_agent', lambda self: self.agent_ready.update(ok=False)), \
             mock.patch.object(MOD.Assistant, 'say', lambda self, text='', **k: said.append(text)):
            self.assertEqual(MOD.card_turn(dict(CFG), 'hello'), 1)
        self.assertEqual(self.events[-1]['state'], 'error')
        self.assertIn("couldn't reach", said[0])

    def test_tool_events_from_the_stream(self):
        import io
        body = (b'event: tool_call_started\ndata: {"tool":"phone_device"}\n\n'
                b'event: content_delta\ndata: {"token":"x"}\n\n'
                b'event: tool_call_completed\ndata: {"tool":"phone_device","resultOk":true}\n\n')
        got = []
        with mock.patch.object(MOD.urllib.request, 'urlopen', lambda url, timeout=0: io.BufferedReader(io.BytesIO(body))):
            MOD.tool_events('voice', MOD.threading.Event(), out=lambda **kw: got.append(kw))
        self.assertEqual(got, [{'state': 'tool', 'name': 'phone_device'}, {'state': 'thinking'}])

if __name__ == '__main__':
    unittest.main()
