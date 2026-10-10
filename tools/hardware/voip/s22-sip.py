#!/usr/bin/env python3
"""s22-sip: stdlib-only SIP/RTP user agent for software-only voice calls.

Interim voice channel for the S22 (carrier call audio is blocked by the ABOX
DSP DMA issue): no speaker and no microphone are involved.  The far end's audio
is decoded from RTP into WAV files / raw pipes, and audio to send (TTS output,
test tones) is encoded from WAV files / a raw FIFO into RTP.

Python standard library only (runs on CPython 3.12 .. 3.14, no audioop).
Signalling: SIP over UDP (RFC 3261 subset), REGISTER with digest auth
(RFC 2617 MD5 / RFC 8760 SHA-256, qop=auth), INVITE (UAC and UAS), CANCEL,
ACK, BYE, OPTIONS, re-INVITE answer, rport NAT handling (RFC 3581).
Media: SDP offer/answer (RFC 3264), RTP G.711 PCMU/PCMA 20 ms (RFC 3550/3551),
reorder buffer with loss/silence fill, RFC 4733 DTMF send/receive, symmetric
RTP latching.  No SRTP/TLS/ICE: use it over Tailscale or a plain-UDP provider.

  s22-sip.py tone OUT.wav [--freq 440 --seconds 2]
  s22-sip.py listen [--bind IP --sip-port 5060] [--record IN.wav] [--play OUT.wav]
  s22-sip.py call sip:owner@100.x.y.z:5060 [--record ..] [--play ..] [--hangup-after-play]
  s22-sip.py listen --user agent --domain sip.example.net --password-env S22_SIP_PASSWORD ...
  s22-sip.py listen --voice-loop --preset s22 --allow-from 100.64.0.0/10 ...

Voice loop: far-end speech segment end (energy VAD) -> WAV -> ASR command ->
OpenUnum POST /api/chat (+ poll /api/chat/pending) or --chat-cmd -> TTS command
-> WAV -> RTP.  Command templates are argv strings with {wav} {text} {out}
placeholders (no shell), so fakes are easy:  --asr-cmd "cat /tmp/fixed.txt"

Fast voice: --fast-voice ws://RIG:8130 forwards the far-end audio (8 kHz PCM16) to the rig's unum-voice
service (VAD, streaming ASR, LLM, TTS and barge-in all run there) and plays back the audio it returns;
see README.md.  If the service is down at call start the classic voice loop above is used when its
--asr-cmd/--tts-cmd (or --preset) are also given.
"""
from __future__ import annotations

import argparse
import array
import base64
import hashlib
import ipaddress
import json
import math
import os
import queue
import random
import re
import secrets
import shlex
import signal
import socket
import struct
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import wave
from collections import deque
from pathlib import Path
from typing import Callable

VERSION = '0.1'
UA_NAME = f's22-sip/{VERSION}'
RATE = 8000
FRAME = 160                      # 20 ms at 8 kHz
T1, T2, TIMER_B = 0.5, 4.0, 32.0
ALLOW = 'INVITE, ACK, BYE, CANCEL, OPTIONS, INFO, NOTIFY, MESSAGE'


def _log(msg: str) -> None:
    sys.stderr.write(f'{time.strftime("%H:%M:%S")} {msg}\n')
    sys.stderr.flush()


# ------------------------------------------------------------------ G.711

def ulaw_encode_sample(s: int) -> int:
    s = max(-32768, min(32767, int(s)))
    sign = 0
    if s < 0:
        s, sign = -s, 0x80
    s = min(s, 32635) + 0x84
    exp = (s >> 7).bit_length() - 1
    mant = (s >> (exp + 3)) & 0x0F
    return ~(sign | (exp << 4) | mant) & 0xFF


def ulaw_decode_sample(u: int) -> int:
    u = ~u & 0xFF
    exp, mant = (u >> 4) & 7, u & 0x0F
    s = (((mant << 3) + 0x84) << exp) - 0x84
    return -s if u & 0x80 else s


_A_SEG_END = (0x1F, 0x3F, 0x7F, 0xFF, 0x1FF, 0x3FF, 0x7FF, 0xFFF)


def alaw_encode_sample(s: int) -> int:
    pcm = max(-32768, min(32767, int(s))) >> 3
    if pcm >= 0:
        mask = 0xD5
    else:
        mask, pcm = 0x55, -pcm - 1
    seg = next((i for i, end in enumerate(_A_SEG_END) if pcm <= end), 8)
    if seg >= 8:
        return 0x7F ^ mask
    aval = seg << 4
    aval |= (pcm >> 1) & 0x0F if seg < 2 else (pcm >> seg) & 0x0F
    return aval ^ mask


def alaw_decode_sample(a: int) -> int:
    a ^= 0x55
    t = (a & 0x0F) << 4
    seg = (a & 0x70) >> 4
    if seg == 0:
        t += 8
    elif seg == 1:
        t += 0x108
    else:
        t = (t + 0x108) << (seg - 1)
    return t if a & 0x80 else -t


_DEC = {'PCMU': array.array('h', [ulaw_decode_sample(i) for i in range(256)]),
        'PCMA': array.array('h', [alaw_decode_sample(i) for i in range(256)])}
_ENC: dict[str, bytes] = {}
_SILENCE_BYTE = {'PCMU': 0xFF, 'PCMA': 0xD5}


def _enc_table(codec: str) -> bytes:
    t = _ENC.get(codec)
    if t is None:
        f = ulaw_encode_sample if codec == 'PCMU' else alaw_encode_sample
        t = _ENC[codec] = bytes(f(s) for s in range(-32768, 32768))
    return t


def g711_encode(samples, codec: str = 'PCMU') -> bytes:
    t = _enc_table(codec)
    return bytes(t[s + 32768] for s in samples)


def g711_decode(data: bytes, codec: str = 'PCMU') -> array.array:
    t = _DEC[codec]
    return array.array('h', [t[b] for b in data])


# ------------------------------------------------------------------ audio files

def _le(a: array.array) -> array.array:
    if sys.byteorder == 'big':
        a = array.array(a.typecode, a)
        a.byteswap()
    return a


