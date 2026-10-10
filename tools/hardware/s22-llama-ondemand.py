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

Measured 2026-10-10 (sway, idle): unloading frees ~860 MB of MemAvailable and
~0.5-1 % of a core; temperature unchanged (31-32 C).

  s22-llama-ondemand serve [--idle-s N]
  s22-llama-ondemand status
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
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
    return argv


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
        try:
            state.mkdir(parents=True, exist_ok=True)
            self.cache = {k: bytes.fromhex(v) for k, v in json.loads((state / 'cache.json').read_text()).items()}
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
                'cached': sorted(self.cache)}

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

    def ensure_backend(self) -> bool:
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
            with self.count_lock:
                self.active += 1
            self.last_use = self.clock()
            try:
                if not self.ensure_backend():
                    msg = b'{"error":"local model held by s22-guardian (thermal/memory)"}' if self.hold.exists() \
                        else b'{"error":"local model failed to load"}'
                    conn.sendall(http_response('503 Service Unavailable', msg))
                    return
                self.relay(conn, head)
            finally:
                with self.count_lock:
                    self.active -= 1
                self.last_use = self.clock()
        except OSError:
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def relay(self, client: socket.socket, head: bytes, count: bool = True) -> None:
        up = socket.create_connection(('127.0.0.1', self.backend_port), timeout=10)
        up.settimeout(None)
        client.settimeout(None)
        up.sendall(head)

        def pump(src, dst):
            try:
                while True:
                    data = src.recv(65536)
                    if not data:
                        break
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

    def serve(self, listen=LISTEN) -> None:
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(listen)
        srv.listen(32)
        stop = threading.Event()
        threading.Thread(target=self.idle_loop, args=(stop,), daemon=True).start()
        self.log(event='start', pid=os.getpid(), idle_s=self.idle_s, argv=self.argv)

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
    OnDemand(idle_s=a.idle_s).serve()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
