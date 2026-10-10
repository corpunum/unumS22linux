#!/usr/bin/env python3
"""Host tests for tools/hardware/s22-keepalive.py (restart policy and mute guard)."""
import importlib.util
import json
import tempfile
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path
from unittest import mock

SRC = Path(__file__).resolve().parent / 's22-keepalive.py'
spec = importlib.util.spec_from_loader('s22_keepalive', SourceFileLoader('s22_keepalive', str(SRC)))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class FakeService(mod.Service):
    def __init__(self, name):
        self.name = name
        self.is_running = True
        self.is_healthy = True
        self.starts = 0
        self.stops = 0

    def running(self, lines=None):
        return self.is_running

    def healthy(self):
        return self.is_healthy

    def start(self):
        self.starts += 1
        self.is_running = self.is_healthy = True
        return True, 'started'

    def stop(self):
        self.stops += 1


class PolicyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.svc = FakeService('openunum')
        cfg = dict(mod.DEFAULTS, services=['openunum'])
        self.k = mod.Keepalive(cfg, state=Path(self.tmp.name), services={'openunum': self.svc})
        self.k.mute_check = lambda reason: {}
        patcher = mock.patch.object(mod.time, 'sleep', lambda s: None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def test_missing_process_restarts_immediately(self):
        self.svc.is_running = self.svc.is_healthy = False
        with mock.patch.object(mod, 'ps_lines', return_value=[]):
            r = self.k.check_once(now=1000)
        self.assertEqual(r['openunum']['action'], 'restart')
        self.assertEqual(self.svc.starts, 1)
        self.assertEqual(self.svc.stops, 0)

    def test_hold_file_pauses_and_release_resumes(self):
        self.k.hold_dir = Path(self.tmp.name) / 'hold'
        self.k.hold_dir.mkdir()
        (self.k.hold_dir / 'openunum').touch()
        self.svc.is_running = self.svc.is_healthy = False
        with mock.patch.object(mod, 'ps_lines', return_value=[]):
            r = self.k.check_once(now=1000)
            self.assertEqual(r['openunum']['action'], 'held')
            self.assertEqual(self.svc.starts, 0)
            (self.k.hold_dir / 'openunum').unlink()
            r = self.k.check_once(now=1020)
        self.assertEqual(r['openunum']['action'], 'restart')
        self.assertEqual(self.svc.starts, 1)

    def test_slow_but_running_waits_then_restarts(self):
        self.svc.is_healthy = False
        with mock.patch.object(mod, 'ps_lines', return_value=[]):
            self.assertEqual(self.k.check_once(now=1000)['openunum']['action'], 'wait')
            self.assertEqual(self.k.check_once(now=1100)['openunum']['action'], 'wait')
            self.assertEqual(self.k.check_once(now=1181)['openunum']['action'], 'restart')
        self.assertEqual((self.svc.stops, self.svc.starts), (1, 1))

    def test_backoff_and_limit(self):
        with mock.patch.object(mod, 'ps_lines', return_value=[]):
            t = 1000
            actions = []
            for _ in range(12):
                self.svc.is_running = self.svc.is_healthy = False
                actions.append(self.k.check_once(now=t)['openunum']['action'])
                t += 700                       # past every backoff step
        self.assertEqual(actions.count('restart'), self.svc.starts)
        # within any 1800 s window there are at most max_restarts restarts
        self.assertLessEqual(self.svc.starts, 12)
        k = self.k
        k.history['openunum'] = [5000 + i for i in range(5)]
        k.next_try['openunum'] = 0
        k.bad_since['openunum'] = 0
        self.assertEqual(k.decide('openunum', False, False, 5100), 'limited')
        self.assertEqual(k.decide('openunum', False, False, 7000), 'restart')

    def test_backoff_blocks_rapid_retry(self):
        self.svc.start = lambda: (False, 'boom')
        with mock.patch.object(mod, 'ps_lines', return_value=[]):
            self.svc.is_running = self.svc.is_healthy = False
            self.assertEqual(self.k.check_once(now=1000)['openunum']['action'], 'restart')
            self.assertEqual(self.k.check_once(now=1010)['openunum']['action'], 'wait')
            self.assertEqual(self.k.check_once(now=1031)['openunum']['action'], 'restart')
            self.assertEqual(self.k.check_once(now=1060)['openunum']['action'], 'wait')   # backoff 60 s now
            self.assertEqual(self.k.check_once(now=1092)['openunum']['action'], 'restart')

    def test_healthy_resets(self):
        with mock.patch.object(mod, 'ps_lines', return_value=[]):
            self.assertEqual(self.k.check_once(now=1000)['openunum']['action'], 'ok')
        self.assertEqual(self.svc.starts, 0)


class MuteTest(unittest.TestCase):
    def test_parse_cget(self):
        out = "numid=5,iface=MIXER,name='Left Digital PCM Volume'\n  ; type=INTEGER\n  : values=817\n"
        self.assertEqual(mod.parse_cget(out), 817)
        self.assertIsNone(mod.parse_cget('garbage'))

    def test_needs_mute(self):
        self.assertFalse(mod.needs_mute({'level': 0, 'amp': [0, 0]}))
        self.assertTrue(mod.needs_mute({'level': 0, 'amp': [817, 0]}))   # restart reset the amp
        self.assertTrue(mod.needs_mute({'level': 3, 'amp': [0, 0]}))
        self.assertFalse(mod.needs_mute({'level': None, 'amp': None}))

    def test_apply_mute_writes_level_and_amps(self):
        calls = []
        with tempfile.TemporaryDirectory() as d, mock.patch.object(mod.Path, 'exists', return_value=True):
            (Path(d) / 'volume').write_text('5\n')
            mod.apply_mute(Path(d), runner=lambda args: calls.append(args))
            self.assertEqual((Path(d) / 'volume').read_text(), '0\n')
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(c[-1] == '0' and c[1] == 'cset' for c in calls))

    def test_pids_matching_ignores_self_and_grep(self):
        lines = ['  PID COMMAND', ' 10 node src/server.mjs', ' 11 grep node src/server.mjs',
                 ' 12 python3 /srv/s22/hardware/bin/s22-keepalive node src/server.mjs']
        self.assertEqual(mod.pids_matching('node src/server.mjs', lines), [10])


