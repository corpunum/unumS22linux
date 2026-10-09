#!/usr/bin/env python3
"""check-model-pin: is OpenUnum's default model on the S22 still the pinned one?

Reads openunum.json (default: the phone's chroot copy) and checks that
model.provider / model.model are the pinned values (default openai /
openai/gpt-6-luna). With --routing it also checks the routing settings that
stop the automatic fallback switch (fallbackEnabled false, ollama-local
disabled; see README of tools/openunum-phone and the config-drift notes).
With --live it also asks the running server (GET /api/model/current), whose
in-memory value is what gets written back to the file after every turn.

Exit 0 = pinned, 1 = drifted, 2 = unreadable. Read-only: never writes.

  check-model-pin [--config PATH] [--provider P --model M] [--routing] [--live] [--json]
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

CONFIG = Path('/mnt/omarchy-trial/root/.openunum/openunum.json')
API = 'http://127.0.0.1:18880'


def check(doc: dict, provider: str, model: str, routing: bool = False) -> list[str]:
    m = doc.get('model') or {}
    problems = []
    if m.get('provider') != provider:
        problems.append(f"model.provider is {m.get('provider')!r}, want {provider!r}")
    if m.get('model') != model:
        problems.append(f"model.model is {m.get('model')!r}, want {model!r}")
    if routing:
        r = m.get('routing') or {}
        if r.get('fallbackEnabled') is not False:
            problems.append('model.routing.fallbackEnabled is not false (auto fallback can move the default)')
        if 'ollama-local' not in (r.get('disabledProviders') or []):
            problems.append('model.routing.disabledProviders lacks ollama-local (mission recovery can switch to it)')
    return problems


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--config', type=Path, default=CONFIG)
    ap.add_argument('--provider', default='openai')
    ap.add_argument('--model', default='openai/gpt-6-luna')
    ap.add_argument('--routing', action='store_true')
    ap.add_argument('--live', action='store_true')
    ap.add_argument('--json', action='store_true')
    a = ap.parse_args(argv)
    out: dict = {'config': str(a.config), 'want': {'provider': a.provider, 'model': a.model}}
    try:
        doc = json.loads(a.config.read_text())
    except (OSError, ValueError) as e:
        out.update(ok=False, error=f'{type(e).__name__}: {e}')
        print(json.dumps(out) if a.json else f'unreadable: {out["error"]}')
        return 2
    problems = check(doc, a.provider, a.model, a.routing)
    if a.live:
        try:
            with urllib.request.urlopen(API + '/api/model/current', timeout=10) as r:
                cur = json.loads(r.read() or b'{}')
            out['live'] = {k: cur.get(k) for k in ('provider', 'model', 'activeProvider', 'activeModel')}
            problems += [f'live {p}' for p in check({'model': cur}, a.provider, a.model)]
        except (OSError, ValueError) as e:
            out['live'] = {'error': f'{type(e).__name__}: {e}'}
    out.update(ok=not problems, problems=problems)
    if a.json:
        print(json.dumps(out, indent=1))
    else:
        print('pinned' if not problems else 'DRIFT: ' + '; '.join(problems))
    return 0 if not problems else 1


if __name__ == '__main__':
    sys.exit(main())
