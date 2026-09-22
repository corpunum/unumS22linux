#!/usr/bin/env python3
"""Compile a private diagnostic fixture; does not stage or execute on phone."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'builds/bt-nvm-20260922'
EXPECTED='7f9564170b3cfae8293b432248406827215319d0b88f8a9417877d2539dc3c5d'

def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--precomputed',action='store_true');args=ap.parse_args()
    raw=(OUT/'hpnv21-normal-key-diagnostic.bab').read_bytes()
    assert len(raw)==7023 and hashlib.sha256(raw).hexdigest()==EXPECTED
    header='/* Private recovered NVM bytes. Never publish. Diagnostic only. */\n'
    header+='static const uint8_t s22_nvm_payload[7023] = {'+','.join(f'0x{x:02x}' for x in raw)+'};\n'
    p=OUT/'qca-nvm-private.h'
    if p.exists():assert p.read_text()==header
    else:
        with p.open('x') as f:f.write(header)
        p.chmod(0o600)
    stem='bt-qca6490-nvm-precomputed' if args.precomputed else 'bt-qca6490-nvm-capture'
    source=ROOT/'tools/hardware'/(stem+'.c')
    binary=OUT/stem
    subprocess.run(['aarch64-linux-gnu-gcc','-static','-std=c11','-Wall','-Wextra','-Werror',
                    '-O2',str(source),'-o',str(binary)],check=True)
    receipt={'nvm_sha256':EXPECTED,'generated_header_sha256':hashlib.sha256(p.read_bytes()).hexdigest(),
             'binary_sha256':hashlib.sha256(binary.read_bytes()).hexdigest()}
    (OUT/(stem+'-build-receipt.json')).write_text(json.dumps(receipt,indent=2)+'\n')
    print(json.dumps(receipt,indent=2))

if __name__=='__main__':main()
