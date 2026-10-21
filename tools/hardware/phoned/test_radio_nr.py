#!/usr/bin/env python3
"""5G NSA visibility: physical channel config (08/3C) and mode_select decoding. No device access."""
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('phoned', Path(__file__).with_name('s22-phoned.py'))
P = importlib.util.module_from_spec(spec)
spec.loader.exec_module(P)

# Real 08/3C NOTI payload logged by phoned on the S22 (Vodafone GR, 2026-10-21): LTE B3 anchor,
# B20 SCell and an NR n78 100 MHz secondary cell (EN-DC).
LIVE_ENDC = (b'{"num_pch":3,"pch":[{"act":"lte","band":{"band":3,"rat":3},"cid":[0,0,0,0,0,0,0,0,0],"conn_state":1,'
             b'"dlbw":20000,"dlch_num":1451,"freq_range":2,"pcid":334,"ulbw":20000,"ulch_num":19451},{"act":"lte",'
             b'"band":{"band":20,"rat":3},"cid":[0,0,0,0,0,0,0,0,0],"conn_state":2,"dlbw":10000,"dlch_num":6300,'
             b'"freq_range":1,"pcid":334,"ulbw":1400,"ulch_num":0},{"act":"nr","band":{"band":78,"rat":5},'
             b'"cid":[0,0,0,0,0,0,0,0,0],"conn_state":2,"dlbw":100000,"dlch_num":643334,"freq_range":3,"pcid":234,'
             b'"ulbw":100,"ulch_num":643334}]}')
LIVE_LTE_ONLY = (b'{"num_pch":1,"pch":[{"act":"lte","band":{"band":3,"rat":3},"cid":[0,0,0,0,0,0,0,0,0],"conn_state":1,'
                 b'"dlbw":20000,"dlch_num":1451,"freq_range":2,"pcid":334,"ulbw":20000,"ulch_num":19451}]}')


class RadioTests(unittest.TestCase):
    def test_endc_detected(self):
        d = P.dec_phys_channels(LIVE_ENDC)
        self.assertTrue(d['nr_nsa'])
        self.assertEqual(d['nr_bands'], [78])
        self.assertEqual(d['total_dl_khz'], 130000)
        self.assertEqual(d['cells'][2], {'act': 'nr', 'band': 78, 'conn': 'secondary', 'dl_khz': 100000,
                                         'ul_khz': 100, 'arfcn': 643334, 'pci': 234})

    def test_lte_only(self):
        d = P.dec_phys_channels(LIVE_LTE_ONLY)
        self.assertFalse(d['nr_nsa'])
        self.assertEqual(d['nr_bands'], [])

    def test_garbage_is_empty(self):
        self.assertEqual(P.dec_phys_channels(b'\x00\x01'), {})

    def test_describe_routes_08_3c(self):
        fr = P.Frame(1, 0, 8, 0x3C, P.NOTI, LIVE_ENDC)
        self.assertTrue(P.describe(fr)['decoded']['nr_nsa'])
        self.assertEqual(fr.name, 'NET_PHYS_CHANNEL_CONFIG')

    def test_mode_select_bits(self):
        # 75 is what this CP reports: NR + LTE + WCDMA + GSM (stock NETWORK_MODE_NR_LTE_GSM_WCDMA).
        self.assertEqual(P.mode_select_rats(75), ['gsm', 'wcdma', 'lte', 'nr'])
        self.assertEqual(P.mode_select_rats(11), ['gsm', 'wcdma', 'lte'])
        self.assertEqual(P.dec_mode_select(b'{"mode_select":75}')['rats'], ['gsm', 'wcdma', 'lte', 'nr'])

    def test_on_phys_updates_status_and_event(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            ph = P.Phone(td, simulate=True)
            ph._on_phys(None, P.dec_phys_channels(LIVE_LTE_ONLY))
            ph._on_phys(None, P.dec_phys_channels(LIVE_ENDC))
            st = ph.status()
            self.assertTrue(st['radio']['nr_nsa'])
            self.assertIsNotNone(st['nr_last_seen'])
            evs = [e for e in ph.events.since(0)[1] if e['type'] == 'radio']
            self.assertEqual([e['nr_nsa'] for e in evs], [False, True])


if __name__ == '__main__':
    unittest.main()
