#!/usr/bin/env python3
"""Bounded Bluetooth discovery on hci0 (S22 QCA6490 via the plain-H4 bridge).

Waits for hci0 (registered by bt-qca6490-hci-bridge-probe), brings it up
(HCIDEVUP), runs a passive LE scan (receive only) and one classic inquiry, then
HCIDEVDOWN. It does not pair, connect or advertise. It prints JSON. Addresses are
masked unless --show-addr is given.
"""
import argparse
import fcntl
import json
import os
import select
import socket
import struct
import sys
import time

HCIDEVUP, HCIDEVDOWN = 0x400448C9, 0x400448CA
SOL_HCI, HCI_FILTER = 0, 2


def mask(addr, show):
    return addr if show else addr[:8] + ":xx:xx:" + addr[-2:]


def bdaddr(b):
    return ":".join(f"{x:02X}" for x in reversed(b))


def ad_name(data):
    i = 0
    while i < len(data):
        ln = data[i]
        if ln == 0 or i + 1 + ln > len(data):
            break
        t = data[i + 1]
        if t in (0x08, 0x09):
            return data[i + 2:i + 1 + ln].decode("utf-8", "replace")
        i += 1 + ln
    return None


def cmd(s, ogf, ocf, params=b""):
    op = (ogf << 10) | ocf
    s.send(struct.pack("<BHB", 1, op, len(params)) + params)


