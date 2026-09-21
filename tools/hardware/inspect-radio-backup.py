#!/usr/bin/env python3
"""Inspect the private radio backup using the pinned 32-byte cp_toc layout.

No modem, SIM or device access. CRC/signature validation and CP boot are not
implemented. Prints only bounded header metadata, hashes and build markers.
"""
import hashlib
import json
from pathlib import Path
import re
import struct

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT/'rootfs/hardware-reuse-20260921'
EXPECTED = '386155dc00d3034b22139b01e402515e71f4a0672df761d665ce74155c7b962e'


def main():
    data=(BASE/'radio-readonly.img').read_bytes()
    assert len(data)==83886080 and hashlib.sha256(data).hexdigest()==EXPECTED
    entries=[]
    # Bound the read to the first 1 KiB. Do not guess a header-count meaning
    # for reserved fields or read component payloads using this report.
    for offset in range(0,1024,32):
        name,img_offset,mem_offset,size,crc,reserved=struct.unpack_from('<12sIIIII',data,offset)
        if not any(name):
            break
        name=name.split(b'\0',1)[0]
        if not re.fullmatch(rb'[A-Z0-9_]{1,12}',name):
            break
        entries.append(dict(name=name.decode(),table_offset=offset,
                            image_offset=img_offset,bytes=size,
                            file_bounds_valid=img_offset+size<=len(data),
                            crc_checked=False))
    assert entries and entries[0]['name']=='TOC'
    report=dict(bytes=len(data),sha256=EXPECTED,table_entry_bytes=32,
                header_layout_source='drivers/soc/samsung/cpif/shm_ipc.c:struct cp_toc',
                entries=entries,
                build_markers=sorted({x.decode() for x in re.findall(rb'S901B[A-Z0-9]{5,}',data)}),
                board_marker_present=b'S5133AP_RAINBOWR0' in data,
                limitations=['Bounded header metadata only; no CRC or signature validation.',
                             'Build markers are embedded strings, not proof of successful modem boot.',
                             'No CP firmware loading, RIL, SIM read, data session, call or SMS.'])
    (BASE/'radio-header.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))


if __name__=='__main__':
    main()
