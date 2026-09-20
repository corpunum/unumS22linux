#!/usr/bin/env python3
"""Save a bounded, read-only public-safe persistence status from the phone."""
import argparse
import datetime
import json
from pathlib import Path
import shlex
import subprocess

PROJECT=Path(__file__).resolve().parents[2]
REMOTE='''
import json,re,urllib.request
from pathlib import Path
processes=[]
for p in Path('/proc').iterdir():
    if not p.name.isdigit(): continue
    try:
        comm=(p/'comm').read_text().strip()
        cmd=(p/'cmdline').read_bytes().replace(b'\\0',b' ').decode()
        if comm in ('Hyprland','foot','squeekboard','quickshell','weston','seatd') or cmd.startswith('/usr/bin/python3 /usr/local/bin/start-persistent-desktop') or '/server/bin/llama-server ' in cmd:
            if cmd.startswith(('python3 -c','sh -c')): continue
            processes.append({'pid':int(p.name),'comm':comm,'cmdline':cmd})
    except (OSError,UnicodeError): pass
with open('/proc/boot_reset','rb') as f: bore=f.read(4096).replace(b'\\0',b'').decode()
last=re.search(r'^last: (\\d+)',bore,re.M)
last=last.group(1) if last else None
record=re.search(r'^\\[\\s*'+str(last)+r'\\].*$',bore,re.M)
mode=re.search(r'> (RECOVERY|NORMAL|DOWNLOAD) >',record.group(0)) if record else None
boot_mode=mode.group(1) if mode else 'UNCONFIRMED'
try:
    op=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    health=json.load(op.open('http://127.0.0.1:8089/health',timeout=2))
except Exception as e: health={'error':str(e)}
runtime=Path('/run/s22-persistent-ready.json')
print(json.dumps({
    'pid1':Path('/proc/1/comm').read_text().strip(),
    'uptime_seconds':float(Path('/proc/uptime').read_text().split()[0]),
    'bore_last':last,'boot_mode':boot_mode,
    'mounts':[r for r in Path('/proc/self/mountinfo').read_text().splitlines() if any(p in r for p in ('/srv/s22','/mnt/omarchy-trial','/mnt/model-bench'))],
    'processes':processes,'model_health':health,
    'runtime_marker':json.loads(runtime.read_text()) if runtime.exists() else None,
    'mem_available_kib':int(re.search(r'MemAvailable:\\s+(\\d+)',Path('/proc/meminfo').read_text()).group(1)),
    'battery_temperature_c':int(Path('/sys/class/power_supply/battery/temp').read_text())/10,
    'physical_touch_verified':False,
}))
'''


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('label')
    args=parser.parse_args()
    if not args.label.replace('-','').isalnum():
        raise SystemExit('Use an alphanumeric/hyphen label')
    result=json.loads(subprocess.check_output([str(PROJECT/'tools/s22-ssh'),'python3 -c '+shlex.quote(REMOTE)],timeout=30))
    result['captured_utc']=datetime.datetime.now(datetime.timezone.utc).isoformat()
    folder=PROJECT/'evidence/persistence-20260920'
    folder.mkdir(exist_ok=True)
    target=folder/(args.label+'.json')
    with target.open('x') as handle:
        json.dump(result,handle,indent=2)
        handle.write('\n')
    print(target)
    print(json.dumps({k:v for k,v in result.items() if k not in ('mounts','processes')},indent=2))


if __name__=='__main__': main()
