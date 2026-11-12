#!/usr/bin/env python3
"""Hardware-free tests for s22-uplink-share: only the rig is forwarded, the guards, and that forwarding is
always off again. Run: python3 tools/hardware/network/test_s22_uplink_share.py"""
import importlib.machinery
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent


def load():
    loader = importlib.machinery.SourceFileLoader('s22_uplink_share', str(HERE / 's22-uplink-share.py'))
    spec = importlib.util.spec_from_loader('s22_uplink_share', loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class Base(unittest.TestCase):
    RIG = '192.168.77.20'

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.m = m = load()
        m.CONF, m.STATE, m.USAGE, m.AUDIT = root / 'conf.json', root / 'st/state.json', root / 'st/usage.json', root / 'audit.jsonl'
        m.NETPOLICY, m.NP_STATUS, m.BATTERY, m.NET, m.FORWARD = (root / 'np.json', root / 'np-status.json',
                                                                 root / 'cap', root / 'net', root / 'ip_forward')
        m.CONF.write_text(json.dumps({'rig': self.RIG}))
        m.NETPOLICY.write_text(json.dumps({'wifi_saver': True}))
        m.NP_STATUS.write_text(json.dumps({'links': {'mobile': {'state': 'up', 'ipv4': '10.1.2.3'}}}))
        m.BATTERY.write_text('80\n')
        m.FORWARD.write_text('0\n')
        self.set_counters(1000)
        os.environ['S22_SHARE_NO_WATCHDOG'] = '1'
        m.UP_WAIT_S = 0.2
        self.cmds, self.fail_on = [], None
        self.chains = set()

        def run(argv, check=True):
            self.cmds.append(argv)
            joined = ' '.join(argv)
            rc = 0
            if self.fail_on and self.fail_on in joined:
                rc = 1
            if '-N' in argv:
                self.chains.add(argv[-1])
            if '-X' in argv:
                self.chains.discard(argv[-1])
            if '-S' in argv and argv[-1] not in self.chains:
                rc = 1

            class R:
                returncode, stdout, stderr = rc, '', 'boom' if rc else ''
            if check and rc:
                raise RuntimeError(f'{joined}: rc=1 boom')
            return R()
        m.run = run

    def tearDown(self):
        os.environ.pop('S22_SHARE_NO_WATCHDOG', None)
        self.tmp.cleanup()

    def set_counters(self, n):
        d = self.m.NET / 'rmnet0' / 'statistics'
        d.mkdir(parents=True, exist_ok=True)
        (d / 'rx_bytes').write_text(str(n // 2))
        (d / 'tx_bytes').write_text(str(n - n // 2))

    def audit(self):
        return [json.loads(l) for l in self.m.AUDIT.read_text().splitlines()] if self.m.AUDIT.exists() else []


class OnOffTest(Base):
    def test_on_forwards_only_the_rig_and_off_undoes_everything(self):
        r = self.m.on()
        self.assertTrue(r['on'] and r['forwarding'])
        flat = [' '.join(c) for c in self.cmds]
        self.assertIn(f'ip rule add from {self.RIG} lookup 177 priority 1177', flat)
        rules = [c for c in flat if '-A S22SHARE ' in c]
        self.assertTrue(all(self.RIG in c for c in rules[:-1]), rules)
        self.assertTrue(rules[-1].endswith('-A S22SHARE -j DROP'), 'everything else is dropped, on any interface')
        self.assertTrue(any(c.endswith(f'-s {self.RIG} -o rmnet+ -j MASQUERADE') for c in flat))
        self.assertEqual(json.loads(self.m.NETPOLICY.read_text())['mobile_mode'], 'always')
        self.assertEqual(self.m.FORWARD.read_text().strip(), '1')
        self.set_counters(1000 + 5000)
        out = self.m.off()
        self.assertEqual((out['ok'], out['on'], out['left']), (True, False, []))
        self.assertEqual(self.m.FORWARD.read_text().strip(), '0')
        self.assertNotIn('mobile_mode', json.loads(self.m.NETPOLICY.read_text()), 'previous (absent) mode restored')
        self.assertEqual(json.loads(self.m.NETPOLICY.read_text())['wifi_saver'], True)
        self.assertEqual(self.m.used_today(), 5000, 'session bytes in the ledger')
        self.assertEqual([a['event'] for a in self.audit()], ['enable', 'disable'])
        self.assertFalse(self.m.STATE.exists())

    def test_a_failed_enable_leaves_forwarding_off(self):
        self.fail_on = 'MASQUERADE'
        with self.assertRaises(RuntimeError):
            self.m.on()
        self.assertEqual(self.m.FORWARD.read_text().strip(), '0')
        self.assertNotIn('mobile_mode', json.loads(self.m.NETPOLICY.read_text()))
        self.assertFalse(self.m.STATE.exists())
        self.assertTrue(self.audit()[-1]['reason'].startswith('enable failed'))

    def test_mobile_data_that_never_comes_up_aborts(self):
        self.m.NP_STATUS.write_text(json.dumps({'links': {'mobile': {'state': 'down'}}}))
        with self.assertRaises(RuntimeError) as cm:
            self.m.on()
        self.assertIn('mobile data', str(cm.exception))
        self.assertEqual(self.m.FORWARD.read_text().strip(), '0')

    def test_refuses_without_a_private_rig_address(self):
        for bad in ({}, {'rig': '8.8.8.8'}, {'rig': 'not-an-ip'}):
            self.m.CONF.write_text(json.dumps(bad))
            with self.assertRaises(RuntimeError):
                self.m.on()
        self.assertFalse(any('-N' in c for c in self.cmds))


class GuardTest(Base):
    def test_battery_at_40_refuses_and_stops(self):
        self.m.BATTERY.write_text('40\n')
        with self.assertRaises(RuntimeError):
            self.m.on()
        self.m.BATTERY.write_text('60\n')
        self.m.on()
        self.m.BATTERY.write_text('40\n')
        out = self.m.watch(once=True)
        self.assertEqual((out['on'], out['reason']), (False, 'battery 40 %'))
        self.assertEqual(self.m.FORWARD.read_text().strip(), '0')

    def test_daily_cap_and_six_hours(self):
        self.m.on()
        self.set_counters(1000 + self.m.CAP_BYTES)
        self.assertEqual(self.m.watch(once=True)['reason'], 'daily cap')
        with self.assertRaises(RuntimeError):
            self.m.on()                                   # cap used up today
        self.m.USAGE.write_text(json.dumps({'date': '2000-01-01', 'bytes': 99 ** 9}))
        self.m.on()                                       # a new day
        t0 = self.m.now()
        self.m.now = lambda: t0 + self.m.MAX_S + 1
        self.assertEqual(self.m.watch(once=True)['reason'], 'six hours')

    def test_sweep_undoes_a_reboot_left_state(self):
        self.m.on()
        self.m.FORWARD.write_text('0\n')                  # after a reboot the kernel state is gone
        out = self.m.sweep()
        self.assertTrue(out['swept'])
        self.assertNotIn('mobile_mode', json.loads(self.m.NETPOLICY.read_text()))
        self.assertFalse(self.m.STATE.exists())
        self.assertEqual(self.audit()[-1]['reason'], 'sweep')
        self.assertFalse(self.m.sweep()['swept'], 'nothing left')


if __name__ == '__main__':
    unittest.main(verbosity=1)
