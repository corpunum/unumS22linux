#!/usr/bin/env python3
"""s22-converse: hands-free voice conversation with the phone's OpenUnum.

Installed as /srv/s22/hardware/bin/s22-converse next to s22-assistant, whose
ASR, OpenUnum and speech helpers it reuses (loaded from the sibling file).

Half-duplex turn taking, one OpenUnum session for the whole conversation (so
context carries; the model is OpenUnum's default, never overridden):

  1. listen: s22-rec captures the main mic; its raw 4-slot stream is read live
     (S22_REC_RAW) and an energy VAD on 20 ms frames finds the start and the
     end of an utterance (end = END_SILENCE_S of quiet after speech)
  2. the utterance (plus a short pre-roll) is written as a 16-bit WAV and
     transcribed on the phone (same sherpa-onnx command as s22-assistant)
  3. OpenUnum answers (POST /api/chat, poll /api/chat/pending)
  4. s22-say speaks the cleaned, shortened reply; the mic is closed meanwhile
  5. repeat until an exit phrase ("stop", "goodbye", "τέλος", ...), IDLE_S
     without speech, or MAX_TURNS

The phone stays muted: nothing here touches the volume or the amp. In
--silent-verify mode nothing is played at all: the "microphone" is a list of
WAV files and every s22-say call renders to a WAV file (S22_SAY_OUT) instead
of playing, so the whole loop can be checked on the device in silence.

Uses the assistant's lock (/run/s22-assistant.lock), so push-to-talk and a
conversation never overlap; a lock left by a dead process is broken.
Every turn is appended to /srv/s22/buttons/converse.jsonl.

  s22-converse                                live conversation (mic + speaker)
  s22-converse --silent-verify A.wav [B.wav]  WAVs in, rendered WAVs out
           [--out-dir DIR]                    (default /srv/s22/state/converse/verify-<t>)
  s22-converse --max-turns N --idle-s S       override the config for this run

Settings: the "converse" object in /srv/s22/buttons/assistant.json.
"""
from __future__ import annotations

import argparse
import array
import fcntl
import json
import os
import re
import subprocess
import sys
import threading
import time
import wave
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from math import log10, sqrt
from pathlib import Path


def _load_assistant():
    here = Path(__file__).resolve().parent
    for name in ('s22-assistant.py', 's22-assistant'):
        path = here / name
        if path.is_file():
            loader = SourceFileLoader('s22_assistant', str(path))
            mod = module_from_spec(spec_from_loader('s22_assistant', loader))
            loader.exec_module(mod)
            return mod
    raise ImportError(f's22-assistant not found next to {__file__}')


A = _load_assistant()

STATE = Path('/srv/s22/state/converse')
REC_STOP = Path('/run/s22-converse.rec-stop')
REC_READY = Path('/run/s22-converse.rec-ready')
RAW_CHROOT = '/tmp/s22-converse.raw'           # inside the chroot (S22_REC_RAW)
RATE = 48000
SLOTS = 4                                      # s22-rec DMA: 4 x S16_LE, main mic = slot 0

CONVERSE_DEFAULTS = {
    'session_prefix': 'voice-conv',
    'max_turns': 10,
    'idle_s': 20.0,             # no speech for this long while listening -> end
    'max_utterance_s': 25.0,
    'end_silence_s': 0.9,       # quiet after speech that ends an utterance
    'min_speech_s': 0.3,        # shorter bursts are clicks/bumps, ignored
    'pre_roll_s': 0.3,
    'frame_ms': 20,
    'margin_db': 12.0,          # speech = this far above the noise floor ...
    'abs_floor_db': -55.0,      # ... and above this absolute level (dBFS)
    'cue': True,                # earcon when the mic opens
}

EXIT_PHRASES = (
    'stop', 'stop listening', 'goodbye', 'good bye', 'bye', 'bye bye', "that's all",
    'that is all', 'end conversation', 'end the conversation', 'exit', 'quit',
    'thank you goodbye', 'thanks goodbye', 'no more questions',
    'τέλος', 'τελος', 'αντίο', 'αντιο', 'σταμάτα', 'σταματα', 'γεια', 'γεια σου',
)

CONVERSE_CONTEXT = (
    'This is a hands-free spoken conversation with the owner through the Galaxy S22 phone. '
    'Each message was transcribed by on-device speech recognition, so it may contain '
    'recognition errors. Your reply will be read aloud by text-to-speech: answer in one to '
    'three short, plain spoken sentences unless asked for more, and keep the conversation '
    'going naturally. No markdown, lists, tables, code or URLs in the spoken answer.')


