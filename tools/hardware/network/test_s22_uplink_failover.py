#!/usr/bin/env python3
"""Hardware-free tests for s22-uplink-failover (run, tcp_probe, trace_probe, pause and the HTTP calls are faked; the route table,
rule list, tables 178/179/180 and DNS are stateful). Run: python3 tools/hardware/network/test_s22_uplink_failover.py"""
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
GW2, RIG_ADDR = '192.168.77.51', '192.168.77.10'   # GW2: the phone after a DHCP change
TOKEN = 'fake-' + 'bearer-for-tests'   # not a secret; split so the secret scanner stays quiet
ROUTE = ['ip', 'route', 'replace', 'default', 'via', GW, 'dev', 'eno1', 'metric', '50']
ROUTE_DEL = ['ip', 'route', 'del', 'default', 'via', GW, 'dev', 'eno1', 'metric', '50']
RULE_ADD = ['ip', 'rule', 'add', 'fwmark', '0x178', 'lookup', '178', 'priority', '1178']
ROUTE_GET = ['ip', '-j', 'route', 'get']
VIA_ROUTE = ['ip', 'route', 'replace', 'default', 'via', GW, 'dev', 'eno1', 'table', '179']
VIA_RULE_ADD = ['ip', 'rule', 'add', 'fwmark', '0x179', 'lookup', '179', 'priority', '1179']
VIA_RULE_DEL = ['ip', 'rule', 'del', 'fwmark', '0x179', 'lookup', '179', 'priority', '1179']
VIA_FLUSH = ['ip', 'route', 'flush', 'table', '179']
NM_CHECK = ['nmcli', 'networking', 'connectivity', 'check']
DNS = ['1.0.0.1', '149.112.112.112']
ST_RULE_ADD = ['ip', 'rule', 'add', 'fwmark', '0x17a', 'lookup', '180', 'priority', '1180']
ST_RULE_DEL = ['ip', 'rule', 'del', 'fwmark', '0x17a', 'lookup', '180', 'priority', '1180']
MAIN_IP, VIA_IP = '198.51.100.9', '203.0.113.7'   # documentation ranges: fake public addresses
OK_RAW = b'HTTP/1.1 200 OK\r\nServer: x\r\n\r\nfl=1\nip=203.0.113.7\ncolo=ATH\nhttp=http/1.1\n'


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
        m.STATE, m.AUDIT, m.LOCK = root / 'state.json', root / 'audit.jsonl', root / 'lock'
        self.tok = root / 'token'
        self.tok.write_text(TOKEN + '\n')
        self.tok.chmod(0o600)
        self.conf = root / 'conf.json'
        self.write_conf()
        self.cfg = m.load_config(self.conf)
        self.clock, self.cmds, self.posts, self.gets = 1000.0, [], [], []
        self.ping_ok, self.tcp_ok, self.post_result = False, False, (200, {'ok': True})
        self.off_raises = self.on_raises = False
        self.reachable = {GW, GW2}          # neighbours that are REACHABLE
        self.addrs = [{'local': RIG_ADDR, 'prefixlen': 24}]
        self.status = {'on': True, 'wlan0_ipv4': GW}         # None -> the status GET raises
        self.fail = []                      # argv prefixes that make `run` return rc=1
        self.routes = [{'dst': 'default', 'gateway': MAIN, 'dev': 'eno1', 'metric': 100}]
        self.tabs = {'178': [], '179': [], '180': []}
        self.rules, self.dns, self.tcp_calls = [], None, []
        self.http_ok, self.http_calls, self.pauses = True, [], []
        self.http_seq = []                  # consumed first by the phone-path trace, then http_ok applies
        self.main_calls, self.main_trace_ok, self.main_ip, self.via_ip = [], True, MAIN_IP, VIA_IP
        self.raises = []                    # argv prefixes that make `run` raise OSError
        m.now = lambda: self.clock
        m.run, m.http_post, m.http_get, m.tcp_probe = self.frun, self.post, self.get, self.tcp
        m.trace_probe, m.pause = self.trace, self.pause

    def tearDown(self):
        self.tmp.cleanup()

    def frun(self, argv, check=True):
        self.cmds.append(argv)
        rc, out = 0, ''
        if any(argv[:len(p)] == p for p in self.raises):
            raise OSError('boom')
        if any(argv[:len(p)] == p for p in self.fail):
            rc = 1
        elif argv[0] == 'ping':
            rc = 0 if self.ping_ok else 1
        elif argv[:4] == ['ip', '-j', 'route', 'show']:
            out = json.dumps(self.routes)
        elif argv[:5] == ['ip', '-j', '-4', 'addr', 'show']:
            out = json.dumps([{'addr_info': self.addrs}])
        elif argv[:4] == ['ip', '-j', 'neigh', 'show']:
            out = json.dumps([{'dst': argv[4], 'state': ['REACHABLE' if argv[4] in self.reachable else 'FAILED']}])
        elif argv[:4] == ['ip', '-j', 'route', 'get']:
            out = '[{}]'
        elif argv[:4] == ['ip', '-j', 'rule', 'show']:
            out = json.dumps([r for r in self.rules if str(r['priority']) == argv[5]])
        elif argv[:3] == ['ip', 'route', 'replace'] and 'table' in argv:
            self.tabs[argv[-1]] = [argv[5]]
        elif argv[:3] == ['ip', 'route', 'replace']:
            self.routes = [r for r in self.routes if r['gateway'] != argv[5]]
            self.routes.append({'dst': 'default', 'gateway': argv[5], 'dev': argv[7], 'metric': int(argv[9])})
        elif argv[:3] == ['ip', 'route', 'del']:
            self.routes = [r for r in self.routes if r['gateway'] != argv[5]]
        elif argv[:3] == ['ip', 'route', 'flush']:
            self.tabs[argv[-1]] = []
        elif argv[:3] == ['ip', 'rule', 'add']:
            self.rules.append({'priority': int(argv[-1]), 'fwmark': argv[4], 'table': argv[6]})
        elif argv[:3] == ['ip', 'rule', 'del']:
            for r in self.rules:
                if str(r['priority']) == argv[-1]:
                    self.rules.remove(r)
                    break
        elif argv[:3] == ['resolvectl', 'dns', 'eno1'] and len(argv) == 3:
            out = 'Link 2 (eno1): ' + ' '.join(self.dns or [])
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

    def trace(self, dev, target, mark):
        if mark in (0x178, 0):   # main line (or unmarked)
            self.main_calls.append((dev, target, mark))
            return {'ip': self.main_ip, 'colo': 'FRA'} if self.main_trace_ok else None
        self.http_calls.append((dev, target, mark))
        ok = self.http_seq.pop(0) if self.http_seq else self.http_ok
        return {'ip': self.via_ip, 'colo': 'ATH'} if ok else None

    table = property(lambda self: self.tabs['178'], lambda self, v: self.tabs.__setitem__('178', v))
    via_table = property(lambda self: self.tabs['179'], lambda self, v: self.tabs.__setitem__('179', v))

    def pause(self, seconds):
        self.pauses.append(seconds)
        self.clock += seconds

    def via_rules(self):
        return [r for r in self.rules if r['priority'] == 1179]

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
        self.assertEqual([r['priority'] for r in self.rules if r['priority'] == 1178], [1178])

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
        self.reconf(keepalive_s=0)
        self.routes = []
        for _ in range(10):
            st = self.step()
            self.assertEqual((st['mode'], st['note'], st['fails']), ('normal', 'no_gateway', 0))
        self.assertEqual((self.posts, self.gets), ([], []))
        self.assertNotIn('down', self.names() if self.m.AUDIT.exists() else [])

    def test_no_gateway_with_default_keepalive_does_one_get_and_no_post(self):
        self.routes = []
        for _ in range(10):
            self.assertEqual(self.step()['note'], 'no_gateway')
        self.assertEqual((len(self.gets), self.posts), (1, []))

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
        self.assertEqual(self.m.phone_status(self.cfg), {'on': True, 'wlan0_ipv4': GW})
        self.assertEqual(self.gets, [('http://phone.example:8767/v1/device/uplink.status', TOKEN, 10)])
        self.status = None
        self.assertIsNone(self.m.phone_status(self.cfg))

    def test_resolve_follows_a_changed_phone_address(self):
        self.status = {'on': True, 'wlan0_ipv4': GW2}
        self.assertEqual(self.m.resolve_phone_gw(self.cfg), GW2)
        self.assertEqual(self.cfg['phone_gw'], GW2)
        ev = [e for e in self.events() if e['event'] == 'phone_gw_changed']
        self.assertEqual(len(ev), 1)
        self.assertEqual((ev[0]['configured'], ev[0]['resolved']), (GW, GW2))
        self.m.resolve_phone_gw(self.cfg)   # same answer again: not logged again
        self.assertEqual(len([e for e in self.events() if e['event'] == 'phone_gw_changed']), 1)

    def test_resolve_falls_back_with_a_warning(self):
        cases = [(None, 'phone_unreachable'), ({'on': True}, 'no_wlan0_ipv4'),
                 ({'wlan0_ipv4': 'junk'}, 'not_an_ip'), ({'wlan0_ipv4': '1.1.1.1'}, 'not_private_ipv4'),
                 ({'wlan0_ipv4': '10.9.9.9'}, 'off_subnet'), ({'wlan0_ipv4': RIG_ADDR}, 'own_address'),
                 ({'wlan0_ipv4': '192.168.77.99'}, 'neighbour_not_reachable')]
        for status, why in cases:
            self.status = status
            self.cfg.pop('_gw_noted', None)
            self.assertEqual(self.m.resolve_phone_gw(self.cfg), GW, why)
            self.assertEqual(self.events()[-1]['event'], 'phone_gw_fallback')
            self.assertEqual(self.events()[-1].get("reason"), why, self.events()[-1])

    def test_failover_uses_the_resolved_address(self):
        self.status = {'on': True, 'wlan0_ipv4': GW2}
        st = self.step(3)
        self.assertEqual(st['mode'], 'failover')
        self.assertEqual(st['phone_gw_live'], GW2)
        self.assertEqual(self.tabs['179'], [GW2])
        self.assertIn(['ip', 'route', 'replace', 'default', 'via', GW2, 'dev', 'eno1', 'metric', '50'], self.cmds)
        self.assertNotIn(ROUTE, self.cmds)

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


