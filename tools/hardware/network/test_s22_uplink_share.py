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
        self.probes, self.pauses, self.answers = [], [], []

        def data_probe(iface, target):
            self.probes.append((iface, target))
            return self.answers.pop(0) if len(self.answers) > 1 else (self.answers[0] if self.answers else True)
        m.data_probe = data_probe
        m.pause = self.pauses.append

    def tearDown(self):
        os.environ.pop('S22_SHARE_NO_WATCHDOG', None)
        self.tmp.cleanup()

    def set_counters(self, n):
        d = self.m.NET / 'rmnet0' / 'statistics'
        d.mkdir(parents=True, exist_ok=True)
        (d / 'rx_bytes').write_text(str(n // 2))
        (d / 'tx_bytes').write_text(str(n - n // 2))

    def rounds(self, *flags):
        """Script the probe: one entry per round (a bad round fails both targets); the last one repeats."""
        self.answers = [x for f in flags for x in ([True] if f else [False] * len(self.m.PROBE_TARGETS))]
        if not flags[-1]:
            self.answers = self.answers[:-len(self.m.PROBE_TARGETS)] + [False]
        else:
            self.answers = self.answers[:-1] + [True]

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


class DataCheckTest(Base):
    def assert_nothing_left(self):
        self.assertEqual(self.m.FORWARD.read_text().strip(), '0')
        self.assertFalse(self.m.STATE.exists())
        self.assertNotIn('mobile_mode', json.loads(self.m.NETPOLICY.read_text()), 'previous mode restored')
        self.assertFalse(any('rule' in c and 'add' in c or '-N' in c for c in self.cmds), 'no rule, no chain')
        self.assertEqual(self.chains, set())

    def test_on_refuses_when_no_round_is_ever_good_and_leaves_nothing(self):
        self.rounds(False)
        with self.assertRaises(RuntimeError) as cm:
            self.m.on()
        self.assertIn('mobile data carries no traffic (no data)', str(cm.exception))
        self.assertIn('check the SIM data allowance', str(cm.exception))
        self.assert_nothing_left()
        self.assertTrue(self.audit()[-1]['reason'].startswith('enable failed'))
        self.assertEqual(len(self.pauses), self.m.VERIFY_FIRST_TRIES - 1)

    def test_on_refuses_when_the_first_round_is_good_and_a_later_one_bad(self):
        self.rounds(True, False)
        with self.assertRaises(RuntimeError) as cm:
            self.m.on()
        self.assertIn('data stopped after 8 s', str(cm.exception))
        self.assert_nothing_left()

    def test_on_succeeds_after_verify_n_good_rounds_with_gaps(self):
        self.rounds(True)
        self.assertTrue(self.m.on()['on'])
        self.assertEqual(self.pauses, [self.m.VERIFY_GAP_S] * (self.m.VERIFY_N - 1))
        self.assertEqual(len(self.probes), self.m.VERIFY_N)

    def test_the_probe_is_bound_to_the_mobile_interface(self):
        self.m.on()
        self.assertTrue(self.probes)
        self.assertEqual({i for i, _ in self.probes}, {'rmnet0'})
        self.assertNotIn('wlan0', {i for i, _ in self.probes})
        self.assertTrue({t for _, t in self.probes} <= set(self.m.PROBE_TARGETS))

    def test_watch_turns_off_after_three_bad_rounds_and_not_after_two(self):
        self.m.on()
        self.rounds(False)
        for n in (1, 2):
            self.assertTrue(self.m.watch(once=True)['on'])
            self.assertEqual(self.m.status()['data_fails'], n)
        out = self.m.watch(once=True)
        self.assertEqual((out['on'], out['reason']), (False, 'no data'))
        self.assertEqual(self.m.FORWARD.read_text().strip(), '0')

    def test_a_good_round_resets_the_count(self):
        self.m.on()
        self.rounds(False)
        self.m.watch(once=True)
        self.m.watch(once=True)
        self.rounds(True)
        self.m.watch(once=True)
        self.assertEqual(self.m.status()['data_fails'], 0)
        self.rounds(False)
        self.assertTrue(self.m.watch(once=True)['on'], 'two more bad rounds are not three in a row')

    def test_status_reports_data_fails_and_last_check(self):
        s = self.m.status()
        self.assertEqual((s['data_fails'], s['data_checked']), (0, None))
        self.m.on()
        self.rounds(False)
        self.m.watch(once=True)
        s = self.m.status()
        self.assertEqual(s['data_fails'], 1)
        self.assertIsInstance(s['data_checked'], int)

    def test_the_skip_switch_skips_on_and_watch_and_is_audited(self):
        os.environ['S22_SHARE_NO_DATACHECK'] = '1'
        try:
            self.rounds(False)
            self.assertTrue(self.m.on()['on'])
            for _ in range(4):
                self.assertTrue(self.m.watch(once=True)['on'])
        finally:
            os.environ.pop('S22_SHARE_NO_DATACHECK', None)
        self.assertEqual(self.probes, [])
        self.assertEqual(self.audit()[0]['datacheck'], 'skipped')


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


class ParseTrace(unittest.TestCase):
    OK = b'HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\n\r\nfl=1\nh=1.1.1.1\nip=203.0.113.7\ncolo=ATH\nhttp=http/1.0\n'

    def setUp(self):
        self.pt = load().parse_trace

    def test_200_with_colo_and_ip(self):
        self.assertEqual(self.pt(self.OK), {'ip': '203.0.113.7', 'colo': 'ATH'})

    def test_http10_and_missing_ip(self):
        r = self.pt(b'HTTP/1.0 200 OK\r\n\r\ncolo=FRA\n')
        self.assertEqual(r, {'ip': '', 'colo': 'FRA'})

    def test_portal_redirect_is_none(self):
        raw = b'HTTP/1.1 302 Found\r\nLocation: http://203.0.113.9/topup\r\n\r\ncolo=ATH\n'
        self.assertIsNone(self.pt(raw))

    def test_200_without_colo_is_none(self):
        self.assertIsNone(self.pt(b'HTTP/1.1 200 OK\r\n\r\n<html>top up at 203.0.113.9</html>'))
        self.assertIsNone(self.pt(b'HTTP/1.1 200 OK\r\n\r\nip=203.0.113.7\ncolo=\n'))

    def test_truncated_or_garbage_is_none(self):
        for raw in (b'', b'HTTP/1.1', b'HTTP/1.1 200', b'\x00\xff\xfe garbage', b'SSH-2.0-x\r\n\r\ncolo=ATH'):
            self.assertIsNone(self.pt(raw))


if __name__ == '__main__':
    unittest.main(verbosity=1)