def read_wav(path) -> tuple[int, array.array]:
    """Read any common WAV (PCM 8/16/24/32, float 32/64, extensible) to mono int16."""
    data = Path(path).read_bytes()
    if data[:4] != b'RIFF' or data[8:12] != b'WAVE':
        raise ValueError(f'{path}: not a RIFF/WAVE file')
    pos, fmt, pcm = 12, None, None
    while pos + 8 <= len(data):
        cid = data[pos:pos + 4]
        size = struct.unpack_from('<I', data, pos + 4)[0]
        if cid == b'data' and (size == 0 or size == 0xFFFFFFFF or pos + 8 + size > len(data)):
            size = len(data) - pos - 8          # streamed writer left the size open
        body = data[pos + 8:pos + 8 + size]
        if cid == b'fmt ':
            fmt = body
        elif cid == b'data':
            pcm = body
        pos += 8 + size + (size & 1)
    if fmt is None or pcm is None:
        raise ValueError(f'{path}: missing fmt or data chunk')
    tag, ch, rate, _, _, bits = struct.unpack_from('<HHIIHH', fmt)
    if tag == 0xFFFE and len(fmt) >= 26:
        tag = struct.unpack_from('<H', fmt, 24)[0]
    width = bits // 8
    usable = len(pcm) - len(pcm) % (width * ch)
    pcm = pcm[:usable]
    if tag == 1 and bits == 16:
        a = array.array('h'); a.frombytes(pcm); vals = _le(a)
    elif tag == 1 and bits == 8:
        vals = [(b - 128) << 8 for b in pcm]
    elif tag == 1 and bits == 24:
        vals = [int.from_bytes(pcm[i:i + 3], 'little', signed=True) >> 8 for i in range(0, len(pcm), 3)]
    elif tag == 1 and bits == 32:
        a = array.array('i'); a.frombytes(pcm); vals = [v >> 16 for v in _le(a)]
    elif tag == 3 and bits in (32, 64):
        a = array.array('f' if bits == 32 else 'd'); a.frombytes(pcm)
        vals = [max(-32768, min(32767, int(v * 32767))) for v in _le(a)]
    else:
        raise ValueError(f'{path}: unsupported WAV format tag={tag} bits={bits}')
    if ch > 1:
        vals = [sum(vals[i:i + ch]) // ch for i in range(0, len(vals), ch)]
    return rate, array.array('h', vals)


def write_wav(path, rate: int, samples) -> None:
    with wave.open(str(path), 'wb') as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
        w.writeframes(_le(array.array('h', samples)).tobytes())


class WavStreamWriter:
    """Thread-safe incremental mono 16-bit WAV writer (header fixed on close)."""

    def __init__(self, path, rate: int = RATE):
        self.lock = threading.Lock()
        self.w = wave.open(str(path), 'wb')
        self.w.setnchannels(1); self.w.setsampwidth(2); self.w.setframerate(rate)
        self.samples = 0

    def __call__(self, samples) -> None:
        with self.lock:
            if self.w is not None:
                self.w.writeframes(_le(array.array('h', samples)).tobytes())
                self.samples += len(samples)

    def close(self) -> None:
        with self.lock:
            if self.w is not None:
                self.w.close(); self.w = None


def resample(samples, src: int, dst: int) -> array.array:
    """Linear resampler with a box pre-filter when downsampling (speech grade)."""
    if src == dst:
        return array.array('h', samples)
    x = list(samples)
    n = len(x)
    if n == 0:
        return array.array('h')
    if src > dst:
        w = int(round(src / dst))
        if w > 1:
            pre = [0]
            for v in x:
                pre.append(pre[-1] + v)
            lo, hi = w // 2, w - w // 2
            x = [(pre[min(n, i + hi)] - pre[max(0, i - lo)]) // (min(n, i + hi) - max(0, i - lo))
                 for i in range(n)]
    out_n = int(n * dst / src)
    step = src / dst
    out = array.array('h', bytes(2 * out_n))
    for i in range(out_n):
        p = i * step
        j = int(p)
        a = x[j]
        b = x[j + 1] if j + 1 < n else a
        out[i] = max(-32768, min(32767, int(a + (b - a) * (p - j))))
    return out


def tone(freq: float, seconds: float, rate: int = RATE, amp: int = 8000) -> array.array:
    return array.array('h', [int(amp * math.sin(2 * math.pi * freq * i / rate))
                             for i in range(int(seconds * rate))])


def goertzel(samples, freq: float, rate: int = RATE) -> float:
    """Normalised tone power (0..~1 for a full-scale sine) at freq."""
    n = len(samples)
    if not n:
        return 0.0
    k = 2 * math.cos(2 * math.pi * freq / rate)
    s1 = s2 = 0.0
    for v in samples:
        s1, s2 = v / 32768 + k * s1 - s2, s1
    return (s1 * s1 + s2 * s2 - k * s1 * s2) / (n * n / 4)


# ------------------------------------------------------------------ SIP messages

_COMPACT = {'v': 'via', 'f': 'from', 't': 'to', 'i': 'call-id', 'm': 'contact',
            'l': 'content-length', 'c': 'content-type', 'k': 'supported',
            's': 'subject', 'e': 'content-encoding', 'o': 'event', 'r': 'refer-to'}
_MULTI = {'via', 'contact', 'route', 'record-route', 'allow', 'supported', 'require'}


def _canon(name: str) -> str:
    n = name.strip().lower()
    return _COMPACT.get(n, n)


def split_commas(value: str) -> list[str]:
    """Split a header value on commas outside quotes and <...>."""
    out, cur, quote, angle = [], [], False, False
    for c in value:
        if c == '"':
            quote = not quote
        elif c == '<' and not quote:
            angle = True
        elif c == '>' and not quote:
            angle = False
        if c == ',' and not quote and not angle:
            out.append(''.join(cur).strip()); cur = []
        else:
            cur.append(c)
    if ''.join(cur).strip():
        out.append(''.join(cur).strip())
    return out


class SipMessage:
    def __init__(self, start_line: str, headers=None, body: bytes | str = b''):
        self.start_line = start_line.strip()
        self.headers: list[tuple[str, str]] = list(headers or [])
        self.body = body.encode() if isinstance(body, str) else bytes(body)

    @property
    def is_request(self) -> bool:
        return not self.start_line.startswith('SIP/2.0 ')

    @property
    def method(self) -> str:
        if self.is_request:
            return self.start_line.split(' ', 1)[0].upper()
        return self.cseq[1]

    @property
    def uri(self) -> str:
        return self.start_line.split(' ')[1]

    @property
    def status(self) -> int:
        return 0 if self.is_request else int(self.start_line.split(' ')[1])

    @property
    def reason(self) -> str:
        parts = self.start_line.split(' ', 2)
        return parts[2] if len(parts) > 2 else ''

    @property
    def cseq(self) -> tuple[int, str]:
        num, _, meth = (self.get('cseq') or '0 ?').partition(' ')
        return int(num), meth.strip().upper()

    def get(self, name: str, default=None):
        n = _canon(name)
        for k, v in self.headers:
            if _canon(k) == n:
                return split_commas(v)[0] if n in _MULTI and v else v
        return default

    def get_all(self, name: str) -> list[str]:
        n = _canon(name)
        out = []
        for k, v in self.headers:
            if _canon(k) == n:
                out.extend(split_commas(v) if n in _MULTI else [v])
        return out

    def remove(self, name: str) -> None:
        n = _canon(name)
        self.headers = [(k, v) for k, v in self.headers if _canon(k) != n]

    def set(self, name: str, value: str) -> None:
        self.remove(name); self.headers.append((name, value))

    def add(self, name: str, value: str) -> None:
        self.headers.append((name, value))

    def to_bytes(self) -> bytes:
        self.set('Content-Length', str(len(self.body)))
        head = '\r\n'.join([self.start_line] + [f'{k}: {v}' for k, v in self.headers])
        return head.encode() + b'\r\n\r\n' + self.body

    @classmethod
    def parse(cls, data: bytes) -> 'SipMessage':
        head, sep, body = data.partition(b'\r\n\r\n')
        if not sep:
            head, sep, body = data.partition(b'\n\n')
        lines = head.decode('utf-8', 'replace').replace('\r\n', '\n').split('\n')
        while lines and not lines[0].strip():
            lines.pop(0)
        if not lines:
            raise ValueError('empty SIP message')
        start = lines[0].strip()
        parts = start.split(' ')
        if not (start.startswith('SIP/2.0 ') and len(parts) >= 2 and parts[1].isdigit()) and \
                not (len(parts) == 3 and parts[2] == 'SIP/2.0'):
            raise ValueError(f'bad SIP start line: {start!r}')
        headers: list[tuple[str, str]] = []
        for line in lines[1:]:
            if line[:1] in (' ', '\t') and headers:
                k, v = headers[-1]
                headers[-1] = (k, v + ' ' + line.strip())
            elif ':' in line:
                k, v = line.split(':', 1)
                headers.append((k.strip(), v.strip()))
        msg = cls(start, headers, body)
        cl = msg.get('content-length')
        if cl is not None and cl.strip().isdigit():
            msg.body = body[:int(cl)]
        return msg


def parse_params(s: str) -> dict[str, str]:
    out = {}
    for p in s.split(';'):
        p = p.strip()
        if p:
            k, _, v = p.partition('=')
            out[k.strip().lower()] = v.strip().strip('"')
    return out


def parse_nameaddr(value: str) -> tuple[str, str, dict[str, str]]:
    """'"Bob" <sip:bob@h>;tag=1' -> ('Bob', 'sip:bob@h', {'tag': '1'})"""
    value = value.strip()
    if '<' in value:
        display, _, rest = value.partition('<')
        uri, _, params = rest.partition('>')
        return display.strip().strip('"'), uri.strip(), parse_params(params)
    uri, _, params = value.partition(';')
    return '', uri.strip(), parse_params(params)


def parse_uri(uri: str) -> dict:
    m = re.match(r'^(sips?):(?:([^@;]*)@)?(\[[^\]]+\]|[^:;?]+)(?::(\d+))?([^?]*)', uri.strip(), re.I)
    if not m:
        raise ValueError(f'bad SIP URI {uri!r}')
    user = (m.group(2) or '').split(':', 1)[0]
    return {'scheme': m.group(1).lower(), 'user': user, 'host': m.group(3).strip('[]'),
            'port': int(m.group(4)) if m.group(4) else None, 'params': parse_params(m.group(5))}


def tag_of(value: str | None) -> str | None:
    return parse_nameaddr(value)[2].get('tag') if value else None


def parse_via(value: str) -> dict:
    proto, _, rest = value.strip().partition(' ')
    sent_by, _, params = rest.strip().partition(';')
    host, _, port = sent_by.strip().partition(':')
    return {'proto': proto, 'host': host, 'port': int(port) if port.isdigit() else 5060,
            'params': parse_params(params)}


def parse_challenge(value: str) -> tuple[str, dict[str, str]]:
    scheme, _, rest = value.strip().partition(' ')
    fields = {}
    for m in re.finditer(r'([\w-]+)\s*=\s*("(?:[^"\\]|\\.)*"|[^,\s]+)', rest):
        v = m.group(2)
        fields[m.group(1).lower()] = v[1:-1] if v.startswith('"') else v
    return scheme, fields


# ------------------------------------------------------------------ digest auth

def _hash(alg: str, s: str) -> str:
    base = alg.upper().replace('-SESS', '')
    if base in ('', 'MD5'):
        return hashlib.md5(s.encode()).hexdigest()
    if base == 'SHA-256':
        return hashlib.sha256(s.encode()).hexdigest()
    raise ValueError(f'unsupported digest algorithm {alg}')


def digest_response(username: str, password: str, realm: str, nonce: str, method: str,
                    uri: str, qop: str | None = None, nc: str = '00000001',
                    cnonce: str | None = None, algorithm: str = 'MD5') -> str:
    ha1 = _hash(algorithm, f'{username}:{realm}:{password}')
    if algorithm.upper().endswith('-SESS'):
        ha1 = _hash(algorithm, f'{ha1}:{nonce}:{cnonce}')
    ha2 = _hash(algorithm, f'{method}:{uri}')
    if qop:
        return _hash(algorithm, f'{ha1}:{nonce}:{nc}:{cnonce}:{qop}:{ha2}')
    return _hash(algorithm, f'{ha1}:{nonce}:{ha2}')


def authorization_value(chal: dict[str, str], username: str, password: str, method: str,
                        uri: str, nc: int = 1, cnonce: str | None = None) -> str:
    alg = chal.get('algorithm', 'MD5')
    qops = [q.strip() for q in chal.get('qop', '').split(',') if q.strip()]
    qop = 'auth' if 'auth' in qops else None
    cnonce = cnonce or secrets.token_hex(8)
    ncs = f'{nc:08x}'
    resp = digest_response(username, password, chal.get('realm', ''), chal.get('nonce', ''),
                           method, uri, qop, ncs, cnonce, alg)
    parts = [f'username="{username}"', f'realm="{chal.get("realm", "")}"',
             f'nonce="{chal.get("nonce", "")}"', f'uri="{uri}"', f'response="{resp}"',
             f'algorithm={alg}']
    if qop:
        parts += [f'qop={qop}', f'nc={ncs}', f'cnonce="{cnonce}"']
    if 'opaque' in chal:
        parts.append(f'opaque="{chal["opaque"]}"')
    return 'Digest ' + ', '.join(parts)


# ------------------------------------------------------------------ SDP

STATIC_PT = {0: ('PCMU', 8000), 8: ('PCMA', 8000)}


def build_sdp(ip: str, port: int, codecs=((0, 'PCMU'), (8, 'PCMA')), dtmf_pt: int | None = 101,
              sess_id: int = 0, version: int = 1, direction: str = 'sendrecv') -> str:
    pts = [str(pt) for pt, _ in codecs] + ([str(dtmf_pt)] if dtmf_pt is not None else [])
    lines = ['v=0', f'o=s22 {sess_id or random.randint(1, 2**31)} {version} IN IP4 {ip}',
             's=s22-sip', f'c=IN IP4 {ip}', 't=0 0', f'm=audio {port} RTP/AVP {" ".join(pts)}']
    lines += [f'a=rtpmap:{pt} {name}/8000' for pt, name in codecs]
    if dtmf_pt is not None:
        lines += [f'a=rtpmap:{dtmf_pt} telephone-event/8000', f'a=fmtp:{dtmf_pt} 0-15']
    lines += ['a=ptime:20', f'a={direction}']
    return '\r\n'.join(lines) + '\r\n'


def parse_sdp(text: str | bytes) -> dict:
    if isinstance(text, bytes):
        text = text.decode('utf-8', 'replace')
    out = {'ip': None, 'port': 0, 'payloads': [], 'rtpmap': {}, 'direction': 'sendrecv',
           'ptime': 20}
    in_audio = seen_audio = False
    for line in text.replace('\r\n', '\n').split('\n'):
        line = line.strip()
        if len(line) < 2 or line[1] != '=':
            continue
        k, v = line[0], line[2:]
        if k == 'm':
            parts = v.split()
            in_audio = parts[0] == 'audio' and not seen_audio
            if in_audio:
                seen_audio = True
                out['port'] = int(parts[1].split('/')[0])
                out['proto'] = parts[2]
                out['payloads'] = [int(p) for p in parts[3:] if p.isdigit()]
        elif k == 'c' and (in_audio or not seen_audio):
            out['ip'] = v.split()[-1].split('/')[0]
        elif k == 'a' and (in_audio or not seen_audio):
            if v.startswith('rtpmap:'):
                pt, _, enc = v[7:].partition(' ')
                name, _, rate = enc.partition('/')
                out['rtpmap'][int(pt)] = (name.upper(), int(rate.split('/')[0] or 8000))
            elif v in ('sendrecv', 'sendonly', 'recvonly', 'inactive'):
                out['direction'] = v
            elif v.startswith('ptime:') and v[6:].strip().isdigit():
                out['ptime'] = int(v[6:])
    return out


def negotiate(remote: dict, prefer=('PCMU', 'PCMA')):
    """Pick (pt, codec, dtmf_pt) from a parsed SDP, honouring the remote order."""
    if not remote.get('port'):
        return None
    chosen = None
    dtmf = None
    for pt in remote['payloads']:
        name, rate = remote['rtpmap'].get(pt, STATIC_PT.get(pt, ('', 0)))
        if chosen is None and name in prefer and rate == 8000:
            chosen = (pt, name)
        if name == 'TELEPHONE-EVENT' and rate == 8000 and dtmf is None:
            dtmf = pt
    return None if chosen is None else (chosen[0], chosen[1], dtmf)


# ------------------------------------------------------------------ RTP

def rtp_pack(pt: int, seq: int, ts: int, ssrc: int, payload: bytes, marker: bool = False) -> bytes:
    return struct.pack('!BBHII', 0x80, (0x80 if marker else 0) | (pt & 0x7F),
                       seq & 0xFFFF, ts & 0xFFFFFFFF, ssrc & 0xFFFFFFFF) + payload


def rtp_unpack(data: bytes) -> dict | None:
    if len(data) < 12 or data[0] >> 6 != 2:
        return None
    b0, b1, seq, ts, ssrc = struct.unpack_from('!BBHII', data)
    off = 12 + 4 * (b0 & 0x0F)
    if b0 & 0x10:
        if len(data) < off + 4:
            return None
        off += 4 + 4 * struct.unpack_from('!H', data, off + 2)[0]
    end = len(data)
    if b0 & 0x20 and end > off:
        end -= data[-1]
    if off > end:
        return None
    return {'pt': b1 & 0x7F, 'marker': bool(b1 & 0x80), 'seq': seq, 'ts': ts, 'ssrc': ssrc,
            'payload': data[off:end]}


class JitterBuffer:
    """Reorder RTP by sequence; emits payload bytes and int silence sample counts.

    Arrival-driven (no clock): a packet waits for its predecessors until
    `depth` later packets are held, then missing ones count as lost; timestamp
    gaps (loss or silence suppression) are filled with silence.
    """

    def __init__(self, depth: int = 3, max_gap: int = 5 * RATE):
        self.depth, self.max_gap = depth, max_gap
        self.pkts: dict[int, tuple[int, bytes]] = {}
        self.next_seq: int | None = None
        self.next_ts = 0
        self.lost = self.late = 0

    def push(self, seq: int, ts: int, payload: bytes | None) -> list:
        if self.next_seq is None:
            self.next_seq, self.next_ts = seq, ts
        d = (seq - self.next_seq) & 0xFFFF
        if d >= 0x8000 or seq in self.pkts:
            self.late += 1
            return []
        out = []
        if d > 1000:                     # stream restart: drain and resync
            out = self.flush()
            self.next_seq, self.next_ts = seq, ts
        self.pkts[seq] = (ts, payload)
        return out + self._drain(False)

    def _drain(self, force: bool) -> list:
        out: list = []
        while self.pkts:
            if self.next_seq in self.pkts:
                ts, p = self.pkts.pop(self.next_seq)
                if p is None:            # sequence slot used by a non-audio packet (DTMF)
                    self.next_seq = (self.next_seq + 1) & 0xFFFF
                    continue
                gap = (ts - self.next_ts) & 0xFFFFFFFF
                if 0 < gap <= self.max_gap:
                    out.append(gap)
                out.append(p)
                self.next_ts = (ts + len(p)) & 0xFFFFFFFF
                self.next_seq = (self.next_seq + 1) & 0xFFFF
            elif force or len(self.pkts) > self.depth:
                self.lost += 1
                self.next_seq = (self.next_seq + 1) & 0xFFFF
            else:
                break
        return out

    def flush(self) -> list:
        return self._drain(True)


_DTMF_EVENTS = '0123456789*#ABCD'


class RtpSession:
    """One RTP audio stream: paced 20 ms sender + receiver with reorder buffer."""

    def __init__(self, sock: socket.socket, remote: tuple[str, int], pt: int, codec: str,
                 dtmf_pt: int | None = None, latch: bool = True, log=_log):
        self.sock, self.remote, self.pt, self.codec, self.dtmf_pt = sock, remote, pt, codec, dtmf_pt
        self.latch, self.log = latch, log
        self.ssrc = random.getrandbits(32)
        self.seq = random.getrandbits(16)
        self.ts = random.getrandbits(32)
        self.out: deque = deque()
        self.lock = threading.Lock()
        self.on_audio: list[Callable] = []
        self.on_dtmf: list[Callable] = []
        self.jb = JitterBuffer()
        self.running = False
        self.latched = False
        self.rx = self.tx = 0
        self.last_rx = time.monotonic()
        self._last_ev_ts = None
        self._threads: list[threading.Thread] = []

    @property
    def local_port(self) -> int:
        return self.sock.getsockname()[1]

    def start(self) -> None:
        self.running = True
        self.last_rx = time.monotonic()
        for fn in (self._send_loop, self._recv_loop):
            t = threading.Thread(target=fn, daemon=True)
            t.start(); self._threads.append(t)

    def stop(self) -> None:
        self.running = False
        for t in self._threads:
            t.join(timeout=1)
        for item in self.jb.flush():
            self._emit(item)
        try:
            self.sock.close()
        except OSError:
            pass

    # -- sending
    def play(self, samples) -> None:
        s = array.array('h', samples)
        with self.lock:
            for i in range(0, len(s), FRAME):
                fr = s[i:i + FRAME]
                if len(fr) < FRAME:
                    fr.extend([0] * (FRAME - len(fr)))
                self.out.append(('a', fr))

    def play_wav(self, path) -> float:
        rate, s = read_wav(path)
        s = resample(s, rate, RATE)
        self.play(s)
        return len(s) / RATE

    def flush(self) -> None:
        with self.lock:
            self.out.clear()

    @property
    def busy(self) -> bool:
        return bool(self.out)

    def wait_idle(self, timeout: float) -> bool:
        end = time.monotonic() + timeout
        while self.out and time.monotonic() < end:
            time.sleep(0.02)
        return not self.out

    def send_dtmf(self, digits: str, ms: int = 160) -> None:
        if self.dtmf_pt is None:
            raise RuntimeError('far end did not negotiate telephone-event')
        with self.lock:
            for d in digits.upper():
                ev = _DTMF_EVENTS.index(d)
                n = max(1, ms // 20)
                for i in range(n):
                    self.out.append(('e', ev, (i + 1) * FRAME, False, i == 0))
                for _ in range(3):
                    self.out.append(('e', ev, n * FRAME, True, False))
                for _ in range(3):
                    self.out.append(('a', array.array('h', [0] * FRAME)))

    def _send_loop(self) -> None:
        silence = bytes([_SILENCE_BYTE[self.codec]]) * FRAME
        nxt = time.monotonic()
        ev_ts = None
        while self.running:
            with self.lock:
                item = self.out.popleft() if self.out else None
            if item is not None and item[0] == 'e':
                _, ev, dur, end, first = item
                if first or ev_ts is None:
                    ev_ts = self.ts
                payload = bytes([ev, (0x80 if end else 0) | 10]) + struct.pack('!H', dur)
                pkt = rtp_pack(self.dtmf_pt, self.seq, ev_ts, self.ssrc, payload, first)
            else:
                if ev_ts is not None:
                    ev_ts = None
                payload = g711_encode(item[1], self.codec) if item else silence
                pkt = rtp_pack(self.pt, self.seq, self.ts, self.ssrc, payload)
            try:
                self.sock.sendto(pkt, self.remote)
                self.tx += 1
            except OSError:
                pass
            self.seq = (self.seq + 1) & 0xFFFF
            self.ts = (self.ts + FRAME) & 0xFFFFFFFF
            nxt += 0.02
            delay = nxt - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            elif delay < -0.2:
                nxt = time.monotonic()

    # -- receiving
    def _emit(self, item) -> None:
        samples = array.array('h', [0] * item) if isinstance(item, int) else g711_decode(item, self.codec)
        for cb in self.on_audio:
            try:
                cb(samples)
            except Exception as e:  # a broken consumer must not kill the stream
                self.log(f'rtp: audio consumer error {e!r}')

    def _recv_loop(self) -> None:
        self.sock.settimeout(0.2)
        while self.running:
            try:
                data, addr = self.sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            p = rtp_unpack(data)
            if p is None:
                continue
            if self.latch and not self.latched and addr != self.remote:
                self.log(f'rtp: latching remote {self.remote} -> {addr}')
                self.remote = addr
            self.latched = True
            self.rx += 1
            self.last_rx = time.monotonic()
            if self.dtmf_pt is not None and p['pt'] == self.dtmf_pt:
                pl = p['payload']
                if len(pl) >= 4 and pl[1] & 0x80 and p['ts'] != self._last_ev_ts:
                    self._last_ev_ts = p['ts']
                    if pl[0] < len(_DTMF_EVENTS):
                        for cb in self.on_dtmf:
                            cb(_DTMF_EVENTS[pl[0]])
                for item in self.jb.push(p['seq'], p['ts'], None):   # keep seq accounting
                    self._emit(item)
                continue
            if p['pt'] not in (self.pt, 0, 8):     # comfort noise (13), video, ...
                continue
            if p['pt'] != self.pt:          # codec switch mid-call (PCMU<->PCMA)
                self.pt, self.codec = p['pt'], STATIC_PT[p['pt']][0]
            for item in self.jb.push(p['seq'], p['ts'], p['payload']):
                self._emit(item)


# ------------------------------------------------------------------ VAD

class EnergyVad:
    """Energy VAD with an adaptive noise floor; emits ('start',) and ('segment', samples)."""

    def __init__(self, rate: int = RATE, frame_ms: int = 20, min_db: float = -42.0,
                 margin_db: float = 12.0, start_ms: int = 60, hangover_ms: int = 700,
                 min_speech_ms: int = 250, max_segment_s: float = 30.0, preroll_ms: int = 200):
        self.n = rate * frame_ms // 1000
        self.frame_ms = frame_ms
        self.min_db, self.margin = min_db, margin_db
        self.start_frames = max(1, start_ms // frame_ms)
        self.hang_frames = max(1, hangover_ms // frame_ms)
        self.min_frames = max(1, min_speech_ms // frame_ms)
        self.max_frames = int(max_segment_s * 1000 / frame_ms)
        self.pre_frames = preroll_ms // frame_ms
        self.noise = -60.0
        self.buf = array.array('h')
        self.pre: deque = deque(maxlen=max(1, self.pre_frames))
        self.seg: list = []
        self.active = False
        self.loud = self.quiet = self.voiced = 0

    @staticmethod
    def level_db(frame) -> float:
        if not frame:
            return -100.0
        e = sum(v * v for v in frame) / len(frame)
        return 10 * math.log10(e / (32768.0 ** 2) + 1e-12)

    def threshold(self) -> float:
        return max(self.min_db, self.noise + self.margin)

    def feed(self, samples) -> list:
        self.buf.extend(samples)
        events = []
        while len(self.buf) >= self.n:
            fr = self.buf[:self.n]
            del self.buf[:self.n]
            events += self._frame(fr)
        return events

    def _frame(self, fr) -> list:
        db = self.level_db(fr)
        speech = db > self.threshold()
        if not self.active:
            if not speech:   # track the floor only outside speech (fast down, slow up)
                self.noise = db if db < self.noise else self.noise * 0.98 + db * 0.02
            self.pre.append(fr)
            self.loud = self.loud + 1 if speech else 0
            if self.loud >= self.start_frames:
                self.active, self.quiet = True, 0
                self.voiced = self.loud
                self.seg = list(self.pre)
                return [('start',)]
            return []
        self.seg.append(fr)
        if speech:
            self.quiet = 0
            self.voiced += 1
        else:
            self.quiet += 1
        if self.quiet >= self.hang_frames or len(self.seg) >= self.max_frames:
            return self._close()
        return []

    def _close(self) -> list:
        seg, voiced = self.seg, self.voiced
        self.active, self.seg, self.loud, self.voiced = False, [], 0, 0
        self.pre.clear()
        if voiced < self.min_frames:
            return [('drop',)]
        out = array.array('h')
        for fr in seg:
            out.extend(fr)
        return [('segment', out)]


# ------------------------------------------------------------------ dialogs / UA

class Call:
    def __init__(self, call_id: str, direction: str):
        self.call_id, self.direction = call_id, direction
        self.local_tag = secrets.token_hex(4)
        self.local_hdr = self.remote_hdr = ''
        self.remote_target = ''
        self.route_set: list[str] = []
        self.local_cseq = 1
        self.dest: tuple[str, int] | None = None
        self.state = 'init'
        self.invite: SipMessage | None = None
        self.invite_branch = ''
        self.last_response: bytes | None = None
        self.last_ack: tuple[bytes, tuple] | None = None
        self.acked = threading.Event()
        self.ended = threading.Event()
        self.rtp: RtpSession | None = None
        self.rtp_sock: socket.socket | None = None
        self.local_ip = ''
        self.sdp_version = 1
        self.sess_id = random.randint(1, 2**31)
        self.remote_user = ''
        self.source: tuple[str, int] | None = None
        self.started = time.time()
        self.reason = ''


def _branch() -> str:
    return 'z9hG4bK' + secrets.token_hex(8)


class SipUA:
    def __init__(self, user: str = 's22', domain: str | None = None, password: str | None = None,
                 auth_user: str | None = None, display: str | None = None,
                 bind_ip: str = '0.0.0.0', sip_port: int = 5060, rtp_port: int = 0,
                 proxy: str | None = None, public_ip: str | None = None, expires: int = 300,
                 codecs=('PCMU', 'PCMA'), allow_from=(), answer_delay: float = 0.5,
                 log=_log, verbose: bool = False):
        self.user, self.domain, self.password = user, domain, password
        self.auth_user = auth_user or user
        self.display = display
        self.bind_ip, self.rtp_port = bind_ip, rtp_port
        self.proxy = self._hostport(proxy) if proxy else None
        self.public_ip, self.expires = public_ip, expires
        self.codecs = tuple(codecs)
        self.allow_from = list(allow_from)
        self.answer_delay = answer_delay
        self.log, self.verbose = log, verbose
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind((bind_ip, sip_port))
        self.port = self.sock.getsockname()[1]
        self.pending: dict[str, queue.Queue] = {}
        self.calls: dict[str, Call] = {}
        self.auto_answer = True
        self.on_media: list[Callable[[Call], None]] = []
        self.on_ended: list[Callable[[Call], None]] = []
        self.nat_contact: tuple[str, int] | None = None
        self.registered = False
        self.reg_call_id = secrets.token_hex(10)
        self.reg_tag = secrets.token_hex(4)
        self.reg_cseq = 0
        self.reg_interval = expires
        self.running = False
        self._threads: list[threading.Thread] = []
        self.lock = threading.Lock()

    # -- plumbing
    @staticmethod
    def _hostport(s: str, default: int = 5060) -> tuple[str, int]:
        s = s.strip()
        if s.startswith('sip:'):
            u = parse_uri(s)
            return u['host'], u['port'] or default
        host, _, port = s.rpartition(':') if s.count(':') == 1 else (s, '', '')
        return (host, int(port)) if port else (s, default)

    def start(self) -> None:
        self.running = True
        t = threading.Thread(target=self._recv_loop, daemon=True)
        t.start(); self._threads.append(t)

    def close(self) -> None:
        for c in list(self.calls.values()):
            if c.state != 'ended':
                self.hangup(c)
        if self.registered:
            self.register(expires=0)
        self.running = False
        try:
            self.sock.close()
        except OSError:
            pass

    def resolve(self, host: str, port: int | None) -> tuple[str, int]:
        info = socket.getaddrinfo(host, port or 5060, socket.AF_INET, socket.SOCK_DGRAM)
        return info[0][4][0], info[0][4][1]

    def local_ip(self, dest_ip: str) -> str:
        if self.public_ip:
            return self.public_ip
        if self.nat_contact:
            return self.nat_contact[0]
        if self.bind_ip not in ('0.0.0.0', ''):
            return self.bind_ip
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect((dest_ip, 9))
            return s.getsockname()[0]
        except OSError:
            return '127.0.0.1'
        finally:
            s.close()

    def contact(self, dest_ip: str) -> str:
        host, port = self.nat_contact or (self.local_ip(dest_ip), self.port)
        return f'<sip:{self.user}@{host}:{port}>'

    def aor(self, dest_ip: str) -> str:
        return f'sip:{self.user}@{self.domain}' if self.domain else \
            f'sip:{self.user}@{self.local_ip(dest_ip)}:{self.port}'

    def send(self, msg: SipMessage | bytes, addr: tuple[str, int]) -> None:
        data = msg.to_bytes() if isinstance(msg, SipMessage) else msg
        if self.verbose:
            self.log(f'>>> {addr}\n{data.decode(errors="replace")}')
        try:
            self.sock.sendto(data, addr)
        except OSError as e:
            self.log(f'sip: send to {addr} failed: {e}')

    def is_allowed(self, msg: SipMessage, addr: tuple[str, int]) -> bool:
        if not self.allow_from:
            return True
        frm = parse_nameaddr(msg.get('from') or '')[1]
        try:
            fu = parse_uri(frm)
        except ValueError:
            fu = {'user': '', 'host': ''}
        for rule in self.allow_from:
            if '/' in rule:
                try:
                    if ipaddress.ip_address(addr[0]) in ipaddress.ip_network(rule, strict=False):
                        return True
                except ValueError:
                    pass
            elif rule in (addr[0], fu['user'], f'{fu["user"]}@{fu["host"]}', frm):
                return True
        return False

    # -- transactions
    def transact(self, msg: SipMessage, addr: tuple[str, int], timeout: float = TIMER_B,
                 prov_timeout: float | None = None, on_provisional=None) -> SipMessage | None:
        branch = parse_via(msg.get('via'))['params'].get('branch', '')
        q: queue.Queue = queue.Queue()
        self.pending[branch] = q
        invite = msg.method == 'INVITE'
        try:
            self.send(msg, addr)
            interval, provisional = T1, False
            deadline = time.monotonic() + timeout
            next_tx = time.monotonic() + interval
            while True:
                now = time.monotonic()
                if now >= deadline:
                    return None
                retransmit = not (invite and provisional)
                wait = min(deadline, next_tx) - now if retransmit else deadline - now
                try:
                    resp = q.get(timeout=max(0.01, wait))
                except queue.Empty:
                    if retransmit and time.monotonic() >= next_tx:
                        self.send(msg, addr)
                        interval = interval * 2 if invite else min(interval * 2, T2)
                        if provisional:
                            interval = T2
                        next_tx = time.monotonic() + interval
                    continue
                if resp.status < 200:
                    if not provisional and prov_timeout is not None:
                        deadline = time.monotonic() + prov_timeout
                    provisional = True
                    if on_provisional:
                        on_provisional(resp)
                    continue
                return resp
        finally:
            self.pending.pop(branch, None)

    def build_request(self, method: str, ruri: str, call: Call | None, dest: tuple[str, int],
                      from_hdr: str, to_hdr: str, call_id: str, cseq: int, body: bytes | str = b'',
                      ctype: str | None = None, routes=(), branch: str | None = None,
                      contact: bool = True) -> SipMessage:
        lip = self.local_ip(dest[0])
        via_host, via_port = self.nat_contact or (lip, self.port)
        m = SipMessage(f'{method} {ruri} SIP/2.0')
        m.add('Via', f'SIP/2.0/UDP {via_host}:{via_port};branch={branch or _branch()};rport')
        m.add('Max-Forwards', '70')
        for r in routes:
            m.add('Route', r)
        m.add('From', from_hdr)
        m.add('To', to_hdr)
        m.add('Call-ID', call_id)
        m.add('CSeq', f'{cseq} {method}')
        if contact and method not in ('ACK', 'CANCEL', 'BYE'):
            m.add('Contact', self.contact(dest[0]))
        m.add('User-Agent', UA_NAME)
        if method in ('INVITE', 'OPTIONS'):
            m.add('Allow', ALLOW)
        if body:
            m.add('Content-Type', ctype or 'application/sdp')
        m.body = body.encode() if isinstance(body, str) else body
        return m

    def _auth(self, resp: SipMessage, method: str, uri: str, nc: int) -> tuple[str, str] | None:
        hdr = 'WWW-Authenticate' if resp.status == 401 else 'Proxy-Authenticate'
        val = resp.get(hdr)
        if not val or self.password is None:
            return None
        scheme, chal = parse_challenge(val)
        if scheme.lower() != 'digest':
            return None
        name = 'Authorization' if resp.status == 401 else 'Proxy-Authorization'
        try:
            return name, authorization_value(chal, self.auth_user, self.password, method, uri, nc)
        except ValueError as e:
            self.log(f'sip: {e}')
            return None

    # -- registration
    def registrar(self) -> tuple[str, int]:
        if self.proxy:
            return self.resolve(*self.proxy)
        if not self.domain:
            raise RuntimeError('register needs --domain (or --proxy)')
        return self.resolve(self.domain, 5060)

    def register(self, expires: int | None = None) -> bool:
        expires = self.expires if expires is None else expires
        addr = self.registrar()
        ruri = f'sip:{self.domain}'
        auth = None
        nc = 0
        for _attempt in range(5):
            self.reg_cseq += 1
            aor = f'sip:{self.user}@{self.domain}'
            m = self.build_request('REGISTER', ruri, None, addr, f'<{aor}>;tag={self.reg_tag}',
                                   f'<{aor}>', self.reg_call_id, self.reg_cseq)
            m.add('Expires', str(expires))
            if auth:
                m.add(*auth)
            resp = self.transact(m, addr)
            if resp is None:
                self.log(f'sip: REGISTER to {addr} timed out')
                return False
            if resp.status in (401, 407):
                stale = 'stale=true' in (resp.get('www-authenticate') or resp.get('proxy-authenticate') or '').lower()
                if auth and not stale:
                    self.log(f'sip: REGISTER rejected: {resp.status} {resp.reason} (bad credentials?)')
                    return False
                nc += 1
                auth = self._auth(resp, 'REGISTER', ruri, nc)
                if auth is None:
                    self.log('sip: REGISTER challenged but no password / unsupported scheme')
                    return False
                continue
            if 200 <= resp.status < 300:
                if expires == 0:
                    self.registered = False
                    return True
                via = parse_via(resp.get('via'))['params']
                rec, rport = via.get('received'), via.get('rport', '')
                if rec and rport.isdigit() and not self.nat_contact and not self.public_ip and \
                        (rec, int(rport)) != (self.local_ip(addr[0]), self.port):
                    self.nat_contact = (rec, int(rport))
                    self.log(f'sip: behind NAT, re-registering with public contact {rec}:{rport}')
                    continue
                granted = expires
                for c in resp.get_all('contact'):
                    e = parse_nameaddr(c)[2].get('expires')
                    if e and e.isdigit():
                        granted = int(e)
                if (resp.get('expires') or '').isdigit():
                    granted = int(resp.get('expires'))
                self.reg_interval = max(30, granted)
                self.registered = True
                self.log(f'sip: registered {aor} for {granted}s')
                return True
            self.log(f'sip: REGISTER failed: {resp.status} {resp.reason}')
            return False
        return False

    def keep_registered(self, keepalive: float = 25.0) -> None:
        def loop():
            last = time.monotonic()
            while self.running:
                time.sleep(1)
                if not self.running:
                    break
                now = time.monotonic()
                if now - last >= self.reg_interval / 2:
                    last = now
                    if not self.register():
                        last = now - self.reg_interval / 2 + 30   # retry in 30 s
                elif keepalive and int(now - last) % int(keepalive) == 0:
                    try:
                        self.sock.sendto(b'\r\n\r\n', self.registrar())   # RFC 5626 CRLF keepalive
                    except (OSError, RuntimeError):
                        pass
        t = threading.Thread(target=loop, daemon=True)
        t.start(); self._threads.append(t)

    # -- media
    def _open_rtp(self) -> socket.socket:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        if self.rtp_port:
            for p in range(self.rtp_port, self.rtp_port + 200, 2):
                try:
                    s.bind((self.bind_ip, p)); return s
                except OSError:
                    continue
        s.bind((self.bind_ip, 0))
        return s

    def _our_codecs(self):
        return tuple((0 if c == 'PCMU' else 8, c) for c in self.codecs)

    def _sdp(self, call: Call, codecs=None, dtmf_pt=101) -> str:
        call.sdp_version += 1
        return build_sdp(call.local_ip, call.rtp_sock.getsockname()[1], codecs or self._our_codecs(),
                         dtmf_pt, call.sess_id, call.sdp_version)

    def _start_media(self, call: Call, remote_sdp: dict, chosen) -> None:
        pt, codec, dtmf = chosen
        remote = (remote_sdp['ip'] or (call.source or ('0.0.0.0',))[0], remote_sdp['port'])
        if call.rtp is not None:
            call.rtp.remote = remote
            return
        call.rtp = RtpSession(call.rtp_sock, remote, pt, codec, dtmf, log=self.log)
        call.rtp.start()
        self.log(f'call {call.call_id[:8]}: media {codec}/{pt} -> {remote[0]}:{remote[1]} '
                 f'(local rtp {call.rtp.local_port}, dtmf={dtmf})')
        for cb in self.on_media:
            try:
                cb(call)
            except Exception as e:
                self.log(f'media hook error {e!r}')

    def _dialog_dest(self, call: Call) -> tuple[str, int]:
        if call.route_set:
            u = parse_uri(parse_nameaddr(call.route_set[0])[1])
            return self.resolve(u['host'], u['port'])
        if self.proxy:
            return self.resolve(*self.proxy)
        if call.direction == 'in' and call.source:
            return call.source
        u = parse_uri(call.remote_target)
        return self.resolve(u['host'], u['port'])

    # -- outgoing calls
    def normalize_target(self, target: str) -> str:
        t = target.strip()
        if not t.lower().startswith(('sip:', 'sips:')):
            t = 'sip:' + t
        if '@' not in t and self.domain and not re.match(r'^sip:[\d.]+(:\d+)?$', t):
            t = f'{t}@{self.domain}'
        return t

    def call(self, target: str, ring_timeout: float = 60.0) -> Call | None:
        ruri = self.normalize_target(target)
        u = parse_uri(ruri)
        dest = self.resolve(*self.proxy) if self.proxy else self.resolve(u['host'], u['port'])
        call = Call(secrets.token_hex(12), 'out')
        call.local_ip = self.local_ip(dest[0])
        call.rtp_sock = self._open_rtp()
        call.local_hdr = f'<{self.aor(dest[0])}>;tag={call.local_tag}'
        call.remote_hdr = f'<{ruri}>'
        call.remote_target = ruri
        call.dest = dest
        call.source = dest
        call.remote_user = u['user']
        self.calls[call.call_id] = call
        offer = self._sdp(call)
        auth, nc = None, 0
        call.state = 'calling'
        for _attempt in range(3):
            branch = _branch()
            inv = self.build_request('INVITE', ruri, call, dest, call.local_hdr, call.remote_hdr,
                                     call.call_id, call.local_cseq, offer, branch=branch)
            if auth:
                inv.add(*auth)
            call.invite, call.invite_branch = inv, branch

            def prov(r: SipMessage) -> None:
                call.state = 'ringing'
                self.log(f'call {call.call_id[:8]}: {r.status} {r.reason}')
            resp = self.transact(inv, dest, prov_timeout=ring_timeout, on_provisional=prov)
            if resp is None:
                if call.state == 'ringing':
                    self.cancel(call)
                self.log(f'call {call.call_id[:8]}: no answer')
                self._end(call, 'timeout')
                return None
            if resp.status >= 300:
                self._ack_non2xx(inv, resp, dest)
            if resp.status in (401, 407) and auth is None:
                nc += 1
                auth = self._auth(resp, 'INVITE', ruri, nc)
                if auth:
                    call.local_cseq += 1
                    continue
            if 200 <= resp.status < 300:
                call.remote_hdr = resp.get('to')
                ct = resp.get('contact')
                if ct:
                    call.remote_target = parse_nameaddr(ct)[1]
                call.route_set = list(reversed(resp.get_all('record-route')))
                self._send_ack(call)
                call.state = 'confirmed'
                rsdp = parse_sdp(resp.body)
                chosen = negotiate(rsdp, self.codecs)
                if chosen is None:
                    self.log('call: answer has no usable codec')
                    self.hangup(call)
                    return None
                self._start_media(call, rsdp, chosen)
                return call
            self.log(f'call {call.call_id[:8]}: failed {resp.status} {resp.reason}')
            self._end(call, f'{resp.status}')
            return None
        self._end(call, 'auth')
        return None

    def _ack_non2xx(self, inv: SipMessage, resp: SipMessage, dest) -> None:
        ack = SipMessage(f'ACK {inv.uri} SIP/2.0')
        ack.add('Via', inv.get('via'))
        ack.add('Max-Forwards', '70')
        for r in inv.get_all('route'):
            ack.add('Route', r)
        ack.add('From', inv.get('from'))
        ack.add('To', resp.get('to'))
        ack.add('Call-ID', inv.get('call-id'))
        ack.add('CSeq', f'{inv.cseq[0]} ACK')
        self.send(ack, dest)

    def _send_ack(self, call: Call) -> None:
        dest = self._dialog_dest(call)
        ack = self.build_request('ACK', call.remote_target, call, dest, call.local_hdr,
                                 call.remote_hdr, call.call_id, call.local_cseq,
                                 routes=call.route_set)
        data = ack.to_bytes()
        call.last_ack = (data, dest)
        self.send(data, dest)

    def cancel(self, call: Call) -> None:
        inv = call.invite
        if inv is None:
            return
        c = SipMessage(f'CANCEL {inv.uri} SIP/2.0')
        c.add('Via', inv.get('via')); c.add('Max-Forwards', '70')
        c.add('From', inv.get('from')); c.add('To', inv.get('to'))
        c.add('Call-ID', inv.get('call-id')); c.add('CSeq', f'{inv.cseq[0]} CANCEL')
        self.transact(c, call.dest, timeout=4)

    def hangup(self, call: Call, reason: str = 'local hangup') -> None:
        if call.state == 'ended':
            return
        if call.direction == 'out' and call.state in ('calling', 'ringing'):
            self.cancel(call)
            self._end(call, 'cancelled')
            return
        if call.direction == 'in' and call.state in ('ringing',):
            self._respond_invite(call, 486, 'Busy Here')
            self._end(call, reason)
            return
        dest = self._dialog_dest(call)
        auth = None
        for _ in range(2):
            call.local_cseq += 1
            bye = self.build_request('BYE', call.remote_target, call, dest, call.local_hdr,
                                     call.remote_hdr, call.call_id, call.local_cseq,
                                     routes=call.route_set)
            if auth:
                bye.add(*auth)
            resp = self.transact(bye, dest, timeout=8)
            if resp is not None and resp.status in (401, 407) and auth is None:
                auth = self._auth(resp, 'BYE', call.remote_target, 1)
                if auth:
                    continue
            break
        self._end(call, reason)

    def _end(self, call: Call, reason: str) -> None:
        with self.lock:
            if call.state == 'ended':
                return
            call.state = 'ended'
            call.reason = reason
        if call.rtp is not None:
            call.rtp.stop()
        elif call.rtp_sock is not None:
            call.rtp_sock.close()
        self.log(f'call {call.call_id[:8]}: ended ({reason})')
        call.ended.set()
        for cb in self.on_ended:
            try:
                cb(call)
            except Exception as e:
                self.log(f'end hook error {e!r}')

    # -- incoming
    def make_response(self, req: SipMessage, code: int, reason: str, to_tag: str | None = None,
                      body: bytes | str = b'', addr=None, contact_ip: str | None = None) -> SipMessage:
        r = SipMessage(f'SIP/2.0 {code} {reason}')
        for i, v in enumerate(req.get_all('via')):
            if i == 0 and addr is not None:
                pv = parse_via(v)
                if 'rport' in pv['params'] and not pv['params']['rport']:
                    v = re.sub(r';rport(?=;|$)', f';rport={addr[1]}', v)
                if pv['host'] != addr[0] and 'received=' not in v:
                    v += f';received={addr[0]}'
            r.add('Via', v)
        r.add('From', req.get('from'))
        to = req.get('to') or ''
        if to_tag and not tag_of(to):
            to += f';tag={to_tag}'
        r.add('To', to)
        r.add('Call-ID', req.get('call-id'))
        r.add('CSeq', req.get('cseq'))
        if req.method == 'INVITE' and 200 <= code < 300:
            for rr in req.get_all('record-route'):
                r.add('Record-Route', rr)
        if contact_ip is not None:
            r.add('Contact', self.contact(contact_ip))
        if req.method in ('INVITE', 'OPTIONS'):
            r.add('Allow', ALLOW)
        r.add('User-Agent', UA_NAME)
        if body:
            r.add('Content-Type', 'application/sdp')
            r.body = body.encode() if isinstance(body, str) else body
        return r

    def _respond_invite(self, call: Call, code: int, reason: str, body=b'') -> None:
        r = self.make_response(call.invite, code, reason, call.local_tag, body, call.source,
                               contact_ip=call.source[0] if code < 300 else None)
        call.last_response = r.to_bytes()
        self.send(call.last_response, call.source)

    def _recv_loop(self) -> None:
        self.sock.settimeout(0.5)
        while self.running:
            try:
                data, addr = self.sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                break
            if not data.strip():
                continue            # CRLF keepalive
            try:
                msg = SipMessage.parse(data)
            except (ValueError, IndexError) as e:
                self.log(f'sip: unparsable datagram from {addr}: {e}')
                continue
            if self.verbose:
                self.log(f'<<< {addr}\n{data.decode(errors="replace")}')
            try:
                if msg.is_request:
                    self._handle_request(msg, addr)
                else:
                    self._handle_response(msg)
            except Exception as e:      # keep the UA alive on malformed input
                self.log(f'sip: error handling message from {addr}: {e!r}')

    def _handle_response(self, msg: SipMessage) -> None:
        branch = parse_via(msg.get('via') or 'SIP/2.0/UDP x')['params'].get('branch', '')
        q = self.pending.get(branch)
        if q is not None:
            q.put(msg)
            return
        call = self.calls.get(msg.get('call-id') or '')
        if call and msg.method == 'INVITE' and 200 <= msg.status < 300 and call.last_ack:
            self.send(*call.last_ack)       # our ACK was lost: re-ACK the 2xx retransmission

    def _handle_request(self, msg: SipMessage, addr: tuple[str, int]) -> None:
        m = msg.method
        call = self.calls.get(msg.get('call-id') or '')
        if m == 'INVITE':
            self._on_invite(msg, addr, call)
        elif m == 'ACK':
            if call:
                call.acked.set()
                if call.state == 'answering-late' and msg.body:
                    rsdp = parse_sdp(msg.body)
                    chosen = negotiate(rsdp, self.codecs)
                    if chosen:
                        call.state = 'confirmed'
                        self._start_media(call, rsdp, chosen)
        elif m == 'BYE':
            if call is None or call.state == 'ended':
                self.send(self.make_response(msg, 481, 'Call/Transaction Does Not Exist', addr=addr), addr)
                return
            self.send(self.make_response(msg, 200, 'OK', addr=addr), addr)
            self._end(call, 'remote hangup')
        elif m == 'CANCEL':
            self.send(self.make_response(msg, 200, 'OK', addr=addr), addr)
            if call and call.state in ('ringing',) and call.direction == 'in':
                self._respond_invite(call, 487, 'Request Terminated')
                self._end(call, 'cancelled by caller')
        elif m == 'REGISTER':
            # Softphones (Linphone "third-party SIP account") insist on registering
            # before they show the account as usable. Nothing is routed by this
            # registration, so accept it -- credentials ignored -- from callers the
            # allow list admits (e.g. --allow-from 100.64.0.0/10 = Tailscale only).
            if not self.is_allowed(msg, addr):
                self.send(self.make_response(msg, 403, 'Forbidden', addr=addr), addr)
                return
            r = self.make_response(msg, 200, 'OK', addr=addr)
            try:
                expires = max(0, min(int(msg.get('expires') or 3600), 3600))
            except ValueError:
                expires = 3600
            for c in msg.get_all('contact'):
                r.add('Contact', c if ';expires=' in c else f'{c};expires={expires}')
            r.add('Expires', str(expires))
            self.send(r.to_bytes(), addr)
        elif m in ('OPTIONS', 'INFO', 'NOTIFY', 'MESSAGE'):
            if m == 'MESSAGE':
                self.log(f'sip: MESSAGE from {msg.get("from")}: {msg.body[:200]!r}')
            self.send(self.make_response(msg, 200, 'OK', addr=addr), addr)
        else:
            self.send(self.make_response(msg, 501, 'Not Implemented', addr=addr), addr)

    def _on_invite(self, msg: SipMessage, addr, call: Call | None) -> None:
        if call is not None:
            if tag_of(msg.get('to')) and call.state == 'confirmed':      # re-INVITE
                rsdp = parse_sdp(msg.body) if msg.body else None
                chosen = negotiate(rsdp, self.codecs) if rsdp else None
                if rsdp and chosen:
                    call.rtp.remote = (rsdp['ip'] or addr[0], rsdp['port'])
                pt_codec = ((call.rtp.pt, call.rtp.codec),) if call.rtp else None
                body = self._sdp(call, pt_codec, call.rtp.dtmf_pt if call.rtp else 101)
                r = self.make_response(msg, 200, 'OK', call.local_tag, body, addr, contact_ip=addr[0])
                self.send(r, addr)
            elif call.last_response:                                   # retransmission
                self.send(call.last_response, addr)
            return
        if not self.is_allowed(msg, addr):
            self.log(f'sip: rejecting INVITE from {msg.get("from")} via {addr} (not in --allow-from)')
            self.send(self.make_response(msg, 403, 'Forbidden', secrets.token_hex(4), addr=addr), addr)
            return
        if any(c.state != 'ended' for c in self.calls.values()) or not self.auto_answer:
            self.send(self.make_response(msg, 486, 'Busy Here', secrets.token_hex(4), addr=addr), addr)
            return
        call = Call(msg.get('call-id'), 'in')
        call.invite, call.source = msg, addr
        call.local_ip = self.local_ip(addr[0])
        call.local_hdr = (msg.get('to') or '') + f';tag={call.local_tag}'
        call.remote_hdr = msg.get('from') or ''
        call.remote_target = parse_nameaddr(msg.get('contact') or msg.get('from'))[1]
        call.route_set = msg.get_all('record-route')
        call.local_cseq = random.randint(1, 10000)
        call.remote_user = parse_uri(parse_nameaddr(msg.get('from'))[1])['user'] \
            if msg.get('from') else ''
        call.state = 'ringing'
        self.calls[call.call_id] = call
        self.log(f'call {call.call_id[:8]}: incoming from {call.remote_hdr} via {addr[0]}:{addr[1]}')
        self.send(self.make_response(msg, 100, 'Trying', addr=addr), addr)
        self._respond_invite(call, 180, 'Ringing')
        threading.Thread(target=self._answer, args=(call,), daemon=True).start()

    def _answer(self, call: Call) -> None:
        time.sleep(self.answer_delay)
        if call.state != 'ringing':
            return
        call.rtp_sock = self._open_rtp()
        msg = call.invite
        if msg.body:
            rsdp = parse_sdp(msg.body)
            chosen = negotiate(rsdp, self.codecs)
            if chosen is None:
                self._respond_invite(call, 488, 'Not Acceptable Here')
                self._end(call, 'no common codec')
                return
            pt, codec, dtmf = chosen
            body = self._sdp(call, ((pt, codec),), dtmf)
            call.state = 'confirmed'
            self._respond_invite(call, 200, 'OK', body)
            self._start_media(call, rsdp, chosen)
        else:                                     # late offer: SDP answer arrives in ACK
            call.state = 'answering-late'
            self._respond_invite(call, 200, 'OK', self._sdp(call))
        # retransmit 2xx until ACK (Timer G / H)
        interval, waited = T1, 0.0
        while not call.acked.wait(interval):
            waited += interval
            if waited >= TIMER_B or call.state == 'ended':
                if call.state != 'ended':
                    self.log(f'call {call.call_id[:8]}: no ACK, hanging up')
                    self.hangup(call, 'no ACK')
                return
            self.send(call.last_response, call.source)
            interval = min(interval * 2, T2)


# ------------------------------------------------------------------ voice loop

SPOKEN_CONTEXT = (
    'This message was spoken by the owner during a voice call to the Galaxy S22 agent and '
    'transcribed by on-device speech recognition, so it may contain recognition errors. '
    'Your reply will be spoken on the call by text-to-speech: answer in one to three short '
    'plain spoken sentences unless asked for more. No markdown, lists, tables, code or URLs.')

_MD = [(re.compile(r'```.*?```', re.S), ' (code omitted) '), (re.compile(r'`([^`]*)`'), r'\1'),
       (re.compile(r'!?\[([^\]]+)\]\([^)]*\)'), r'\1'), (re.compile(r'https?://\S+'), 'a link'),
       (re.compile(r'^\s{0,3}#{1,6}\s*', re.M), ''), (re.compile(r'^\s*[-*+>]\s+', re.M), ''),
       (re.compile(r'(\*\*|__|\*|~~)'), ''), (re.compile(r'\|'), ', '), (re.compile(r'\s+'), ' ')]


def clean_for_speech(text: str, max_chars: int = 450) -> str:
    for rx, rep in _MD:
        text = rx.sub(rep, text)
    text = text.strip()
    if len(text) > max_chars:
        cut = text[:max_chars]
        dot = max(cut.rfind('. '), cut.rfind('? '), cut.rfind('! '))
        text = cut[:dot + 1] if dot > max_chars // 3 else cut.rsplit(' ', 1)[0] + '.'
    return text


def fill_template(template, root: str = '', **values: str) -> list[str]:
    """argv template (str or list) with {name} placeholders; paths under root are made root-relative."""
    argv = shlex.split(template) if isinstance(template, str) else list(template)
    out = []
    for a in argv:
        for k, v in values.items():
            v = str(v)
            if root and k in ('wav', 'out') and v.startswith(root.rstrip('/') + '/'):
                v = v[len(root.rstrip('/')):]
            a = a.replace('{' + k + '}', v)
        out.append(a)
    return out


def parse_asr_output(out: str) -> str:
    """sherpa-onnx prints a JSON object with "text"; otherwise take the plain stdout."""
    for line in reversed(out.splitlines()):
        line = line.strip()
        if line.startswith('{') and '"text"' in line:
            try:
                return str(json.loads(line).get('text', '')).strip()
            except ValueError:
                pass
    return out.strip()


def command_asr(template, root: str = '', timeout: float = 120) -> Callable[[str], str]:
    def asr(wav: str) -> str:
        r = subprocess.run(fill_template(template, root, wav=wav), capture_output=True,
                           text=True, timeout=timeout)
        text = parse_asr_output(r.stdout)
        if r.returncode != 0 and not text:
            raise RuntimeError(f'asr rc={r.returncode}: {r.stderr[-300:]}')
        return text
    return asr


def command_tts(template, root: str = '', timeout: float = 120) -> Callable[[str, str], None]:
    def tts(text: str, out: str) -> None:
        r = subprocess.run(fill_template(template, root, text=text, out=out), capture_output=True,
                           text=True, timeout=timeout)
        if r.returncode != 0 or not os.path.exists(out):
            raise RuntimeError(f'tts rc={r.returncode}: {r.stderr[-300:]}')
    return tts


def command_chat(template, timeout: float = 240) -> Callable[[str], str]:
    def chat(text: str) -> str:
        r = subprocess.run(fill_template(template, text=text), capture_output=True, text=True,
                           timeout=timeout)
        if r.returncode != 0:
            raise RuntimeError(f'chat rc={r.returncode}: {r.stderr[-300:]}')
        return r.stdout.strip()
    return chat


class OpenUnumChat:
    """POST /api/chat then poll /api/chat/pending (same protocol as s22-assistant)."""

    def __init__(self, api: str = 'http://127.0.0.1:18880', session: str = 'voice-call',
                 timeout: float = 240, poll: float = 1.0, context: str = SPOKEN_CONTEXT):
        self.api, self.session, self.timeout, self.poll, self.context = \
            api.rstrip('/'), session, timeout, poll, context

    def _http(self, method: str, path: str, body=None, timeout: float = 30) -> dict:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.api + path, data=data, method=method,
                                     headers={'content-type': 'application/json'})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read() or b'{}')

    def __call__(self, text: str) -> str:
        out = self._http('POST', '/api/chat', {
            'sessionId': self.session, 'message': text,
            'context': [{'title': 'voice call', 'content': self.context}]}, timeout=60)
        start = time.monotonic()
        while out.get('pending') and not out.get('reply'):
            if time.monotonic() - start > self.timeout:
                raise TimeoutError('agent reply timed out')
            time.sleep(self.poll)
            out = self._http('GET', f'/api/chat/pending?sessionId={self.session}', timeout=15)
        if out.get('error') and not out.get('reply'):
            raise RuntimeError(f"agent error: {out.get('error')}")
        return str(out.get('reply') or '').strip()


class VoiceLoop:
    """Far-end speech -> ASR -> agent -> TTS -> RTP, one turn at a time."""

    def __init__(self, rtp: RtpSession, asr, chat, tts, work_dir, asr_rate: int = 16000,
                 vad: EnergyVad | None = None, barge_in: bool = False, greeting: str | None = None,
                 pin: str | None = None, still_working_s: float = 12.0,
                 still_working_text: str = 'One moment.', log=_log):
        self.rtp, self.asr, self.chat, self.tts = rtp, asr, chat, tts
        self.work = Path(work_dir)
        self.work.mkdir(parents=True, exist_ok=True)
        self.asr_rate, self.barge_in, self.greeting = asr_rate, barge_in, greeting
        self.vad = vad or EnergyVad()
        self.pin, self.digits = pin, ''
        self.locked = bool(pin)
        self.still_working_s, self.still_working_text = still_working_s, still_working_text
        self.log = log
        self.q: queue.Queue = queue.Queue()
        self.turns: list[dict] = []
        self.turn = 0
        self.discard = False
        self.running = False
        self.thread: threading.Thread | None = None

    def attach(self) -> None:
        self.running = True
        self.rtp.on_audio.append(self.feed)
        self.rtp.on_dtmf.append(self.on_dtmf)
        self.thread = threading.Thread(target=self._worker, daemon=True)
        self.thread.start()
        if self.locked:
            self.log('voice: locked, waiting for DTMF PIN')
        elif self.greeting:
            self.q.put(('say', self.greeting))

    def stop(self) -> None:
        self.running = False
        self.q.put(None)
        if self.thread:
            self.thread.join(timeout=5)

    def on_dtmf(self, d: str) -> None:
        self.log(f'voice: DTMF {d}')
        if self.locked:
            self.digits = (self.digits + d)[-len(self.pin):]
            if self.digits == self.pin:
                self.locked = False
                self.log('voice: PIN accepted')
                self.q.put(('say', self.greeting or 'Hello.'))

    def feed(self, samples) -> None:
        for ev in self.vad.feed(samples):
            if ev[0] == 'start':
                if self.rtp.busy:
                    if self.barge_in:
                        self.rtp.flush()
                        self.log('voice: barge-in, playback flushed')
                    else:
                        self.discard = True
            elif ev[0] in ('segment', 'drop'):
                if self.discard or self.locked or ev[0] == 'drop':
                    self.discard = False
                    continue
                self.q.put(('seg', ev[1]))

    def _speak(self, text: str, tag: str) -> float:
        out = str(self.work / f'{tag}-out.wav')
        self.tts(text, out)
        rate, s = read_wav(out)
        s = resample(s, rate, RATE)
        self.rtp.play(s)
        return len(s) / RATE

    def _worker(self) -> None:
        while self.running:
            item = self.q.get()
            if item is None:
                break
            kind, val = item
            self.turn += 1
            tag = f'turn-{self.turn:03d}'
            rec = {'turn': self.turn, 'kind': kind, 't': round(time.time(), 3)}
            try:
                if kind == 'say':
                    rec['spoken'] = val
                    self._speak(val, tag)
                else:
                    t0 = time.monotonic()
                    wav = str(self.work / f'{tag}-in.wav')
                    write_wav(wav, self.asr_rate, resample(val, RATE, self.asr_rate))
                    rec['speech_s'] = round(len(val) / RATE, 2)
                    text = self.asr(wav)
                    rec['transcript'] = text
                    rec['asr_ms'] = int((time.monotonic() - t0) * 1000)
                    if not re.search(r'\w', text or ''):
                        rec['result'] = 'empty'
                        continue
                    self.log(f'voice: heard {text!r}')
                    t1 = time.monotonic()
                    reply = self._chat_with_filler(text, tag)
                    rec['chat_ms'] = int((time.monotonic() - t1) * 1000)
                    spoken = clean_for_speech(reply) or 'I have no answer for that.'
                    rec['reply'] = spoken
                    self.log(f'voice: reply {spoken!r}')
                    t2 = time.monotonic()
                    rec['reply_s'] = round(self._speak(spoken, tag), 2)
                    rec['tts_ms'] = int((time.monotonic() - t2) * 1000)
                rec['result'] = 'ok'
            except Exception as e:
                rec['result'] = 'error'
                rec['error'] = repr(e)[:300]
                self.log(f'voice: turn failed {e!r}')
            finally:
                self.turns.append(rec)
                try:
                    with open(self.work / 'turns.jsonl', 'a') as f:
                        f.write(json.dumps(rec) + '\n')
                except OSError:
                    pass

    def _chat_with_filler(self, text: str, tag: str) -> str:
        box: dict = {}

        def run():
            try:
                box['reply'] = self.chat(text)
            except Exception as e:
                box['error'] = e
        t = threading.Thread(target=run, daemon=True)
        t.start()
        t.join(self.still_working_s)
        if t.is_alive() and self.still_working_text:
            try:
                self._speak(self.still_working_text, tag + '-wait')
            except Exception as e:
                self.log(f'voice: filler failed {e!r}')
        t.join()
        if 'error' in box:
            raise box['error']
        return box.get('reply', '')


# ------------------------------------------------------------------ fast voice (rig streaming bridge)

_WS_GUID = '258EAFA5-E914-47DA-95CA-C5AB0DC85B11'


class WsClient:
    """Minimal RFC 6455 client (text/binary, ping/pong, close); stdlib only, thread-safe send."""

    def __init__(self, url: str, timeout: float = 3.0):
        u = urllib.parse.urlparse(url)
        if u.scheme != 'ws':
            raise ValueError('only ws:// is supported (use Tailscale for transport security)')
        self.host, self.port = u.hostname, u.port or 80
        self.path = (u.path or '/') + (('?' + u.query) if u.query else '')
        self.timeout = timeout
        self.sock: socket.socket | None = None
        self.lock = threading.Lock()
        self.closed = False
        self._buf = b''

    def connect(self) -> None:
        sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        key = base64.b64encode(os.urandom(16)).decode()
        sock.sendall((f'GET {self.path} HTTP/1.1\r\nHost: {self.host}:{self.port}\r\nUpgrade: websocket\r\n'
                      f'Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n').encode())
        head = b''
        while b'\r\n\r\n' not in head:
            chunk = sock.recv(4096)
            if not chunk:
                raise ConnectionError('websocket handshake: connection closed')
            head += chunk
            if len(head) > 65536:
                raise ConnectionError('websocket handshake: header too large')
        head, _, self._buf = head.partition(b'\r\n\r\n')
        lines = head.decode('latin-1').split('\r\n')
        if ' 101 ' not in lines[0] + ' ':
            raise ConnectionError(f'websocket handshake failed: {lines[0]}')
        hdr = {k.strip().lower(): v.strip() for k, _, v in (ln.partition(':') for ln in lines[1:])}
        want = base64.b64encode(hashlib.sha1((key + _WS_GUID).encode()).digest()).decode()
        if hdr.get('sec-websocket-accept') != want:
            raise ConnectionError('websocket handshake: bad Sec-WebSocket-Accept')
        sock.settimeout(None)
        self.sock = sock

    def _send(self, opcode: int, payload: bytes) -> None:
        n = len(payload)
        mask = os.urandom(4)
        head = bytes([0x80 | opcode])
        if n < 126:
            head += bytes([0x80 | n])
        elif n < 65536:
            head += bytes([0x80 | 126]) + struct.pack('>H', n)
        else:
            head += bytes([0x80 | 127]) + struct.pack('>Q', n)
        masked = (int.from_bytes(payload, 'big') ^ int.from_bytes((mask * (n // 4 + 1))[:n], 'big')).to_bytes(n, 'big') if n else b''
        with self.lock:
            if self.sock is None or self.closed:
                raise ConnectionError('websocket closed')
            self.sock.sendall(head + mask + masked)

    def send_text(self, text: str) -> None:
        self._send(0x1, text.encode())

    def send_binary(self, data: bytes) -> None:
        self._send(0x2, data)

    def _read(self, n: int) -> bytes:
        while len(self._buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError('websocket closed by peer')
            self._buf += chunk
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def recv(self):
        """Next data message as (opcode, payload); None once closed.  Answers ping, handles fragments."""
        frag_op, frag = None, b''
        while True:
            try:
                b0, b1 = self._read(2)
                n = b1 & 0x7F
                if n == 126:
                    n = struct.unpack('>H', self._read(2))[0]
                elif n == 127:
                    n = struct.unpack('>Q', self._read(8))[0]
                mask = self._read(4) if b1 & 0x80 else None
                data = self._read(n) if n else b''
            except (OSError, ConnectionError):
                self.closed = True
                return None
            if mask:
                data = (int.from_bytes(data, 'big') ^ int.from_bytes((mask * (n // 4 + 1))[:n], 'big')).to_bytes(n, 'big')
            op = b0 & 0x0F
            if op == 0x8:
                self.closed = True
                return None
            if op == 0x9:
                try:
                    self._send(0xA, data)
                except (OSError, ConnectionError):
                    pass
                continue
            if op == 0xA:
                continue
            if op in (0x1, 0x2):
                frag_op, frag = op, data
            elif op == 0x0:
                frag += data
            if b0 & 0x80:
                return frag_op, frag

    def close(self) -> None:
        if self.sock is not None and not self.closed:
            try:
                self._send(0x8, struct.pack('>H', 1000))
            except (OSError, ConnectionError):
                pass
        self.closed = True
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass


class FastVoiceLoop:
    """Bridge to the rig's unum-voice service: far-end PCM16 8 kHz out, reply PCM16 8 kHz in.

    VAD, endpointing, streaming ASR, LLM, TTS and barge-in all run on the rig; this class only moves
    audio.  Same attach/stop/on_dtmf/turns surface as VoiceLoop.  A {"type":"clear"} message from the rig
    (barge-in) flushes the RTP playout queue.  `fallback()` may return a classic VoiceLoop, used when the
    service cannot be reached at call start.
    """

    def __init__(self, rtp, url: str, token: str | None = None, greeting: str | None = None,
                 pin: str | None = None, barge_in: bool = True, lang: str = 'auto',
                 fallback: Callable | None = None, connect_timeout: float = 2.0,
                 reconnect_s: float = 1.0, client_factory=WsClient, log=_log):
        self.rtp, self.url, self.token, self.greeting = rtp, url, token, greeting
        self.pin, self.digits, self.locked = pin, '', bool(pin)
        self.barge_in, self.lang, self.fallback = barge_in, lang, fallback
        self.connect_timeout, self.reconnect_s, self.client_factory, self.log = \
            connect_timeout, reconnect_s, client_factory, log
        self.ws = None
        self.ready = False
        self.running = False
        self.delegate = None
        self.turns: list[dict] = []
        self.thread: threading.Thread | None = None
        self.tx_bytes = self.rx_bytes = 0

    # -- lifecycle
    def _open(self) -> bool:
        try:
            ws = self.client_factory(self.url, self.connect_timeout)
            ws.connect()
            hello = {'type': 'hello', 'codec': 'pcm16', 'rate': RATE, 'out_rate': RATE,
                     'barge_in': self.barge_in, 'lang': self.lang}
            if self.token:
                hello['token'] = self.token
            ws.send_text(json.dumps(hello))
            self.ws = ws
            return True
        except (OSError, ConnectionError, ValueError) as e:
            self.log(f'fast-voice: cannot connect to {self.url}: {e!r}')
            return False

    def attach(self) -> None:
        self.running = True
        if not self._open():
            if self.fallback:
                self.delegate = self.fallback()
                self.log('fast-voice: falling back to the classic voice loop')
                self.delegate.attach()
            else:
                self.running = False
            return
        self.rtp.on_audio.append(self.feed)
        self.rtp.on_dtmf.append(self.on_dtmf)
        self.thread = threading.Thread(target=self._rx_loop, daemon=True)
        self.thread.start()
        if self.locked:
            self.log('fast-voice: locked, waiting for DTMF PIN')
        elif self.greeting:
            self._say(self.greeting)

    def stop(self) -> None:
        self.running = False
        if self.delegate:
            self.delegate.stop()
        if self.ws:
            try:
                self.ws.send_text(json.dumps({'type': 'bye'}))
            except (OSError, ConnectionError):
                pass
            self.ws.close()
        if self.thread:
            self.thread.join(timeout=3)

    # -- tx
    def _say(self, text: str) -> None:
        try:
            self.ws.send_text(json.dumps({'type': 'say', 'text': text}))
        except (OSError, ConnectionError, AttributeError):
            pass

    def on_dtmf(self, d: str) -> None:
        if self.locked:
            self.digits = (self.digits + d)[-len(self.pin):]
            if self.digits == self.pin:
                self.locked = False
                self.log('fast-voice: PIN accepted')
                self._say(self.greeting or 'Hello.')

    def feed(self, samples) -> None:
        if self.locked or not self.running or self.ws is None or self.ws.closed:
            return
        data = _le(array.array('h', samples)).tobytes()
        try:
            self.ws.send_binary(data)
            self.tx_bytes += len(data)
        except (OSError, ConnectionError):
            pass                      # the rx loop notices and reconnects

    # -- rx
    def _rx_loop(self) -> None:
        cur: dict = {}
        while self.running:
            msg = self.ws.recv()
            if msg is None:
                if not self.running:
                    break
                self.log('fast-voice: connection lost, reconnecting')
                self.rtp.flush()
                while self.running and not self._open():
                    time.sleep(self.reconnect_s)
                continue
            op, payload = msg
            if op == 0x2:
                self.rx_bytes += len(payload)
                pcm = array.array('h')
                pcm.frombytes(payload[:len(payload) // 2 * 2])
                self.rtp.play(_le(pcm))
                continue
            try:
                ev = json.loads(payload.decode())
            except ValueError:
                continue
            t = ev.get('type')
            if t == 'clear':
                self.rtp.flush()
                self.log('fast-voice: barge-in, playback flushed')
            elif t == 'asr_final':
                cur = {'transcript': ev.get('text', '')}
                self.log(f"fast-voice: heard {ev.get('text', '')!r}")
            elif t == 'metrics':
                cur.update({'turn': ev.get('turn'), 'reply': ' '.join(ev.get('spoken') or []),
                            'first_audio_ms': ev.get('endpoint_to_first_audio_ms'),
                            'asr_ms': ev.get('asr_final_ms'), 'llm_ttft_ms': ev.get('llm_ttft_ms'),
                            'tts_ms': ev.get('tts_first_ms'), 'result': 'ok'})
                self.turns.append(cur)
                cur = {}
            elif t == 'error':
                self.log(f"fast-voice: server error {ev.get('error')!r}")
                if ev.get('error') == 'unauthorized':
                    self.running = False


# ------------------------------------------------------------------ presets / CLI

_SUPERTONIC = '/opt/s22-tts/sherpa-onnx-supertonic-tts-int8-2026-03-06'
_PARAKEET = '/opt/s22-asr/sherpa-onnx-nemo-parakeet_tdt_ctc_110m-en-36000-int8'
PRESETS = {
    # run inside the chroot (/mnt/omarchy-trial): paths are chroot paths
    's22': {
        'asr': ['taskset', '-c', '4-7', '/opt/s22-asr/bin/sherpa-onnx-offline', '--num-threads=4',
                '--model-type=nemo_ctc', f'--nemo-ctc-model={_PARAKEET}/model.int8.onnx',
                f'--tokens={_PARAKEET}/tokens.txt', '{wav}'],
        'tts': ['taskset', '-c', '4-7', '/opt/s22-tts/bin/sherpa-onnx-offline-tts', '--num-threads=4',
                '--sid=0', f'--supertonic-duration-predictor={_SUPERTONIC}/duration_predictor.int8.onnx',
                f'--supertonic-text-encoder={_SUPERTONIC}/text_encoder.int8.onnx',
                f'--supertonic-vector-estimator={_SUPERTONIC}/vector_estimator.int8.onnx',
                f'--supertonic-vocoder={_SUPERTONIC}/vocoder.int8.onnx',
                f'--supertonic-tts-json={_SUPERTONIC}/tts.json',
                f'--supertonic-unicode-indexer={_SUPERTONIC}/unicode_indexer.bin',
                f'--supertonic-voice-style={_SUPERTONIC}/voice.bin',
                '--output-filename={out}', '{text}'],
        'root': '',
    },
}
PRESETS['s22-outer'] = {   # run in the outer recovery userland; tools via chroot
    'asr': ['chroot', '/mnt/omarchy-trial'] + PRESETS['s22']['asr'],
    'tts': ['chroot', '/mnt/omarchy-trial'] + PRESETS['s22']['tts'],
    'root': '/mnt/omarchy-trial',
}


def _fifo_reader(path: str, rtp: RtpSession, stop: threading.Event) -> None:
    """Stream raw s16le 8 kHz mono from a FIFO (reopened after each writer)."""
    while not stop.is_set():
        try:
            with open(path, 'rb') as f:
                while not stop.is_set():
                    chunk = f.read(FRAME * 2)
                    if not chunk:
                        break
                    a = array.array('h'); a.frombytes(chunk[:len(chunk) // 2 * 2])
                    rtp.play(_le(a))
        except OSError:
            time.sleep(0.5)


def _common(p: argparse.ArgumentParser) -> None:
    g = p.add_argument_group('account / network')
    g.add_argument('--user', default=os.environ.get('S22_SIP_USER', 's22'))
    g.add_argument('--domain', default=os.environ.get('S22_SIP_DOMAIN'))
    g.add_argument('--password-env', default='S22_SIP_PASSWORD',
                   help='environment variable holding the SIP password (never on argv)')
    g.add_argument('--auth-user', help='digest username if different from --user')
    g.add_argument('--proxy', help='outbound proxy / registrar host[:port]')
    g.add_argument('--no-register', action='store_true')
    g.add_argument('--bind', default='0.0.0.0', help='local IP (e.g. the Tailscale 100.x address)')
    g.add_argument('--sip-port', type=int, default=5060)
    g.add_argument('--rtp-port', type=int, default=40000, help='first RTP port to try (0 = any)')
    g.add_argument('--public-ip', help='address to advertise in Contact/SDP')
    g.add_argument('--expires', type=int, default=300)
    g.add_argument('--codecs', default='PCMU,PCMA')
    g.add_argument('--allow-from', action='append', default=[],
                   help='accept calls only from this caller user/uri/IP/CIDR (repeatable)')
    g.add_argument('--allow-any', action='store_true', help='voice loop without --allow-from')
    g.add_argument('-v', '--verbose', action='store_true', help='log every SIP message')
    m = p.add_argument_group('media')
    m.add_argument('--play', help='WAV to send once the call is up (any rate/format)')
    m.add_argument('--play-fifo', help='FIFO of raw s16le 8 kHz mono to stream continuously')
    m.add_argument('--record', help='write far-end audio to this WAV (8 kHz mono)')
    m.add_argument('--rec-pipe', help='write far-end audio as raw s16le 8 kHz to this FIFO/file')
    m.add_argument('--duration', type=float, default=0, help='hang up after N seconds (0 = never)')
    m.add_argument('--hangup-after-play', action='store_true')
    m.add_argument('--dtmf', help='send these DTMF digits after the call is up')
    m.add_argument('--rtp-timeout', type=float, default=30.0)
    m.add_argument('--keep-listening', action='store_true', help='listen: stay up after a call')
    m.add_argument('--answer-delay', type=float, default=0.5)
    m.add_argument('--summary', help='write a JSON call summary here')
    v = p.add_argument_group('voice loop')
    v.add_argument('--voice-loop', action='store_true')
    v.add_argument('--preset', choices=sorted(PRESETS))
    v.add_argument('--asr-cmd', help='argv template, {wav} = 16 kHz mono WAV of the far-end turn')
    v.add_argument('--tts-cmd', help='argv template, {text} -> write WAV to {out}')
    v.add_argument('--chat-cmd', help='argv template, {text} -> reply on stdout (instead of OpenUnum)')
    v.add_argument('--chat-url', default='http://127.0.0.1:18880')
    v.add_argument('--chat-session', default='voice-call')
    v.add_argument('--asr-rate', type=int, default=16000)
    v.add_argument('--cmd-root', help='strip this prefix from {wav}/{out} (chroot tools)')
    v.add_argument('--work-dir', default='/tmp/s22-sip')
    v.add_argument('--greeting', default='Hi, it is your phone agent. Go ahead.')
    v.add_argument('--pin', help='require these DTMF digits before the agent listens')
    v.add_argument('--barge-in', action='store_true', help='far-end speech interrupts playback')
    v.add_argument('--vad-min-db', type=float, default=-42.0)
    v.add_argument('--vad-hangover-ms', type=int, default=700)
    v.add_argument('--fast-voice', metavar='WS_URL',
                   help='stream the call to the rig unum-voice service (ws://100.x.y.z:8130) instead of the '
                        'ASR/chat/TTS command loop; falls back to it when the service is unreachable')
    v.add_argument('--fast-voice-token-env', default='UNUM_VOICE_TOKEN',
                   help='env var holding the shared secret the service expects')
    v.add_argument('--fast-voice-lang', default='auto')


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.split('\n\n')[0],
                                formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    sub = p.add_subparsers(dest='cmd', required=True)
    t = sub.add_parser('tone', help='write a test-tone WAV')
    t.add_argument('out'); t.add_argument('--freq', type=float, default=440)
    t.add_argument('--seconds', type=float, default=2.0); t.add_argument('--rate', type=int, default=RATE)
    lp = sub.add_parser('listen', help='(register and) auto-answer incoming calls')
    _common(lp)
    cp = sub.add_parser('call', help='place a call')
    cp.add_argument('target', help='sip:user@host[:port], user (with --domain) or IP[:port]')
    cp.add_argument('--ring-timeout', type=float, default=60)
    _common(cp)
    return p


def _make_voice_loop(args, call: Call, log):
    if getattr(args, 'fast_voice', None):
        can_fall_back = bool((args.asr_cmd or PRESETS.get(args.preset or '', {}).get('asr'))
                             and (args.tts_cmd or PRESETS.get(args.preset or '', {}).get('tts')))
        return FastVoiceLoop(call.rtp, args.fast_voice, token=os.environ.get(args.fast_voice_token_env) or None,
                             greeting=args.greeting or None, pin=args.pin, barge_in=True,
                             lang=args.fast_voice_lang,
                             fallback=(lambda: _make_classic_loop(args, call, log)) if can_fall_back else None,
                             log=log)
    return _make_classic_loop(args, call, log)


def _make_classic_loop(args, call: Call, log) -> VoiceLoop:
    preset = PRESETS.get(args.preset or '', {})
    root = args.cmd_root if args.cmd_root is not None else preset.get('root', '')
    asr_t = args.asr_cmd or preset.get('asr')
    tts_t = args.tts_cmd or preset.get('tts')
    if not asr_t or not tts_t:
        raise SystemExit('--voice-loop needs --asr-cmd and --tts-cmd (or --preset)')
    chat = command_chat(args.chat_cmd) if args.chat_cmd else \
        OpenUnumChat(args.chat_url, args.chat_session)
    work = Path(args.work_dir) / time.strftime('%Y%m%d-%H%M%S')
    vad = EnergyVad(min_db=args.vad_min_db, hangover_ms=args.vad_hangover_ms)
    return VoiceLoop(call.rtp, command_asr(asr_t, root), chat, command_tts(tts_t, root), work,
                     asr_rate=args.asr_rate, vad=vad, barge_in=args.barge_in,
                     greeting=args.greeting or None, pin=args.pin, log=log)


def run(args, log=_log) -> int:
    if args.cmd == 'tone':
        write_wav(args.out, args.rate, tone(args.freq, args.seconds, args.rate))
        return 0
    if (args.voice_loop or args.fast_voice) and not args.allow_from and not args.allow_any and args.cmd == 'listen':
        raise SystemExit('--voice-loop/--fast-voice on listen needs --allow-from (or explicit --allow-any): '
                         'anyone who can reach this port could talk to the agent')
    password = os.environ.get(args.password_env) if args.password_env else None
    ua = SipUA(user=args.user, domain=args.domain, password=password, auth_user=args.auth_user,
               bind_ip=args.bind, sip_port=args.sip_port, rtp_port=args.rtp_port, proxy=args.proxy,
               public_ip=args.public_ip, expires=args.expires,
               codecs=[c.strip().upper() for c in args.codecs.split(',') if c.strip()],
               allow_from=args.allow_from, answer_delay=args.answer_delay, log=log,
               verbose=args.verbose)
    ua.start()
    state: dict = {'calls': [], 'loops': []}
    stop = threading.Event()

    def on_media(call: Call) -> None:
        rtp = call.rtp
        rtp.on_dtmf.append(lambda d: log(f'call: received DTMF {d}'))
        if args.record:
            w = WavStreamWriter(args.record)
            rtp.on_audio.append(w)
            ua.on_ended.append(lambda c, w=w: w.close() if c is call else None)
        if args.rec_pipe:
            fh = open(args.rec_pipe, 'wb', buffering=0)
            rtp.on_audio.append(lambda s: fh.write(_le(array.array('h', s)).tobytes()))
        if args.play:
            log(f'call: playing {args.play} ({rtp.play_wav(args.play):.1f}s)')
        if args.play_fifo:
            threading.Thread(target=_fifo_reader, args=(args.play_fifo, rtp, call.ended),
                             daemon=True).start()
        if args.dtmf and rtp.dtmf_pt is not None:
            rtp.send_dtmf(args.dtmf)
        if args.voice_loop or args.fast_voice:
            loop = _make_voice_loop(args, call, log)
            loop.attach()
            state['loops'].append(loop)
        state['calls'].append(call)

    ua.on_media.append(on_media)
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    rc = 0
    try:
        if args.domain and password and not args.no_register:
            if not ua.register():
                log('register failed'); rc = 2
                if args.cmd == 'listen':
                    return rc
            else:
                ua.keep_registered()
        if args.cmd == 'call':
            call = ua.call(args.target, args.ring_timeout)
            if call is None:
                return 1
            _supervise(ua, call, args, stop)
        else:
            log(f'listening on {ua.bind_ip}:{ua.port} as {ua.aor(ua.bind_ip if ua.bind_ip != "0.0.0.0" else "8.8.8.8")}')
            while not stop.is_set():
                live = [c for c in ua.calls.values() if c.state != 'ended']
                if live and live[0].rtp is not None:
                    _supervise(ua, live[0], args, stop)
                    if not args.keep_listening:
                        break
                stop.wait(0.2)
    except KeyboardInterrupt:
        pass
    finally:
        for loop in state['loops']:
            loop.stop()
        ua.close()
        summary = [{'call_id': c.call_id, 'direction': c.direction, 'remote': c.remote_hdr,
                    'codec': c.rtp.codec if c.rtp else None, 'rx': c.rtp.rx if c.rtp else 0,
                    'tx': c.rtp.tx if c.rtp else 0, 'lost': c.rtp.jb.lost if c.rtp else 0,
                    'seconds': round(time.time() - c.started, 1), 'end': c.reason,
                    'turns': next((l.turns for l in state['loops'] if l.rtp is c.rtp), [])}
                   for c in state['calls']]
        print(json.dumps(summary, indent=1))
        if args.summary:
            Path(args.summary).write_text(json.dumps(summary, indent=1))
    return rc


def _supervise(ua: SipUA, call: Call, args, stop: threading.Event) -> None:
    t0 = time.monotonic()
    while not call.ended.is_set() and not stop.is_set():
        if args.duration and time.monotonic() - t0 >= args.duration:
            break
        if args.hangup_after_play and call.rtp and not call.rtp.busy and time.monotonic() - t0 > 1:
            time.sleep(0.4)
            break
        if call.rtp and time.monotonic() - call.rtp.last_rx > args.rtp_timeout:
            ua.log(f'call: no RTP for {args.rtp_timeout:.0f}s')
            break
        call.ended.wait(0.1)
    if not call.ended.is_set():
        ua.hangup(call)


def main(argv=None) -> int:
    return run(build_parser().parse_args(argv))


if __name__ == '__main__':
    sys.exit(main())