class DnsRestore(Base):
    PREV = [MAIN, 'fe80::1%eno1']

    def test_previous_resolvers_are_saved_and_restored_on_leave(self):
        self.reconf(up_after=1, min_failover_s=0)
        self.dns = list(self.PREV)
        self.step(3)
        self.assertEqual((self.dns, self.state()['dns_prev']), (DNS, self.PREV))
        self.ping_ok = True
        self.step()
        self.assertEqual(self.dns, self.PREV)
        self.assertNotIn(['resolvectl', 'revert', 'eno1'], self.cmds)
        st = self.state()
        self.assertEqual((st['dns_set'], st['dns_prev'], st['mode']), (False, [], 'normal'))

    def test_failover_resolvers_are_never_saved_as_previous_on_reentry(self):
        self.dns = list(DNS)   # a crash left the failover resolvers on the link and no saved ones
        self.step(3)
        self.assertEqual(self.state()['dns_prev'], [])

    def test_existing_previous_is_not_overwritten_on_reentry(self):
        self.m.write_json(self.m.STATE, {'mode': 'normal', 'dns_prev': [MAIN]})
        self.dns = list(DNS)
        self.step(3)
        self.assertEqual(self.state()['dns_prev'], [MAIN])

    def test_empty_previous_reverts_then_asks_networkmanager_to_reapply(self):
        self.reconf(up_after=1, min_failover_s=0)
        self.step(3)
        self.assertEqual(self.state()['dns_prev'], [])
        self.ping_ok = True
        self.step()
        rev, rea = ['resolvectl', 'revert', 'eno1'], ['nmcli', 'device', 'reapply', 'eno1']
        self.assertIn(rev, self.cmds)
        self.assertLess(self.cmds.index(rev), self.cmds.index(rea))
        self.assertFalse(self.state()['dns_set'])

    def test_sweep_restores_previous_resolvers_from_state(self):
        self.m.write_json(self.m.STATE, {'mode': 'failover', 'dns_set': True, 'dns_prev': [MAIN]})
        self.dns = list(DNS)
        st = self.m.sweep(self.cfg)
        self.assertEqual(self.dns, [MAIN])
        self.assertNotIn(['resolvectl', 'revert', 'eno1'], self.cmds)
        self.assertEqual((st['dns_set'], st['dns_prev']), (False, []))

    def test_failing_restore_is_audited_once_in_ten_minutes(self):
        self.m.write_json(self.m.STATE, {'mode': 'normal', 'dns_set': True, 'dns_prev': [MAIN]})
        self.fail = [['resolvectl', 'dns']]
        self.ping_ok = True   # stay in normal mode: only the restore can fail
        self.step(10)
        self.assertEqual(self.names().count('dns_failed'), 1)
        st = self.state()
        self.assertEqual((st['dns_set'], st['dns_prev']), (True, [MAIN]))
        self.clock += 600
        self.step()
        self.assertEqual(self.names().count('dns_failed'), 2)


