#!/usr/bin/env python3
"""Compact OpenUnum profile for the phone's local :8090 model (native root, as root).

The local model (Gemma-4 E2B Q4_0 on two throttled A710 cores, keepalive "llama_backend")
prefills at ~22-31 tok/s. OpenUnum 512541d0 sent it ~8-10k tokens per turn: 19 core tool
schemas (~5.4k tokens) + system prompt + up to 140 history messages, i.e. 5-8 minutes of
prefill before the first token, at 77-78 C. The 2026-10-09 compact profile (swap08.sh:
modelExecutionProfiles.compact.allowedTools + toolTrim) no longer applies: since the
no-guardrails change both are only enforced with runtime.guardrails.enabled.

What still applies with guardrails off, and only to local routes (Luna is untouched):
  * runtime.toolDisclosureCore  - the advertised core under toolDisclosure "search" (local
    providers only). Every installed tool stays callable through tool_search.
  * modelExecutionProfiles.compact.maxHistoryMessages - history window of the compact tier,
    which is the tier OpenUnum infers for a <=14B local model (Luna is "full").
It also renames the local model id to what :8090 actually serves.

  local-compact-profile.py apply [--config PATH] [--history N] [--tools a,b]
  local-compact-profile.py show  [--config PATH]

Run apply with OpenUnum STOPPED (it rewrites its config on save). Backup: PATH.pre-compact-<ts>.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time

CONFIG = '/mnt/omarchy-trial/root/.openunum/openunum.json'
LOCAL_MODEL = 'llama-cpp-local//models/gemma-4-E2B-it-qat-q4_0-heretic-Q4_0_PURE.gguf'
OLD_LOCAL = ('llama-cpp-local//models/Qwen3.5-0.8B-Q4_0.gguf', 'llama-cpp-local//models/Qwen3.5-2B-Q4_0.gguf')
CORE_TOOLS = ['web_search']
HISTORY = 6


def patch(cfg: dict, history: int = HISTORY, tools: list[str] | None = None) -> dict:
    m = cfg.setdefault('model', {})
    pm = m.setdefault('providerModels', {})
    pm['llama-cpp-local'] = LOCAL_MODEL
    if m.get('model') in OLD_LOCAL:
        m['model'] = LOCAL_MODEL
    r = cfg.setdefault('runtime', {})
    r['toolDisclosure'] = 'search'
    r['toolDisclosureCore'] = list(CORE_TOOLS if tools is None else tools)
    prof = r.setdefault('modelExecutionProfiles', {}).setdefault('compact', {})
    prof['maxHistoryMessages'] = int(history)
    return cfg


def summary(cfg: dict) -> dict:
    r = cfg.get('runtime', {})
    return {'model': cfg.get('model', {}).get('model'),
            'local': cfg.get('model', {}).get('providerModels', {}).get('llama-cpp-local'),
            'toolDisclosure': r.get('toolDisclosure'), 'toolDisclosureCore': r.get('toolDisclosureCore'),
            'compact.maxHistoryMessages': r.get('modelExecutionProfiles', {}).get('compact', {}).get('maxHistoryMessages'),
            'guardrails': r.get('guardrails')}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('cmd', choices=('apply', 'show'))
    ap.add_argument('--config', default=CONFIG)
    ap.add_argument('--history', type=int, default=HISTORY)
    ap.add_argument('--tools', default=','.join(CORE_TOOLS))
    a = ap.parse_args(argv)
    cfg = json.load(open(a.config))
    if a.cmd == 'show':
        print(json.dumps(summary(cfg), indent=1))
        return 0
    bak = f'{a.config}.pre-compact-{time.strftime("%Y%m%d-%H%M%S")}'
    shutil.copy2(a.config, bak)
    patch(cfg, a.history, [t for t in a.tools.split(',') if t])
    tmp = a.config + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(cfg, f, indent=2)
    st = os.stat(a.config)
    os.chown(tmp, st.st_uid, st.st_gid)
    os.replace(tmp, a.config)
    print('backup', bak)
    print(json.dumps(summary(cfg), indent=1))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
