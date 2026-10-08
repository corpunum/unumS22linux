#!/usr/bin/env python3
"""Host tests for tools/hardware/s22-keepalive.py (restart policy and mute guard)."""
import importlib.util
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


if __name__ == '__main__':
    unittest.main()
