#!/usr/bin/env python3
"""s22_filler: the one shared way to play a pre-rendered "hmm, let me think" clip on the phone speaker.

The clips (voice-fillers/<lang>/<category>/<n>.wav + manifest.json) are rendered once with the phone's own
voices (Supertonic F1 for English, the el-antonis Piper voice for Greek) and loudness-normalised to the same
-16 LUFS as every synthesized answer (s22_loudnorm.py), so a filler and the answer after it are one voice at
one volume.  Picking is s22_voice.FillerBank (question -> category, never the same clip twice).

Used by: s22-touchd (`filler` command and `tts` with "filler": true, i.e. the agent's text_to_speech tool and
the shell/agent chat), and the s22-filler CLI below.  s22-assistant, s22-converse and s22-sip use
s22_voice.FillerBank directly with their own timing (they know when the agent starts and stops thinking).

  s22-filler [--second] [--out speaker|earpiece|bottom|top] [--wait] [--dry-run] QUESTION...
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import s22_voice as V  # noqa: E402  (installed next to this script)

CHROOT = Path(os.environ.get('S22_CHROOT', '/mnt/omarchy-trial'))
FILLER_DIR = Path(os.environ.get('S22_FILLER_DIR', str(CHROOT / 'root/.openunum/assets/voice-fillers')))
STATE_FILE = Path(os.environ.get('S22_FILLER_STATE', '/srv/s22/state/voice-filler-last.json'))
SAY = os.environ.get('S22_SAY', '/srv/s22/hardware/bin/s22-say')


def pick(question: str, second: bool = False, root=None, state_file=None):
    """-> (wav path INSIDE the chroot or None, info)."""
    bank = V.FillerBank(root or FILLER_DIR, chroot_prefix=str(CHROOT), state_file=state_file or STATE_FILE)
    return bank.pick(question, second=second)


def play(question: str, second: bool = False, out: str = 'speaker', wait: bool = False, say: str | None = None,
         root=None, state_file=None, env=None):
    """Start s22-say --wav <clip>. -> (Popen or None, info). Playback calls queue on s22-say's own lock."""
    wav, info = pick(question, second, root, state_file)
    if not wav:
        return None, info
    e = {k: v for k, v in (env or os.environ).items() if k != 'S22_SAY_OUT'}
    proc = subprocess.Popen([say or SAY, '--out', out, '--wav', wav], env=e, stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    if wait:
        proc.wait()
    return proc, info


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('question', nargs='*')
    ap.add_argument('--second', action='store_true', help='a "still thinking" clip (general category)')
    ap.add_argument('--out', default='speaker', choices=['speaker', 'earpiece', 'bottom', 'top'])
    ap.add_argument('--wait', action='store_true')
    ap.add_argument('--dry-run', action='store_true', help='only print the clip that would play')
    a = ap.parse_args(argv)
    q = ' '.join(a.question)
    if a.dry_run:
        wav, info = pick(q, a.second)
    else:
        proc, info = play(q, a.second, a.out, a.wait)
        wav = None if proc is None else 'playing'
    print(json.dumps({'ok': bool(wav), 'clip': wav, **info}, ensure_ascii=False))
    return 0 if wav else 1


if __name__ == '__main__':
    sys.exit(main())
