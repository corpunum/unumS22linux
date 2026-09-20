#!/usr/bin/env python3
"""Bounded udhcpc hook for wlan0; preserves rescue networking."""
import ipaddress
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

STATE = Path(os.environ.get("WIFI_DHCP_STATE", "/srv/s22/hardware/wifi-private/lease.json"))
RESOLV = Path("/etc/resolv.conf")
BOOT_ID = Path('/proc/sys/kernel/random/boot_id')
RESCUE = ipaddress.ip_network("10.55.0.0/24")


def fail(message):
    print("wifi-dhcp-hook: " + message, file=sys.stderr)
    return 2


def ipv4(value, label):
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        raise ValueError(label + " is not IPv4")
    if address.version != 4:
        raise ValueError(label + " is not IPv4")
    if address.is_unspecified or address.is_loopback or address.is_multicast:
        raise ValueError(label + " is not a usable unicast address")
    return address


def lease_from_env():
    if os.environ.get("interface") != "wlan0":
        raise ValueError("refusing non-wlan0 interface")
    address = ipv4(os.environ.get("ip", ""), "lease address")
    routers = os.environ.get("router", "").split()
    if not routers:
        raise ValueError("gateway is missing")
    gateway = ipv4(routers[0], "gateway")
    for candidate in routers:
        ipv4(candidate, "gateway")
    mask = ipv4(os.environ.get("subnet", ""), "subnet mask")
    hostbits = (~int(mask)) & 0xffffffff
    if hostbits == 0xffffffff or hostbits & (hostbits + 1):
        raise ValueError("invalid contiguous subnet mask")
    prefix = 32 - hostbits.bit_length()
    network = ipaddress.ip_network((address, prefix), strict=False)
    if network.overlaps(RESCUE):
        raise ValueError("lease subnet overlaps USB ECM rescue subnet")
    if (gateway not in network or gateway in (network.network_address, network.broadcast_address)
            or address in (network.network_address, network.broadcast_address) or address == gateway):
        raise ValueError("gateway or lease address outside usable lease subnet")
    dns = []
    for candidate in os.environ.get("domain_name_servers", os.environ.get("dns", "")).split():
        dns.append(str(ipv4(candidate, "DNS server")))
    return {"address": str(address), "prefix": prefix, "gateway": str(gateway), "dns": dns}


def run_ip(*args, check=True):
    return subprocess.run((os.environ.get("WIFI_DHCP_IP", "ip"), *args),
                          check=check, capture_output=True, text=True)


def ensure_wlan_interface():
    if os.environ.get("interface") != "wlan0":
        raise ValueError("refusing non-wlan0 interface")


def conflicting_default():
    result = run_ip("route", "show", "default", "metric", "600")
    output = getattr(result, "stdout", "") or ""
    for line in output.splitlines():
        fields = line.split()
        if 'dev' not in fields or fields.index('dev') + 1 >= len(fields):
            return True
        if fields[fields.index('dev') + 1] != 'wlan0':
            return True
    return False


def save(lease):
    lease['boot_id'] = BOOT_ID.read_text().strip()
    STATE.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".lease.", dir=STATE.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as output:
            json.dump(lease, output, sort_keys=True)
            output.write("\n")
        os.replace(temporary, STATE)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def resolver_backup():
    return STATE.parent / "resolv.conf.before-wifi"


def current_lease():
    """A persistent lease cannot own addresses/resolver contents in a new boot."""
    if not STATE.is_file():
        return None
    lease = json.loads(STATE.read_text())
    if lease.get('boot_id') == BOOT_ID.read_text().strip():
        return lease
    # native-start recreates the USB resolver at boot. Drop only our stale
    # metadata, never apply old addresses or restore an earlier boot's DNS.
    STATE.unlink()
    resolver_backup().unlink(missing_ok=True)
    return None