def wait_cc(s, ogf, ocf, timeout=2.0):
    op = (ogf << 10) | ocf
    end = time.time() + timeout
    while time.time() < end:
        r, _, _ = select.select([s], [], [], end - time.time())
        if not r:
            break
        p = s.recv(300)
        if p[0] == 4 and p[1] == 0x0E and struct.unpack_from("<H", p, 4)[0] == op:
            return p[6]
        if p[0] == 4 and p[1] == 0x0F and struct.unpack_from("<H", p, 5)[0] == op:
            return p[3]
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wait", type=float, default=60)
    ap.add_argument("--le-seconds", type=float, default=6)
    ap.add_argument("--inquiry-units", type=int, default=4, help="x1.28 s")
    ap.add_argument("--show-addr", action="store_true")
    a = ap.parse_args()
    out = {"steps": []}
    t0 = time.time()
    while not os.path.exists("/sys/class/bluetooth/hci0"):
        if time.time() - t0 > a.wait:
            out["error"] = "hci0 never appeared"
            print(json.dumps(out))
            return 1
        time.sleep(0.2)
    out["hci0_after_s"] = round(time.time() - t0, 1)
    ctl = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_RAW, socket.BTPROTO_HCI)
    try:
        fcntl.ioctl(ctl, HCIDEVUP, 0)
        out["steps"].append("HCIDEVUP ok")
    except OSError as e:
        out["steps"].append(f"HCIDEVUP errno {e.errno}")  # 114 EALREADY = already up
    s = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_RAW, socket.BTPROTO_HCI)
    s.bind((0,))
    s.setsockopt(SOL_HCI, HCI_FILTER, struct.pack("<IIIH2x", 1 << 4, 0xFFFFFFFF, 0xFFFFFFFF, 0))
    try:
        cmd(s, 0x04, 0x0009)  # Read BD_ADDR
        end = time.time() + 2
        while time.time() < end:
            r, _, _ = select.select([s], [], [], 0.5)
            if r:
                p = s.recv(300)
                if p[1] == 0x0E and struct.unpack_from("<H", p, 4)[0] == 0x1009 and p[6] == 0:
                    out["local_addr"] = mask(bdaddr(p[7:13]), a.show_addr)
                    break
        cmd(s, 0x04, 0x0001)  # Read Local Version
        end = time.time() + 2
        while time.time() < end:
            r, _, _ = select.select([s], [], [], 0.5)
            if r:
                p = s.recv(300)
                if p[1] == 0x0E and struct.unpack_from("<H", p, 4)[0] == 0x1001 and p[6] == 0:
                    hv, hr, lv, mf, ls = struct.unpack_from("<BHBHH", p, 7)
                    out["version"] = {"hci": hv, "lmp": lv, "manufacturer": mf, "subver": hex(ls)}
                    break
        devices = {}
        # Passive LE scan: interval/window 0x10 (10 ms), public own addr, accept all.
        cmd(s, 0x08, 0x000B, struct.pack("<BHHBB", 0, 0x10, 0x10, 0, 0))
        out["le_set_params_status"] = wait_cc(s, 0x08, 0x000B)
        ext = out["le_set_params_status"] == 0x0C  # disallowed: host already uses extended scan
        if ext:
            # LE Set Extended Scan Parameters: own public, accept all, 1M PHY, passive 10 ms/10 ms.
            cmd(s, 0x08, 0x0041, struct.pack("<BBBBHH", 0, 0, 1, 0, 0x10, 0x10))
            out["le_ext_params_status"] = wait_cc(s, 0x08, 0x0041)
            cmd(s, 0x08, 0x0042, struct.pack("<BBHH", 1, 0, 0, 0))
            out["le_scan_enable_status"] = wait_cc(s, 0x08, 0x0042)
        else:
            cmd(s, 0x08, 0x000C, b"\x01\x00")
            out["le_scan_enable_status"] = wait_cc(s, 0x08, 0x000C)
        end = time.time() + a.le_seconds
        while time.time() < end:
            r, _, _ = select.select([s], [], [], 0.3)
            if not r:
                continue
            p = s.recv(300)
            if p[1] == 0x3E and p[3] == 0x0D:  # LE extended advertising report
                n, off = p[4], 5
                for _ in range(n):
                    if off + 24 > len(p):
                        break
                    addr = bdaddr(p[off + 3:off + 9])
                    rssi = struct.unpack_from("<b", p, off + 13)[0]
                    dl = p[off + 23]
                    data = p[off + 24:off + 24 + dl]
                    d = devices.setdefault(("le", addr), {"type": "le", "addr": mask(addr, a.show_addr), "reports": 0})
                    d["reports"] += 1
                    d["rssi"] = rssi
                    nm = ad_name(data)
                    if nm:
                        d["name"] = nm
                    off += 24 + dl
            if p[1] == 0x3E and p[3] == 0x02:  # LE advertising report
                n, off = p[4], 5
                for _ in range(n):
                    if off + 9 > len(p):
                        break
                    addr = bdaddr(p[off + 2:off + 8])
                    dl = p[off + 8]
                    data = p[off + 9:off + 9 + dl]
                    rssi = struct.unpack_from("<b", p, off + 9 + dl)[0] if off + 9 + dl < len(p) else None
                    d = devices.setdefault(("le", addr), {"type": "le", "addr": mask(addr, a.show_addr), "reports": 0})
                    d["reports"] += 1
                    d["rssi"] = rssi
                    nm = ad_name(data)
                    if nm:
                        d["name"] = nm
                    off += 10 + dl
        if ext:
            cmd(s, 0x08, 0x0042, struct.pack("<BBHH", 0, 0, 0, 0))
            wait_cc(s, 0x08, 0x0042)
        else:
            cmd(s, 0x08, 0x000C, b"\x00\x00")
            wait_cc(s, 0x08, 0x000C)
        # Classic inquiry (GIAC), with RSSI results if supported.
        cmd(s, 0x03, 0x0045, b"\x01")  # Write Inquiry Mode: RSSI
        wait_cc(s, 0x03, 0x0045)
        cmd(s, 0x01, 0x0001, b"\x33\x8b\x9e" + bytes([a.inquiry_units, 0]))
        end = time.time() + a.inquiry_units * 1.28 + 3
        while time.time() < end:
            r, _, _ = select.select([s], [], [], 0.3)
            if not r:
                continue
            p = s.recv(300)
            if p[1] == 0x01:
                out["inquiry_complete_status"] = p[3]
                break
            if p[1] == 0x0F and struct.unpack_from("<H", p, 5)[0] == 0x0401:
                out["inquiry_cmd_status"] = p[3]
            if p[1] in (0x02, 0x22, 0x2F):
                addr = bdaddr(p[4:10])
                d = devices.setdefault(("br", addr), {"type": "br/edr", "addr": mask(addr, a.show_addr)})
                if p[1] in (0x22, 0x2F):
                    d["rssi"] = struct.unpack_from("<b", p, 4 + 6 + 1 + 1 + 3 + 2)[0]
                if p[1] == 0x2F:
                    nm = ad_name(p[4 + 14:])
                    if nm:
                        d["name"] = nm
        out["devices"] = sorted(devices.values(), key=lambda d: -(d.get("rssi") or -127))
        out["le_count"] = sum(1 for k in devices if k[0] == "le")
        out["br_count"] = sum(1 for k in devices if k[0] == "br")
    finally:
        s.close()
        try:
            fcntl.ioctl(ctl, HCIDEVDOWN, 0)
            out["steps"].append("HCIDEVDOWN ok")
        except OSError as e:
            out["steps"].append(f"HCIDEVDOWN errno {e.errno}")
        ctl.close()
    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
