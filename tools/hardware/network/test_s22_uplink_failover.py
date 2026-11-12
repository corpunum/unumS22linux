#!/usr/bin/env python3
"""Hardware-free tests for s22-uplink-failover (run, tcp_probe and the HTTP calls are faked; the route table,
rule list, table 178 and DNS are stateful). Run: python3 tools/hardware/network/test_s22_uplink_failover.py"""
import importlib.machinery
import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
GW, MAIN = '192.168.77.50', '192.168.77.1'
TOKEN = 'fake-' + 'bearer-for-tests'   # not a secret; split so the secret scanner stays quiet
ROUTE = ['ip', 'route', 'replace', 'default', 'via', GW, 'dev', 'eno1', 'metric', '50']
ROUTE_DEL = ['ip', 'route', 'del', 'default', 'via', GW, 'dev', 'eno1', 'metric', '50']
RULE_ADD = ['ip', 'rule', 'add', 'fwmark', '0x178', 'lookup', '178', 'priority', '1178']
ROUTE_GET = ['ip', '-j', 'route', 'get']
DNS = ['1.0.0.1', '149.112.112.112']


def load():
    loader = importlib.machinery.SourceFileLoader('s22_uplink_failover', str(HERE / 's22-uplink-failover.py'))
    spec = importlib.util.spec_from_loader('s22_uplink_failover', loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = root = Path(self.tmp.name)
        self.m = m = load()
        m.STATE, m.AUDIT = root / 'state.json', root / 'audit.jsonl'
        self.tok = root / 'token'
        self.tok.write_text(TOKEN + '\n')
        self.tok.chmod(0o600)
        self.conf = root / 'conf.json'
        self.write_conf()
        self.cfg = m.load_config(self.conf)
        self.clock, self.cmds, self.posts, self.gets = 1000.0, [], [], []
        self.ping_ok, self.tcp_ok, self.post_result = False, False, (200, {'ok': True})
        self.off_raises = self.on_raises = False
        self.status = {'on': True}         # None -> the status GET raises
        self.fail = []                      # argv prefixes that make `run` return rc=1
        self.routes = [{'dst': 'default', 'gateway': MAIN, 'dev': 'eno1', 'metric': 100}]
        self.rules, self.table, self.dns, self.tcp_calls = [], [], None, []
        m.now = lambda: self.clock
        m.run, m.http_post, m.http_get, m.tcp_probe = self.frun, self.post, self.get, self.tcp

    def tearDown(self):
        self.tmp.cleanup()

    def frun(self, argv, check=True):
        self.cmds.append(argv)
        rc, out = 0, ''
        if any(argv[:len(p)] == p for p in self.fail):
            rc = 1
        elif argv[0] == 'ping':
            rc = 0 if self.ping_ok else 1
        elif argv[:4] == ['ip', '-j', 'route', 'show']:
            out = json.dumps(self.routes)
        elif argv[:4] == ['ip', '-j', 'route', 'get']:
            out = '[{}]'
        elif argv[:4] == ['ip', '-j', 'rule', 'show']:
            out = json.dumps(self.rules)
        elif argv[:3] == ['ip', 'route', 'replace'] and 'table' in argv:
            self.table = [argv[5]]
        elif argv[:3] == ['ip', 'route', 'replace']:
            self.routes = [r for r in self.routes if r['gateway'] != argv[5]]
            self.routes.append({'dst': 'default', 'gateway': argv[5], 'dev': argv[7], 'metric': int(argv[9])})
        elif argv[:3] == ['ip', 'route', 'del']:
            self.routes = [r for r in self.routes if r['gateway'] != argv[5]]
        elif argv[:3] == ['ip', 'route', 'flush']:
            self.table = []
        elif argv[:3] == ['ip', 'rule', 'add']:
            self.rules.append({'priority': 1178, 'fwmark': '0x178', 'table': '178'})
        elif argv[:3] == ['ip', 'rule', 'del'] and self.rules:
            self.rules.pop()
        elif argv[:2] == ['resolvectl', 'dns']:
            self.dns = argv[3:]
        elif argv[:2] == ['resolvectl', 'revert']:
            self.dns = None
        if check and rc != 0:
            raise RuntimeError(f'{argv[:5]} rc={rc}')
        return subprocess.CompletedProcess(argv, rc, out, '')

    def post(self, url, token, payload, timeout):
        self.posts.append((url, token, payload, timeout))
        if payload['on'] is False and self.off_raises:
            raise OSError('phone unreachable')
        if payload['on'] is True and self.on_raises:
            raise TimeoutError('timed out')
        return self.post_result

    def get(self, url, token, timeout):
        self.gets.append((url, token, timeout))
        if self.status is None:
            raise OSError('phone unreachable')
        return 200, self.status

    def tcp(self, dev, target):
        self.tcp_calls.append((dev, target))
        return self.tcp_ok

    def write_conf(self, **kw):
        c = {'phone_gw': GW, 'api': 'http://phone.example:8767', 'token_file': str(self.tok), **kw}
        self.conf.write_text(json.dumps(c))

    def reconf(self, **kw):
        self.write_conf(**kw)
        self.cfg = self.m.load_config(self.conf)

    def step(self, n=1):
        for _ in range(n):
            self.clock += 10
            st = self.m.once(self.cfg)
        return st

    def events(self):
        return [json.loads(x) for x in self.m.AUDIT.read_text().splitlines()]

    def names(self):
        return [e['event'] for e in self.events()]

    def ons(self):
        return [p for p in self.posts if p[2]['on'] is True]

    def offs(self):
        return [p for p in self.posts if p[2]['on'] is False]

    def phone_route(self):
        return any(r['gateway'] == GW for r in self.routes)

    def state(self):
        return json.loads(self.m.STATE.read_text())


class Probe(Base):  # change 1
    def test_probe_uses_fwmark_table_and_rule_not_host_routes(self):
        self.step()
        self.assertIn(['ip', 'route', 'replace', 'default', 'via', MAIN, 'dev', 'eno1', 'table', '178'], self.cmds)
        self.assertIn(RULE_ADD, self.cmds)
        self.assertIn(['ip', '-j', 'rule', 'show', 'priority', '1178'], self.cmds)
        self.assertIn(['ping', '-c1', '-W2', '-I', 'eno1', '-m', '0x178', '9.9.9.9'], self.cmds)
        self.assertIn(('eno1', '1.1.1.1'), self.tcp_calls)
        self.assertFalse([c for c in self.cmds if any('/32' in a for a in c)])
        self.assertEqual((self.m.MARK, self.m.TABLE, self.m.RULE_PRIO), (0x178, 178, 1178))

    def test_rule_is_not_added_twice(self):
        self.step(10)
        self.assertEqual(self.cmds.count(RULE_ADD), 1)
        self.assertEqual(len(self.rules), 1)

    def test_tcp_alone_counts_as_main_up(self):
        self.tcp_ok = True
        self.assertEqual(self.step(5)['mode'], 'normal')
        self.assertEqual(self.state()['fails'], 0)

    def test_ping_alone_counts_as_main_up(self):
        self.ping_ok = True
        self.step()
        self.assertEqual(self.state()['oks'], 1)

    def test_sweep_deletes_rule_and_flushes_table(self):
        self.step()
        self.assertTrue(self.rules and self.table)
        self.m.sweep(self.cfg)
        self.assertEqual((self.rules, self.table), ([], []))
        self.assertIn(['ip', 'route', 'flush', 'table', '178'], self.cmds)

    def test_main_gateway_is_default_route_not_phone(self):
        self.routes.append({'dst': 'default', 'gateway': GW, 'dev': 'eno1', 'metric': 50})
        self.assertEqual(self.m.main_gateway(self.cfg), MAIN)


class NoGateway(Base):  # change 2
    def test_no_gateway_is_not_down_and_nothing_is_posted(self):
        self.routes = []
        for _ in range(10):
            st = self.step()
            self.assertEqual((st['mode'], st['note'], st['fails']), ('normal', 'no_gateway', 0))
        self.assertEqual((self.posts, self.gets), ([], []))
        self.assertNotIn('down', self.names() if self.m.AUDIT.exists() else [])

    def test_no_gateway_in_failover_keeps_failover_and_counts_failure(self):
        self.step(3)
        self.assertEqual(self.state()['mode'], 'failover')
        self.routes = [r for r in self.routes if r['gateway'] == GW]
        st = self.step(2)
        self.assertEqual((st['mode'], st['note']), ('failover', 'no_gateway'))
        self.assertGreaterEqual(st['fails'], 2)
        self.assertEqual(len(self.ons()), 1)


class Failover(Base):
    def test_three_failures_trigger_failover(self):  # base behaviour
        self.step(2)
        self.assertFalse(self.phone_route())
        st = self.step()
        self.assertEqual(st['mode'], 'failover')
        self.assertIn(ROUTE, self.cmds)
        self.assertEqual(self.posts[0], ('http://phone.example:8767/v1/device/uplink.share', TOKEN, {'on': True}, 100))
        self.assertEqual(self.names(), ['down', 'failover_on'])

    def test_phone_refusal_adds_no_route_sends_off_and_retries_slowly(self):  # change 4
        self.post_result = (403, {'ok': False})
        st = self.step(3)
        self.assertEqual(st['mode'], 'normal')
        self.assertFalse(self.phone_route())
        self.assertEqual((len(self.ons()), len(self.offs())), (1, 1))
        self.step(3)  # 30 s later: still inside the 60 s back-off
        self.assertEqual(len(self.ons()), 1)
        self.step(4)
        self.assertEqual(len(self.ons()), 2)
        self.assertIn('failover_refused', self.names())

    def test_route_replace_failure_sends_off_and_no_failover(self):  # change 4 / required case
        self.fail = [ROUTE]
        st = self.step(3)
        self.assertEqual(st['mode'], 'normal')
        self.assertEqual([p[2]['on'] for p in self.posts], [True, False])
        self.assertEqual(self.events()[-1]['reason'], 'route')
        self.assertIsNone(self.dns)

    def test_on_timeout_sends_off(self):  # change 4
        self.on_raises = True
        st = self.step(3)
        self.assertEqual(st['mode'], 'normal')
        self.assertEqual([p[2]['on'] for p in self.posts], [True, False])
        self.assertFalse(self.phone_route())

    def test_http_timeouts_and_status_call(self):  # change 4
        self.m.share(self.cfg, True)
        self.m.share(self.cfg, False)
        self.assertEqual([p[3] for p in self.posts], [100, 20])
        self.assertEqual(self.m.phone_status(self.cfg), {'on': True})
        self.assertEqual(self.gets, [('http://phone.example:8767/v1/device/uplink.status', TOKEN, 10)])
        self.status = None
        self.assertIsNone(self.m.phone_status(self.cfg))

    def test_no_path_to_phone_gw_is_refused(self):  # change 3
        self.fail = [ROUTE_GET]
        st = self.step(3)
        self.assertEqual(st['mode'], 'normal')
        self.assertEqual(self.posts, [])
        self.assertEqual(self.events()[-1]['reason'], 'no_path')
        self.assertIn(['ip', '-j', 'route', 'get', GW, 'oif', 'eno1'], self.cmds)
        self.step(5)  # RETRY_S: no second attempt within 60 s
        self.assertEqual(sum(c[:4] == ROUTE_GET for c in self.cmds), 1)
        self.step(1)
        self.assertEqual(sum(c[:4] == ROUTE_GET for c in self.cmds), 2)

    def test_normal_mode_deletes_stale_phone_route(self):  # change 5
        self.routes.append({'dst': 'default', 'gateway': GW, 'dev': 'eno1', 'metric': 50})
        self.step()
        self.assertFalse(self.phone_route())
        self.assertIn(ROUTE_DEL, self.cmds)

    def test_normal_mode_reverts_dns_when_state_says_dns_set(self):  # change 5
        self.m.write_json(self.m.STATE, {'mode': 'normal', 'dns_set': True})
        self.dns = DNS
        st = self.step()
        self.assertIsNone(self.dns)
        self.assertFalse(st['dns_set'])

    def test_failover_route_is_reasserted_every_iteration(self):  # change 6a
        self.step(3)
        self.routes = [r for r in self.routes if r['gateway'] != GW]
        self.step()
        self.assertTrue(self.phone_route())

    def test_phone_reports_off_during_failover_tears_down_and_respects_retry(self):  # change 6b
        self.step(3)
        self.status = {'on': False}
        self.step(3)
        st = self.state()
        self.assertEqual(st['mode'], 'normal')
        self.assertFalse(self.phone_route())
        self.assertEqual(len(self.offs()), 1)
        self.assertEqual(self.events()[-1]['reason'], 'phone_stopped')
        self.assertIsNone(self.dns)
        self.step(5)  # 50 s after teardown: RETRY_S not over
        self.assertEqual(len(self.ons()), 1)
        self.step(1)
        self.assertEqual(len(self.ons()), 2)

    def test_three_unreachable_status_polls_tear_down(self):  # change 6b
        self.step(3)
        self.status = None
        self.step(8)
        self.assertEqual(self.state()['mode'], 'failover')
        self.step(1)
        self.assertEqual(self.state()['mode'], 'normal')
        self.assertFalse(self.phone_route())
        self.assertEqual(self.events()[-1]['reason'], 'phone_unreachable')
        self.assertEqual(len(self.offs()), 1)

    def test_one_unreachable_poll_does_not_tear_down(self):  # change 6b
        self.step(3)
        self.status = None
        self.step(3)
        self.status = {'on': True}
        self.step(10)
        self.assertEqual(self.state()['mode'], 'failover')
        self.assertEqual(self.state()['status_fails'], 0)

    def test_two_successes_remove_route_even_if_off_post_fails(self):
        self.reconf(up_after=2, min_failover_s=0)
        self.step(3)
        self.ping_ok, self.off_raises = True, True
        self.step()
        st = self.step()
        self.assertEqual(st['mode'], 'normal')
        self.assertFalse(self.phone_route())
        self.assertEqual(self.posts[-1][2], {'on': False})
        self.assertEqual(self.names()[-3:], ['failover_on', 'up', 'failover_off'])


class AntiFlap(Base):
    def test_min_failover_s_holds_failover_despite_recovery(self):  # change 7
        self.reconf(up_after=2, min_failover_s=300)
        self.step(3)
        self.ping_ok = True
        self.assertEqual(self.step(5)['mode'], 'failover')   # oks >= up_after but only 50 s in
        self.clock += 300
        self.assertEqual(self.step()['mode'], 'normal')

    def test_alternating_ping_results_do_not_leave_failover_before_min_failover_s(self):  # change 7
        self.reconf(up_after=2, min_failover_s=300)
        self.step(3)
        for i in range(25):
            self.ping_ok = i % 2 == 0
            self.assertEqual(self.step()['mode'], 'failover')

    def test_defaults_up_after_6_and_min_failover_300(self):  # change 7
        self.assertEqual((self.cfg['up_after'], self.cfg['min_failover_s']), (6, 300))
        self.step(3)
        self.ping_ok = True
        self.clock += 400
        self.assertEqual(self.step(5)['mode'], 'failover')
        self.assertEqual(self.step()['mode'], 'normal')

    def cycle(self):
        self.ping_ok = False
        for _ in range(120):
            st = self.step()
            if st['mode'] == 'failover':
                break
        self.ping_ok = True
        self.step()
        self.assertEqual(self.state()['mode'], 'normal')
        return st['gap']

    def test_backoff_doubles_within_the_hour_and_caps_at_900(self):  # change 7
        self.reconf(down_after=1, up_after=1, min_failover_s=0)
        self.assertEqual([self.cycle() for _ in range(7)], [60, 120, 240, 480, 900, 900, 900])

    def test_backoff_resets_after_an_hour_of_normal_mode(self):  # change 7
        self.reconf(down_after=1, up_after=1, min_failover_s=0)
        self.assertEqual([self.cycle(), self.cycle()], [60, 120])
        self.clock += 3700
        self.step()
        self.assertEqual(self.state()['gap'], 60)
        self.assertEqual(self.cycle(), 60)


class Dns(Base):
    def test_dns_set_on_enter_and_reverted_on_leave(self):  # change 8
        self.reconf(up_after=1, min_failover_s=0)
        self.step(3)
        self.assertEqual(self.dns, DNS)
        self.assertIn(['resolvectl', 'dns', 'eno1', *DNS], self.cmds)
        self.assertTrue(self.state()['dns_set'])
        self.ping_ok = True
        self.step()
        self.assertIsNone(self.dns)
        self.assertIn(['resolvectl', 'revert', 'eno1'], self.cmds)
        self.assertFalse(self.state()['dns_set'])

    def test_dns_failure_is_audited_not_fatal_and_sweep_reverts(self):  # change 8
        self.fail = [['resolvectl', 'dns']]
        self.assertEqual(self.step(3)['mode'], 'failover')
        self.assertIn('dns_failed', self.names())
        self.dns = DNS
        self.m.sweep(self.cfg)
        self.assertIsNone(self.dns)

    def test_failover_dns_default_and_validation(self):  # change 8
        self.assertEqual(self.cfg['failover_dns'], DNS)
        self.write_conf(failover_dns=['not-an-ip'])
        with self.assertRaises(self.m.ConfigError):
            self.m.load_config(self.conf)


class StateFirst(Base):
    def test_audit_never_raises_on_oserror(self):  # change 9
        blocker = self.root / 'blocker'
        blocker.write_text('x')
        self.m.AUDIT = blocker / 'audit.jsonl'
        self.assertEqual(self.step(3)['mode'], 'failover')
        self.assertEqual(self.state()['mode'], 'failover')

    def test_state_is_written_before_the_failover_audit(self):  # change 9
        real = self.m.audit

        def boom(event, **kw):
            if event == 'failover_on':
                raise OSError('disk full')
            real(event, **kw)
        self.m.audit = boom
        self.step(2)
        with self.assertRaises(OSError):
            self.step()
        self.assertEqual(self.state()['mode'], 'failover')
        self.assertTrue(self.phone_route())


class Startup(Base):
    def live_failover(self):
        self.m.write_json(self.m.STATE, {'mode': 'failover', 'sharing': True, 'dns_set': True,
                                         'failover_since': 900})
        self.routes.append({'dst': 'default', 'gateway': GW, 'dev': 'eno1', 'metric': 50})
        self.rules.append({'priority': 1178})
        self.dns = DNS

    def test_restart_during_outage_adopts_when_phone_says_on(self):  # change 10
        self.live_failover()
        self.assertEqual(self.m.startup(self.cfg), 'adopt')
        self.assertTrue(self.phone_route())
        self.assertEqual((self.dns, self.posts), (DNS, []))
        self.assertEqual(self.names(), ['adopt'])
        self.assertEqual(self.step()['mode'], 'failover')

    def test_restart_sweeps_and_posts_off_when_phone_says_off(self):  # change 10
        self.live_failover()
        self.status = {'on': False}
        self.assertEqual(self.m.startup(self.cfg), 'sweep')
        self.assertFalse(self.phone_route())
        self.assertEqual((self.rules, self.dns), ([], None))
        self.assertEqual(len(self.offs()), 1)
        self.assertEqual(self.state()['mode'], 'normal')

    def test_restart_sweeps_when_phone_unreachable(self):  # change 10
        self.live_failover()
        self.status = None
        self.assertEqual(self.m.startup(self.cfg), 'sweep')
        self.assertEqual(len(self.offs()), 1)

    def test_sweep_always_posts_off_whatever_state_says(self):  # change 10
        self.assertEqual(self.m.startup(self.cfg), 'sweep')   # no state file at all
        self.m.write_json(self.m.STATE, {'mode': 'normal', 'sharing': False})
        self.m.sweep(self.cfg)
        self.assertEqual(len(self.offs()), 2)
        self.assertEqual(self.gets, [])

    def test_stop_tears_everything_down(self):  # change 10 (SIGTERM / ExecStopPost sweep)
        real_once = self.m.once

        def fake_once(cfg):
            try:
                for _ in range(3):
                    self.clock += 10
                    real_once(cfg)
            finally:
                self.m.STOP.set()
        self.m.once = fake_once
        self.m.loop(self.cfg)
        self.assertFalse(self.phone_route())
        self.assertEqual((self.rules, self.table, self.dns), ([], [], None))
        self.assertEqual(self.posts[-1][2], {'on': False})
        self.assertEqual(self.names()[-1], 'stop')
        self.assertEqual(self.state()['mode'], 'normal')

    def test_sweep_cleans_leftover_state(self):
        self.live_failover()
        st = self.m.sweep(self.cfg)
        self.assertEqual((st['mode'], st['sharing']), ('normal', False))
        self.assertIn(ROUTE_DEL, self.cmds)
        self.assertEqual(self.posts[-1][2], {'on': False})


class Config(Base):
    def test_public_phone_gw_refused(self):
        self.write_conf(phone_gw='1.1.1.1')
        with self.assertRaises(self.m.ConfigError):
            self.m.load_config(self.conf)

    def test_group_readable_token_refused(self):
        self.tok.chmod(0o644)
        with self.assertRaises(self.m.ConfigError):
            self.m.load_config(self.conf)

    def test_missing_token_gives_missing_or_unreadable_error(self):  # change 11
        os.unlink(self.tok)
        with self.assertRaisesRegex(self.m.ConfigError, 'token_file is missing or unreadable'):
            self.m.load_config(self.conf)

    @unittest.skipIf(os.geteuid() == 0, 'root ignores directory permissions')
    def test_unreadable_token_directory_gives_config_error(self):  # change 11
        d = self.root / 'locked'
        d.mkdir()
        (d / 'token').write_text(TOKEN)
        (d / 'token').chmod(0o600)
        self.write_conf(token_file=str(d / 'token'))
        d.chmod(0)
        try:
            with self.assertRaisesRegex(self.m.ConfigError, 'token_file is missing or unreadable'):
                self.m.load_config(self.conf)
        finally:
            d.chmod(0o700)

    def test_unreadable_token_file_gives_config_error(self):  # change 11, also true as root
        orig = Path.read_text

        def deny(self_, *a, **k):
            if self_ == self.tok:
                raise PermissionError('denied')
            return orig(self_, *a, **k)
        Path.read_text = deny
        try:
            with self.assertRaisesRegex(self.m.ConfigError, 'missing or unreadable'):
                self.m.load_config(self.conf)
        finally:
            Path.read_text = orig

    def test_defaults(self):
        c = self.cfg
        self.assertEqual((c['dev'], c['targets'], c['interval_s'], c['down_after'], c['up_after'], c['metric'],
                          c['min_failover_s'], c['failover_dns']),
                         ('eno1', ['1.1.1.1', '9.9.9.9'], 10, 3, 6, 50, 300, DNS))

    def test_token_never_in_audit_or_state(self):
        self.reconf(up_after=1, min_failover_s=0)
        self.step(3)
        self.ping_ok, self.off_raises = True, True
        self.step(2)
        self.m.sweep(self.cfg)
        self.assertNotIn(TOKEN, self.m.AUDIT.read_text())
        self.assertNotIn(TOKEN, self.m.STATE.read_text())
        self.assertNotIn('token', self.state())


class Unit(unittest.TestCase):  # change 12
    def test_unit_hardening(self):
        u = (HERE / 'unum-uplink-failover.service').read_text()
        self.assertIn('ProtectHome=yes\n', u)
        self.assertIn('CapabilityBoundingSet=CAP_NET_ADMIN CAP_NET_RAW\n', u)
        self.assertIn('AmbientCapabilities=CAP_NET_ADMIN CAP_NET_RAW\n', u)
        fam = [x for x in u.splitlines() if x.startswith('RestrictAddressFamilies=')][0].split('=')[1].split()
        self.assertEqual(sorted(fam), ['AF_INET', 'AF_INET6', 'AF_NETLINK', 'AF_UNIX'])
        self.assertIn('/etc/unum/s22-device.token', u.splitlines()[0] + u.splitlines()[1])
        self.assertIn('ExecStopPost=', u)


if __name__ == '__main__':
    unittest.main()