def merged_resolver(original, dns):
    original_servers = []
    other = []
    options = []
    for line in original.splitlines():
        fields = line.split()
        if fields and fields[0].lower() == "nameserver":
            if len(fields) >= 2:
                try:
                    server = str(ipaddress.ip_address(fields[1]))
                except ValueError:
                    server = None
                if server is not None:
                    if server not in original_servers:
                        original_servers.append(server)
                    comment = line.find("#")
                    if comment >= 0:
                        other.append(line[comment:])
                    continue
            other.append(line)
        elif fields and fields[0].lower() == "options":
            options.extend(line.split('#', 1)[0].split()[1:])
            if '#' in line:
                other.append('#' + line.split('#', 1)[1])
        else:
            other.append(line)
    servers = []
    for server in dns + original_servers:
        if server not in servers and len(servers) < 3:
            servers.append(server)
    option_values = []
    for item in options:
        if item not in option_values:
            option_values.append(item)
    for value in ("timeout:2", "attempts:2"):
        if not any(item.split(":", 1)[0] == value.split(":", 1)[0] for item in option_values):
            option_values.append(value)
    lines = [f"nameserver {server}" for server in servers]
    lines.extend(other)
    lines.append("options " + " ".join(option_values))
    return "\n".join(lines) + "\n"


def update_resolver(dns, owned=None):
    if not dns:
        return None
    original = RESOLV.read_text()
    if owned is not None and original != owned:
        return False
    backup = resolver_backup()
    backup.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not backup.exists():
        fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as output:
            output.write(original)
    generated = merged_resolver(backup.read_text() if backup.exists() else original, dns)
    RESOLV.write_text(generated)
    return generated


def restore_resolver(lease):
    generated = lease.get("resolver_content")
    backup = resolver_backup()
    if not generated or not backup.is_file():
        return
    if RESOLV.read_text() == generated:
        RESOLV.write_text(backup.read_text())
        backup.unlink()
    else:
        # The user changed the resolver; abandon the private snapshot rather
        # than allowing a later lease to overwrite that change.
        backup.unlink()


def deconfig():
    try:
        ensure_wlan_interface()
    except ValueError as error:
        return fail(str(error))
    try:
        lease = current_lease()
        if lease is None:
            return 0
        if not {"address", "prefix", "gateway", "dns"}.issubset(lease):
            raise ValueError("invalid owned lease state")
        if not (isinstance(lease["address"], str) and isinstance(lease["gateway"], str)
                and isinstance(lease["prefix"], int) and isinstance(lease["dns"], list)
                and all(isinstance(item, str) for item in lease["dns"])):
            raise ValueError("invalid owned lease state")
        run_ip("route", "del", "default", "via", lease["gateway"], "dev", "wlan0", "metric", "600", check=False)
        run_ip("addr", "del", f"{lease['address']}/{lease['prefix']}", "dev", "wlan0", check=False)
        restore_resolver(lease)
    except (OSError, ValueError, json.JSONDecodeError, subprocess.CalledProcessError) as error:
        return fail(str(error))
    STATE.unlink()
    return 0


def bound():
    try:
        ensure_wlan_interface()
        lease = lease_from_env()
        if conflicting_default():
            raise ValueError("metric-600 default exists on another interface")
        old = current_lease()
        run_ip("addr", "replace", f"{lease['address']}/{lease['prefix']}", "dev", "wlan0")
        run_ip("route", "replace", "default", "via", lease["gateway"], "dev", "wlan0", "metric", "600")
        if old and old['gateway'] != lease['gateway']:
            run_ip("route", "del", "default", "via", old["gateway"], "dev", "wlan0", "metric", "600", check=False)
        if old and (old['address'], old['prefix']) != (lease['address'], lease['prefix']):
            run_ip("addr", "del", f"{old['address']}/{old['prefix']}", "dev", "wlan0", check=False)
        owned = old.get("resolver_content") if old else None
        if (old and old.get('resolver_user_override')) or (owned is not None and RESOLV.read_text() != owned):
            lease['resolver_user_override'] = True
            backup = resolver_backup()
            if backup.is_file():
                backup.unlink()
        else:
            generated = update_resolver(lease["dns"], owned)
            if generated is False:
                lease['resolver_user_override'] = True
                backup = resolver_backup()
                if backup.is_file():
                    backup.unlink()
            elif generated is not None:
                lease["resolver_content"] = generated
            elif not lease["dns"] and owned is not None:
                lease["resolver_content"] = owned
        save(lease)
    except (OSError, ValueError, subprocess.CalledProcessError, json.JSONDecodeError) as error:
        return fail(str(error))
    return 0


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else ""
    if action in ("deconfig", "leasefail"):
        raise SystemExit(deconfig())
    if action in ("bound", "renew"):
        raise SystemExit(bound())
    raise SystemExit(fail("unsupported udhcpc action"))
