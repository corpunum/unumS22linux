#!/usr/bin/env python3
"""Host-only tests for s22-converse.py (no device access, no audio).

The whole loop runs against fake commands: a fake `chroot` + ASR that return
scripted transcripts, a fake s22-say that records whether it rendered or
played, and a fake OpenUnum HTTP layer.
"""
import array
import fcntl
import importlib.util
import json
import math
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location('s22_converse', HERE / 's22-converse.py')
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)
A = MOD.A
RATE = 16000


def tone(seconds, amp=8000, freq=220, rate=RATE):
    n = int(seconds * rate)
    return array.array('h', (int(amp * math.sin(2 * math.pi * freq * i / rate)) for i in range(n)))


def quiet(seconds, amp=30, rate=RATE):
    n = int(seconds * rate)
    return array.array('h', ((amp if i % 2 else -amp) for i in range(n)))


def cfg(**over):
    c = dict(MOD.CONVERSE_DEFAULTS)
    c.update(over)
    return c


class PureTests(unittest.TestCase):
    def test_exit_phrases(self):
        for t in ('Stop.', 'Goodbye!', 'OK, goodbye.', 'Bye bye', "That's all, thanks",
                  'τέλος', 'Stop please', 'End the conversation.'):
            self.assertTrue(MOD.is_exit_phrase(t), t)
        for t in ('How do I stop the timer on my phone?', 'Say goodbye to my mother for me tomorrow',
                  '', 'What is the weather', 'stopwatch'):
            self.assertFalse(MOD.is_exit_phrase(t), t)

    def test_frame_db(self):
        self.assertEqual(MOD.frame_db([]), -120.0)
        self.assertEqual(MOD.frame_db([0] * 10), -120.0)
        self.assertAlmostEqual(MOD.frame_db([32767, -32767] * 5), 0.0, places=2)
        self.assertLess(MOD.frame_db(quiet(0.02)), -55)

    def test_vad_finds_one_utterance_with_preroll(self):
        vad = MOD.EnergyVAD(cfg(), RATE)
        sig = quiet(1.0) + tone(1.2) + quiet(1.5)
        utts = []
        for i in range(0, len(sig), 800):                  # streamed in 50 ms chunks
            utts += vad.feed(sig[i:i + 800])
        self.assertEqual(len(utts), 1)
        dur = len(utts[0]) / RATE
        # speech 1.2 s + pre-roll 0.3 s + end silence 0.9 s, frame-quantised
        self.assertGreater(dur, 1.2 + 0.3 + 0.8)
        self.assertLess(dur, 1.2 + 0.3 + 1.0)
        self.assertFalse(vad.in_speech)
        self.assertLess(vad.noise, -55)

    def test_vad_ignores_clicks_and_hum(self):
        vad = MOD.EnergyVAD(cfg(), RATE)
        self.assertEqual(vad.feed(quiet(0.5) + tone(0.12) + quiet(1.5)), [])   # click
        vad = MOD.EnergyVAD(cfg(), RATE)
        self.assertEqual(vad.feed(tone(2.0, amp=60)), [])                       # steady hum near floor

    def test_vad_caps_long_utterance_and_flush(self):
        vad = MOD.EnergyVAD(cfg(max_utterance_s=1.0), RATE)
        utts = vad.feed(quiet(0.3) + tone(2.5))
        self.assertGreaterEqual(len(utts), 1)
        self.assertLessEqual(len(utts[0]) / RATE, 1.01)
        vad = MOD.EnergyVAD(cfg(), RATE)
        self.assertEqual(vad.feed(quiet(0.3) + tone(0.8)), [])
        self.assertEqual(len(vad.flush()), 1)                 # input ended mid-utterance

    def test_raw_to_mono_takes_slot0(self):
        raw = array.array('h', [1, 9, 9, 9, 2, 9, 9, 9, 3, 9]).tobytes()
        mono, rest = MOD.raw_to_mono(raw)
        self.assertEqual(list(mono), [1, 2])
        self.assertEqual(len(rest), 4)

    def test_wav_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'x' / 'a.wav'
            MOD.write_wav(p, tone(0.1), RATE)
            rate, s = MOD.read_wav(p)
            self.assertEqual((rate, len(s)), (RATE, int(0.1 * RATE)))

    def test_lock_holders_parses_proc_locks(self):
        with tempfile.NamedTemporaryFile() as f:
            ino = os.stat(f.name).st_ino
            text = (f'1: FLOCK  ADVISORY  WRITE 4242 fd:01:{ino} 0 EOF\n'
                    f'2: POSIX  ADVISORY  WRITE 77 fd:01:{ino} 0 EOF\n'
                    f'3: FLOCK  ADVISORY  WRITE 99 fd:01:{ino + 1} 0 EOF\n')
            self.assertEqual(MOD.lock_holders(Path(f.name), text), [4242])


class LockTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 's22-assistant.lock'

    def tearDown(self):
        self.tmp.cleanup()

    def test_free_lock_records_pid(self):
        f, info = MOD.acquire_lock(self.path, holders=lambda p: [])
        self.assertIsNone(info)
        self.assertEqual(self.path.read_text().strip(), str(os.getpid()))
        f.close()

    def _hold(self, pid_text):
        other = open(self.path, 'w')
        other.write(pid_text)
        other.flush()
        fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.addCleanup(other.close)
        return os.stat(self.path).st_ino

    def test_live_holder_is_busy(self):
        self._hold(f'{os.getppid()}\n')
        f, info = MOD.acquire_lock(self.path, holders=lambda p: [])
        self.assertIsNone(f)
        self.assertEqual(info['live'], [os.getppid()])

    def test_stale_lock_of_dead_pid_is_replaced(self):
        old_ino = self._hold('999999999\n')          # holder "died" but the fd leaked
        f, info = MOD.acquire_lock(self.path, holders=lambda p: [999999998])
        self.assertIsNone(info)
        self.assertNotEqual(os.stat(self.path).st_ino, old_ino)
        self.assertEqual(self.path.read_text().strip(), str(os.getpid()))
        f.close()

    def test_unknown_holder_is_not_broken(self):
        self._hold('')                               # e.g. s22-assistant (writes no pid)
        f, info = MOD.acquire_lock(self.path, holders=lambda p: [])
        self.assertIsNone(f)


FAKE_CHROOT = """#!/bin/sh
root=$1; shift
FAKE_ROOT=$root exec "$@"
"""

FAKE_ASR = """#!/usr/bin/env python3
import json, os, sys
wav = sys.argv[-1]
assert os.path.exists(os.environ['FAKE_ROOT'] + wav), wav
script = os.environ['FAKE_ASR_SCRIPT']
items = json.load(open(script))
text = items.pop(0) if items else ''
json.dump(items, open(script, 'w'))
print(json.dumps({'text': ' ' + text, 'tokens': []}))
"""

FAKE_SAY = """#!/bin/sh
if [ -n "${S22_SAY_OUT:-}" ]; then
  printf 'RIFFfake' > "$S22_SAY_OUT"; echo "RENDER $*" >> "$FAKE_SAY_LOG"
else
  echo "PLAYED $*" >> "$FAKE_SAY_LOG"
fi
"""


