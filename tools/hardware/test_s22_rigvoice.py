#!/usr/bin/env python3
"""Host tests for s22_rigvoice.py (the rig's owner voice + Greek/English ASR) and the s22-say "antonis" engine."""
from __future__ import annotations

import json
import math
import os
import tempfile
import threading
import time
import unittest
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
import sys
sys.path.insert(0, str(HERE))
import s22_rigvoice as RV  # noqa: E402


class Stub:
    """voxcpm (/v1/audio/speech/stream) + whisper-server (/inference) stand-in."""

    def __init__(self, delay=0.0, seconds=0.5, status=200, asr=None):
        self.delay, self.seconds, self.status = delay, seconds, status
        self.asr = list(asr or [])
        self.requests = []
        stub = self

        class H(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'

            def log_message(self, *a):
                pass

            def do_POST(self):
                body = self.rfile.read(int(self.headers['Content-Length']))
                stub.requests.append((self.path, body))
                if self.path.endswith('/inference'):
                    out = json.dumps(stub.asr.pop(0)).encode()
                    self.send_response(200)
                    self.send_header('Content-Length', str(len(out)))
                    self.end_headers()
                    self.wfile.write(out)
                    return
                self.send_response(stub.status)
                self.send_header('x-sample-rate', '48000')
                self.send_header('Transfer-Encoding', 'chunked')
                self.end_headers()
                time.sleep(stub.delay)
                n = int(48000 * stub.seconds) if stub.status == 200 else 0
                pcm = b''.join((int(3000 * math.sin(2 * math.pi * 220 * i / 48000)) & 0xFFFF).to_bytes(2, 'little')
                               for i in range(n))
                try:
                    for i in range(0, len(pcm), 9601):
                        c = pcm[i:i + 9601]
                        self.wfile.write(b'%x\r\n%s\r\n' % (len(c), c))
                        self.wfile.flush()
                    self.wfile.write(b'0\r\n\r\n')
                except (BrokenPipeError, ConnectionResetError):
                    pass

        self.srv = ThreadingHTTPServer(('127.0.0.1', 0), H)
        self.url = f'http://127.0.0.1:{self.srv.server_address[1]}'
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


class RigVoiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self._down = RV.DOWN_DIR
        RV.DOWN_DIR = Path(self.tmp.name)
        self.addCleanup(setattr, RV, 'DOWN_DIR', self._down)

    def cfg(self, stub, **kw):
        return dict(RV.DEFAULTS, tts_url=stub.url, asr_url=stub.url, **kw)

    def test_tts_writes_owner_voice_wav(self):
        s = Stub(seconds=0.5)
        self.addCleanup(s.close)
        out = Path(self.tmp.name) / 'a.wav'
        info = RV.tts_to_wav('Καλημέρα. Hello.', out, self.cfg(s))
        with wave.open(str(out)) as w:
            self.assertEqual((w.getframerate(), w.getnchannels(), w.getnframes()), (48000, 1, 24000))
        self.assertEqual(json.loads(s.requests[0][1]), {'input': 'Καλημέρα. Hello.', 'voice': 'antonis'})
        self.assertAlmostEqual(info['audio_s'], 0.5, places=2)
        self.assertTrue(info['normalised'])

    def test_late_first_audio_gives_up_and_marks_down(self):
        s = Stub(delay=1.0)
        self.addCleanup(s.close)
        c = self.cfg(s, ttfa_s=0.3)
        t0 = time.monotonic()
        with self.assertRaises(RV.RigUnavailable):
            RV.tts_pcm('Hello.', c)
        self.assertLess(time.monotonic() - t0, 0.9)
        self.assertTrue(RV.is_down('tts', c))
        n = len(s.requests)
        with self.assertRaises(RV.RigUnavailable):         # skipped without a request while marked down
            RV.tts_pcm('Hello.', c)
        self.assertEqual(len(s.requests), n)
        self.assertFalse(RV.is_down('tts', c, now=time.time() + c['down_s'] + 1))

    def test_unreachable_http_error_and_empty_audio(self):
        with self.assertRaises(RV.RigUnavailable):
            RV.tts_pcm('Hi.', dict(RV.DEFAULTS, tts_url='http://127.0.0.1:9', ttfa_s=0.3))
        for kw in ({'status': 500}, {'seconds': 0.01}):
            RV.clear_down('tts')
            s = Stub(**kw)
            self.addCleanup(s.close)
            with self.assertRaises(RV.RigUnavailable):
                RV.tts_pcm('Hi.', self.cfg(s))
        with self.assertRaises(RV.RigUnavailable):
            RV.tts_pcm('Hi.', dict(RV.DEFAULTS, enabled=False))

    def test_asr_greek_and_language_narrowing(self):
        wav = Path(self.tmp.name) / 'q.wav'
        RV.write_wav(wav, b'\x00\x00' * 4800, 48000)
        s = Stub(asr=[{'language': 'greek', 'text': ' Τι ώρα είναι;\n'},
                      {'language': 'italian', 'text': 'x', 'language_probabilities': {'it': 0.5, 'en': 0.3, 'el': 0.2}},
                      {'language': 'english', 'text': ' What time is it?'}])
        self.addCleanup(s.close)
        r = RV.transcribe(wav, self.cfg(s))
        self.assertEqual((r['text'], r['lang'], r['redo']), ('Τι ώρα είναι;', 'el', False))
        self.assertIn(b'name="language"\r\n\r\nauto', s.requests[0][1])
        r = RV.transcribe(wav, self.cfg(s))
        self.assertEqual((r['text'], r['lang'], r['redo']), ('What time is it?', 'en', True))
        self.assertIn(b'name="language"\r\n\r\nen', s.requests[2][1])

    def test_asr_unreachable_marks_down(self):
        wav = Path(self.tmp.name) / 'q.wav'
        RV.write_wav(wav, b'\x00\x00' * 480, 48000)
        c = dict(RV.DEFAULTS, asr_url='http://127.0.0.1:9', asr_connect_s=0.3)
        with self.assertRaises(RV.RigUnavailable):
            RV.transcribe(wav, c)
        self.assertTrue(RV.is_down('asr', c))

    def test_fast_normalise_hits_target(self):
        import array
        import math
        import s22_loudnorm as LN
        for amp in (0.06, 0.1, 0.3):            # quieter than ~-36 LUFS hits the 20 dB gain cap, as in s22_loudnorm
            a = array.array('h', [int(amp * 32767 * math.sin(2 * math.pi * 220 * i / 48000)
                                      * (0.6 + 0.4 * math.sin(2 * math.pi * 3 * i / 48000))) for i in range(48000 * 2)])
            out, info = RV.normalise(a.tobytes(), 48000)
            b = array.array('h')
            b.frombytes(out)
            full = LN.measure_lufs([v / 32768.0 for v in b], 48000, max_s=0)
            self.assertTrue(info['normalised'])
            self.assertLess(abs(full - LN.TARGET_LUFS), 0.6, (amp, full, info))
            self.assertLessEqual(max(abs(v) for v in b), 32767 * 10 ** (-2 / 20) + 1)

    def test_pick_lang(self):
        self.assertEqual(RV.pick_lang('greek', {}, ['el', 'en']), ('el', False))
        self.assertEqual(RV.pick_lang('en', {}, ['el', 'en']), ('en', False))
        self.assertEqual(RV.pick_lang('ru', {'el': 0.3, 'en': 0.1}, ['el', 'en']), ('el', True))

    def test_config_file_overrides(self):
        p = Path(self.tmp.name) / 'rigvoice.json'
        p.write_text('{"tts_url": "http://100.76.5.104:1"}')
        c = RV.load_config(p)
        self.assertEqual(c['tts_url'], 'http://100.76.5.104:1')
        self.assertEqual(c['asr_url'], RV.DEFAULTS['asr_url'])
        os.environ['S22_RIGVOICE_LENIENT'] = '1'
        try:
            c = RV.load_config(p)
        finally:
            del os.environ['S22_RIGVOICE_LENIENT']
        self.assertEqual((c['down_s'], c['ttfa_s']), (0.0, 30.0))


class SayAntonisEngine(unittest.TestCase):
    src = (HERE / 's22-say.sh').read_text()

    def test_default_is_owner_voice_with_same_voice_fallback(self):
        self.assertIn('ENGINE=${S22_TTS_ENGINE:-antonis}', self.src)
        block = self.src[self.src.index('    antonis)'):self.src.index('    supertonic) S=')]
        self.assertIn('"$RIGVOICE_PY" tts', block)
        self.assertIn('S22_TTS_OFFLINE:-el-antonis', block)
        self.assertIn('S22_SAY_STRICT', block)
        # Greek routing to a phone engine only applies when the default is a phone engine
        self.assertIn('[ "$ENGINE" != antonis ]', self.src)

    def test_strict_mode_never_falls_back_to_espeak(self):
        for anchor in ('R=/tmp/s22-say-render-$$.wav', 'WAV=/tmp/s22-say-$$.wav'):
            i = self.src.index(anchor)
            block = self.src[i:self.src.index('espeak-ng', i)]
            self.assertIn('S22_SAY_STRICT', block)

    def test_every_render_records_its_voice(self):
        self.assertEqual(self.src.count('voice_log "${VOICE_USED:-$ENGINE}"'), 2)
        self.assertIn('[ "$VOICE_USED" = antonis-rig ] ||', self.src)   # rig renders come normalised


if __name__ == '__main__':
    unittest.main()
