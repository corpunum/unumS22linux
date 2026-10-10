#!/usr/bin/env python3
"""Hardware-free tests for s22-touchd (gesture recogniser, multitouch reader, control socket)
and s22-ui (CLI against a fake daemon). Run: python3 tools/touchui/test_s22_touchd.py"""
import importlib.machinery
import importlib.util
import json
import os
import socket
import tempfile
import threading
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent


def load(name, file):
    loader = importlib.machinery.SourceFileLoader(name, str(HERE / file))
    spec = importlib.util.spec_from_loader(name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


td = load('s22_touchd', 's22-touchd.py')
ui = load('s22_ui', 's22-ui.py')

ABS, SYN = td.EV_ABS, td.EV_SYN


class EdgeSwipeTest(unittest.TestCase):
    def swipe(self, pts, dt=0.02):
        r = td.EdgeSwipe()
        t = 0.0
        r.begin(*pts[0], t)
        for p in pts[1:]:
            t += dt
            r.feed(*p, t)
        return r.end(t)

    def test_bottom_edge_up_is_home(self):
        self.assertEqual(self.swipe([(0.5, 0.995), (0.5, 0.9), (0.52, 0.8)]), 'home')

    def test_top_edge_down_is_switcher(self):
        self.assertEqual(self.swipe([(0.5, 0.01), (0.5, 0.2)]), 'switcher')

    def test_side_edges_are_back(self):
        self.assertEqual(self.swipe([(0.01, 0.5), (0.3, 0.52)]), 'back')
        self.assertEqual(self.swipe([(0.99, 0.5), (0.7, 0.5)]), 'back')

    def test_mid_screen_swipe_and_taps_are_ignored(self):
        self.assertIsNone(self.swipe([(0.5, 0.6), (0.5, 0.3)]))
        self.assertIsNone(self.swipe([(0.5, 0.995), (0.5, 0.99)]))        # too short
        self.assertIsNone(self.swipe([(0.5, 0.995), (0.95, 0.85)]))       # too diagonal

    def test_slow_swipe_is_ignored(self):
        self.assertIsNone(self.swipe([(0.5, 0.995), (0.5, 0.7)], dt=2.0))

    def test_cancel(self):
        r = td.EdgeSwipe()
        r.begin(0.5, 0.995, 0)
        r.feed(0.5, 0.7, 0.1)
        r.cancel()
        self.assertIsNone(r.end(0.2))


class TouchReaderTest(unittest.TestCase):
    def run_events(self, evs):
        got = []
        rd = td.TouchReader(td.EdgeSwipe(), got.append, (0, 4095), (0, 4095))
        t = 0.0
        for e in evs:
            t += 0.02
            rd.event(*e, t)
        return got

    def finger(self, slot, tid, pts, lift=True):
        evs = [(ABS, td.ABS_MT_SLOT, slot), (ABS, td.ABS_MT_TRACKING_ID, tid)]
        for x, y in pts:
            evs += [(ABS, td.ABS_MT_POSITION_X, int(x * 4095)), (ABS, td.ABS_MT_POSITION_Y, int(y * 4095)), (SYN, 0, 0)]
        if lift:
            evs += [(ABS, td.ABS_MT_TRACKING_ID, -1), (SYN, 0, 0)]
        return evs

    def test_single_finger_home(self):
        self.assertEqual(self.run_events(self.finger(0, 7, [(0.5, 0.997), (0.5, 0.9), (0.5, 0.75)])), ['home'])

    def test_second_finger_cancels(self):
        evs = self.finger(0, 7, [(0.5, 0.997), (0.5, 0.9)], lift=False)
        evs += self.finger(1, 8, [(0.2, 0.5)], lift=False)
        evs += [(ABS, td.ABS_MT_SLOT, 0), (ABS, td.ABS_MT_POSITION_Y, int(0.7 * 4095)), (SYN, 0, 0),
                (ABS, td.ABS_MT_TRACKING_ID, -1), (SYN, 0, 0),
                (ABS, td.ABS_MT_SLOT, 1), (ABS, td.ABS_MT_TRACKING_ID, -1), (SYN, 0, 0)]
        self.assertEqual(self.run_events(evs), [])

    def test_two_gestures_in_a_row(self):
        evs = self.finger(0, 1, [(0.01, 0.5), (0.4, 0.5)]) + self.finger(0, 2, [(0.5, 0.01), (0.5, 0.3)])
        self.assertEqual(self.run_events(evs), ['back', 'switcher'])


class SocketTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        run = Path(self.tmp.name)
        td.RUN, td.SOCK = run, run / 'ctl.sock'
        ui.RUN, ui.SOCK = run, run / 'ctl.sock'
        self.calls = []
        td.ui_call = lambda fn, *a, timeout=8: (self.calls.append((fn,) + a), {'ok': True, 'result': 'ok'})[1]
        td.display_state = lambda: 'on'
        self.d = td.Daemon(gestures=False, ui=False)
        self.th = threading.Thread(target=self.d.serve, daemon=True)
        self.th.start()
        for _ in range(100):
            if td.SOCK.exists():
                break
            threading.Event().wait(0.02)

    def tearDown(self):
        self.d.stopping.set()
        self.th.join(3)
        self.tmp.cleanup()

    def test_unknown_command_rejected(self):
        r = td.request({'cmd': 'rm -rf'})
        self.assertFalse(r['ok'])
        self.assertEqual(r['error'], 'unknown_command')

    def test_ui_function_allowlist(self):
        self.assertEqual(td.request({'cmd': 'ui', 'args': ['evil']})['error'], 'unknown_ui_function')
        self.assertTrue(td.request({'cmd': 'ui', 'args': ['home']})['ok'])
        self.assertEqual(self.calls[-1], ('home',))

    def test_gesture_command_routes_to_ui(self):
        self.assertTrue(td.request({'cmd': 'gesture', 'name': 'switcher'})['ok'])
        self.assertEqual(self.calls[-1], ('switcher',))
        self.assertFalse(td.request({'cmd': 'gesture', 'name': 'x'})['ok'])

    def test_model_rejects_unknown_choice(self):
        r = td.request({'cmd': 'model', 'arg': 'gpt-evil'})
        self.assertEqual(r['error'], 'bad_choice')

    def test_cli_card_and_open(self):
        self.assertTrue(ui.main(['card', 'T', 'body', 'text', '--ttl', '5'])['ok'])
        self.assertEqual(self.calls[-1], ('card', 'T', 'body text', '5'))
        ui.main(['open', 'settings'])
        self.assertEqual(self.calls[-1], ('open', 'settings'))

    def test_cli_confirm_reads_answer_file(self):
        def answer_soon():
            threading.Event().wait(0.3)
            cid = self.calls[-1][1]
            (td.RUN / 'confirm').mkdir(exist_ok=True)
            (td.RUN / 'confirm' / f'{cid}.json').write_text(json.dumps({'answer': 'no'}))
        orig = td.ui_call

        def ui_call(fn, *a, timeout=8):
            orig(fn, *a, timeout=timeout)
            return {'ok': True, 'result': 'asking'}
        td.ui_call = ui_call
        threading.Thread(target=answer_soon, daemon=True).start()
        r = ui.main(['confirm', 'Proceed?', '--timeout', '5'])
        self.assertEqual(r['answer'], 'no')

    def test_cli_reports_unreachable_daemon(self):
        ui.SOCK = Path(self.tmp.name) / 'missing.sock'
        self.assertEqual(ui.main(['home'])['error'], 'touchd_unreachable')


class SessionEnvTest(unittest.TestCase):
    """Hyprland is preferred; the opt-in sway-pixman desktop is found via its session file."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / 'run/user/0').mkdir(parents=True)
        self.saved = (td.CHROOT, td.SWAY_SESSION, td.pids_by_comm, td.session_bus)
        td.CHROOT = self.root
        td.SWAY_SESSION = self.root / 'run/s22-desktop/session'
        self.running = set()
        td.pids_by_comm = lambda comm: [1] if comm in self.running else []
        td.session_bus = lambda comp='Hyprland': f'unix:path=/tmp/{comp}'

    def tearDown(self):
        td.CHROOT, td.SWAY_SESSION, td.pids_by_comm, td.session_bus = self.saved
        self.tmp.cleanup()

    def sway(self, display='wayland-1', sock='/run/user/0/sway-ipc.0.9.sock'):
        self.running.add('sway')
        td.SWAY_SESSION.parent.mkdir(parents=True, exist_ok=True)
        td.SWAY_SESSION.write_text(f'{display}\n{sock}\n')
        (self.root / 'run/user/0' / display).touch()

    def test_no_session(self):
        self.assertIsNone(td.session_env())

    def test_sway_session(self):
        self.sway()
        env = td.session_env()
        self.assertEqual(env['WAYLAND_DISPLAY'], 'wayland-1')
        self.assertEqual(env['SWAYSOCK'], '/run/user/0/sway-ipc.0.9.sock')
        self.assertNotIn('HYPRLAND_INSTANCE_SIGNATURE', env)
        self.assertEqual(env['DBUS_SESSION_BUS_ADDRESS'], 'unix:path=/tmp/sway')

    def test_stale_sway_file_without_process_is_ignored(self):
        self.sway()
        self.running.clear()
        self.assertIsNone(td.session_env())

    def test_sway_display_must_be_a_plain_socket_name(self):
        self.sway()
        td.SWAY_SESSION.write_text('../../etc/passwd\n\n')
        self.assertIsNone(td.session_env())

    def test_hyprland_preferred_when_both_run(self):
        self.sway(display='wayland-2')
        sig = self.root / 'run/user/0/hypr/abc'
        sig.mkdir(parents=True)
        (sig / '.socket.sock').touch()
        (self.root / 'run/user/0/wayland-1').touch()
        env = td.session_env()
        self.assertEqual(env['HYPRLAND_INSTANCE_SIGNATURE'], 'abc')
        self.assertEqual(env['WAYLAND_DISPLAY'], 'wayland-1')


if __name__ == '__main__':
    unittest.main()
