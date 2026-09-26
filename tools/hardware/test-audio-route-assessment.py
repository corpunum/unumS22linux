#!/usr/bin/env python3
"""Hardware-free receipt acceptance regression tests."""
import copy
import importlib.util
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

spec=importlib.util.spec_from_file_location('route',Path(__file__).with_name('run-audio-route-prepare-once.py'))
route=importlib.util.module_from_spec(spec);spec.loader.exec_module(route)
BASE={'wrapper_returncode':0,'after_audio_exit':0,'trace_capture_exit':0,'kernel_capture_exit':0,
      'same_boot':True,'candidate_identity_verified':True,
      'before':{'boot_id':'boot-private-dynamic'},
      'candidate_identity':{
          'trial_identity':route.HCI_TRIAL_ID,
          'candidate_sha256':route.HCI_CANDIDATE_RECOVERY_SHA256,
          'gnu_build_id':route.HCI_CANDIDATE_GNU_BUILD_ID,
          'boot_id':'boot-private-dynamic','actual_mode':'RECOVERY',
          'observer_receipt_verified':True,'live_recovery_hash_verified':True},
      'diagnostic_kind':'one-second-digital-zero',
      'result':{'child_returncode':0,'child_exited':True,'child_stderr':'',
                'child_deadline_exceeded':False,'after_status':'closed',
                'amps_still_off':True,'cleanup_verified':True,'cleanup_errors':[],
                'restored':{n:{'returncode':0,'value':'0'} for n in ('ABOX SPUS OUT2','ABOX UAIF1 SPK')}}}
TRACE='ioctl(4, SNDRV_PCM_IOCTL_PREPARE) = 0 <0.001>\nioctl(4, SNDRV_PCM_IOCTL_WRITEI_FRAMES) = 0 <0.001>\n'


def source_sample(sequence, *, dpcm='uaif1-start', bclk='0', gate='0',
                  rdma_status=0, rdma_status_add=0, hw_ptr=0,
                  pm_active=True, clock_status='ok', clock_nodes_present=True):
    if dpcm=='uaif1-start':
        dpcm_text='[RDMA2 - Playback]\nState: start\nBackends:\n- UAIF1\n   State: start\n'
    elif dpcm=='no-backend':
        dpcm_text='[RDMA2 - Playback]\nState: start\nBackends:\n No active DSP links\n'
    else:
        dpcm_text=''
    clocks=[]
    if clock_status=='ok':
        for name,role in route.audio_snapshot.UAIF1_CLOCKS:
            present=clock_nodes_present
            status='ok' if present else 'not_present'
            clocks.append({'name':name,'role':role,
                           'clk_rate':{'status':status,'value':'12288000' if present else None},
                           'clk_enable_count':{'status':status,'value':bclk if role=='bclk' else gate if role=='bclk_gate' else '0'},
                           'clk_prepare_count':{'status':status,'value':'1' if present else None}})
    sample_ns=1_000_000_000*(sequence+1)
    sample={'sequence':sequence,'sample_monotonic_ns':sample_ns,
            'monotonic':sample_ns/1_000_000_000,
            'runtime_status':'active' if pm_active else 'suspended',
            'cache_only':'N' if pm_active else 'Y','service':'1',
            'alsa_counters':{'state':'RUNNING','hw_ptr':hw_ptr},
            'alsa_status':'state: RUNNING\nhw_ptr: %d\n'%hw_ptr,
            'hw_params_parsed':{'period_size':1024},
            'registers':({'1230':rdma_status,'1238':rdma_status_add} if pm_active else {}),
            'observations':{
                'dpcm_rdma2':({'status':'ok','value':dpcm_text} if dpcm_text else
                              {'status':'not_present','error_code':'ENOENT'}),
                'clocks':{'status':clock_status,'clocks':clocks},
            }}
    if not pm_active:
        sample['status_read_skipped']=True
        sample['status_read_skip_reason']='abox_runtime_not_active'
    return sample