# ---------------------------------------------------------------- pure helpers

def load_config(base: Path = A.BASE) -> dict:
    cfg = A.load_config(base)
    conv = dict(CONVERSE_DEFAULTS)
    if isinstance(cfg.get('converse'), dict):
        conv.update(cfg['converse'])
    cfg['converse'] = conv
    return cfg


def normalize_phrase(text: str) -> str:
    text = text.lower().replace('’', "'")
    text = re.sub(r"[^\w\s']", ' ', text)
    return re.sub(r'\s+', ' ', text).strip()


def is_exit_phrase(text: str) -> bool:
    """A short utterance that is (or ends/starts with) an exit phrase.

    "Stop." ends the conversation; "how do I stop the timer" does not.
    """
    t = normalize_phrase(text)
    if not t:
        return False
    if t in EXIT_PHRASES:
        return True
    words = t.split()
    if len(words) > 4:
        return False
    return any(t.startswith(p + ' ') or t.endswith(' ' + p) for p in EXIT_PHRASES if ' ' not in p) \
        or any(p in t for p in EXIT_PHRASES if ' ' in p)


def frame_db(samples) -> float:
    """RMS level of int16 samples in dBFS (-120 for silence)."""
    n = len(samples)
    if not n:
        return -120.0
    acc = 0
    for s in samples:
        acc += s * s
    rms = sqrt(acc / n)
    return 20 * log10(rms / 32768.0) if rms > 0 else -120.0


class EnergyVAD:
    """Streaming energy VAD over int16 mono samples.

    feed() returns the utterances completed by the new samples (each an
    array('h') including the pre-roll). The noise floor starts at the first
    frames and then tracks quiet frames (fast down, slow up), so a steady
    hum does not count as speech.
    """

    def __init__(self, cfg: dict, rate: int = RATE):
        self.rate = rate
        self.flen = max(1, int(rate * cfg['frame_ms'] / 1000))
        fs = cfg['frame_ms'] / 1000
        self.margin = float(cfg['margin_db'])
        self.abs_floor = float(cfg['abs_floor_db'])
        self.start_frames = max(1, round(0.1 / fs))            # 100 ms of speech starts
        self.end_frames = max(1, round(cfg['end_silence_s'] / fs))
        self.min_frames = max(1, round(cfg['min_speech_s'] / fs))
        self.max_frames = max(1, round(cfg['max_utterance_s'] / fs))
        self.pre_frames = max(0, round(cfg['pre_roll_s'] / fs))
        self.noise: float | None = None
        self.pending = array.array('h')
        self.history: list = []        # recent frames before a start (pre-roll)
        self.speech: list | None = None
        self.voiced_run = 0
        self.silent_run = 0
        self.voiced_total = 0
        self.frames_seen = 0
        self.last_db = -120.0

    @property
    def in_speech(self) -> bool:
        return self.speech is not None

    def threshold(self) -> float:
        floor = self.noise if self.noise is not None else -90.0
        return max(floor + self.margin, self.abs_floor)

    def _frame(self, frame) -> array.array | None:
        db = frame_db(frame)
        self.last_db = db
        self.frames_seen += 1
        voiced = db > self.threshold()
        if self.noise is None:
            self.noise = db
        elif not voiced and self.speech is None:
            # fast down, slow up
            self.noise = db if db < self.noise else self.noise + 0.05 * (db - self.noise)
        if self.speech is None:
            self.history.append(frame)
            if voiced:
                self.voiced_run += 1
                if self.voiced_run >= self.start_frames:
                    keep = self.pre_frames + self.voiced_run
                    self.speech = self.history[-keep:]
                    self.history = []
                    self.voiced_total = self.voiced_run
                    self.silent_run = 0
            else:
                self.voiced_run = 0
            del self.history[:-(self.pre_frames + self.start_frames + 1)]
            return None
        self.speech.append(frame)
        if voiced:
            self.voiced_total += 1
            self.silent_run = 0
        else:
            self.silent_run += 1
        if self.silent_run >= self.end_frames or len(self.speech) >= self.max_frames:
            frames, voiced_total = self.speech, self.voiced_total
            self.speech = None
            self.voiced_run = self.silent_run = self.voiced_total = 0
            if voiced_total < self.min_frames:
                return None                                   # a click, not speech
            out = array.array('h')
            for f in frames:
                out.extend(f)
            return out
        return None

    def feed(self, samples) -> list:
        self.pending.extend(samples)
        done = []
        n = len(self.pending) // self.flen * self.flen
        for i in range(0, n, self.flen):
            u = self._frame(self.pending[i:i + self.flen])
            if u is not None:
                done.append(u)
        del self.pending[:n]
        return done

    def flush(self) -> list:
        """End of input: close a running utterance (if it was long enough)."""
        if self.speech is None or self.voiced_total < self.min_frames:
            self.speech = None
            return []
        out = array.array('h')
        for f in self.speech:
            out.extend(f)
        self.speech = None
        return [out]


