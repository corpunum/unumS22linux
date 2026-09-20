#!/usr/bin/env python3
"""Select the active saved network; only explicit --install reads its secret."""
import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path


def nm(*args, secrets=False, privileged=False):
    cmd = (['sudo', '-n'] if privileged else []) + ['nmcli', '--terse', '--escape', 'no']
    if secrets:
        cmd.append('--show-secrets')
    result = subprocess.run(cmd + list(args), capture_output=True, text=True, timeout=15)
    if result.returncode:
        raise RuntimeError('NetworkManager query failed; output withheld')
    return result.stdout.rstrip('\n')


def select_profile(scan_file):
    seen = {line.split('SSID: ', 1)[1] for line in Path(scan_file).read_text().splitlines()
            if line.lstrip().startswith('SSID: ')}
    matches = []
    for line in nm('-f', 'UUID,TYPE', 'connection', 'show', '--active').splitlines():
        uuid, kind = line.split(':', 1)
        if kind != '802-11-wireless':
            continue
        ssid = nm('-g', '802-11-wireless.ssid', 'connection', 'show', 'uuid', uuid)
        if ssid in seen:
            matches.append((uuid, ssid))
    if len(matches) != 1:
        raise RuntimeError('requires exactly one active saved profile matching the phone scan')
    uuid, ssid = matches[0]
    if nm('-g', '802-11-wireless-security.key-mgmt', 'connection', 'show', 'uuid', uuid) != 'wpa-psk':
        raise RuntimeError('selected profile is not supported WPA-PSK; no downgrade attempted')
    if not 1 <= len(ssid.encode()) <= 32:
        raise RuntimeError('SSID length unsupported')
    return uuid, ssid


def derive_psk(secret, ssid):
    if re.fullmatch('[0-9A-Fa-f]{64}', secret):
        return secret.lower()
    if not 8 <= len(secret.encode()) <= 63:
        raise RuntimeError('saved PSK unavailable or invalid; value withheld')
    return hashlib.pbkdf2_hmac('sha1', secret.encode(), ssid.encode(), 4096, 32).hex()


def provision(scan_file, install=False):
    uuid, ssid = select_profile(scan_file)
    print(json.dumps({'active_matching_profile': True, 'security': 'wpa-psk',
                      'credentials_requested': install}), flush=True)
    if not install:
        return
    args = ('-g', '802-11-wireless-security.psk', 'connection', 'show', 'uuid', uuid)
    try:
        secret = nm(*args, secrets=True)
    except RuntimeError:
        secret = ''
    if not secret or secret == '<hidden>':
        secret = nm(*args, secrets=True, privileged=True)
    payload = json.dumps({'ssid_hex': ssid.encode().hex(),
                          'psk_hex': derive_psk(secret, ssid)}).encode()
    ssh = Path(__file__).resolve().parents[1] / 's22-ssh'
    result = subprocess.run([str(ssh), 'python3 /srv/s22/hardware/wifi-tools/wifi-install-profile.py'],
                            input=payload, capture_output=True, timeout=20)
    if result.returncode:
        raise RuntimeError('private profile installation failed; remote output withheld')
    response = json.loads(result.stdout)
    if response.get('status') != 'written':
        raise RuntimeError('unexpected private installation response')
    print(json.dumps({'profile_installed': True, 'plaintext_passphrase_stored': False}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scan-file', required=True)
    parser.add_argument('--install', action='store_true')
    args = parser.parse_args()
    try:
        provision(args.scan_file, args.install)
    except RuntimeError as exc:
        parser.exit(1, str(exc) + '\n')


if __name__ == '__main__':
    main()