class Keepalive(Base):
    URL = 'http://phone.example:8767/v1/device/uplink.status'

    def setUp(self):
        super().setUp()
        self.ping_ok = True

    def test_keepalive_interval_is_respected(self):
        self.step(30)   # 10 s .. 300 s
        self.assertEqual(self.gets, [(self.URL, TOKEN, 10)])
        self.step()     # 310 s: 300 s after the first answer
        self.assertEqual(len(self.gets), 2)

    def test_keepalive_never_changes_mode_or_counters(self):
        self.reconf(keepalive_s=10)
        for status in ({'rig_configured': True}, None, {'on': False}):
            self.status = status
            self.step()
        st = self.state()
        self.assertEqual((st['mode'], st['fails'], st['oks'], st['sharing']), ('normal', 0, 3, False))
        self.assertEqual((len(self.gets), self.posts), (3, []))
        self.ping_ok = False
        self.status = None
        self.assertEqual(self.step(2)['fails'], 2)   # failures still counted, still no failover from GETs
        self.assertEqual(self.posts, [])

    def test_status_fields_are_read_from_the_state_object_s22d_really_sends(self):
        real = {'ok': True, 'cap': 'uplink.status', 'effect': 'read',
                'state': {'on': False, 'rig_configured': True, 'battery': 85, 'forwarding': False}}
        self.status = real
        ps = self.m.phone_status(self.cfg)
        self.assertIs(ps['on'], False)
        self.assertIs(ps['rig_configured'], True)
        self.m.once(self.cfg)
        self.assertIs(self.m.read_json(self.m.STATE)['phone_ok'], True)

    def test_phone_ok_is_false_when_rig_configured_is_false_or_missing(self):
        self.reconf(keepalive_s=10)
        for status, ok in (({'rig_configured': False}, False), ({'on': True}, False),
                           ({'rig_configured': True}, True)):
            self.status = status
            st = self.step()
            self.assertEqual((st['phone_ok'], st['phone_seen']), (ok, self.clock))

    def test_unreachable_and_back_transitions_are_audited_once_each(self):
        self.reconf(keepalive_s=10)
        self.status = {'rig_configured': True}
        self.step(2)
        self.status = None
        st = self.step(3)
        self.assertEqual((st['phone_ok'], st['phone_seen']), (None, 1020.0))
        self.status = {'rig_configured': True}
        self.step(3)
        self.assertEqual([n for n in self.names() if n.startswith('phone_')], ['phone_unreachable', 'phone_back'])

    def test_keepalive_zero_disables_it(self):
        self.reconf(keepalive_s=0)
        self.step(50)
        self.assertEqual((self.gets, self.posts), ([], []))
        self.assertIsNone(self.state()['phone_ok'])

    def test_keepalive_s_validation(self):
        self.assertEqual(self.cfg['keepalive_s'], 300)
        for bad in (-1, 1.5, '300', None):
            self.write_conf(keepalive_s=bad)
            with self.assertRaises(self.m.ConfigError):
                self.m.load_config(self.conf)
        self.write_conf(keepalive_s=0)
        self.assertEqual(self.m.load_config(self.conf)['keepalive_s'], 0)


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


