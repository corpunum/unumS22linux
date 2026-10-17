#!/usr/bin/env python3
"""Experimental data controls, synthetic IPC only; never opens phone devices."""
import importlib.util
import json
import os
from pathlib import Path
import struct
import tempfile
import threading
import time
import unittest

spec = importlib.util.spec_from_file_location('phoned', Path(__file__).with_name('s22-phoned.py'))
P = importlib.util.module_from_spec(spec)
spec.loader.exec_module(P)

class Log:
    def __init__(self):
        self.rows = []
    def write(self, row):
        self.rows.append(row)

class ExperimentalIpcTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name, 'modem-state')
        self.state.write_text('ONLINE')
        self.ph = P.Phone(self.tmp.name, modem_state_path=str(self.state))
        self.log = Log()
        self.r, self.w = os.pipe()
        self.link = P.Link(None, self.log, self.ph.handle_frame)
        self.link.ipc = self.w
        self.ph.attach(self.link)
    def tearDown(self):
        for fd in (self.r, self.w, self.link.wake_r, self.link.wake_w):
            os.close(fd)
        self.tmp.cleanup()
    def transact(self, args, code=P.GEN_OK, response=None):
        received = []
        def modem():
            raw = os.read(self.r, 8192)
            received.append(raw)
            fr = P.FrameDecoder().feed(raw)[0]
            if response is not None:
                self.link._dispatch(P.Frame(8, fr.mseq, fr.group, fr.index, P.RESP, response))
            else:
                payload = bytes([fr.group, fr.index, fr.type]) + struct.pack('<H', code)
                self.link._dispatch(P.Frame(8, fr.mseq, *P.GEN_PHONE_RES, P.RESP, payload))
        t = threading.Thread(target=modem)
        t.start()
        result = self.ph.ipc_send(*args)
        t.join(2)
        self.assertFalse(t.is_alive())
        self.assertEqual(len(received), 1)
        self.assertEqual(result[1]['request_hex'], received[0].hex())
        return result, received[0]
    UP = '{"auth_type":0,"cid":1,"reason":0,"state":1}\n'
    DOWN = '{"auth_type":0,"cid":1,"reason":0,"state":0}\n'

    def test_exact_activate_deactivate_and_gen_rejection(self):
        (status, result), raw = self.transact((13, 4, 3, self.UP))
        self.assertEqual(raw[4:7], b'\x0d\x04\x03')
        self.assertEqual(raw[7:], self.UP.encode())
        self.assertEqual(raw[0] | raw[1] << 8, 7 + len(self.UP))
        self.assertEqual(status, 200)
        self.assertTrue(result['ok'])
        self.assertFalse(result['timed_out'])
        self.assertEqual(result['gen_phone_res']['code'], 0x8000)
        self.ph.last_ipc_send = time.monotonic()   # deactivate bypasses the cooldown
        (status, result), raw = self.transact((13, 4, 3, self.DOWN), code=0x8010)
        self.assertEqual(raw[7:], self.DOWN.encode())
        self.assertFalse(result['ok'])
        self.assertTrue(result['gen_response_hex'].endswith('8001020d04031080'))
        self.assertEqual(len([r for r in self.log.rows if r.get('dir') == 'tx']), 2)

    def test_json_cmd_list_get_requires_exact_stock_body(self):
        self.assertEqual(self.ph.ipc_send(15, 0x3b, 2, '{"jver":"2.0"}\n')[0], 403)
        self.assertEqual(self.ph.ipc_send(15, 0x3b, 3, '{"jver":"1.0"}\n')[0], 403)
        (status, result), raw = self.transact((15, 0x3b, 2, '{"jver":"1.0"}\n'),
                                              response=b'{"gprs":[[1,4]]}')
        self.assertEqual(raw[:2], b'\x16\x00')
        self.assertEqual(raw[4:], b'\x0f\x3b\x02{"jver":"1.0"}\n')
        self.assertEqual(result['response']['decoded']['json'], {'gprs': [[1, 4]]})

    def test_removed_call_release_frame_is_refused(self):
        for payload in ('01', '02'):
            self.assertEqual(self.ph.ipc_send(2, 3, 3, payload)[0], 403)
            self.assertEqual(self.ph.ipc_send(3, 2, 3, payload)[0], 403)
        self.assertEqual(self.log.rows, [])

    def test_json_bytes_preserved_and_response(self):
        payload = '{"mode":1,"mobile_data_enabled":1,"roaming_data_enabled":0}'
        (_, result), raw = self.transact((13, 29, 3, payload), response=b'{"result":0}')
        self.assertEqual(raw[7:], payload.encode())
        self.assertTrue(result['ok'])
        self.assertEqual(result['response']['decoded']['json'], {'result': 0})
    def test_does_not_bypass_call_sms_or_sim_gates(self):
        for args in ((2, 1, 1, '01'), (2, 3, 1, ''), (2, 3, 3, '00'),
                     (4, 1, 1, '00'), (5, 1, 3, '00'), (8, 10, 3, '4b')):
            self.assertEqual(self.ph.ipc_send(*args)[0], 403)
        for args in ((256, 1, 3, ''), (13, 20, 3, '{bad'), (13, 20, 3, 'xy')):
            self.assertEqual(self.ph.ipc_send(*args)[0], 400)
        self.assertEqual(self.log.rows, [])
    def test_cooldown_offline_and_busy_do_not_send(self):
        self.ph.last_ipc_send = time.monotonic()
        self.assertEqual(self.ph.ipc_send(13, 20, 3, '{}')[0], 429)
        self.ph.last_ipc_send = None
        self.state.write_text('OFFLINE')
        self.assertEqual(self.ph.ipc_send(13, 20, 3, '{}')[0], 409)
        self.ph.ipc_send_lock.acquire()
        self.assertEqual(self.ph.ipc_send(13, 4, 3, self.DOWN)[0], 409)
        self.ph.ipc_send_lock.release()
        self.assertEqual(self.log.rows, [])
    def test_strict_sequence_inner_command_and_type_matching(self):
        p = P.Pending((13, 20), False, strict=True, typ=3)
        self.link.pending[5] = p
        for aseq, cmd, typ in ((0, (13, 20), 3), (6, (13, 20), 3),
                               (5, (13, 27), 3), (5, (13, 20), 2)):
            body = bytes([*cmd, typ]) + b'\x00\x80'
            self.link._dispatch(P.Frame(3, aseq, 128, 1, 2, body))
            self.assertFalse(p.event.is_set())
        self.link._dispatch(P.Frame(3, 0, 13, 20, 2, b'{}'))
        self.assertFalse(p.event.is_set())
        self.link._dispatch(P.Frame(3, 5, 13, 27, 2, b'{}'))
        self.assertFalse(p.event.is_set())
        self.link._dispatch(P.Frame(3, 5, 128, 1, 2, b'\x0d\x14\x03\x00\x80'))
        self.assertTrue(p.event.is_set())
        self.assertTrue(p.ok)
    def test_timeout_retains_actual_tx_no_retry(self):
        pending = self.link.request((13, 20), 3, b'{}', timeout=0.01, strict=True)
        self.assertFalse(pending.completed)
        self.assertFalse(pending.ok)
        self.assertEqual(pending.tx, os.read(self.r, 8192))
        self.assertEqual(self.link.pending, {})
        self.assertEqual(len(self.log.rows), 1)
    def test_gen_wrong_wire_type_and_sequence_exhaustion(self):
        pending = P.Pending((13, 20), False, strict=True, typ=3)
        self.link.pending[5] = pending
        self.link._dispatch(P.Frame(3, 5, 128, 1, P.INDI, b'\x0d\x14\x03\x00\x80'))
        self.assertFalse(pending.event.is_set())
        self.link.pending = {n: pending for n in range(1, 256)}
        with self.assertRaises(P.LinkDown):
            self.link.send((13, 20), 3, b'{}')
        self.assertEqual(len(self.link.pending), 255)
        self.assertFalse(any(row.get('dir') == 'tx' for row in self.log.rows))

    def test_get_success_ack_does_not_finish_before_response(self):
        pending = P.Pending((13, 4), True, strict=True, typ=2)
        self.link.pending[5] = pending
        self.link._dispatch(P.Frame(3, 5, 128, 1, 2, b'\x0d\x04\x02\x00\x80'))
        self.assertFalse(pending.event.is_set())
        self.link._dispatch(P.Frame(4, 5, 13, 4, 2, b'{"num_act":0,"records":[]}'))
        self.assertTrue(pending.event.is_set())
        self.assertIsNotNone(pending.resp)

    def test_http_and_cli_use_the_same_guarded_sender(self):
        import subprocess
        with tempfile.TemporaryDirectory() as tmp:
            daemon = P.Daemon(state_dir=tmp, simulate=True, socket_path=str(Path(tmp, 'p.sock')), port=0).start()
            try:
                deadline = time.monotonic() + 3
                while not daemon.phone.sms_ready and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertTrue(daemon.phone.sms_ready)
                for body in (self.UP, self.DOWN):
                    typed = body.replace('\n', '\\n')        # what an operator types on the CLI
                    run = subprocess.run([os.sys.executable, P.__file__, 'ipc-send', '0x0d', '4', '3', typed,
                                          '--socket', daemon.socket_path],
                                         capture_output=True, text=True, timeout=12)
                    result = json.loads(run.stdout)
                    self.assertEqual(bytes.fromhex(result['request_hex'])[4:], b'\x0d\x04\x03' + body.encode())
                    self.assertFalse(result['timed_out'], result)
            finally:
                daemon.stop()

    def test_data_notifications_preserve_json_binary_and_bound_history(self):
        for n in range(70):
            data = json.dumps({'cid': n, 'ipv4': '10.1.2.3'}).encode()
            self.ph.handle_frame(P.Frame(1, 0, 13, 9, 3, data))
        self.ph.handle_frame(P.Frame(1, 0, 13, 10, 3, b'\x01'))
        rows = self.ph.status()['data_notifications']
        self.assertEqual(len(rows), 64)
        self.assertEqual(rows[-2]['decoded']['json']['ipv4'], '10.1.2.3')
        self.assertEqual(rows[-1]['decoded'], {'unmapped_binary': True})
        self.assertEqual(rows[-1]['frame_hex'], '080001000d0a0301')

if __name__ == '__main__':
    unittest.main()
