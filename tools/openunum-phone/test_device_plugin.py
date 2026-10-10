#!/usr/bin/env python3
"""Run s22-device plugin node:test suite; safely skip if Node >=18 is absent."""
from __future__ import annotations
import os, re, shutil, subprocess, sys
from pathlib import Path
HERE=Path(__file__).resolve().parent
TEST=HERE/'plugins'/'s22-device'/'test'/'plugin.test.mjs'
node=os.environ.get('NODE') or shutil.which('node')
if not node or not Path(node).is_file():
    print('SKIP s22-device plugin tests: node >=18 unavailable'); raise SystemExit(0)
r=subprocess.run([node,'--version'],capture_output=True,text=True)
m=re.match(r'v(\d+)\.',r.stdout.strip())
if not m or int(m.group(1))<18:
    print('SKIP s22-device plugin tests: node >=18 unavailable'); raise SystemExit(0)
raise SystemExit(subprocess.run([node,'--test',str(TEST)],cwd=HERE,env={**os.environ,'S22_PHONE_PLUGIN_BASE':'builtin'},timeout=150).returncode)
