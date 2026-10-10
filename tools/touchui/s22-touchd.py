#!/usr/bin/env python3
"""s22-touchd: touch-UI helper daemon for the Galaxy S22 native-Linux phone.

Runs on the native (Alpine) root as root, next to s22-buttons and s22-keepalive.
It does three things:

1. UI supervisor: keeps the touch shell (Quickshell, /opt/s22-touch/shell.qml in
   the Arch chroot, Qt software rendering) running while a Hyprland session
   (or the opt-in sway-pixman desktop, /etc/s22-desktop) exists. Restarts it with backoff if it dies. Never touches Hyprland itself.
2. Gestures: reads the touchscreen evdev node WITHOUT grabbing it (Hyprland keeps
   getting every event) and maps single-finger edge swipes to UI actions:
       bottom edge, swipe up      -> home
       left/right edge, swipe in  -> back
       top edge, swipe down       -> app switcher
3. Control socket: one JSON request per connection on
   /mnt/omarchy-trial/run/s22-touch/ctl.sock (= /run/s22-touch/ctl.sock inside
   the chroot). The chroot side (s22-ui CLI, the QML shell, the OpenUnum s22-ui
   plugin) uses it for actions that need the native root (screen power, mute
   status, camera) and for UI calls. Only the commands in COMMANDS exist.

Muting: this daemon never plays or routes audio. "status" only READS the volume
level and amp gains (s22-keepalive enforces 0).
Power key: install hooks/power (s22-buttons runs it on a short press instead of
its default s22-display toggle): screen on -> lock cover + DPMS off; screen
off -> DPMS on (the lock cover is already up).
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import select
import signal
import socket
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path

CHROOT = Path(os.environ.get('S22_TOUCH_CHROOT', '/mnt/omarchy-trial'))
RUN_IN = '/run/s22-touch'                     # path inside the chroot
RUN = Path(str(CHROOT) + RUN_IN)              # same dir seen from the native root
SOCK = RUN / 'ctl.sock'
SHELL_DIR = '/opt/s22-touch'                  # inside the chroot
LOG = Path('/srv/s22/state/touchui/touchd.log')
DISPLAY = '/srv/s22/hardware/bin/s22-display'
DISPLAY_STATE = Path('/run/s22-display-state')
CAMERA = '/srv/s22/hardware/bin/s22-camera'   # reviewed client (sha256 3f1375f6...) since 2026-10-09
BUTTONS = Path('/srv/s22/buttons')
AUDIO_OPT = '/opt/s22-audio'
AMP_CONTROLS = ('Left Digital PCM Volume', 'Right Digital PCM Volume')
LOCK = Path('/run/s22-touchd.lock')
OPENUNUM_API = 'http://127.0.0.1:18880'
PIN_FILE = Path('/srv/s22/state/keepalive/openunum.pinned.json')   # s22-keepalive pin_source
MODEL_CHOICES = {'luna': ('openai', 'openai/gpt-6-luna'),
                 'local': ('llama-cpp-local', '/models/Qwen3.5-0.8B-Q4_0.gguf')}
DEFAULT_MODEL_CHOICE = 'luna'
LOG_MAX = 1 << 20

EVENT = struct.Struct('<qqHHi')
EV_SYN, EV_KEY, EV_ABS = 0, 1, 3
SYN_REPORT = 0
ABS_MT_SLOT, ABS_MT_POSITION_X, ABS_MT_POSITION_Y, ABS_MT_TRACKING_ID = 0x2f, 0x35, 0x36, 0x39
BTN_TOUCH = 0x14a


def log(**rec) -> None:
    rec = {'ts': round(time.time(), 3), **rec}
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        if LOG.exists() and LOG.stat().st_size > LOG_MAX:
            os.replace(LOG, LOG.with_suffix('.log.1'))
        with open(LOG, 'a') as f:
            f.write(json.dumps(rec, sort_keys=True) + '\n')
    except OSError:
        pass


# ------------------------------------------------------------------ gestures

class EdgeSwipe:
    """Single-finger edge-swipe recogniser on normalised coordinates (0..1).

    feed(x, y, t) while one finger is down, then end(t) -> gesture name or None.
    A second finger cancels the gesture (multi-touch belongs to the apps).
    """

    def __init__(self, edge: float = 0.035, top_edge: float = 0.03, min_travel: float = 0.12,
                 max_ms: int = 900, max_drift: float = 0.6):
        self.edge, self.top_edge, self.min_travel = edge, top_edge, min_travel
        self.max_ms, self.max_drift = max_ms, max_drift
        self.reset()

    def reset(self) -> None:
        self.start = None      # (x, y, t)
        self.last = None
        self.cancelled = False

    def begin(self, x: float, y: float, t: float) -> None:
        self.reset()
        self.start = self.last = (x, y, t)

    def feed(self, x: float, y: float, t: float) -> None:
        if self.start is None:
            self.begin(x, y, t)
        else:
            self.last = (x, y, t)

    def cancel(self) -> None:
        self.cancelled = True

    def end(self, t: float) -> str | None:
        start, last, cancelled = self.start, self.last, self.cancelled
        self.reset()
        if start is None or last is None or cancelled:
            return None
        x0, y0, t0 = start
        x1, y1, _ = last
        if (t - t0) * 1000 > self.max_ms:
            return None
        dx, dy = x1 - x0, y1 - y0
        if y0 >= 1 - self.edge and -dy >= self.min_travel and abs(dx) <= self.max_drift * -dy:
            return 'home'
        if y0 <= self.top_edge and dy >= self.min_travel and abs(dx) <= self.max_drift * dy:
            return 'switcher'
        if x0 <= self.edge and dx >= self.min_travel * 0.8 and abs(dy) <= self.max_drift * dx:
            return 'back'
        if x0 >= 1 - self.edge and -dx >= self.min_travel * 0.8 and abs(dy) <= self.max_drift * -dx:
            return 'back'
        return None


def _ioc(direction: int, typ: int, nr: int, size: int) -> int:
    return (direction << 30) | (size << 16) | (typ << 8) | nr


def eviocgabs(fd: int, axis: int) -> tuple[int, int]:
    buf = bytearray(24)
    fcntl.ioctl(fd, _ioc(2, ord('E'), 0x40 + axis, 24), buf, True)
    _value, lo, hi, *_ = struct.unpack('<6i', bytes(buf))
    return lo, hi


def find_touchscreen(sys_root: str = '/sys/class/input') -> str | None:
    for ev in sorted(Path(sys_root).glob('event*')):
        try:
            if (ev / 'device/name').read_text().strip() == 'sec_touchscreen':
                return '/dev/input/' + ev.name
        except OSError:
            continue
    return None


class TouchReader:
    """Protocol-B multitouch reader feeding an EdgeSwipe."""

    def __init__(self, recogniser: EdgeSwipe, on_gesture, xr=(0, 1079), yr=(0, 2339)):
        self.r = recogniser
        self.on_gesture = on_gesture
        self.xr, self.yr = xr, yr
        self.slot = 0
        self.slots: dict[int, list] = {}        # slot -> [tracking_id, x, y]
        self.primary: int | None = None
        self.dirty = False

    def norm(self, x: int, y: int) -> tuple[float, float]:
        (xl, xh), (yl, yh) = self.xr, self.yr
        return (x - xl) / max(1, xh - xl), (y - yl) / max(1, yh - yl)

    def event(self, typ: int, code: int, value: int, t: float) -> None:
        if typ == EV_ABS:
            if code == ABS_MT_SLOT:
                self.slot = value
            elif code == ABS_MT_TRACKING_ID:
                if value == -1:
                    self.slots.pop(self.slot, None)
                else:
                    prev = self.slots.get(self.slot)
                    self.slots[self.slot] = [value, prev[1] if prev else None, prev[2] if prev else None]
                    if self.primary is not None and self.slot != self.primary:
                        self.r.cancel()            # second finger
                self.dirty = True
            elif code in (ABS_MT_POSITION_X, ABS_MT_POSITION_Y):
                s = self.slots.setdefault(self.slot, [None, None, None])
                s[1 if code == ABS_MT_POSITION_X else 2] = value
                self.dirty = True
        elif typ == EV_SYN and code == SYN_REPORT and self.dirty:
            self.dirty = False
            self.sync(t)

    def sync(self, t: float) -> None:
        if self.primary is None:
            live = [k for k, v in self.slots.items() if v[1] is not None and v[2] is not None]
            if live:
                self.primary = live[0]
                x, y = self.norm(self.slots[self.primary][1], self.slots[self.primary][2])
                self.r.begin(x, y, t)
                if len(live) > 1:
                    self.r.cancel()
            return
        s = self.slots.get(self.primary)
        if s is None:                                  # primary finger lifted
            g = self.r.end(t)
            self.primary = None
            if not self.slots and g:
                self.on_gesture(g)
            elif self.slots:
                # other fingers still down: wait until all are up before a new gesture
                self.r.reset()
            return
        if s[1] is not None and s[2] is not None:
            x, y = self.norm(s[1], s[2])
            self.r.feed(x, y, t)


def gesture_loop(daemon: 'Daemon') -> None:
    while not daemon.stopping.is_set():
        node = find_touchscreen()
        if not node:
            log(event='gesture_no_device')
            daemon.stopping.wait(30)
            continue
        try:
            fd = os.open(node, os.O_RDONLY | os.O_NONBLOCK)
        except OSError as e:
            log(event='gesture_open_failed', node=node, error=str(e))
            daemon.stopping.wait(30)
            continue
        try:
            try:
                xr, yr = eviocgabs(fd, ABS_MT_POSITION_X), eviocgabs(fd, ABS_MT_POSITION_Y)
            except OSError:
                xr, yr = (0, 1079), (0, 2339)
            log(event='gesture_start', node=node, x=xr, y=yr)
            reader = TouchReader(EdgeSwipe(), daemon.gesture, xr, yr)
            buf = b''
            while not daemon.stopping.is_set():
                r, _, _ = select.select([fd], [], [], 1.0)
                if not r:
                    continue
                try:
                    chunk = os.read(fd, EVENT.size * 64)
                except BlockingIOError:
                    continue
                if not chunk:
                    break
                buf += chunk
                n = len(buf) // EVENT.size
                for i in range(n):
                    sec, usec, typ, code, value = EVENT.unpack_from(buf, i * EVENT.size)
                    reader.event(typ, code, value, sec + usec / 1e6)
                buf = buf[n * EVENT.size:]
        except OSError as e:
            log(event='gesture_read_failed', error=str(e))
            daemon.stopping.wait(5)
        finally:
            os.close(fd)


# ------------------------------------------------------------------ chroot helpers

def hypr_signature() -> str | None:
    d = CHROOT / 'run/user/0/hypr'
    try:
        sigs = sorted(d.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return None
    for p in sigs:
        if (p / '.socket.sock').exists():
            return p.name
    return None


SWAY_SESSION = CHROOT / 'run/s22-desktop/session'   # written by s22-sway-pixman.conf


def sway_session() -> tuple[str, str] | None:
    """(WAYLAND_DISPLAY, SWAYSOCK) of a running sway-pixman desktop, else None."""
    if not pids_by_comm('sway'):
        return None
    try:
        lines = SWAY_SESSION.read_text().split('\n')
    except OSError:
        return None
    display = lines[0].strip() if lines else ''
    swaysock = lines[1].strip() if len(lines) > 1 else ''
    if not display or '/' in display or not (CHROOT / 'run/user/0' / display).exists():
        return None
    return display, swaysock


def session_env() -> dict | None:
    """Environment of the running desktop session (Hyprland, else the opt-in
    sway-pixman profile), or None when there is none."""
    sig = hypr_signature()
    if sig and (CHROOT / 'run/user/0/wayland-1').exists():
        extra, comp = {'WAYLAND_DISPLAY': 'wayland-1', 'HYPRLAND_INSTANCE_SIGNATURE': sig}, 'Hyprland'
    else:
        sway = sway_session()
        if sway is None:
            return None
        extra, comp = {'WAYLAND_DISPLAY': sway[0]}, 'sway'
        if sway[1]:
            extra['SWAYSOCK'] = sway[1]
    env = {
        'HOME': '/root', 'PATH': '/usr/local/bin:/opt/omarchy-source/bin:/usr/bin:/bin',
        'XDG_RUNTIME_DIR': '/run/user/0', **extra,
        'LANG': 'C.UTF-8', 'TZ': os.environ.get('TZ', 'Europe/Athens'),
        'QT_QUICK_BACKEND': 'software', 'QSG_RHI_BACKEND': 'software', 'QT_QPA_PLATFORM': 'wayland',
        'QT_QPA_PLATFORMTHEME': 'generic', 'NO_AT_BRIDGE': '1', 'QT_ACCESSIBILITY': '0',
        'QS_DISABLE_FILE_WATCHER': '1', 'QS_NO_RELOAD_POPUP': '1',
        'QT_QUICK_CONTROLS_STYLE': 'Basic',
    }
    bus = session_bus(comp)
    if bus:
        env['DBUS_SESSION_BUS_ADDRESS'] = bus
    return env


def session_bus(comp: str = 'Hyprland') -> str | None:
    """DBus session address of the compositor's session (for squeekboard)."""
    for pid in pids_by_comm(comp):
        try:
            for item in Path(f'/proc/{pid}/environ').read_bytes().split(b'\0'):
                if item.startswith(b'DBUS_SESSION_BUS_ADDRESS='):
                    return item.split(b'=', 1)[1].decode()
        except OSError:
            continue
    return None


