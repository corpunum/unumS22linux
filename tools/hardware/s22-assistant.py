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

Agent card modes (unum-fastshell's Home card; JSON event lines on stdout):
  s22-assistant --card listen --stop-file F   record until F appears (hold) -> ASR ->
                                              {"transcript": ...}; never waits for a running answer
                                              (the cue is a haptic buzz plus the earcon only if nothing plays)
  s22-assistant --card turn --text T          one agent turn with fillers, spoken thinking and the
                                              -16 LUFS voice; {"state": "thinking" | "tool" (name) |
                                              "reply" (text) | "speaking" | "done" | "error"}.
                                              Turns queue on the same lock as the button hook.
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

try:                                   # natural-voice helpers installed next to this script
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import s22_voice as V
except ImportError:                    # old install: plain markdown stripping, no fillers
    V = None

CHROOT = Path('/mnt/omarchy-trial')
ASR_DIR = '/opt/s22-asr'                       # inside the chroot
WORK = '/tmp/s22-assistant'                    # inside the chroot
BIN = Path('/srv/s22/hardware/bin')
BASE = Path('/srv/s22/buttons')
LOCK = Path('/run/s22-assistant.lock')
REC_STOP = Path('/run/s22-assistant.rec-stop')
REC_READY = Path('/run/s22-assistant.rec-ready')
CARD_REC_STOP = Path('/run/s22-assistant.card-rec-stop')
CARD_REC_READY = Path('/run/s22-assistant.card-rec-ready')
CARD_LISTEN_LOCK = Path('/run/s22-assistant.card-listen.lock')
SAY_LOCK = Path('/run/s22-say.lock')
HAPTIC_SOCK = '/run/s22-haptic.sock'
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
    'still_working_s': 15.0,     # spoken "One moment." when the filler clips are unavailable
    'poll_s': 0.4,               # reply polling interval
    # natural voice (s22_voice.py, from OpenUnum 89ac48de)
    'enhance': 'chatty',         # chatty | light | off: lists -> "First... Then...", pauses, fillers
    'fillers': True,             # pre-rendered "hmm, let me think" clip while the agent works
    'filler_delay_s': 1.2,       # no answer audio by now -> first filler clip
    'filler_second_s': 6.0,      # still no answer -> a second "still thinking" clip
    'filler_repeat_s': 25.0,     # ... and another this often (max 3 more)
    'filler_dir': '/root/.openunum/assets/voice-fillers',   # inside the chroot
    'split_chars': 120,          # answers longer than this: first sentence is spoken while the rest renders
    'speak_thinking': True,      # speak short reasoning chunks if the agent streams any
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


_FRAMEWORK_NOTE = re.compile(r'^(verification (warning|note)|evidence (warning|note))\s*:', re.I)


def clean_for_speech(text: str) -> str:
    for rx, sub in _MD:
        text = rx.sub(sub, text)
    # OpenUnum appends framework notes (e.g. "Verification warning: ...") for
    # the chat UI; they are not part of the answer and must not be spoken.
    lines = [l.strip() for l in text.splitlines()
             if l.strip() and not _FRAMEWORK_NOTE.match(l.strip())]
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


def split_first_chunk(text: str, min_total: int = 120, first_max: int = 110, first_min: int = 40) -> tuple[str, str]:
    """(first, rest): a short opening for a long answer, so playback can start while the rest renders.

    The first sentence if it is at most `first_max` characters, else the first clause (comma, semicolon
    or colon) that ends between `first_min` and `first_max`. Short answers are returned whole.
    """
    if len(text) <= min_total:
        return text, ''
    m = re.search(r'[.!?…](?=\s)', text)
    if m and first_min <= m.end() <= first_max:
        return text[:m.end()], text[m.end():].strip()
    for m in re.finditer(r'[,;:](?=\s)', text):
        if first_min <= m.end() <= first_max:
            return text[:m.end()], text[m.end():].strip()
        if m.end() > first_max:
            break
    if m := re.search(r'[.!?…](?=\s)', text):
        if m.end() >= 25 and m.end() < len(text) - 10:
            return text[:m.end()], text[m.end():].strip()
    return text, ''


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
    def say(self, text: str = '', wav: str | None = None, wait: bool = True, filler: bool = False):
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

    card = False          # Agent card mode: own stop/ready files, cue never waits for playback

    def cue(self) -> None:
        """The mic is open. Card mode: a haptic buzz, and the earcon only if nothing is playing
        (a running answer must not delay the next recording)."""
        if not self.card:
            self.say(wav=f'{ASR_DIR}/listen.wav')
            return
        haptic('buzz')
        if not say_busy():
            self.say(wav=f'{ASR_DIR}/listen.wav', wait=False)

    def record(self, out_chroot: str) -> float:
        """Record into the chroot path; returns the recorded seconds."""
        rec_stop, rec_ready = (CARD_REC_STOP, CARD_REC_READY) if self.card else (REC_STOP, REC_READY)
        for f in (rec_stop, rec_ready):
            f.unlink(missing_ok=True)
        ptt_stop = os.environ.get('S22_PTT_STOP')
        hold = os.environ.get('S22_PTT') == 'hold' and ptt_stop
        max_s = self.cfg['max_s'] if hold else self.cfg['fixed_s']
        argv = [str(BIN / 's22-rec'), '--stop-file', str(rec_stop),
                str(int(max_s + 0.999)), str(CHROOT) + out_chroot]
        proc = subprocess.Popen(argv, env={**os.environ, 'S22_REC_READY': str(rec_ready)},
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL)
        t0 = time.time()
        while not rec_ready.exists() and proc.poll() is None and time.time() - t0 < 5:
            time.sleep(0.02)
        self.cue()                                       # mic is open: cue
        if self.card:
            emit(state='listening')
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
                rec_stop.touch()
                break
            time.sleep(0.03)
        proc.wait(timeout=30)
        rec_stop.unlink(missing_ok=True)
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
            # liveness, not the deep /api/health: deep checks run every diagnostic
            # and exceed a few seconds right after boot or under load, which made
            # the assistant think OpenUnum was down (2026-10-08).
            self._http('GET', '/health', timeout=10)
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

    def ask(self, text: str, still_working: bool = True) -> str:
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
            if still_working and not warned and time.time() - start > self.cfg['still_working_s']:
                warned = True
                self.say('One moment.', wait=False)
            time.sleep(float(self.cfg.get('poll_s', 1.0)))
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

    # -- natural voice: speakable text, filler clips, spoken thinking
    prerender = True      # synthesize the answer while a filler plays (off for --silent-verify)
    filler_proc = None    # a filler/thinking clip still playing when the reply arrived

    def finish_filler(self) -> None:
        """Let the clip that is playing finish (never cut off) before the real answer starts."""
        proc, self.filler_proc = self.filler_proc, None
        if proc is not None:
            try:
                proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                pass

    def speakable(self, reply: str) -> tuple[str, bool]:
        """Reply -> (text to speak, truncated). Bullets and markdown are never spoken."""
        style = self.cfg.get('enhance', 'chatty')
        if V is not None and style != 'off':
            text = V.reply_to_speech(reply, style=style)
        else:
            text = clean_for_speech(reply)
        return summarize(text, int(self.cfg['max_spoken_chars']))

    def filler_bank(self):
        if V is None or not self.cfg.get('fillers', True):
            return None
        root = CHROOT / str(self.cfg['filler_dir']).lstrip('/')
        if not (root / 'manifest.json').is_file():
            return None
        return V.FillerBank(root, chroot_prefix=str(CHROOT), state_file=Path('/srv/s22/state/voice-filler-last.json'))

    def _thinking_listener(self, stop: threading.Event, out: list, speaker) -> None:
        """Follow /api/chat/stream; reasoning_delta tokens become short spoken chunks (out)."""
        try:
            req = urllib.request.Request(f"{API}/api/chat/stream?sessionId={self.cfg['session']}")
            with urllib.request.urlopen(req, timeout=15) as r:
                event = ''
                while not stop.is_set():
                    line = r.readline()
                    if not line:
                        return
                    line = line.decode('utf-8', 'replace').strip()
                    if line.startswith('event:'):
                        event = line[6:].strip()
                    elif line.startswith('data:') and event in ('reasoning_delta', 'content_delta'):
                        try:
                            token = json.loads(line[5:]).get('token', '')
                        except ValueError:
                            continue
                        counter = 'reasoning_events' if event == 'reasoning_delta' else 'content_events'
                        self.rec[counter] = self.rec.get(counter, 0) + 1
                        if event == 'content_delta':
                            speaker.answer_started()
                            continue
                        chunk = speaker.push(token)
                        if chunk:
                            out.append(chunk)
        except (OSError, ValueError, urllib.error.URLError):
            return

    def ask_with_fillers(self, text: str) -> str:
        """self.ask() with filler clips while it runs.

        No answer 1.2 s after the question: a category-matched clip (never the
        same one twice in a row); 6 s: a second "still thinking" clip, then one
        every 25 s; spoken reasoning chunks when the agent streams any. Clips are
        finished (not cut) before this returns, so the real answer never overlaps.
        """
        cfg = self.cfg
        bank = self.filler_bank() if self.speak_enabled else None
        result: dict = {}

        def work():
            try:
                result['reply'] = self.ask(text, still_working=bank is None)
            except BaseException as error:        # re-raised in the caller
                result['error'] = error

        t0 = time.time()
        self.rec['ask_t0'] = round(t0, 3)
        worker = threading.Thread(target=work, daemon=True)
        worker.start()
        events: list = []
        stop = threading.Event()
        chunks: list = []
        if V is not None and cfg.get('speak_thinking', True) and self.speak_enabled:
            threading.Thread(target=self._thinking_listener, args=(stop, chunks, V.ThinkingSpeaker()),
                             daemon=True).start()
        playing = None
        next_filler = float(cfg['filler_delay_s'])
        played = 0
        try:
            while worker.is_alive():
                worker.join(0.05)
                now = time.time() - t0
                if playing is not None and playing.poll() is not None:
                    playing = None
                if not worker.is_alive():
                    break
                if playing is not None:
                    continue
                if chunks:
                    chunk = chunks.pop(0)
                    playing = self.say(chunk, wait=False, filler=True)
                    events.append({'kind': 'thinking', 'at_s': round(now, 2), 'text': chunk})
                elif bank is not None and now >= next_filler and played <= 4:
                    wav, info = bank.pick(text, second=played > 0)
                    if wav:
                        playing = self.say(wav=wav, wait=False, filler=True)
                        events.append({'kind': 'filler', 'at_s': round(now, 2), **info})
                    played += 1
                    next_filler = now + (float(cfg['filler_second_s']) - float(cfg['filler_delay_s'])
                                         if played == 1 else float(cfg['filler_repeat_s']))
            self.rec['reply_wait_ms'] = int((time.time() - t0) * 1000)
            self.rec['reply_t'] = round(time.time(), 3)
            self.filler_proc = playing                 # speak_answer() lets it finish before the answer
        finally:
            stop.set()
            self.rec['fillers'] = events
        if 'error' in result:
            raise result['error']
        return result.get('reply', '')

    def _render(self, text: str, name: str) -> str | None:
        """Synthesize `text` with s22-say in render-only mode; the chroot path of the WAV, or None."""
        out = CHROOT / WORK.lstrip('/') / name
        out.parent.mkdir(parents=True, exist_ok=True)
        try:
            out.unlink(missing_ok=True)
            subprocess.run([str(BIN / 's22-say'), text], env={**os.environ, 'S22_SAY_OUT': str(out)},
                           stdin=subprocess.DEVNULL, capture_output=True, timeout=180)
            if out.is_file() and out.stat().st_size > 1000:
                return f'{WORK}/{name}'
        except (OSError, subprocess.SubprocessError):
            pass
        return None

    def speak_answer(self, spoken: str) -> None:
        """Speak the answer, synthesizing before playing so a long answer starts sooner.

        A longer answer is split after its first sentence: sentence one is rendered and starts
        playing while the rest renders (each s22-say call loads the TTS model, ~2 s, so rendering
        the whole answer first left several silent seconds after the filler). Playback calls
        queue on s22-say's own lock, so the order is kept.
        """
        if not (self.speak_enabled and self.prerender):
            self.finish_filler()
            self.say(spoken)
            return
        t = time.time()
        first, rest = split_first_chunk(spoken, int(self.cfg.get('split_chars', 120)))
        w1 = self._render(first, 'answer1.wav')   # synthesized while any filler is still playing
        self.finish_filler()
        if not w1:
            self.say(spoken)
            return
        self.rec['render_ms'] = int((time.time() - t) * 1000)
        self.rec['answer_launch_t'] = round(time.time(), 3)
        p1 = self.say(wav=w1, wait=False)
        w2 = self._render(rest, 'answer2.wav') if rest else None
        if p1 is not None:
            p1.wait(timeout=180)
        if rest:
            if w2:
                self.say(wav=w2)
            else:
                self.say(rest)
        self.rec['render_total_ms'] = int((time.time() - t) * 1000)

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
            reply = self.ask_with_fillers(text)
            self.rec['agent_ms'] = int((time.time() - t) * 1000)
            self.rec['reply_chars'] = len(reply)
            spoken, cut = self.speakable(reply)
            if not spoken:
                spoken = 'The assistant returned an empty answer.'
            self.rec.update(spoken_chars=len(spoken), truncated=cut, reply=reply[:2000],
                            result='ok')
            self.rec['to_speech_ms'] = int((time.time() - t0) * 1000)
            self.rec['spoken'] = spoken[:2000]
            t = time.time()
            self.speak_answer(spoken)
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


def emit(**kw) -> None:
    """One JSON event line for the shell's Agent card."""
    print(json.dumps(kw, ensure_ascii=False), flush=True)


def haptic(what: str) -> None:
    try:
        import socket
        s = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        s.setblocking(False)
        s.sendto(what.encode(), HAPTIC_SOCK)
        s.close()
    except OSError:
        pass


def say_busy() -> bool:
    """True while s22-say holds its lock (something is speaking)."""
    try:
        with open(SAY_LOCK, 'a') as f:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(f, fcntl.LOCK_UN)
        return False
    except BlockingIOError:
        return True
    except OSError:
        return False


def tool_events(session: str, stop: threading.Event, out=None, api: str = API) -> None:
    """Follow the session's /api/chat/stream and emit tool start/finish as card states."""
    out = out or emit
    try:
        with urllib.request.urlopen(f'{api}/api/chat/stream?sessionId={session}', timeout=30) as r:
            event = ''
            while not stop.is_set():
                line = r.readline()
                if not line:
                    return
                line = line.decode('utf-8', 'replace').strip()
                if line.startswith('event:'):
                    event = line[6:].strip()
                elif line.startswith('data:') and event.startswith('tool_call_'):
                    try:
                        d = json.loads(line[5:])
                    except ValueError:
                        continue
                    if event == 'tool_call_started':
                        out(state='tool', name=str(d.get('tool') or '?'))
                    else:
                        out(state='thinking')
    except (OSError, ValueError, urllib.error.URLError):
        return


def card_listen(cfg: dict, stop_file: str) -> int:
    """Record while the card is held (stop file = release), transcribe, print the transcript."""
    lock = open(CARD_LISTEN_LOCK, 'w')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        emit(state='error', error='already listening')
        return 1
    os.environ['S22_PTT'] = 'hold'
    os.environ['S22_PTT_STOP'] = stop_file
    a = Assistant(cfg, speak=True, base=BASE)
    a.card = True
    a.rec['mode'] = 'card-listen'
    try:
        work = CHROOT / WORK.lstrip('/')
        work.mkdir(parents=True, exist_ok=True)
        wav = f'{WORK}/card-{os.getpid()}.wav'
        a.rec['record_s'] = a.record(wav)
        haptic('tick')
        emit(state='transcribing', record_s=a.rec['record_s'])
        text = a.transcribe(wav)
        try:
            (CHROOT / wav.lstrip('/')).unlink()
        except OSError:
            pass
        a.rec['transcript'] = text
        a.rec['result'] = 'ok' if re.search(r'\w', text) else 'empty'
        emit(transcript=text, record_s=a.rec['record_s'], asr_ms=a.rec.get('asr_ms'))
        return 0
    except Exception as error:
        a.rec.update(result='error', error=f'{type(error).__name__}: {error}'[:500])
        emit(state='error', error=a.rec['error'])
        return 1
    finally:
        a.log()


def card_turn(cfg: dict, text: str) -> int:
    """One agent turn for the card: thinking / tool / reply / speaking / done on stdout."""
    lock = open(LOCK, 'w')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        emit(state='waiting')                 # a button push-to-talk turn is running: queue behind it
        fcntl.flock(lock, fcntl.LOCK_EX)
    a = Assistant(cfg, speak=True, base=BASE)
    a.card = True
    a.rec['mode'] = 'card-turn'
    a.rec['transcript'] = text
    t0 = time.time()
    stop = threading.Event()
    emit(state='thinking')
    threading.Thread(target=tool_events, args=(cfg['session'], stop), daemon=True).start()
    try:
        a.ensure_agent()
        if not a.agent_ready.get('ok'):
            raise ConnectionError('OpenUnum not reachable')
        reply = a.ask_with_fillers(text)
        a.rec['agent_ms'] = int((time.time() - t0) * 1000)
        spoken, cut = a.speakable(reply)
        if not spoken:
            spoken = 'The assistant returned an empty answer.'
        a.rec.update(reply=reply[:2000], spoken=spoken[:2000], truncated=cut, result='ok')
        stop.set()
        emit(state='reply', text=reply[:4000])
        emit(state='speaking')
        a.speak_answer(spoken)
        emit(state='done', ms=int((time.time() - t0) * 1000))
        return 0
    except Exception as error:
        a.rec.update(result='error', error=f'{type(error).__name__}: {error}'[:500])
        emit(state='error', error=a.rec['error'])
        reach = isinstance(error, (ConnectionError, urllib.error.URLError, TimeoutError, OSError))
        a.say("Sorry, I couldn't reach the assistant." if reach else 'Sorry, something went wrong with the assistant.')
        return 1
    finally:
        stop.set()
        a.rec['total_ms'] = int((time.time() - t0) * 1000)
        a.log()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--wav', help='WAV path inside the chroot to transcribe instead of recording')
    ap.add_argument('--text', help='transcript to send instead of recording')
    ap.add_argument('--no-speak', action='store_true')
    ap.add_argument('--card', choices=['listen', 'turn'], help="unum-fastshell's Agent card (JSON lines on stdout)")
    ap.add_argument('--stop-file', help='card listen: the release note (JSON {"t": unix seconds})')
    args = ap.parse_args(argv)
    if args.card == 'listen':
        return card_listen(load_config(), args.stop_file or str(CARD_REC_STOP) + '.release')
    if args.card == 'turn':
        if not args.text:
            ap.error('--card turn needs --text')
        return card_turn(load_config(), args.text)
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