class Assessment(unittest.TestCase):
    def test_remote_wrapper_is_syntactically_valid(self):
        compile(route.WRAPPER,'audio-diagnostic-wrapper','exec')
        compile(route.AUDIO_PREFLIGHT,'audio-route-read-only-preflight','exec')
        self.assertNotIn('assert ',route.WRAPPER)
        self.assertNotIn('assert ',route.AUDIO_PREFLIGHT)
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
    def test_missing_candidate_identity_does_not_accept_a_live_diagnostic(self):
        r=copy.deepcopy(BASE);r.pop('candidate_identity_verified')
        self.assertFalse(route.classify(r,TRACE)['diagnostic_completed'])
        for key,bad_value in (('trial_identity','old-trial'),
                              ('candidate_sha256','0'*64),
                              ('gnu_build_id','rollback-build-id'),
                              ('actual_mode','BOOT'),
                              ('live_recovery_hash_verified',False)):
            r=copy.deepcopy(BASE);r['candidate_identity'][key]=bad_value
            self.assertFalse(route.classify(r,TRACE)['candidate_identity_verified'])
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

    def test_source_stages_identify_running_frontend_without_backend(self):
        result=route.audio_snapshot.source_path_assessment([
            source_sample(0,dpcm='no-backend'),source_sample(1,dpcm='no-backend')])
        self.assertEqual(result['frontend'],'alsa_running_observed')
        self.assertEqual(result['dpcm_backend'],'running_frontend_without_active_dpcm_backend')
        self.assertEqual(result['localization'],'running_frontend_without_dpcm_backend')

    def test_missing_dpcm_node_is_unknown_not_no_backend(self):
        result=route.audio_snapshot.source_path_assessment([
            source_sample(0,dpcm='missing'),source_sample(1,dpcm='missing')])
        self.assertEqual(result['dpcm_backend'],
                         'unknown_dpcm_observation_missing_for_running_sample')
        self.assertEqual(result['dpcm_observation_status_counts'],{'not_present':2})

    def test_uaif1_framework_clock_gate_stage_is_paired_with_dpcm_start(self):
        result=route.audio_snapshot.source_path_assessment([
            source_sample(0,bclk='0',gate='0'),source_sample(1,bclk='0',gate='0')])
        self.assertEqual(result['uaif1_clock_stage'],
                         'uaif1_started_with_bclk_and_gate_enable_counts_zero')
        self.assertEqual(result['localization'],
                         'uaif1_backend_started_but_linux_bclk_enable_counts_are_zero')
        self.assertEqual(result['uaif1_started_sample_count'],2)

    def test_static_rdma_after_dpcm_and_framework_clock_stays_unresolved(self):
        result=route.audio_snapshot.source_path_assessment([
            source_sample(0,bclk='1',gate='1'),source_sample(1,bclk='1',gate='1')])
        self.assertEqual(result['rdma2_stage'],
                         'rdma2_status_position_unchanged_during_contiguous_running_samples')
        self.assertEqual(result['localization'],
                         'frontend_backend_and_clock_framework_reached_start; dsp_consumption_vs_physical_clock_unresolved')
        self.assertIn('not a physical pin waveform',result['limits'][1])

    def test_rdma_position_without_hw_pointer_advance_localizes_notification_gap(self):
        result=route.audio_snapshot.source_path_assessment([
            source_sample(0,rdma_status=0,rdma_status_add=0,hw_ptr=0),
            source_sample(1,rdma_status=1,rdma_status_add=4,hw_ptr=0)])
        self.assertEqual(result['rdma2_stage'],
                         'rdma2_status_position_changed_during_running_samples')
        self.assertEqual(result['pointer_ipc_stage'],
                         'rdma_position_changes_but_alsa_hw_ptr_does_not_advance')
        self.assertEqual(result['localization'],
                         'rdma_status_moves_but_firmware_pointer_ipc_or_alsa_notification_path_lags')

    def test_gapped_rdma_or_pointer_samples_do_not_claim_stall_or_progress(self):
        result=route.audio_snapshot.source_path_assessment([
            source_sample(0,rdma_status=0,hw_ptr=0),
            source_sample(2,rdma_status=1,hw_ptr=1024)])
        self.assertFalse(result['rdma2_samples_contiguous'])
        self.assertEqual(result['rdma2_stage'],
                         'rdma2_progress_not_localized_insufficient_or_gapped_running_samples')
        self.assertFalse(result['alsa_hw_ptr_samples_contiguous'])
        self.assertFalse(result['alsa_hw_ptr_advanced'])

    def test_clock_pm_skip_and_missing_nodes_are_distinguished(self):
        skipped=[source_sample(i,pm_active=False,clock_status='skipped_pm_gate') for i in range(2)]
        skipped_result=route.audio_snapshot.source_path_assessment(skipped)
        self.assertEqual(skipped_result['clock_observation_status_counts'],
                         {'skipped_pm_gate':2})
        self.assertEqual(skipped_result['uaif1_clock_missing_node_count'],0)
        missing=[source_sample(i,clock_nodes_present=False) for i in range(2)]
        missing_result=route.audio_snapshot.source_path_assessment(missing)
        self.assertEqual(missing_result['clock_observation_status_counts'],{'ok':2})
        self.assertEqual(missing_result['uaif1_clock_missing_node_count'],6)

    def test_live_candidate_gate_rejects_before_remote_audio_preflight(self):
        remote_calls=[]
        trial=SimpleNamespace(phone_health=lambda:{'boot_id':'current-boot'},
                              remote=lambda command:remote_calls.append(command))
        with mock.patch.object(route,'transport_project_root',return_value=Path('/transport')), \
             mock.patch.object(route,'load',return_value=trial), \
             mock.patch.object(route,'verify_live_hci_candidate',
                               return_value={'boot_id':'different-boot'}), \
             mock.patch.object(sys,'argv',[
                 'run-audio-route-prepare-once.py','audio-negative-gate-test','--execute']):
            with self.assertRaisesRegex(RuntimeError,'boot changed during preflight'):
                route.main()
        self.assertEqual(remote_calls,[])

    def test_candidate_identity_error_rejects_before_audio_preflight(self):
        remote_calls=[]
        trial=SimpleNamespace(phone_health=lambda:{'boot_id':'candidate-boot'},
                              remote=lambda command:remote_calls.append(command))
        with mock.patch.object(route,'transport_project_root',return_value=Path('/transport')), \
             mock.patch.object(route,'load',return_value=trial), \
             mock.patch.object(route,'verify_live_hci_candidate',
                               side_effect=RuntimeError('RECOVERY hash mismatch')), \
             mock.patch.object(sys,'argv',[
                 'run-audio-route-prepare-once.py','audio-hash-gate-test','--execute']):
            with self.assertRaisesRegex(RuntimeError,'RECOVERY hash mismatch'):
                route.main()
        self.assertEqual(remote_calls,[])

    def test_candidate_evidence_requires_observer_buildid_boot_and_partition_hash(self):
        expected_hash='a'*64
        def validate_snapshot(value,post_reboot):
            if value.get('gnu_build_id')!='reviewed-candidate-build-id':
                raise ValueError('candidate build ID mismatch')
        hci=SimpleNamespace(
            TRIAL_ID='hci-candidate-20260924-second',
            EXPECTED_FLASH_SHA256=expected_hash,
            observer_receipt_valid=lambda value:value.get('trial_identity')=='hci-candidate-20260924-second',
            validate_snapshot=validate_snapshot,
            require=lambda ok,message: (_ for _ in ()).throw(ValueError(message)) if not ok else None)
        observer={'trial_identity':hci.TRIAL_ID,'boot_id':'boot-private-dynamic'}
        current={'boot_id':'boot-private-dynamic','gnu_build_id':'reviewed-candidate-build-id',
                 'kernel_release':'same-release-as-rollback'}
        result=route.validate_candidate_evidence(hci,observer,current,expected_hash)
        self.assertEqual(result['gnu_build_id'],'reviewed-candidate-build-id')
        self.assertEqual(result['boot_id'],'boot-private-dynamic')
        for observed,live_hash in (
                ({**observer,'trial_identity':'other-trial'},expected_hash),
                (observer,'b'*64),
                ({**observer,'boot_id':'older-boot'},expected_hash)):
            with self.subTest(observer=observed,live_hash=live_hash):
                with self.assertRaises(ValueError):
                    route.validate_candidate_evidence(hci,observed,current,live_hash)
        with self.assertRaisesRegex(ValueError,'build ID mismatch'):
            route.validate_candidate_evidence(hci,observer,
                                               {**current,'gnu_build_id':'rollback-build-id'},
                                               expected_hash)

if __name__=='__main__':unittest.main()
