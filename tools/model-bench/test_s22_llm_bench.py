#!/usr/bin/env python3
"""Host-only tests for s22-llm-bench.py (no phone, no llama.cpp): matrix, argv, safety guards, report."""
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location('s22_llm_bench', HERE / 's22-llm-bench.py')
B = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(B)


def rec(name, workload, cfg, status='ok', peak=55.0, **kw):
    return dict({'name': name, 'workload': workload, 'config': cfg, 'status': status, 'peak_c': peak}, **kw)


class Pure(unittest.TestCase):
    def test_builtin_matrix_is_valid_and_has_the_production_profile(self):
        m = B.load_matrix(None)
        self.assertEqual(m['cool2'], {'cpus': [4, 5, 6], 'threads': 2, 'threads_batch': None, 'cap_khz': 1800000})
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'm.json'
            for bad in ({'x': {'cpus': [], 'threads': 2}}, {'x': {'cpus': [4], 'threads': 1, 'threads_batch': 2}}):
                p.write_text(json.dumps(bad))
                with self.assertRaises(ValueError):
                    B.load_matrix(str(p))

    def test_argv(self):
        c = B.MATRIX['cool2-tb3']
        a = B.bench_argv(c)
        self.assertEqual(a[a.index('-t') + 1], '2,3')            # prefill and decode thread counts, one run
        self.assertEqual(a[a.index('-dev') + 1], 'none')
        s = B.server_argv(c)
        self.assertEqual((s[s.index('-t') + 1], s[s.index('-tb') + 1]), ('2', '3'))
        self.assertEqual(s[s.index('--port') + 1], str(B.PORT))
        self.assertNotIn(str(B.PORT), ('8090', '8091'))
        self.assertNotIn('-tb', B.server_argv(B.MATRIX['cool2']))
        self.assertEqual(B.bench_argv(B.MATRIX['cool2'])[B.bench_argv(B.MATRIX['cool2']).index('-t') + 1], '2')

    def test_caps_ok_refuses_while_a_cap_is_active(self):
        vals = {'scaling_max_freq': '2515000', 'cpuinfo_max_freq': '2515000'}
        read = lambda p: vals[p.rsplit('/', 1)[1]]
        self.assertTrue(B.caps_ok(read)[0])
        vals['scaling_max_freq'] = '1728000'                    # the 10-11 incident: the proxy's cap was live
        self.assertFalse(B.caps_ok(read)[0])
        self.assertFalse(B.caps_ok(lambda p: (_ for _ in ()).throw(OSError()))[0])

    def test_parsers(self):
        out = ('noise\n{"n_prompt": 512, "n_gen": 0, "n_threads": 3, "avg_ts": 47.5, "stddev_ts": 0.2}\n'
               '{"n_prompt": 0, "n_gen": 64, "n_threads": 2, "avg_ts": 8.1, "stddev_ts": 0.0}\n{bad\n')
        rows = B.bench_rows(out)
        self.assertEqual([(r['n_threads'], r['avg_ts']) for r in rows], [(3, 47.5), (2, 8.1)])
        self.assertIn('MATMUL_INT8 = 1', B.system_info('x\nsystem_info: n_threads = 2 | CPU : NEON = 1 | '
                                                     'MATMUL_INT8 = 1 | REPACK = 1 |\n'))
        self.assertIsNone(B.system_info('nothing here'))

    def test_report_picks_the_fastest_cool_config_and_says_what_to_set(self):
        recs = [
            rec('cool2', 'bench', B.MATRIX['cool2'], rows=[{'n_prompt': 512, 'n_threads': 2, 'avg_ts': 32},
                                                           {'n_gen': 64, 'n_threads': 2, 'avg_ts': 8.0}]),
            rec('cool2', 'turn', B.MATRIX['cool2'], turns=[{'prompt_ms': 9000, 'predicted_per_second': 10.5}] * 3),
            rec('cool2-tb3', 'bench', B.MATRIX['cool2-tb3'], rows=[{'n_prompt': 512, 'n_threads': 3, 'avg_ts': 46},
                                                                   {'n_gen': 64, 'n_threads': 2, 'avg_ts': 8.0}]),
            rec('cool2-tb3', 'turn', B.MATRIX['cool2-tb3'], peak=61,
                turns=[{'prompt_ms': 6300, 'predicted_per_second': 10.4}] * 3),
            rec('mid2-tb3', 'turn', B.MATRIX['mid2-tb3'], peak=67,                  # faster but too hot
                turns=[{'prompt_ms': 5100, 'predicted_per_second': 10.6}] * 3),
            rec('big2-tb4', 'turn', B.MATRIX['big2-tb4'], status='aborted-thermal-67', peak=67),
        ]
        s = B.summarize(recs)
        self.assertEqual((s['cool2']['pp'], s['cool2']['tg']), (32, 8.0))
        self.assertEqual(s['cool2-tb3']['pp'], 46)
        best, why = B.recommend(s)
        self.assertEqual(best, 'cool2-tb3', why)
        self.assertIn('echo 3 > /srv/s22/state/llama-ondemand/threads-batch', B.ondemand_setting(B.MATRIX[best]))
        self.assertIn('rm -f', B.ondemand_setting(B.MATRIX['cool2']))
        self.assertIn('review first', B.ondemand_setting(B.MATRIX['mid2-tb3']))
        self.assertEqual(B.recommend({'x': {'ok': False, 'peak_c': 70}})[0], None)

    def test_answer_hash_flags_a_kernel_that_changes_the_greedy_answer(self):
        turn = [{'prompt_ms': 9000, 'predicted_per_second': 10.5}]
        s = B.summarize([rec('cool2', 'turn', B.MATRIX['cool2'], turns=turn, answer_sha='aa'),
                         rec('cool2-kleidi', 'turn', B.MATRIX['cool2-kleidi'], turns=turn, answer_sha='bb'),
                         rec('cool2-tb3', 'turn', B.MATRIX['cool2-tb3'], turns=turn, answer_sha='aa'),
                         rec('cool3', 'bench', B.MATRIX['cool3'], rows=[])])
        self.assertEqual({n: v['answer_differs'] for n, v in s.items()},
                         {'cool2': False, 'cool2-kleidi': True, 'cool2-tb3': False, 'cool3': False})

    def test_report_rejects_a_config_that_decodes_slower(self):
        s = {'cool2': {'ok': True, 'peak_c': 55, 'turn_prompt_ms': 9000, 'turn_decode_tps': 10.5},
             'cool3': {'ok': True, 'peak_c': 60, 'turn_prompt_ms': 6000, 'turn_decode_tps': 9.0}}
        self.assertEqual(B.recommend(s)[0], 'cool2')               # 3 decode threads lost decode speed

    def test_plan_runs_nothing(self):
        buf = io.StringIO()
        with redirect_stdout(buf), mock.patch.object(B.subprocess, 'Popen', side_effect=AssertionError('ran')):
            self.assertEqual(B.main(['plan', '--configs', 'cool2,cool2-tb3', '--workloads', 'bench']), 0)
        lines = [json.loads(l) for l in buf.getvalue().splitlines()]
        self.assertEqual([(l['config'], l['workload']) for l in lines], [('cool2', 'bench'), ('cool2-tb3', 'bench')])