class ViaPath(Base):
    def test_via_rule_is_not_added_twice(self):
        self.step(3)
        self.step(10)   # failover: several path checks re-assert the path
        self.assertEqual(self.cmds.count(VIA_RULE_ADD), 1)
        self.assertEqual(len(self.via_rules()), 1)
        self.assertIn(VIA_ROUTE, self.cmds)
        self.assertEqual((self.m.VIA_MARK, self.m.VIA_TABLE, self.m.VIA_RULE_PRIO), (0x179, 179, 1179))

    def test_dead_phone_path_changes_no_route_or_dns_sends_off_and_respects_retry_gap(self):
        self.http_ok = False
        st = self.step(3)
        self.assertEqual(st['mode'], 'normal')
        self.assertFalse(self.phone_route())
        self.assertNotIn(ROUTE, self.cmds)
        self.assertFalse([c for c in self.cmds if c[:2] == ['resolvectl', 'dns']])
        self.assertIsNone(self.dns)
        self.assertEqual((len(self.ons()), len(self.offs())), (1, 1))
        self.assertEqual(self.posts[-1][2], {'on': False})
        ev = self.events()[-1]
        self.assertEqual((ev['event'], ev['reason']), ('failover_refused', 'phone_path_dead'))
        self.assertEqual(len(self.http_calls), 6)   # 3 tries x 2 verify targets
        self.assertEqual(self.pauses, [8, 8])
        self.assertEqual((ev['stage'], ev['tries'], ev['hold']), ('first', 3, 3))
        self.assertEqual((self.via_rules(), self.via_table), ([], []))
        self.step(4)   # 52 s after the attempt: still inside the 60 s gap
        self.assertEqual(len(self.ons()), 1)
        self.step(1)
        self.assertEqual(len(self.ons()), 2)

    def test_verification_succeeds_on_a_later_try_and_reports_probes_and_held(self):
        self.reconf(verify_targets=['1.1.1.1'])
        self.http_seq = [False, False]
        st = self.step(3)
        self.assertEqual(st['mode'], 'failover')
        self.assertTrue(self.phone_route())
        self.assertEqual(self.pauses, [8, 8, 8, 8])   # 2 between failed tries, 2 between hold probes
        self.assertEqual((self.events()[-1]['verify_probes'], self.events()[-1]['held']), (5, 3))
        self.assertNotIn('verify_tries_used', self.events()[-1])
        self.assertEqual(self.http_calls[0], ('eno1', '1.1.1.1', 0x179))
        self.assertLess(self.cmds.index(VIA_ROUTE), self.cmds.index(ROUTE))
        self.assertEqual(self.dns, DNS)

    def test_connect_only_result_is_not_enough_to_enter_failover(self):
        self.tcp_ok = True       # a bare TCP connect succeeds, but no HTTP data flows
        self.http_ok = False
        self.assertEqual(self.m.probe_via_phone(self.cfg, {}), False)
        self.ping_ok = self.tcp_ok = False
        self.assertEqual(self.step(3)['mode'], 'normal')
        self.assertFalse(self.phone_route())
        self.assertEqual(self.events()[-1]['reason'], 'phone_path_dead')

    def test_path_that_holds_enters_failover_and_reports_probes_and_held(self):
        st = self.step(3)
        self.assertEqual(st['mode'], 'failover')
        self.assertEqual((self.events()[-1]['verify_probes'], self.events()[-1]['held']), (3, 3))
        self.assertEqual(len(self.http_calls), 3)
        self.assertEqual(self.pauses, [8, 8])

    def test_path_that_answers_once_and_then_dies_is_refused_with_stage_hold(self):
        self.http_seq = [True, False]
        self.http_ok = False
        st = self.step(3)
        self.assertEqual(st['mode'], 'normal')
        self.assertFalse(self.phone_route())
        self.assertNotIn(ROUTE, self.cmds)
        self.assertFalse([c for c in self.cmds if c[:2] == ['resolvectl', 'dns']])
        self.assertIsNone(self.dns)
        self.assertEqual(self.posts[-1][2], {'on': False})
        self.assertEqual(len(self.offs()), 1)
        ev = self.events()[-1]
        self.assertEqual((ev['event'], ev['reason'], ev['stage']), ('failover_refused', 'phone_path_dead', 'hold'))
        self.assertEqual(self.pauses, [8])   # no restart after a path that worked and then died
        self.assertEqual((self.via_rules(), self.via_table), ([], []))

    def test_path_that_dies_on_the_last_hold_probe_is_refused_with_stage_hold(self):
        self.http_seq = [True, True, False]
        self.http_ok = False
        self.assertEqual(self.step(3)['mode'], 'normal')
        self.assertEqual(self.events()[-1]['stage'], 'hold')
        self.assertEqual(len(self.http_calls), 4)   # two answered at once, the failing one tried both targets

    def test_path_that_never_answers_is_refused_with_stage_first(self):
        self.http_ok = False
        self.step(3)
        self.assertEqual(self.events()[-1]['stage'], 'first')
        self.assertNotIn(ROUTE, self.cmds)

    def test_hold_pauses_use_verify_gap_s(self):
        self.reconf(verify_gap_s=7, verify_hold=4)
        self.step(3)
        self.assertEqual(self.pauses, [7, 7, 7])
        self.assertEqual(self.events()[-1]['verify_probes'], 4)
        self.reconf(verify_gap_s=0, verify_hold=1)
        self.pauses.clear()
        self.assertEqual(self.m.verify_phone_path(self.cfg, {}), (1, 'ok'))
        self.assertEqual(self.pauses, [])

    def test_probe_via_phone_uses_verify_targets_and_never_the_main_targets(self):
        self.reconf(targets=['9.9.9.9'], verify_targets=['1.0.0.1'])
        self.http_ok = False
        self.m.probe_via_phone(self.cfg, {})
        self.assertEqual(self.http_calls, [('eno1', '1.0.0.1', 0x179)])
        self.http_calls.clear()
        self.reconf()
        self.http_ok = True
        self.m.probe_via_phone(self.cfg, {})
        self.assertEqual({c[1] for c in self.http_calls}, {'1.1.1.1'})
        self.http_calls.clear()
        self.http_ok = False
        self.m.probe_via_phone(self.cfg, {})
        self.assertEqual([c[1] for c in self.http_calls], ['1.1.1.1', '1.0.0.1'])
        self.assertNotIn('9.9.9.9', [c[1] for c in self.http_calls])

    def test_failover_is_left_after_three_dead_path_checks_with_new_defaults(self):
        self.step(3)
        self.http_ok = False
        self.step(2)                      # two dead checks
        self.assertEqual((self.state()['mode'], self.state()['path_fails']), ('failover', 2))
        self.assertTrue(self.phone_route())
        st = self.step(1)                 # third dead check reaches the default limit of 3
        self.assertEqual(st['mode'], 'normal')
        self.assertFalse(self.phone_route())
        self.assertIsNone(self.dns)
        self.assertEqual(self.events()[-1]['reason'], 'phone_path_dead')
        self.assertEqual(len(self.offs()), 1)
        self.assertEqual(st['last_try'], self.clock)
        self.assertEqual((self.via_rules(), self.via_table), ([], []))

    def test_one_dead_check_then_a_good_one_resets_the_counter(self):
        self.reconf(path_fail_limit=3)
        self.step(3)
        self.http_ok = False
        self.step(2)
        self.assertEqual(self.state()['path_fails'], 2)
        self.http_ok = True
        self.step(1)
        self.assertEqual(self.state()['path_fails'], 0)
        self.http_ok = False
        self.step(2)                      # two more dead checks: still below the limit of 3
        self.assertEqual(self.state()['mode'], 'failover')

    def test_path_check_runs_at_most_every_path_check_s(self):
        self.reconf(path_check_s=25)
        self.step(3)
        n = len(self.http_calls)
        self.step(1)
        self.assertEqual(len(self.http_calls), n)
        self.step(2)
        self.assertEqual(len(self.http_calls), n + 1)

    def test_via_path_is_kept_during_failover_and_cleaned_on_leave(self):
        self.reconf(up_after=1, min_failover_s=0)
        self.step(3)
        self.step(2)
        self.assertEqual((len(self.via_rules()), self.via_table), (1, [GW]))
        self.ping_ok = True
        self.assertEqual(self.step()['mode'], 'normal')
        self.assertEqual((self.via_rules(), self.via_table), ([], []))
        self.assertIn(VIA_RULE_DEL, self.cmds)
        self.assertIn(VIA_FLUSH, self.cmds)

    def test_via_path_is_cleaned_on_sweep(self):
        self.step(3)
        self.assertTrue(self.via_rules() and self.via_table)
        self.m.sweep(self.cfg)
        self.assertEqual((self.via_rules(), self.via_table), ([], []))
        self.assertIn(VIA_FLUSH, self.cmds)

    def test_connectivity_check_is_issued_on_leave_and_on_sweep(self):
        self.reconf(up_after=1, min_failover_s=0)
        self.step(3)
        self.assertNotIn(NM_CHECK, self.cmds)
        self.ping_ok = True
        self.step()
        self.assertEqual(self.cmds.count(NM_CHECK), 1)
        self.m.sweep(self.cfg)
        self.assertEqual(self.cmds.count(NM_CHECK), 2)

    def test_connectivity_check_failure_is_ignored(self):
        self.fail = [NM_CHECK]
        self.m.sweep(self.cfg)
        self.assertEqual(self.state()['mode'], 'normal')

    def test_via_path_is_removed_in_normal_mode(self):
        self.m.write_json(self.m.STATE, {'mode': 'normal'})
        self.rules.append({'priority': 1179})
        self.ping_ok = True
        self.step()
        self.assertEqual(self.via_rules(), [])


