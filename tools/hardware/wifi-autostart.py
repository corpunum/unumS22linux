#!/usr/bin/env python3
"""One-shot optional native Wi-Fi startup. Default/--check never writes.

A durable incomplete attempt inhibits future boot activation. No retry,
unload, process-name kill, firmware cleanup, partition operation or reboot.
"""
import argparse
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time

ROOT = Path('/srv/s22')
READY = Path('/run/s22-persistent-ready.json')
ENABLED = Path('/etc/s22-wifi-enabled')
DISABLED = Path('/etc/s22-wifi-disabled')
PRIVATE = ROOT / 'hardware/wifi-private'
TOOLS = ROOT / 'hardware/wifi-tools'
PROFILE = PRIVATE / 'wpa_supplicant.conf'
ATTEMPT = PRIVATE / 'autostart-attempt.json'
LOCK = Path('/run/s22-wifi-autostart.lock')
PROC = Path('/proc')
MOUNTINFO = PROC / 'self/mountinfo'
DEBUGFS = Path('/sys/kernel/debug')
LINKDOWN = PROC / 'sys/net/ipv4/conf/ecm0/ignore_routes_with_linkdown'
WLAN_MODULE = Path('/sys/module/wlan')
UUID = '1dd55c26-bd57-489a-9d9b-4c60e6f430eb'
PIDFILES = {
    'optional-firmware': Path('/run/s22-optional-firmware.pid'),
    'wpa': Path('/run/s22-wpa.pid'),
    'udhcpc': Path('/run/s22-udhcpc.pid'),
}


def boot_id():
    return (PROC / 'sys/kernel/random/boot_id').read_text().strip()


