#!/usr/bin/env python3
"""Minimal Samsung SIPC client for the S22 (Exynos 2200, cpif ss310).

The kernel (cpif link_device.c, rild_ready) sends CMD_INIT_END to the CP only
when both umts_ipc0 (FMT) and umts_rfs0 (RFS) are open. Frames on umts_ipc0
are read/written without the SIPC5 link header (the kernel adds/strips it):

    u16 length (incl. header) | u8 mseq | u8 aseq | u8 group | u8 index | u8 type | data

This tool never sends call, SMS, or network-attach commands. The only
state-changing request it can send is IPC_PWR_PHONE_STATE, and only with
--radio lpm|normal. Normal mode lets the CP scan and camp on a cell, the
same as a phone with no SIM. It does not register or transmit.
"""
import argparse
import json
import os
import select
import struct
import sys
import time

GROUPS = {0x01: "PWR", 0x02: "CALL", 0x04: "SMS", 0x05: "SEC", 0x06: "PB",
          0x07: "DISP", 0x08: "NET", 0x09: "SND", 0x0A: "MISC", 0x0B: "SVC",
          0x0C: "SS", 0x0D: "GPRS", 0x0E: "SAT", 0x0F: "CFG", 0x10: "IMEI",
          0x11: "GPS", 0x12: "SAP", 0x42: "RFS", 0x80: "GEN"}
REQ_TYPES = {1: "EXEC", 2: "GET", 3: "SET", 4: "CFRM", 5: "EVENT"}
RSP_TYPES = {1: "INDI", 2: "RESP", 3: "NOTI"}
NAMES = {
    (0x01, 0x01): "PWR_PHONE_PWR_UP", (0x01, 0x02): "PWR_PHONE_PWR_OFF",
    (0x01, 0x03): "PWR_PHONE_RESET", (0x01, 0x08): "PWR_PHONE_STATE",
    (0x05, 0x01): "SEC_SIM_STATUS", (0x05, 0x02): "SEC_PHONE_LOCK",
    (0x05, 0x07): "SEC_SIM_ICC_TYPE", (0x05, 0x08): "SEC_RSIM_ACCESS",
    (0x05, 0x0A): "SEC_LOCK_INFO",
    (0x07, 0x01): "DISP_ICON_INFO", (0x07, 0x02): "DISP_HOMEZONE_INFO",
    (0x07, 0x06): "DISP_RSSI_INFO",
    (0x08, 0x03): "NET_CURRENT_PLMN", (0x08, 0x05): "NET_REGIST",
    (0x08, 0x08): "NET_SERVING_NETWORK", (0x08, 0x09): "NET_MODE_SEL",
    (0x0A, 0x01): "MISC_ME_VERSION", (0x0A, 0x02): "MISC_ME_IMSI",
    (0x0A, 0x03): "MISC_ME_SN", (0x0A, 0x05): "MISC_TIME_INFO",
    (0x80, 0x01): "GEN_PHONE_RES",
}
SIM_STATUS = {0x00: "READY", 0x01: "SIM_LOCK_REQUIRED", 0x02: "INSIDE_PF_ERROR",
              0x03: "LOCK_SC(PIN)", 0x04: "LOCK_FD", 0x80: "CARD_NOT_PRESENT",
              0x81: "CARD_ERROR", 0x82: "INIT_COMPLETE", 0x83: "PB_INIT_COMPLETE"}
# libsamsung-ipc pwr.h: u16 request state
PHONE_STATE = {"lpm": 0x0001, "normal": 0x0202}


def printable(b):
    return b.split(b"\0", 1)[0].decode("ascii", "replace").strip()


