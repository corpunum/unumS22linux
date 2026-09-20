#!/usr/bin/env python3
import importlib.util, json, tempfile, unittest
from pathlib import Path
from unittest import mock

spec=importlib.util.spec_from_file_location('wifi_auto',Path(__file__).with_name('wifi-autostart.py'))
w=importlib.util.module_from_spec(spec); spec.loader.exec_module(w)

class FakeHarness:
    def responder_ready(self): return {}
    def preflight(self, **kwargs): return None
    def activate(self): return None

class AutoTests(unittest.TestCase):
    def test_attempt_gate_states_and_boot(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'attempt'; old=w.ATTEMPT; w.ATTEMPT=p
            try:
                for state in ('starting','failed'):
                    p.write_text(json.dumps({'state':state,'boot_id':'old'}))
                    with self.assertRaises(RuntimeError): w.check_attempt()
                p.write_text(json.dumps({'state':'complete','boot_id':'same'}))
                with mock.patch.object(w,'boot_id',return_value='same'), self.assertRaises(RuntimeError): w.check_attempt()
                with mock.patch.object(w,'boot_id',return_value='new'): w.check_attempt()
            finally: w.ATTEMPT=old

    def test_default_and_check_are_read_only(self):
        with mock.patch.object(w,'check_base') as check, mock.patch.object(w,'start') as start:
            self.assertEqual(w.main([]),0); self.assertEqual(w.main(['--check']),0)
            self.assertEqual(check.call_count,2); start.assert_not_called()

    def test_atomic_attempt_mode_and_fields(self):
        with tempfile.TemporaryDirectory() as d:
            oldp,olda,oldboot=w.PRIVATE,w.ATTEMPT,w.boot_id; w.PRIVATE=Path(d); w.ATTEMPT=Path(d)/'attempt.json'
            try:
                with mock.patch.object(w,'boot_id',return_value='boot-x'), mock.patch.object(w.time,'monotonic',return_value=3.5): w.atomic_attempt('starting')
                obj=json.loads(w.ATTEMPT.read_text()); self.assertEqual(obj['boot_id'],'boot-x'); self.assertEqual(obj['state'],'starting'); self.assertEqual(w.ATTEMPT.stat().st_mode&0o777,0o600)
            finally: w.PRIVATE,w.ATTEMPT,w.boot_id=oldp,olda,oldboot

    def test_start_order_and_complete_when_ap_absent(self):
        with tempfile.TemporaryDirectory() as d:
            oldlink=w.LINKDOWN; w.LINKDOWN=Path(d)/'linkdown'; w.LINKDOWN.write_text('0'); calls=[]; records=[]
            try:
                with mock.patch.object(w,'check_base'), mock.patch.object(w,'atomic_attempt',side_effect=lambda s,**k: records.append(s)), mock.patch.object(w,'harness_module',return_value=FakeHarness()), mock.patch.object(w,'ensure_debugfs'), mock.patch.object(w,'start_daemon',side_effect=lambda n,c: calls.append((n,c))), mock.patch.object(w,'wait_for_association',return_value=False), mock.patch.object(w,'require_process') as req: w.start()
                self.assertEqual(records,['starting','complete']); self.assertEqual([x[0] for x in calls],['optional-firmware','wpa','udhcpc']); self.assertEqual(w.LINKDOWN.read_text().strip(),'1'); self.assertTrue(req.called)
            finally: w.LINKDOWN=oldlink

    def test_durable_start_precedes_radio_and_completion_follows_process_checks(self):
        steps = []
        class Ordered(FakeHarness):
            def responder_ready(self): steps.append('responder-ready')
            def preflight(self, **kwargs):
                self_test.assertEqual(kwargs, {'require_usb': False})
                steps.append('preflight')
            def activate(self): steps.append('activate')
        self_test = self
        with tempfile.TemporaryDirectory() as d, \
             mock.patch.object(w, 'LINKDOWN', Path(d) / 'linkdown'), \
             mock.patch.object(w, 'check_base'), \
             mock.patch.object(w, 'atomic_attempt', side_effect=lambda state, **kw: steps.append(state)), \
             mock.patch.object(w, 'ensure_debugfs'), \
             mock.patch.object(w, 'harness_module', return_value=Ordered()), \
             mock.patch.object(w, 'start_daemon', side_effect=lambda name, cmd: steps.append(name)), \
             mock.patch.object(w, 'wait_for_association', return_value=True), \
             mock.patch.object(w, 'require_process', side_effect=lambda name, cmd: steps.append('checked-' + name)):
            w.start()
        self.assertEqual(steps, ['starting', 'optional-firmware', 'responder-ready',
                                'preflight', 'activate', 'wpa', 'udhcpc',
                                'responder-ready', 'checked-optional-firmware',
                                'checked-wpa', 'checked-udhcpc', 'complete'])

    def test_activation_failure_records_failed_without_cleanup(self):
        records=[]
        class Broken(FakeHarness):
            def activate(self): raise RuntimeError('activation')
        with mock.patch.object(w,'check_base'), mock.patch.object(w,'atomic_attempt',side_effect=lambda s,**k: records.append(s)), mock.patch.object(w,'harness_module',return_value=Broken()), mock.patch.object(w,'ensure_debugfs'), mock.patch.object(w,'start_daemon') as daemon:
            with self.assertRaises(RuntimeError): w.start()
        self.assertEqual(records,['starting','failed']); self.assertEqual(daemon.call_count,1)

    def test_require_process_exact_argv_and_uid(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); pid='77'; (root/pid).mkdir(); (root/pid/'cmdline').write_bytes(b'/sbin/wpa_supplicant\0-i\0wlan0\0'); (root/pid/'status').write_text('Uid:\t0\t0\t0\t0\n'); pf=root/'wpa.pid'; pf.write_text(pid)
            oldp,oldf=w.PROC,w.PIDFILES; w.PROC=root; w.PIDFILES={'wpa':pf}
            try:
                self.assertEqual(w.require_process('wpa',['/sbin/wpa_supplicant','-i','wlan0']),77)
                with self.assertRaises(RuntimeError):
                    w.require_process('wpa', ['/sbin/wpa_supplicant', '-i', 'ecm0'])
                (root/pid/'status').write_text('Uid:\t0\t1000\t0\t0\n')
                with self.assertRaises(RuntimeError):
                    w.require_process('wpa',['/sbin/wpa_supplicant','-i','wlan0'])
            finally: w.PROC,w.PIDFILES=oldp,oldf

if __name__=='__main__': unittest.main()
