#!/usr/bin/env python3
"""Hash-check the exact extra firmware on the phone after the recovery boot."""
import hashlib
import argparse
import json
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[2]
CODE=r'''
import hashlib,json,pathlib,urllib.request,subprocess
p=pathlib.Path
def read(path):
 try:return p(path).read_text().strip()
 except OSError:return None
out={'boot_id':read('/proc/sys/kernel/random/boot_id'),'pid1':read('/proc/1/comm'),
     'uptime_seconds':float(read('/proc/uptime').split()[0]),'files':{}}
assert out['pid1']=='native-guardian'
for name,expected in EXPECTED.items():
 target=p('/proc/1/root/vendor/firmware')/name
 try:
  data=target.read_bytes();actual=hashlib.sha256(data).hexdigest()
  out['files'][name]={'bytes':len(data),'sha256':actual,'exact':actual==expected}
 except OSError as exc:out['files'][name]={'exact':False,'error':type(exc).__name__}
out['cards']=read('/proc/asound/cards')
out['pcm']=read('/proc/asound/pcm')
controls=subprocess.run(['amixer','-c','0','controls'],capture_output=True,text=True,timeout=20)
assert controls.returncode==0
out['controls']=controls.stdout.splitlines()
out['model']=json.load(urllib.request.build_opener(urllib.request.ProxyHandler({})).open('http://127.0.0.1:8089/health',timeout=5))
print(json.dumps(out))
'''

def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--name',default='audio-extras-firmware-postboot-v2');args=ap.parse_args()
    assert args.name.replace('-','').isalnum() and '/' not in args.name
    manifest=json.loads((ROOT/'builds/audio-extra-v2-20260922/manifest.json').read_text())
    assert manifest['image_sha256']=='758fc9d30491e17b7c829a89d338ba69476efa15a1280deb8a1b9b8009687f4b'
    expected={x['filename']:x['sha256'] for x in manifest['extras']}
    assert len(expected)==20
    source=CODE.replace('EXPECTED',repr(expected)).encode()
    out=ROOT/'rootfs/main-driver-loop-20260921'/(args.name+'.json')
    assert not out.exists()
    result=subprocess.run([str(ROOT/'tools/s22-ssh'),'python3 -'],input=source,capture_output=True,timeout=30)
    receipt={'returncode':result.returncode,'source_sha256':hashlib.sha256(source).hexdigest()}
    if result.returncode:raise RuntimeError(result.stderr.decode())
    receipt['result']=json.loads(result.stdout)
    with out.open('x') as stream:json.dump(receipt,stream,indent=2);stream.write('\n')
    files=receipt['result']['files']
    print(json.dumps({'exact_extra_files':sum(x['exact'] for x in files.values()),
                     'extra_files_total':len(files),'model':receipt['result']['model'],
                     'uptime_seconds':receipt['result']['uptime_seconds']}))
    assert all(x['exact'] for x in files.values())

if __name__=='__main__':main()
