#!/usr/bin/env python3
"""Build a private first-segment diagnostic; never stage or open the phone."""
import hashlib
import argparse
import json
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[2]
ASSET=ROOT/'rootfs/bt-audio-vendor-assets/vendor/firmware/hpbtfw21.tlv'
FULL_SHA='00d250cddddbf62ec69dabc105263c624aa076f078cf040a83ad580fc53dfdcd'
PREFIX_SHA='57aaa72f9f49b5b9e158ba39230328ad96f805f3a9306f340d9981b43fb04793'
OUT=ROOT/'builds/bt-segment-20260922'

def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--full',action='store_true')
    mode=ap.add_mutually_exclusive_group()
    mode.add_argument('--postpatch-board',action='store_true')
    mode.add_argument('--read-address',action='store_true');args=ap.parse_args()
    if (args.postpatch_board or args.read_address) and not args.full:ap.error('query mode requires --full')
    data=ASSET.read_bytes();prefix=data[:243]
    assert len(data)==195848 and data[:4]==bytes.fromhex('0104fd02')
    assert hashlib.sha256(data).hexdigest()==FULL_SHA
    assert hashlib.sha256(prefix).hexdigest()==PREFIX_SHA
    out=ROOT/'builds/bt-patch-20260922' if args.full else OUT
    out.mkdir(exist_ok=True)
    payload=data if args.full else prefix
    assert data[14]==3 and data[16:20]==bytes.fromhex('13000102')
    name='s22_patch_payload' if args.full else 's22_patch_prefix'
    header=('/* Private firmware bytes. Never publish this generated file. */\n' if args.full else
            '/* Private firmware prefix. Never publish this generated file. */\n')
    header+='static const uint8_t '+name+'['+str(len(payload))+'] = {'+','.join(f'0x{x:02x}' for x in payload)+'};\n'
    p=out/('qca-patch-private.h' if args.full else 'qca-patch-prefix-private.h')
    if p.exists():assert p.read_text()==header
    else:
        with p.open('x') as f:f.write(header)
        p.chmod(0o600)
    stem='bt-qca6490-patch-capture' if args.full else 'bt-qca6490-segment-capture'
    if args.postpatch_board:stem='bt-qca6490-postpatch-board'
    if args.read_address:stem='bt-qca6490-read-address'
    source=ROOT/'tools/hardware'/(stem+'.c')
    binary=out/stem
    subprocess.run(['aarch64-linux-gnu-gcc','-static','-std=c11','-Wall','-Wextra','-Werror',
                    '-O2',str(source),'-o',str(binary)],check=True)
    receipt={'asset_sha256':FULL_SHA,'prefix_sha256':PREFIX_SHA,
             'generated_header_sha256':hashlib.sha256(p.read_bytes()).hexdigest(),
             'binary_sha256':hashlib.sha256(binary.read_bytes()).hexdigest()}
    (out/(stem+'-build-receipt.json')).write_text(json.dumps(receipt,indent=2)+'\n')
    print(json.dumps(receipt,indent=2))

if __name__=='__main__':main()
