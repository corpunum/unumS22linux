#!/usr/bin/env python3
"""Read the real ALSA control count through Arch's installed libasound.

Use the tested close-range compatibility wrapper when launching in Arch.
SND_CTL_READONLY=0x0004 is verified against its installed alsa/control.h.
This opens only hw:0's control device; no PCM or mixer write is performed.
"""
import ctypes as c
import json

lib=c.CDLL('libasound.so.2')
pointer=c.c_void_p
signatures={
    'snd_ctl_open':([c.POINTER(pointer),c.c_char_p,c.c_int],c.c_int),
    'snd_ctl_close':([pointer],c.c_int),
    'snd_ctl_elem_list_malloc':([c.POINTER(pointer)],c.c_int),
    'snd_ctl_elem_list_free':([pointer],None),
    'snd_ctl_elem_list':([pointer,pointer],c.c_int),
    'snd_ctl_elem_list_get_count':([pointer],c.c_uint),
}
for name,(args,result) in signatures.items():
    function=getattr(lib,name); function.argtypes=args; function.restype=result
control=pointer(); listing=pointer()
def check(result):
    if result<0:raise RuntimeError('ALSA returned '+str(result))
    return result
try:
    check(lib.snd_ctl_open(c.byref(control),b'hw:0',0x0004))
    check(lib.snd_ctl_elem_list_malloc(c.byref(listing)))
    check(lib.snd_ctl_elem_list(control,listing))
    count=lib.snd_ctl_elem_list_get_count(listing)
    if count!=1736:raise RuntimeError('control count differs: '+str(count))
    print(json.dumps({'library':'libasound.so.2','card':'hw:0','control_count':count,
                      'open_mode':'SND_CTL_READONLY','pcm_opened':False,'mixer_written':False}))
finally:
    if listing:lib.snd_ctl_elem_list_free(listing)
    if control:check(lib.snd_ctl_close(control))
