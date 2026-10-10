#!/usr/bin/env python3
"""Hardware-free host tests for s22-sip.py (loopback UDP on 127.0.0.1 only)."""
import array
import importlib.util
import json
import math
import os
import random
import socket
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

SPEC = importlib.util.spec_from_file_location('s22_sip', Path(__file__).with_name('s22-sip.py'))
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


def quiet(*_a, **_k):
    pass


def noise(n, amp=60, seed=1):
    r = random.Random(seed)
    return array.array('h', [int(r.gauss(0, amp)) for _ in range(n)])


class G711Tests(unittest.TestCase):
    def test_ulaw_vectors(self):
        self.assertEqual(M.ulaw_encode_sample(0), 0xFF)
        self.assertEqual(M.ulaw_encode_sample(-32768), 0x00)
        self.assertEqual(M.ulaw_encode_sample(32767), 0x80)
        self.assertEqual(M.ulaw_decode_sample(0xFF), 0)
        self.assertEqual(M.ulaw_decode_sample(0x00), -32124)
        self.assertEqual(M.ulaw_decode_sample(0x80), 32124)

    def test_alaw_vectors(self):
        self.assertEqual(M.alaw_encode_sample(0), 0xD5)
        self.assertEqual(M.alaw_decode_sample(0xD5), 8)
        self.assertEqual(M.alaw_decode_sample(0x55), -8)
        self.assertEqual(M.alaw_encode_sample(32767), 0xAA)

    def test_roundtrip_error_is_bounded(self):
        for codec in ('PCMU', 'PCMA'):
            xs = list(range(-32768, 32768, 37))
            ys = M.g711_decode(M.g711_encode(xs, codec), codec)
            for x, y in zip(xs, ys):
                self.assertLessEqual(abs(x - y), max(16, abs(x) // 15), (codec, x, y))
            # every code decodes and re-encodes to itself (except µ-law's two zeros)
            for b in range(256):
                v = M.g711_decode(bytes([b]), codec)[0]
                if not (codec == 'PCMU' and b == 0x7F):
                    self.assertEqual(M.g711_encode([v], codec)[0], b, (codec, b))


class SipMessageTests(unittest.TestCase):
    INVITE = (b'INVITE sip:bob@biloxi.com SIP/2.0\r\n'
              b'v: SIP/2.0/UDP pc33.atlanta.com;branch=z9hG4bK776asdhds, SIP/2.0/UDP p2:5070;branch=z9hG4bKx\r\n'
              b'Max-Forwards: 70\r\n'
              b'To: Bob <sip:bob@biloxi.com>\r\n'
              b'f: "Alice, A" <sip:alice@atlanta.com>;tag=1928301774\r\n'
              b'i: a84b4c76e66710@pc33.atlanta.com\r\n'
              b'CSeq: 314159 INVITE\r\n'
              b'Subject: folded\r\n  header line\r\n'
              b'm: <sip:alice@pc33.atlanta.com>\r\n'
              b'c: application/sdp\r\n'
              b'l: 4\r\n\r\nv=0\r\nEXTRA')

    def test_parse_request_compact_and_folding(self):
        m = M.SipMessage.parse(self.INVITE)
        self.assertTrue(m.is_request)
        self.assertEqual((m.method, m.uri), ('INVITE', 'sip:bob@biloxi.com'))
        self.assertEqual(m.get('Call-ID'), 'a84b4c76e66710@pc33.atlanta.com')
        self.assertEqual(m.cseq, (314159, 'INVITE'))
        self.assertEqual(len(m.get_all('via')), 2)
        self.assertEqual(M.parse_via(m.get('via'))['params']['branch'], 'z9hG4bK776asdhds')
        self.assertEqual(M.tag_of(m.get('from')), '1928301774')
        self.assertEqual(M.parse_nameaddr(m.get('from'))[0], 'Alice, A')
        self.assertEqual(m.get('subject'), 'folded header line')
        self.assertEqual(m.body, b'v=0\r')

    def test_build_roundtrip_and_response(self):
        m = M.SipMessage('BYE sip:x@1.2.3.4:5062 SIP/2.0', [('Via', 'SIP/2.0/UDP 1.1.1.1;branch=z9hG4bK1'),
                                                          ('CSeq', '2 BYE')], 'hello')
        p = M.SipMessage.parse(m.to_bytes())
        self.assertEqual(p.get('content-length'), '5')
        self.assertEqual(p.body, b'hello')
        r = M.SipMessage.parse(b'SIP/2.0 401 Unauthorized\r\nCSeq: 1 REGISTER\r\n'
                               b'WWW-Authenticate: Digest realm="r", nonce="n", qop="auth,auth-int"\r\n\r\n')
        self.assertEqual((r.status, r.reason, r.method), (401, 'Unauthorized', 'REGISTER'))
        scheme, chal = M.parse_challenge(r.get('www-authenticate'))
        self.assertEqual((scheme, chal['realm'], chal['qop']), ('Digest', 'r', 'auth,auth-int'))
        with self.assertRaises(ValueError):
            M.SipMessage.parse(b'garbage\r\n\r\n')

    def test_uri_parse(self):
        u = M.parse_uri('sip:alice:secret@[fd7a::1]:5070;transport=udp')
        self.assertEqual((u['user'], u['host'], u['port'], u['params']['transport']),
                         ('alice', 'fd7a::1', 5070, 'udp'))
        self.assertEqual(M.parse_uri('sip:100.64.1.2')['port'], None)


class DigestTests(unittest.TestCase):
    def test_rfc2617_vector(self):
        r = M.digest_response('Mufasa', 'Circle Of Life', 'testrealm@host.com',
                              'dcd98b7102dd2f0e8b11d0f600bfb0c093', 'GET', '/dir/index.html',
                              qop='auth', nc='00000001', cnonce='0a4f113b')
        self.assertEqual(r, '6629fae49393a05397450978507c4ef1')

    def test_rfc2069_no_qop_and_header(self):
        import hashlib
        h = lambda s: hashlib.md5(s.encode()).hexdigest()
        exp = h(f"{h('u:r:p')}:n:{h('REGISTER:sip:example.org')}")
        self.assertEqual(M.digest_response('u', 'p', 'r', 'n', 'REGISTER', 'sip:example.org'), exp)
        v = M.authorization_value({'realm': 'r', 'nonce': 'n', 'qop': 'auth', 'opaque': 'o'},
                                  'u', 'p', 'REGISTER', 'sip:example.org', 1, 'c')
        self.assertIn('qop=auth', v); self.assertIn('nc=00000001', v)
        self.assertIn('opaque="o"', v); self.assertIn('cnonce="c"', v)
        sha = M.authorization_value({'realm': 'r', 'nonce': 'n', 'algorithm': 'SHA-256'},
                                    'u', 'p', 'INVITE', 'sip:x', 1)
        self.assertIn('algorithm=SHA-256', sha)


class SdpRtpTests(unittest.TestCase):
    def test_negotiate(self):
        offer = ('v=0\r\no=- 1 1 IN IP4 10.0.0.1\r\ns=-\r\nc=IN IP4 10.0.0.1\r\nt=0 0\r\n'
                 'm=audio 7078 RTP/AVP 96 8 0 101\r\nc=IN IP4 100.64.0.9\r\n'
                 'a=rtpmap:96 opus/48000/2\r\na=rtpmap:101 telephone-event/8000\r\n'
                 'm=video 9078 RTP/AVP 97\r\nc=IN IP4 1.1.1.1\r\n')
        s = M.parse_sdp(offer)
        self.assertEqual((s['ip'], s['port']), ('100.64.0.9', 7078))
        self.assertEqual(M.negotiate(s), (8, 'PCMA', 101))
        self.assertEqual(M.negotiate(s, ('PCMU',)), (0, 'PCMU', 101))
        self.assertIsNone(M.negotiate(M.parse_sdp('m=audio 1 RTP/AVP 96\r\na=rtpmap:96 opus/48000/2\r\n')))
        self.assertIsNone(M.negotiate(M.parse_sdp('c=IN IP4 1.2.3.4\r\nm=audio 0 RTP/AVP 0\r\n')))
        ours = M.parse_sdp(M.build_sdp('192.0.2.5', 40002))
        self.assertEqual(M.negotiate(ours), (0, 'PCMU', 101))
        self.assertEqual(ours['ip'], '192.0.2.5')

    def test_rtp_pack_unpack(self):
        pkt = M.rtp_pack(0, 65535, 2**32 - 1, 0xDEADBEEF, b'\xff' * 160, marker=True)
        p = M.rtp_unpack(pkt)
        self.assertEqual((p['pt'], p['seq'], p['ts'], p['ssrc'], p['marker'], len(p['payload'])),
                         (0, 65535, 2**32 - 1, 0xDEADBEEF, True, 160))
        # CSRC + header extension + padding
        raw = bytes([0x80 | 0x20 | 0x10 | 1, 8]) + b'\x00\x01' + b'\x00' * 8 + b'CSRC' + \
            b'\xbe\xde\x00\x01' + b'EXT!' + b'abc' + b'\x00\x00\x03'
        p = M.rtp_unpack(raw)
        self.assertEqual((p['pt'], p['payload']), (8, b'abc'))
        self.assertIsNone(M.rtp_unpack(b'\x00' * 12))

    def test_jitter_buffer(self):
        jb = M.JitterBuffer(depth=2)
        f = lambda i: bytes([i]) * 160
        out = []
        for seq in (65534, 0, 65535, 1, 1, 65533):        # reorder, wrap, dup, late
            out += jb.push(seq, seq * 160 % 2**32 if seq < 60000 else (seq - 65536) * 160 % 2**32, f(seq % 256))
        out += jb.flush()
        self.assertEqual([o[0] for o in out], [254, 255, 0, 1])
        self.assertEqual(jb.late, 2)
        # loss: seq 11 missing -> 160 samples of silence filled from the timestamp gap
        jb = M.JitterBuffer(depth=2)
        out = []
        for seq in (10, 12, 13, 14):
            out += jb.push(seq, seq * 160, f(seq))
        out += jb.flush()
        self.assertEqual(out, [f(10), 160, f(12), f(13), f(14)])
        self.assertEqual(jb.lost, 1)


class AudioTests(unittest.TestCase):
    def test_wav_formats_and_resample(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'a.wav'
            M.write_wav(p, 24000, M.tone(300, 0.5, 24000))
            rate, s = M.read_wav(p)
            self.assertEqual((rate, len(s)), (24000, 12000))
            r8 = M.resample(s, 24000, 8000)
            self.assertEqual(len(r8), 4000)
            self.assertGreater(M.goertzel(r8, 300), 0.04)
            # float32 stereo, written by hand
            import struct
            frames = b''.join(struct.pack('<ff', 0.5, -0.5) for _ in range(100))
            fmt = struct.pack('<HHIIHH', 3, 2, 16000, 16000 * 8, 8, 32)
            data = b'RIFF' + struct.pack('<I', 4 + 8 + len(fmt) + 8 + len(frames)) + b'WAVE' + \
                b'fmt ' + struct.pack('<I', len(fmt)) + fmt + b'data' + struct.pack('<I', len(frames)) + frames
            (Path(d) / 'f.wav').write_bytes(data)
            rate, s = M.read_wav(Path(d) / 'f.wav')
            self.assertEqual((rate, len(s), s[0]), (16000, 100, 0))

    def test_vad_segments(self):
        sig = array.array('h')
        sig += noise(8000)                                  # 1.0 s noise floor
        sig += M.tone(300, 0.8, amp=6000)                   # speech 1
        sig += noise(8000, seed=2)
        sig += M.tone(500, 0.5, amp=3000)                   # speech 2
        sig += noise(8000, seed=3)
        sig += M.tone(500, 0.06, amp=6000)                  # click: too short
        sig += noise(8000, seed=4)
        vad = M.EnergyVad(hangover_ms=400)
        events = []
        for i in range(0, len(sig), 137):                   # odd chunking on purpose
            events += vad.feed(sig[i:i + 137])
        segs = [e[1] for e in events if e[0] == 'segment']
        self.assertEqual(len(segs), 2, [e[0] for e in events])
        self.assertAlmostEqual(len(segs[0]) / 8000, 0.8 + 0.4 + 0.2, delta=0.15)
        self.assertGreater(M.goertzel(segs[1], 500), M.goertzel(segs[1], 300) * 10)
        self.assertIn(('drop',), events)

    def test_clean_and_template(self):
        self.assertEqual(M.clean_for_speech('**Hi** see [x](http://a) `ok`'), 'Hi see x ok')
        self.assertTrue(len(M.clean_for_speech('word. ' * 200, 100)) <= 100)
        argv = M.fill_template('tool --o={out} "{text}"', '/mnt/r', text="it's {fine}", out='/mnt/r/tmp/o.wav')
        self.assertEqual(argv, ['tool', '--o=/tmp/o.wav', "it's {fine}"])
        self.assertEqual(M.parse_asr_output('x\n{"text": " hello there", "tokens": []}\n'), 'hello there')
        self.assertEqual(M.parse_asr_output('plain words\n'), 'plain words')


# ------------------------------------------------------------------ live loopback

def mk_ua(**kw):
    kw.setdefault('bind_ip', '127.0.0.1')
    kw.setdefault('sip_port', 0)
    kw.setdefault('answer_delay', 0.05)
    ua = M.SipUA(log=quiet, **kw)
    ua.start()
    return ua


def wait_for(pred, timeout=8.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.02)
    return False


class LoopbackCallTests(unittest.TestCase):
    def test_call_tone_both_ways_and_dtmf(self):
        a, b = mk_ua(user='agent'), mk_ua(user='owner')
        try:
            got_a, got_b, digits = array.array('h'), array.array('h'), []

            def media_a(call):
                call.rtp.on_audio.append(got_a.extend)
                call.rtp.on_dtmf.append(digits.append)
                call.rtp.play(M.tone(600, 1.2))
            a.on_media.append(media_a)
            call = b.call(f'sip:agent@127.0.0.1:{a.port}', ring_timeout=5)
            self.assertIsNotNone(call)
            self.assertEqual(call.state, 'confirmed')
            call.rtp.on_audio.append(got_b.extend)
            call.rtp.play(M.tone(1000, 1.0))
            call.rtp.send_dtmf('4#')
            self.assertTrue(wait_for(lambda: len(got_a) >= 8000 * 1.3 and len(got_b) >= 8000 * 1.3))
            self.assertTrue(wait_for(lambda: digits == ['4', '#'], 3), digits)
            b.hangup(call)
            acall = next(iter(a.calls.values()))
            self.assertTrue(acall.ended.wait(3))
            self.assertEqual(acall.reason, 'remote hangup')
            self.assertEqual(call.reason, 'local hangup')
            self.assertGreater(M.goertzel(got_a[2000:6000], 1000), 0.01)
            self.assertGreater(M.goertzel(got_a[2000:6000], 1000), 20 * M.goertzel(got_a[2000:6000], 600))
            self.assertGreater(M.goertzel(got_b[2000:6000], 600), 20 * M.goertzel(got_b[2000:6000], 1000))
            self.assertGreater(call.rtp.rx, 50)
            self.assertEqual(call.rtp.jb.lost, 0)
            self.assertEqual(acall.rtp.jb.lost, 0)   # DTMF seq slots are not losses
        finally:
            a.close(); b.close()

    def test_allow_from_and_busy(self):
        a, b = mk_ua(user='agent', allow_from=['owner']), mk_ua(user='stranger')
        try:
            self.assertIsNone(b.call(f'sip:agent@127.0.0.1:{a.port}', ring_timeout=3))
            self.assertEqual(next(iter(b.calls.values())).reason, '403')
        finally:
            a.close(); b.close()
        a = mk_ua(user='agent', allow_from=['127.0.0.0/8'])
        b, c = mk_ua(user='owner'), mk_ua(user='owner2')
        try:
            first = b.call(f'sip:agent@127.0.0.1:{a.port}', ring_timeout=3)
            self.assertIsNotNone(first)
            self.assertIsNone(c.call(f'sip:agent@127.0.0.1:{a.port}', ring_timeout=3))
            self.assertEqual(next(iter(c.calls.values())).reason, '486')
            b.hangup(first)
        finally:
            a.close(); b.close(); c.close()


class InboundRegisterTests(unittest.TestCase):
    """Softphones register before calling; accept from allowed sources only."""

    def _register(self, port):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.bind(('127.0.0.1', 0)); s.settimeout(3)
        me = s.getsockname()[1]
        s.sendto((f'REGISTER sip:127.0.0.1 SIP/2.0\r\nVia: SIP/2.0/UDP 127.0.0.1:{me};branch=z9hG4bKreg1;rport\r\n'
                  'Max-Forwards: 70\r\nFrom: <sip:owner@127.0.0.1>;tag=t1\r\nTo: <sip:owner@127.0.0.1>\r\n'
                  'Call-ID: reg-test\r\nCSeq: 1 REGISTER\r\n'
                  f'Contact: <sip:owner@127.0.0.1:{me}>\r\nExpires: 7200\r\nContent-Length: 0\r\n\r\n').encode(),
                 ('127.0.0.1', port))
        try:
            return s.recv(4000).decode()
        finally:
            s.close()

    def test_register_accepted_from_allowed_and_refused_otherwise(self):
        a = mk_ua(user='agent', allow_from=['127.0.0.0/8'])
        try:
            r = self._register(a.port)
            self.assertTrue(r.startswith('SIP/2.0 200'), r)
            self.assertIn(';expires=3600', r)
            self.assertIn('Expires: 3600', r)
        finally:
            a.close()
        a = mk_ua(user='agent', allow_from=['10.0.0.0/8'])
        try:
            self.assertTrue(self._register(a.port).startswith('SIP/2.0 403'))
        finally:
            a.close()


class FakeRegistrar(threading.Thread):
    """Challenges REGISTER with 401 + MD5 qop=auth, accepts a correct response."""

    def __init__(self, password):
        super().__init__(daemon=True)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(('127.0.0.1', 0))
        self.sock.settimeout(0.2)
        self.port = self.sock.getsockname()[1]
        self.password, self.nonce = password, 'abc123nonce'
        self.ok = []
        self.running = True

    def run(self):
        while self.running:
            try:
                data, addr = self.sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                return
            req = M.SipMessage.parse(data)
            auth = req.get('authorization')
            ua = M.SipUA.__new__(M.SipUA)     # reuse make_response without sockets
            if not auth:
                r = M.SipUA.make_response(ua, req, 401, 'Unauthorized', 'reg1', addr=addr)
                r.add('WWW-Authenticate', f'Digest realm="test", nonce="{self.nonce}", qop="auth", algorithm=MD5')
            else:
                _, f = M.parse_challenge(auth)
                exp = M.digest_response(f['username'], self.password, f['realm'], f['nonce'], 'REGISTER',
                                        f['uri'], f.get('qop'), f.get('nc'), f.get('cnonce'))
                good = exp == f['response']
                r = M.SipUA.make_response(ua, req, 200 if good else 403, 'OK' if good else 'Forbidden',
                                          'reg1', addr=addr)
                if good:
                    r.add('Contact', req.get('contact') + ';expires=120')
                    self.ok.append(req.get('expires'))
            self.sock.sendto(r.to_bytes(), addr)


class RegisterTests(unittest.TestCase):
    def test_register_digest_and_unregister(self):
        reg = FakeRegistrar('s3cret'); reg.start()
        try:
            ua = mk_ua(user='agent', domain='test.invalid', password='s3cret', proxy=f'127.0.0.1:{reg.port}')
            self.assertTrue(ua.register())
            self.assertEqual((ua.registered, ua.reg_interval), (True, 120))
            self.assertIsNone(ua.nat_contact)
            ua.close()
            self.assertEqual(reg.ok, ['300', '0'])
            bad = mk_ua(user='agent', domain='test.invalid', password='wrong', proxy=f'127.0.0.1:{reg.port}')
            self.assertFalse(bad.register())
            bad.running = False; bad.sock.close()
        finally:
            reg.running = False; reg.sock.close()


class VoiceLoopTests(unittest.TestCase):
    def test_voice_loop_over_loopback(self):
        a, b = mk_ua(user='agent'), mk_ua(user='owner')
        heard, asked = [], []
        with tempfile.TemporaryDirectory() as d:
            loops = []

            def asr(wav):
                rate, s = M.read_wav(wav)
                heard.append((rate, len(s) / rate))
                return 'what time is it' if M.goertzel(s, 400, rate) > 0.005 else ''

            def chat(text):
                asked.append(text)
                return '**It is** noon. See https://example.com'

            def tts(text, out):
                M.write_wav(out, 22050, M.tone(700, 0.6, 22050))

            def media_a(call):
                loop = M.VoiceLoop(call.rtp, asr, chat, tts, d, vad=M.EnergyVad(hangover_ms=300),
                                   greeting=None, log=quiet)
                loop.attach(); loops.append(loop)
            a.on_media.append(media_a)
            try:
                call = b.call(f'sip:agent@127.0.0.1:{a.port}', ring_timeout=5)
                got = array.array('h')
                call.rtp.on_audio.append(got.extend)
                call.rtp.play(noise(4000) + M.tone(400, 0.7, amp=6000) + noise(8000))
                self.assertTrue(wait_for(lambda: loops and loops[0].turns, 10))
                self.assertTrue(wait_for(lambda: M.goertzel(got[-4000:], 700) > 0.01, 6))
                self.assertEqual(asked, ['what time is it'])
                turn = loops[0].turns[0]
                self.assertEqual((turn['result'], turn['reply']), ('ok', 'It is noon. See a link'))
                self.assertEqual(heard[0][0], 16000)
                self.assertAlmostEqual(heard[0][1], 0.7 + 0.3 + 0.2, delta=0.2)
                self.assertTrue((Path(loops[0].work) / 'turns.jsonl').exists())
                b.hangup(call)
            finally:
                for l in loops:
                    l.stop()
                a.close(); b.close()

    def test_pin_gate_and_command_backends(self):
        class FakeRtp:
            busy = False
            def __init__(self):
                self.on_audio, self.on_dtmf, self.played = [], [], []
            def play(self, s):
                self.played.append(len(s))
            def flush(self):
                pass
        with tempfile.TemporaryDirectory() as d:
            fake_asr = Path(d) / 'asr.py'
            fake_asr.write_text('import sys, json\nprint("log line")\nprint(json.dumps({"text": " hi " + str(len(sys.argv[1]) > 0)}))\n')
            fake_tts = Path(d) / 'tts.py'
            fake_tts.write_text(f'import sys\nsys.path.insert(0, {str(Path(__file__).parent)!r})\n'
                                'import importlib.util\n'
                                f'spec = importlib.util.spec_from_file_location("m", {str(Path(__file__).with_name("s22-sip.py"))!r})\n'
                                'm = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n'
                                'm.write_wav(sys.argv[1], 16000, m.tone(300, 0.25, 16000))\n')
            asr = M.command_asr([sys.executable, '-I', str(fake_asr), '{wav}'])
            tts = M.command_tts([sys.executable, '-I', '-B', str(fake_tts), '{out}', '{text}'])
            chat = M.command_chat([sys.executable, '-I', '-c', 'import sys; print("echo:", sys.argv[1])', '{text}'])
            self.assertEqual(asr('/x.wav'), 'hi True')
            self.assertEqual(chat('a b'), 'echo: a b')
            rtp = FakeRtp()
            loop = M.VoiceLoop(rtp, asr, chat, tts, d, pin='12', greeting='hello', log=quiet)
            loop.attach()
            try:
                loop.feed(M.tone(400, 1.0, amp=6000) + array.array('h', [0] * 8000))
                time.sleep(0.3)
                self.assertEqual(loop.turns, [])          # locked: speech ignored
                for dgt in '912':
                    loop.on_dtmf(dgt)
                self.assertFalse(loop.locked)
                self.assertTrue(wait_for(lambda: rtp.played, 10))
                self.assertEqual(rtp.played[0], 2000)       # 0.25 s at 8 kHz
                self.assertEqual(loop.turns[0]['spoken'], 'hello')
            finally:
                loop.stop()

    def test_openunum_chat_protocol(self):
        polls = []

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, obj):
                body = json.dumps(obj).encode()
                self.send_response(200); self.send_header('content-type', 'application/json')
                self.send_header('content-length', str(len(body))); self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                n = int(self.headers['content-length'])
                req = json.loads(self.rfile.read(n))
                polls.append(req)
                self._send({'pending': True})

            def do_GET(self):
                polls.append(self.path)
                self._send({'pending': len(polls) < 3} if len(polls) < 3 else {'reply': 'done'})
        srv = HTTPServer(('127.0.0.1', 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            chat = M.OpenUnumChat(f'http://127.0.0.1:{srv.server_port}', 'call-x', poll=0.01)
            self.assertEqual(chat('hello'), 'done')
            self.assertEqual(polls[0]['sessionId'], 'call-x')
            self.assertEqual(polls[0]['message'], 'hello')
            self.assertTrue(polls[1].startswith('/api/chat/pending?sessionId=call-x'))
        finally:
            srv.shutdown(); srv.server_close()


class MiniWsServer(threading.Thread):
    """Stdlib RFC 6455 server for one client: records frames, lets the test push frames back."""

    def __init__(self, token=None):
        super().__init__(daemon=True)
        self.srv = socket.socket(); self.srv.bind(('127.0.0.1', 0)); self.srv.listen(1)
        self.port = self.srv.getsockname()[1]
        self.got = []                 # (opcode, payload)
        self.conn = None
        self.up = threading.Event()
        self.start()

    def _read(self, n):
        buf = b''
        while len(buf) < n:
            c = self.conn.recv(n - len(buf))
            if not c:
                raise ConnectionError
            buf += c
        return buf

    def close(self):
        for sk in (self.conn, self.srv):
            try:
                sk.close()
            except (OSError, AttributeError):
                pass

    def push(self, op, payload=b''):
        n = len(payload)
        head = bytes([0x80 | op]) + (bytes([n]) if n < 126 else bytes([126]) + n.to_bytes(2, 'big'))
        self.conn.sendall(head + payload)

    def run(self):
        import base64, hashlib
        self.conn, _ = self.srv.accept()
        req = b''
        while b'\r\n\r\n' not in req:
            req += self.conn.recv(4096)
        key = [l.split(b':', 1)[1].strip() for l in req.split(b'\r\n') if l.lower().startswith(b'sec-websocket-key')][0]
        acc = base64.b64encode(hashlib.sha1(key + b'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').digest())
        self.conn.sendall(b'HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n'
                          b'Sec-WebSocket-Accept: ' + acc + b'\r\n\r\n')
        self.up.set()
        try:
            while True:
                b0, b1 = self._read(2)
                n = b1 & 0x7F
                if n == 126:
                    n = int.from_bytes(self._read(2), 'big')
                assert b1 & 0x80, 'client frames must be masked'
                mask = self._read(4)
                data = bytes(c ^ mask[i % 4] for i, c in enumerate(self._read(n)))
                if b0 & 0x0F == 0x8:
                    break
                self.got.append((b0 & 0x0F, data))
        except (ConnectionError, OSError):
            pass


class FakeRtp:
    busy = False

    def __init__(self):
        self.on_audio, self.on_dtmf, self.played, self.flushed = [], [], [], 0

    def play(self, s):
        self.played.append(array.array('h', s))

    def flush(self):
        self.flushed += 1


class FastVoiceTests(unittest.TestCase):
    def test_ws_client_roundtrip_with_masking_and_ping(self):
        srv = MiniWsServer(); self.addCleanup(srv.close)
        c = M.WsClient(f'ws://127.0.0.1:{srv.port}/x', timeout=3)
        c.connect()
        try:
            self.assertTrue(srv.up.wait(2))
            payload = bytes(range(256)) * 3            # > 125 bytes: 16-bit length path
            c.send_binary(payload); c.send_text('hi')
            self.assertTrue(wait_for(lambda: len(srv.got) == 2, 3))
            self.assertEqual(srv.got, [(2, payload), (1, b'hi')])
            srv.push(0x9, b'p')                        # ping must be answered and skipped
            srv.push(0x1, b'{"type":"clear"}')
            self.assertEqual(c.recv(), (1, b'{"type":"clear"}'))
            self.assertTrue(wait_for(lambda: (0xA, b'p') in srv.got, 3))
        finally:
            c.close()

    def test_loop_forwards_audio_plays_reply_and_flushes_on_clear(self):
        srv = MiniWsServer(); self.addCleanup(srv.close)
        rtp = FakeRtp()
        logs = []
        loop = M.FastVoiceLoop(rtp, f'ws://127.0.0.1:{srv.port}', token='tok', greeting='hello',
                               log=lambda m: logs.append(m))
        loop.attach()
        try:
            self.assertTrue(srv.up.wait(2))
            self.assertTrue(wait_for(lambda: len(srv.got) >= 2, 3))
            hello = json.loads(srv.got[0][1])
            self.assertEqual((hello['type'], hello['codec'], hello['rate'], hello['token']), ('hello', 'pcm16', 8000, 'tok'))
            self.assertEqual(json.loads(srv.got[1][1]), {'type': 'say', 'text': 'hello'})
            frame = M.tone(400, 0.02, amp=5000)
            self.assertEqual(len(frame), 160)
            rtp.on_audio[0](frame)
            self.assertTrue(wait_for(lambda: any(op == 2 for op, _ in srv.got), 3))
            sent = [d for op, d in srv.got if op == 2][0]
            self.assertEqual(array.array('h', sent).tolist(), frame.tolist() if sys.byteorder == 'little' else array.array('h', sent).tolist())
            reply = M.tone(700, 0.04, amp=6000)
            srv.push(0x2, reply.tobytes() if sys.byteorder == 'little' else M._le(reply).tobytes())
            self.assertTrue(wait_for(lambda: rtp.played, 3))
            self.assertEqual(rtp.played[0].tolist(), reply.tolist())
            srv.push(0x1, json.dumps({'type': 'clear'}).encode())
            self.assertTrue(wait_for(lambda: rtp.flushed == 1, 3))
            srv.push(0x1, json.dumps({'type': 'asr_final', 'text': 'what time is it'}).encode())
            srv.push(0x1, json.dumps({'type': 'metrics', 'turn': 1, 'spoken': ['It is noon.'],
                                      'endpoint_to_first_audio_ms': 410, 'asr_final_ms': 65}).encode())
            self.assertTrue(wait_for(lambda: loop.turns, 3))
            self.assertEqual((loop.turns[0]['transcript'], loop.turns[0]['reply'], loop.turns[0]['first_audio_ms']),
                             ('what time is it', 'It is noon.', 410))
        finally:
            loop.stop()

    def test_pin_gate_blocks_audio_until_dtmf(self):
        srv = MiniWsServer(); self.addCleanup(srv.close)
        rtp = FakeRtp()
        loop = M.FastVoiceLoop(rtp, f'ws://127.0.0.1:{srv.port}', pin='12', greeting='hi', log=quiet)
        loop.attach()
        try:
            self.assertTrue(srv.up.wait(2))
            rtp.on_audio[0](M.tone(400, 0.02, amp=5000))
            time.sleep(0.2)
            self.assertFalse(any(op == 2 for op, _ in srv.got))      # locked: nothing leaves the phone
            for d in '912':
                rtp.on_dtmf[0](d)
            self.assertTrue(wait_for(lambda: any(op == 1 and b'"say"' in d for op, d in srv.got), 3))
            rtp.on_audio[0](M.tone(400, 0.02, amp=5000))
            self.assertTrue(wait_for(lambda: any(op == 2 for op, _ in srv.got), 3))
        finally:
            loop.stop()

    def test_falls_back_to_classic_loop_when_service_is_down(self):
        s = socket.socket(); s.bind(('127.0.0.1', 0)); port = s.getsockname()[1]; s.close()    # nobody listens
        rtp = FakeRtp()
        made = []

        class Classic:
            def attach(self): made.append('attach')
            def stop(self): made.append('stop')
        loop = M.FastVoiceLoop(rtp, f'ws://127.0.0.1:{port}', fallback=Classic, connect_timeout=0.5, log=quiet)
        loop.attach(); loop.stop()
        self.assertEqual(made, ['attach', 'stop'])
        self.assertEqual(rtp.on_audio, [])                   # no streaming hook installed

    def test_cli_guard_and_flags(self):
        a = M.build_parser().parse_args(['listen', '--fast-voice', 'ws://100.1.2.3:8130', '--allow-from', '100.64.0.0/10'])
        self.assertEqual((a.fast_voice, a.fast_voice_token_env), ('ws://100.1.2.3:8130', 'UNUM_VOICE_TOKEN'))
        with self.assertRaises(SystemExit) as cm:
            M.run(M.build_parser().parse_args(['listen', '--fast-voice', 'ws://127.0.0.1:1']), log=quiet)
        self.assertIn('--allow-from', str(cm.exception))
        with self.assertRaises(ValueError):
            M.WsClient('wss://example.com/x')


class CliTests(unittest.TestCase):
    def test_tone_and_voice_loop_guard(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / 't.wav'
            self.assertEqual(M.main(['tone', str(out), '--freq', '1000', '--seconds', '0.5']), 0)
            rate, s = M.read_wav(out)
            self.assertEqual((rate, len(s)), (8000, 4000))
        with self.assertRaises(SystemExit) as cm:
            M.run(M.build_parser().parse_args(['listen', '--voice-loop', '--preset', 's22']), log=quiet)
        self.assertIn('--allow-from', str(cm.exception))
        self.assertEqual(M.PRESETS['s22-outer']['asr'][:2], ['chroot', '/mnt/omarchy-trial'])


if __name__ == '__main__':
    result = unittest.main(exit=False, verbosity=1).result
    sys.exit(0 if result.wasSuccessful() else 1)