def decode(group, index, typ, data):
    """Best-effort decode for the few replies we ask for."""
    key = (group, index)
    try:
        if key == (0x0A, 0x03) and len(data) >= 2:   # ME_SN: type, len, str
            n = data[1]
            return {"sn_type": data[0], "value": printable(data[2:2 + n])}
        if key == (0x0A, 0x01) and len(data) >= 129:
            return {"software": printable(data[1:33]),
                    "hardware": printable(data[33:65]),
                    "cal_date": printable(data[65:97]),
                    "misc": printable(data[97:129])}
        if key == (0x05, 0x01) and data:
            return {"sim_status": SIM_STATUS.get(data[0], hex(data[0])),
                    "raw": data[:4].hex()}
        if key == (0x80, 0x01) and len(data) >= 5:
            g, i, t, code = data[0], data[1], data[2], struct.unpack_from("<H", data, 3)[0]
            return {"for": NAMES.get((g, i), f"{g:02x}/{i:02x}"),
                    "for_type": REQ_TYPES.get(t, t), "code": hex(code),
                    "ok": code == 0x8000}
        if key == (0x07, 0x01) and len(data) >= 6:
            # icon info: flags, rssi-bars, battery, act, reg ... (layout partly guessed)
            return {"flags": data[0], "bars": data[1], "raw": data.hex()}
    except Exception as e:  # noqa: BLE001 - decoding is diagnostic only
        return {"decode_error": str(e)}
    return None


def summarize(replies, show_imei=False):
    out = {}
    for r in replies:
        d = r.get("decoded") or {}
        n = r["name"]
        if n == "MISC_ME_SN" and d.get("sn_type") == 1 and d.get("value"):
            v = d["value"]
            imei = v[:15]
            out["imei"] = imei if show_imei else imei[:8] + "*****" + imei[-2:]
            out["imeisv_svn"] = v[15:17]
        elif n == "MISC_ME_VERSION" and "software" in d:
            out["modem_sw"] = d["software"]
            out["model"] = d.get("misc")
            out["hw"] = d.get("hardware")
        elif n == "SEC_SIM_STATUS" and "sim_status" in d:
            out["sim"] = d["sim_status"]
        elif n == "NET_REGIST" and "json" in d:
            j = d["json"]
            out["registration"] = {k: j.get(k) for k in ("act", "reg_status", "rej_cause", "pci", "cid", "tac", "band") if k in j}
        elif n == "DISP_RSSI_INFO" and "json" in d:
            j = d["json"]
            if "lte_rsrp" not in j:
                out.setdefault("signal", {}).update(j)
                continue
            sig = {"lte_rsrp_dbm": -j["lte_rsrp"] if j.get("lte_rsrp", 255) < 255 else None,
                   "lte_rsrq_db": -j["lte_rsrq"] if j.get("lte_rsrq", 255) < 255 else None,
                   "lte_rssnr_db": j["lte_rssnr"] / 10 if abs(j.get("lte_rssnr", 2**31 - 1)) < 1000 else None,
                   "lte_sig_str": j.get("lte_sig_str")}
            out.setdefault("signal", {}).update(sig)
        elif n == "DISP_ICON_INFO" and "bars" in d:
            out["bars"] = d["bars"]
    return out


