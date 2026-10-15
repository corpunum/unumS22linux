#!/usr/bin/env python3
"""Run the self-contained Lightpanda launcher tests using Node's built-in test runner."""
from __future__ import annotations
import os
from pathlib import Path
import shutil
import subprocess
import sys

HERE = Path(__file__).resolve().parent

def main() -> int:
    node = shutil.which('node') or '/opt/node/bin/node'
    if not Path(node).is_file():
        print('FAIL: Node.js not found', file=sys.stderr)
        return 1
    env = dict(os.environ)
    env.setdefault('OPENUNUM_HOME', '/tmp/openunum-lightpanda-test')
    return subprocess.run([node, '--test', str(HERE / 'tests/lightpanda-ondemand.test.mjs')], cwd=HERE, env=env).returncode

if __name__ == '__main__':
    raise SystemExit(main())
