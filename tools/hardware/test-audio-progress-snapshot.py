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
            {'monotonic':1.0,'alsa_counters':{'state':'RUNNING','hw_ptr':1024},
             'hw_params_parsed':{'period_size':1024}},
            {'monotonic':2.0,'alsa_counters':{'state':'RUNNING','hw_ptr':3072},
             'hw_params_parsed':{'period_size':1024}},
        ]
        result=module.period_progress(samples)
        self.assertTrue(result['verified'])
        self.assertEqual(result['periods_advanced'],2)
        self.assertFalse(result['irq_counter_available'])
        self.assertIn('proxy',result['irq_counter_reason'])
    def test_period_progress_fails_closed_on_missing_geometry_and_time_regression(self):
        samples=[
            {'monotonic':2.0,'alsa_counters':{'state':'RUNNING','hw_ptr':0},
             'hw_params_parsed':{'period_size':1024}},
            {'monotonic':1.0,'alsa_counters':{'state':'RUNNING','hw_ptr':2048},
             'hw_params_parsed':{'period_size':1024}},
        ]
        self.assertFalse(module.period_progress(samples)['verified'])
        self.assertFalse(module.period_progress([samples[0],{
            'monotonic':3.0,'alsa_counters':{'state':'RUNNING','hw_ptr':2048},
            'hw_params_parsed':{}}])['verified'])
    def test_period_progress_rejects_gaps_inside_running_window(self):
        def running(timestamp, pointer):
            return {'monotonic':timestamp,
                    'alsa_counters':{'state':'RUNNING','hw_ptr':pointer},
                    'hw_params_parsed':{'period_size':1024}}
        gaps=(
            {'error_type':'OSError','monotonic':1.5,'sample_monotonic_ns':1500000000},
            {'monotonic':1.5,'alsa_counters':{'state':'XRUN','hw_ptr':1024},
             'hw_params_parsed':{'period_size':1024}},
        )
        for gap in gaps:
            with self.subTest(gap=gap):
                result=module.period_progress([running(1.0,0),gap,running(2.0,2048)])
                self.assertFalse(result['verified'])
                self.assertEqual(result['periods_advanced'],0)
                self.assertIn('separated',result['reason'])
    def test_nonrunning_samples_outside_running_window_do_not_hide_progress(self):
        samples=[
            {'monotonic':0.5,'alsa_counters':{'state':'PREPARED'}},
            {'monotonic':1.0,'alsa_counters':{'state':'RUNNING','hw_ptr':0},
             'hw_params_parsed':{'period_size':1024}},
            {'monotonic':2.0,'alsa_counters':{'state':'RUNNING','hw_ptr':2048},
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
            for name,rate in (('dout_audif', '12288000'),('abox_sclk','24576000'),('gpu_core','123')):
                clock=root/name;clock.mkdir()
                for key,value in (('clk_rate',rate),('clk_enable_count','1'),('clk_prepare_count','1')):
                    (clock/key).write_text(value+'\n')
            result=module._capture_clock_observations(root)
            self.assertEqual(result['status'],'ok')
            by_name={item['name']:item for item in result['clocks']}
            self.assertEqual(set(by_name),{'dout_audif','abox_sclk'})
            self.assertEqual(by_name['dout_audif']['clk_rate']['value'],'12288000')
            self.assertLessEqual(result['start_monotonic_ns'],result['end_monotonic_ns'])

if __name__=='__main__':unittest.main()
