#!/usr/bin/env python3
"""Host-only tests for s22-guardian (no /proc, /sys or phone access).
Run: python3 tools/hardware/test_s22_guardian.py"""
import importlib.machinery
import importlib.util
import json
import signal
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
loader = importlib.machinery.SourceFileLoader('s22_guardian', str(HERE / 's22-guardian.py'))
spec = importlib.util.spec_from_loader('s22_guardian', loader)
mod = importlib.util.module_from_spec(spec)
loader.exec_module(mod)
HZ = mod.HZ

LLAMA = '/system/bin/llama-server -m /models/Qwen3.5-0.8B-Q4_0.gguf -ngl 99'
SEARCH = '/usr/local/bin/unumsearch --config /srv/s22/unumsearch/config.toml serve'


def proc(cmd, ticks=0, ppid=100, state='S', comm=None, kthread=False):
    return {'ppid': ppid, 'state': state, 'ticks': ticks, 'cmd': cmd,
            'comm': comm or cmd.split(' ')[0].rsplit('/', 1)[-1], 'kthread': kthread}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / 'chroot/run/s22-touch').mkdir(parents=True)
        self.notes, self.kills, self.nice = [], [], {}
        self.now = 1000.0
        self.g = mod.Guardian(json.loads(json.dumps(mod.DEFAULTS)), state=root / 'state', run=root / 'run',
                              hold_dir=root / 'hold', chroot=root / 'chroot',
                              notify=lambda t, b: self.notes.append(t),
                              killer=lambda pid, sig: self.kills.append((pid, sig)),
                              renicer=self.renice, clock=lambda: self.now)
        self.root = root

    def renice(self, pid, value):
        old = self.nice.get(pid, 0)
        if value is not None:
            self.nice[pid] = value
        return old

    def tearDown(self):
        self.tmp.cleanup()

    def table(self):
        return {10: proc(LLAMA), 11: proc(SEARCH), 12: proc('node src/server.mjs'),
                13: proc('/usr/sbin/sshd -D')}

    def run_pass(self, tmax, table=None, mem=4000.0, disks=None, confirm=True):
        if confirm and tmax >= self.g.cfg['hot_c']:     # levels >= 2 need two passes
            self.g.check_once(now=self.now, temps={'BIG': tmax}, table=table or self.table(), mem=mem,
                              disks=disks or {'/': 21.0, '/srv/s22': 90000.0})
        return self.g.check_once(now=self.now, temps={'BIG': tmax, 'MID': tmax - 3}, table=table or self.table(),
                                 mem=mem, disks=disks or {'/': 21.0, '/srv/s22': 90000.0})


class ThermalTest(Base):
    def test_cool_does_nothing(self):
        st = self.run_pass(45)
        self.assertEqual(st['level'], 0)
        self.assertEqual(self.kills, [])
        self.assertEqual(self.notes, [])
        env = (self.root / 'chroot/run/s22-touch/guardian.env').read_text()
        self.assertIn('g_level=0', env)

    def test_warn_logs_and_alerts_only(self):
        st = self.run_pass(62)
        self.assertEqual(st['level'], 1)
        self.assertEqual(self.kills, [])
        self.assertEqual(len(self.notes), 1)

    def test_hot_holds_llama_and_renices(self):
        st = self.run_pass(71)
        self.assertEqual(st['level'], 2)
        self.assertTrue((self.root / 'hold/llama').exists())
        self.assertIn((10, signal.SIGTERM), self.kills)
        self.assertNotIn(12, [p for p, _ in self.kills])        # OpenUnum untouched
        self.assertEqual(self.nice.get(11), 19)
        self.assertFalse((self.root / 'hold/unumsearch').exists())

    def test_critical_stops_nonessential_only(self):
        self.run_pass(81)
        self.assertTrue((self.root / 'hold/unumsearch').exists())
        killed = {p for p, _ in self.kills}
        self.assertEqual(killed, {10, 11})

    def test_single_spike_does_not_stop_services(self):
        st = self.run_pass(81, confirm=False)
        self.assertEqual(st['level'], 1)
        self.assertEqual(self.kills, [])
        self.now += 20
        self.assertEqual(self.run_pass(50, confirm=False)['level'], 0)
        self.assertEqual(self.kills, [])

    def test_hysteresis_and_restore(self):
        self.run_pass(72)
        self.now += 20
        self.assertEqual(self.run_pass(64)['level'], 2)       # still hot side of the band
        self.now += 20
        self.assertEqual(self.run_pass(56)['level'], 2)
        self.now += 20
        st = self.run_pass(54)
        self.assertEqual(st['level'], 0)
        self.assertFalse((self.root / 'hold/llama').exists())
        self.assertEqual(self.nice.get(11), 0)                # nice restored

    def test_stale_holds_from_previous_run_released_when_cool(self):
        (self.root / 'hold').mkdir()
        (self.root / 'hold/llama').write_text('s22-guardian\n')
        (self.root / 'hold/other').write_text('owner\n')       # not ours: left alone
        self.run_pass(40)
        self.assertFalse((self.root / 'hold/llama').exists())
        self.assertTrue((self.root / 'hold/other').exists())


