#!/usr/bin/env python3
"""Host-only tests for s22-charge-limit.py (fake power-supply directory; no phone)."""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location('s22_charge_limit', HERE / 's22-charge-limit.py')
C = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(C)


class ChargeLimit(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        d = Path(self.td.name)
        self.psy = d / 'battery'
        self.psy.mkdir()
        (self.psy / C.ATTR).write_text('100\n')
        (self.psy / 'capacity').write_text('97\n')
        (self.psy / 'status').write_text('Charging\n')
        self.cfg = d / 'state' / 'charge-limit.json'

    def tearDown(self):
        self.td.cleanup()

    def test_apply_writes_only_when_different_and_reads_back(self):
        r = C.apply(85, self.psy)
        self.assertEqual((r['ok'], r['changed'], r['kernel'], r['before']), (True, True, 85, 100))
        self.assertEqual((self.psy / C.ATTR).read_text(), '85\n')
        self.assertEqual(C.apply(85, self.psy), {'ok': True, 'changed': False, 'kernel': 85})
        self.assertEqual(C.apply(0, self.psy)['kernel'], 0)          # off: the kernel's boot default
        C.apply(85, self.psy)
        for bad in (49, 101, -1, 1, 'x'):
            with self.assertRaises(ValueError):
                C.apply(bad, self.psy)
        self.assertEqual((self.psy / C.ATTR).read_text(), '85\n')      # nothing written for a bad value

    def test_missing_attribute_is_reported_not_created(self):
        (self.psy / C.ATTR).unlink()
        r = C.apply(85, self.psy)
        self.assertFalse(r['ok'])
        self.assertFalse((self.psy / C.ATTR).exists())

    def test_run_writes_nothing_without_a_configuration(self):
        logs = []
        C.run(psy=self.psy, config=self.cfg, once=True, log=lambda *a, **k: logs.append(a))
        self.assertEqual((self.psy / C.ATTR).read_text(), '100\n')
        self.assertEqual(logs, [])

    def test_run_reapplies_after_a_reboot_reset(self):
        C.save(85, self.cfg)
        self.assertEqual(json.loads(self.cfg.read_text())['limit'], 85)
        logs = []
        C.run(psy=self.psy, config=self.cfg, once=True, log=lambda *a, **k: logs.append(a))
        self.assertEqual((self.psy / C.ATTR).read_text(), '85\n')
        (self.psy / C.ATTR).write_text('100\n')                   # what a reboot does
        C.run(psy=self.psy, config=self.cfg, once=True, log=lambda *a, **k: logs.append(a))
        self.assertEqual((self.psy / C.ATTR).read_text(), '85\n')
        self.assertEqual(len(logs), 2)

    def test_status_and_kernel_value_with_an_event_suffix(self):
        (self.psy / C.ATTR).write_text('85 HIGHSOC\n')               # the driver can print an event name
        self.assertEqual(C.kernel_limit(self.psy), 85)
        s = C.status(self.psy, self.cfg)
        self.assertEqual((s['kernel'], s['active'], s['capacity'], s['status'], s['configured'], s['supported']),
                         (85, True, '97', 'Charging', None, True))
        (self.psy / C.ATTR).write_text('0\n')
        self.assertFalse(C.status(self.psy, self.cfg)['active'])


if __name__ == '__main__':
    unittest.main()