def pids_by_comm(comm: str) -> list[int]:
    out = []
    for p in Path('/proc').iterdir():
        if p.name.isdigit():
            try:
                if (p / 'comm').read_text().strip() == comm:
                    out.append(int(p.name))
            except OSError:
                pass
    return out


def chroot_run(argv: list[str], env: dict, timeout: float = 10) -> subprocess.CompletedProcess:
    cmd = ['chroot', str(CHROOT), '/usr/bin/env', '-i'] + [f'{k}={v}' for k, v in env.items()] + argv
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def ui_call(fn: str, *args: str, timeout: float = 8) -> dict:
    env = session_env()
    if env is None:
        return {'ok': False, 'error': 'no_session'}
    try:
        r = chroot_run(['quickshell', 'ipc', '-p', SHELL_DIR, 'call', 'touch', fn, *[str(a) for a in args]],
                       env, timeout)
    except (OSError, subprocess.SubprocessError) as e:
        return {'ok': False, 'error': f'{type(e).__name__}: {e}'}
    if r.returncode != 0:
        return {'ok': False, 'error': 'ipc_failed', 'stderr': r.stderr.strip()[-400:]}
    out = r.stdout.strip()
    try:
        return {'ok': True, 'result': json.loads(out)} if out[:1] in '{[' else {'ok': True, 'result': out}
    except ValueError:
        return {'ok': True, 'result': out}


