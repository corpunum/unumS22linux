#!/usr/bin/env python3
"""Read-only live WLAN checks; emit no SSID, MAC, address, gateway or DNS IP.

Requires the existing reviewed connection. Sends a DNS query and an HTTPS
request through wlan0; never changes interfaces, routes, or services.
"""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import urllib.request


def run(*args):
    return subprocess.run(args, capture_output=True, text=True, timeout=30)


def main():
    results = {"timestamp_utc": datetime.now(timezone.utc).isoformat(),
               "uptime_seconds": float(Path('/proc/uptime').read_text().split()[0])}
    checks = {}
    stats = Path('/sys/kernel/debug/cnss/stats').read_text().strip()
    results['cnss_state'] = stats
    checks['cnss_mission_ready'] = stats.startswith('State: 0x420107(')
    status = run('wpa_cli', '-p', '/run/wpa_supplicant-s22', '-i', 'wlan0', 'status')
    values = dict(line.split('=', 1) for line in status.stdout.splitlines() if '=' in line)
    checks['wpa2_ccmp_completed'] = status.returncode == 0 and all(
        values.get(key) == value for key, value in
        [('wpa_state', 'COMPLETED'), ('key_mgmt', 'WPA2-PSK'), ('pairwise_cipher', 'CCMP')])
    lease_path = Path('/srv/s22/hardware/wifi-private/lease.json')
    lease = json.loads(lease_path.read_text())
    checks['lease_private'] = lease_path.stat().st_mode & 0o777 == 0o600
    checks['usb_carrier'] = Path('/sys/class/net/ecm0/carrier').read_text().strip() == '1'
    routes = run('ip', 'route', 'show', 'default')
    checks['usb_default_preserved'] = any('dev ecm0' in line for line in routes.stdout.splitlines())
    checks['wlan_default_metric_600'] = any('dev wlan0' in line and 'metric 600' in line
                                           for line in routes.stdout.splitlines())
    servers = [line.split()[1] for line in Path('/etc/resolv.conf').read_text().splitlines()
               if line.startswith('nameserver ') and len(line.split()) >= 2]
    checks['lease_dns_first'] = bool(servers and lease['dns'] and servers[0] == lease['dns'][0])
    native_stat = Path('/etc/resolv.conf').stat()
    arch_stat = Path('/mnt/omarchy-trial/etc/resolv.conf').stat()
    checks['arch_shares_resolver_inode'] = (native_stat.st_dev, native_stat.st_ino) == (
        arch_stat.st_dev, arch_stat.st_ino)
    for name, prefix in [('native', ()), ('arch', ('chroot', '/mnt/omarchy-trial'))]:
        resolved = run(*prefix, 'getent', 'hosts', 'example.com')
        checks[name + '_app_dns'] = resolved.returncode == 0 and bool(resolved.stdout.strip())

    query_id = os.urandom(2)
    query = query_id + struct.pack('!HHHHH', 0x0100, 1, 0, 0, 0)
    query += b'\x07example\x03com\x00' + struct.pack('!HH', 1, 1)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as dns:
        dns.settimeout(8)
        dns.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, b'wlan0\x00')
        dns.connect((lease['dns'][0], 53))
        dns.send(query)
        response = dns.recv(4096)
    header = struct.unpack('!HHHHHH', response[:12])
    checks['dns_forced_wlan0'] = (response[:2] == query_id and bool(header[1] & 0x8000)
                                  and header[1] & 0x000f == 0 and header[3] > 0)
    https = run('curl', '--noproxy', '*', '--interface', 'wlan0', '--connect-timeout', '10',
                '--max-time', '20', '-sS', '-o', '/dev/null', '-w', '%{http_code} %{ssl_verify_result}',
                'https://example.com/')
    checks['https_forced_wlan0_tls_verified'] = https.returncode == 0 and https.stdout == '200 0'
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open('http://127.0.0.1:8089/health', timeout=5) as model:
        checks['resident_model_healthy'] = json.load(model).get('status') == 'ok'
    results['checks'] = checks
    results['all_checks_passed'] = all(checks.values())
    results['not_tested'] = ['reboot_autostart', 'USB_disconnected', 'suspend_resume', 'roaming', 'sustained_throughput']
    print(json.dumps(results, indent=2, sort_keys=True))
    return 0 if all(checks.values()) else 1


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as error:
        # Network/library exception messages can include private addresses.
        print(json.dumps({'all_checks_passed': False, 'error_type': type(error).__name__}))
        raise SystemExit(1)
