#!/usr/bin/env python3
"""Placeholder libseat session used to hand the S22 panel from Hyprland to a
trial compositor and back WITHOUT stopping Hyprland or its supervisor.

Runs inside the Arch chroot (LIBSEAT_BACKEND=seatd, same /run/seatd.sock as
the Hyprland session). seatd's seat is not VT-bound, so every client gets a
session number (Hyprland = 1, this holder = 2). When Hyprland is asked to
switch to session 2 (the XF86Switch_VT_2 keysym, e.g. `wtype -k
XF86Switch_VT_2`), seatd drops DRM master on Hyprland's card fd, revokes its
input fds and activates this holder. The holder opens no devices, so the
panel is free for a compositor started with LIBSEAT_BACKEND=noop.

Return path: when this process exits (stop file, SIGTERM, SIGHUP, or crash),
its seatd connection closes and seatd re-activates the first remaining
client: Hyprland. Stop the trial compositor BEFORE releasing the holder, so
Hyprland can take DRM master back.

usage: p2-seat-hold.py STATE_FILE STOP_FILE
STATE_FILE gets one word: waiting | active | inactive | closed.
Spawns nothing (no fork: safe under the close_range kernel bug).
"""
import ctypes
import os
import signal
import sys
import time

state_file, stop_file = sys.argv[1], sys.argv[2]
lib = ctypes.CDLL('libseat.so.1')
CB = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p)


class Listener(ctypes.Structure):
    _fields_ = [('enable_seat', CB), ('disable_seat', CB)]


lib.libseat_open_seat.restype = ctypes.c_void_p
lib.libseat_open_seat.argtypes = [ctypes.POINTER(Listener), ctypes.c_void_p]
for name in ('libseat_dispatch', 'libseat_disable_seat', 'libseat_close_seat', 'libseat_switch_session'):
    getattr(lib, name).restype = ctypes.c_int
lib.libseat_dispatch.argtypes = [ctypes.c_void_p, ctypes.c_int]
lib.libseat_disable_seat.argtypes = [ctypes.c_void_p]
lib.libseat_close_seat.argtypes = [ctypes.c_void_p]
lib.libseat_switch_session.argtypes = [ctypes.c_void_p, ctypes.c_int]


def put(word: str) -> None:
    tmp = state_file + '.tmp'
    with open(tmp, 'w') as f:
        f.write(f'{word} {time.time():.3f}\n')
    os.replace(tmp, state_file)


seat = None


def on_enable(_s, _u):
    put('active')


def on_disable(s, _u):
    put('inactive')
    lib.libseat_disable_seat(s)


listener = Listener(CB(on_enable), CB(on_disable))
stop = False


def handle(_sig, _frm):
    global stop
    stop = True


for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
    signal.signal(sig, handle)

put('waiting')
seat = lib.libseat_open_seat(ctypes.byref(listener), None)
if not seat:
    put('closed')
    sys.exit('libseat_open_seat failed')
while not stop and not os.path.exists(stop_file):
    if lib.libseat_dispatch(seat, 250) < 0:
        break
lib.libseat_close_seat(seat)
put('closed')