# ------------------------------------------------------------------ host status

def amp_values() -> list[int | None] | None:
    if not Path('/sys/class/sound/card0').exists():
        return None
    vals: list[int | None] = []
    for ctl in AMP_CONTROLS:
        try:
            r = subprocess.run(['chroot', str(CHROOT), '/usr/bin/env', f'LD_LIBRARY_PATH={AUDIO_OPT}/usr/lib',
                                f'{AUDIO_OPT}/usr/bin/amixer', '-c0', 'cget', f'name={ctl}'],
                               capture_output=True, text=True, timeout=10)
            v = None
            for line in r.stdout.splitlines():
                line = line.strip()
                if line.startswith(': values='):
                    v = int(line.split('=', 1)[1].split(',')[0])
            vals.append(v)
        except (OSError, subprocess.SubprocessError, ValueError):
            vals.append(None)
    return vals


def openunum(method: str, path: str, body: dict | None = None, timeout: float = 15) -> dict:
    import urllib.request
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(OPENUNUM_API + path, data=data, method=method,
                                 headers={'content-type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode() or '{}')
    except Exception as e:  # noqa: BLE001 - reported to the caller
        return {'ok': False, 'error': f'{type(e).__name__}: {e}'}


def display_state() -> str:
    try:
        return DISPLAY_STATE.read_text().strip() or 'on'
    except OSError:
        return 'on'


# ------------------------------------------------------------------ daemon

class Daemon:
    def __init__(self, gestures: bool = True, ui: bool = True):
        self.gestures_on, self.ui_on = gestures, ui
        self.stopping = threading.Event()
        self.ui_proc: subprocess.Popen | None = None
        self.ui_starts: list[float] = []
        self.status_cache: tuple[float, dict] | None = None
        self.camera_busy = threading.Lock()
        self.last_gesture = 0.0

    # -- gestures
    def gesture(self, name: str) -> None:
        now = time.monotonic()
        if now - self.last_gesture < 0.4:
            return
        self.last_gesture = now
        if display_state() == 'off':
            return
        r = ui_call(name)
        log(event='gesture', name=name, ok=r.get('ok'), error=r.get('error'))

    # -- UI supervisor
    def ui_loop(self) -> None:
        backoff = 2.0
        for pid in stale_shell_pids():     # left behind by a SIGKILLed predecessor
            log(event='ui_stale_kill', pid=pid)
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass
        while not self.stopping.is_set():
            if self.ui_proc is None or self.ui_proc.poll() is not None:
                if self.ui_proc is not None:
                    log(event='ui_exit', code=self.ui_proc.returncode)
                    self.ui_proc = None
                env = session_env()
                if env is None:
                    self.stopping.wait(5)
                    continue
                now = time.time()
                self.ui_starts = [t for t in self.ui_starts if now - t < 600]
                if len(self.ui_starts) >= 6:
                    log(event='ui_giving_up_for_now', starts=len(self.ui_starts))
                    self.stopping.wait(300)
                    continue
                self.ui_starts.append(now)
                logf = open(RUN / 'shell.log', 'ab')
                cmd = ['chroot', str(CHROOT), '/usr/bin/env', '-i'] + [f'{k}={v}' for k, v in env.items()] + \
                      ['nice', '-n', '5', 'quickshell', '-n', '-p', f'{SHELL_DIR}/shell.qml']
                self.ui_proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=logf, stderr=logf,
                                                start_new_session=True)
                logf.close()
                log(event='ui_start', pid=self.ui_proc.pid)
                self.stopping.wait(backoff)
                backoff = min(backoff * 2, 60) if self.ui_proc.poll() is not None else 2.0
            else:
                self.stopping.wait(2)

    def stop_ui(self) -> None:
        p = self.ui_proc
        if p and p.poll() is None:
            # quickshell runs under chroot+env; kill the exact chain of PIDs we own
            for pid in [p.pid] + child_pids(p.pid):
                try:
                    os.kill(pid, signal.SIGTERM)
                except OSError:
                    pass
            try:
                p.wait(5)
            except subprocess.TimeoutExpired:
                for pid in [p.pid] + child_pids(p.pid):
                    try:
                        os.kill(pid, signal.SIGKILL)
                    except OSError:
                        pass

    # -- commands
    def cmd_status(self, req: dict) -> dict:
        now = time.monotonic()
        if self.status_cache and now - self.status_cache[0] < 15 and not req.get('fresh'):
            return self.status_cache[1]
        try:
            level = int((BUTTONS / 'volume').read_text().strip())
        except (OSError, ValueError):
            level = None
        amps = amp_values()
        st = {'ok': True, 'volume_level': level, 'amps': amps,
              'muted': level in (0, None) and all(v in (0, None) for v in (amps or [])),
              'display': display_state(),
              'keepalive': Path('/srv/s22/state/keepalive/enabled').exists(),
              'ui_pid': self.ui_proc.pid if self.ui_proc and self.ui_proc.poll() is None else None}
        self.status_cache = (now, st)
        return st

    def cmd_display(self, req: dict) -> dict:
        arg = req.get('arg', 'toggle')
        if arg not in ('on', 'off', 'toggle', 'status'):
            return {'ok': False, 'error': 'bad_arg'}
        if arg in ('off',) or (arg == 'toggle' and display_state() == 'on'):
            ui_call('lock')
        r = subprocess.run([DISPLAY, arg], capture_output=True, text=True, timeout=20)
        return {'ok': r.returncode == 0, 'display': display_state(), 'out': r.stdout.strip()[-200:]}

    def cmd_power(self, req: dict) -> dict:
        return self.cmd_display({'arg': 'toggle'})

    def cmd_ui(self, req: dict) -> dict:
        args = req.get('args') or []
        if not args or not all(isinstance(a, (str, int, float)) for a in args):
            return {'ok': False, 'error': 'bad_args'}
        fn = str(args[0])
        if fn not in UI_FUNCS:
            return {'ok': False, 'error': 'unknown_ui_function', 'allowed': sorted(UI_FUNCS)}
        if display_state() == 'off' and fn in ('home', 'open', 'switcher', 'card', 'confirm'):
            # wake for anything the user must see; the lock cover stays on top
            subprocess.run([DISPLAY, 'on'], capture_output=True, timeout=20)
        return ui_call(fn, *args[1:], timeout=float(req.get('timeout', 8)))

    def cmd_camera(self, req: dict) -> dict:
        if not os.path.exists(CAMERA):
            return {'ok': False, 'error': 'no_camera_tool'}
        if not self.camera_busy.acquire(blocking=False):
            return {'ok': False, 'error': 'busy'}

        def run():
            out = RUN / 'camera-latest.png'
            res = RUN / 'camera.json'
            try:
                res.write_text(json.dumps({'state': 'running', 'started': time.time()}))
                r = subprocess.run(['/usr/bin/python3', CAMERA, 'capture', '--out', str(out), '--allow-fw-stall',
                                    '--exposure-us', '20000', '--iso', '400'],
                                   capture_output=True, text=True, timeout=180)
                res.write_text(json.dumps({'state': 'done', 'rc': r.returncode, 'finished': time.time(),
                                           'path': f'{RUN_IN}/camera-latest.png' if out.exists() else None,
                                           'tail': (r.stdout + r.stderr)[-600:]}))
                log(event='camera', rc=r.returncode)
            except (OSError, subprocess.SubprocessError) as e:
                res.write_text(json.dumps({'state': 'failed', 'error': str(e)}))
            finally:
                self.camera_busy.release()
        threading.Thread(target=run, daemon=True).start()
        return {'ok': True, 'state': 'running', 'result_file': f'{RUN_IN}/camera.json'}

    def cmd_model(self, req: dict) -> dict:
        """Switch OpenUnum's default model AND tell s22-keepalive to pin the new choice
        (keepalive re-reads its pin file every pass, so it never fights the switch)."""
        choice = req.get('arg')
        if choice == 'current':
            return {'ok': True, 'current': openunum('GET', '/api/model/current'), 'choices': MODEL_CHOICES}
        if choice not in MODEL_CHOICES:
            return {'ok': False, 'error': 'bad_choice', 'choices': sorted(MODEL_CHOICES)}
        provider, model = MODEL_CHOICES[choice]
        out = openunum('POST', '/api/model/switch', {'provider': provider, 'model': model})
        cur = openunum('GET', '/api/model/current')
        if cur.get('provider') != provider:
            return {'ok': False, 'error': 'switch_failed', 'answer': out, 'current': cur}
        if choice == DEFAULT_MODEL_CHOICE:
            PIN_FILE.unlink(missing_ok=True)          # back to keepalive's configured pin
        else:
            tmp = PIN_FILE.with_suffix('.tmp')
            tmp.write_text(json.dumps({'model': {'provider': cur['provider'], 'model': cur['model']},
                                       'set_by': 's22-touchd', 'ts': time.time()}))
            os.replace(tmp, PIN_FILE)
        log(event='model', choice=choice, current=cur)
        return {'ok': True, 'current': cur, 'pinned': PIN_FILE.exists()}

    def cmd_gesture(self, req: dict) -> dict:
        name = req.get('name')
        if name not in ('home', 'back', 'switcher'):
            return {'ok': False, 'error': 'bad_gesture'}
        self.last_gesture = 0
        self.gesture(name)
        return {'ok': True}

    def handle(self, req: dict) -> dict:
        fn = COMMANDS.get(str(req.get('cmd')))
        if fn is None:
            return {'ok': False, 'error': 'unknown_command', 'allowed': sorted(COMMANDS)}
        try:
            return fn(self, req)
        except (OSError, subprocess.SubprocessError, ValueError) as e:
            return {'ok': False, 'error': f'{type(e).__name__}: {e}'}

    def serve(self) -> None:
        RUN.mkdir(parents=True, exist_ok=True)
        os.chmod(RUN, 0o755)
        try:
            SOCK.unlink()
        except FileNotFoundError:
            pass
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(str(SOCK))
        os.chmod(SOCK, 0o600)
        srv.listen(8)
        srv.settimeout(1.0)
        while not self.stopping.is_set():
            try:
                conn, _ = srv.accept()
            except socket.timeout:
                continue
            threading.Thread(target=self._conn, args=(conn,), daemon=True).start()
        srv.close()

    def _conn(self, conn: socket.socket) -> None:
        with conn:
            conn.settimeout(10)
            data = b''
            try:
                while not data.endswith(b'\n') and len(data) < 65536:
                    chunk = conn.recv(4096)
                    if not chunk:
                        break
                    data += chunk
                req = json.loads(data.decode() or '{}')
                resp = self.handle(req if isinstance(req, dict) else {})
            except (ValueError, OSError) as e:
                resp = {'ok': False, 'error': f'bad_request: {e}'}
            try:
                conn.sendall((json.dumps(resp) + '\n').encode())
            except OSError:
                pass

    def run(self) -> None:
        log(event='start', pid=os.getpid(), gestures=self.gestures_on, ui=self.ui_on)
        RUN.mkdir(parents=True, exist_ok=True)
        if self.ui_on:
            threading.Thread(target=self.ui_loop, daemon=True).start()
        if self.gestures_on:
            threading.Thread(target=gesture_loop, args=(self,), daemon=True).start()
        self.serve()


def stale_shell_pids() -> list[int]:
    """Exact-match PIDs of a touch-shell Quickshell (never pattern-kill anything else)."""
    want = ['quickshell', '-n', '-p', f'{SHELL_DIR}/shell.qml']
    out = []
    for pid in pids_by_comm('quickshell'):
        try:
            argv = Path(f'/proc/{pid}/cmdline').read_bytes().rstrip(b'\0').split(b'\0')
        except OSError:
            continue
        if [a.decode(errors='replace') for a in argv] == want:
            out.append(pid)
    return out


def child_pids(pid: int) -> list[int]:
    out, todo = [], [pid]
    while todo:
        p = todo.pop()
        try:
            for t in Path(f'/proc/{p}/task').iterdir():
                kids = (t / 'children').read_text().split()
                for k in kids:
                    out.append(int(k))
                    todo.append(int(k))
        except OSError:
            pass
    return out


COMMANDS = {
    'status': Daemon.cmd_status,
    'display': Daemon.cmd_display,
    'power': Daemon.cmd_power,
    'ui': Daemon.cmd_ui,
    'camera_capture': Daemon.cmd_camera,
    'gesture': Daemon.cmd_gesture,
    'model': Daemon.cmd_model,
}
UI_FUNCS = {'home', 'hide', 'back', 'switcher', 'open', 'card', 'confirm', 'confirmResult',
            'lock', 'unlock', 'keyboard', 'state', 'perf'}


def request(req: dict, timeout: float = 30) -> dict:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    s.connect(str(SOCK))
    with s:
        s.sendall((json.dumps(req) + '\n').encode())
        data = b''
        while not data.endswith(b'\n'):
            chunk = s.recv(65536)
            if not chunk:
                break
            data += chunk
    return json.loads(data.decode())


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--no-gestures', action='store_true')
    ap.add_argument('--no-ui', action='store_true', help='do not supervise the Quickshell touch shell')
    ap.add_argument('ctl', nargs='*', help='client mode: power | display on|off|toggle | status | gesture NAME | ui FN ARGS...')
    a = ap.parse_args(argv)
    if a.ctl:
        cmd, rest = a.ctl[0], a.ctl[1:]
        req: dict = {'cmd': cmd}
        if cmd == 'display':
            req['arg'] = rest[0] if rest else 'toggle'
        elif cmd == 'gesture':
            req['name'] = rest[0] if rest else ''
        elif cmd == 'ui':
            req['args'] = rest
        elif cmd == 'model':
            req['arg'] = rest[0] if rest else 'current'
        elif cmd == 'status':
            req['fresh'] = True
        print(json.dumps(request(req)))
        return 0
    lock = open(LOCK, 'w')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print('s22-touchd already running', file=sys.stderr)
        return 0
    d = Daemon(gestures=not a.no_gestures, ui=not a.no_ui)

    def stop(*_):
        d.stopping.set()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        d.run()
    finally:
        d.stop_ui()
        log(event='stop')
    return 0


if __name__ == '__main__':
    sys.exit(main())