class LoopTests(unittest.TestCase):
    """The full turn-taking loop in --silent-verify mode, all commands faked."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.root = root
        self.chroot = root / 'chroot'
        (self.chroot / 'tmp').mkdir(parents=True)
        bindir = root / 'bin'
        bindir.mkdir()
        for name, body in (('chroot', FAKE_CHROOT), ('fake-asr', FAKE_ASR), ('s22-say', FAKE_SAY)):
            p = bindir / name
            p.write_text(body)
            p.chmod(p.stat().st_mode | stat.S_IXUSR)
        self.say_log = root / 'say.log'
        self.script = root / 'asr.json'
        self.base = root / 'buttons'
        self.base.mkdir()
        env = {'PATH': f"{bindir}:{os.environ.get('PATH', '')}", 'FAKE_SAY_LOG': str(self.say_log),
               'FAKE_ASR_SCRIPT': str(self.script)}
        patches = [
            mock.patch.dict(os.environ, env),
            mock.patch.object(A, 'CHROOT', self.chroot),
            mock.patch.object(A, 'BIN', bindir),
            mock.patch.object(A, 'asr_argv', lambda engine, wav, asr_dir=A.ASR_DIR:
                              [sys.executable, str(bindir / 'fake-asr'), wav]),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.posts = []

    def tearDown(self):
        self.tmp.cleanup()

    def fake_http(self, method, path, body=None, timeout=30):
        if path == '/health':
            return 200, {'ok': True}
        if method == 'POST' and path == '/api/chat':
            self.posts.append(body)
            return 200, {'reply': f"**Answer {len(self.posts)}** to: {body['message']}",
                         'model': {'activeModel': 'openai/gpt-6-luna'}}
        raise AssertionError(f'unexpected {method} {path}')

    def wav(self, name, sig):
        p = self.root / 'in' / name
        MOD.write_wav(p, sig, RATE)
        return str(p)

    def run_conv(self, transcripts, inputs, **conv):
        self.script.write_text(json.dumps(transcripts))
        c = MOD.load_config(self.base)
        c['converse'].update(conv)
        conv_obj = MOD.Converser(c, inputs=inputs, out_dir=self.root / 'out', base=self.base)
        with mock.patch.object(MOD.Converser, '_http', side_effect=self.fake_http):
            return conv_obj, conv_obj.converse()

    def speech_wav(self, name):
        return self.wav(name, quiet(0.4) + tone(0.8) + quiet(1.2))

    def test_conversation_keeps_session_and_ends_on_exit_phrase(self):
        inputs = [self.speech_wav('1.wav'), self.speech_wav('2.wav'), self.speech_wav('3.wav'),
                  self.speech_wav('4.wav')]
        conv, out = self.run_conv(['What time is it?', 'And tomorrow?', 'Goodbye.', 'never'], inputs)
        self.assertEqual(out['ended'], 'exit_phrase')
        self.assertEqual(out['turns'], 3)
        self.assertEqual([p['message'] for p in self.posts], ['What time is it?', 'And tomorrow?'])
        sessions = {p['sessionId'] for p in self.posts}
        self.assertEqual(sessions, {conv.session})
        self.assertTrue(conv.session.startswith('voice-conv-'))
        for p in self.posts:
            self.assertNotIn('model', p)                        # OpenUnum's default (Luna)
            self.assertIn('hands-free spoken conversation', p['context'][0]['content'])
        self.assertEqual(A.VOICE_CONTEXT[:20], 'This message was spo')   # restored
        # zero audio: everything rendered, nothing played
        log = self.say_log.read_text().splitlines()
        self.assertTrue(log)
        self.assertFalse([l for l in log if l.startswith('PLAYED')])
        self.assertIn('RENDER Answer 1 to: What time is it?', log)
        self.assertEqual(log[-1], 'RENDER Goodbye.')
        for f in out['rendered']:
            self.assertTrue(Path(f).exists(), f)
        self.assertTrue((self.root / 'out' / 'turn01-heard.wav').exists())
        jl = [json.loads(l) for l in (self.base / 'converse.jsonl').read_text().splitlines()]
        self.assertEqual([r['result'] for r in jl], ['ok', 'ok', 'exit'])

    def test_idle_and_max_turns(self):
        _, out = self.run_conv([], [self.wav('q.wav', quiet(2.0))])
        self.assertEqual(out['ended'], 'idle')
        self.assertEqual(self.posts, [])
        self.assertIn('RENDER Ending the conversation.', self.say_log.read_text())
        self.say_log.unlink()
        inputs = [self.speech_wav(f'{i}.wav') for i in range(3)]
        _, out = self.run_conv(['one', 'two', 'three'], inputs, max_turns=2)
        self.assertEqual((out['ended'], len(self.posts)), ('max_turns', 2))

    def test_empty_transcript_is_not_sent(self):
        _, out = self.run_conv(['', 'stop'], [self.speech_wav('a.wav'), self.speech_wav('b.wav')])
        self.assertEqual(self.posts, [])
        self.assertEqual(out['ended'], 'exit_phrase')
        self.assertIn("RENDER Sorry, I didn't catch that.", self.say_log.read_text())


class SayRenderModeTest(unittest.TestCase):
    def test_render_branch_never_reaches_the_sound_card(self):
        src = (HERE / 's22-say.sh').read_text()
        start = src.index('if [ -n "${S22_SAY_OUT:-}" ]; then')
        end = src.index('\nfi\n', start)
        block = src[start:end]
        first_card_use = min(src.index(k, src.index('TTS=/opt/s22-tts'))
                             for k in ('batch defaults', 'batch "$ROUTE"', 'play "$WAV"', 'mknod'))
        self.assertLess(end, first_card_use)
        self.assertIn('exit', block)
        for bad in ('amixer', 'aplay', 'batch', 'play ', '/dev/snd', 'Volume'):
            self.assertNotIn(bad, block)


if __name__ == '__main__':
    unittest.main()
