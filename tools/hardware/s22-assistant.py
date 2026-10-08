#!/usr/bin/env python3
"""s22-assistant: push-to-talk voice assistant for the S22 (button hook).

Installed as /srv/s22/hardware/bin/s22-assistant; /srv/s22/buttons/hooks/assistant
points at it, so holding volume up starts it (see s22-buttons).

  1. cue: earcon as soon as the microphone is open
  2. record: while volume up is held (S22_PTT=hold, S22_PTT_STOP from the
     daemon); a release within EARLY_S of the cue, or a hook started on
     release (no S22_PTT), records a fixed FIXED_S window instead
  3. speech to text on the phone: sherpa-onnx offline ASR (NeMo Parakeet TDT
     CTC 110M int8 by default, Moonshine tiny selectable), big cores only
  4. agent: the phone's OpenUnum (started if needed), session "voice",
     POST /api/chat then GET /api/chat/pending until the reply arrives
  5. speak the reply with s22-say (markdown stripped, long replies shortened)

One interaction at a time (flock); a trigger while busy is ignored.  Every
interaction is appended to /srv/s22/buttons/assistant.jsonl.

  s22-assistant                 run as the button hook
  s22-assistant --wav FILE      skip recording: transcribe FILE (test mode)
  s22-assistant --text TEXT     skip recording and ASR (test mode)
  s22-assistant --no-speak ...  print instead of speaking (test mode)
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

CHROOT = Path('/mnt/omarchy-trial')
ASR_DIR = '/opt/s22-asr'                       # inside the chroot
WORK = '/tmp/s22-assistant'                    # inside the chroot
BIN = Path('/srv/s22/hardware/bin')
BASE = Path('/srv/s22/buttons')
LOCK = Path('/run/s22-assistant.lock')
REC_STOP = Path('/run/s22-assistant.rec-stop')
REC_READY = Path('/run/s22-assistant.rec-ready')
API = 'http://127.0.0.1:18880'

DEFAULTS = {
    'engine': 'parakeet',      # parakeet | moonshine
    'session': 'voice',
    'fixed_s': 6.0,            # fixed window when the key is not held
    'max_s': 30.0,             # longest hold recording
    'early_s': 1.0,            # release this soon after the cue -> fixed window
    'tail_s': 0.3,             # keep recording briefly after release
    'reply_timeout_s': 240.0,
    'max_spoken_chars': 450,
    'still_working_s': 15.0,
}

VOICE_CONTEXT = (
    'This message was spoken to the owner\'s Galaxy S22 phone with push-to-talk and '
    'transcribed by on-device speech recognition, so it may contain recognition errors. '
    'Your reply will be read aloud by text-to-speech: answer in one to three short, '
    'plain spoken sentences unless asked for more. No markdown, lists, tables, code '
    'or URLs in the spoken answer.')

ENGINES = {
    'parakeet': ['--model-type=nemo_ctc',
                 '--nemo-ctc-model={d}/sherpa-onnx-nemo-parakeet_tdt_ctc_110m-en-36000-int8/model.int8.onnx',
                 '--tokens={d}/sherpa-onnx-nemo-parakeet_tdt_ctc_110m-en-36000-int8/tokens.txt'],
    'moonshine': ['--moonshine-encoder={d}/sherpa-onnx-moonshine-tiny-en-quantized-2026-02-27/encoder_model.ort',
                  '--moonshine-merged-decoder={d}/sherpa-onnx-moonshine-tiny-en-quantized-2026-02-27/decoder_model_merged.ort',
                  '--tokens={d}/sherpa-onnx-moonshine-tiny-en-quantized-2026-02-27/tokens.txt'],
}


# ---------------------------------------------------------------- pure helpers

def load_config(base: Path = BASE) -> dict:
    cfg = dict(DEFAULTS)
    try:
        cfg.update(json.loads((base / 'assistant.json').read_text()))
    except (OSError, ValueError):
        pass
    if os.environ.get('S22_ASR_ENGINE'):
        cfg['engine'] = os.environ['S22_ASR_ENGINE']
    return cfg


def asr_argv(engine: str, wav: str, asr_dir: str = ASR_DIR) -> list[str]:
    if engine not in ENGINES:
        raise ValueError(f'unknown ASR engine {engine}')
    return (['taskset', '-c', '4-7', f'{asr_dir}/bin/sherpa-onnx-offline', '--num-threads=4']
            + [a.format(d=asr_dir) for a in ENGINES[engine]] + [wav])


def parse_asr(output: str) -> str:
    """sherpa-onnx-offline prints one JSON object per file; take the text."""
    for line in reversed(output.splitlines()):
        line = line.strip()
        if line.startswith('{') and '"text"' in line:
            try:
                return str(json.loads(line).get('text', '')).strip()
            except ValueError:
                m = re.search(r'"text":\s*"((?:[^"\\]|\\.)*)"', line)
                if m:
                    return m.group(1).strip()
    return ''


def stop_deadline(cue_t: float, release_t: float | None, now: float, cfg: dict) -> float | None:
    """When to stop recording (monotonic-free: all wall-clock seconds).

    None = keep waiting for the release.  A release before the cue + early_s
    (the owner let go to speak, or the hook ran on release) = fixed window.
    """
    if release_t is None:
        return None
    if release_t - cue_t < cfg['early_s']:
        return cue_t + cfg['fixed_s']
    return max(now, release_t + cfg['tail_s'])


_MD = [
    (re.compile(r'```.*?```', re.S), ' (code omitted) '),
    (re.compile(r'`([^`]*)`'), r'\1'),
    (re.compile(r'!\[([^\]]*)\]\([^)]*\)'), r'\1'),
    (re.compile(r'\[([^\]]+)\]\([^)]*\)'), r'\1'),
    (re.compile(r'https?://\S+'), 'a link'),
    (re.compile(r'^\s{0,3}#{1,6}\s*', re.M), ''),
    (re.compile(r'^\s*>\s?', re.M), ''),
    (re.compile(r'^\s*[-*+]\s+', re.M), ''),
    (re.compile(r'^\s*\d+[.)]\s+', re.M), ''),
    (re.compile(r'^\s*\|?\s*:?-{3,}.*$', re.M), ''),
    (re.compile(r'\|'), ', '),
    (re.compile(r'(\*\*|__|\*|~~)'), ''),
    (re.compile(r'(?<!\w)_(?!\w)'), ''),
    (re.compile(r'\s*°\s*C\b'), ' degrees Celsius'),
    (re.compile(r'\s*°\s*F\b'), ' degrees Fahrenheit'),
    (re.compile(r'\s*°'), ' degrees'),
    (re.compile(r'(\d)\s*%'), r'\1 percent'),
]


def clean_for_speech(text: str) -> str:
    for rx, sub in _MD:
        text = rx.sub(sub, text)
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    out = ''
    for l in lines:                      # join lines; keep sentence breaks
        if out and not out.endswith(('.', '!', '?', ':', ';', ',')):
            out += '.'
        out += (' ' if out else '') + l
    return re.sub(r'\s+', ' ', out).replace(' ,', ',').strip()


def summarize(text: str, limit: int) -> tuple[str, bool]:
    """Whole sentences up to `limit` characters; (spoken, truncated)."""
    if len(text) <= limit:
        return text, False
    sentences = re.split(r'(?<=[.!?])\s+', text)
    out = ''
    for s in sentences:
        if out and len(out) + 1 + len(s) > limit:
            break
        out = (out + ' ' + s).strip()
    if len(out) > limit:                 # one very long sentence
        out = out[:limit].rsplit(' ', 1)[0].rstrip(',;:') + '.'
    return out + ' The full answer is in the voice chat.', True


# ---------------------------------------------------------------- side effects

class Assistant:
    def __init__(self, cfg: dict, speak: bool = True, base: Path = BASE):
        self.cfg = cfg
        self.speak_enabled = speak
        self.base = base
        self.rec = {'t': round(time.time(), 3), 'engine': cfg['engine'],
                    'mode': os.environ.get('S22_PTT') or 'release'}
        self.agent_ready: dict = {}

    # -- audio
    def say(self, text: str = '', wav: str | None = None, wait: bool = True):
        if not self.speak_enabled:
            if text:
                print(f'[say] {text}', flush=True)
            return None
        argv = [str(BIN / 's22-say')] + (['--wav', wav] if wav else [text])
        p = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        if wait:
            p.wait(timeout=180)
        return p

    def record(self, out_chroot: str) -> float:
        """Record into the chroot path; returns the recorded seconds."""
        for f in (REC_STOP, REC_READY):
            f.unlink(missing_ok=True)
        ptt_stop = os.environ.get('S22_PTT_STOP')
        hold = os.environ.get('S22_PTT') == 'hold' and ptt_stop
        max_s = self.cfg['max_s'] if hold else self.cfg['fixed_s']
        argv = [str(BIN / 's22-rec'), '--stop-file', str(REC_STOP),
                str(int(max_s + 0.999)), str(CHROOT) + out_chroot]
        proc = subprocess.Popen(argv, env={**os.environ, 'S22_REC_READY': str(REC_READY)},
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL)
        t0 = time.time()
        while not REC_READY.exists() and proc.poll() is None and time.time() - t0 < 5:
            time.sleep(0.02)
        self.say(wav=f'{ASR_DIR}/listen.wav')           # mic is open: cue
        cue = time.time()
        self.rec['cue_ms'] = int((cue - t0) * 1000)
        deadline = None
        while proc.poll() is None:
            now = time.time()
            if deadline is None:
                rel = None
                if hold and Path(ptt_stop).exists():
                    try:
                        rel = float(json.loads(Path(ptt_stop).read_text())['t'])
                    except (OSError, ValueError, KeyError, TypeError):
                        rel = now
                elif not hold:
                    rel = cue                             # fixed window
                deadline = stop_deadline(cue, rel, now, self.cfg)
            if deadline is not None and now >= deadline:
                REC_STOP.touch()
                break
            time.sleep(0.03)
        proc.wait(timeout=30)
        REC_STOP.unlink(missing_ok=True)
        if proc.returncode != 0:
            raise RuntimeError(f's22-rec failed rc={proc.returncode}')
        return round(time.time() - cue, 2)

    def transcribe(self, wav_chroot: str) -> str:
        t = time.time()
        r = subprocess.run(['chroot', str(CHROOT)] + asr_argv(self.cfg['engine'], wav_chroot),
                           capture_output=True, text=True, timeout=120)
        self.rec['asr_ms'] = int((time.time() - t) * 1000)
        text = parse_asr(r.stdout + '\n' + r.stderr)
        if r.returncode != 0 and not text:
            raise RuntimeError(f'asr rc={r.returncode}: {r.stderr[-300:]}')
        return text

    # -- agent
    def _http(self, method: str, path: str, body: dict | None = None, timeout: float = 30):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(API + path, data=data, method=method,
                                     headers={'content-type': 'application/json'})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b'{}')

    def healthy(self) -> bool:
        try:
            self._http('GET', '/api/health', timeout=4)
            return True
        except (OSError, ValueError, urllib.error.URLError):
            return False

    def ensure_agent(self) -> None:
        """Run in a thread during recording: start OpenUnum if it is down."""
        t = time.time()
        ok = self.healthy()
        if not ok:
            self.agent_ready['started'] = True
            try:
                subprocess.run([str(BIN / 's22-openunum'), 'start'], capture_output=True, timeout=120)
            except (OSError, subprocess.SubprocessError):
                pass
            ok = self.healthy()
        self.agent_ready.update(ok=ok, ms=int((time.time() - t) * 1000))

    def ask(self, text: str) -> str:
        session = self.cfg['session']
        since = time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime(time.time() - 2))
        status, out = self._http('POST', '/api/chat', {
            'sessionId': session, 'message': text,
            'context': [{'title': 'voice input', 'content': VOICE_CONTEXT}]}, timeout=60)
        start = time.time()
        warned = False
        while out.get('pending') and not out.get('reply'):
            if time.time() - start > self.cfg['reply_timeout_s']:
                raise TimeoutError('assistant reply timed out')
            if not warned and time.time() - start > self.cfg['still_working_s']:
                warned = True
                self.say('One moment.', wait=False)
            time.sleep(1.0)
            _, out = self._http('GET', f'/api/chat/pending?sessionId={session}', timeout=15)
            if not out.get('pending') and not out.get('reply'):
                # completed but consumed elsewhere (e.g. the WebUI): read history
                _, act = self._http('GET', f'/api/sessions/{session}/activity?since={since}', timeout=15)
                msgs = [m for m in act.get('messages', []) if m.get('role') == 'assistant']
                out = {'reply': msgs[-1].get('content', '') if msgs else ''}
        if out.get('error') and not out.get('reply'):
            raise RuntimeError(f"assistant error: {out.get('error')}")
        model = out.get('model') or {}
        self.rec['model'] = model.get('activeModel') or model.get('model') if isinstance(model, dict) else model
        return str(out.get('reply') or '').strip()

    def log(self) -> None:
        try:
            with open(self.base / 'assistant.jsonl', 'a') as f:
                f.write(json.dumps(self.rec, sort_keys=True) + '\n')
        except OSError:
            pass

    def run(self, wav: str | None = None, text: str | None = None) -> int:
        t0 = time.time()
        agent = threading.Thread(target=self.ensure_agent, daemon=True)
        agent.start()
        try:
            work = CHROOT / WORK.lstrip('/')
            work.mkdir(parents=True, exist_ok=True)
            if text is None:
                if wav is None:
                    wav = f'{WORK}/in.wav'
                    self.rec['record_s'] = self.record(wav)
                    self.say(wav=f'{ASR_DIR}/done.wav', wait=False)
                else:
                    self.rec['mode'] = 'wav'
                text = self.transcribe(wav)
            else:
                self.rec['mode'] = 'text'
            self.rec['transcript'] = text
            if not re.search(r'\w', text):
                self.rec['result'] = 'empty'
                self.say("Sorry, I didn't catch that.")
                return 0
            agent.join(timeout=150)
            self.rec['agent_start_ms'] = self.agent_ready.get('ms')
            if not self.agent_ready.get('ok'):
                raise ConnectionError('OpenUnum not reachable')
            t = time.time()
            reply = self.ask(text)
            self.rec['agent_ms'] = int((time.time() - t) * 1000)
            self.rec['reply_chars'] = len(reply)
            spoken, cut = summarize(clean_for_speech(reply), int(self.cfg['max_spoken_chars']))
            if not spoken:
                spoken = 'The assistant returned an empty answer.'
            self.rec.update(spoken_chars=len(spoken), truncated=cut, reply=reply[:2000],
                            result='ok')
            self.rec['to_speech_ms'] = int((time.time() - t0) * 1000)
            t = time.time()
            self.say(spoken)
            self.rec['speak_ms'] = int((time.time() - t) * 1000)
            return 0
        except Exception as error:   # every failure is spoken, then logged
            self.rec.update(result='error', error=f'{type(error).__name__}: {error}'[:500])
            reach = isinstance(error, (ConnectionError, urllib.error.URLError, TimeoutError, OSError))
            self.say("Sorry, I couldn't reach the assistant." if reach
                     else 'Sorry, something went wrong with the assistant.')
            return 1
        finally:
            self.rec['total_ms'] = int((time.time() - t0) * 1000)
            self.log()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--wav', help='WAV path inside the chroot to transcribe instead of recording')
    ap.add_argument('--text', help='transcript to send instead of recording')
    ap.add_argument('--no-speak', action='store_true')
    args = ap.parse_args(argv)
    lock = open(LOCK, 'w')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        a = Assistant(load_config(), speak=False)
        a.rec['result'] = 'busy'
        a.log()
        return 0
    a = Assistant(load_config(), speak=not args.no_speak)
    rc = a.run(wav=args.wav, text=args.text)
    if args.no_speak:
        print(json.dumps(a.rec, indent=1, sort_keys=True))
    return rc


if __name__ == '__main__':
    sys.exit(main())
