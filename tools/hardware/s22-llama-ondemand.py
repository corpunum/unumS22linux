#!/usr/bin/env python3
"""s22-llama-ondemand: load the :8090 local fallback model only when it is used.

Native (Alpine) root, as root; supervised by s22-keepalive (service "llama").
Listens on 127.0.0.1:8090, where OpenUnum, s22-assistant and s22-touchd expect
the Qwen3.5-0.8B fallback, and runs the real llama-server on 127.0.0.1:8091.

  * A real request (anything but the probes below) starts llama-server if it
    is not running (warm load measured at ~2.6 s on the phone, against ~35 s of
    prefill for a fallback turn), then the connection is relayed byte for byte
    (streaming works).
  * OpenUnum probes the provider every few seconds (GET /health, /v1/models,
    /props, /slots). While the model is unloaded these are answered from the
    copies cached the last time it was up (/health: {"status":"ok"}), so
    probes never load the model and the fallback still counts as available.
  * After IDLE_S (default 600 s) without requests, llama-server is stopped by
    its exact process group.
  * /run/s22-hold/llama (s22-guardian, thermal) blocks loading: requests get
    503 and a running server is left to the guardian.

  * Prompt precache (2026-10-12). A cold prefill of OpenUnum's ~5.6k-token system prompt + tool
    schemas costs 4-8 min on the phone CPU. The proxy therefore learns the STABLE PREFIX of the
    real chat requests it relays (leading system messages + tools + template knobs), keys it by
    sha256(prefix + model file + KV layout), warms it once in the background (system + tools +
    a one-character user turn, 1 token) and saves the llama-server slot to --slot-save-path.
    After every backend load the slot for the incoming request's prefix (else the current one) is
    restored (~0.14 s) BEFORE the request is relayed, so llama.cpp's prompt cache (--cache-reuse)
    only has to process the new tail. A changed prefix is re-warmed after the request that
    carried it; the last 3 prefixes/slots are kept. The warm job is cancelled if a request for a
    different prefix arrives, honours the guardian hold, and counts as "active" (no idle unload
    while it runs). All of it is best effort: any failure just means a cold prefill, as before.

Measured 2026-10-10 (sway, idle): unloading frees ~860 MB of MemAvailable and
~0.5-1 % of a core; temperature unchanged (31-32 C).

  s22-llama-ondemand serve [--idle-s N]
  s22-llama-ondemand status
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import http.client
import json
import os
import re
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

LISTEN = ('127.0.0.1', 8090)
BACKEND_PORT = 8091
HOLD = Path('/run/s22-hold/llama')
STATE = Path('/srv/s22/state/llama-ondemand')
LOCK = Path('/run/s22-llama-ondemand.lock')
KEEPALIVE_CONFIG = Path('/srv/s22/state/keepalive/config.json')
CACHED = ('/health', '/v1/models', '/props', '/slots')
# Cool profile, measured 2026-10-10 (4 back-to-back fallback turns, Qwen3.5-0.8B on the GPU):
#   any core, -t 4, full clocks   BIG 80-82 C in seconds, guardian stops it
#   mid cores 4-6, -t 2, 2.5 GHz  65 C, guardian stops it after 2 turns
#   mid cores 4-6, -t 2, 1.8 GHz  49 C max / 43 C avg over 97 s, gen ~18 tok/s, pp ~77 tok/s
# llama.cpp's CPU threads busy-wait on the GPU; on the X2 core (cpu7) that alone heats BIG.
# So: pin the server to the A710 cores, 2 threads, and cap that cluster only while loaded.
COOL_CPUS = {4, 5, 6}
COOL_THREADS = '2'
MID_MAX_FREQ = Path('/sys/devices/system/cpu/cpu4/cpufreq/scaling_max_freq')
MID_CAP_KHZ = int(os.environ.get('S22_LLAMA_MID_CAP_KHZ', '1800000'))
# -- prompt precache
SLOT_ARG = '/data/slots'            # as seen inside the isolated exec (chroot = the llama-vulkan dir)
KEEP_PREFIXES = 3
MIN_PREFIX_CHARS = 4000             # ignore small prompts (probes, one-off helpers): not worth a slot
WARM_TIMEOUT_S = 1800
CHAT_PATHS = ('/v1/chat/completions', '/chat/completions')
# first streamed chunk that carries model output (text, reasoning or a tool call), i.e. the real time to first token
CONTENT_RE = re.compile(rb'"(?:content|reasoning_content|tool_calls)"\s*:\s*(?:"[^"]|\[)')
WARM_QUIET_S = float(os.environ.get('S22_LLAMA_WARM_QUIET_S', '60'))   # warm only after the conversation went quiet
MAX_PREFIX_FRAC = 0.6
MAX_BODY = 8 * 1024 * 1024
DEFAULT_ARGV = ['/usr/bin/python3', '/srv/s22/hardware/bin/s22-gpu-exec-isolated',
                '/srv/s22/gpu-compat-20260921/llama-vulkan', '--bridge-v4', '/system/bin/llama-server',
                '-m', '/models/Qwen3.5-0.8B-Q4_0.gguf', '-ngl', '99', '-t', '4', '-c', '16384', '-np', '1',
                '-fa', 'on', '--cache-ram', '0', '--host', '127.0.0.1', '--port', str(BACKEND_PORT),
                '--reasoning', 'off', '--no-webui']


def backend_argv(cfg_path: Path = KEEPALIVE_CONFIG) -> list[str]:
    """llama-server argv: the keepalive "llama_backend" definition if any, with --port BACKEND_PORT."""
    try:
        argv = json.loads(cfg_path.read_text())['service_defs']['llama_backend']['argv']
    except (OSError, ValueError, KeyError, TypeError):
        argv = list(DEFAULT_ARGV)
    argv = [str(a) for a in argv]
    if '--port' in argv:
        argv[argv.index('--port') + 1] = str(BACKEND_PORT)
    else:
        argv += ['--port', str(BACKEND_PORT)]
    if '-t' in argv:
        argv[argv.index('-t') + 1] = COOL_THREADS
    if '--slot-save-path' not in argv:
        argv += ['--slot-save-path', SLOT_ARG]
    if '--cache-reuse' not in argv:
        argv += ['--cache-reuse', '256']
    return argv


def argv_value(argv: list[str], flag: str) -> str | None:
    return argv[argv.index(flag) + 1] if flag in argv and argv.index(flag) + 1 < len(argv) else None


def slot_host_dir(argv: list[str]) -> Path:
    """Host path of --slot-save-path (the server runs chrooted into the llama-vulkan dir)."""
    arg = argv_value(argv, '--slot-save-path') or SLOT_ARG
    for i, a in enumerate(argv):
        if a.endswith('s22-gpu-exec-isolated') and i + 1 < len(argv):
            return Path(argv[i + 1]) / arg.lstrip('/')
    return Path(arg)


def model_tag(argv: list[str]) -> str:
    """What the KV state depends on besides the prompt: model file (name + size), context, KV types."""
    m = argv_value(argv, '-m') or ''
    size = ''
    for i, a in enumerate(argv):
        if a.endswith('s22-gpu-exec-isolated') and i + 1 < len(argv):
            try:
                size = str((Path(argv[i + 1]) / m.lstrip('/')).stat().st_size)
            except OSError:
                pass
    return '|'.join([m, size] + [f'{f}={argv_value(argv, f)}' for f in ('-c', '-ctk', '-ctv', '-fa')])


def extract_prefix(body: dict) -> dict | None:
    """The stable part of a chat request: leading system/developer messages, tools and the knobs that
    change the rendered template. None if too small to be worth caching."""
    msgs = body.get('messages')
    if not isinstance(msgs, list):
        return None
    lead = []
    for m in msgs:
        if isinstance(m, dict) and m.get('role') in ('system', 'developer'):
            lead.append(m)
        else:
            break
    tools = body.get('tools') or []
    spec = {'system': lead, 'tools': tools}
    for k in ('chat_template_kwargs', 'reasoning_effort', 'tool_choice', 'parallel_tool_calls', 'model'):
        if k in body:
            spec[k] = body[k]
    if len(json.dumps(spec, sort_keys=True)) < MIN_PREFIX_CHARS or not lead:
        return None
    return spec


def prefix_key(spec: dict, tag: str) -> str:
    return hashlib.sha256((json.dumps(spec, sort_keys=True, ensure_ascii=False) + '\n' + tag).encode()).hexdigest()


def warm_body(spec: dict, n_predict: int = 1) -> dict:
    """A request that renders exactly the stable prefix, then a minimal user turn."""
    b = {'messages': list(spec['system']) + [{'role': 'user', 'content': '.'}], 'tools': spec['tools'],
         'stream': False, 'temperature': 0, 'max_tokens': n_predict, 'cache_prompt': True}
    for k in ('chat_template_kwargs', 'reasoning_effort', 'model'):
        if k in spec:
            b[k] = spec[k]
    if not b['tools']:
        del b['tools']
    return b


def pin_cool_cpus() -> None:   # Popen preexec_fn: runs in the child before exec
    try:
        os.sched_setaffinity(0, COOL_CPUS)
    except OSError:
        pass


def http_response(status: str, body: bytes, ctype: str = 'application/json') -> bytes:
    return (f'HTTP/1.1 {status}\r\nContent-Type: {ctype}\r\nContent-Length: {len(body)}\r\n'
            'Connection: close\r\n\r\n').encode() + body


class OnDemand:
    def __init__(self, idle_s: float = 600, argv: list[str] | None = None, backend_port: int = BACKEND_PORT,
                 hold: Path = HOLD, state: Path = STATE, start_timeout_s: float = 120, clock=time.monotonic,
                 mid_cap: tuple[Path, int] | None = (MID_MAX_FREQ, MID_CAP_KHZ), preexec=pin_cool_cpus):
        self.preexec = preexec
        self.idle_s, self.backend_port, self.hold, self.state = idle_s, backend_port, hold, state
        self.argv = argv or backend_argv()
        self.start_timeout_s = start_timeout_s
        self.clock = clock
        self.proc: subprocess.Popen | None = None
        self.lock = threading.Lock()
        self.count_lock = threading.Lock()
        self.active = 0
        self.last_use = clock()
        self.cache: dict[str, bytes] = {}
        self.loads = 0
        self.mid_cap = mid_cap
        self.saved_mid_max: str | None = None
        # prompt precache (only when the backend argv has --slot-save-path)
        self.precache = '--slot-save-path' in self.argv
        self.slot_dir = slot_host_dir(self.argv)
        self.tag = model_tag(self.argv)
        self.prefix_dir = state / 'prefix'
        self.warm_lock = threading.Lock()
        self.index_lock = threading.Lock()
        self.warming: tuple[str, http.client.HTTPConnection] | None = None
        self.warm_failed: dict[str, float] = {}
        self.warm_n_predict = int(os.environ.get('S22_LLAMA_WARM_NPREDICT', '1'))
        self.index: dict = {'current': None, 'keys': {}}
        self.armer_for: str | None = None
        self.dirty = False
        self.last_user = ''     # log aid only (racy under concurrency, fine)
        try:
            state.mkdir(parents=True, exist_ok=True)
            self.cache = {k: bytes.fromhex(v) for k, v in json.loads((state / 'cache.json').read_text()).items()}
        except (OSError, ValueError):
            pass
        try:
            self.prefix_dir.mkdir(parents=True, exist_ok=True)
            self.index = json.loads((self.prefix_dir / 'index.json').read_text())
        except (OSError, ValueError):
            pass

    # -- log / status
    def log(self, **rec) -> None:
        rec['t'] = round(time.time(), 3)
        try:
            with open(self.state / 'ondemand.jsonl', 'a') as f:
                f.write(json.dumps(rec, sort_keys=True) + '\n')
        except OSError:
            pass

    def status(self) -> dict:
        return {'loaded': self.running(), 'pid': self.proc.pid if self.running() else None,
                'active': self.active, 'idle_for_s': round(self.clock() - self.last_use),
                'idle_s': self.idle_s, 'loads': self.loads, 'held': self.hold.exists(),
                'cached': sorted(self.cache), 'precache': self.precache,
                'prefixes': {k[:12]: v for k, v in self.index.get('keys', {}).items()},
                'current': (self.index.get('current') or '')[:12], 'warming': bool(self.warming)}

    # -- cool profile: cap the mid cluster while the model is loaded
    def cap_clock(self) -> None:
        if not self.mid_cap or self.saved_mid_max is not None:
            return
        path, khz = self.mid_cap
        try:
            self.saved_mid_max = path.read_text().strip()
            path.write_text(str(khz))
            self.log(event='clock_cap', khz=khz, was=self.saved_mid_max)
        except OSError as e:
            self.saved_mid_max = None
            self.log(event='clock_cap_failed', error=str(e))

    def restore_clock(self) -> None:
        if not self.mid_cap or self.saved_mid_max is None:
            return
        try:
            self.mid_cap[0].write_text(self.saved_mid_max)
            self.log(event='clock_restore', khz=self.saved_mid_max)
        except OSError as e:
            self.log(event='clock_restore_failed', error=str(e))
        self.saved_mid_max = None

    # -- backend
    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def backend_healthy(self, timeout: float = 1.0) -> bool:
        try:
            with urllib.request.urlopen(f'http://127.0.0.1:{self.backend_port}/health', timeout=timeout) as r:
                return r.status < 500
        except Exception:  # noqa: BLE001
            return False

    def ensure_backend(self, want_key: str | None = None) -> bool:
        with self.lock:
            if self.running() and self.backend_healthy():
                return True
            if self.hold.exists():
                return False
            if not self.running():
                t0 = self.clock()
                log = open(self.state / 'llama-server.log', 'ab')
                self.cap_clock()
                self.proc = subprocess.Popen(self.argv, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                             start_new_session=True, preexec_fn=self.preexec)
                log.close()
                self.loads += 1
                self.log(event='load', pid=self.proc.pid)
            deadline = self.clock() + self.start_timeout_s
            while self.clock() < deadline:
                if self.backend_healthy():
                    self.log(event='ready', pid=self.proc.pid, load_s=round(self.clock() - t0, 2)
                             if 't0' in locals() else None)
                    self.refresh_cache()
                    self.restore_slot(want_key)
                    return True
                if not self.running():
                    self.log(event='load_failed', rc=self.proc.returncode)
                    self.restore_clock()
                    return False
                time.sleep(0.2)
            self.log(event='load_timeout')
            return False

    def refresh_cache(self) -> None:
        for path in CACHED:
            try:
                with urllib.request.urlopen(f'http://127.0.0.1:{self.backend_port}{path}', timeout=3) as r:
                    if r.status == 200:
                        self.cache[path] = r.read()
            except Exception:  # noqa: BLE001
                pass
        try:
            (self.state / 'cache.json').write_text(json.dumps({k: v.hex() for k, v in self.cache.items()}))
        except OSError:
            pass

    def unload(self, reason: str) -> None:
        with self.lock:
            if not self.running():
                self.restore_clock()      # e.g. the guardian stopped the server
                return
            pid = self.proc.pid
            try:
                os.killpg(pid, signal.SIGTERM)       # its own session: exact process group
            except ProcessLookupError:
                pass
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                self.proc.wait(5)
            self.log(event='unload', pid=pid, reason=reason)
            self.restore_clock()

    # -- prompt precache
    def slot_name(self, key: str) -> str:
        return key[:20] + '.bin'

    def save_index(self) -> None:
        try:
            (self.prefix_dir / 'index.json').write_text(json.dumps(self.index, sort_keys=True, indent=1))
        except OSError:
            pass

    def backend_post(self, path: str, body: dict, timeout: float = 30) -> dict:
        c = http.client.HTTPConnection('127.0.0.1', self.backend_port, timeout=timeout)
        try:
            c.request('POST', path, json.dumps(body), {'Content-Type': 'application/json'})
            r = c.getresponse()
            data = r.read()
            if r.status >= 300:
                raise OSError(f'{path} -> {r.status} {data[:200]!r}')
            return json.loads(data or b'{}')
        finally:
            c.close()

    def observe(self, spec: dict, key: str) -> None:
        """A real request carried this prefix: remember it (spec on disk, most recent = current), keep the last 3."""
        with self.index_lock:
            spec_path = self.prefix_dir / (key[:20] + '.json')
            if not spec_path.exists():
                try:
                    spec_path.write_text(json.dumps(spec, ensure_ascii=False))
                except OSError:
                    return
            keys = self.index.setdefault('keys', {})
            ent = keys.setdefault(key, {'slot': False, 'chars': len(json.dumps(spec)), 'first_seen': round(time.time())})
            ent['seen'] = round(time.time())
            self.index['current'] = key
            for old in sorted(keys, key=lambda k: keys[k].get('seen', 0))[:-KEEP_PREFIXES]:
                for f in (self.slot_dir / self.slot_name(old), self.prefix_dir / (old[:20] + '.json')):
                    try:
                        f.unlink()
                    except OSError:
                        pass
                keys.pop(old, None)
                self.log(event='prefix_pruned', key=old[:12])
            self.save_index()

    def cacheable(self, spec: dict) -> bool:
        """A prefix must leave room in the context: ~3 chars per token, at most 60 % of -c."""
        ctx = int(argv_value(self.argv, '-c') or 8192)
        return len(json.dumps(spec)) / 3 <= ctx * MAX_PREFIX_FRAC

    def has_slot(self, key: str | None) -> bool:
        ent = self.index.get('keys', {}).get(key or '')
        return bool(ent and ent.get('slot') and (self.slot_dir / self.slot_name(key)).exists())

    def restore_slot(self, want_key: str | None) -> None:
        """Right after a load: put the saved prefix KV state back (the request is relayed afterwards)."""
        if not self.precache:
            return
        key = want_key if want_key and want_key in self.index.get('keys', {}) else (
            None if want_key else self.index.get('current'))
        if not self.has_slot(key):
            return
        t0 = time.monotonic()
        try:
            r = self.backend_post('/slots/0?action=restore', {'filename': self.slot_name(key)}, timeout=60)
            self.dirty = False
            self.log(event='slot_restore', key=key[:12], ms=round((time.monotonic() - t0) * 1000),
                     n_restored=r.get('n_restored'))
        except Exception as e:  # noqa: BLE001
            self.index['keys'][key]['slot'] = False
            self.save_index()
            self.log(event='slot_restore_failed', key=key[:12], error=str(e)[:200])

    def save_slot(self, key: str) -> None:
        t0 = time.monotonic()
        r = self.backend_post('/slots/0?action=save', {'filename': self.slot_name(key)}, timeout=120)
        with self.index_lock:
            ent = self.index['keys'].get(key)
            if ent is not None:
                ent['slot'] = True
                ent['n_saved'] = r.get('n_saved')
                ent['saved_at'] = round(time.time())
                self.save_index()
        self.log(event='slot_save', key=key[:12], ms=round((time.monotonic() - t0) * 1000), n_saved=r.get('n_saved'),
                 bytes=r.get('n_written'))

    def cancel_warm(self, except_key: str | None = None) -> None:
        w = self.warming
        if w and w[0] != except_key:
            try:
                w[1].sock.close() if w[1].sock else None
            except OSError:
                pass
            self.log(event='warm_cancelled', key=w[0][:12], by=(except_key or '')[:12])

    def warm(self, key: str) -> None:
        """Background: make the llama-server slot hold exactly this prefix, then save it. Best effort."""
        if not self.precache or not self.warm_lock.acquire(blocking=False):
            return
        with self.count_lock:
            self.active += 1
        t0 = time.monotonic()
        try:
            if self.has_slot(key) or self.hold.exists():
                return
            spec = json.loads((self.prefix_dir / (key[:20] + '.json')).read_text())
            if not self.ensure_backend(key):
                return
            if self.has_slot(key):          # restored by the load itself
                return
            self.log(event='warm_start', key=key[:12])
            c = http.client.HTTPConnection('127.0.0.1', self.backend_port, timeout=WARM_TIMEOUT_S)
            self.warming = (key, c)
            try:
                c.request('POST', '/v1/chat/completions', json.dumps(warm_body(spec, self.warm_n_predict)),
                          {'Content-Type': 'application/json'})
                r = c.getresponse()
                data = r.read()
            finally:
                self.warming = None
                c.close()
            if r.status != 200:
                raise OSError(f'warm -> {r.status} {data[:200]!r}')
            try:
                timings = json.loads(data).get('timings', {})
            except ValueError:
                timings = {}
            self.log(event='warm_done', key=key[:12], s=round(time.monotonic() - t0, 1),
                     prompt_n=timings.get('prompt_n'), cache_n=timings.get('cache_n'),
                     prompt_ms=timings.get('prompt_ms'))
            self.save_slot(key)
        except Exception as e:  # noqa: BLE001
            self.warm_failed[key] = time.time()
            self.log(event='warm_failed', key=key[:12], error=str(e)[:200])
        finally:
            with self.count_lock:
                self.active -= 1
            self.last_use = self.clock()
            self.warm_lock.release()

    def rearm(self, key: str, force: bool = False) -> None:
        """Quiet period after a turn, slot already saved: put the bare-prefix state back (0.1 s).

        Measured 2026-10-12 on the phone: after a turn the live slot holds the whole conversation and the
        context checkpoint that sat at the prefix boundary has been consumed, so the NEXT turn (whose user
        wrapper differs from the start) cannot roll back to the prefix and re-prefills ALL ~5.4k tokens (300 s).
        Restoring the saved slot puts the prefix state and its checkpoints back, so that next turn costs ~85 s."""
        if (self.active and not force) or not self.running() or self.hold.exists():
            return
        t0 = time.monotonic()
        try:
            self.backend_post('/slots/0?action=restore', {'filename': self.slot_name(key)}, timeout=60)
            self.dirty = False
            self.log(event='slot_rearm', key=key[:12], ms=round((time.monotonic() - t0) * 1000))
        except Exception as e:  # noqa: BLE001
            self.log(event='slot_rearm_failed', key=key[:12], error=str(e)[:200])

    def warm_later(self, key: str) -> None:
        """Warming rolls the slot back to the bare prefix, which would throw away the conversation tail the
        NEXT request of the same turn (tool result follow-up) wants to reuse. So wait until things went quiet."""
        if not (self.precache and not self.has_slot(key) and time.time() - self.warm_failed.get(key, 0) > 600):
            return
        with self.index_lock:
            if self.armer_for == key:      # a quiet-period thread for this key is already waiting
                return
            self.armer_for = key

        def later():
            stamp = self.last_use
            deadline = self.clock() + 3600
            while self.clock() < deadline:
                time.sleep(min(2.0, max(0.05, WARM_QUIET_S / 4)))
                if self.last_use != stamp or self.active:
                    stamp = self.last_use      # still busy: restart the quiet period
                    continue
                if self.clock() - stamp >= WARM_QUIET_S:
                    break
            else:
                return
            self.armer_for = None
            if self.index.get('current') != key:
                return
            if not self.has_slot(key):
                self.warm(key)

        def run():
            try:
                later()
            finally:
                self.armer_for = None
        threading.Thread(target=run, daemon=True).start()

    def idle_loop(self, stop: threading.Event) -> None:
        while not stop.wait(5):
            if self.running() and self.active == 0 and self.clock() - self.last_use >= self.idle_s:
                self.unload('idle')
            elif not self.running() and self.saved_mid_max is not None:
                self.restore_clock()      # the server exited on its own (guardian, crash)

    # -- connections
    def handle(self, conn: socket.socket) -> None:
        try:
            conn.settimeout(30)
            head = b''
            while b'\r\n\r\n' not in head and len(head) < 65536:
                chunk = conn.recv(4096)
                if not chunk:
                    return
                head += chunk
            line = head.split(b'\r\n', 1)[0].decode('latin-1', 'replace').split(' ')
            method, path = (line + ['', ''])[:2]
            path = path.split('?', 1)[0]
            probe = method == 'GET' and path in CACHED
            if probe and not (self.running() and self.backend_healthy(0.5)):
                body = self.cache.get(path) or (b'{"status":"ok"}' if path == '/health' else None)
                if body is None:
                    conn.sendall(http_response('503 Service Unavailable', b'{"error":"not loaded"}'))
                else:
                    conn.sendall(http_response('200 OK', body))
                return
            if probe:       # loaded: answer from the backend, but a probe is not "use" (keeps idle timer)
                self.relay(conn, head, count=False)
                return
            spec = key = None
            last_role = ''
            self.last_user = ''
            if self.precache and method == 'POST' and path in CHAT_PATHS:
                head, spec, last_role = self.read_body(conn, head)
                if spec is not None and not self.cacheable(spec):
                    self.log(event='prefix_too_big', chars=len(json.dumps(spec)))
                    spec = None
                if spec is not None:
                    key = prefix_key(spec, self.tag)
                    self.cancel_warm(except_key=key)
            with self.count_lock:
                self.active += 1
            self.last_use = self.clock()
            t_req = self.clock()
            try:
                if not self.ensure_backend(key):
                    msg = b'{"error":"local model held by s22-guardian (thermal/memory)"}' if self.hold.exists() \
                        else b'{"error":"local model failed to load"}'
                    conn.sendall(http_response('503 Service Unavailable', msg))
                    return
                if key and last_role == 'user' and self.dirty and self.has_slot(key):
                    # New turn (not a tool-result continuation) and the slot was used by something else since the
                    # prefix state was last put in place: put it back (0.1 s). Measured: OpenUnum fires small side
                    # requests after every turn that wipe the cache; without this the next turn re-prefills 5.4k tokens.
                    self.rearm(key, force=True)
                if self.precache and method == 'POST':
                    self.dirty = True         # any generation request (chat or not) replaces the slot's cache
                    if not key:
                        self.log(event='other_post', path=path, bytes=len(head))
                first = self.relay(conn, head)
                if key:
                    self.log(event='request', key=key[:12], slot=self.has_slot(key), n_tools=len(spec['tools']),
                             user=self.last_user, 
                             ttft_s=None if first is None else round(first - t_req, 2),
                             total_s=round(self.clock() - t_req, 2))
            finally:
                with self.count_lock:
                    self.active -= 1
                self.last_use = self.clock()
            if key:
                self.observe(spec, key)
                self.warm_later(key)
        except OSError:
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def dump_user(self, j: dict) -> None:
        """Diagnostic: keep the first non-system turn of the last two big requests (what is NOT in the stable prefix)."""
        try:
            msgs = j.get('messages') or []
            rest = [m for m in msgs if not (isinstance(m, dict) and m.get('role') in ('system', 'developer'))]
            if rest and len(json.dumps(msgs)) > MIN_PREFIX_CHARS:
                d = self.state / 'last-user'
                d.mkdir(exist_ok=True)
                p1, p0 = d / 'last.json', d / 'prev.json'
                if p1.exists():
                    p1.replace(p0)
                p1.write_text(json.dumps(rest, ensure_ascii=False))
        except OSError:
            pass

    def read_body(self, conn: socket.socket, head: bytes) -> tuple[bytes, dict | None, str]:
        """Complete the request (Content-Length body) so its prefix can be learned; returns (bytes to relay, spec)."""
        try:
            hdr, _, rest = head.partition(b'\r\n\r\n')
            n = None
            for ln in hdr.split(b'\r\n')[1:]:
                k, _, v = ln.partition(b':')
                if k.strip().lower() == b'content-length':
                    n = int(v.strip())
                elif k.strip().lower() == b'transfer-encoding':
                    return head, None, ''
            if n is None or n > MAX_BODY:
                return head, None, ''
            body = rest
            while len(body) < n:
                chunk = conn.recv(min(65536, n - len(body)))
                if not chunk:
                    break
                body += chunk
            head = hdr + b'\r\n\r\n' + body
            if len(body) != n:
                return head, None, ''
            j = json.loads(body)
            self.dump_user(j)
            for m in reversed(j.get('messages') or []):
                if isinstance(m, dict) and m.get('role') == 'user':
                    self.last_user = str(m.get('content'))[:60]
                    break
            msgs = j.get('messages')
            role = str(msgs[-1].get('role')) if isinstance(msgs, list) and msgs and isinstance(msgs[-1], dict) else ''
            return head, extract_prefix(j), role
        except (ValueError, OSError, AttributeError, TypeError):
            return head, None, ''

    def relay(self, client: socket.socket, head: bytes, count: bool = True) -> float | None:
        up = socket.create_connection(('127.0.0.1', self.backend_port), timeout=10)
        up.settimeout(None)
        client.settimeout(None)
        up.sendall(head)

        first: list[float] = []
        content_first: list[float] = []

        def pump(src, dst):
            try:
                while True:
                    data = src.recv(65536)
                    if not data:
                        break
                    if src is up:
                        if not first:
                            first.append(self.clock())
                        if len(content_first) < 1 and CONTENT_RE.search(data):
                            content_first.append(self.clock())

                    dst.sendall(data)
                    if count:
                        self.last_use = self.clock()
            except OSError:
                pass
            finally:
                try:
                    dst.shutdown(socket.SHUT_WR)
                except OSError:
                    pass

        t = threading.Thread(target=pump, args=(client, up), daemon=True)
        t.start()
        pump(up, client)
        t.join(5)
        up.close()
        return (content_first[0] if content_first else (first[0] if first else None))

    def warm_on_start(self) -> None:
        """Optional (--warm-on-start): after boot, bring the current prefix's slot back (or build it)."""
        key = self.index.get('current')
        if not (self.precache and key):
            return
        if self.has_slot(key):
            self.ensure_backend(key)
            self.last_use = self.clock()       # the idle unload still applies
        else:
            self.warm(key)

    def serve(self, listen=LISTEN, warm_start: bool = False) -> None:
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(listen)
        srv.listen(32)
        stop = threading.Event()
        threading.Thread(target=self.idle_loop, args=(stop,), daemon=True).start()
        self.log(event='start', pid=os.getpid(), idle_s=self.idle_s, argv=self.argv, precache=self.precache)
        if warm_start:
            threading.Thread(target=self.warm_on_start, daemon=True).start()

        def bye(*_):
            stop.set()
            self.unload('shutdown')
            os._exit(0)
        signal.signal(signal.SIGTERM, bye)
        signal.signal(signal.SIGINT, bye)
        while True:
            conn, _ = srv.accept()
            threading.Thread(target=self.handle, args=(conn,), daemon=True).start()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('cmd', nargs='?', default='serve', choices=['serve', 'status'])
    ap.add_argument('--warm-on-start', action='store_true',
                    default=os.environ.get('S22_LLAMA_WARM_ON_START') == '1',
                    help='after boot restore (or build) the current prefix slot; the idle unload still applies')
    ap.add_argument('--idle-s', type=float, default=float(os.environ.get('S22_LLAMA_IDLE_S', 600)))
    a = ap.parse_args(argv)
    if a.cmd == 'status':
        try:
            with urllib.request.urlopen(f'http://127.0.0.1:{BACKEND_PORT}/health', timeout=2) as r:
                loaded = r.status < 500
        except Exception:  # noqa: BLE001
            loaded = False
        print(json.dumps({'loaded': loaded, 'held': HOLD.exists()}))
        return 0
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(LOCK, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print('already running', file=sys.stderr)
        return 1
    OnDemand(idle_s=a.idle_s).serve(warm_start=a.warm_on_start)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