def atomic_attempt(state, **fields):
    record = dict(boot_id=boot_id(), state=state, monotonic=round(time.monotonic(), 3), **fields)
    fd, temporary = tempfile.mkstemp(prefix='.attempt.', dir=PRIVATE)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'w') as output:
            json.dump(record, output, sort_keys=True)
            output.write('\n')
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, ATTEMPT)
        directory = os.open(PRIVATE, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def mount_at(target):
    for line in MOUNTINFO.read_text().splitlines():
        left, separator, right = line.partition(' - ')
        fields, post = left.split(), right.split()
        if separator and len(fields) > 5 and len(post) > 1 and fields[4] == str(target):
            return fields[2], post[0], fields[5].split(',')
    return None


def process_args(pid):
    try:
        raw = (PROC / str(pid) / 'cmdline').read_bytes()
        return [value.decode() for value in raw.split(b'\0') if value]
    except (OSError, UnicodeError):
        return []


def require_process(name, expected):
    try:
        pid = int(PIDFILES[name].read_text().strip())
        if pid <= 1 or process_args(pid) != expected:
            raise RuntimeError('unexpected service command or PID')
        status = (PROC / str(pid) / 'status').read_text()
        uids = next((line.split()[1:3] for line in status.splitlines() if line.startswith('Uid:')), None)
        if uids != ['0', '0']:
            raise RuntimeError('service is not root-owned')
        return pid
    except (OSError, ValueError) as error:
        raise RuntimeError('service PID unavailable: ' + name) from error


def check_attempt():
    if not ATTEMPT.exists():
        return
    old = json.loads(ATTEMPT.read_text())
    if old.get('state') != 'complete':
        raise RuntimeError('previous incomplete/failed Wi-Fi startup; no automatic retry')
    if old.get('boot_id') == boot_id():
        raise RuntimeError('Wi-Fi startup already attempted in this boot')


def check_base():
    if not ENABLED.is_file() or DISABLED.exists():
        raise RuntimeError('optional Wi-Fi startup disabled')
    if os.geteuid() != 0 or os.uname().release != '5.10.260-g4e5c5ad7d950':
        raise RuntimeError('wrong target kernel or uid')
    if (PROC / '1/comm').read_text().strip() != 'native-guardian':
        raise RuntimeError('native guardian is not PID1')
    mounted = mount_at(ROOT)
    if not mounted or mounted[0] != '259:20' or mounted[1] != 'ext4' or 'rw' not in mounted[2]:
        raise RuntimeError('verified userdata is not mounted at the expected path')
    if json.loads(READY.read_text()).get('uuid') != UUID:
        raise RuntimeError('persistent runtime is not ready')
    if PRIVATE.is_symlink() or not PRIVATE.is_dir() or PRIVATE.stat().st_mode & 0o777 != 0o700:
        raise RuntimeError('private directory must be a real mode-0700 directory')
    if PROFILE.is_symlink() or not PROFILE.is_file() or PROFILE.stat().st_mode & 0o777 != 0o600:
        raise RuntimeError('private profile must be a real mode-0600 file')
    if PRIVATE.stat().st_uid != 0 or PROFILE.stat().st_uid != 0:
        raise RuntimeError('private profile ownership differs')
    for name in ('wifi-bringup-once.py', 'wifi-optional-firmware.py', 'wifi-dhcp-hook.py'):
        if not (TOOLS / name).is_file():
            raise RuntimeError('required Wi-Fi tool absent')
    if not os.access(TOOLS / 'wifi-dhcp-hook.py', os.X_OK):
        raise RuntimeError('DHCP hook is not executable')
    check_attempt()
    if WLAN_MODULE.exists() or any(path.exists() for path in PIDFILES.values()):
        raise RuntimeError('existing WLAN module or process markers; no adoption/reload')
    for entry in PROC.iterdir():
        if not entry.name.isdigit():
            continue
        args = process_args(entry.name)
        if not args:
            continue
        if str(TOOLS / 'wifi-optional-firmware.py') in args:
            raise RuntimeError('an optional-firmware responder already exists')
        if Path(args[0]).name in ('wpa_supplicant', 'udhcpc') and 'wlan0' in args:
            raise RuntimeError('a WLAN userspace process already exists')


def harness_module():
    path = TOOLS / 'wifi-bringup-once.py'
    spec = importlib.util.spec_from_file_location('s22_wifi_harness', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run(*args, timeout=10):
    return subprocess.run(args, capture_output=True, text=True, check=True, timeout=timeout)


def ensure_debugfs():
    current = mount_at(DEBUGFS)
    if current is None:
        run('mount', '-t', 'debugfs', '-o', 'ro', 'debugfs', str(DEBUGFS))
        current = mount_at(DEBUGFS)
    if not current or current[1] != 'debugfs' or 'ro' not in current[2]:
        raise RuntimeError('expected read-only debugfs mount')
    if not (DEBUGFS / 'cnss/stats').is_file():
        raise RuntimeError('CNSS debugfs unavailable')


def start_daemon(name, command):
    logs = [PRIVATE / (name + '-autostart.' + suffix) for suffix in ('log', 'err')]
    for path in logs:
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            os.fchmod(fd, 0o600)
        finally:
            os.close(fd)
    run('start-stop-daemon', '--start', '--background', '--make-pidfile',
        '--pidfile', str(PIDFILES[name]), '--stdout', str(logs[0]),
        '--stderr', str(logs[1]), '--exec', command[0], '--', *command[1:])
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            return require_process(name, command)
        except RuntimeError:
            time.sleep(.1)
    raise RuntimeError('service did not become live: ' + name)


def wait_for_association(wpa_command):
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        require_process('wpa', wpa_command)
        status = subprocess.run(['wpa_cli', '-p', '/run/wpa_supplicant-s22',
                                 '-i', 'wlan0', 'status'],
                                capture_output=True, text=True, timeout=5)
        if status.returncode == 0 and 'wpa_state=COMPLETED' in status.stdout.splitlines():
            return True
        time.sleep(1)
    # AP absence is not a driver fault. WPA and DHCP can keep waiting without
    # repeating module insertion/calibration or disabling the next clean boot.
    return False


def start():
    check_base()
    atomic_attempt('starting')
    try:
        harness = harness_module()
        ensure_debugfs()
        responder = ['/usr/bin/python3', str(TOOLS / 'wifi-optional-firmware.py'),
                     '--serve', '--acknowledge']
        start_daemon('optional-firmware', responder)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                harness.responder_ready()
                break
            except (OSError, RuntimeError):
                time.sleep(.25)
        else:
            raise RuntimeError('optional-firmware responder readiness timeout')
        harness.preflight(require_usb=False)
        harness.activate()
        LINKDOWN.write_text('1\n')
        if LINKDOWN.read_text().strip() != '1':
            raise RuntimeError('USB linkdown route filter not enabled')
        wpa = ['/sbin/wpa_supplicant', '-D', 'nl80211', '-i', 'wlan0', '-c', str(PROFILE)]
        start_daemon('wpa', wpa)
        associated = wait_for_association(wpa)
        dhcp = ['/sbin/udhcpc', '-f', '-i', 'wlan0', '-p', str(PIDFILES['udhcpc']),
                '-s', str(TOOLS / 'wifi-dhcp-hook.py'), '-t', '4', '-T', '3', '-A', '30', '-a1000']
        start_daemon('udhcpc', dhcp)
        harness.responder_ready()
        for name, command in [('optional-firmware', responder), ('wpa', wpa), ('udhcpc', dhcp)]:
            require_process(name, command)
        atomic_attempt('complete', services_started=True, association_observed=associated,
                       internet_verified=False)
        print(json.dumps({'event': 'wifi_services_started', 'association_observed': associated,
                          'internet_verified': False}), flush=True)
    except Exception as error:
        # Keep firmware, responder and any already-started radio userspace.
        # A kernel request can outlive this observer. Never retry or unload.
        atomic_attempt('failed', reason_type=type(error).__name__)
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--start', action='store_true')
    mode.add_argument('--check', action='store_true')
    args = parser.parse_args(argv)
    if not args.start:
        check_base()
        print(json.dumps({'fresh_boot_prerequisites': True, 'mutating': False}))
        return 0
    with LOCK.open('a+') as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        start()
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as error:
        print(json.dumps({'event': 'wifi_startup_refused', 'reason_type': type(error).__name__,
                          'detail': str(error)}), flush=True)
        raise SystemExit(1)
