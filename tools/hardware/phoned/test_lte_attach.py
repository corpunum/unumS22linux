#!/usr/bin/env python3
"""LTE initial-attach profile at modem start, against the fake CP; never opens phone devices."""
import importlib.util
import json
from pathlib import Path
import tempfile
import time
import unittest

spec = importlib.util.spec_from_file_location('phoned', Path(__file__).with_name('s22-phoned.py'))
P = importlib.util.module_from_spec(spec)
spec.loader.exec_module(P)


class LteAttachAtStartTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.daemon = None

    def tearDown(self):
        if self.daemon:
            self.daemon.stop()
        self.tmp.cleanup()

    def start(self, config=None, reject=None, **kw):
        if config is not None:
            Path(self.tmp.name, P.LTE_ATTACH_CONFIG).write_text(config)
        self.daemon = P.Daemon(state_dir=self.tmp.name, simulate=True, socket_path='', port=None,
                               sim_kwargs={'reject': reject} if reject else None, **kw).start()
        ph = self.daemon.phone
        deadline = time.monotonic() + 5
        while (ph.lte_attach_status is None or not ph.sms_ready) and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(ph.sms_ready, 'SMS setup must complete')
        self.assertIsNotNone(ph.lte_attach_status, 'attach must be attempted')
        return ph

    def attach_frames(self):
        return [f for f in self.daemon.fake['cp'].rx if f.cmd == P.GPRS_LTE_ATTACH_APN_INFO]

    def logged(self):
        rows = [json.loads(l) for l in Path(self.tmp.name, 'ipc.jsonl').read_text().splitlines()]
        return [r for r in rows if r.get('event') == 'lte_attach']

    def test_default_sends_vodafone_profile_once_after_sms_ready(self):
        ph = self.start()
        frames = self.attach_frames()
        self.assertEqual([f.type for f in frames], [P.SET])
        self.assertEqual(frames[0].data, P.lte_attach_apn_body('internet.vodafone.gr'))
        rx = self.daemon.fake['cp'].rx
        self.assertLess(rx.index(next(f for f in rx if f.cmd == P.SMS_DEVICE_READY)), rx.index(frames[0]))
        self.assertEqual(ph.lte_attach_status['result'], 'accepted')
        self.assertEqual(ph.status()['lte_attach']['apn'], 'internet.vodafone.gr')
        # idempotent: more polls / SIM notifications do not resend on the same modem start
        ph.poll()
        ph.send_lte_attach()
        time.sleep(0.2)
        self.assertEqual(len(self.attach_frames()), 1)
        self.assertEqual(len(self.logged()), 1)

    def test_new_modem_start_sends_again(self):
        ph = self.start()
        ph.on_link(False)
        ph.on_link(True)
        deadline = time.monotonic() + 3
        while len(self.attach_frames()) < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(len(self.attach_frames()), 2)

    def test_config_file_apn_and_disable(self):
        self.start(config='{"enabled": true, "apn": "test.apn"}')
        self.assertEqual(self.attach_frames()[0].data[3:104].rstrip(b'\0'), b'test.apn')
        self.daemon.stop()
        self.tearDown()
        self.setUp()
        ph = self.start(config='{"enabled": false}')
        self.assertEqual(self.attach_frames(), [])
        self.assertEqual(ph.lte_attach_status['result'], 'disabled')

    def test_cli_override_and_bad_config(self):
        ph = self.start(config='not json', lte_attach_apn='cli.apn')
        self.assertIn('config_error', ph.lte_attach_status)
        self.assertEqual(self.attach_frames()[0].data[3:104].rstrip(b'\0'), b'cli.apn')

    def test_rejection_and_bad_apn_never_block_sms(self):
        ph = self.start(reject={P.GPRS_LTE_ATTACH_APN_INFO: 0x8010})
        self.assertEqual(ph.lte_attach_status['result'], 'rejected')
        self.assertTrue(ph.sms_ready)
        self.assertTrue(ph.status()['ok'])
        self.daemon.stop()
        self.tearDown()
        self.setUp()
        ph = self.start(config='{"apn": "bad apn"}')
        self.assertEqual(ph.lte_attach_status['result'], 'error')
        self.assertEqual(self.attach_frames(), [])
        self.assertTrue(ph.sms_ready)

    def test_not_sent_before_sim_usable(self):
        self.daemon = P.Daemon(state_dir=self.tmp.name, simulate=True, socket_path='', port=None,
                               sim_kwargs={'sim_status': 3}).start()   # PIN-locked, no auto PIN file
        time.sleep(0.5)
        self.assertEqual(self.attach_frames(), [])


if __name__ == '__main__':
    unittest.main()