class ServiceTableTest(unittest.TestCase):
    def test_default_services_and_optional_phoned(self):
        with tempfile.TemporaryDirectory() as d:
            phoned = Path(d) / 's22-phoned'
            defs = {'phoned': dict(mod.SERVICE_DEFS['phoned'], requires=str(phoned))}
            cfg = dict(mod.DEFAULTS, services=['openunum', 'unumsearch', 'phoned', 'nope'],
                       service_defs=defs)
            svcs, skipped = mod.build_services(cfg)
            self.assertEqual(list(svcs), ['openunum', 'unumsearch'])
            self.assertIn('missing', skipped['phoned'])
            self.assertEqual(skipped['nope'], 'unknown service')
            phoned.write_text('#!/bin/sh\n')
            svcs, skipped = mod.build_services(cfg)
            self.assertEqual(list(svcs), ['openunum', 'unumsearch', 'phoned'])
            self.assertEqual(svcs['phoned'].argv, ['/srv/s22/hardware/bin/s22-phoned', 'serve'])
            self.assertEqual(svcs['phoned'].needle, 's22-phoned serve')
        # phoned is only managed when named in "services"
        svcs, _ = mod.build_services(dict(mod.DEFAULTS))
        self.assertEqual(list(svcs), ['openunum', 'unumsearch'])

    def test_builtin_phoned_definition(self):
        d = mod.SERVICE_DEFS['phoned']
        self.assertEqual(d['health'], {'http': 'http://127.0.0.1:8095/status'})
        self.assertEqual(d['requires'], '/srv/s22/hardware/bin/s22-phoned')

    def test_config_only_service(self):
        cfg = dict(mod.DEFAULTS, services=['llama'], service_defs={'llama': {
            'argv': ['/srv/s22/llama/llama-server', '--port', 8090], 'needle': 'llama-server --port 8090',
            'health': {'http': 'http://127.0.0.1:8090/health'}, 'start_wait_s': 120, 'unhealthy_s': 600}})
        svcs, skipped = mod.build_services(cfg)
        self.assertEqual(skipped, {})
        llama = svcs['llama']
        self.assertEqual(llama.argv[-1], '8090')
        with mock.patch.object(mod, 'http_ok', return_value=True) as h:
            self.assertTrue(llama.healthy())
        h.assert_called_once_with('http://127.0.0.1:8090/health', timeout=10.0)
        bad, skipped = mod.build_services(dict(mod.DEFAULTS, services=['x'], service_defs={'x': {}}))
        self.assertEqual(bad, {})
        self.assertIn('bad definition', skipped['x'])

    def test_unumsearch_row_keeps_old_behaviour(self):
        svc = mod.Generic('unumsearch', mod.SERVICE_DEFS['unumsearch'])
        with mock.patch.object(mod, 'tcp_ok', return_value=False) as t:
            self.assertFalse(svc.healthy())
        t.assert_called_once_with('127.0.0.1', 7781)

    def test_generic_start_waits_for_health(self):
        with tempfile.TemporaryDirectory() as d:
            svc = mod.Generic('t', {'argv': ['/bin/sleep', '5'], 'log': f'{d}/t.log',
                                    'health': {'tcp': ['127.0.0.1', 1]}, 'start_wait_s': 0})
            ok, detail = svc.start()
            self.assertFalse(ok)
            self.assertIn('not healthy', detail)
            svc = mod.Generic('t', {'argv': ['/bin/true'], 'log': f'{d}/t.log'})
            self.assertTrue(svc.start()[0])           # no probe: started is enough

    def test_file_health_probe_for_modem_state(self):
        with tempfile.TemporaryDirectory() as d:
            state = Path(d) / 'modem_state'
            svc = mod.Generic('m', {'argv': ['x'], 'health': {'file': str(state), 'equals': 'ONLINE'}})
            self.assertFalse(svc.healthy())                 # missing file
            state.write_text('INIT\n')
            self.assertFalse(svc.healthy())
            state.write_text('ONLINE\n')
            self.assertTrue(svc.healthy())
        modem = mod.SERVICE_DEFS['modem']
        self.assertEqual(modem['health']['equals'], 'ONLINE')
        self.assertEqual(modem['needle'], 'vendor/bin/cbd')

    def test_per_service_unhealthy_window(self):
        with tempfile.TemporaryDirectory() as d:
            slow = mod.Generic('llama', {'argv': ['x'], 'unhealthy_s': 600})
            k = mod.Keepalive(dict(mod.DEFAULTS), state=Path(d), services={'llama': slow})
            self.assertEqual(k.decide('llama', True, False, 1000), 'wait')
            self.assertEqual(k.decide('llama', True, False, 1500), 'wait')    # global 180 s passed
            self.assertEqual(k.decide('llama', True, False, 1601), 'restart')


