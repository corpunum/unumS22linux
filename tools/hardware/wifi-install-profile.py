#!/usr/bin/env python3
"""Remote, non-daemon WPA profile installer; consumes secret JSON on stdin."""
import json, os, re, sys, tempfile
from pathlib import Path

DEST = Path('/srv/s22/hardware/wifi-private/wpa_supplicant.conf')

def fail(msg):
    raise SystemExit(msg)

def install(data):
    if not isinstance(data, dict) or set(data) != {'ssid_hex', 'psk_hex'}: fail('invalid payload keys')
    ssid, psk = data['ssid_hex'], data['psk_hex']
    if not isinstance(ssid, str) or not isinstance(psk, str): fail('payload values must be strings')
    if len(ssid) == 0 or len(ssid) > 64 or len(ssid) % 2 or len(psk) != 64:
        fail('invalid SSID/PSK length')
    if not re.fullmatch('[0-9a-fA-F]+', ssid) or not re.fullmatch('[0-9a-fA-F]{64}', psk):
        fail('SSID/PSK must be strict hexadecimal')
    parent = DEST.parent
    if DEST.exists() or DEST.is_symlink(): fail('refusing existing profile')
    if parent.is_symlink(): fail('refusing symlink private directory')
    if parent.exists() and (parent.stat().st_mode & 0o077): fail('private directory permissions unsafe')
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(parent, 0o700)
    content = ('ctrl_interface=/run/wpa_supplicant-s22\nupdate_config=0\n\n'
               'network={\n\tkey_mgmt=WPA-PSK\n\tssid=' + ssid + '\n\tpsk=' + psk + '\n}\n')
    fd, tmp = tempfile.mkstemp(prefix='.wpa-', dir=str(parent), text=True)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'w') as out: out.write(content); out.flush(); os.fsync(out.fileno())
        # Atomic publication without replacing a concurrently created file.
        os.link(tmp, DEST)
        os.chmod(DEST, 0o600)
        if DEST.read_text() != content: fail('profile readback mismatch')
    finally:
        if os.path.exists(tmp): os.unlink(tmp)
    print(json.dumps({'status': 'written', 'bytes': len(content)}))

if __name__ == '__main__':
    try: data = json.load(sys.stdin)
    except Exception: fail('invalid JSON payload')
    install(data)
