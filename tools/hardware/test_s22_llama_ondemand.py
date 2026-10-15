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
mod.WARM_QUIET_S = 0.4

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
        self.maxfreq = d / 'scaling_max_freq'
        self.maxfreq.write_text('2515000\n')
        self.od = mod.OnDemand(idle_s=1.0, argv=[sys.executable, str(d / 'fake.py'), '--port', str(self.bport)],
                               backend_port=self.bport, hold=self.hold, state=d / 'state', start_timeout_s=10,
                               mid_cap=(self.maxfreq, 1800000))
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
            self.assertEqual(mod.backend_argv(p), ['x', '--port', str(mod.BACKEND_PORT), '--slot-save-path',
                                                   mod.SLOT_ARG, '--cache-reuse', '256'])
            self.assertIn('--port', mod.backend_argv(Path(d) / 'missing.json'))


    # -- cool profile
    def test_clock_capped_while_loaded_and_restored_on_unload(self):
        self.assertTrue(self.od.ensure_backend())
        self.assertEqual(self.maxfreq.read_text().strip(), '1800000')
        self.od.unload('test')
        self.assertEqual(self.maxfreq.read_text().strip(), '2515000')

    def test_clock_restored_when_server_dies_on_its_own(self):
        self.assertTrue(self.od.ensure_backend())
        self.od.proc.kill(); self.od.proc.wait(5)
        for _ in range(40):          # idle loop polls every 5 s; call the restore path directly
            if self.maxfreq.read_text().strip() == '2515000':
                break
            self.od.unload('dead')
        self.assertEqual(self.maxfreq.read_text().strip(), '2515000')

    def test_backend_argv_forces_two_threads(self):
        argv = mod.backend_argv(Path('/nonexistent'))      # DEFAULT_ARGV carries -t 4
        self.assertEqual(argv[argv.index('-t') + 1], '2')


# -- prompt precache -------------------------------------------------------------------------
FAKE_SLOTS = r'''
import http.server, json, sys, time, os
port = int(sys.argv[sys.argv.index('--port') + 1])
slots = sys.argv[sys.argv.index('--slot-save-path') + 1]
LOG = os.path.join(slots, 'calls.log')
def note(s):
    open(LOG, 'a').write(s + '\n')
class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def reply(self, obj, code=200):
        b = json.dumps(obj).encode(); self.send_response(code); self.send_header('Content-Length', str(len(b)))
        self.end_headers(); self.wfile.write(b)
    def do_GET(self):
        self.reply({'status': 'ok'})
    def do_POST(self):
        n = int(self.headers.get('Content-Length', 0)); body = json.loads(self.rfile.read(n) or b'{}')
        if self.path.startswith('/slots/0'):
            fn = os.path.join(slots, body['filename'])
            if 'action=save' in self.path:
                open(fn, 'w').write('kv'); note('save ' + body['filename']); return self.reply({'n_saved': 10, 'n_written': 2})
            if os.path.exists(fn):
                note('restore ' + body['filename']); return self.reply({'n_restored': 10})
            return self.reply({'error': 'nofile'}, 400)
        note('chat max_tokens=%s last=%s' % (body.get('max_tokens'), body['messages'][-1]['content'][:20]))
        self.reply({'choices': [{'message': {'content': 'ok'}}], 'timings': {'prompt_n': 5, 'cache_n': 0}})
http.server.ThreadingHTTPServer(('127.0.0.1', port), H).serve_forever()
'''


class PrecacheTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        (d / 'slots').mkdir()
        (d / 'fake.py').write_text(FAKE_SLOTS)
        self.bport, self.fport = free_port(), free_port()
        self.argv = [sys.executable, str(d / 'fake.py'), '--port', str(self.bport), '--slot-save-path', str(d / 'slots')]
        self.make()
        self.slots = d / 'slots'

    def make(self):
        d = Path(self.tmp.name)
        self.od = mod.OnDemand(idle_s=60, argv=self.argv, backend_port=self.bport, hold=d / 'hold', state=d / 'state',
                               start_timeout_s=10, mid_cap=None)

    def tearDown(self):
        self.od.unload('test'); self.tmp.cleanup()

    @staticmethod
    def body(user, extra_sys='x'):
        return {'model': 'm', 'messages': [{'role': 'system', 'content': 'S' * 5000 + extra_sys},
                                           {'role': 'user', 'content': user}],
                'tools': [{'type': 'function', 'function': {'name': 't', 'parameters': {}}}], 'stream': True}

    def request(self, body):
        a, b = socket.socketpair()
        data = json.dumps(body).encode()
        a.sendall(b'POST /v1/chat/completions HTTP/1.1\r\nHost: x\r\nContent-Length: %d\r\n\r\n' % len(data) + data)
        t = threading.Thread(target=self.od.handle, args=(b,)); t.start()
        t.join(30)
        a.close()
        t.join(5)

    def calls(self):
        f = self.slots / 'calls.log'
        return f.read_text().splitlines() if f.exists() else []

    def wait(self, pred, secs=10):
        for _ in range(int(secs / 0.1)):
            if pred():
                return True
            time.sleep(0.1)
        return False

    def test_extract_prefix_ignores_small_prompts_and_user_turns(self):
        self.assertIsNone(mod.extract_prefix({'messages': [{'role': 'system', 'content': 'hi'}, {'role': 'user', 'content': 'x'}]}))
        a = mod.extract_prefix(self.body('what is the battery?'))
        b = mod.extract_prefix(self.body('another question'))
        self.assertEqual(a, b)
        self.assertNotEqual(mod.prefix_key(a, 'm1'), mod.prefix_key(a, 'm2'))
        self.assertNotEqual(mod.prefix_key(a, 'm1'), mod.prefix_key(mod.extract_prefix(self.body('q', 'y')), 'm1'))
        wb = mod.warm_body(a)
        self.assertEqual(wb['max_tokens'], 1)
        self.assertEqual(wb['messages'][-1], {'role': 'user', 'content': '.'})

    def test_learn_warm_save_then_restore_after_reload(self):
        self.request(self.body('what is the battery?'))
        self.assertTrue(self.wait(lambda: any(c.startswith('save ') for c in self.calls())), self.calls())
        self.assertTrue(any(c.startswith('chat max_tokens=1 last=.') for c in self.calls()))      # the warm request
        key = self.od.index['current']
        self.assertTrue(self.od.has_slot(key))
        self.assertTrue(self.wait(lambda: self.od.active == 0))
        self.od.unload('test')
        (self.slots / 'calls.log').unlink()
        self.request(self.body('second question'))
        self.assertEqual([c.split()[0] for c in self.calls()][:2], ['restore', 'chat'])          # restored BEFORE the request
        self.assertFalse(any(c.startswith('save') for c in self.calls()))                       # same prefix: no re-warm

    def test_new_turn_restores_prefix_but_tool_continuation_does_not(self):
        self.request(self.body('first'))
        self.assertTrue(self.wait(lambda: self.od.active == 0 and self.od.has_slot(self.od.index['current'])))
        restores = lambda: len([c for c in self.calls() if c.startswith('restore')])
        n = restores()
        self.request(self.body('second turn'))                    # slot was used since: prefix put back first
        self.assertEqual(restores(), n + 1)
        cont = self.body('second turn')
        cont['messages'] += [{'role': 'assistant', 'content': 'x'}, {'role': 'tool', 'content': 'y'}]
        self.request(cont)                                         # tool-result continuation: keep the live cache
        self.assertEqual(restores(), n + 1)
        self.request(self.body('third turn'))
        self.assertEqual(restores(), n + 2)

    def test_changed_prefix_rewarms_and_keeps_last_three(self):
        for i in range(5):
            self.request(self.body('q', extra_sys=f'v{i}'))
            self.assertTrue(self.wait(lambda: self.od.active == 0 and self.od.index['keys'][self.od.index['current']]['slot']))
        self.assertEqual(len(self.od.index['keys']), mod.KEEP_PREFIXES)
        self.assertEqual(len(list(self.slots.glob('*.bin'))), mod.KEEP_PREFIXES)
        self.assertEqual(len(list((self.od.prefix_dir).glob('*.json'))) - 1, mod.KEEP_PREFIXES)   # minus index.json

    def test_state_survives_proxy_restart(self):
        self.request(self.body('q'))
        self.assertTrue(self.wait(lambda: self.od.active == 0 and self.od.has_slot(self.od.index['current'])))
        key = self.od.index['current']
        self.od.unload('test')
        self.make()                                         # new proxy process, same state dir
        self.assertTrue(self.od.has_slot(key))

    def test_oversized_prefix_is_ignored(self):
        self.argv += ['-c', '1000']                          # 5000+ chars of system prompt > 60 % of 1000 tokens
        self.make()
        self.request(self.body('q'))
        self.assertEqual(self.od.index['keys'], {})
        self.assertFalse(any(c.startswith('save') for c in self.calls()))

    def test_no_precache_without_slot_arg(self):
        self.argv = self.argv[:-2]
        self.make()
        self.assertFalse(self.od.precache)


if __name__ == '__main__':
    unittest.main()
