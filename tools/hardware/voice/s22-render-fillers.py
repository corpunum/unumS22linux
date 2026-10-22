#!/usr/bin/env python3
"""s22-render-fillers: render OpenUnum's filler phrase bank with the PHONE's own TTS.

Runs on the phone (root). Each phrase of filler-phrases.json (the rig's
FILLER_PHRASES from src/ui/modules/voice-fillers.js) is synthesized by s22-say
in render-only mode (S22_SAY_OUT: nothing is played, the sound card is not
touched) with exactly the engine that speaks the answers, so a filler and the
answer that follows it share one voice: the owner's own voice (s22-say engine "antonis", the rig's VoxCPM
voice) for Greek and English. Rendering is strict (S22_SAY_STRICT=1): a clip the rig cannot render fails the
run instead of falling back to another voice, so a bank never mixes voices (rerun when the rig is back;
existing clips are kept unless --force). Output layout and manifest.json match
the rig's ~/.openunum/assets/voice-fillers:

  <out>/<lang>/<category>/<n>.wav      (n starts at 1)
  <out>/manifest.json                  {"version":1,"generatedAt":..,"clips":{lang:{cat:[{n,phrase,durationSec}]}}}

  s22-render-fillers [--phrases FILE] [--out DIR] [--only en|el] [--force] [--renorm]

Every clip is loudness-normalised (s22_loudnorm: -16 LUFS, peak <= -2 dBFS), the same target
s22-say applies to answers, so a filler and the answer after it play at one volume.
--renorm only re-normalises the clips that exist (no synthesis; the voice is unchanged).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import wave
from pathlib import Path

CHROOT = Path('/mnt/omarchy-trial')
DEFAULT_OUT = CHROOT / 'root/.openunum/assets/voice-fillers'
DEFAULT_PHRASES = Path(__file__).resolve().parent / 'filler-phrases.json'
SAY = '/srv/s22/hardware/bin/s22-say'
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'bin'))
sys.path.insert(0, '/srv/s22/hardware/bin')
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    import s22_loudnorm as LN
except ImportError:          # old install: s22-say normalises renders itself
    LN = None


def wav_seconds(path: Path) -> float:
    with wave.open(str(path), 'rb') as w:
        return round(w.getnframes() / float(w.getframerate()), 2)


def render(text: str, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, 'S22_SAY_OUT': str(out), 'S22_SAY_STRICT': '1', 'S22_RIGVOICE_LENIENT': '1'}
    r = subprocess.run([SAY, text], env=env, capture_output=True, text=True, timeout=180)
    if r.returncode != 0 or not out.is_file() or out.stat().st_size < 1000:
        raise RuntimeError(f'render failed rc={r.returncode} {r.stderr[-200:]!r} for {text!r}')


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--phrases', default=str(DEFAULT_PHRASES))
    ap.add_argument('--out', default=str(DEFAULT_OUT))
    ap.add_argument('--only', choices=['en', 'el'])
    ap.add_argument('--force', action='store_true', help='re-render clips that already exist')
    ap.add_argument('--renorm', action='store_true', help='only loudness-normalise existing clips')
    args = ap.parse_args(argv)
    bank = json.loads(Path(args.phrases).read_text())['phrases']
    out = Path(args.out)
    manifest = {'version': 1, 'generatedAt': time.strftime('%Y-%m-%dT%H:%M:%S.000Z', time.gmtime()),
                'voice': 'owner voice: rig VoxCPM2 antonis (en + el) via s22-say engine antonis, -16 LUFS', 'clips': {}}
    done = skipped = 0
    for lang, cats in bank.items():
        if args.only and lang != args.only:
            continue
        for cat, phrases in cats.items():
            entries = []
            for i, phrase in enumerate(phrases, 1):
                wav = out / lang / cat / f'{i}.wav'
                if args.renorm:
                    if not wav.is_file():
                        raise SystemExit(f'--renorm: missing clip {wav}')
                    if LN is None:
                        raise SystemExit('--renorm needs s22_loudnorm.py')
                    LN.normalise_file(wav)
                    skipped += 1
                elif args.force or not wav.is_file():
                    render(phrase, wav)
                    done += 1
                else:
                    skipped += 1
                entries.append({'n': i, 'phrase': phrase, 'durationSec': wav_seconds(wav)})
                print(f'{lang}/{cat}/{i} {entries[-1]["durationSec"]}s', flush=True)
            manifest['clips'].setdefault(lang, {})[cat] = entries
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + '\n')
    print(json.dumps({'rendered': done, 'kept': skipped, 'out': str(out)}))
    return 0


if __name__ == '__main__':
    sys.exit(main())
