#!/bin/sh
# Use a new override-redirect container, rather than moving managed windows.
set -eu
export DISPLAY=${DISPLAY:-:0}
exec python3 - <<'PY'
import ctypes as C
import os
import select
import signal
import subprocess
import time

class Attributes(C.Structure):
    _fields_ = [
        ('background_pixmap', C.c_ulong), ('background_pixel', C.c_ulong),
        ('border_pixmap', C.c_ulong), ('border_pixel', C.c_ulong),
        ('bit_gravity', C.c_int), ('win_gravity', C.c_int),
        ('backing_store', C.c_int), ('backing_planes', C.c_ulong),
        ('backing_pixel', C.c_ulong), ('save_under', C.c_int),
        ('event_mask', C.c_long), ('do_not_propagate_mask', C.c_long),
        ('override_redirect', C.c_int), ('colormap', C.c_ulong),
        ('cursor', C.c_ulong),
    ]

lib = C.CDLL('libX11.so.6')
D, W = C.c_void_p, C.c_ulong
def api(name, result, *args):
    fn = getattr(lib, name)
    fn.restype, fn.argtypes = result, args
    return fn
open_display = api('XOpenDisplay', D, C.c_char_p)
close_display = api('XCloseDisplay', C.c_int, D)
screen_of = api('XDefaultScreen', C.c_int, D)
root_of = api('XRootWindow', W, D, C.c_int)
width_of = api('XDisplayWidth', C.c_int, D, C.c_int)
height_of = api('XDisplayHeight', C.c_int, D, C.c_int)
create = api('XCreateSimpleWindow', W, D, W, C.c_int, C.c_int,
             C.c_uint, C.c_uint, C.c_uint, C.c_ulong, C.c_ulong)
change = api('XChangeWindowAttributes', C.c_int, D, W, C.c_ulong, C.POINTER(Attributes))
name = api('XStoreName', C.c_int, D, W, C.c_char_p)
reparent = api('XReparentWindow', C.c_int, D, W, W, C.c_int, C.c_int)
move_resize = api('XMoveResizeWindow', C.c_int, D, W, C.c_int, C.c_int, C.c_uint, C.c_uint)
map_window = api('XMapWindow', C.c_int, D, W)
map_raised = api('XMapRaised', C.c_int, D, W)
focus = api('XSetInputFocus', C.c_int, D, W, C.c_int, C.c_ulong)
sync = api('XSync', C.c_int, D, C.c_int)
query = api('XQueryTree', C.c_int, D, W, C.POINTER(W), C.POINTER(W),
            C.POINTER(C.POINTER(W)), C.POINTER(C.c_uint))
free = api('XFree', C.c_int, C.c_void_p)

display = open_display(os.environ['DISPLAY'].encode())
if not display:
    raise SystemExit('start-s22-x11: cannot open Xwayland display')
processes = []
running = True
def stop(*_):
    global running
    running = False
signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)

def children(window):
    root, parent, count = W(), W(), C.c_uint()
    raw = C.POINTER(W)()
    if not query(display, window, C.byref(root), C.byref(parent), C.byref(raw), C.byref(count)):
        return []
    result = [raw[i] for i in range(count.value)]
    if raw:
        free(raw)
    return result

try:
    screen = screen_of(display)
    root = root_of(display, screen)
    width, height = width_of(display, screen), height_of(display, screen)
    if not (320 <= width <= 4096 and 600 <= height <= 8192):
        raise RuntimeError(f'unexpected X11 screen {width}x{height}')
    panel = 32
    keyboard_height = min(360, max(280, height // 3))
    terminal_height = height - panel - keyboard_height
    canvas = create(display, root, 0, 0, width, height, 0, 0, 0x101418)
    attrs = Attributes(override_redirect=1)
    change(display, canvas, 1 << 9, C.byref(attrs))
    name(display, canvas, b'S22 native console')
    slot = create(display, canvas, 0, panel, width, terminal_height, 0, 0, 0x101418)
    map_window(display, slot)
    map_raised(display, canvas)
    sync(display, 0)

    keyboard = subprocess.Popen(['matchbox-keyboard', '-xid'], stdout=subprocess.PIPE, text=True)
    processes.append(keyboard)
    if not select.select([keyboard.stdout], [], [], 10)[0]:
        raise RuntimeError('keyboard did not report its embedding window')
    keyboard_id = int(keyboard.stdout.readline().strip())
    reparent(display, keyboard_id, canvas, 0, panel + terminal_height)
    move_resize(display, keyboard_id, 0, panel + terminal_height, width, keyboard_height)
    map_window(display, keyboard_id)
    sync(display, 0)

    terminal = subprocess.Popen([
        'xterm', '-into', str(slot), '-fa', 'monospace', '-fs', '12',
        '-bg', '#101418', '-fg', '#d7e0e7', '-cr', '#70d2c4',
        '-geometry', '50x32+0+0', '-title', 'S22 Linux',
        '-e', '/usr/local/bin/s22-shell',
    ])
    processes.append(terminal)
    deadline = time.monotonic() + 10
    terminal_windows = []
    while time.monotonic() < deadline and terminal.poll() is None:
        terminal_windows = children(slot)
        if terminal_windows:
            break
        time.sleep(.1)
    if not terminal_windows:
        raise RuntimeError('xterm did not create its embedded window')
    terminal_id = terminal_windows[0]
    move_resize(display, terminal_id, 0, 0, width, terminal_height)
    focus(display, terminal_id, 2, 0)
    sync(display, 0)
    print(f'start-s22-x11: canvas={canvas} terminal={terminal_id} keyboard={keyboard_id} '
          f'layout={width}x{height} keyboard_height={keyboard_height}', flush=True)
    while running and terminal.poll() is None and keyboard.poll() is None:
        time.sleep(.25)
finally:
    for process in processes:
        if process.poll() is None:
            process.terminate()
    for process in processes:
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
    close_display(display)
PY