class RestartTest(Base):
    def test_nice_values_survive_a_guardian_restart(self):
        self.run_pass(71)
        self.assertEqual(self.nice.get(11), 19)
        g2 = mod.Guardian(self.g.cfg, state=self.root / 'state', run=self.root / 'run', hold_dir=self.root / 'hold',
                          chroot=self.root / 'chroot', notify=lambda t, b: None, killer=lambda p, s: None,
                          renicer=self.renice, clock=lambda: self.now)
        g2.check_once(now=self.now, temps={'BIG': 40}, table=self.table(), mem=4000, disks={})
        self.assertEqual(self.nice.get(11), 0)
        self.assertFalse((self.root / 'hold/llama').exists())


class RunawayTest(Base):
    def spin(self, pid, cmd, ppid=100, minutes=31, state='R', kthread=False):
        self.ticks = getattr(self, 'ticks', {})
        ticks = self.ticks.get(pid, 0)
        last = None
        for _ in range(int(minutes * 3) + 1):
            last = self.g.check_once(now=self.now, temps={'BIG': 40}, table={pid: proc(cmd, ticks, ppid, state,
                                                                                       kthread=kthread)},
                                     mem=4000, disks={'/': 21.0})
            self.now += 20
            ticks += int(20 * HZ * 0.95)
        self.ticks[pid] = ticks
        return last

    def test_unknown_spinner_terminated_after_30_min(self):
        self.spin(500, '/usr/bin/python3 -m unittest test_x', minutes=29)
        self.assertEqual(self.kills, [])
        self.spin(500, '/usr/bin/python3 -m unittest test_x', minutes=2)
        self.assertIn((500, signal.SIGTERM), self.kills)

    def test_orphan_spinner_faster(self):
        self.spin(501, '/tmp/x', ppid=1, minutes=11)
        self.assertIn((501, signal.SIGTERM), self.kills)

    def test_allowlist_and_kernel_threads_never_touched(self):
        self.spin(502, '/usr/bin/sway -c /root/s22-sway-pixman.conf', minutes=40)
        self.spin(503, '[kworker/0:1]', kthread=True, minutes=40)
        self.spin(504, 'node src/server.mjs', minutes=40)
        self.assertEqual(self.kills, [])

    def test_unkillable_alerts_recovery_reboot(self):
        self.spin(505, '/usr/bin/python3 spin.py', ppid=1, minutes=13)
        self.assertIn((505, signal.SIGKILL), self.kills)
        self.assertTrue(any('unkillable' in n for n in self.notes))
        self.assertEqual(self.g.check_once(now=self.now, temps={'BIG': 40},
                                           table={505: proc('/usr/bin/python3 spin.py', 10**9, 1, 'R')},
                                           mem=4000, disks={})['stuck'], [505])

    def test_idle_process_ignored(self):
        for _ in range(200):
            self.g.check_once(now=self.now, temps={'BIG': 40}, table={600: proc('/tmp/idle', 5)},
                              mem=4000, disks={})
            self.now += 20
        self.assertEqual(self.kills, [])


class FloorTest(Base):
    def test_memory_alert_then_shed_then_restore(self):
        self.run_pass(40, mem=350)
        self.assertEqual(len(self.notes), 1)
        self.assertFalse((self.root / 'hold/llama').exists())
        self.now += 20
        self.run_pass(40, mem=200)
        self.assertTrue((self.root / 'hold/llama').exists())
        self.now += 20
        self.run_pass(40, mem=2000)
        self.assertFalse((self.root / 'hold/llama').exists())

    def test_disk_alert_rate_limited(self):
        self.run_pass(40, disks={'/srv/s22': 2000.0, '/': 21.0})
        self.now += 20
        self.run_pass(40, disks={'/srv/s22': 2000.0, '/': 21.0})
        self.assertEqual(len(self.notes), 1)
        self.now += 21600
        self.run_pass(40, disks={'/srv/s22': 2000.0})
        self.assertEqual(len(self.notes), 2)

    def test_root_overlay_normal_level_is_quiet(self):
        self.run_pass(40, disks={'/': 21.0, '/srv/s22': 90000.0})
        self.assertEqual(self.notes, [])


if __name__ == '__main__':
    unittest.main()
