#!/usr/bin/env python3
"""s22_rigvoice: the phone's link to the rig's owner voice and Greek/English speech recognition (stdlib only).

Owner 2026-10-22: ONE voice everywhere, his own, and he wants to speak Greek to the phone.

  TTS  the rig's VoxCPM2 + LoRA "antonis" (voxcpm-greek.service, reached at 100.76.5.104:8890 through
       s22-voxcpm-tailnet.service): Greek AND English, streamed (first audio ~0.2 s, RTF ~0.9).
  ASR  the rig's whisper-server large-v3-turbo (s22-whisper.service, 100.76.5.104:8891), language
       auto-detected and narrowed to Greek or English.

Both bind the rig's Tailscale address only. Every call has hard deadlines (TTS: first audio within
ttfa_s = 2 s, no stall over 4 s, the whole clause within 3 s + 0.12 s per character; ASR: 2 s to connect,
20 s in all); a failure writes a down-marker so the next calls skip the rig for down_s (60 s) instead of
paying the timeout again. The callers then fall back on the phone: s22-say -> Piper el-antonis (the same
voice distilled; its English is only partly intelligible), s22-assistant -> Parakeet (English only).

S22_RIGVOICE_LENIENT=1 (offline renders such as the filler clips): long deadlines, no down-marker.
Settings: /srv/s22/state/rigvoice.json overrides DEFAULTS (e.g. {"tts_url": "http://100.76.5.104:1"} to
force the fallback, {"enabled": false} to stay on the phone); S22_RIGVOICE_CONFIG names another file.

CLI (installed as /srv/s22/hardware/bin/s22_rigvoice.py):
  s22_rigvoice.py tts OUT.wav TEXT...    exit 0 = OUT.wav written (48 kHz mono s16, already at -16 LUFS),
                                         1 = use the fallback
  s22_rigvoice.py asr IN.wav             JSON {"text", "lang", "ms"} on stdout, exit 1 on failure
  s22_rigvoice.py status                 JSON: settings, down-markers, rig health
"""
from __future__ import annotations

import http.client
import json
import os
import socket
import sys
import time
import uuid
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))      # s22_loudnorm.py is installed next to this file
from urllib.parse import urlparse

DEFAULTS = {
    'enabled': True,
    'tts_url': 'http://100.76.5.104:8890',
    'asr_url': 'http://100.76.5.104:8891',
    'voice': 'antonis',
    'ttfa_s': 2.0,             # no first audio by then -> fallback
    'stall_s': 4.0,            # no audio for this long mid-stream -> fallback
    'tts_base_s': 3.0,         # whole-clause budget: base + per_char * len(text) (~RTF 1.7 allowed)
    'tts_per_char_s': 0.12,
    'asr_connect_s': 2.0,
    'asr_total_s': 20.0,
    'down_s': 60.0,            # after a failure, skip the rig this long
    'langs': ['el', 'en'],     # the languages the owner speaks; whisper's guess is narrowed to these
}
CONFIG = Path(os.environ.get('S22_RIGVOICE_CONFIG', '/srv/s22/state/rigvoice.json'))
DOWN_DIR = Path(os.environ.get('S22_RIGVOICE_RUN', '/run'))


class RigUnavailable(Exception):
    pass


def load_config(path: Path = CONFIG) -> dict:
    cfg = dict(DEFAULTS)
    try:
        cfg.update(json.loads(path.read_text()))
    except (OSError, ValueError):
        pass
    if os.environ.get('S22_RIGVOICE_LENIENT') == '1':   # offline renders (filler clips): wait, never mark down
        cfg.update(ttfa_s=max(cfg['ttfa_s'], 30.0), stall_s=max(cfg['stall_s'], 30.0), tts_base_s=60.0,
                   tts_per_char_s=1.0, down_s=0.0)
    return cfg


# ---------------------------------------------------------------- down markers

def _marker(kind: str) -> Path:
    return DOWN_DIR / f's22-rigvoice.{kind}.down'


def is_down(kind: str, cfg: dict, now: float | None = None) -> bool:
    try:
        t = float(_marker(kind).read_text().split()[0])
    except (OSError, ValueError, IndexError):
        return False
    return (now if now is not None else time.time()) - t < float(cfg['down_s'])


def mark_down(kind: str, why: str) -> None:
    try:
        _marker(kind).write_text(f'{time.time():.3f} {why[:200]}\n')
    except OSError:
        pass


def clear_down(kind: str) -> None:
    try:
        _marker(kind).unlink()
    except OSError:
        pass


