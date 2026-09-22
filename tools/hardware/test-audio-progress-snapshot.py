#!/usr/bin/env python3
import importlib.util
from pathlib import Path
import unittest
spec=importlib.util.spec_from_file_location('snapshot',Path(__file__).with_name('audio-progress-snapshot.py'))
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)

class Planner(unittest.TestCase):
    ranges='0-8\n1200-1230\n1238-1238\n'
    access='0000: y y n n\n1200: y y y n\n1230: y n y n\n1238: y n y n\n'
    def test_exact_offsets_and_single_record_sizes(self):
        ctrl,status,status_add=module.plans(self.ranges,self.access)
        self.assertEqual((ctrl['register'],ctrl['offset'],ctrl['length']),(0x1200,3*15,15))
        self.assertEqual((status['offset'],status['length']),(15*15,15))
        self.assertEqual((status_add['offset'],status_add['length']),(16*15,15))
    def test_decode_enable_only(self):
        self.assertEqual(module.decode_rdma2_ctrl(0),{'raw':0,'enable':False})
        self.assertEqual(module.decode_rdma2_ctrl(0x80000001),{'raw':0x80000001,'enable':True})
    def test_ctrl_must_be_volatile_and_nonprecious(self):
        with self.assertRaises(ValueError):
            module.plans(self.ranges,self.access.replace('1200: y y y n','1200: y y n n'))
    def test_reject_precious(self):
        with self.assertRaises(ValueError):module.plans(self.ranges,self.access.replace('y n y n','y n y y'))
    def test_reject_nonvolatile_or_writable(self):
        for bad in ('y n n n','y y y n'):
            with self.assertRaises(ValueError):module.plans(self.ranges,self.access.replace('y n y n',bad))
    def test_reject_absent_target(self):
        with self.assertRaises(ValueError):module.plans('0-8\n',self.access)
    def test_reject_overlapping_range(self):
        with self.assertRaises(ValueError):module.plans(self.ranges+'1238-1240\n',self.access)
    def test_reject_bad_alignment(self):
        with self.assertRaises(ValueError):module.plans('0-9\n',self.access)

if __name__=='__main__':unittest.main()
