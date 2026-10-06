#!/usr/bin/env python3
"""Host-only tests for s22-buttons.py (no device access)."""
import importlib.util, json, os, tempfile, unittest
from pathlib import Path
from unittest import mock

SPEC = importlib.util.spec_from_file_location('s22_buttons', Path(__file__).with_name('s22-buttons.py'))
MOD = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(MOD)


class ButtonTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory(); self.base = Path(self.td.name)
        self.b = MOD.Buttons(self.base, dry_run=True)

    def tearDown(self):
        self.td.cleanup()

    def events(self):
        return [json.loads(l) for l in (self.base / 'events.jsonl').read_text().splitlines()]

    def test_amp_raw_mapping(self):
        self.assertEqual(MOD.amp_raw(10), 817)
        self.assertEqual(MOD.amp_raw(9), 785)
        self.assertEqual(MOD.amp_raw(0), 0)
        self.assertEqual(MOD.amp_raw(99), 817)

    def test_short_volume_presses_step_and_persist(self):
        self.b.handle(115, 1, now=0.0); self.b.handle(115, 0, now=0.2)
        self.assertEqual(self.b.level, 8)
        self.b.handle(114, 1, now=1.0); self.b.handle(114, 0, now=1.1)
        self.b.handle(114, 1, now=2.0); self.b.handle(114, 0, now=2.1)
        self.assertEqual(self.b.level, 6)
        self.assertEqual((self.base / 'volume').read_text(), '6\n')
        amps = [a for a in self.b.actions if 'cset' in a]
        self.assertIn('name=Left Digital PCM Volume', amps[-1] + amps[-2])
        ev = self.events()
        self.assertEqual([e['action'] for e in ev][:2], ['press', 'release'])
        self.assertEqual(ev[1]['held_ms'], 200)

    def test_volume_clamps(self):
        for i in range(20):
            self.b.handle(114, 1, now=i); self.b.handle(114, 0, now=i + .1)
        self.assertEqual(self.b.level, 0)

    def test_long_volup_runs_assistant_hook(self):
        hooks = self.base / 'hooks'; hooks.mkdir()
        h = hooks / 'assistant'; h.write_text('#!/bin/sh\n'); h.chmod(0o755)
        self.b.handle(115, 1, now=0.0); self.b.handle(115, 0, now=1.5)
        self.assertEqual(self.b.actions[-1], (str(h),))
        self.assertEqual(self.b.level, 7)

    def test_power_without_hook_only_logs(self):
        self.b.handle(116, 1, now=0.0); self.b.handle(116, 0, now=0.1)
        self.assertEqual(self.b.actions, [])
        self.assertEqual(self.events()[-1]['key'], 'power')

    def test_unknown_codes_and_repeats_ignored(self):
        self.b.handle(30, 1); self.b.handle(115, 2)
        self.assertFalse((self.base / 'events.jsonl').exists())

    def test_find_nodes_by_name(self):
        with tempfile.TemporaryDirectory() as td:
            for ev, name in (('event0', 'gpio_keys'), ('event1', 'sec-pmic-key'), ('event7', 'sec_touchscreen')):
                d = Path(td, ev, 'device'); d.mkdir(parents=True); (d / 'name').write_text(name + '\n')
            self.assertEqual(MOD.find_nodes(td), ['/dev/input/event0', '/dev/input/event1'])


if __name__ == '__main__':
    unittest.main()
