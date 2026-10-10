#!/usr/bin/env python3
"""Host-only tests for s22-llama-ondemand with a fake llama-server.
Run: python3 tools/hardware/test_s22_llama_ondemand.py"""
import importlib.machinery
import importlib.util
import json
import socket
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
loader = importlib.machinery.SourceFileLoader('s22_llama_ondemand', str(HERE / 's22-llama-ondemand.py'))
spec = importlib.util.spec_from_loader('s22_llama_ondemand', loader)
mod = importlib.util.module_from_spec(spec)
loader.exec_module(mod)

FAKE = r'''
import http.server, json, sys, time
port = int(sys.argv[sys.argv.index('--port') + 1])
class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        body = {'/health': b'{"status":"ok"}', '/v1/models': b'{"models":[{"name":"fake"}]}'}.get(self.path)
        self.send_response(200 if body else 404); self.send_header('Content-Length', str(len(body or b'')))
        self.end_headers(); self.wfile.write(body or b'')
    def do_POST(self):
        n = int(self.headers.get('Content-Length', 0)); self.rfile.read(n)
        self.send_response(200); self.send_header('Content-Type', 'text/event-stream'); self.end_headers()
        for i in range(3):
            self.wfile.write(f'data: {{"tok": {i}}}\n\n'.encode()); self.wfile.flush(); time.sleep(0.05)
        self.wfile.write(b'data: [DONE]\n\n')
time.sleep(0.3)   # simulated model load
http.server.ThreadingHTTPServer(('127.0.0.1', port), H).serve_forever()
'''


def free_port():
    s = socket.socket(); s.bind(('127.0.0.1', 0)); p = s.getsockname()[1]; s.close(); return p


class OnDemandTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        (d / 'fake.py').write_text(FAKE)
        self.bport, self.fport = free_port(), free_port()
        self.hold = d / 'hold'
        self.od = mod.OnDemand(idle_s=1.0, argv=[sys.executable, str(d / 'fake.py'), '--port', str(self.bport)],
                               backend_port=self.bport, hold=self.hold, state=d / 'state', start_timeout_s=10)
        threading.Thread(target=self._serve, daemon=True).start()
        for _ in range(50):
            try:
                socket.create_connection(('127.0.0.1', self.fport), 0.2).close()
                break
            except OSError:
                time.sleep(0.05)

    def _serve(self):
        srv = socket.socket(); srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(('127.0.0.1', self.fport)); srv.listen(8)
        self.srv = srv
        stop = threading.Event()
        threading.Thread(target=self.od.idle_loop, args=(stop,), daemon=True).start()
        while True:
            try:
                c, _ = srv.accept()
            except OSError:
                return
            threading.Thread(target=self.od.handle, args=(c,), daemon=True).start()

    def tearDown(self):
        self.od.unload('test')
        self.srv.close()
        self.tmp.cleanup()

    def get(self, path):
        with urllib.request.urlopen(f'http://127.0.0.1:{self.fport}{path}', timeout=10) as r:
            return r.status, r.read()

    def post(self):
        req = urllib.request.Request(f'http://127.0.0.1:{self.fport}/v1/chat/completions', data=b'{"x":1}',
                                     headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, r.read()

    def test_probe_does_not_load(self):
        st, body = self.get('/health')
        self.assertEqual((st, json.loads(body)['status']), (200, 'ok'))
        self.assertFalse(self.od.running())
        self.assertEqual(self.od.loads, 0)

    def test_request_loads_streams_then_idle_unloads(self):
        st, body = self.post()
        self.assertEqual(st, 200)
        self.assertIn(b'data: [DONE]', body)
        self.assertEqual(body.count(b'"tok"'), 3)
        self.assertTrue(self.od.running())
        self.assertEqual(self.od.loads, 1)
        # cached probes now come from the real backend copy
        self.assertIn('/v1/models', self.od.cache)
        self.post()
        self.assertEqual(self.od.loads, 1)            # warm: no second load
        for _ in range(60):
            self.get('/health')                         # probes while loaded must not keep it loaded
            if not self.od.running():
                break
            time.sleep(0.2)
        self.assertFalse(self.od.running())
        st, body = self.get('/v1/models')               # served from cache while unloaded
        self.assertIn(b'fake', body)
        self.assertFalse(self.od.running())

    def test_hold_blocks_loading(self):
        self.hold.write_text('s22-guardian\n')
        with self.assertRaises(urllib.error.HTTPError) as e:
            self.post()
        self.assertEqual(e.exception.code, 503)
        self.assertFalse(self.od.running())

    def test_backend_argv_rewrites_port(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'c.json'
            p.write_text(json.dumps({'service_defs': {'llama_backend': {'argv': ['x', '--port', '8090']}}}))
            self.assertEqual(mod.backend_argv(p), ['x', '--port', str(mod.BACKEND_PORT)])
            self.assertIn('--port', mod.backend_argv(Path(d) / 'missing.json'))


if __name__ == '__main__':
    unittest.main()
