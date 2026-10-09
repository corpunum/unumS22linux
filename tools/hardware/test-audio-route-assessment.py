#!/usr/bin/env python3
"""Hardware-free receipt acceptance regression tests."""
import copy
import base64
from contextlib import contextmanager
import importlib.util
import json
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

spec=importlib.util.spec_from_file_location('route',Path(__file__).with_name('run-audio-route-prepare-once.py'))
route=importlib.util.module_from_spec(spec);spec.loader.exec_module(route)
node_spec=importlib.util.spec_from_file_location('audio_node',Path(__file__).with_name('audio-rdma2-node-once.py'))
node_helper=importlib.util.module_from_spec(node_spec);node_spec.loader.exec_module(node_helper)
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

def isolated_python(script,*args):
    flags=['-O'] if sys.flags.optimize else []
    return subprocess.run([sys.executable,*flags,'-I','-c',script,*args],
                          capture_output=True,text=True,check=False)


def node_fixture(root, *, card_id='RainbowPrince', pcm_status='closed',
                 dev='116:3', uevent=None, wrong_class_link=False,
                 wrong_devchar_link=False):
    proc=root/'proc';sysroot=root/'sys';devroot=root/'dev'
    (proc/'1').mkdir(parents=True);(proc/'sys/kernel').mkdir(parents=True)
    (proc/'asound/card0/pcm2p/sub0').mkdir(parents=True)
    (proc/'1/comm').write_text('native-guardian\n')
    (proc/'sys/kernel/osrelease').write_text(node_helper.KERNEL_RELEASE+'\n')
    (proc/'asound/card0/id').write_text(card_id+'\n')
    (proc/'asound/card0/pcm2p/sub0/status').write_text(pcm_status+'\n')
    target=sysroot/node_helper.EXPECTED_TARGET
    target.mkdir(parents=True)
    (target/'dev').write_text(dev+'\n')
    if uevent is None:
        uevent='MAJOR=116\nMINOR=3\nDEVNAME=snd/pcmC0D2p\nDEVTYPE=pcm\n'
    (target/'uevent').write_text(uevent)
    class_dir=sysroot/'class/sound';class_dir.mkdir(parents=True)
    devchar_dir=sysroot/'dev/char';devchar_dir.mkdir(parents=True)
    wrong=sysroot/'devices/platform/sound/sound/card0/other-pcm';wrong.mkdir(parents=True)
    (class_dir/node_helper.NODE_NAME).symlink_to(wrong if wrong_class_link else target)
    (devchar_dir/'116:3').symlink_to(wrong if wrong_devchar_link else target)
    devroot.mkdir(mode=0o755);os.chmod(devroot,0o755)
    snd=devroot/'snd';snd.mkdir(mode=0o755);os.chmod(snd,0o755)
    return proc,sysroot,devroot


