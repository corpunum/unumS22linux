#!/usr/bin/env python3
"""Read-only libdrm identity probe; never acquires master or sets a mode."""
import ctypes as C
import os


class Version(C.Structure):
    _fields_ = [("major", C.c_int), ("minor", C.c_int), ("patch", C.c_int),
                ("name_len", C.c_int), ("name", C.c_char_p),
                ("date_len", C.c_int), ("date", C.c_char_p),
                ("desc_len", C.c_int), ("desc", C.c_char_p)]


lib = C.CDLL("libdrm.so.2", use_errno=True)
lib.drmGetVersion.argtypes = [C.c_int]
lib.drmGetVersion.restype = C.POINTER(Version)
lib.drmFreeVersion.argtypes = [C.POINTER(Version)]
lib.drmGetBusid.argtypes = [C.c_int]
lib.drmGetBusid.restype = C.c_void_p
lib.drmFreeBusid.argtypes = [C.c_void_p]
for path in ("/dev/dri/card0", "/dev/dri/card1"):
    fd = os.open(path, os.O_RDWR | os.O_CLOEXEC)
    try:
        version = lib.drmGetVersion(fd)
        busid = lib.drmGetBusid(fd)
        if version:
            v = version.contents
            print(path, "driver=", v.name, "version=", (v.major, v.minor, v.patch),
                  "description=", v.desc,
                  "busid=", C.string_at(busid) if busid else None)
            lib.drmFreeVersion(version)
        if busid:
            lib.drmFreeBusid(busid)
    finally:
        os.close(fd)
