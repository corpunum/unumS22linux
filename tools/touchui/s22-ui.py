#!/usr/bin/env python3
"""s22-ui: one action surface for the S22 touch UI (user, agent and gestures).

Runs INSIDE the Arch chroot (as /usr/local/bin/s22-ui). Talks to s22-touchd on
/run/s22-touch/ctl.sock; prints one JSON object.

  s22-ui home | hide | back | switcher | lock | unlock | state
  s22-ui open chat|phone|camera|files|settings|switcher|terminal
  s22-ui card TITLE BODY [--ttl SECONDS]
  s22-ui confirm QUESTION [--timeout SECONDS]     -> {"answer": "yes"|"no"|"timeout"}
  s22-ui keyboard on|off
  s22-ui display on|off|toggle|status             (off = lock cover + DPMS off)
  s22-ui status                                   (mute state, display, keepalive)
  s22-ui model current|luna|local
  s22-ui camera                                   (starts a rear still; result in /run/s22-touch/camera.json)
  s22-ui screenshot PATH                          (grim, PNG)
  s22-ui perf [SECONDS]                           (frame-time probe -> /run/s22-touch/perf.json)
"""
from __future__ import annotations

import json
import os
import secrets
import socket
import subprocess
import sys
import time
from pathlib import Path

RUN = Path(os.environ.get('S22_TOUCH_RUN', '/run/s22-touch'))
SOCK = RUN / 'ctl.sock'
SIMPLE_UI = {'home', 'hide', 'back', 'switcher', 'lock', 'unlock', 'state'}


def request(req: dict, timeout: float = 30) -> dict:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(str(SOCK))
    except OSError as e:
        return {'ok': False, 'error': 'touchd_unreachable', 'message': str(e)}
    with s:
        s.sendall((json.dumps(req) + '\n').encode())
        data = b''
        while not data.endswith(b'\n'):
            chunk = s.recv(65536)
            if not chunk:
                break
            data += chunk
    try:
        return json.loads(data.decode())
    except ValueError:
        return {'ok': False, 'error': 'bad_response', 'raw': data.decode(errors='replace')[:400]}


def ui(*args, timeout: float = 8) -> dict:
    return request({'cmd': 'ui', 'args': [str(a) for a in args], 'timeout': timeout}, timeout + 5)


def opt(argv: list[str], name: str, default: str) -> tuple[list[str], str]:
    if name in argv:
        i = argv.index(name)
        if i + 1 < len(argv):
            return argv[:i] + argv[i + 2:], argv[i + 1]
    return argv, default


def confirm(question: str, timeout_s: float) -> dict:
    cid = 'c' + secrets.token_hex(6)
    res = RUN / 'confirm' / f'{cid}.json'
    r = ui('confirm', cid, question, int(timeout_s))
    if not r.get('ok') or r.get('result') != 'asking':
        return {'ok': False, 'error': 'confirm_not_shown', 'detail': r}
    deadline = time.time() + timeout_s + 5
    while time.time() < deadline:
        if res.exists():
            try:
                ans = json.loads(res.read_text())
                res.unlink(missing_ok=True)
                return {'ok': True, 'id': cid, 'answer': ans.get('answer', 'unknown')}
            except (OSError, ValueError):
                pass
        time.sleep(0.5)
    return {'ok': True, 'id': cid, 'answer': 'timeout'}


def session_env() -> dict:
    env = dict(os.environ)
    env.setdefault('XDG_RUNTIME_DIR', '/run/user/0')
    env.setdefault('WAYLAND_DISPLAY', 'wayland-1')
    return env


def main(argv: list[str]) -> dict:
    if not argv or argv[0] in ('-h', '--help'):
        print(__doc__)
        return {'ok': True}
    cmd, rest = argv[0], argv[1:]
    if cmd in SIMPLE_UI:
        r = ui(cmd)
        if cmd == 'state' and r.get('ok') and isinstance(r.get('result'), str):
            try:
                r['result'] = json.loads(r['result'])
            except ValueError:
                pass
        return r
    if cmd == 'open':
        return ui('open', rest[0] if rest else '')
    if cmd == 'card':
        rest, ttl = opt(rest, '--ttl', '8')
        if not rest:
            return {'ok': False, 'error': 'usage: card TITLE [BODY] [--ttl S]'}
        return ui('card', rest[0], ' '.join(rest[1:]), ttl)
    if cmd == 'confirm':
        rest, t = opt(rest, '--timeout', '60')
        if not rest:
            return {'ok': False, 'error': 'usage: confirm QUESTION [--timeout S]'}
        return confirm(' '.join(rest), max(5.0, min(600.0, float(t))))
    if cmd == 'keyboard':
        return ui('keyboard', rest[0] if rest else 'on')
    if cmd == 'display':
        return request({'cmd': 'display', 'arg': rest[0] if rest else 'toggle'})
    if cmd == 'status':
        return request({'cmd': 'status', 'fresh': '--fresh' in rest})
    if cmd == 'model':
        return request({'cmd': 'model', 'arg': rest[0] if rest else 'current'})
    if cmd == 'camera':
        return request({'cmd': 'camera_capture'})
    if cmd == 'screenshot':
        path = rest[0] if rest else f'/tmp/s22-screen-{int(time.time())}.png'
        r = subprocess.run(['grim', '-l', '1', path], env=session_env(), capture_output=True, text=True, timeout=20)
        return {'ok': r.returncode == 0, 'path': path, 'error': r.stderr.strip()[-300:] or None}
    if cmd == 'perf':
        secs = float(rest[0]) if rest else 5.0
        out = RUN / 'perf.json'
        out.unlink(missing_ok=True)
        r = ui('perf', int(secs))
        if not r.get('ok'):
            return r
        deadline = time.time() + secs + 15
        while time.time() < deadline:
            if out.exists():
                try:
                    return {'ok': True, **json.loads(out.read_text())}
                except ValueError:
                    pass
            time.sleep(0.5)
        return {'ok': False, 'error': 'perf_timeout'}
    return {'ok': False, 'error': f'unknown command {cmd!r}', 'help': 's22-ui --help'}


if __name__ == '__main__':
    out = main(sys.argv[1:])
    if out is not None and not (len(sys.argv) > 1 and sys.argv[1] in ('-h', '--help')):
        print(json.dumps(out))
    sys.exit(0 if (out or {}).get('ok', True) else 1)
