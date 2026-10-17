#!/usr/bin/env python3
"""mobile_data up/down/status against the simulated CP; never opens phone devices or runs ip."""
import importlib.util
import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import time
import unittest

spec = importlib.util.spec_from_file_location('phoned', Path(__file__).with_name('s22-phoned.py'))
P = importlib.util.module_from_spec(spec)
spec.loader.exec_module(P)

# Exact frames the live CP accepted on 2026-10-17 (RECON.md, S2 live run); mseq byte zeroed.
LIVE_DEFINE = bytes.fromhex('480000000d01037b2261705f70636f223a5b305d2c2261706e223a22696e7465726e65742e766f6461666f6e652e6772222c22636964223a312c227064705f74797065223a327d0a')
LIVE_UP = bytes.fromhex('340000000d04037b22617574685f74797065223a302c22636964223a312c22726561736f6e223a302c227374617465223a317d0a')
LIVE_DOWN = bytes.fromhex('340000000d04037b22617574685f74797065223a302c22636964223a312c22726561736f6e223a302c227374617465223a307d0a')


class Ip:
    def __init__(self, fail_on=None):
        self.calls, self.fail_on = [], fail_on

    def __call__(self, *args):
        self.calls.append(args)
        return (False, 'boom') if self.fail_on and self.fail_on in args else (True, '')


def frame(cmd, data):
    return P.Frame(0, 0, cmd[0], cmd[1], P.SET, data).encode()


class BodiesTest(unittest.TestCase):
    def test_builders_match_the_frames_the_live_modem_accepted(self):
        self.assertEqual(frame(P.GPRS_DEFINE_PDP_CONTEXT, P.define_pdp_body(1, 'internet.vodafone.gr')), LIVE_DEFINE)
        self.assertEqual(frame(P.GPRS_PDP_CONTEXT, P.pdp_state_body(1, True)), LIVE_UP)
        self.assertEqual(frame(P.GPRS_PDP_CONTEXT, P.pdp_state_body(1, False)), LIVE_DOWN)
        self.assertTrue(P.is_pdp_deactivate(P.pdp_state_body(1, False)))
        self.assertFalse(P.is_pdp_deactivate(P.pdp_state_body(1, True)))

    def test_apn_validation(self):
        for bad in ('', 'a"b', 'x' * 101, 'a b', '-lead'):
            with self.assertRaises(ValueError):
                P.define_pdp_body(1, bad)

    def test_lte_attach_apn_layout_matches_libsamsung_ipc(self):
        # Offsets asserted by libsamsung-ipc (manoedinata fork) samsung-ipc/tests/shannon_data.c:116-123.
        b = P.lte_attach_apn_body('internet.vodafone.gr')
        self.assertEqual(len(b), 194)
        self.assertEqual(b[:3], bytes([1, 1, P.GPRS_PROTOCOL_IPV4]))      # type, profile_id, protocol
        self.assertEqual(b[3:104].rstrip(b'\0'), b'internet.vodafone.gr')  # apn[101] at 3
        self.assertEqual(b[127:191], bytes(64))                           # username @127, password @159
        self.assertEqual(b[191:], bytes([0, P.GPRS_PROTOCOL_IPV4, 0]))     # auth @191, protocol_roaming, unknown5
        self.assertEqual(b[104:127], bytes(23))
        fr = frame(P.GPRS_LTE_ATTACH_APN_INFO, b)
        self.assertEqual(fr[:7], bytes.fromhex('c90000000d1403'))
        self.assertIn(P.GPRS_LTE_ATTACH_APN_INFO, P.DATA_SET_ALLOWLIST)
        # empty APN (network-chosen default bearer) is the rollback value
        self.assertEqual(P.lte_attach_apn_body('')[3:104], bytes(101))
        for bad in ('a"b', 'x' * 101, 'a b'):
            with self.assertRaises(ValueError):
                P.lte_attach_apn_body(bad)


class MobileDataTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.net = Path(self.tmp.name, 'net')
        stats = self.net / 'rmnet0' / 'statistics'
        stats.mkdir(parents=True)
        (stats / 'rx_bytes').write_text('1234\n')
        (stats / 'tx_bytes').write_text('567\n')
        (self.net / 'rmnet0' / 'operstate').write_text('unknown\n')
        self.ip = Ip()
        self.daemon = None

    def tearDown(self):
        if self.daemon:
            self.daemon.stop()
        self.tmp.cleanup()

    def start(self, **kw):
        self.daemon = P.Daemon(state_dir=self.tmp.name, simulate=True, socket_path=str(Path(self.tmp.name, 'p.sock')),
                               port=0, ip_runner=self.ip, net_sys=str(self.net), **kw).start()
        deadline = time.monotonic() + 3
        while not self.daemon.phone.sms_ready and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(self.daemon.phone.sms_ready)
        return self.daemon.phone

    def cp_rx(self, cmd):
        return [f for f in self.daemon.fake['cp'].rx if f.cmd == cmd]

    def test_up_configures_rmnet_as_fallback_and_down_releases(self):
        ph = self.start()
        code, st = ph.mobile_data_up()
        self.assertEqual(code, 200, st)
        self.assertEqual((st['state'], st['ipv4'], st['interface']), ('up', '10.20.30.40', 'rmnet0'))
        self.assertEqual(st['dns'], ['192.0.2.53', '192.0.2.54'])
        self.assertEqual((st['rx_bytes'], st['tx_bytes'], st['call_state']), (1234, 567, 1))
        self.assertEqual([f.data for f in self.cp_rx(P.GPRS_DEFINE_PDP_CONTEXT)], [LIVE_DEFINE[7:]])
        self.assertEqual([f.data for f in self.cp_rx(P.GPRS_PDP_CONTEXT)], [LIVE_UP[7:]])
        self.assertIn(('addr', 'add', '10.20.30.40/32', 'dev', 'rmnet0'), self.ip.calls)
        self.assertIn(('link', 'set', 'rmnet0', 'mtu', '1500', 'up'), self.ip.calls)
        self.assertIn(('route', 'replace', 'default', 'dev', 'rmnet0', 'metric', '9000'), self.ip.calls)
        # Never touches the management links or the default route of other devices.
        for call in self.ip.calls:
            self.assertNotIn('ecm0', call)
            self.assertNotIn('wlan0', call)
            self.assertNotIn('tailscale0', call)
        self.assertEqual(ph.mobile_data_up()[0], 200)      # idempotent: no second activation
        self.assertEqual(len(self.cp_rx(P.GPRS_PDP_CONTEXT)), 1)
        self.ip.calls.clear()
        code, st = ph.mobile_data_down()
        self.assertEqual(code, 200, st)
        self.assertEqual((st['state'], st['ipv4'], st['call_state']), ('down', None, 3))
        self.assertEqual(self.cp_rx(P.GPRS_PDP_CONTEXT)[-1].data, LIVE_DOWN[7:])
        self.assertEqual(self.ip.calls[0], ('route', 'del', 'default', 'dev', 'rmnet0', 'metric', '9000'))
        self.assertIn(('link', 'set', 'rmnet0', 'down'), self.ip.calls)
        code, st = ph.mobile_data_up()                      # re-activation works
        self.assertEqual((code, st['state']), (200, 'up'))

    def test_modem_refusal_is_reported_and_rolled_back(self):
        ph = self.start()
        cp = self.daemon.fake['cp']
        orig = cp.handle

        def refuse_define(fr):
            if fr.cmd == P.GPRS_DEFINE_PDP_CONTEXT:
                return cp.gen(fr, 0x8010)
            return orig(fr)
        cp.handle = refuse_define
        code, st = ph.mobile_data_up()
        self.assertEqual(code, 502)
        self.assertIn('0x8010', st['error'])
        self.assertEqual(st['state'], 'down')
        self.assertNotIn(('addr', 'add', '10.20.30.40/32', 'dev', 'rmnet0'), self.ip.calls)
        self.assertEqual([f.data for f in self.cp_rx(P.GPRS_PDP_CONTEXT)], [LIVE_DOWN[7:]])

    def test_missing_ip_configuration_times_out_and_deactivates(self):
        ph = self.start(data_timeout=0.3)
        cp = self.daemon.fake['cp']
        orig = cp.handle

        def silent_activate(fr):
            if fr.cmd == P.GPRS_PDP_CONTEXT and json.loads(fr.data)['state'] == 1:
                return cp.gen(fr)
            return orig(fr)
        cp.handle = silent_activate
        code, st = ph.mobile_data_up()
        self.assertEqual(code, 502)
        self.assertIn('no IP configuration', st['error'])
        self.assertEqual(self.cp_rx(P.GPRS_PDP_CONTEXT)[-1].data, LIVE_DOWN[7:])

    def test_ip_failure_rolls_back(self):
        self.ip.fail_on = 'replace'
        ph = self.start()
        code, st = ph.mobile_data_up()
        self.assertEqual(code, 502)
        self.assertIn('ip route replace', st['error'])
        self.assertIn(('link', 'set', 'rmnet0', 'down'), self.ip.calls)

    def test_network_release_tears_down_interface(self):
        ph = self.start()
        self.assertEqual(ph.mobile_data_up()[0], 200)
        self.ip.calls.clear()
        self.daemon.fake['cp'].out(P.GPRS_CALL_STATUS, P.NOTI, json.dumps(
            {'cid': 1, 'data_call_state': 3, 'data_callstate_rsn': 36}).encode())
        deadline = time.monotonic() + 3
        while ('link', 'set', 'rmnet0', 'down') not in self.ip.calls and time.monotonic() < deadline:
            time.sleep(0.02)
        st = ph.mobile_data_status()
        self.assertEqual((st['state'], st['call_state']), ('released_by_network', 3))
        self.assertIn(('link', 'set', 'rmnet0', 'down'), self.ip.calls)

    def test_input_and_state_gates(self):
        ph = self.start()
        self.assertEqual(ph.mobile_data_up(apn='bad apn')[0], 400)
        self.assertEqual(ph.mobile_data_up(cid=9)[0], 400)
        self.assertEqual(ph.mobile_data_down(cid=0)[0], 400)
        ph.data_lock.acquire()
        self.assertEqual(ph.mobile_data_up()[0], 409)
        ph.data_lock.release()
        ph.simulate = False
        ph.modem_state_path = str(Path(self.tmp.name, 'absent'))
        self.assertEqual(ph.mobile_data_up()[0], 409)
        self.assertEqual(self.cp_rx(P.GPRS_DEFINE_PDP_CONTEXT), [])

    def test_http_and_cli(self):
        self.start()
        sock = self.daemon.socket_path
        for action, state in (('up', 'up'), ('status', 'up'), ('down', 'down')):
            run = subprocess.run([os.sys.executable, P.__file__, 'mobile-data', action, '--socket', sock],
                                 capture_output=True, text=True, timeout=60)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            self.assertEqual(json.loads(run.stdout)['state'], state)


if __name__ == '__main__':
    unittest.main()
