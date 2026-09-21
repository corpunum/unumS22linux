#!/usr/bin/env python3
"""Hardware-free receipt acceptance regression tests."""
import copy
import importlib.util
from pathlib import Path
import unittest

spec=importlib.util.spec_from_file_location('route',Path(__file__).with_name('run-audio-route-prepare-once.py'))
route=importlib.util.module_from_spec(spec);spec.loader.exec_module(route)
BASE={'wrapper_returncode':0,'after_audio_exit':0,'trace_capture_exit':0,'kernel_capture_exit':0,
      'same_boot':True,'diagnostic_kind':'one-second-digital-zero',
      'result':{'child_returncode':0,'child_exited':True,'child_stderr':'',
                'child_deadline_exceeded':False,'after_status':'closed',
                'restored':{n:{'returncode':0,'value':'0'} for n in ('ABOX SPUS OUT2','ABOX UAIF1 SPK')}}}
TRACE='ioctl(4, SNDRV_PCM_IOCTL_PREPARE) = 0 <0.001>\nioctl(4, SNDRV_PCM_IOCTL_WRITEI_FRAMES) = 0 <0.001>\n'

class Assessment(unittest.TestCase):
    def test_completed_diagnostic_does_not_claim_physical_sound(self):
        a=route.classify(BASE,TRACE)
        self.assertTrue(a['diagnostic_completed']);self.assertFalse(a['physical_playback_verified'])
    def test_deadline_rejects_zero_exit(self):
        r=copy.deepcopy(BASE);r['result']['child_deadline_exceeded']=True
        self.assertFalse(route.classify(r,TRACE)['diagnostic_completed'])
    def test_legacy_abort_rejects_zero_exit(self):
        r=copy.deepcopy(BASE);r['result'].pop('child_deadline_exceeded')
        r['result']['child_stderr']='Aborted by signal Terminated...'
        self.assertEqual(route.classify(r,TRACE)['outcome'],'interrupted')
    def test_signal_trace_rejects_zero_exit(self):
        self.assertTrue(route.classify(BASE,TRACE+'--- SIGTERM {si_signo=SIGTERM} ---')['child_interrupted'])
    def test_missing_restore_rejects(self):
        r=copy.deepcopy(BASE);r['result']['restored'].pop('ABOX SPUS OUT2')
        self.assertFalse(route.classify(r,TRACE)['diagnostic_completed'])
    def test_failed_child_rejects(self):
        r=copy.deepcopy(BASE);r['result']['child_returncode']=1
        self.assertFalse(route.classify(r,TRACE)['diagnostic_completed'])
    def test_prepare_only_disallows_writes(self):
        r=copy.deepcopy(BASE);r['diagnostic_kind']='prepare-only'
        self.assertFalse(route.classify(r,TRACE)['diagnostic_completed'])
        self.assertTrue(route.classify(r,TRACE.splitlines()[0]+'\n')['diagnostic_completed'])
    def test_missing_trace_or_new_boot_rejects(self):
        self.assertFalse(route.classify(BASE,'')['diagnostic_completed'])
        r=copy.deepcopy(BASE);r['same_boot']=False
        self.assertFalse(route.classify(r,TRACE)['diagnostic_completed'])

if __name__=='__main__':unittest.main()
