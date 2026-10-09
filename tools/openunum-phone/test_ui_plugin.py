#!/usr/bin/env python3
"""Run the s22-ui OpenUnum plugin's node:test suite from a Python-only runner.

Usage: python3 -I -B tools/openunum-phone/test_ui_plugin.py

Hardware-free and dependency-free: the suite starts a fake s22-touchd on a
temporary unix socket. If no suitable node (>= 18, which has
`node --test`) is found the test is SKIPPED with exit 0; a failing suite exits
non-zero. Works under a stripped environment (PATH=os.defpath) by also probing
the usual node install locations. Safe under python -O (no bare asserts).
"""
from __future__ import annotations

import glob
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

HERE = Path(__file__).resolve().parent
TEST_FILE = HERE / "plugins" / "s22-ui" / "test" / "plugin.test.mjs"
TIMEOUT_SECONDS = 150
MIN_NODE_MAJOR = 18


def _candidates() -> list[str]:
    found: list[str] = []
    explicit = os.environ.get("NODE")
    if explicit:
        found.append(explicit)
    which = shutil.which("node")
    if which:
        found.append(which)
    found += [
        "/usr/local/bin/node",
        "/usr/bin/node",
        "/opt/node/bin/node",
    ]
    # GitHub-hosted runners keep extra versions in the tool cache.
    found += sorted(glob.glob("/opt/hostedtoolcache/node/*/x64/bin/node"), reverse=True)
    seen: set[str] = set()
    unique: list[str] = []
    for path in found:
        if path not in seen:
            seen.add(path)
            unique.append(path)
    return unique


def _node_major(node: str) -> int | None:
    try:
        out = subprocess.run([node, "--version"], capture_output=True, text=True,
                             timeout=20, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.match(r"v(\d+)\.", out.stdout.strip())
    return int(match.group(1)) if out.returncode == 0 and match else None


def find_node() -> tuple[str | None, str]:
    tried = []
    for node in _candidates():
        if not (os.path.isfile(node) and os.access(node, os.X_OK)):
            continue
        major = _node_major(node)
        tried.append(f"{node} (v{major})" if major else f"{node} (unusable)")
        if major is not None and major >= MIN_NODE_MAJOR:
            return node, ""
    detail = ", ".join(tried) if tried else "no node binary found"
    return None, detail


def main() -> int:
    if not TEST_FILE.is_file():
        print(f"FAIL: test file missing: {TEST_FILE}", file=sys.stderr)
        return 1
    node, detail = find_node()
    if node is None:
        print(f"SKIP s22-phone plugin tests: node >= {MIN_NODE_MAJOR} not available ({detail})")
        return 0
    env = dict(os.environ)
    env["S22_PHONE_PLUGIN_BASE"] = "builtin"
    env.pop("NODE_OPTIONS", None)
    command = [node, "--test", str(TEST_FILE)]
    print("RUN " + " ".join(command), flush=True)
    try:
        result = subprocess.run(command, cwd=str(HERE), env=env, check=False,
                                timeout=TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        print(f"FAIL: node --test timed out after {TIMEOUT_SECONDS}s", file=sys.stderr)
        return 124
    if result.returncode != 0:
        print(f"FAIL: s22-phone plugin tests exited {result.returncode}", file=sys.stderr)
        return 1
    print("PASS s22-phone plugin tests")
    return 0


if __name__ == "__main__":
    sys.exit(main())