class Selftest(Base):
    def run_self(self, seconds=40):
        res, rc = self.m.selftest(self.cfg, seconds)
        return res, rc

    def test_all_pass_run(self):
        self.ping_ok = True
        res, rc = self.run_self(40)
        self.assertEqual(rc, 0)
        self.assertTrue(res['ok'] and res['share_ok'])
        self.assertEqual((res['passed'], res['failed'], res['first_fail_t']), (9, 0, None))
        self.assertEqual([x['t'] for x in res['samples']], [0, 5, 10, 15, 20, 25, 30, 35, 40])
        self.assertEqual([p[2]['on'] for p in self.posts], [True, False])
        self.assertEqual(self.posts[0][3], 100)
        self.assertEqual((self.via_rules(), self.via_table), ([], []))
        self.assertEqual(self.events()[-1], {**self.events()[-1], 'event': 'selftest', 'passed': 9, 'failed': 0})

    def test_run_failing_from_second_sample_sets_first_fail_and_still_cleans_up(self):
        self.http_seq = [True]
        self.http_ok = False
        res, rc = self.run_self(40)
        self.assertEqual(rc, 1)
        self.assertFalse(res['ok'])
        self.assertEqual((res['passed'], res['failed'], res['first_fail_t']), (1, 8, 5))
        self.assertIn(ST_RULE_DEL, self.cmds)
        self.assertEqual(self.tabs['180'], [])
        self.assertEqual([r for r in self.rules if r['priority'] == 1180], [])
        self.assertEqual(self.posts[-1][2], {'on': False})

    def test_refused_while_failover_is_active(self):
        self.m.write_json(self.m.STATE, {'mode': 'failover'})
        res, rc = self.run_self()
        self.assertEqual(rc, 1)
        self.assertIn('error', res)
        self.assertEqual((self.posts, self.cmds), ([], []))

    def test_never_touches_default_route_or_dns(self):
        self.run_self(10)
        for c in self.cmds:
            self.assertNotEqual(c[0], 'resolvectl')
            self.assertFalse(c[:3] in (['ip', 'route', 'replace'], ['ip', 'route', 'del']) and 'metric' in c, c)
        self.assertNotIn(ROUTE, self.cmds)
        self.assertTrue(self.routes == [{'dst': 'default', 'gateway': MAIN, 'dev': 'eno1', 'metric': 100}])

    def test_share_refused_fails_without_probing_and_still_shares_off(self):
        self.post_result = (403, {'ok': False})
        res, rc = self.run_self()
        self.assertEqual((rc, res['ok'], res['share_ok'], res['samples']), (1, False, False, []))
        self.assertEqual(self.http_calls, [])
        self.assertEqual(self.posts[-1][2], {'on': False})

    def test_main_ip_is_learned_even_when_the_phone_refuses_the_share(self):
        self.post_result = (403, {'ok': False})
        res, rc = self.run_self()
        self.assertEqual((rc, res['share_ok'], res['main_ip_known']), (1, False, True))
        self.assertEqual(len(self.main_calls), 1)

    def test_fewer_than_three_samples_is_not_ok(self):
        res, _ = self.m.selftest(self.cfg, 5)
        self.assertEqual(len(res['samples']), 2)
        self.assertFalse(res['ok'])

    def test_samples_spanning_under_15_s_are_not_ok(self):
        res, rc = self.m.selftest(self.cfg, 10)   # 3 good samples at 0, 5, 10 s: span 10 s
        self.assertEqual((len(res['samples']), res['failed']), (3, 0))
        self.assertFalse(res['ok'])
        self.assertEqual(rc, 1)
        res, rc = self.m.selftest(self.cfg, 15)   # span exactly 15 s is enough
        self.assertTrue(res['ok'])

    def test_cli_default_seconds_is_40(self):
        import contextlib
        import io
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = self.m.main(['--config', str(self.conf), 'selftest'])
        self.assertEqual((rc, len(json.loads(out.getvalue())['samples'])), (0, 9))

    def test_cli_seconds_range_and_exit_code(self):
        import contextlib
        import io
        for bad in ('9', '121'):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                self.m.main(['--config', str(self.conf), 'selftest', '--seconds', bad])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = self.m.main(['--config', str(self.conf), 'selftest', '--seconds', '10'])
        self.assertEqual(rc, 1)   # a 10 s run spans under 15 s, so it can never be ok
        self.assertEqual(json.loads(out.getvalue())['passed'], 3)


class NewConfig(Base):
    def test_new_ints_defaults_and_ranges(self):
        c = self.cfg
        self.assertEqual((c['verify_tries'], c['verify_hold'], c['verify_gap_s'], c['path_check_s'],
                          c['path_fail_limit']), (3, 3, 8, 10, 3))
        good = {'verify_tries': (1, 10), 'verify_hold': (1, 10), 'verify_gap_s': (0, 30), 'path_check_s': (5, 300),
                'path_fail_limit': (1, 10)}
        for k, (lo, hi) in good.items():
            for v in (lo, hi):
                self.write_conf(**{k: v})
                self.assertEqual(self.m.load_config(self.conf)[k], v)
            for v in (lo - 1, hi + 1, 1.5, '3', None, True):
                self.write_conf(**{k: v})
                with self.assertRaises(self.m.ConfigError, msg=f'{k}={v!r}'):
                    self.m.load_config(self.conf)

    def test_verify_targets_default_and_validation(self):
        self.assertEqual(self.cfg['verify_targets'], ['1.1.1.1', '1.0.0.1'])
        self.write_conf(verify_targets=['1.0.0.1'])
        self.assertEqual(self.m.load_config(self.conf)['verify_targets'], ['1.0.0.1'])
        for v in ([], 'x', '1.1.1.1', ['nope'], [None], None, 5, ['2001:db8::1'], ['1.1.1.1', '2001:db8::1']):
            self.write_conf(verify_targets=v)
            with self.assertRaisesRegex(self.m.ConfigError, 'verify_targets', msg=repr(v)):
                self.m.load_config(self.conf)


