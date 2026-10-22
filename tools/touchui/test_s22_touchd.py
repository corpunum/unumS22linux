#!/usr/bin/env python3
"""Hardware-free tests for s22-touchd (gesture recogniser, multitouch reader, control socket)
and s22-ui (CLI against a fake daemon). Run: python3 tools/touchui/test_s22_touchd.py"""
import importlib.machinery
import importlib.util
import json
import sys
import time
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



class TtsPlayTest(unittest.TestCase):
    """cmd_tts renders with S22_SAY_OUT, then (play=true) speaks the WAV with s22-say --wav."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.orig = (td.RUN, td.subprocess.run)
        td.RUN = Path(self.tmp.name)
        self.calls = []

        def fake_run(argv, env=None, **kw):
            self.calls.append((argv, dict(env or {})))
            if env and env.get('S22_SAY_OUT'):
                Path(env['S22_SAY_OUT']).write_bytes(b'RIFF')
            class R:
                returncode, stdout, stderr = 0, '', ''
            return R()
        td.subprocess.run = fake_run
        self.d = td.Daemon(gestures=False, ui=False)

    def tearDown(self):
        td.RUN, td.subprocess.run = self.orig
        self.tmp.cleanup()

    def test_render_only_by_default(self):
        r = self.d.cmd_tts({'text': 'hello'})
        self.assertTrue(r['ok']); self.assertFalse(r['played']); self.assertEqual(len(self.calls), 1)

    def test_play_speaks_rendered_wav(self):
        r = self.d.cmd_tts({'text': 'γεια σου', 'engine': 'el-antonis', 'play': True, 'out': 'earpiece'})
        self.assertTrue(r['played']); self.assertEqual(r['out'], 'earpiece')
        argv, env = self.calls[1]
        self.assertEqual(argv[1:4], ['--out', 'earpiece', '--wav'])
        self.assertEqual(argv[4], r['path'])
        self.assertNotIn('S22_SAY_OUT', env)

    def test_bad_out_rejected(self):
        self.assertEqual(self.d.cmd_tts({'text': 'x', 'play': True, 'out': 'hdmi'})['error'], 'bad_out')

class FillerTest(unittest.TestCase):
    """`filler` and tts "filler": true play a pre-rendered clip from the shared bank before the answer."""
    def setUp(self):
        import json, stat
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        bank = root / 'chroot/root/.openunum/assets/voice-fillers'
        (bank / 'en/general').mkdir(parents=True)
        (bank / 'en/general/1.wav').write_bytes(b'RIFF')
        (bank / 'manifest.json').write_text(json.dumps({'version': 1, 'clips': {'en': {'general': [
            {'n': 1, 'phrase': 'Hmm, give me a second', 'durationSec': 1.0}]}}}))
        self.log = root / 'say.log'
        say = root / 'say'
        say.write_text(f'#!/bin/sh\necho "$@" >> {self.log}\n')
        say.chmod(say.stat().st_mode | stat.S_IXUSR)
        self.env = {'S22_CHROOT': str(root / 'chroot'), 'S22_FILLER_DIR': str(bank),
                    'S22_FILLER_STATE': str(root / 'last.json')}
        self.saved = {k: os.environ.get(k) for k in self.env}
        os.environ.update(self.env)
        for m in ('s22_filler',):
            sys.modules.pop(m, None)
        self.orig = (td.SAY, td.RUN, td.subprocess.run)
        td.SAY = str(say)
        td.RUN = root
        def fake_run(argv, env=None, **kw):
            if env and env.get('S22_SAY_OUT'):
                Path(env['S22_SAY_OUT']).write_bytes(b'RIFF')
            class R:
                returncode, stdout, stderr = 0, '', ''
            return R()
        td.subprocess.run = fake_run
        self.d = td.Daemon(gestures=False, ui=False)

    def tearDown(self):
        td.SAY, td.RUN, td.subprocess.run = self.orig
        for k, v in self.saved.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        sys.modules.pop('s22_filler', None)
        # The filler plays from a background thread that may still be writing its
        # state file under the temp dir; give it a moment, then remove the dir
        # (CI raced here: "Directory not empty" on 2026-10-22).
        import threading, time, shutil
        for t in threading.enumerate():
            if t is not threading.current_thread() and t.daemon is False and t.name != 'MainThread':
                t.join(timeout=2)
        for _ in range(20):
            try:
                self.tmp.cleanup()
                break
            except OSError:
                time.sleep(0.1)
        else:
            shutil.rmtree(self.tmp.name, ignore_errors=True)

    def said(self):
        import time
        for _ in range(50):
            if self.log.exists() and self.log.read_text().strip():
                break
            time.sleep(0.05)
        return self.log.read_text().splitlines() if self.log.exists() else []

    def test_filler_command_plays_a_clip(self):
        r = self.d.cmd_filler({'text': 'do the thing', 'out': 'speaker'})
        self.assertTrue(r['ok'], r)
        self.assertEqual((r['lang'], r['category']), ('en', 'general'))
        self.assertTrue(self.said()[0].startswith('--out speaker --wav /root/.openunum/assets/voice-fillers/en/general/1.wav'))

    def test_tts_with_filler_starts_the_clip_then_plays_the_answer(self):
        r = self.d.cmd_tts({'text': 'It is noon.', 'play': True, 'filler': True, 'thinking_for': 'what time is it'})
        self.assertTrue(r['played'])
        self.assertTrue(r['filler']['ok'])

    def test_no_filler_unless_asked_and_bad_out_rejected(self):
        r = self.d.cmd_tts({'text': 'x', 'play': True})
        self.assertNotIn('filler', r)
        self.assertEqual(self.d.cmd_filler({'out': 'hdmi'})['error'], 'bad_out')


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



class CameraPhotoTest(unittest.TestCase):
    """camera_photo(): meter -> burst -> develop, with a fake subprocess.run."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        self.saved = (td.CAMERA_WORK, td.GALLERY, td.RUN, td.DEVELOP, td.CAMERA)
        td.CAMERA_WORK, td.GALLERY, td.RUN = t / 'work', t / 'gallery', t / 'run'
        td.RUN.mkdir()
        td.DEVELOP = str(t / 'dev')
        td.CAMERA = str(t / 'cam')
        Path(td.DEVELOP).write_text('x')
        self.calls = []

    def tearDown(self):
        td.CAMERA_WORK, td.GALLERY, td.RUN, td.DEVELOP, td.CAMERA = self.saved
        self.tmp.cleanup()

    def fake(self, develop_ok=True):
        class R:
            def __init__(s, rc, out):
                s.returncode, s.stdout, s.stderr = rc, out, ''

        def run(cmd, **kw):
            self.calls.append(cmd)
            if cmd[0] == td.DEVELOP and cmd[1] == 'meter':
                return R(0, '{"ok": true, "exposure_us": 30000, "gain": 5.5}')
            if cmd[0] == td.DEVELOP:
                return R(0, '{"ok": true, "stacked": 4, "ms": 300}' if develop_ok else '{"ok": false, "error": "x"}')
            out = cmd[cmd.index('--out') + 1]
            Path(out).write_bytes(b'png')
            return R(0, '{"ok": true}')
        return run

    def test_metered_burst_is_developed(self):
        out = td.camera_photo('front', run=self.fake())
        self.assertTrue(out['develop_ok'])
        self.assertEqual((out['exposure_us'], out['gain'], out['stacked']), (30000, 5.5, 4))
        shot = self.calls[2]
        self.assertIn('--raw-burst', shot)
        self.assertEqual(shot[shot.index('--cis-exposure-us') + 1], '30000')
        self.assertEqual(shot[shot.index('--cis-again') + 1], '5.5')
        self.assertEqual(shot[shot.index('--sensor') + 1], 'front')
        self.assertIn('--stack', self.calls[3])

    def test_develop_failure_falls_back_to_quick_png(self):
        out = td.camera_photo('rear', run=self.fake(develop_ok=False))
        self.assertFalse(out['develop_ok'])
        self.assertEqual(out['rc'], 0)
        self.assertEqual((td.RUN / 'camera-latest.png').read_bytes(), b'png')

    def test_bad_sensor_rejected(self):
        d = td.Daemon.__new__(td.Daemon)
        Path(td.CAMERA).write_text('x')
        self.assertEqual(td.Daemon.cmd_camera(d, {'sensor': 'side'})['error'], 'bad_sensor')