def read_wav(path) -> tuple[int, array.array]:
    """16-bit PCM WAV -> (rate, first-channel samples)."""
    with wave.open(str(path), 'rb') as w:
        if w.getsampwidth() != 2:
            raise ValueError(f'{path}: need 16-bit PCM, got {8 * w.getsampwidth()}-bit')
        ch, rate = w.getnchannels(), w.getframerate()
        data = array.array('h', w.readframes(w.getnframes()))
    if sys.byteorder == 'big':
        data.byteswap()
    return rate, (data[0::ch] if ch > 1 else data)


def write_wav(path, samples, rate: int = RATE) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = array.array('h', samples)
    if sys.byteorder == 'big':
        data.byteswap()
    tmp = path.with_name(path.name + '.tmp')
    with wave.open(str(tmp), 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(data.tobytes())
    os.replace(tmp, path)


def raw_to_mono(buf: bytes, slots: int = SLOTS) -> tuple[array.array, bytes]:
    """s22-rec raw stream (interleaved S16_LE slots) -> (slot 0, leftover bytes)."""
    step = 2 * slots
    n = len(buf) // step * step
    a = array.array('h', buf[:n])
    if sys.byteorder == 'big':
        a.byteswap()
    return a[0::slots], buf[n:]


# ---------------------------------------------------------------- the lock

def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:                                       # a zombie holds nothing
        stat = Path(f'/proc/{pid}/stat').read_text()
        return stat.rsplit(')', 1)[1].split()[0] != 'Z'
    except (OSError, IndexError):
        return True


def lock_holders(path: Path, locks_text: str | None = None) -> list[int]:
    """PIDs holding a flock on `path`'s inode, from /proc/locks."""
    try:
        ino = os.stat(path).st_ino
        text = Path('/proc/locks').read_text() if locks_text is None else locks_text
    except OSError:
        return []
    out = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 6 and parts[1] == 'FLOCK':
            try:
                if int(parts[5].rsplit(':', 1)[1]) == ino:
                    out.append(int(parts[4]))
            except (ValueError, IndexError):
                pass
    return out


def acquire_lock(path: Path = A.LOCK, holders=lock_holders):
    """(file, None) on success, (None, info) when a live process holds it.

    The pid of the owner is written into the file. A lock whose recorded pid
    and whose /proc/locks holders are all dead is stale: the file is
    replaced (new inode) and locked afresh.
    """
    for attempt in (1, 2):
        f = open(path, 'a+')
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            try:
                f.seek(0)
                recorded = int(f.read().strip() or 0)
            except ValueError:
                recorded = 0
            f.close()
            pids = [p for p in ([recorded] if recorded else []) + holders(path) if p != os.getpid()]
            live = [p for p in pids if pid_alive(p)]
            if live or not pids or attempt == 2:
                return None, {'holders': pids, 'live': live}
            try:
                path.unlink()                                 # stale: new inode
            except OSError:
                return None, {'holders': pids, 'live': live, 'unlink': 'failed'}
            continue
        f.seek(0)
        f.truncate()
        f.write(f'{os.getpid()}\n')
        f.flush()
        return f, None
    return None, {}


# ---------------------------------------------------------------- side effects

class Converser(A.Assistant):
    """One conversation; `inputs` = WAV files instead of the mic (silent)."""

    def __init__(self, cfg: dict, inputs: list[str] | None = None, out_dir: Path | None = None,
                 base: Path = A.BASE):
        super().__init__(cfg, speak=True, base=base)
        self.conv = cfg['converse']
        self.silent = inputs is not None
        self.inputs = list(inputs or [])
        self.out_dir = out_dir
        self.session = f"{self.conv['session_prefix']}-{time.strftime('%Y%m%d-%H%M%S')}"
        self.cfg['session'] = self.session
        self.turn = 0
        self.say_n = 0
        self.rendered: list[str] = []
        self.turns: list[dict] = []

    # -- audio out
    def say(self, text: str = '', wav: str | None = None, wait: bool = True):
        argv = [str(A.BIN / 's22-say')] + (['--wav', wav] if wav else [text])
        env = dict(os.environ)
        if self.silent:
            if wav:                     # earcons: nothing to verify, skip them
                return None
            self.say_n += 1
            out = self.out_dir / f'turn{self.turn:02d}-say{self.say_n:02d}.wav'
            env['S22_SAY_OUT'] = str(out)
            self.rendered.append(str(out))
            wait = True                 # renders are cheap to wait for; keeps order
        else:
            env.pop('S22_SAY_OUT', None)
        p = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, env=env)
        if wait:
            p.wait(timeout=180)
        return p

    # -- audio in
    def listen_wav(self, path: str) -> tuple[array.array | None, dict]:
        """Silent mode: one input WAV is one listening window."""
        rate, samples = read_wav(path)
        vad = EnergyVAD(self.conv, rate)
        step = rate // 2
        for i in range(0, len(samples), step):        # stream it like the mic
            done = vad.feed(samples[i:i + step])
            if done:
                return done[0], {'rate': rate, 'source': path, 'noise_db': round(vad.noise or 0, 1)}
        done = vad.flush()
        info = {'rate': rate, 'source': path, 'noise_db': round(vad.noise or 0, 1)}
        return (done[0] if done else None), info

    def listen_mic(self) -> tuple[array.array | None, dict]:
        """Live: run s22-rec, VAD its raw stream, stop it when the utterance ends."""
        conv = self.conv
        raw_host = A.CHROOT / RAW_CHROOT.lstrip('/')
        for f in (REC_STOP, REC_READY, raw_host):
            f.unlink(missing_ok=True)
        max_s = int(conv['idle_s'] + conv['max_utterance_s'] + 5)
        argv = [str(A.BIN / 's22-rec'), '--stop-file', str(REC_STOP), str(max_s),
                str(A.CHROOT) + f'{A.WORK}/conv-rec.wav']
        proc = subprocess.Popen(argv, env={**os.environ, 'S22_REC_READY': str(REC_READY),
                                           'S22_REC_RAW': RAW_CHROOT},
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL)
        info: dict = {'source': 'mic', 'rate': RATE}
        try:
            t0 = time.time()
            while not REC_READY.exists() and proc.poll() is None and time.time() - t0 < 5:
                time.sleep(0.02)
            if conv['cue']:
                self.say(wav=f'{A.ASR_DIR}/listen.wav')      # half-duplex: waits for the cue
            skip = 0
            try:                              # whatever the mic caught during the cue
                skip = raw_host.stat().st_size // (2 * SLOTS) * (2 * SLOTS)
            except OSError:
                pass
            vad = EnergyVAD(conv, RATE)
            start = time.time()
            leftover = b''
            fh = None
            while proc.poll() is None:
                if fh is None:
                    try:
                        fh = open(raw_host, 'rb')
                        fh.seek(skip)
                    except OSError:
                        fh = None
                chunk = fh.read() if fh else b''
                if chunk:
                    mono, leftover = raw_to_mono(leftover + chunk)
                    done = vad.feed(mono)
                    if done:
                        info['noise_db'] = round(vad.noise or 0, 1)
                        return done[0], info
                if not vad.in_speech and time.time() - start > conv['idle_s']:
                    info['idle'] = True
                    return None, info
                time.sleep(0.05)
            info['rec_rc'] = proc.returncode
            done = vad.flush()
            return (done[0] if done else None), info
        finally:
            REC_STOP.touch()
            try:
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                proc.kill()
            REC_STOP.unlink(missing_ok=True)

    # -- one turn
    def take_turn(self, utterance: array.array, rate: int) -> dict:
        rec: dict = {'turn': self.turn, 'session': self.session,
                     'utterance_s': round(len(utterance) / rate, 2)}
        wav_chroot = f'{A.WORK}/conv-utt{self.turn:02d}.wav'
        write_wav(A.CHROOT / wav_chroot.lstrip('/'), utterance, rate)
        if self.silent and self.out_dir:
            write_wav(self.out_dir / f'turn{self.turn:02d}-heard.wav', utterance, rate)
        text = self.transcribe(wav_chroot)
        rec['asr_ms'] = self.rec.get('asr_ms')
        rec['transcript'] = text
        if not re.search(r'\w', text):
            rec['result'] = 'empty'
            self.say("Sorry, I didn't catch that.")
            return rec
        if is_exit_phrase(text):
            rec['result'] = 'exit'
            self.say('Goodbye.')
            return rec
        t = time.time()
        reply = self.ask(text)
        rec['agent_ms'] = int((time.time() - t) * 1000)
        rec['model'] = self.rec.get('model')
        spoken, cut = A.summarize(A.clean_for_speech(reply), int(self.cfg['max_spoken_chars']))
        if not spoken:
            spoken = 'The assistant returned an empty answer.'
        rec.update(reply=reply[:2000], spoken=spoken, truncated=cut, result='ok')
        t = time.time()
        self.say(spoken)
        rec['speak_ms'] = int((time.time() - t) * 1000)
        return rec

    def log_turn(self, rec: dict) -> None:
        rec = dict(rec, t=round(time.time(), 3), mode='silent' if self.silent else 'live')
        self.turns.append(rec)
        try:
            with open(self.base / 'converse.jsonl', 'a') as f:
                f.write(json.dumps(rec, sort_keys=True, ensure_ascii=False) + '\n')
        except OSError:
            pass

    def converse(self) -> dict:
        t0 = time.time()
        (A.CHROOT / A.WORK.lstrip('/')).mkdir(parents=True, exist_ok=True)
        if self.out_dir:
            self.out_dir.mkdir(parents=True, exist_ok=True)
        agent = threading.Thread(target=self.ensure_agent, daemon=True)
        agent.start()
        old_context = A.VOICE_CONTEXT
        A.VOICE_CONTEXT = CONVERSE_CONTEXT           # ask() reads the module global
        ended = 'max_turns'
        try:
            agent.join(timeout=150)
            if not self.agent_ready.get('ok'):
                self.say("Sorry, I couldn't reach the assistant.")
                ended = 'agent_unreachable'
                return self.summary(ended, t0)
            while self.turn < int(self.conv['max_turns']):
                self.turn += 1
                self.say_n = 0
                if self.silent:
                    if not self.inputs:
                        ended = 'inputs_exhausted'
                        break
                    utt, info = self.listen_wav(self.inputs.pop(0))
                else:
                    utt, info = self.listen_mic()
                if utt is None:
                    self.log_turn({'turn': self.turn, 'session': self.session, 'result': 'idle', **info})
                    if not self.silent or not self.inputs:
                        ended = 'idle'
                        self.say('Ending the conversation.')
                        break
                    continue
                try:
                    rec = self.take_turn(utt, info.get('rate', RATE))
                except Exception as error:   # spoken, logged, conversation ends
                    rec = {'turn': self.turn, 'result': 'error',
                           'error': f'{type(error).__name__}: {error}'[:500]}
                    self.say('Sorry, something went wrong. Ending the conversation.')
                    self.log_turn({**info, **rec})
                    ended = 'error'
                    break
                self.log_turn({**info, **rec})
                if rec.get('result') == 'exit':
                    ended = 'exit_phrase'
                    break
            return self.summary(ended, t0)
        finally:
            A.VOICE_CONTEXT = old_context

    def summary(self, ended: str, t0: float) -> dict:
        return {'session': self.session, 'ended': ended, 'turns': len(self.turns),
                'mode': 'silent' if self.silent else 'live', 'rendered': self.rendered,
                'total_s': round(time.time() - t0, 1),
                'log': [{k: r.get(k) for k in ('turn', 'result', 'transcript', 'spoken', 'error')}
                        for r in self.turns]}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--silent-verify', nargs='+', metavar='WAV',
                    help='16-bit WAV files used as the microphone; speech is rendered, not played')
    ap.add_argument('--out-dir', help='where --silent-verify writes rendered speech')
    ap.add_argument('--max-turns', type=int)
    ap.add_argument('--idle-s', type=float)
    args = ap.parse_args(argv)
    cfg = load_config()
    if args.max_turns:
        cfg['converse']['max_turns'] = args.max_turns
    if args.idle_s:
        cfg['converse']['idle_s'] = args.idle_s
    out_dir = None
    if args.silent_verify:
        out_dir = Path(args.out_dir or STATE / f"verify-{time.strftime('%Y%m%d-%H%M%S')}")
    lock, busy = acquire_lock()
    if lock is None:
        print(json.dumps({'result': 'busy', **(busy or {})}))
        return 0
    c = Converser(cfg, inputs=args.silent_verify, out_dir=out_dir)
    summary = c.converse()
    print(json.dumps(summary, indent=1, ensure_ascii=False))
    return 0 if summary['ended'] not in ('error', 'agent_unreachable') else 1


if __name__ == '__main__':
    sys.exit(main())