class ModelPinTest(unittest.TestCase):
    PIN = {'provider': 'openai', 'model': 'openai/gpt-6-luna'}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.conf = self.dir / 'openunum.json'
        self.conf.write_text(json.dumps({'server': {'port': 18880}, 'model': {
            'provider': 'llama-cpp-local', 'model': 'llama-cpp-local/openai/qwen3.8-flash-next-uncensored-lean',
            'providerModels': {'llama-cpp-local': 'llama-cpp-local/openai/qwen3.8-flash-next-uncensored-lean'},
            'routing': {'fallbackEnabled': True, 'fallbackProviders': ['ollama-local', 'llama-cpp-local']}}}))

    def tearDown(self):
        self.tmp.cleanup()

    def test_file_restore_keeps_everything_else(self):
        self.assertEqual(mod.pin_config_file(self.conf, self.PIN), 'restored')
        doc = json.loads(self.conf.read_text())
        self.assertEqual((doc['model']['provider'], doc['model']['model']), ('openai', 'openai/gpt-6-luna'))
        self.assertEqual(doc['model']['providerModels']['openai'], 'openai/gpt-6-luna')
        self.assertIn('llama-cpp-local', doc['model']['providerModels'])
        self.assertEqual(doc['server'], {'port': 18880})
        self.assertEqual(mod.pin_config_file(self.conf, self.PIN), 'ok')
        self.assertTrue(mod.pin_config_file(self.dir / 'missing.json', self.PIN).startswith('error'))

    def test_pinned_copy_wins_and_carries_routing(self):
        pinned = self.dir / 'pinned.json'
        cfg = dict(mod.DEFAULTS, pin_source=str(pinned))
        self.assertEqual(mod.load_pin(cfg), mod.DEFAULTS['pin'])
        pinned.write_text(json.dumps({'model': {'provider': 'openai', 'model': 'openai/gpt-6-luna',
                                                'routing': {'fallbackEnabled': False}}}))
        pin = mod.load_pin(cfg)
        self.assertEqual(pin['routing'], {'fallbackEnabled': False})
        self.assertEqual(mod.pin_config_file(self.conf, pin), 'restored')
        routing = json.loads(self.conf.read_text())['model']['routing']
        self.assertEqual(routing['fallbackEnabled'], False)
        self.assertEqual(routing['fallbackProviders'], ['ollama-local', 'llama-cpp-local'])

    def keepalive(self, api, **cfg):
        svc = FakeService('openunum')
        k = mod.Keepalive(dict(mod.DEFAULTS, pin_model=True, pin_source=str(self.dir / 'none'), **cfg),
                          state=self.dir, services={'openunum': svc}, api=api, config_path=self.conf)
        k.mute_check = lambda reason: {}
        return k, svc

    def test_running_server_is_fixed_through_the_api_not_the_file(self):
        calls = []
        current = {'provider': 'llama-cpp-local', 'model': 'llama-cpp-local/qwen'}

        def api(method, path, body=None):
            calls.append((method, path, body))
            if method == 'POST':
                current.update(provider=body['provider'], model=body['model'])
            return dict(current)
        k, _ = self.keepalive(api)
        before = self.conf.read_text()
        with mock.patch.object(mod, 'ps_lines', return_value=[]):
            r = k.check_once(now=1000)
        self.assertEqual(r['model_pin'], 'restored')
        self.assertEqual(calls[1], ('POST', '/api/model/switch',
                                    {'provider': 'openai', 'model': 'openai/gpt-6-luna'}))
        self.assertEqual(self.conf.read_text(), before)          # file untouched while running
        with mock.patch.object(mod, 'ps_lines', return_value=[]):
            self.assertEqual(k.check_once(now=1020)['model_pin'], 'ok')
        log = (self.dir / 'keepalive.jsonl').read_text()
        self.assertIn('"via": "api"', log)

    def test_owner_choice_of_local_model_is_kept(self):
        calls = []
        current = {'provider': 'llama-cpp-local', 'model': 'llama-cpp-local//models/Qwen3.5-0.8B-Q4_0.gguf'}

        def api(method, path, body=None):
            calls.append((method, path, body))
            return dict(current)
        k, _ = self.keepalive(api)
        with mock.patch.object(mod, 'ps_lines', return_value=[]):
            self.assertEqual(k.check_once(now=1000)['model_pin'], 'owner_choice')
        self.assertFalse([c for c in calls if c[0] == 'POST'])      # no switch back to Luna
        allow = mod.DEFAULTS['pin_allow']
        self.conf.write_text(json.dumps({'model': dict(current)}))
        self.assertEqual(mod.pin_config_file(self.conf, self.PIN, allow), 'ok')
        self.assertEqual(json.loads(self.conf.read_text())['model']['provider'], 'llama-cpp-local')

    def test_failed_switch_backs_off(self):
        k, _ = self.keepalive(lambda m, p, b=None: {'ok': False, 'reason': 'provider_disabled'}
                              if m == 'POST' else {'provider': 'x', 'model': 'y'})
        with mock.patch.object(mod, 'ps_lines', return_value=[]):
            self.assertEqual(k.check_once(now=1000)['model_pin'], 'failed')
            self.assertEqual(k.check_once(now=1100)['model_pin'], 'wait')
            self.assertEqual(k.check_once(now=1601)['model_pin'], 'failed')

    def test_file_pinned_before_restart_only_when_stopped(self):
        k, svc = self.keepalive(lambda *a, **kw: {})
        svc.is_running = svc.is_healthy = False
        with mock.patch.object(mod, 'ps_lines', return_value=[]), \
             mock.patch.object(mod.time, 'sleep', lambda s: None):
            r = k.check_once(now=1000)
        self.assertEqual(r['openunum']['action'], 'restart')
        self.assertEqual(json.loads(self.conf.read_text())['model']['provider'], 'openai')
        # still running after stop -> the file is left alone
        self.conf.write_text(json.dumps({'model': {'provider': 'llama-cpp-local', 'model': 'm'}}))
        svc.running = lambda lines=None: True
        with mock.patch.object(mod.time, 'sleep', lambda s: None):
            self.assertEqual(k.pin_file_while_stopped(svc), 'skipped')
        self.assertEqual(json.loads(self.conf.read_text())['model']['provider'], 'llama-cpp-local')

    def test_pin_off_by_default(self):
        k = mod.Keepalive(dict(mod.DEFAULTS), state=self.dir, services={'openunum': FakeService('openunum')},
                          api=lambda *a, **kw: self.fail('api called'))
        k.mute_check = lambda reason: {}
        with mock.patch.object(mod, 'ps_lines', return_value=[]):
            self.assertNotIn('model_pin', k.check_once(now=1000))


if __name__ == '__main__':
    unittest.main()