class DeviceGuards(unittest.TestCase):
    """The device-side guards with a fake sysfs (temperature, cpufreq) and a fake workload process."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        d = Path(self.td.name)
        self.zone = d / 'temp'
        self.zone.write_text('45000\n')
        self.patches = [mock.patch.object(B, 'ZONES', {'BIG': str(self.zone)}),
                        mock.patch.object(B, 'CPUFREQ', str(d) + '/cpu%d_%s')]
        for p in self.patches:
            p.start()
        for cpu in (4, 7):
            (d / f'cpu{cpu}_scaling_max_freq').write_text('2515000\n')

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.td.cleanup()

    def test_hold_is_taken_and_only_our_own_is_removed(self):
        h = Path(self.td.name) / 'hold' / 'llama'
        with B.Hold(h):
            self.assertTrue(h.read_text().startswith('s22-llm-bench'))
        self.assertFalse(h.exists())
        h.write_text('s22-guardian\n')                          # someone else's hold: refuse, leave it
        with self.assertRaises(RuntimeError):
            with B.Hold(h):
                pass
        self.assertEqual(h.read_text(), 's22-guardian\n')

    def test_cap_restores_exactly_what_it_read(self):
        p = Path(B.CPUFREQ % (4, 'scaling_max_freq'))
        with B.Cap(1800000, [4, 5, 6]):
            self.assertEqual(p.read_text(), '1800000')
        self.assertEqual(p.read_text().strip(), '2515000')
        with B.Cap(None, [4, 5, 6]):
            self.assertEqual(p.read_text().strip(), '2515000')

    def test_waits_for_the_resident_model_to_unload(self):
        proc = Path(self.td.name) / 'proc'
        for pid, argv in ((101, [b'/usr/bin/python3', b'/srv/s22/hardware/bin/s22-llama-ondemand', b'serve']),
                          (202, [b'/system/bin/llama-server-b30266', b'-m', b'/models/x.gguf'])):
            (proc / str(pid)).mkdir(parents=True)
            (proc / str(pid) / 'cmdline').write_bytes(b'\0'.join(argv) + b'\0')
        (proc / 'self').mkdir()
        self.assertEqual(B.backend_pids(proc), [202])                     # the proxy itself does not count
        with self.assertRaises(RuntimeError):
            B.wait_unloaded(0, proc)
        (proc / '202' / 'cmdline').write_bytes(b'')                       # it unloaded (a zombie has no cmdline)
        self.assertEqual(B.wait_unloaded(5, proc, poll_s=0.01), 0.0)

    def test_guarded_kills_its_own_process_on_heat(self):
        prog = [sys.executable, '-c', 'import time,sys; print("{}"); sys.stdout.flush(); time.sleep(30)']
        self.zone.write_text('70000\n')
        g = B.guarded(prog, [0], abort_c=66, timeout_s=20)
        self.assertEqual(g['status'], 'aborted-thermal-70')
        self.assertLess(g['wall_s'], 10)
        self.zone.write_text('45000\n')
        g = B.guarded([sys.executable, '-c', 'print("{\\"n_prompt\\": 1}")'], [0], abort_c=66, timeout_s=20)
        self.assertEqual((g['status'], B.bench_rows(g['stdout'])[0]['n_prompt']), ('ok', 1))
        g = B.guarded([sys.executable, '-c', 'raise SystemExit(3)'], [0], abort_c=66, timeout_s=20)
        self.assertEqual(g['status'], 'failed-rc-3')          # e.g. a binary that was never staged


if __name__ == '__main__':
    unittest.main()