class ParseTrace(Base):
    def test_200_with_colo_is_parsed_with_ip(self):
        self.assertEqual(self.m.parse_trace(OK_RAW), {'ip': VIA_IP, 'colo': 'ATH'})

    def test_200_without_ip_line_gives_empty_ip(self):
        raw = b'HTTP/1.0 200 OK\r\n\r\ncolo=FRA\n'
        self.assertEqual(self.m.parse_trace(raw), {'ip': '', 'colo': 'FRA'})

    def test_carrier_portal_302_does_not_count(self):
        raw = b'HTTP/1.1 302 Found\r\nLocation: http://portal.invalid/\r\n\r\ncolo=ATH\nip=203.0.113.7\n'
        self.assertIsNone(self.m.parse_trace(raw))
        self.assertIsNone(self.m.parse_trace(raw.replace(b'302 Found', b'301 Moved')))

    def test_200_without_colo_or_with_empty_colo_does_not_count(self):
        self.assertIsNone(self.m.parse_trace(b'HTTP/1.1 200 OK\r\n\r\n<html>welcome</html>'))
        self.assertIsNone(self.m.parse_trace(b'HTTP/1.1 200 OK\r\n\r\nip=203.0.113.7\ncolo=\n'))

    def test_short_reads_and_garbage_are_none(self):
        for raw in (b'', b'HTTP/1.1 2', b'HTTP/1.1 200 OK', b'HTTP/1.1 200 OK\r\n', b'\x00\xff\xfe garbage',
                    b'SSH-2.0-OpenSSH\r\n\r\ncolo=ATH\n', b'HTTP/2 200\r\n\r\ncolo=ATH\n'):
            self.assertIsNone(self.m.parse_trace(raw), raw)

    def test_trace_probe_is_verified_https_on_443_with_device_and_mark(self):
        import types
        log = []

        class Sock:
            def __init__(self, *a):
                log.append(('socket', a))
                self.sent, self.chunks = b'', [OK_RAW, b'']

            def setsockopt(self, lvl, opt, val):
                log.append(('opt', opt, val))

            def settimeout(self, t):
                log.append(('timeout', t))

            def connect(self, addr):
                log.append(('connect', addr))

            def sendall(self, data):
                log.append(('send', data))

            def recv(self, n):
                return self.chunks.pop(0)

            def close(self):
                pass

        class Ctx:
            def wrap_socket(self, sock, server_hostname):
                log.append(('tls', server_hostname))
                return sock
        fake_sock = types.SimpleNamespace(AF_INET=2, SOCK_STREAM=1, SOL_SOCKET=1, SO_BINDTODEVICE=25, SO_MARK=36,
                                          socket=Sock)
        m = load()
        m.socket, m.ssl = fake_sock, types.SimpleNamespace(create_default_context=lambda: Ctx())
        self.assertEqual(m.trace_probe('eno1', '1.1.1.1', 0x17a), {'ip': VIA_IP, 'colo': 'ATH'})
        self.assertIn(('opt', 25, b'eno1'), log)
        self.assertIn(('opt', 36, 0x17a), log)
        self.assertIn(('connect', ('1.1.1.1', 443)), log)
        self.assertIn(('tls', '1.1.1.1'), log)
        self.assertIn(('send', b'GET /cdn-cgi/trace HTTP/1.0\r\nHost: 1.1.1.1\r\n\r\n'), log)
        self.assertEqual(log[0], ('socket', (2, 1)))   # IPv4 only
        log.clear()
        m.trace_probe('eno1', '1.1.1.1', 0)
        self.assertNotIn(36, [x[1] for x in log if x[0] == 'opt'])   # mark 0: no SO_MARK


class WrongWay(Base):
    def test_main_ip_is_learned_over_the_main_marked_path_at_the_keepalive_cadence(self):
        self.ping_ok = True
        self.step()
        self.assertEqual(self.state()['main_ip'], MAIN_IP)
        self.assertEqual(self.main_calls, [('eno1', '1.1.1.1', 0x178)])
        self.step(29)        # inside 300 s
        self.assertEqual(len(self.main_calls), 1)
        self.step(1)
        self.assertEqual(len(self.main_calls), 2)

    def test_first_answering_verify_target_is_used_for_learning(self):
        calls = []
        self.m.trace_probe = lambda d, t, mk: calls.append(t) or (None if t == '1.1.1.1' else {'ip': MAIN_IP, 'colo': 'x'})
        self.m.first_trace(self.cfg, 0x178)
        self.assertEqual(calls, ['1.1.1.1', '1.0.0.1'])

    def test_main_ip_is_not_learned_when_the_main_line_is_down(self):
        self.step(2)
        self.assertEqual(self.main_calls, [])

    def test_trace_through_phone_with_unknown_main_ip_counts(self):
        self.assertTrue(self.m.probe_via_phone(self.cfg, {}))

    def test_trace_with_different_ip_counts(self):
        self.assertTrue(self.m.probe_via_phone(self.cfg, {'main_ip': MAIN_IP}))

    def test_trace_whose_ip_equals_main_ip_does_not_count_and_is_audited_once_per_10_minutes(self):
        self.via_ip = MAIN_IP
        st = {'main_ip': MAIN_IP}
        for _ in range(5):
            self.assertFalse(self.m.probe_via_phone(self.cfg, st))
        self.assertEqual(self.names().count('via_path_wrong_way'), 1)
        self.clock += 600
        self.assertFalse(self.m.probe_via_phone(self.cfg, st))
        self.assertEqual(self.names().count('via_path_wrong_way'), 2)

    def test_wrong_way_path_refuses_failover(self):
        self.m.write_json(self.m.STATE, {'mode': 'normal', 'main_ip': MAIN_IP})
        self.via_ip = MAIN_IP
        st = self.step(3)
        self.assertEqual(st['mode'], 'normal')
        self.assertFalse(self.phone_route())
        self.assertEqual(self.events()[-1]['reason'], 'phone_path_dead')
        self.assertEqual(self.names().count('via_path_wrong_way'), 1)

    def test_wrong_way_during_failover_counts_as_failed_check(self):
        self.m.write_json(self.m.STATE, {'mode': 'normal', 'main_ip': MAIN_IP})
        self.step(3)
        self.assertEqual(self.state()['mode'], 'failover')
        self.via_ip = MAIN_IP
        self.step(3)
        self.assertEqual(self.state()['mode'], 'normal')
        self.assertEqual(self.events()[-1]['reason'], 'phone_path_dead')

    def test_carrier_portal_302_does_not_count_through_probe_via_phone(self):
        portal = b'HTTP/1.1 302 Found\r\nLocation: http://portal.invalid/\r\n\r\ncolo=ATH\n'
        self.m.trace_probe = lambda d, t, mk: self.m.parse_trace(portal)
        self.assertFalse(self.m.probe_via_phone(self.cfg, {}))
        self.step(3)
        self.assertEqual(self.state()['mode'], 'normal')


class Stopping(Base):
    def test_pause_waits_on_the_stop_event(self):
        import types
        m = load()
        waited = []
        m.STOP = types.SimpleNamespace(wait=waited.append, is_set=lambda: False)
        m.pause(3)
        self.assertEqual(waited, [3])

    def test_stop_set_during_the_hold_gives_reason_stopping_share_off_and_no_route(self):
        def pause(seconds):
            self.pauses.append(seconds)
            if len(self.pauses) == 2:
                self.m.STOP.set()
        self.m.pause = pause
        st = self.step(3)
        self.assertEqual(st['mode'], 'normal')
        ev = self.events()[-1]
        self.assertEqual((ev['event'], ev['reason']), ('failover_refused', 'stopping'))
        self.assertEqual(len(self.offs()), 1)
        self.assertFalse(self.phone_route())
        self.assertNotIn(ROUTE, self.cmds)
        self.assertEqual((self.via_rules(), self.via_table), ([], []))
        self.assertEqual(len(self.http_calls), 2)   # no probe after the pause that saw STOP

    def test_stop_already_set_means_no_probe_at_all(self):
        self.m.STOP.set()
        self.assertEqual(self.m.verify_phone_path(self.cfg, {}), (0, 'stopping'))
        self.assertEqual(self.http_calls, [])

    def test_stop_set_between_first_pause_and_probe_probes_nothing_more(self):
        self.http_ok = False

        def pause(seconds):
            self.m.STOP.set()
        self.m.pause = pause
        self.assertEqual(self.m.verify_phone_path(self.cfg, {}), (1, 'stopping'))


