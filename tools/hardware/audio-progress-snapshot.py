#!/usr/bin/env python3
"""Narrow read-only RDMA2 status sampling; default only plans the reads.

regmap range/access are metadata. Read exactly one fixed-format record for
each status register using pread; never filter a full registers dump.
"""
import argparse
import json
import os
from pathlib import Path
import re
import time

MAP=Path('/sys/kernel/debug/regmap/18c50000.abox')
ABOX=Path('/sys/bus/platform/devices/18c50000.abox')
TARGETS=(0x1230,0x1238)

def plans(ranges_text, access_text):
    ranges=[];previous=-1
    for line in ranges_text.splitlines():
        m=re.fullmatch(r'([0-9a-f]+)-([0-9a-f]+)',line)
        if not m:raise ValueError('invalid range metadata')
        a,b=[int(x,16) for x in m.groups()]
        if a%4 or b%4 or b<a or a<=previous:raise ValueError('unordered or unaligned ranges')
        ranges.append((a,b));previous=b
    flags={};widths=set()
    for line in access_text.splitlines():
        m=re.fullmatch(r'([0-9a-f]+): ([yn]) ([yn]) ([yn]) ([yn])',line)
        if not m:raise ValueError('invalid access metadata')
        address=int(m[1],16)
        if address in flags:raise ValueError('duplicate register')
        flags[address]=m.groups()[1:];widths.add(len(m[1]))
    if not ranges or len(widths)!=1:raise ValueError('missing/inconsistent register layout')
    width=widths.pop()
    if width not in (4,5,6):raise ValueError('unexpected address width')
    size=width+8+3 # pinned config: val_bits32, stride4
    result=[]
    for target in TARGETS:
        if flags.get(target)!=('y','n','y','n'):raise ValueError('target not readable/read-only/volatile/nonprecious')
        index=0
        for a,b in ranges:
            if a<=target<=b:
                index+=(target-a)//4;break
            index+=(b-a)//4+1
        else:raise ValueError('target absent from printable map')
        result.append({'register':target,'offset':index*size,'length':size,'width':width})
    return result

def bounded(path,limit):
    with open(path,'r') as f:data=f.read(limit+1)
    if len(data)>limit:raise ValueError('oversized '+str(path))
    return data

def snapshot(read_status=False):
    if bounded('/proc/1/comm',64).strip()!='native-guardian':raise ValueError('wrong native session')
    plan=plans(bounded(MAP/'range',65536),bounded(MAP/'access',262144))
    result={'monotonic':time.monotonic(),'plan':plan,
            'runtime_status':bounded(ABOX/'power/runtime_status',64).strip(),
            'cache_only':bounded(MAP/'cache_only',64).strip(),
            'service':bounded(ABOX/'service',64).strip(),
            'reset_count':bounded(ABOX/'reset_count',64).strip(),
            'alsa_status':bounded('/proc/asound/card0/pcm2p/sub0/status',4096),
            'registers':{}}
    if not read_status or result['runtime_status']!='active' or result['cache_only']!='N' or result['service']!='1':
        result['status_read_skipped']=True;return result
    fd=os.open(MAP/'registers',os.O_RDONLY|os.O_CLOEXEC|os.O_NOFOLLOW)
    try:
        for p in plan:
            raw=os.pread(fd,p['length'],p['offset']).decode('ascii')
            m=re.fullmatch(r'([0-9a-f]+): ([0-9a-f]{8})\n',raw)
            if len(raw)!=p['length'] or not m or int(m[1],16)!=p['register']:
                raise ValueError('register record mismatch; stop rather than scanning')
            result['registers'][f'{p["register"]:04x}']=int(m[2],16)
    finally:os.close(fd)
    return result

if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--read-status',action='store_true')
    args=ap.parse_args();print(json.dumps(snapshot(args.read_status),sort_keys=True))
