#!/usr/bin/env python3
"""Export selected 2026-09-21 follow-up results, never raw/private captures.

--check-wifi additionally runs the existing non-mutating WLAN acceptance
checks over USB SSH. No hardware firmware, drivers or services are started.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[2]
PRIVATE = ROOT / 'rootfs/hardware-reuse-20260921'
PUBLIC = ROOT / 'evidence/hardware-followup-20260921'


def read(path):
    return json.loads(path.read_text())


def write(name, data):
    serialized = json.dumps(data, indent=2, sort_keys=True) + '\n'
    assert not any(x in serialized for x in ('boot_id', '/home/', 'sample_frames'))
    (PUBLIC / name).write_text(serialized)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check-wifi', action='store_true')
    args = parser.parse_args()
    PUBLIC.mkdir(parents=True, exist_ok=True)
    sensor_trials = []
    for name in ('magnetometer-native-first', 'magnetometer-native-repeat',
                 'light-native-first', 'light-native-repeat'):
        r = read(PRIVATE / 'sensor-samples' / name / 'receipt.json')
        sensor_trials.append(dict(
            name=name, started_at=r['started_at'], returncode=r['returncode'],
            source_sha256=r['source_sha256'], same_boot=r['same_boot'],
            kernel_capture_exit=r['kernel_capture_exit'],
            resident_4b_healthy=r['after']['model'] == 'ok',
            sampling={k: v for k, v in r['sampling'].items()
                      if k not in ('sample_frames', 'axes_min', 'axes_max')}))
    write('sensors.json', dict(trials=sensor_trials, limitations=[
        'Magnetometer accuracy reports zero: calibrated compass not accepted.',
        'Light lux remained 1; physical illumination response not tested.',
        'Enable mask and buffer flags restored; no desktop integration or autostart.']))

    r = read(PRIVATE / 'bt-trials/live-wlan-version-first/receipt.json')
    line = next(s for s in r['uart_output'].splitlines() if s.startswith('h4='))
    event = bytes.fromhex(line.split('raw=', 1)[1])
    assert len(event) == event[2] + 3 == 95
    assert event[:8] == bytes.fromhex('04 0e 5c 01 00 fc 00 06')
    version = event[8:].decode('ascii').strip()
    assert 'PF=QCA6490' in version and 'SoCID=0x400C0210' in version
    write('bluetooth-version.json', dict(
        **{k: r[k] for k in ('started_at', 'returncode', 'elapsed', 'source_sha256',
                            'binary_sha256', 'same_boot', 'kernel_capture_exit',
                            'after_metadata_exit', 'after_vote_check', 'strace_capture_exit')},
        event_bytes=len(event), command_complete_validated_by_exporter=True,
        controller_version=version, resident_4b_healthy=r['after']['model'] == 'ok',
        limitations=['Raw UART exchange only; no patch/NVM download, HCI, pairing or audio.',
                     'Driver logged GPIO503 request EBUSY for its already-owned GPIO; exchange completed.',
                     'Power-off and independent WLAN regulator vote restored.']))

    trial = PRIVATE / 'npu-trials/cellular-loader-first'
    r = read(trial / 'receipt.json')
    stdout = (trial / 'stdout.txt').read_text()
    trace = (trial / 'strace.txt').read_text()
    # Only the post-exec trace belongs to vendor loading; native Python has
    # earlier harmless terminal ioctls. No raw trace is published.
    execution = trace.split('execve("/bin/cellular-dlopen-probe"', 1)
    assert len(execution) == 2
    vendor_trace = execution[1]
    connects = [s for s in vendor_trace.splitlines() if 'connect(' in s]
    write('cellular-loader.json', dict(
        **{k: r[k] for k in ('started_at', 'returncode', 'elapsed_seconds', 'same_boot',
                            'manifest_sha256', 'probe_sha256', 'helper_sha256',
                            'closure_missing', 'closure_ambiguous', 'read_only_proc',
                            'private_network_namespace', 'devices_exposed',
                            'kernel_capture_exit', 'strace_capture_exit')},
        staged_files=r['staged']['files'],
        ril_symbol_resolved='result=symbol_resolved calls=none dlclose=none' in stdout,
        resident_4b_healthy=r['after']['model'] == 'ok',
        post_exec_ioctl_count=len(re.findall(r'\bioctl\(', vendor_trace)),
        post_exec_clone_count=len(re.findall(r'\bclone3?\(', vendor_trace)),
        post_exec_connects=len(connects),
        successful_connects=sum('= 0 ' in s for s in connects),
        limitations=['dlopen constructors executed; no RIL API, modem or SIM operation.',
                     'Absent generated linker configuration warned; load and lookup succeeded.',
                     'CP startup and protected NV/EFS handling remain unresolved.']))

    wifi_file = PRIVATE / 'followup-wifi.json'
    if args.check_wifi:
        result = subprocess.run([str(ROOT / 'tools/s22-ssh'), 'python3 -'],
                                input=(ROOT / 'tools/hardware/wifi-acceptance.py').read_text(),
                                text=True, capture_output=True, timeout=100)
        wifi = json.loads(result.stdout)
        wifi_file.write_text(json.dumps(wifi, indent=2) + '\n')
        if result.returncode or not wifi.get('all_checks_passed'):
            raise RuntimeError('WLAN checks failed; inspect private receipt')
    if wifi_file.exists():
        write('wifi.json', read(wifi_file))
    write('manifest.json', dict(format='s22-hardware-followup-public-v1',
        excluded=['firmware and vendor binary bytes', 'raw kernel logs and traces',
                  'device identifiers', 'timestamped sensor frames'],
        files={p.name: hashlib.sha256(p.read_bytes()).hexdigest()
               for p in sorted(PUBLIC.iterdir()) if p.is_file() and p.name != 'manifest.json'}))
    print(json.dumps({'exported': str(PUBLIC.relative_to(ROOT))}))


if __name__ == '__main__':
    main()