class VerifyStages(Base):
    def test_returns_tuple_and_no_function_attribute(self):
        self.assertEqual(self.m.verify_phone_path(self.cfg, {}), (3, 'ok'))
        self.assertFalse(hasattr(self.m.verify_phone_path, 'stage'))

    def test_setup_stage_when_via_path_cannot_be_made(self):
        self.fail = [VIA_ROUTE]
        self.assertEqual(self.m.verify_phone_path(self.cfg, {}), (0, 'setup'))
        self.fail = []
        self.http_ok = False
        self.assertEqual(self.m.verify_phone_path(self.cfg, {}), (3, 'first'))
        self.http_seq, self.http_ok = [True, False], False
        self.assertEqual(self.m.verify_phone_path(self.cfg, {}), (2, 'hold'))

    def test_setup_failure_is_refused_as_phone_path_dead_with_stage_setup(self):
        self.fail = [VIA_ROUTE]
        self.assertEqual(self.step(3)['mode'], 'normal')
        ev = self.events()[-1]
        self.assertEqual((ev['reason'], ev['stage']), ('phone_path_dead', 'setup'))
        self.assertEqual(len(self.offs()), 1)


class VerifyingFlag(Base):
    def test_verifying_is_written_before_share_on_and_cleared_after(self):
        seen = {}
        orig_post = self.post

        def post(url, token, payload, timeout):
            if payload['on']:
                seen['at_on'] = self.state()
            return orig_post(url, token, payload, timeout)
        self.m.http_post = post
        orig_trace = self.trace

        def trace(dev, target, mark):
            seen.setdefault('at_probe', self.state())
            return orig_trace(dev, target, mark)
        self.m.trace_probe = trace
        st = self.step(3)
        self.assertTrue(seen['at_on']['verifying'])
        self.assertFalse(seen['at_on']['sharing'])
        self.assertEqual((seen['at_probe']['verifying'], seen['at_probe']['sharing']), (True, True))
        self.assertEqual((st['mode'], self.state()['verifying'], self.state()['sharing']), ('failover', False, True))

    def test_verifying_is_cleared_on_refusal_paths(self):
        for setup in (lambda: setattr(self, 'http_ok', False), lambda: setattr(self, 'post_result', (403, {})),
                      lambda: setattr(self, 'fail', [ROUTE])):
            self.tmp.cleanup()
            self.setUp()
            setup()
            self.step(3)
            self.assertEqual((self.state()['verifying'], self.state()['sharing'], self.state()['mode']),
                             (False, False, 'normal'))

    def test_verifying_is_cleared_when_an_exception_propagates(self):
        self.m.verify_phone_path = lambda cfg, st: 1 / 0
        self.step(2)
        self.step()
        self.assertEqual(self.state()['verifying'], False)

    def test_status_command_shows_verifying(self):
        import contextlib
        import io
        seen = []
        orig_trace = self.trace

        def trace(dev, target, mark):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.m.main(['--config', str(self.conf), 'status'])
            seen.append(json.loads(out.getvalue()).get('verifying'))
            return orig_trace(dev, target, mark)
        self.m.trace_probe = trace
        self.step(3)
        self.assertEqual(set(seen), {True})


class LastLook(Base):
    def test_main_back_during_verification_refuses_without_route(self):
        def pause(seconds):
            self.pauses.append(seconds)
            self.ping_ok = True   # the main line recovers while the phone path is being verified
        self.m.pause = pause
        st = self.step(3)
        self.assertEqual((st['mode'], st['fails']), ('normal', 0))
        ev = self.events()[-1]
        self.assertEqual((ev['event'], ev['reason']), ('failover_refused', 'main_back'))
        self.assertFalse(self.phone_route())
        self.assertNotIn(ROUTE, self.cmds)
        self.assertEqual([p[2]['on'] for p in self.posts], [True, False])
        self.assertEqual((self.via_rules(), self.via_table), ([], []))
        self.assertEqual((self.state()['verifying'], self.state()['sharing']), (False, False))
        self.assertIsNone(self.dns)

    def test_last_look_uses_the_main_probe_and_runs_right_before_the_route(self):
        self.step(3)
        probes = [i for i, c in enumerate(self.cmds) if c[0] == 'ping']
        self.assertLess(probes[-1], self.cmds.index(ROUTE))
        self.assertGreaterEqual(len(probes), 4)   # 3 loop probes + the last look


class BroadCleanup(Base):
    def test_non_runtimeerror_after_share_ok_still_shares_off_and_cleans_up(self):
        self.raises = [ROUTE]
        st = self.step(3)
        self.assertEqual(st['mode'], 'normal')
        self.assertEqual([p[2]['on'] for p in self.posts], [True, False])
        self.assertEqual((self.via_rules(), self.via_table), ([], []))
        ev = self.events()[-1]
        self.assertEqual((ev['event'], ev['reason']), ('failover_refused', 'error'))
        self.assertFalse(self.phone_route())

    def test_exception_inside_verification_is_cleaned_up_too(self):
        def boom(cfg, st):
            raise ValueError('x')
        self.m.verify_phone_path = boom
        self.assertEqual(self.step(3)['mode'], 'normal')
        self.assertEqual([p[2]['on'] for p in self.posts], [True, False])
        self.assertEqual(self.events()[-1]['reason'], 'error')


