#!/usr/bin/env python3
"""converse-e2e: ONE live s22-converse turn where the 'person' is a WAV played from the phone speaker.

Runs on the phone as root. s22-converse listens on the main mic; once its cue has played and the mic is
open, QUESTION_WAV (path inside the chroot) is played with s22-say at the default speech level (4/10), the
mic hears it, the VAD ends the utterance, ASR -> OpenUnum -> fillers -> answer run exactly as in use.
The mic hears its own speaker only faintly, so the VAD floor/margin are relaxed for this run only
(in memory; /srv/s22/buttons/assistant.json is not touched).

  converse-e2e.py QUESTION_WAV_IN_CHROOT [--abs-floor-db -66] [--margin-db 9]
Prints the converse summary JSON; the turn record (with t_vad_end, ask_t0, fillers[], reply_t,
answer_launch_t) is appended to /srv/s22/buttons/converse.jsonl.
"""
import argparse
import json
import subprocess
import sys
import threading
import time
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument('wav')
ap.add_argument('--abs-floor-db', type=float, default=-66.0)
ap.add_argument('--margin-db', type=float, default=9.0)
ap.add_argument('--delay-s', type=float, default=1.2, help='wait after the mic is ready + cue before the question')
args = ap.parse_args()

path = '/srv/s22/hardware/bin/s22-converse'
loader = SourceFileLoader('s22_converse', path)
C = module_from_spec(spec_from_loader('s22_converse', loader))
loader.exec_module(C)

cfg = C.load_config()
cfg['converse'].update(max_turns=1, idle_s=40.0, abs_floor_db=args.abs_floor_db, margin_db=args.margin_db)
marks = {}


def play_question():
    t0 = time.time()
    while not C.REC_READY.exists() and time.time() - t0 < 20:
        time.sleep(0.02)
    time.sleep(args.delay_s + 0.8)                     # route setup + the listen cue
    marks['question_start'] = round(time.time(), 3)
    subprocess.run(['/srv/s22/hardware/bin/s22-say', '--wav', args.wav], stdin=subprocess.DEVNULL,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
    marks['question_end'] = round(time.time(), 3)


lock, busy = C.acquire_lock()
if lock is None:
    print(json.dumps({'result': 'busy', **(busy or {})}))
    sys.exit(1)
threading.Thread(target=play_question, daemon=True).start()
summary = C.Converser(cfg).converse()
print(json.dumps({**summary, 'marks': marks}, indent=1, ensure_ascii=False))
