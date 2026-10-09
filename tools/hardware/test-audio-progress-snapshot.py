#!/usr/bin/env python3
import importlib.util
from pathlib import Path
import tempfile
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
    def test_decode_soc4_rdma2_status_pair(self):
        status=module.decode_rdma2_status(0x80000000 | (0x5a << 20) | 0x12345)
        self.assertTrue(status['progress'])
        self.assertEqual(status['rbuf_offset'],0x5a)
        self.assertEqual(status['rbuf_count'],0x12345)
        self.assertEqual(module.decode_rdma2_status_add(0xc1234567)['current_address'],
                         0x41234567)
        for invalid in (True,-1,0x100000000,'1'):
            with self.subTest(invalid=invalid),self.assertRaises(ValueError):
                module.decode_rdma2_status(invalid)
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
    def test_parse_alsa_progress_and_buffer_geometry(self):
        status='state: RUNNING\n\nowner_pid   : 23\nhw_ptr      : 2048\nappl_ptr    : 4096\ndelay       : 12\navail       : 8000\navail_max   : 8192\n'
        hw='access: RW_INTERLEAVED\nformat: S16_LE\nsubformat: STD\nchannels: 2\nrate: 48000 (48000/1)\nperiod_size: 1024\nbuffer_size: 8192\n'
        self.assertEqual(module.parse_alsa_status(status),{
            'state':'RUNNING','hw_ptr':2048,'appl_ptr':4096,'delay':12,
            'avail':8000,'avail_max':8192})
        self.assertEqual(module.parse_hw_params(hw),{
            'access':'RW_INTERLEAVED','format':'S16_LE','subformat':'STD',
            'channels':2,'rate':48000,'period_size':1024,'buffer_size':8192})
    def test_period_boundaries_are_a_proxy_not_irq_or_sound_acceptance(self):
        samples=[
            {'sequence':0,'monotonic':1.0,'alsa_counters':{'state':'RUNNING','hw_ptr':1024},
             'hw_params_parsed':{'period_size':1024}},
            {'sequence':1,'monotonic':2.0,'alsa_counters':{'state':'RUNNING','hw_ptr':3072},
             'hw_params_parsed':{'period_size':1024}},
        ]
        result=module.period_progress(samples)
        self.assertTrue(result['verified'])
        self.assertEqual(result['periods_advanced'],2)
        self.assertFalse(result['irq_counter_available'])
        self.assertIn('proxy',result['irq_counter_reason'])
        self.assertIn('Observed samples only',result['continuity_scope'])
    def test_period_progress_rejects_capture_sequence_gap(self):
        samples=[
            {'sequence':0,'monotonic':1.0,
             'alsa_counters':{'state':'RUNNING','hw_ptr':0},
             'hw_params_parsed':{'period_size':1024}},
            {'sequence':2,'monotonic':2.0,
             'alsa_counters':{'state':'RUNNING','hw_ptr':2048},
             'hw_params_parsed':{'period_size':1024}},
        ]
        result=module.period_progress(samples)
        self.assertFalse(result['verified'])
        self.assertEqual(result['periods_advanced'],0)
        self.assertIn('sequence gap',result['reason'])
    def test_period_progress_requires_complete_valid_sequences(self):
        def running(timestamp, pointer, sequence_marker=None, include_sequence=True):
            sample={'monotonic':timestamp,
                    'alsa_counters':{'state':'RUNNING','hw_ptr':pointer},
                    'hw_params_parsed':{'period_size':1024}}
            if include_sequence:
                sample['sequence']=sequence_marker
            return sample
        all_missing=[running(1.0,0,include_sequence=False),
                     running(2.0,2048,include_sequence=False)]
        mixed=[running(1.0,0,0),running(2.0,2048,include_sequence=False)]
        for samples in (all_missing,mixed):
            with self.subTest(samples=samples):
                result=module.period_progress(samples)
                self.assertFalse(result['verified'])
                self.assertIn('sequence missing',result['reason'])
        for invalid in (None,True,-1,'1'):
            with self.subTest(invalid=invalid):
                result=module.period_progress([running(1.0,0,0),
                                               running(2.0,2048,invalid)])
                self.assertFalse(result['verified'])
                self.assertEqual(result['reason'],'invalid RUNNING sample')
    def test_period_progress_fails_closed_on_missing_geometry_and_time_regression(self):
        samples=[
            {'sequence':0,'monotonic':2.0,'alsa_counters':{'state':'RUNNING','hw_ptr':0},
             'hw_params_parsed':{'period_size':1024}},
            {'sequence':1,'monotonic':1.0,'alsa_counters':{'state':'RUNNING','hw_ptr':2048},
             'hw_params_parsed':{'period_size':1024}},
        ]
        self.assertFalse(module.period_progress(samples)['verified'])
        self.assertFalse(module.period_progress([samples[0],{
            'sequence':1,'monotonic':3.0,'alsa_counters':{'state':'RUNNING','hw_ptr':2048},
            'hw_params_parsed':{}}])['verified'])
    def test_period_progress_rejects_gaps_inside_running_window(self):
        def running(timestamp, pointer, sequence):
            return {'sequence':sequence,'monotonic':timestamp,
                    'alsa_counters':{'state':'RUNNING','hw_ptr':pointer},
                    'hw_params_parsed':{'period_size':1024}}
        gaps=(
            {'sequence':1,'error_type':'OSError','monotonic':1.5,
             'sample_monotonic_ns':1500000000},
            {'sequence':1,'monotonic':1.5,'alsa_counters':{'state':'XRUN','hw_ptr':1024},
             'hw_params_parsed':{'period_size':1024}},
        )
        for gap in gaps:
            with self.subTest(gap=gap):
                result=module.period_progress([running(1.0,0,0),gap,
                                               running(2.0,2048,2)])
                self.assertFalse(result['verified'])
                self.assertEqual(result['periods_advanced'],0)
                self.assertIn('separated',result['reason'])
    def test_nonrunning_samples_outside_running_window_do_not_hide_progress(self):
        samples=[
            {'monotonic':0.5,'alsa_counters':{'state':'PREPARED'}},
            {'sequence':1,'monotonic':1.0,'alsa_counters':{'state':'RUNNING','hw_ptr':0},
             'hw_params_parsed':{'period_size':1024}},
            {'sequence':2,'monotonic':2.0,'alsa_counters':{'state':'RUNNING','hw_ptr':2048},
             'hw_params_parsed':{'period_size':1024}},
            {'monotonic':2.5,'alsa_counters':{'state':'SETUP'}},
        ]
        self.assertTrue(module.period_progress(samples)['verified'])
    def test_timed_read_captures_monotonic_interval_and_bounds_content(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'sample';path.write_text('observed\n')
            record=module.timed_text(path,32)
            self.assertEqual(record['status'],'ok')
            self.assertEqual(record['value'],'observed')
            self.assertLessEqual(record['start_monotonic_ns'],record['end_monotonic_ns'])
            path.write_text('too long')
            self.assertEqual(module.timed_text(path,3)['status'],'too_large')
            self.assertEqual(module.timed_text(Path(temp)/'absent')['status'],'not_present')
    def test_clock_capture_only_reads_named_observation_points(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            names=[name for name,_ in module.UAIF1_CLOCKS]
            for name,rate in ((name,'12288000') for name in names):
                clock=root/name;clock.mkdir()
                for key,value in (('clk_rate',rate),('clk_enable_count','1'),('clk_prepare_count','1')):
                    (clock/key).write_text(value+'\n')
            (root/'unrelated-clk').mkdir()
            read_paths=[]
            def reader(path,limit):
                read_paths.append(Path(path))
                return module.timed_text(path,limit)
            result=module._capture_clock_observations(root,allow_reads=True,reader=reader)
            self.assertEqual(result['status'],'ok')
            by_name={item['name']:item for item in result['clocks']}
            self.assertEqual(set(by_name),set(names))
            self.assertEqual(by_name['DOUT_DIV_CLK_AUD_UAIF1']['clk_rate']['value'],'12288000')
            self.assertEqual({path.parent.name for path in read_paths},set(names))
            self.assertLessEqual(result['start_monotonic_ns'],result['end_monotonic_ns'])
    def test_clock_reads_are_skipped_without_runtime_pm_gate(self):
        calls=[]
        result=module._capture_clock_observations('/unused',reader=lambda *args:calls.append(args),
                                                  allow_reads=False)
        self.assertEqual(result['status'],'skipped_pm_gate')
        self.assertEqual(result['clocks'],[])
        self.assertEqual(calls,[])
    def test_dapm_capture_uses_exact_component_widget_paths(self):
        calls=[]
        def reader(path,limit):
            calls.append((Path(path),limit))
            return {'status':'not_present'}
        result=module._capture_dapm_observations('/fake/asoc',reader=reader)
        expected={Path('/fake/asoc')/owner/'dapm'/widget
                  for owner,widgets in module.DAPM_WIDGET_PATHS.items()
                  for widget in widgets}
        self.assertEqual({path for path,_ in calls},expected)
        self.assertEqual(len(calls),24)
        self.assertTrue(all(limit==module.MAX_DAPM_READ_BYTES for _,limit in calls))
        self.assertNotIn(Path('/fake/asoc/Rainbow-Prince/dpcm/RDMA2/state'),
                         {path for path,_ in calls})

    def test_parse_source_asoc_dpcm_state_and_no_backend_case(self):
        started='[RDMA2 - Playback]\nState: start\nBackends:\n- UAIF1\n   State: start\n'
        parsed=module.parse_dpcm_state(started)
        self.assertEqual(parsed['streams'][0]['state'],'start')
        self.assertEqual(parsed['streams'][0]['backends'],[{'name':'UAIF1','state':'start'}])
        no_backend=module.parse_dpcm_state(
            '[RDMA2 - Playback]\nState: start\nBackends:\n No active DSP links\n')
        self.assertTrue(no_backend['streams'][0]['no_active_backends'])

if __name__=='__main__':unittest.main()
