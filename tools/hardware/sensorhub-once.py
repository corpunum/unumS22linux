#!/usr/bin/env python3
"""One source-matched r0s CHUB startup; no partition writes or reboot.

Run on the host. The only phone writes are a new exact firmware file in
userdata and the guardian's RAM firmware directory, and one fs_ready write.
No calibration, reset, self-test, sensor enable, or Android service is used.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shlex
import subprocess
import time

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / 'rootfs/hardware-reuse-20260921'
FIRMWARE = BASE / 'sensorhub/shub_pamir_rainbow.bin'
SHA256 = 'eb8396f5b3cc904638e68c5b72c20768e3b8852bc0adcbc663e51942bea86679'
SIZE = 929560
DEST = '/srv/s22/hardware-reuse-20260921/sensorhub/shub_pamir_rainbow.bin'
ACTIVE = '/proc/1/root/vendor/firmware/sensorhub/shub_pamir_rainbow.bin'


def run(cmd, **kwargs):
    return subprocess.run([str(ROOT / 'tools/s22-ssh'), cmd], capture_output=True,
                          timeout=25, **kwargs)


def python(script, data=None):
    result = run('python3 -c ' + shlex.quote(script), input=data)
    if result.returncode:
        raise RuntimeError(result.stderr.decode(errors='replace'))
    return result.stdout.decode()


STATUS = '''import json,pathlib
p=pathlib.Path
root=p('/sys/class/sensors/ssp_sensor')
keys=['fs_ready','fw_name','mcu_rev','mcu_name','enable','sensor_state']
result={k:(root/k).read_text().strip() for k in keys}
result['accel_raw']=p('/sys/class/sensors/accelerometer_sensor/raw_data').read_text().strip()
result['light_raw']=p('/sys/class/sensors/light_sensor/raw_data').read_text().strip()
result['dt_os_name']=p('/sys/firmware/devicetree/base/contexthub/os-name').read_bytes().rstrip(b'\\0').decode()
result['firmware_search_path']=p('/sys/module/firmware_class/parameters/path').read_text().strip()
print(json.dumps(result))
'''


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--start', action='store_true', help='stage exact firmware and write fs_ready once')
    args = ap.parse_args()
    spec = importlib.util.spec_from_file_location('gpu_trial', ROOT / 'tools/gpu-compat/run-trial.py')
    trial = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(trial)
    before = trial.phone_health()
    status_before = json.loads(python(STATUS))
    if not args.start:
        print(json.dumps(status_before, indent=2))
        return
    if status_before['dt_os_name'] != 'sensorhub/shub_pamir_rainbow.bin':
        raise RuntimeError('Different DT firmware target')
    if status_before['fs_ready'] != '0' or status_before['enable'] != '0' or status_before['fw_name']:
        raise RuntimeError('Hub is no longer in the untouched baseline; do not repeat startup')
    payload = FIRMWARE.read_bytes()
    if len(payload) != SIZE or hashlib.sha256(payload).hexdigest() != SHA256:
        raise RuntimeError('Firmware artifact mismatch')
    raw = BASE / 'sensor-start-once'
    raw.mkdir(mode=0o700, exist_ok=False)
    (raw / 'before.json').write_text(json.dumps(dict(health=before, sensors=status_before), indent=2)+'\n')
    kernel_before = run('dmesg', text=True)
    if kernel_before.returncode:
        raise RuntimeError('No kernel capture; do not stage/start')
    (raw / 'before-kernel.txt').write_text(kernel_before.stdout)
    boundary = max(map(float, re.findall(r'^\[\s*([0-9.]+)\]', kernel_before.stdout, re.M)), default=0)
    stage = '''import hashlib,json,os,pathlib,sys
p=pathlib.Path
targets=[p(DEST),p(ACTIVE)]
data=sys.stdin.buffer.read(SIZE+1)
assert len(data)==SIZE and hashlib.sha256(data).hexdigest()==SHA256
assert p('/proc/1/comm').read_text().strip()=='native-guardian'
assert p('/sys/module/firmware_class/parameters/path').read_text().strip()=='/vendor/firmware'
assert not any(t.exists() or t.is_symlink() for t in targets), 'Existing target; do not overwrite'
for t in targets:
 t.parent.mkdir(parents=True,exist_ok=True)
 with t.open('xb') as f: f.write(data); f.flush(); os.fsync(f.fileno())
 os.chmod(t,0o644)
 assert hashlib.sha256(t.read_bytes()).hexdigest()==SHA256
print(json.dumps(dict(firmware_sha256=SHA256,size=SIZE,staged=True,partition_writes=False)))
'''
    for key, value in [('DEST', DEST), ('ACTIVE', ACTIVE), ('SIZE', SIZE), ('SHA256', SHA256)]:
        stage = stage.replace(key, repr(value))
    (raw / 'stage.json').write_text(python(stage, payload))
    start = '''import pathlib
p=pathlib.Path('/sys/class/sensors/ssp_sensor/fs_ready')
assert p.read_text().strip()=='0'
with p.open('w') as f: f.write('1\\n')
print('fs_ready_written_once')
'''
    began = datetime.now(timezone.utc).isoformat()
    result = run('timeout -k 3 15 python3 -c ' + shlex.quote(start), text=True)
    (raw / 'startup-stdout.txt').write_text(result.stdout)
    (raw / 'startup-stderr.txt').write_text(result.stderr)
    # Let the asynchronous stock refresh task complete, never poll/reset it.
    time.sleep(5)
    status_after = json.loads(python(STATUS))
    after = trial.phone_health()
    kernel_after = run('dmesg', text=True)
    (raw / 'after-kernel.txt').write_text(kernel_after.stdout)
    delta = [line for line in kernel_after.stdout.splitlines()
             if (m := re.match(r'^\[\s*([0-9.]+)\]', line)) and float(m[1]) > boundary]
    (raw / 'kernel-delta.txt').write_text('\n'.join(delta)+'\n')
    receipt = dict(started_at=began, returncode=result.returncode,
                   firmware_sha256=SHA256, size=SIZE, before=status_before,
                   after=status_after, same_boot=before['boot_id']==after['boot_id'],
                   model_healthy=after['model']=='ok', temperature=after['temperature'],
                   kernel_capture_exit=kernel_after.returncode,
                   note='Hub startup only; sensor samples and calibration not accepted.')
    (raw / 'receipt.json').write_text(json.dumps(receipt, indent=2)+'\n')
    print(json.dumps(receipt, indent=2))
    if result.returncode or kernel_after.returncode or not receipt['same_boot']:
        raise RuntimeError('Startup/health anomaly; do not repeat or reset')


if __name__ == '__main__':
    main()