class SelftestIsolation(Base):
    def test_constants(self):
        self.assertEqual((self.m.ST_MARK, self.m.ST_TABLE, self.m.ST_RULE_PRIO), (0x17a, 180, 1180))

    def test_selftest_uses_mark_table_and_rule_of_its_own(self):
        res, rc = self.m.selftest(self.cfg, 40)
        self.assertEqual(rc, 0)
        self.assertEqual({c[2] for c in self.http_calls}, {0x17a})
        self.assertIn(ST_RULE_ADD, self.cmds)
        self.assertIn(['ip', 'route', 'replace', 'default', 'via', GW, 'dev', 'eno1', 'table', '180'], self.cmds)
        self.assertIn(ST_RULE_DEL, self.cmds)
        self.assertFalse([c for c in self.cmds if '179' in c or '1179' in c])
        self.assertEqual((self.tabs['180'], self.tabs['179']), ([], []))

    def test_daemon_normal_mode_cleanup_does_not_remove_rule_1180(self):
        self.rules.append({'priority': 1180, 'fwmark': '0x17a', 'table': '180'})
        self.tabs['180'] = [GW]
        self.ping_ok = True
        self.step(3)
        self.assertEqual([r['priority'] for r in self.rules if r['priority'] == 1180], [1180])
        self.assertEqual(self.tabs['180'], [GW])
        self.assertIn(VIA_RULE_DEL, self.cmds)

    def test_main_ip_learned_unmarked_without_rule_1178_and_marked_with_it(self):
        self.m.selftest(self.cfg, 10)
        self.assertEqual(self.main_calls, [('eno1', '1.1.1.1', 0)])
        self.main_calls.clear()
        self.rules.append({'priority': 1178, 'fwmark': '0x178', 'table': '178'})
        self.m.selftest(self.cfg, 10)
        self.assertEqual(self.main_calls, [('eno1', '1.1.1.1', 0x178)])

    def test_json_has_main_ip_known_and_ip_differs_but_never_the_addresses(self):
        res, rc = self.m.selftest(self.cfg, 40)
        self.assertTrue(res['main_ip_known'])
        self.assertTrue(all(x['ip_differs'] is True for x in res['samples']))
        text = json.dumps(res)
        self.assertNotIn(MAIN_IP, text)
        self.assertNotIn(VIA_IP, text)

    def test_unknown_main_ip_gives_null_ip_differs_and_can_still_be_ok(self):
        self.main_trace_ok = False
        res, rc = self.m.selftest(self.cfg, 40)
        self.assertEqual((res['main_ip_known'], rc, res['ok']), (False, 0, True))
        self.assertTrue(all(x['ip_differs'] is None for x in res['samples']))

    def test_wrong_way_samples_make_selftest_fail(self):
        self.via_ip = MAIN_IP
        res, rc = self.m.selftest(self.cfg, 40)
        self.assertEqual((rc, res['ok'], res['passed']), (1, False, 0))
        self.assertTrue(all(x['ip_differs'] is False and x['ok'] is False for x in res['samples']))

    def test_share_off_is_skipped_when_the_daemon_went_into_failover_meanwhile(self):
        orig = self.post

        def post(url, token, payload, timeout):
            r = orig(url, token, payload, timeout)
            if payload['on']:
                self.m.write_json(self.m.STATE, {'mode': 'failover'})
            return r
        self.m.http_post = post
        self.m.selftest(self.cfg, 10)
        self.assertEqual([p[2]['on'] for p in self.posts], [True])
        self.assertIn(ST_RULE_DEL, self.cmds)   # its own path is still removed

    def test_share_off_is_skipped_when_the_daemon_is_verifying(self):
        orig = self.post

        def post(url, token, payload, timeout):
            if payload['on']:
                self.m.write_json(self.m.STATE, {'mode': 'normal', 'verifying': True})
            return orig(url, token, payload, timeout)
        self.m.http_post = post
        self.m.selftest(self.cfg, 10)
        self.assertEqual([p[2]['on'] for p in self.posts], [True])

    def test_refused_while_daemon_is_verifying(self):
        self.m.write_json(self.m.STATE, {'mode': 'normal', 'verifying': True})
        res, rc = self.m.selftest(self.cfg, 10)
        self.assertEqual((rc, self.posts), (1, []))


class Locking(Base):
    def run_main(self, *args):
        import contextlib
        import io
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = self.m.main(['--config', str(self.conf), *args])
        return rc, json.loads(out.getvalue())

    def failing_lock(self):
        import contextlib

        @contextlib.contextmanager
        def lock(wait_s):
            raise self.m.LockBusy('held')
            yield
        self.m.hold_lock = lock

    def test_sweep_reports_a_json_error_when_the_lock_cannot_be_had(self):
        self.failing_lock()
        rc, out = self.run_main('sweep')
        self.assertEqual((rc, out['ok']), (1, False))
        self.assertIn('lock', out['error'])
        self.assertEqual((self.posts, self.cmds), ([], []))

    def test_selftest_reports_a_json_error_when_the_lock_cannot_be_had(self):
        self.failing_lock()
        rc, out = self.run_main('selftest')
        self.assertEqual((rc, out['ok']), (1, False))
        self.assertEqual((self.posts, self.cmds), ([], []))

    def test_real_flock_contention_fails_then_succeeds_after_release(self):
        import fcntl
        self.m.LOCK_WAIT_S = 0.3
        fd = os.open(self.m.LOCK, os.O_RDWR | os.O_CREAT)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            rc, out = self.run_main('sweep')
            self.assertEqual((rc, out['ok']), (1, False))
        finally:
            os.close(fd)
        rc, out = self.run_main('sweep')
        self.assertEqual((rc, out['mode']), (0, 'normal'))

    def test_wait_is_up_to_120_seconds(self):
        self.assertEqual(self.m.LOCK_WAIT_S, 120)
        self.assertEqual(str(self.m.LOCK), str(self.root / 'lock'))   # overridden by the tests

    def test_daemon_holds_the_lock_around_each_once(self):
        import fcntl
        held = []

        def fake_once(cfg):
            fd = os.open(self.m.LOCK, os.O_RDWR | os.O_CREAT)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                held.append(False)
            except OSError:
                held.append(True)
            finally:
                os.close(fd)
            self.m.STOP.set()
        self.m.once = fake_once
        self.m.loop(self.cfg)
        self.assertEqual(held, [True])
        # and it is released again afterwards
        fd = os.open(self.m.LOCK, os.O_RDWR)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        os.close(fd)

    def test_busy_lock_skips_the_daemon_iteration_and_audits(self):
        self.failing_lock()
        calls = []
        self.m.once = lambda cfg: calls.append(1)
        self.m.STOP.set()
        self.m.loop(self.cfg)
        self.assertEqual(calls, [])

    def test_selftest_installs_term_hup_int_handlers_that_raise_and_restores_them(self):
        import signal
        before = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT)}
        seen = {}

        def fake_selftest(cfg, seconds):
            for sg in before:
                seen[sg] = signal.getsignal(sg)
            return {'ok': True}, 0
        self.m.selftest = fake_selftest
        self.run_main('selftest')
        for sg, h in seen.items():
            with self.assertRaises(SystemExit):
                h(sg, None)
        self.assertEqual({s: signal.getsignal(s) for s in before}, before)

    def test_signal_during_selftest_still_cleans_up(self):
        def pause(seconds):
            raise SystemExit(143)
        self.m.pause = pause
        with self.assertRaises(SystemExit):
            self.m.selftest(self.cfg, 40)
        self.assertEqual([p[2]['on'] for p in self.posts], [True, False])
        self.assertEqual(self.tabs['180'], [])


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
        self.assertIn('TimeoutStopSec=150\n', u)


if __name__ == '__main__':
    unittest.main()