def _conn(url: str, timeout: float) -> tuple[http.client.HTTPConnection, str]:
    u = urlparse(url)
    return http.client.HTTPConnection(u.hostname or '127.0.0.1', u.port or 80, timeout=timeout), u.path.rstrip('/')


# ---------------------------------------------------------------- TTS

def tts_pcm(text: str, cfg: dict, clock=time.monotonic) -> tuple[bytes, int, dict]:
    """(pcm s16le mono, sample rate, timing) from the rig, or RigUnavailable."""
    if not cfg.get('enabled', True):
        raise RigUnavailable('disabled')
    if is_down('tts', cfg):
        raise RigUnavailable('marked down')
    t0 = clock()
    budget = float(cfg['tts_base_s']) + float(cfg['tts_per_char_s']) * len(text)
    conn, base = _conn(cfg['tts_url'], float(cfg['ttfa_s']))
    try:
        try:
            conn.request('POST', base + '/v1/audio/speech/stream',
                         json.dumps({'input': text, 'voice': cfg['voice']}).encode(),
                         {'Content-Type': 'application/json'})
            r = conn.getresponse()
            if r.status != 200:
                raise RigUnavailable(f'HTTP {r.status}')
            rate = int(r.getheader('x-sample-rate') or 48000)
            chunks, ttfa = [], None
            while True:
                data = r.read1(19200)
                if ttfa is None:
                    ttfa = clock() - t0
                    conn.sock.settimeout(float(cfg['stall_s']))
                if not data:
                    break
                chunks.append(data)
                if clock() - t0 > budget:
                    raise RigUnavailable(f'too slow: over {budget:.1f} s')
        except (OSError, http.client.HTTPException, ValueError) as e:
            raise RigUnavailable(f'{type(e).__name__}: {e}') from None
    except RigUnavailable as e:
        mark_down('tts', str(e))
        raise
    finally:
        conn.close()
    pcm = b''.join(chunks)
    pcm = pcm[:len(pcm) // 2 * 2]
    if len(pcm) < rate // 10:                      # under 50 ms of audio: a failed generation
        mark_down('tts', 'empty audio')
        raise RigUnavailable('empty audio')
    return pcm, rate, {'ttfa_s': round(ttfa or 0.0, 3), 'total_s': round(clock() - t0, 3),
                       'audio_s': round(len(pcm) / 2 / rate, 3)}


def normalise(pcm: bytes, rate: int) -> tuple[bytes, dict]:
    """The -16 LUFS / -2 dBFS target of every phone voice (s22_loudnorm), fast enough for the answer path:
    loudness is measured on a 3:1 box-decimated copy of the first 4 s (the K-weighting is computed for that
    rate; within ~0.3 LU of the full-rate measure on speech), the gain + soft limiter is applied to all of it.
    About 0.2 s on the phone for a 3 s clause instead of ~1 s. Without s22_loudnorm the audio is returned as is."""
    try:
        import array
        import s22_loudnorm as LN
    except ImportError:
        return pcm, {'normalised': False}
    a = array.array('h')
    a.frombytes(pcm)
    if sys.byteorder == 'big':
        a.byteswap()
    k = 3 if rate >= 32000 else 1
    n = min(len(a), int(4.0 * rate)) // k * k
    dec = [(a[i] + a[i + 1] + a[i + 2]) / 98304.0 for i in range(0, n, 3)] if k == 3 else [v / 32768.0 for v in a[:n]]
    lufs = LN.measure_lufs(dec, rate / k, max_s=0)
    if lufs is None:
        return pcm, {'normalised': False}
    gain_db = max(-30.0, min(LN.MAX_GAIN_DB, LN.TARGET_LUFS - lufs))
    out = LN.apply_gain(a, 10 ** (gain_db / 20))
    if sys.byteorder == 'big':
        out.byteswap()
    return out.tobytes(), {'normalised': True, 'before_lufs': round(lufs, 2), 'gain_db': round(gain_db, 2)}


def write_wav(path, pcm: bytes, rate: int) -> None:
    tmp = f'{path}.part'
    with wave.open(tmp, 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    os.replace(tmp, path)


def tts_to_wav(text: str, out, cfg: dict | None = None) -> dict:
    cfg = cfg or load_config()
    pcm, rate, info = tts_pcm(text, cfg)
    t = time.monotonic()
    pcm, norm = normalise(pcm, rate)
    write_wav(out, pcm, rate)
    info.update(norm, norm_s=round(time.monotonic() - t, 3))
    return info


# ---------------------------------------------------------------- ASR

def _multipart(fields: dict, file_field: str, filename: str, data: bytes) -> tuple[bytes, str]:
    b = uuid.uuid4().hex
    parts = []
    for k, v in fields.items():
        parts.append(f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    parts.append(f'--{b}\r\nContent-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'
                 f'Content-Type: audio/wav\r\n\r\n'.encode() + data + b'\r\n')
    parts.append(f'--{b}--\r\n'.encode())
    return b''.join(parts), f'multipart/form-data; boundary={b}'


WHISPER_LANG = {'greek': 'el', 'english': 'en'}


def pick_lang(detected: str | None, probs: dict | None, langs) -> tuple[str, bool]:
    """(language among `langs`, whether whisper's own guess was outside them)."""
    code = WHISPER_LANG.get(str(detected or '').lower(), str(detected or '').lower())
    if code in langs:
        return code, False
    probs = probs or {}
    best = max(langs, key=lambda l: float(probs.get(l, 0.0)))
    return best, True


def _inference(data: bytes, cfg: dict, language: str) -> dict:
    conn, base = _conn(cfg['asr_url'], float(cfg['asr_connect_s']))
    try:
        body, ctype = _multipart({'response_format': 'verbose_json', 'language': language, 'temperature': '0.0'},
                                 'file', 'utterance.wav', data)
        conn.connect()
        conn.sock.settimeout(float(cfg['asr_total_s']))
        conn.request('POST', base + '/inference', body, {'Content-Type': ctype})
        r = conn.getresponse()
        raw = r.read()
        if r.status != 200:
            raise RigUnavailable(f'HTTP {r.status}: {raw[:120]!r}')
        return json.loads(raw)
    finally:
        conn.close()


def transcribe(wav_path, cfg: dict | None = None, clock=time.monotonic) -> dict:
    """{"text", "lang", "ms", "redo"} from the rig's whisper, or RigUnavailable."""
    cfg = cfg or load_config()
    if not cfg.get('enabled', True):
        raise RigUnavailable('disabled')
    if is_down('asr', cfg):
        raise RigUnavailable('marked down')
    t0 = clock()
    data = Path(wav_path).read_bytes()
    langs = list(cfg.get('langs') or ['el', 'en'])
    try:
        d = _inference(data, cfg, 'auto')
        lang, outside = pick_lang(d.get('language'), d.get('language_probabilities'), langs)
        if outside:                                  # e.g. accented English guessed as another language
            d = _inference(data, cfg, lang)
    except (OSError, http.client.HTTPException, ValueError, RigUnavailable) as e:
        why = e if isinstance(e, RigUnavailable) else RigUnavailable(f'{type(e).__name__}: {e}')
        mark_down('asr', str(why))
        raise why from None
    text = ' '.join(str(d.get('text', '')).split())
    return {'text': text, 'lang': lang, 'ms': int((clock() - t0) * 1000), 'redo': outside}


# ---------------------------------------------------------------- CLI

def _health(url: str) -> object:
    try:
        conn, base = _conn(url, 2.0)
        conn.request('GET', base + ('/health' if url.endswith('8890') else '/'))
        r = conn.getresponse()
        body = r.read(300)
        conn.close()
        return json.loads(body) if r.status == 200 and body.startswith(b'{') else r.status
    except (OSError, http.client.HTTPException, ValueError) as e:
        return f'{type(e).__name__}: {e}'


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    cfg = load_config()
    if argv[:1] == ['tts'] and len(argv) >= 3:
        try:
            info = tts_to_wav(' '.join(argv[2:]), argv[1], cfg)
        except RigUnavailable as e:
            print(f's22_rigvoice: tts unavailable: {e}', file=sys.stderr)
            return 1
        print(json.dumps(info))
        return 0
    if argv[:1] == ['asr'] and len(argv) == 2:
        try:
            print(json.dumps(transcribe(argv[1], cfg), ensure_ascii=False))
        except RigUnavailable as e:
            print(f's22_rigvoice: asr unavailable: {e}', file=sys.stderr)
            return 1
        return 0
    if argv[:1] == ['status']:
        down = {k: _marker(k).read_text().strip() if _marker(k).exists() else None for k in ('tts', 'asr')}
        print(json.dumps({'config': cfg, 'down': down, 'tts': _health(cfg['tts_url']),
                          'asr': _health(cfg['asr_url'])}, ensure_ascii=False))
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == '__main__':
    socket.setdefaulttimeout(30)
    sys.exit(main())
