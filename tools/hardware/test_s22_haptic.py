#!/usr/bin/env python3
"""Host-only tests for s22-haptic.py (fake motor, no device access)."""
import importlib.util, json, struct, tempfile, unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location('s22_haptic', Path(__file__).with_name('s22-haptic.py'))
MOD = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(MOD)


class FakeMotor:
    def __init__(self):
        self.calls = []

    def start(self, kind):
        self.calls.append(('start', kind))

    def stop(self, kind):
        self.calls.append(('stop', kind))


class HapticTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory(); self.dir = self.td.name
        self.now = [0.0]
        self.motor = FakeMotor()
        self.player = MOD.Player(self.motor, self.dir, clock=lambda: self.now[0],
                                 sleep=lambda s: None)

    def tearDown(self):
        self.td.cleanup()

    def write_cfg(self, data):
        Path(self.dir, 'config.json').write_text(json.dumps(data))

    def test_pack_effect_layout(self):
        eff = MOD.pack_effect(0, 39, 0x1122334455667788)
        self.assertEqual(len(eff), 48)
        self.assertEqual(struct.unpack_from('<Hh', eff, 0), (0x51, -1))
        self.assertEqual(struct.unpack_from('<H', eff, 16)[0], 0x5D)
        self.assertEqual(struct.unpack_from('<I', eff, 36)[0], 2)
        self.assertEqual(struct.unpack_from('<Q', eff, 40)[0], 0x1122334455667788)

    def test_missing_config_means_enabled(self):
        self.assertTrue(MOD.load_config(self.dir)['enabled'])

    def test_disabled_and_malformed_config_are_silent(self):
        self.write_cfg({'enabled': False})
        self.assertEqual(self.player.handle('tick'), 'dropped')
        self.assertEqual(self.player.handle('pattern 50,50,50'), 'dropped')
        self.assertEqual(self.motor.calls, [])
        Path(self.dir, 'config.json').write_text('{nope')
        self.assertFalse(MOD.load_config(self.dir)['enabled'])
        self.write_cfg({'enabled': 'yes'})
        self.assertFalse(MOD.load_config(self.dir)['enabled'])

    def test_config_hot_reload(self):
        self.write_cfg({'enabled': False})
        self.assertEqual(self.player.handle('tick'), 'dropped')
        self.write_cfg({'enabled': True, 'min_interval_ms': 1})
        self.now[0] += 1
        self.assertEqual(self.player.handle('tick'), 'ok')

    def test_rate_limit_min_interval(self):
        self.assertEqual(self.player.handle('tick'), 'ok')
        self.now[0] += 0.005
        self.assertEqual(self.player.handle('tick'), 'dropped')
        self.now[0] += 0.030
        self.assertEqual(self.player.handle('buzz'), 'ok')
        self.assertEqual(self.motor.calls, [('start', 'tick'), ('start', 'buzz')])

    def test_window_budget(self):
        self.write_cfg({'window_max_plays': 3, 'min_interval_ms': 1})
        results = []
        for _ in range(5):
            self.now[0] += 0.01
            results.append(self.player.handle('tick'))
        self.assertEqual(results, ['ok', 'ok', 'ok', 'dropped', 'dropped'])
        self.now[0] += 11
        self.assertEqual(self.player.handle('tick'), 'ok')

    def test_on_time_budget(self):
        self.write_cfg({'window_max_on_ms': 100, 'min_interval_ms': 1})
        out = []
        for _ in range(4):
            self.now[0] += 0.01
            out.append(self.player.handle('buzz'))   # 41 ms each
        self.assertEqual(out, ['ok', 'ok', 'dropped', 'dropped'])

    def test_plan_pattern_mapping_and_caps(self):
        cfg = dict(MOD.DEFAULTS)
        self.assertEqual(MOD.plan_pattern([10, 100, 40, 100, 200], cfg),
                         [('tick', 10, 100), ('buzz', 40, 100), ('long', 200, 0)])
        capped = MOD.plan_pattern([9999], cfg)
        self.assertEqual(capped, [('long', 500, 0)])
        many = MOD.plan_pattern([400, 100] * 20, cfg)
        self.assertLessEqual(sum(on + off for _, on, off in many), cfg['max_pattern_ms'])
        self.assertLessEqual(len(many), cfg['max_segments'])
        self.assertEqual(MOD.plan_pattern([0], cfg), [])

    def test_pattern_runs_and_stops_long(self):
        self.player.clock = lambda: self.now.__setitem__(0, self.now[0] + 0.05) or self.now[0]
        self.assertTrue(self.player.pattern([10, 0, 200, 0], blocking=True))
        self.assertEqual(self.motor.calls,
                         [('start', 'tick'), ('start', 'long'), ('stop', 'long')])

    def test_parse_pattern_and_handle_errors(self):
        self.assertEqual(MOD.parse_pattern('30, 50,30'), [30, 50, 30])
        with self.assertRaises(ValueError):
            MOD.parse_pattern('a,b')
        with self.assertRaises(ValueError):
            MOD.parse_pattern('-5')
        self.assertTrue(self.player.handle('pattern x').startswith('error'))
        self.assertTrue(self.player.handle('frob').startswith('error'))

    def test_status_counts(self):
        self.player.handle('tick')
        status = json.loads(self.player.handle('status'))
        self.assertEqual(status['played'], 1)
        self.assertTrue(status['enabled'])

    def test_find_event_node(self):
        with tempfile.TemporaryDirectory() as root:
            for n, name in ((3, 'other'), (6, MOD.DEVICE_NAME)):
                d = Path(root, f'event{n}/device'); d.mkdir(parents=True)
                (d / 'name').write_text(name + '\n')
            self.assertEqual(MOD.find_event_node(root), '/dev/input/event6')


if __name__ == '__main__':
    unittest.main()
