#!/usr/bin/env python3
import importlib.util
from pathlib import Path
import unittest
spec=importlib.util.spec_from_file_location('snapshot',Path(__file__).with_name('audio-progress-snapshot.py'))
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)

class Planner(unittest.TestCase):
    ranges='0-8\n1200-1230\n1238-1238\n'
    access='0000: y y n n\n1230: y n y n\n1238: y n y n\n'
    def test_exact_offsets_and_single_record_sizes(self):
        a,b=module.plans(self.ranges,self.access)
        self.assertEqual((a['offset'],a['length']),(15*15,15))
        self.assertEqual((b['offset'],b['length']),(16*15,15))
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
