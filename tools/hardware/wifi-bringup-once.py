#!/usr/bin/env python3
"""One-shot, device-specific QCA6490 experiment; inspect unless --activate.

Not an installer or boot service. Requires a fresh, never-activated CNSS state,
the reviewed local payload, and mounted read-only CNSS debugfs. Does not write
EFS, calibration files, partition devices, network settings, or MAC addresses.
Kernel WLAN recovery can continue independently after this observer exits.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time
import urllib.request

PAYLOAD = Path('/srv/s22/hardware/wifi-20260920')
MODULE_SHA = 'cbf8932d079e97006a5b7aae0e1b5acfe65b3e8b5113ab8fa095daa36796738d'
MANIFEST_SHA = '7ab578db57b5c2cef888a646ae989a81f815b9594d4b8f015331308229dca148'
CNSS = Path('/sys/devices/platform/qcom,cnss-qca6490')
STATS = Path('/sys/kernel/debug/cnss/stats')
FIRMWARE = Path('/proc/1/root/vendor/firmware')
TARGETS = ('qca6490', 'wlan', 'wlan-connection-roaming.ini',
           'wlan-connection-roaming-backup.ini')
OPTIONAL_NAME = 'qca6490/qdss_trace_config_v2.cfg'
RESPONDER = Path('/srv/s22/hardware/wifi-tools/wifi-optional-firmware.py')
RESPONDER_SHA = '9a37e9a340cb2cf3e17cad4bce5cfd9917c4f9187243c67347a41bf6820f7aa0'
RESPONDER_META = Path('/run/wifi-optional-firmware.json')
RESPONDER_LOCK = Path('/run/wifi-optional-firmware.lock')
POST_CAL_FULL = 0x420107
POST_CAL_IDLE = 0x420100


def event(kind, **fields):
    print(json.dumps(dict(event=kind, monotonic=round(time.monotonic(), 3), **fields)), flush=True)


def digest(path):
    with path.open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def run(*args, timeout=10):
    return subprocess.run(args, check=True, capture_output=True, text=True, timeout=timeout).stdout


def state():
    value = STATS.read_text().strip()
    if not re.fullmatch(r'State: 0x[0-9a-f]+\([^\n]*\)', value):
        raise RuntimeError('unexpected CNSS stats format')
    return value


def responder_ready(proc_root=Path('/proc')):
    """Verify the exact acknowledged optional-firmware responder is healthy."""
    if digest(RESPONDER) != RESPONDER_SHA:
        raise RuntimeError('optional-firmware responder hash mismatch')
    try:
        metadata = json.loads(RESPONDER_META.read_text())
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        raise RuntimeError('optional-firmware responder metadata unavailable') from exc
    if (metadata.get('ready') is not True or metadata.get('acknowledge') is not True or
            metadata.get('request') != OPTIONAL_NAME or
            not str(metadata.get('owner', '')).startswith(f'{metadata.get("pid")}:')):
        raise RuntimeError('optional-firmware responder metadata mismatch')
    pid = metadata.get('pid')
    if not isinstance(pid, int) or pid <= 1:
        raise RuntimeError('optional-firmware responder PID invalid')
    proc = Path(proc_root) / str(pid)
    try:
        cmdline = proc.joinpath('cmdline').read_bytes().split(b'\0')
        status = proc.joinpath('status').read_text()
    except FileNotFoundError as exc:
        raise RuntimeError('optional-firmware responder is not running') from exc
    expected = [b'/usr/bin/python3', str(RESPONDER).encode(),
                b'--serve', b'--acknowledge']
    if cmdline != expected + [b'']:
        raise RuntimeError('optional-firmware responder command mismatch')
    uids = next((line.split()[1:3] for line in status.splitlines()
                 if line.startswith('Uid:')), None)
    if uids != ['0', '0']:
        raise RuntimeError('optional-firmware responder is not root-owned')
    try:
        with RESPONDER_LOCK.open('r') as lock:
            import fcntl
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return metadata
    raise RuntimeError('optional-firmware responder lock is not held')


def calibration_success_since(before, after):
    # dmesg is a ring.  A newly timestamped marker is accepted even if older
    # lines rolled out; an unchanged marker (or a count-only increase caused
    # by duplicated text) is never enough evidence.
    marker = 'Calibration completed successfully'
    old = {line.strip() for line in before.splitlines() if marker in line}
    new = {line.strip() for line in after.splitlines() if marker in line}
    return bool(new - old)


def post_calibration_state(current, interfaces, fresh_calibration):
    """Allow only the observed full or idle post-calibration CNSS states."""
    match = re.fullmatch(r'State: (0x[0-9a-f]+)\([^\n]*\)', current)
    if not match or 'wlan0' not in interfaces or not fresh_calibration:
        return False
    value = int(match.group(1), 16)
    return value in (POST_CAL_FULL, POST_CAL_IDLE)


def preflight(allow_no_usb=False, require_usb=None):
    # ``require_usb`` is the explicit API used by the boot wrapper; retain
    # the CLI-oriented spelling for callers that already use it.
    if require_usb is not None:
        allow_no_usb = not require_usb
    if os.geteuid() != 0 or os.uname().release != '5.10.260-g4e5c5ad7d950':
        raise RuntimeError('wrong target kernel or uid')
    if Path('/proc/1/comm').read_text().strip() != 'native-guardian':
        raise RuntimeError('native guardian is not PID 1')
    if Path('/sys/module/wlan').exists() or state() != 'State: 0x400000(PCI PROBE DONE)':
        raise RuntimeError('requires fresh CNSS PCI-probed-only state, no WLAN module')
    if Path('/sys/module/firmware_class/parameters/path').read_text().strip() != '/vendor/firmware':
        raise RuntimeError('unexpected firmware path')
    dt = Path('/sys/firmware/devicetree/base/qcom,cnss-qca6490')
    if not (dt / 'qcom,wlan-cbc-enabled').exists() or (dt / 'use-nv-mac').exists():
        raise RuntimeError('CBC / optional DMS preconditions differ')
    if Path('/proc/sys/kernel/panic_on_warn').read_text().strip() != '0':
        raise RuntimeError('panic_on_warn enabled')
    if digest(PAYLOAD / 'modules/wlan.ko') != MODULE_SHA:
        raise RuntimeError('module hash mismatch')
    manifest = PAYLOAD / 'firmware.sha256'
    if digest(manifest) != MANIFEST_SHA:
        raise RuntimeError('firmware manifest mismatch')
    count = 0
    for line in manifest.read_text().splitlines():
        expected, name = line.split(maxsplit=1)
        rel = Path(name)
        if rel.is_absolute() or '..' in rel.parts or digest(PAYLOAD / 'firmware' / rel) != expected:
            raise RuntimeError('firmware closure mismatch')
        count += 1
    if count != 14:
        raise RuntimeError('wrong firmware file count')
    for name in TARGETS:
        target = FIRMWARE / name
        if target.exists() or target.is_symlink():
            raise RuntimeError('firmware target already exists; do not overlay unknown state')
    if not (FIRMWARE / 'tsp_stm').is_dir():
        raise RuntimeError('initial-root touchscreen assets absent')
    if (not allow_no_usb and
            Path('/sys/class/net/ecm0/carrier').read_text().strip() != '1'):
        raise RuntimeError('USB ECM carrier absent')
    responder_ready()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open('http://127.0.0.1:8089/health', timeout=5) as response:
        if json.load(response).get('status') != 'ok':
            raise RuntimeError('resident model unhealthy')
    event('preflight_ok', firmware_files=count, state=state(), optional_responder=True,
          usb_rescue_required=not allow_no_usb)


def activate():
    # Firmware staging and all slow checks precede module insertion. No model
    # turn, SSH round trip, or asset transfer can split insertion from fs_ready.
    for name in TARGETS:
        source, target = PAYLOAD / 'firmware' / name, FIRMWARE / name
        if source.is_dir():
            target.mkdir()
        else:
            target.touch(exist_ok=False)
        run('mount', '-o', 'bind', str(source), str(target))
        run('mount', '-o', 'remount,bind,ro', str(source), str(target))
    mounts = run('chroot', '/proc/1/root', '/system/bin/sh', '-c', 'cat /proc/mounts')
    for name in TARGETS:
        found = [line.split() for line in mounts.splitlines()
                 if len(line.split()) > 3 and line.split()[1] == '/vendor/firmware/' + name]
        if len(found) != 1 or 'ro' not in found[0][3].split(','):
            raise RuntimeError('initial-root firmware read-only bind not verified')
    event('firmware_binds_verified', targets=list(TARGETS))
    if state() != 'State: 0x400000(PCI PROBE DONE)':
        raise RuntimeError('CNSS changed before activation')
    responder_ready()
    # Stock rc's normal ramdump_mode 0/2 policy. No calibration bypass/quirks.
    (CNSS / 'recovery').write_text('1\n')
    event('wlan_only_recovery_enabled')
    # This attribute belongs to the existing CNSS platform driver, not WLAN;
    # opening it early verifies access without invoking its store callback.
    with (CNSS / 'fs_ready').open('w') as ready:
        started = time.monotonic()
        responder_ready()
        log_before = run('dmesg')
        run('/sbin/insmod', str(PAYLOAD / 'modules/wlan.ko'), timeout=15)
        ready.write('1\n')
        ready.flush()
        event('module_and_fs_ready', elapsed=round(time.monotonic() - started, 3))
    # No fabricated MAC completion: the reviewed driver's normal 10s grace
    # and firmware-MAC fallback remain intact. Do not rerun this event.
    deadline = time.monotonic() + 170
    while time.monotonic() < deadline:
        current = state()
        interfaces = sorted(p.name for p in Path('/sys/class/net').glob('*')
                            if (p / 'phy80211').exists())
        event('observe', state=current, wlan_interfaces=interfaces)
        if any(flag in current for flag in ('DRIVER_RECOVERY', 'FW_BOOT_RECOVERY', 'DEV_ERR', 'IN_PANIC')):
            raise RuntimeError('CNSS failure state; no further activation or unload attempted')
        log_after = run('dmesg')
        fresh_calibration = calibration_success_since(log_before, log_after)
        if post_calibration_state(current, interfaces, fresh_calibration):
            event('enumerated_after_calibration', connectivity_verified=False,
                  calibration_success=True, post_calibration_state=current)
            return
        time.sleep(5)
    raise RuntimeError('enumeration deadline expired; no automatic retry/unload')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--activate', action='store_true')
    parser.add_argument('--allow-no-usb', action='store_true',
                        help='allow boot use without the USB ECM rescue link')
    args = parser.parse_args()
    preflight(allow_no_usb=args.allow_no_usb)
    if args.activate:
        try:
            activate()
        except Exception:
            # A timer/recovery can still request firmware after our observer
            # fails. Do not remove assets under a live kernel or rmmod here.
            event('observer_failed_no_cleanup', wlan_loaded=Path('/sys/module/wlan').exists(),
                  firmware_left_available=True, automatic_retry=False)
            raise