class Modem:
    def __init__(self, log):
        self.ipc = os.open("/dev/umts_ipc0", os.O_RDWR | os.O_NONBLOCK)
        self.rfs = os.open("/dev/umts_rfs0", os.O_RDWR | os.O_NONBLOCK)
        self.mseq = 0
        self.log = log
        self.replies = []

    def emit(self, rec):
        rec["t"] = round(time.time(), 3)
        line = json.dumps(rec)
        print(line, flush=True)
        if self.log:
            self.log.write(line + "\n")
            self.log.flush()

    def send(self, group, index, typ, data=b""):
        self.mseq = self.mseq % 0xFF + 1
        frame = struct.pack("<HBBBBB", 7 + len(data), self.mseq, 0, group, index, typ) + data
        for _ in range(30):
            try:
                os.write(self.ipc, frame)
                break
            except BlockingIOError:
                time.sleep(0.1)
            except OSError as e:
                if e.errno == 11:  # EAGAIN: INIT_END not done yet
                    time.sleep(0.2)
                    continue
                raise
        self.emit({"dir": "tx", "name": NAMES.get((group, index), f"{group:02x}/{index:02x}"),
                   "type": REQ_TYPES.get(typ, typ), "mseq": self.mseq, "hex": frame.hex()})
        return self.mseq

    def pump(self, seconds):
        end = time.time() + seconds
        while time.time() < end:
            r, _, _ = select.select([self.ipc, self.rfs], [], [], max(0, end - time.time()))
            for fd in r:
                try:
                    buf = os.read(fd, 65536)
                except BlockingIOError:
                    continue
                if not buf:
                    continue
                if fd == self.rfs:
                    self.on_rfs(buf)
                else:
                    self.on_fmt(buf)

    def on_fmt(self, buf):
        off = 0
        while off + 7 <= len(buf):
            length, mseq, aseq, group, index, typ = struct.unpack_from("<HBBBBB", buf, off)
            if length < 7:
                self.emit({"dir": "rx", "bad": buf[off:].hex()})
                return
            data = buf[off + 7: off + length]
            rec = {"dir": "rx", "name": NAMES.get((group, index), f"{GROUPS.get(group, hex(group))}/{index:02x}"),
                   "type": RSP_TYPES.get(typ, typ), "mseq": mseq, "aseq": aseq,
                   "len": length, "hex": data[:160].hex()}
            dec = decode(group, index, typ, data)
            if data[:1] == b"{":
                try:
                    dec = {"json": json.loads(data.split(b"\0", 1)[0].decode())}
                except ValueError:
                    pass
            if dec:
                rec["decoded"] = dec
            self.replies.append(rec)
            self.emit(rec)
            off += length

    def on_rfs(self, buf):
        # RFS requests are logged only. We do not serve files to the CP.
        self.emit({"dir": "rfs-rx", "len": len(buf), "hex": buf[:96].hex()})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--listen", type=float, default=8.0, help="seconds to listen before queries")
    ap.add_argument("--after", type=float, default=6.0, help="seconds to listen after queries")
    ap.add_argument("--no-query", action="store_true")
    ap.add_argument("--radio", choices=sorted(PHONE_STATE), help="send PWR_PHONE_STATE")
    ap.add_argument("--show-imei", action="store_true", help="do not mask the IMEI")
    ap.add_argument("--log", default="/srv/s22/state/modem/ipc.jsonl")
    a = ap.parse_args()
    os.makedirs(os.path.dirname(a.log), exist_ok=True)
    with open(a.log, "a") as log:
        m = Modem(log)
        m.emit({"event": "opened", "devices": ["umts_ipc0", "umts_rfs0"]})
        m.pump(a.listen)
        if a.radio:
            m.send(0x01, 0x08, 0x01, struct.pack("<H", PHONE_STATE[a.radio]))
            m.pump(3)
        if not a.no_query:
            m.send(0x0A, 0x01, 0x02, b"\xff")          # ME_VERSION
            m.pump(1.5)
            m.send(0x0A, 0x03, 0x02, b"\x01")          # ME_SN: IMEI
            m.pump(1.5)
            m.send(0x0A, 0x03, 0x02, b"\x03")          # ME_SN: serial
            m.pump(1.5)
            m.send(0x05, 0x01, 0x02)                   # SIM status
            m.pump(1.5)
            m.send(0x07, 0x01, 0x02, b"\xff")          # icon info (rssi/bars)
            m.pump(1.5)
            m.send(0x08, 0x05, 0x02, b"\xff\x02")      # NET_REGIST (PS)
            m.pump(1.5)
            m.send(0x07, 0x06, 0x02)                   # RSSI info (JSON)
            m.pump(1.5)
            m.send(0x01, 0x08, 0x02)                   # phone state
        m.pump(a.after)
        m.emit({"event": "done", "rx_frames": len(m.replies)})
        summary = summarize(m.replies, a.show_imei)
        print("SUMMARY " + json.dumps(summary), flush=True)
        log.write(json.dumps({"summary": summary, "t": round(time.time(), 3)}) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
