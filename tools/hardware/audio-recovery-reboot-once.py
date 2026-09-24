#!/usr/bin/env python3
"""One explicitly selected recovery reboot, followed by bounded observation.

Requires the separate, successful audio RECOVERY flash receipt. No retries of
reboot, firmware writes, Android targets or hardware-control probes.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
SSH = ROOT / 'tools/s22-ssh'
OUT = ROOT / 'rootfs/hardware-reuse-20260924/hci-observer'
EXPECTED_TARGET = 'recovery'
EXPECTED_FLASH_SHA256 = '42da267f3dd9f94f30f62a95fb2ac13f91d4cf98f1a2307f7cc14e45d9c49be5'
EXPECTED_BASE_SHA256 = '758fc9d30491e17b7c829a89d338ba69476efa15a1280deb8a1b9b8009687f4b'
EXPECTED_BUILD_ID = '5.10.260-g4e5c5ad7d950'
MIN_UPTIME_SECONDS = 180
OBSERVATION_SECONDS = 600
SERIOUS_FAULT = re.compile(r'Kernel panic|Oops:|BUG:|Unable to handle kernel|general protection fault|Out of memory:|oom-kill|Call trace:', re.I)
BORE_RECORD = re.compile(r'^\[\s*\d+\].*$', re.M)

class ObserverError(RuntimeError):
    pass

SNAPSHOT = r'''
import json,pathlib,re,subprocess,urllib.request
p=pathlib.Path
def read(name,limit=16384):
 try:
  with open(name,'rb') as f:return f.read(limit).decode('utf-8','replace').rstrip('\0\n')
 except OSError:return None
ready=read('/run/s22-persistent-ready.json')
try:persistent=json.loads(ready) if ready else None
except Exception:persistent=None
try:
 op=urllib.request.build_opener(urllib.request.ProxyHandler({}))
 with op.open('http://127.0.0.1:8089/health',timeout=3) as f:model=json.load(f)
except Exception:model=None
try:
 r=subprocess.run(['dmesg'],capture_output=True,text=True,timeout=5,check=False)
 log=r.stdout[-131072:] if r.returncode==0 else None
except Exception:log=None
records=re.findall(r'^\[\s*\d+\].*$',read('/proc/boot_reset',16384) or '',re.M)
idle=False
if isinstance(model,dict):
 if model.get('assistant_idle') is True:idle=True
 elif type(model.get('active_requests')) is int and type(model.get('queued_requests')) is int:
  idle=model['active_requests']==0 and model['queued_requests']==0
 elif model.get('inference_active') is False:idle=True
state=dict(boot_id=read('/proc/sys/kernel/random/boot_id'),
 uptime_seconds=float((read('/proc/uptime') or '0').split()[0]),pid1=read('/proc/1/comm'),
 build_id=read('/proc/sys/kernel/osrelease'),boot_reset_first_record=records[0] if records else None,
 native_ready=p('/run/native-ready').exists(),persistent_ready=persistent,model=model,
 assistant_idle=idle,serious_fault=None if log is None else bool(re.search(
  r'Kernel panic|Oops:|BUG:|Unable to handle kernel|general protection fault|Out of memory:|oom-kill|Call trace:',log,re.I)))
print(json.dumps(state))
'''


def snapshot():
    result = subprocess.run([str(SSH), 'python3 -c '+shlex.quote(SNAPSHOT)],
                            capture_output=True, text=True, timeout=12)
    if result.returncode:
        raise RuntimeError('SSH snapshot unavailable')
    return json.loads(result.stdout)


def save(name, value):
    durable_json(OUT/name, value)


def require(condition, message):
    if not condition:
        raise ObserverError(message)


def validate_flash_receipt(flash):
    require(isinstance(flash, dict), 'flash receipt must be a JSON object')
    require(flash.get('mode') == 'flash', "flash receipt mode must be 'flash'")
    require(flash.get('partition_written') == EXPECTED_TARGET,
            'flash receipt target must be RECOVERY')
    require(flash.get('before_sha256') == EXPECTED_BASE_SHA256,
            'flash receipt baseline hash mismatch')
    require(flash.get('readback_sha256') == EXPECTED_FLASH_SHA256,
            'flash receipt candidate hash mismatch')
    require(flash.get('reboot_performed') is False,
            'flash receipt must show that deployment did not reboot')


def ready_value(value):
    return (isinstance(value, dict) and
            (value.get('ready') is True or value.get('status') == 'ready'))


def recovery_record(value):
    return (isinstance(value, str) and ' / R / ' in value and
            'INFORM3(12345674)' in value and ' > RECOVERY >' in value and
            not re.search(r'PANIC|WDOG|\bKP\b', value))


def validate_snapshot(state, *, post_reboot):
    require(isinstance(state, dict), 'device snapshot must be an object')
    require(state.get('pid1') == 'native-guardian', 'native guardian is not PID 1')
    require(state.get('native_ready') is True and ready_value(state.get('persistent_ready')),
            'native readiness is not established')
    require(isinstance(state.get('model'), dict) and state['model'].get('status') == 'ok',
            'assistant health endpoint is not healthy')
    require(state.get('assistant_idle') is True, 'assistant idleness is not established')
    require(state.get('serious_fault') is False,
            'serious-fault status is unknown or a serious fault was recorded')
    uptime = state.get('uptime_seconds')
    require(type(uptime) in (int, float) and uptime >= MIN_UPTIME_SECONDS,
            'device uptime is below 180 seconds')
    if post_reboot:
        require(state.get('build_id') == EXPECTED_BUILD_ID,
                'running kernel build ID does not match the HCI candidate')
        require(recovery_record(state.get('boot_reset_first_record')),
                'latest boot record does not prove the RECOVERY target')


def durable_json(path, value):
    data = (json.dumps(value, sort_keys=True, indent=2) + '\n').encode()
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_CLOEXEC', 0)
    flags |= getattr(os, 'O_NOFOLLOW', 0)
    fd = os.open(path, flags, 0o600)
    try:
        offset = 0
        while offset < len(data):
            count = os.write(fd, data[offset:])
            if count <= 0:
                raise OSError('durable write made no progress')
            offset += count
        os.fsync(fd)
    finally:
        os.close(fd)
    dfd = os.open(path.parent, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0)
                  | getattr(os, 'O_NOFOLLOW', 0))
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)


def ensure_private_directory(path):
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = path.lstat()
    require(path.is_dir() and not path.is_symlink(), 'state path is not a real directory')
    require(info.st_uid == os.geteuid() and info.st_mode & 0o077 == 0,
            'state directory must be private and owned by this user')


def read_json(path):
    try:
        info = path.lstat()
        require(path.is_file() and not path.is_symlink() and info.st_size <= 1048576,
                'receipt must be a bounded regular file')
        require(info.st_uid == os.geteuid() and info.st_mode & 0o077 == 0,
                'receipt must be private and owned by this user')
        value = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as error:
        raise ObserverError(f'cannot read receipt: {error}') from error
    require(isinstance(value, dict), 'receipt must contain a JSON object')
    return value


def observer_receipt_valid(value):
    observed = value.get('observed_seconds') if isinstance(value, dict) else None
    uptime = value.get('continuous_uptime_seconds') if isinstance(value, dict) else None
    return (isinstance(value, dict) and
            value.get('schema') == 's22-hci-recovery-observer/v1' and
            value.get('status') == 'completed' and
            value.get('target') == EXPECTED_TARGET and
            value.get('candidate_sha256') == EXPECTED_FLASH_SHA256 and
            value.get('build_id') == EXPECTED_BUILD_ID and
            value.get('actual_mode') == 'RECOVERY' and
            isinstance(value.get('boot_id'), str) and bool(value.get('boot_id')) and
            value.get('reboot_requests') == 1 and
            type(observed) in (int, float) and observed >= OBSERVATION_SECONDS and
            type(uptime) in (int, float) and uptime >= MIN_UPTIME_SECONDS and
            value.get('readiness') is True and value.get('assistant_idle') is True and
            value.get('no_serious_fault') is True)


def request_reboot_once(marker, result_path, runner=subprocess.run):
    durable_json(marker, {'target': EXPECTED_TARGET, 'status': 'request_started',
                          'created_at': datetime.now(timezone.utc).isoformat(),
                          'retry_allowed': False})
    try:
        result = runner([str(SSH), 's22-reboot recovery'], capture_output=True,
                        text=True, timeout=15, check=False)
    except subprocess.TimeoutExpired:
        durable_json(result_path, {'status': 'unknown_disconnect_after_request',
                                   'outcome': 'UNKNOWN', 'retry_allowed': False})
        raise ObserverError('reboot request disconnected; outcome UNKNOWN; no retry')
    if result.returncode != 0:
        durable_json(result_path, {'status': 'unknown_disconnect_after_request',
                                   'outcome': 'UNKNOWN', 'returncode': result.returncode,
                                   'retry_allowed': False})
        raise ObserverError('reboot request returned failure; outcome UNKNOWN; no retry')
    durable_json(result_path, {'status': 'request_returned', 'returncode': 0,
                               'retry_allowed': False})


def _ssh_transport(transport, host, remote):
    if transport == 'usb':
        return [str(SSH), remote]
    require(host and re.fullmatch(r'[A-Za-z0-9._:-]+', host),
            f'invalid {transport} SSH host')
    known_hosts = ROOT/'evidence/native-linux-20260919/native-v2-known-hosts'
    return ['ssh', '-o', 'StrictHostKeyChecking=yes', '-o',
            'UserKnownHostsFile='+str(known_hosts), '-o', 'ConnectTimeout=5',
            '-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=3',
            'root@'+host, remote]


def snapshot_over(transport='usb', host=None, runner=subprocess.run):
    result = runner(_ssh_transport(transport, host, 'python3 -c '+shlex.quote(SNAPSHOT)),
                    capture_output=True, text=True, timeout=15, check=False)
    if result.returncode:
        raise ObserverError(f'{transport} reconnect unavailable')
    try:
        state = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise ObserverError(f'{transport} returned invalid snapshot JSON') from error
    require(isinstance(state, dict), 'snapshot must be a JSON object')
    return state


def reconnect_snapshot(wifi_host=None, tailscale_host=None, runner=subprocess.run):
    failures = []
    routes = [('usb', None)]
    if wifi_host:
        routes.append(('wifi', wifi_host))
    if tailscale_host:
        routes.append(('tailscale', tailscale_host))
    for route, host in routes:
        try:
            return snapshot_over(route, host, runner), route
        except (ObserverError, OSError, subprocess.TimeoutExpired) as error:
            failures.append(route)
    raise ObserverError('no configured USB/WiFi/Tailscale route reconnected: '+','.join(failures))


def redacted_host_enumeration(runner=subprocess.run):
    """Return counts and interface states only; omit addresses and host identities."""
    found = {'usb_device_count': None, 'interfaces': None, 'wifi_devices': None,
             'tailscale': None}
    if shutil.which('lsusb'):
        result = runner(['lsusb'], capture_output=True, text=True, timeout=5, check=False)
        if result.returncode == 0:
            found['usb_device_count'] = len(result.stdout.splitlines())
    if shutil.which('ip'):
        result = runner(['ip', '-brief', 'link'], capture_output=True, text=True,
                        timeout=5, check=False)
        if result.returncode == 0:
            found['interfaces'] = [{'name': cols[0].rstrip(':'), 'state': cols[1]}
                                   for line in result.stdout.splitlines()
                                   if len(cols := line.split()) >= 2]
    if shutil.which('nmcli'):
        result = runner(['nmcli', '-t', '-f', 'DEVICE,TYPE,STATE', 'device', 'status'],
                        capture_output=True, text=True, timeout=5, check=False)
        if result.returncode == 0:
            found['wifi_devices'] = [{'device': fields[0], 'state': fields[2]}
                                     for line in result.stdout.splitlines()
                                     if len(fields := line.split(':')) >= 3 and fields[1] == 'wifi']
    if shutil.which('tailscale'):
        result = runner(['tailscale', 'status', '--json'], capture_output=True, text=True,
                        timeout=5, check=False)
        if result.returncode == 0:
            try:
                state = json.loads(result.stdout)
                peers = state.get('Peer', {})
                found['tailscale'] = {'backend_state': state.get('BackendState'),
                                      'peer_count': len(peers),
                                      'online_peers': sum(bool(p.get('Online')) for p in peers.values()
                                                          if isinstance(p, dict))}
            except (json.JSONDecodeError, AttributeError):
                found['tailscale'] = {'status_json_valid': False}
    return found


def candidate_hash():
    result = subprocess.run([str(SSH), 'sha256sum /dev/block/by-name/recovery'],
                            capture_output=True, text=True, timeout=30, check=False)
    require(result.returncode == 0, 'cannot read live RECOVERY hash')
    match = re.match(r'^([0-9a-f]{64})\s+', result.stdout)
    require(match is not None and match.group(1) == EXPECTED_FLASH_SHA256,
            'live RECOVERY hash does not match the candidate')


def run_reboot_observer(state_root, name, flash, wifi_host=None, tailscale_host=None,
                        observation_seconds=OBSERVATION_SECONDS, sample_interval=5):
    validate_flash_receipt(flash)
    before = snapshot_over('usb')
    validate_snapshot(before, post_reboot=False)
    candidate_hash()
    ensure_private_directory(state_root)
    out = state_root/name
    out.mkdir(mode=0o700)
    enumeration = redacted_host_enumeration()
    durable_json(out/'before.json', before)
    durable_json(out/'host-enumeration.json', enumeration)
    marker = state_root/'hci-candidate-reboot-attempted.json'
    request_reboot_once(marker, out/'request-result.json')

    prior_boot = before.get('boot_id')
    candidate_boot = None
    accepted = None
    samples = 0
    started = time.monotonic()
    deadline = started + observation_seconds
    last_valid_elapsed = -1
    while time.monotonic() < deadline:
        samples += 1
        try:
            state, transport = reconnect_snapshot(wifi_host, tailscale_host)
            elapsed = round(time.monotonic()-started, 2)
            state['host_elapsed_seconds'] = elapsed
            state['transport'] = transport
            durable_json(out/('sample-%03d.json' % samples), state)
            boot_id = state.get('boot_id')
            if candidate_boot is None and boot_id and boot_id != prior_boot:
                candidate_boot = boot_id
            if candidate_boot is not None:
                require(boot_id == candidate_boot, 'boot ID changed during observation')
                validate_snapshot(state, post_reboot=True)
                accepted = state
                last_valid_elapsed = elapsed
        except (ObserverError, OSError, subprocess.TimeoutExpired, ValueError) as error:
            accepted = None
            print(json.dumps({'elapsed_seconds': round(time.monotonic()-started, 2),
                              'observation_error': type(error).__name__}), flush=True)
        time.sleep(min(sample_interval, max(0, deadline-time.monotonic())))

    observed = round(time.monotonic()-started, 2)
    success = (candidate_boot is not None and accepted is not None and
               observed >= observation_seconds and
               last_valid_elapsed >= observation_seconds-sample_interval and
               accepted.get('uptime_seconds', 0) >= MIN_UPTIME_SECONDS)
    receipt = {'schema': 's22-hci-recovery-observer/v1',
               'status': 'completed' if success else 'incomplete',
               'target': EXPECTED_TARGET, 'candidate_sha256': EXPECTED_FLASH_SHA256,
               'build_id': EXPECTED_BUILD_ID, 'actual_mode': 'RECOVERY' if candidate_boot else None,
               'boot_id': candidate_boot, 'reboot_requests': 1,
               'observed_seconds': observed,
               'continuous_uptime_seconds': accepted.get('uptime_seconds') if accepted else None,
               'readiness': bool(accepted and accepted.get('native_ready') and
                                 ready_value(accepted.get('persistent_ready'))),
               'assistant_idle': bool(accepted and accepted.get('assistant_idle') is True),
               'no_serious_fault': bool(accepted and accepted.get('serious_fault') is False),
               'samples': samples, 'host_enumeration': enumeration}
    durable_json(out/'result.json', receipt)
    require(success, '600-second observation did not prove candidate RECOVERY readiness')
    return receipt


def run_hci_once(state_root, name, observer, runner=subprocess.run):
    require(observer_receipt_valid(observer),
            'hci-once requires a valid completed observer receipt')
    ensure_private_directory(state_root)
    out = state_root/(name+'-hci')
    out.mkdir(mode=0o700)
    marker = state_root/'hci-candidate-socket-attempted.json'
    durable_json(marker, {'status': 'request_started', 'retry_allowed': False,
                          'observer_boot_id': observer.get('boot_id')})
    probe = "import json,socket; s=socket.socket(socket.AF_BLUETOOTH,socket.SOCK_RAW,socket.BTPROTO_HCI); s.close(); print(json.dumps({'socket_created_and_closed':True,'attached':False,'scan_sent':False,'pairing_started':False}))"
    try:
        result = runner([str(SSH), 'python3 -c '+shlex.quote(probe)],
                        capture_output=True, text=True, timeout=15, check=False)
    except subprocess.TimeoutExpired:
        durable_json(out/'result.json', {'status': 'unknown_disconnect_after_request',
                                         'outcome': 'UNKNOWN', 'retry_allowed': False})
        raise ObserverError('HCI request disconnected; outcome UNKNOWN; no retry')
    receipt = {'schema': 's22-hci-socket-smoke/v1',
               'status': 'completed' if result.returncode == 0 else 'failed',
               'returncode': result.returncode, 'stdout': result.stdout[-2048:],
               'stderr': result.stderr[-2048:], 'attached': False,
               'scan_sent': False, 'pairing_started': False}
    durable_json(out/'result.json', receipt)
    require(result.returncode == 0, 'HCI socket smoke failed; no retry')
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=('reboot-observe', 'hci-once'), required=True)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--name', default='hci-candidate-20260924')
    parser.add_argument('--state-root', type=Path, default=OUT)
    parser.add_argument('--flash-receipt', type=Path, default=(Path.home()/
                        '.local/state/s22-hci-trial-20260924/receipts/hci-recovery-forward-flash.json'))
    parser.add_argument('--observer-receipt', type=Path)
    parser.add_argument('--wifi-host', default=os.environ.get('S22_WIFI_SSH_HOST'))
    parser.add_argument('--tailscale-host', default=os.environ.get('S22_TAILSCALE_SSH_HOST'))
    args = parser.parse_args(argv)
    if re.fullmatch(r'[a-z0-9-]+', args.name) is None:
        parser.error('use a unique lowercase evidence name')
    if not args.execute:
        print(json.dumps({'plan_only': True, 'mode': args.mode,
                          'target': EXPECTED_TARGET,
                          'observation_seconds': OBSERVATION_SECONDS if args.mode == 'reboot-observe' else None,
                          'retry_policy': 'request disconnect is UNKNOWN; never retry'}, indent=2))
        return 0
    os.umask(0o077)
    try:
        if args.mode == 'reboot-observe':
            result = run_reboot_observer(args.state_root, args.name,
                                         read_json(args.flash_receipt), args.wifi_host,
                                         args.tailscale_host)
        else:
            require(args.observer_receipt is not None,
                    'hci-once requires --observer-receipt')
            result = run_hci_once(args.state_root, args.name,
                                  read_json(args.observer_receipt))
    except (ObserverError, OSError, subprocess.TimeoutExpired, ValueError) as error:
        print(f'observer: {error}', file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