if __name__ == '__main__':
    unittest.main()


class BackendTest(unittest.TestCase):
    """The unum-shell profile: selection, forwarding to shell.sock, rollback to Quickshell."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.run_dir = Path(self.tmp.name)
        self.saved = (td.UI_BACKEND_FILE, td.SHELL_SOCK, td.quickshell_call)
        td.UI_BACKEND_FILE = self.run_dir / 'ui-backend'
        td.SHELL_SOCK = self.run_dir / 'shell.sock'
        self.qs = []
        td.quickshell_call = lambda fn, *a, timeout=8: (self.qs.append((fn,) + a), {'ok': True, 'result': 'qs'})[1]
        os.environ.pop('S22_UI_BACKEND', None)

    def tearDown(self):
        td.UI_BACKEND_FILE, td.SHELL_SOCK, td.quickshell_call = self.saved
        os.environ.pop('S22_UI_BACKEND', None)
        self.tmp.cleanup()

    def fake_shell(self, reply):
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(str(td.SHELL_SOCK))
        srv.listen(1)
        seen = []

        def serve():
            c, _ = srv.accept()
            data = b''
            while not data.endswith(b'\n'):
                data += c.recv(1024)
            seen.append(json.loads(data))
            c.sendall((json.dumps(reply) + '\n').encode())
            c.close()
            srv.close()
        threading.Thread(target=serve, daemon=True).start()
        return seen

    def test_default_is_quickshell(self):
        self.assertEqual(td.ui_backend(), 'quickshell')
        td.ui_call('home')
        self.assertEqual(self.qs, [('home',)])

    def test_profile_file_and_env_select_unum_shell(self):
        td.UI_BACKEND_FILE.write_text('unum-shell\n')
        self.assertEqual(td.ui_backend(), 'unum-shell')
        td.UI_BACKEND_FILE.write_text('garbage')
        self.assertEqual(td.ui_backend(), 'quickshell')
        os.environ['S22_UI_BACKEND'] = 'unum-shell'
        self.assertEqual(td.ui_backend(), 'unum-shell')

    def test_calls_go_to_shell_sock_and_results_are_parsed(self):
        td.UI_BACKEND_FILE.write_text('unum-shell')
        seen = self.fake_shell({'ok': True, 'result': 'card 3'})
        r = td.ui_call('card', 'Title', 'Body', 8)
        self.assertEqual(r, {'ok': True, 'result': 'card 3'})
        self.assertEqual(seen, [{'fn': 'card', 'args': ['Title', 'Body', '8']}])
        self.assertEqual(self.qs, [])

    def test_state_result_is_json_decoded(self):
        td.UI_BACKEND_FILE.write_text('unum-shell')
        self.fake_shell({'ok': True, 'result': '{"page": "chat"}'})
        self.assertEqual(td.ui_call('state')['result'], {'page': 'chat'})

    def test_unreachable_shell_is_reported_and_rollback_restores_quickshell(self):
        td.UI_BACKEND_FILE.write_text('unum-shell')
        self.assertEqual(td.ui_call('home')['error'], 'shell_unreachable')
        td.UI_BACKEND_FILE.write_text('quickshell')
        self.assertTrue(td.ui_call('home')['ok'])
        self.assertEqual(self.qs, [('home',)])

    def test_shell_errors_pass_through(self):
        td.UI_BACKEND_FILE.write_text('unum-shell')
        self.fake_shell({'ok': False, 'error': 'bad_request: x'})
        self.assertEqual(td.ui_call('x'), {'ok': False, 'error': 'bad_request: x'})

    def test_launch_commands(self):
        env = {'WAYLAND_DISPLAY': 'wayland-1', 'HOME': '/root'}
        unum = td.shell_command(env, 'unum-shell')
        self.assertEqual(unum[-1], td.UNUM_SHELL)
        self.assertIn('S22_TOUCH_RUN=/run/s22-touch', unum)
        self.assertIn('quickshell', td.shell_command(env, 'quickshell'))
        self.assertNotIn(td.UNUM_SHELL, td.shell_command(env, 'quickshell'))

    def test_fastshell_launch_and_selection(self):
        env = {'WAYLAND_DISPLAY': 'wayland-1', 'HOME': '/root'}
        fs = td.shell_command(env, 'fastshell')
        self.assertEqual(fs[-1], td.FASTSHELL)
        self.assertNotIn('chroot', fs)                     # native root, not the chroot
        self.assertIn(f'S22_TOUCH_RUN={td.RUN}', fs)
        td.UI_BACKEND_FILE.write_text('fastshell\n')
        saved = td.FASTSHELL
        try:
            td.FASTSHELL = str(self.run_dir / 'missing-binary')
            self.assertEqual(td.ui_backend(), 'quickshell', 'no binary -> classic shell')
            td.FASTSHELL = sys.executable
            self.assertEqual(td.ui_backend(), 'fastshell')
            td.FALLBACK_UNTIL[0] = time.time() + 60
            self.assertEqual(td.ui_backend(), 'quickshell', 'crash-loop fallback')
        finally:
            td.FASTSHELL = saved
            td.FALLBACK_UNTIL[0] = 0.0