def dma_sample(sequence,monotonic,hw_ptr):
    return {'sequence':sequence,'monotonic':monotonic,
            'alsa_status':'state: RUNNING\nhw_ptr: %d\n'%hw_ptr}


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
        helper_source=Path(__file__).with_name('audio-rdma2-node-once.py').read_text()
        self.assertEqual((node_helper.NODE_NAME,node_helper.MAJOR,node_helper.MINOR),
                         ('pcmC0D2p',116,3))
        self.assertNotIn('mdev -s',helper_source)
        self.assertNotIn('os.listdir',helper_source)
        self.assertEqual(helper_source.count('mknod_fn('),1)

    def test_node_helper_receipt_is_exact_and_pinned_to_116_3(self):
        record={'schema':'audio-rdma2-node/v1','path':'/dev/snd/pcmC0D2p',
                'major':116,'minor':3,'uid':0,'gid':0,'mode':'0600',
                'created':True,'state':'created','card_id':'RainbowPrince',
                'pcm_status':'closed',
                'sysfs_class_target':'/sys/devices/platform/sound/sound/card0/pcmC0D2p',
                'sysfs_devchar_target':'/sys/devices/platform/sound/sound/card0/pcmC0D2p',
                'dev':'116:3','devname':'snd/pcmC0D2p','devtype':'pcm'}
        calls=[]
        trial=SimpleNamespace(remote=lambda command:calls.append(command) or
            SimpleNamespace(returncode=0,stdout=json.dumps(record),stderr=''))
        parsed,digest=route.remote_pcm_node_provision(trial)
        self.assertEqual(parsed,record)
        self.assertEqual(len(digest),64)
        self.assertIn('--apply',calls[0])
        invalid={**record,'minor':4,'dev':'116:4'}
        trial.remote=lambda command:SimpleNamespace(returncode=0,
            stdout=json.dumps(invalid),stderr='')
        with self.assertRaisesRegex(RuntimeError,'exact-identity'):
            route.remote_pcm_node_provision(trial)

    def test_operation_marker_precedes_node_then_trace_mutations(self):
        calls=[]
        class Operation:
            def begin(self,*,project_root):calls.append('begin')
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)/'worktree';root.mkdir(mode=0o700)
            local,fd=route.create_private_trial_dir(route.AUDIO_TRIAL_ID,root)
            try:
                with mock.patch.object(route,'remote_pcm_node_provision',
                                       side_effect=lambda trial:(calls.append('node') or
                                           ({'schema':'audio-rdma2-node/v1'},'f'*64))), \
                     mock.patch.object(route,'remote_trace_stage',
                                       side_effect=lambda trial:calls.append('trace') or {'path':'trace'}):
                    result=route.begin_and_stage_trace(SimpleNamespace(),Operation(),fd)
                self.assertEqual(calls,['begin','node','trace'])
                self.assertEqual(result[1],'f'*64)
                self.assertTrue((local/'pcm-node-provision.json').is_file())
            finally:os.close(fd)

    def test_exact_pcm_node_fixture_creates_only_root_0600_116_3(self):
        with tempfile.TemporaryDirectory() as temporary:
            proc,sysroot,devroot=node_fixture(Path(temporary))
            uid=os.getuid();gid=os.getgid();created={}
            calls=[]
            def fake_stat(name,*,dir_fd,follow_symlinks):
                if name!=node_helper.NODE_NAME or not created:
                    raise FileNotFoundError(name)
                return created['info']
            def fake_mknod(name,mode,device,*,dir_fd):
                calls.append((name,mode,device,dir_fd))
                if created:raise FileExistsError(name)
                created['info']=SimpleNamespace(st_mode=mode,st_rdev=device,
                                                st_uid=uid,st_gid=gid)
            chowns=[];chmods=[]
            def fake_chown(name,owner,group,*,dir_fd,follow_symlinks):
                chowns.append((name,owner,group,dir_fd,follow_symlinks))
            def fake_chmod(name,mode,*,dir_fd):chmods.append((name,mode,dir_fd))
            result=node_helper.provision_node(
                sys_root=sysroot,proc_root=proc,dev_root=devroot,apply=True,
                expected_uid=uid,expected_gid=gid,mknod_fn=fake_mknod,
                chown_fn=fake_chown,chmod_fn=fake_chmod,stat_fn=fake_stat)
            self.assertEqual(result['state'],'created')
            self.assertTrue(result['created'])
            self.assertEqual((result['major'],result['minor']),(116,3))
            self.assertEqual((result['uid'],result['gid'],result['mode']),
                             (uid,gid,'0600'))
            self.assertEqual(len(calls),1)
            self.assertEqual(calls[0][:3],(node_helper.NODE_NAME,
                node_helper.stat.S_IFCHR|0o600,os.makedev(116,3)))
            self.assertEqual(len(chowns),1);self.assertEqual(len(chmods),1)

    def test_pcm_node_plan_and_exact_existing_are_no_write(self):
        with tempfile.TemporaryDirectory() as temporary:
            proc,sysroot,devroot=node_fixture(Path(temporary))
            uid=os.getuid();gid=os.getgid();calls=[]
            def missing_stat(name,*,dir_fd,follow_symlinks):raise FileNotFoundError(name)
            planned=node_helper.provision_node(
                sys_root=sysroot,proc_root=proc,dev_root=devroot,apply=False,
                expected_uid=uid,expected_gid=gid,
                mknod_fn=lambda *a,**k:calls.append((a,k)),stat_fn=missing_stat)
            self.assertEqual(planned['state'],'missing');self.assertEqual(calls,[])
            exact=SimpleNamespace(st_mode=node_helper.stat.S_IFCHR|0o600,
                                  st_rdev=os.makedev(116,3),st_uid=uid,st_gid=gid)
            present=node_helper.provision_node(
                sys_root=sysroot,proc_root=proc,dev_root=devroot,apply=True,
                expected_uid=uid,expected_gid=gid,stat_fn=lambda *a,**k:exact,
                mknod_fn=lambda *a,**k:calls.append((a,k)))
            self.assertEqual(present['state'],'already_present_exact')
            self.assertFalse(present['created']);self.assertEqual(calls,[])

    def test_pcm_node_rejects_wrong_sysfs_and_existing_targets_before_mknod(self):
        fixture_cases=(
            {'dev':'116:4'},
            {'uevent':'MAJOR=116\nMINOR=4\nDEVNAME=snd/pcmC0D2p\nDEVTYPE=pcm\n'},
            {'wrong_class_link':True},
            {'wrong_devchar_link':True},
            {'card_id':'WrongCard'},
            {'pcm_status':'state: RUNNING'},
        )
        for overrides in fixture_cases:
            with self.subTest(overrides=overrides),tempfile.TemporaryDirectory() as temporary:
                proc,sysroot,devroot=node_fixture(Path(temporary),**overrides)
                calls=[]
                with self.assertRaises(RuntimeError):
                    node_helper.provision_node(
                        sys_root=sysroot,proc_root=proc,dev_root=devroot,apply=True,
                        expected_uid=os.getuid(),expected_gid=os.getgid(),
                        mknod_fn=lambda *a,**k:calls.append((a,k)))
                self.assertEqual(calls,[])
        with tempfile.TemporaryDirectory() as temporary:
            proc,sysroot,devroot=node_fixture(Path(temporary))
            wrong=SimpleNamespace(st_mode=node_helper.stat.S_IFCHR|0o600,
                                  st_rdev=os.makedev(116,4),st_uid=os.getuid(),st_gid=os.getgid())
            calls=[]
            with self.assertRaisesRegex(RuntimeError,'wrong identity'):
                node_helper.provision_node(
                    sys_root=sysroot,proc_root=proc,dev_root=devroot,apply=True,
                    expected_uid=os.getuid(),expected_gid=os.getgid(),
                    stat_fn=lambda *a,**k:wrong,
                    mknod_fn=lambda *a,**k:calls.append((a,k)))
            self.assertEqual(calls,[])

    def test_pcm_node_mknod_race_returns_eexist_without_replacement(self):
        with tempfile.TemporaryDirectory() as temporary:
            proc,sysroot,devroot=node_fixture(Path(temporary))
            calls=[];chowns=[]
            def missing_stat(name,*,dir_fd,follow_symlinks):raise FileNotFoundError(name)
            def raced_mknod(name,mode,device,*,dir_fd):
                calls.append((name,mode,device));raise FileExistsError(name)
            with self.assertRaises(FileExistsError):
                node_helper.provision_node(
                    sys_root=sysroot,proc_root=proc,dev_root=devroot,apply=True,
                    expected_uid=os.getuid(),expected_gid=os.getgid(),
                    stat_fn=missing_stat,mknod_fn=raced_mknod,
                    chown_fn=lambda *a,**k:chowns.append((a,k)))
            self.assertEqual(len(calls),1);self.assertEqual(chowns,[])

    def test_pcm_node_rejects_existing_symlink_or_regular_file_without_replacement(self):
        for target_kind in ('symlink','regular'):
            with self.subTest(target_kind=target_kind),tempfile.TemporaryDirectory() as temporary:
                proc,sysroot,devroot=node_fixture(Path(temporary))
                target=devroot/'snd'/node_helper.NODE_NAME
                if target_kind=='symlink':
                    target.symlink_to('/dev/null')
                else:
                    target.write_text('not a device node')
                calls=[]
                with self.assertRaises(RuntimeError):
                    node_helper.provision_node(
                        sys_root=sysroot,proc_root=proc,dev_root=devroot,apply=True,
                        expected_uid=os.getuid(),expected_gid=os.getgid(),
                        mknod_fn=lambda *a,**k:calls.append((a,k)))
                self.assertEqual(calls,[])

    def test_pcm_node_rejects_symlinked_sound_directory_before_mknod(self):
        with tempfile.TemporaryDirectory() as temporary:
            proc,sysroot,devroot=node_fixture(Path(temporary))
            snd=devroot/'snd';snd.rmdir();snd.symlink_to('/tmp')
            calls=[]
            with self.assertRaises(OSError):
                node_helper.provision_node(
                    sys_root=sysroot,proc_root=proc,dev_root=devroot,apply=True,
                    expected_uid=os.getuid(),expected_gid=os.getgid(),
                    mknod_fn=lambda *a,**k:calls.append((a,k)))
            self.assertEqual(calls,[])

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
            {'sequence':0,'monotonic':1.0,'alsa_status':'state: RUNNING\nhw_ptr: 100\nappl_ptr: 200\n'},
            {'sequence':1,'monotonic':2.0,'alsa_status':'state: RUNNING\nhw_ptr: 356\nappl_ptr: 456\n'},
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
            dma_sample(0,1.0,100),dma_sample(1,2.0,100),
        ]
        self.assertFalse(route.assess_dma_progress(samples)['verified'])
    def test_regression_or_bad_running_sample_fails_closed(self):
        regress=[
            dma_sample(0,1.0,200),dma_sample(1,2.0,100),
        ]
        malformed=[
            {'sequence':0,'monotonic':1.0,'alsa_status':'state: RUNNING\nhw_ptr: ???\n'},
            dma_sample(1,2.0,300),
        ]
        self.assertFalse(route.assess_dma_progress(regress)['verified'])
        self.assertEqual(route.assess_dma_progress(regress)['reason'],'hw_ptr regression')
        self.assertFalse(route.assess_dma_progress(malformed)['verified'])

    def test_dma_advance_requires_adjacent_running_capture_sequences(self):
        result=route.assess_dma_progress([
            dma_sample(0,1.0,0),dma_sample(2,3.0,1024)])
        self.assertFalse(result['verified'])
        self.assertEqual(result['reason'],
                         'capture sequence gap inside RUNNING observation window')

    def test_dma_advance_rejects_error_or_nonrunning_sample_inside_window(self):
        samples=(
            [dma_sample(0,1.0,0),
             {'sequence':1,'monotonic':2.0,'error_type':'OSError'},
             dma_sample(2,3.0,1024)],
            [dma_sample(0,1.0,0),None,dma_sample(2,3.0,1024)],
            [dma_sample(0,1.0,0),
             {'sequence':1,'monotonic':2.0,'alsa_status':'state: XRUN\nhw_ptr: 512\n'},
             dma_sample(2,3.0,1024)],
        )
        for window in samples:
            with self.subTest(window=window):
                result=route.assess_dma_progress(window)
                self.assertFalse(result['verified'])
                self.assertEqual(result['reason'],
                    'RUNNING observations separated by a missing or non-RUNNING sample')

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

    def test_rdma_and_pointer_disjoint_windows_leave_ipc_stage_unknown(self):
        samples=[
            source_sample(0,bclk='1',gate='1',rdma_status=0,rdma_status_add=0,hw_ptr=0),
            source_sample(1,bclk='1',gate='1',rdma_status=1,rdma_status_add=4,hw_ptr=0),
            source_sample(2,bclk='1',gate='1',rdma_status=1,rdma_status_add=4,hw_ptr=0),
            source_sample(3,bclk='1',gate='1',rdma_status=1,rdma_status_add=4,hw_ptr=0),
        ]
        for sample in samples[:2]:
            sample['alsa_counters'].pop('hw_ptr')
        for sample in samples[2:]:
            sample['registers']={}
        result=route.audio_snapshot.source_path_assessment(samples)
        self.assertTrue(result['rdma2_position_changed'])
        self.assertFalse(result['alsa_hw_ptr_advanced'])
        self.assertEqual(result['rdma_hw_ptr_pair_sample_count'],0)
        self.assertFalse(result['rdma_hw_ptr_pair_samples_contiguous'])
        self.assertEqual(result['pointer_ipc_stage'],
                         'pointer_ipc_not_localized_insufficient_or_gapped_samples')
        self.assertEqual(result['localization'],'insufficient_source_distinguishing_evidence')

    def test_stationary_rdma_pointer_pairs_do_not_explain_unpaired_prior_motion(self):
        samples=[
            source_sample(0,bclk='1',gate='1',rdma_status=0,rdma_status_add=0,hw_ptr=0),
            source_sample(1,bclk='1',gate='1',rdma_status=1,rdma_status_add=4,hw_ptr=0),
            source_sample(2,bclk='1',gate='1',rdma_status=1,rdma_status_add=4,hw_ptr=0),
            source_sample(3,bclk='1',gate='1',rdma_status=1,rdma_status_add=4,hw_ptr=0),
        ]
        for sample in samples[:2]:
            sample['alsa_counters'].pop('hw_ptr')
        result=route.audio_snapshot.source_path_assessment(samples)
        self.assertTrue(result['rdma2_position_changed'])
        self.assertFalse(result['rdma_position_changed_in_paired_window'])
        self.assertEqual(result['rdma_hw_ptr_pair_sample_count'],2)
        self.assertEqual(result['pointer_ipc_stage'],
                         'pointer_ipc_not_localized_insufficient_or_gapped_samples')

    def test_gapped_rdma_or_pointer_samples_do_not_claim_stall_or_progress(self):
        result=route.audio_snapshot.source_path_assessment([
            source_sample(0,rdma_status=0,hw_ptr=0),
            source_sample(2,rdma_status=1,hw_ptr=1024)])
        self.assertFalse(result['rdma2_samples_contiguous'])
        self.assertEqual(result['rdma2_stage'],
                         'rdma2_progress_not_localized_insufficient_or_gapped_running_samples')
        self.assertFalse(result['alsa_hw_ptr_samples_contiguous'])
        self.assertFalse(result['alsa_hw_ptr_advanced'])

    def test_disjoint_rdma_and_pointer_windows_leave_ipc_localization_unknown(self):
        samples=[
            source_sample(0,bclk='1',gate='1',rdma_status=0,rdma_status_add=0,hw_ptr=0),
            source_sample(1,bclk='1',gate='1',rdma_status=1,rdma_status_add=4,hw_ptr=0),
            source_sample(2,bclk='1',gate='1',rdma_status=1,rdma_status_add=4,hw_ptr=0),
            source_sample(3,bclk='1',gate='1',rdma_status=1,rdma_status_add=4,hw_ptr=0),
        ]
        for sample in samples[:2]:sample['alsa_counters'].pop('hw_ptr')
        for sample in samples[2:]:sample['registers']={}
        result=route.audio_snapshot.source_path_assessment(samples)
        self.assertTrue(result['rdma2_position_changed'])
        self.assertFalse(result['alsa_hw_ptr_advanced'])
        self.assertEqual(result['rdma_hw_ptr_pair_sample_count'],0)
        self.assertEqual(result['pointer_ipc_stage'],
                         'pointer_ipc_not_localized_insufficient_or_gapped_samples')
        self.assertEqual(result['localization'],'insufficient_source_distinguishing_evidence')

    def test_fake_filesystem_trace_stage_is_exclusive_and_readback_is_inode_pinned(self):
        with tempfile.TemporaryDirectory() as temp:
            fake_root=Path(temp)/'s22';fake_root.mkdir(mode=0o700)
            uid=os.getuid()
            stage_args=[str(fake_root),str(uid),'0',str(route.TRACE_MIN_FREE_BYTES),
                        str(route.TRACE_MIN_FREE_INODES)]
            stage=isolated_python(route.REMOTE_TRACE_STAGE,*stage_args)
            self.assertEqual(stage.returncode,0,stage.stderr)
            metadata=json.loads(stage.stdout)
            self.assertEqual(metadata['path'],str(fake_root)+'/audio-trials-20260927/audio-zero-node-20260927/trace.strace')
            self.assertEqual(metadata['trace_size'],0)
            trace=fake_root/'audio-trials-20260927'/'audio-zero-node-20260927'/'trace.strace'
            payload=b'ioctl(4, SNDRV_PCM_IOCTL_PREPARE) = 0\n'
            trace.write_bytes(payload)
            second=isolated_python(route.REMOTE_TRACE_STAGE,*stage_args)
            self.assertNotEqual(second.returncode,0)
            self.assertEqual(trace.read_bytes(),payload)
            read_args=[str(fake_root),str(uid),str(metadata['trace_dev']),
                       str(metadata['trace_ino']),str(route.TRACE_MAX_BYTES)]
            readback=isolated_python(route.REMOTE_TRACE_READ,*read_args)
            self.assertEqual(readback.returncode,0,readback.stderr)
            record=json.loads(readback.stdout)
            self.assertEqual(base64.b64decode(record['data_base64']),payload)
            # Retain the old inode: unlink/recreate may reuse it immediately,
            # which does not exercise the different-inode rejection contract.
            original=trace.with_name('retained-original-trace')
            trace.rename(original)
            trace.write_bytes(b'replacement')
            trace.chmod(0o600)
            self.assertNotEqual(original.stat().st_ino,trace.stat().st_ino)
            replaced=isolated_python(route.REMOTE_TRACE_READ,*read_args)
            self.assertNotEqual(replaced.returncode,0)
            self.assertIn('trace_reservation_identity_changed',replaced.stderr)
            # Isolate the inode predicate: identical replacement metadata must
            # pass when only that check is removed from the actual template.
            inode_check=' and trace.st_ino==expected_ino'
            self.assertEqual(route.REMOTE_TRACE_READ.count(inode_check),1)
            mutation=route.REMOTE_TRACE_READ.replace(inode_check,'')
            unpinned=isolated_python(mutation,*read_args)
            self.assertEqual(unpinned.returncode,0,unpinned.stderr)
            self.assertEqual(base64.b64decode(json.loads(unpinned.stdout)['data_base64']),
                             b'replacement')

    def test_fake_filesystem_trace_stage_rejects_symlink_ancestry(self):
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp);real=base/'real';real.mkdir(mode=0o700)
            link=base/'link';link.symlink_to(real,target_is_directory=True)
            result=isolated_python(route.REMOTE_TRACE_STAGE,str(link),str(os.getuid()),
                '0',str(route.TRACE_MIN_FREE_BYTES),str(route.TRACE_MIN_FREE_INODES))
            self.assertNotEqual(result.returncode,0)

    def test_host_receipt_staging_is_private_and_create_exclusive(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'worktree';root.mkdir(mode=0o700)
            directory,fd=route.create_private_trial_dir(route.AUDIO_TRIAL_ID,root)
            try:
                route.write_private_artifact(fd,'receipt.json','{"private":true}\n')
                self.assertEqual((directory/'receipt.json').read_text(),'{"private":true}\n')
                self.assertEqual((directory.stat().st_mode&0o777),0o700)
                self.assertEqual(((directory/'receipt.json').stat().st_mode&0o777),0o600)
                with self.assertRaises(FileExistsError):
                    route.write_private_artifact(fd,'receipt.json','replacement')
            finally:os.close(fd)
            with self.assertRaises(FileExistsError):
                route.create_private_trial_dir(route.AUDIO_TRIAL_ID,root)

    def test_partial_remote_stage_failure_remains_unknown_and_blocks_retry(self):
        class FakeGuard:
            def __init__(self):self.state={}
            @contextmanager
            def acquire_operation_lock(self,project_root,trial_id,operation_kind):
                prior=self.state.get(trial_id)
                if prior in ('pending','unknown'):
                    raise RuntimeError('unresolved trial marker blocks retry')
                operation=FakeOperation(self,trial_id,operation_kind)
                try:yield operation
                except BaseException:
                    if operation.started and not operation.finished:self.state[trial_id]='unknown'
                    raise
                else:
                    if operation.started and not operation.finished:self.state[trial_id]='unknown'

        class FakeOperation:
            def __init__(self,guard,trial_id,kind):
                self.guard=guard;self.trial_id=trial_id;self.kind=kind
                self.started=False;self.finished=False;self.complete_calls=[]
            def begin(self,*,project_root):
                self.started=True;self.guard.state[self.trial_id]='pending'
            def complete(self,*args,**kwargs):
                self.complete_calls.append((args,kwargs));self.finished=True
                self.guard.state[self.trial_id]='complete'

        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'worktree';root.mkdir(mode=0o700)
            local,fd=route.create_private_trial_dir(route.AUDIO_TRIAL_ID,root)
            remote_root=Path(temp)/'remote-srv-s22';remote_root.mkdir(mode=0o700)
            def interrupted_stage(command):
                partial=remote_root/'audio-trials-20260927'/'audio-zero-node-20260927'
                partial.mkdir(parents=True,mode=0o700)
                (partial/'trace.strace').write_text('partial reservation')
                raise subprocess.TimeoutExpired(command,10)
            trial=SimpleNamespace(remote=interrupted_stage)
            guard=FakeGuard()
            try:
                with self.assertRaisesRegex(RuntimeError,'outcome is unknown'):
                    with guard.acquire_operation_lock(root,route.AUDIO_TRIAL_ID,
                                                       route.AUDIO_OPERATION_KIND) as operation, \
                         mock.patch.object(route,'remote_pcm_node_provision',
                                           return_value=({'schema':'fixture'},'fixture-sha')):
                        route.begin_and_stage_trace(trial,operation,fd)
                self.assertEqual(guard.state[route.AUDIO_TRIAL_ID],'unknown')
                self.assertEqual(operation.complete_calls,[])
                self.assertTrue((remote_root/'audio-trials-20260927'/'audio-zero-node-20260927'/'trace.strace').exists())
                note=json.loads((local/'unknown.txt').read_text())
                self.assertTrue(note['pcm_node_may_have_been_created'])
                self.assertTrue(note['remote_trace_stage_may_be_partial'])
                self.assertFalse(note['route_or_pcm_operation_invoked'])
                with self.assertRaisesRegex(RuntimeError,'blocks retry'):
                    with guard.acquire_operation_lock(root,route.AUDIO_TRIAL_ID,
                                                       route.AUDIO_OPERATION_KIND):
                        self.fail('unresolved operation must not be retried')
            finally:os.close(fd)

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
        operation=SimpleNamespace(begin=mock.Mock(),complete=mock.Mock())
        from contextlib import nullcontext
        guard=SimpleNamespace(acquire_operation_lock=lambda *args:nullcontext(operation))
        with mock.patch.object(route,'load_trial_guard',return_value=guard), \
             mock.patch.object(route,'transport_project_root',return_value=Path('/transport')), \
             mock.patch.object(route,'load',return_value=trial), \
             mock.patch.object(route,'verify_live_hci_candidate',
                               return_value={'boot_id':'different-boot'}), \
             mock.patch.object(sys,'argv',[
                 'run-audio-route-prepare-once.py',route.AUDIO_TRIAL_ID,'--execute',
                 '--zero-second','--sample-progress']):
            with self.assertRaisesRegex(RuntimeError,'boot changed during preflight'):
                route.main()
        self.assertEqual(remote_calls,[])
        operation.begin.assert_not_called()

    def test_candidate_identity_error_rejects_before_audio_preflight(self):
        remote_calls=[]
        trial=SimpleNamespace(phone_health=lambda:{'boot_id':'candidate-boot'},
                              remote=lambda command:remote_calls.append(command))
        from contextlib import nullcontext
        operation=SimpleNamespace(begin=mock.Mock(),complete=mock.Mock())
        guard=SimpleNamespace(acquire_operation_lock=lambda *args:nullcontext(operation))
        with mock.patch.object(route,'load_trial_guard',return_value=guard), \
             mock.patch.object(route,'transport_project_root',return_value=Path('/transport')), \
             mock.patch.object(route,'load',return_value=trial), \
             mock.patch.object(route,'verify_live_hci_candidate',
                               side_effect=RuntimeError('RECOVERY hash mismatch')), \
             mock.patch.object(sys,'argv',[
                 'run-audio-route-prepare-once.py',route.AUDIO_TRIAL_ID,'--execute',
                 '--zero-second','--sample-progress']):
            with self.assertRaisesRegex(RuntimeError,'RECOVERY hash mismatch'):
                route.main()
        self.assertEqual(remote_calls,[])
        operation.begin.assert_not_called()

    def test_execute_rejects_trial_alias_before_loading_operation_guard(self):
        with mock.patch.object(route,'load_trial_guard',side_effect=RuntimeError('guard accessed')), \
             mock.patch.object(sys,'argv',['runner','audio-zero-node-20260927-retry','--execute',
                                           '--zero-second','--sample-progress']):
            with self.assertRaises(SystemExit):route.main()

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
