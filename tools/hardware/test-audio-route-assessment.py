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
                'amps_still_off':True,'cleanup_verified':True,'cleanup_errors':[],
                'restored':{n:{'returncode':0,'value':'0'} for n in ('ABOX SPUS OUT2','ABOX UAIF1 SPK')}}}
TRACE='ioctl(4, SNDRV_PCM_IOCTL_PREPARE) = 0 <0.001>\nioctl(4, SNDRV_PCM_IOCTL_WRITEI_FRAMES) = 0 <0.001>\n'

class Assessment(unittest.TestCase):
    def test_remote_wrapper_is_syntactically_valid(self):
        compile(route.WRAPPER,'audio-diagnostic-wrapper','exec')
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
    def test_partial_restore_failure_is_reported_separately(self):
        r=copy.deepcopy(BASE)
        r['result']['restored']['ABOX UAIF1 SPK']={
            'returncode':1,'value':'1','expected_value':'0','verified':False}
        r['result']['cleanup_errors']=['route_write:ABOX UAIF1 SPK']
        a=route.classify(r,TRACE)
        self.assertFalse(a['cleanup_verified'])
        self.assertEqual(a['cleanup_error_count'],1)
        self.assertFalse(a['diagnostic_completed'])
    def test_amp_mute_failure_invalidates_cleanup(self):
        r=copy.deepcopy(BASE);r['result']['amps_still_off']=False
        a=route.classify(r,TRACE)
        self.assertFalse(a['cleanup_verified'])
        self.assertFalse(a['diagnostic_completed'])
    def test_unreaped_child_is_interrupted_and_cleanup_is_not_claimed(self):
        r=copy.deepcopy(BASE);r['result']['child_exited']=False
        r['result']['child_deadline_exceeded']=True
        r['result']['cleanup_verified']=False
        r['result']['cleanup_errors']=['child_not_reaped_routes_not_restored']
        a=route.classify(r,TRACE)
        self.assertTrue(a['child_interrupted'])
        self.assertFalse(a['cleanup_verified'])
        self.assertEqual(a['outcome'],'interrupted')
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
    def test_malformed_cleanup_and_sample_receipt_fails_closed(self):
        assessment=route.classify({'result':{'restored':None,'cleanup_errors':None,
                                             'progress_samples':None}},'')
        self.assertFalse(assessment['diagnostic_completed'])
        self.assertFalse(assessment['cleanup_verified'])
        self.assertEqual(assessment['cleanup_error_count'],1)
    def test_completed_diagnostic_without_samples_does_not_claim_dma(self):
        a=route.classify(BASE,TRACE)
        self.assertTrue(a['diagnostic_completed'])
        self.assertFalse(a['dma_progress_verified'])
        self.assertFalse(a['physical_playback_verified'])
    def test_hw_pointer_advance_verifies_dma_not_sound(self):
        r=copy.deepcopy(BASE)
        r['result']['progress_samples']=[
            {'monotonic':1.0,'alsa_status':'state: RUNNING\nhw_ptr: 100\nappl_ptr: 200\n'},
            {'monotonic':2.0,'alsa_status':'state: RUNNING\nhw_ptr: 356\nappl_ptr: 456\n'},
        ]
        a=route.classify(r,TRACE)
        self.assertTrue(a['dma_progress_verified'])
        self.assertEqual(a['dma_progress']['hw_ptr_advance'],256)
        self.assertFalse(a['period_progress_verified'])
        self.assertFalse(a['physical_playback_verified'])
        self.assertEqual(a['capture_correlation']['sample_count'],2)
    def test_sample_observation_windows_are_correlated(self):
        r=copy.deepcopy(BASE)
        r['result']['progress_samples']=[
            {'snapshot_start_monotonic_ns':100,'snapshot_end_monotonic_ns':140},
            {'snapshot_start_monotonic_ns':200,'snapshot_end_monotonic_ns':260},
        ]
        correlation=route.classify(r,TRACE)['capture_correlation']
        self.assertEqual(correlation['timed_sample_count'],2)
        self.assertEqual(correlation['window_start_monotonic_ns'],100)
        self.assertEqual(correlation['window_end_monotonic_ns'],260)
        self.assertEqual(correlation['maximum_snapshot_duration_ns'],60)
    def test_constant_pointer_does_not_verify_dma(self):
        samples=[
            {'monotonic':1.0,'alsa_status':'state: RUNNING\nhw_ptr: 100\n'},
            {'monotonic':2.0,'alsa_status':'state: RUNNING\nhw_ptr: 100\n'},
        ]
        self.assertFalse(route.assess_dma_progress(samples)['verified'])
    def test_regression_or_bad_running_sample_fails_closed(self):
        regress=[
            {'monotonic':1.0,'alsa_status':'state: RUNNING\nhw_ptr: 200\n'},
            {'monotonic':2.0,'alsa_status':'state: RUNNING\nhw_ptr: 100\n'},
        ]
        malformed=[
            {'monotonic':1.0,'alsa_status':'state: RUNNING\nhw_ptr: ???\n'},
            {'monotonic':2.0,'alsa_status':'state: RUNNING\nhw_ptr: 300\n'},
        ]
        self.assertFalse(route.assess_dma_progress(regress)['verified'])
        self.assertFalse(route.assess_dma_progress(malformed)['verified'])

if __name__=='__main__':unittest.main()
