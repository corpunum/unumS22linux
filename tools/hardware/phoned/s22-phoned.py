#!/usr/bin/env python3
"""s22-phoned: SMS / call / SIM daemon for the Galaxy S22 (Exynos 2200, cpif ss310).

Speaks Samsung SIPC FMT frames on /dev/umts_ipc0 (the kernel adds/strips the
SIPC5 link header) and serves CP NV requests on /dev/umts_rfs0 from the PRIVATE
EFS copy, reusing tools/hardware/modem/s22-modem.py. The CP only gets INIT_END
once both nodes are open, so this daemon owns both.

Message layouts follow libsamsung-ipc (IPC 4.0 era). The S22 runs IPC 4.1
(vendor libsec-ril): many NET/DISP replies are JSON text. Every decoder accepts
JSON (payload starting with '{'), logs raw hex and never raises.

Safety: SMS send and dial are refused unless state-dir/tx_enabled exists AND
the destination is listed in state-dir/allowlist; emergency/short numbers are
always refused; outbound is rate limited. PWR_PHONE_STATE is sent only with
--radio-normal. No NET attach/mode commands exist in this file.
Python stdlib only (3.12+).
"""
from __future__ import annotations

import argparse
import collections
import http.client
import http.server
import importlib.machinery
import importlib.util
import json
import os
import queue
import random
import re
import select
import shutil
import signal
import socket
import socketserver
import stat
import struct
import sys
import threading
import time
import urllib.parse
from pathlib import Path

# ---------------------------------------------------------------- constants
HDR = struct.Struct("<HBBBBB")          # len(incl. header) mseq aseq group index type
EXEC, GET, SET, CFRM, EVENT = 1, 2, 3, 4, 5
INDI, RESP, NOTI = 1, 2, 3

PWR_PHONE_STATE = (0x01, 0x08)          # IPC 4.1 id observed by s22-modem (lib: 0x0107)
CALL_OUTGOING, CALL_INCOMING, CALL_RELEASE, CALL_ANSWER = (2, 1), (2, 2), (2, 3), (2, 4)
CALL_STATUS, CALL_LIST, CALL_BURST_DTMF, CALL_CONT_DTMF = (2, 5), (2, 6), (2, 7), (2, 8)
SMS_SEND_MSG, SMS_INCOMING_MSG, SMS_DELIVER_REPORT = (4, 1), (4, 2), (4, 6)
SMS_DEVICE_READY, SMS_SVC_CENTER_ADDR = (4, 7), (4, 0x0A)
SEC_PIN_STATUS = (5, 1)                 # a.k.a. SEC_SIM_STATUS
DISP_ICON_INFO, DISP_RSSI_INFO = (7, 1), (7, 6)
NET_CURRENT_PLMN, NET_REGIST, NET_SERVING_NETWORK = (8, 3), (8, 5), (8, 8)
GEN_PHONE_RES = (0x80, 1)
GEN_OK = 0x8000

NAMES = {
    PWR_PHONE_STATE: "PWR_PHONE_STATE", (1, 1): "PWR_PHONE_PWR_UP",
    CALL_OUTGOING: "CALL_OUTGOING", CALL_INCOMING: "CALL_INCOMING",
    CALL_RELEASE: "CALL_RELEASE", CALL_ANSWER: "CALL_ANSWER", CALL_STATUS: "CALL_STATUS",
    CALL_LIST: "CALL_LIST", CALL_BURST_DTMF: "CALL_BURST_DTMF", CALL_CONT_DTMF: "CALL_CONT_DTMF",
    SMS_SEND_MSG: "SMS_SEND_MSG", SMS_INCOMING_MSG: "SMS_INCOMING_MSG",
    SMS_DELIVER_REPORT: "SMS_DELIVER_REPORT", SMS_DEVICE_READY: "SMS_DEVICE_READY",
    SMS_SVC_CENTER_ADDR: "SMS_SVC_CENTER_ADDR", SEC_PIN_STATUS: "SEC_SIM_STATUS",
    (5, 2): "SEC_PHONE_LOCK", DISP_ICON_INFO: "DISP_ICON_INFO", DISP_RSSI_INFO: "DISP_RSSI_INFO",
    NET_CURRENT_PLMN: "NET_CURRENT_PLMN", NET_REGIST: "NET_REGIST",
    NET_SERVING_NETWORK: "NET_SERVING_NETWORK", (0x0A, 1): "MISC_ME_VERSION",
    (0x0A, 3): "MISC_ME_SN", GEN_PHONE_RES: "GEN_PHONE_RES",
}
SIM_STATUS = {0x00: "READY", 0x01: "SIM_LOCK_REQUIRED", 0x02: "INSIDE_PF_ERROR",
              0x03: "LOCK_SC(PIN)", 0x04: "LOCK_FD", 0x05: "LOCK_PN", 0x06: "LOCK_PU",
              0x07: "LOCK_PP", 0x08: "LOCK_PC", 0x80: "CARD_NOT_PRESENT",
              0x81: "CARD_ERROR", 0x82: "INIT_COMPLETE", 0x83: "PB_INIT_COMPLETE"}
SIM_USABLE = {"READY", "INIT_COMPLETE", "PB_INIT_COMPLETE"}
FAC_PIN1, FAC_PUK, FAC_BLOCKED = 0x01, 0x02, 0x05
REG_STATUS = {1: "none", 2: "home", 3: "searching", 4: "emergency", 5: "unknown", 6: "roaming"}
ACT = {0: "gsm", 1: "gsm", 2: "gprs", 3: "edge", 4: "umts", 0xFF: "unknown"}
CALL_STATUS_STATE = {1: "dialing", 2: "released", 3: "active", 4: "released", 5: "alerting"}
CALL_LIST_STATE = {1: "active", 2: "holding", 3: "dialing", 4: "alerting", 5: "incoming", 6: "waiting"}
EMERGENCY = {"112", "911", "999", "000", "08", "100", "101", "102", "103", "104", "108", "110",
             "118", "119", "120", "122", "15", "17", "18", "166", "199", "197", "1033"}
MIN_DIGITS = 7                          # anything shorter (short codes, emergency) is refused
STATE_DIR = "/srv/s22/state/phoned"
MODEM_STATE_PATH = "/sys/devices/platform/cpif/modem_state"


def now():
    return round(time.time(), 3)


# ---------------------------------------------------------------- SIPC framing
class Frame:
    __slots__ = ("mseq", "aseq", "group", "index", "type", "data")

    def __init__(self, mseq, aseq, group, index, typ, data=b""):
        self.mseq, self.aseq, self.group, self.index, self.type, self.data = (
            mseq, aseq, group, index, typ, bytes(data))

    @property
    def cmd(self):
        return (self.group, self.index)

    @property
    def name(self):
        return NAMES.get(self.cmd, f"{self.group:02x}/{self.index:02x}")

    def encode(self):
        return HDR.pack(HDR.size + len(self.data), self.mseq, self.aseq,
                        self.group, self.index, self.type) + self.data

    def __repr__(self):
        return f"Frame({self.name} t={self.type} m={self.mseq} a={self.aseq} {self.data.hex()})"


class FrameDecoder:
    """Accumulates bytes; yields whole frames. Handles several/partial frames per read."""

    def __init__(self):
        self.buf = b""
        self.errors = 0

    def feed(self, data):
        self.buf += data
        out = []
        while len(self.buf) >= HDR.size:
            length, mseq, aseq, g, i, t = HDR.unpack_from(self.buf)
            if length < HDR.size:
                # No sync marker exists; cpif hands over whole frames per read(),
                # so a bad header means the rest of this buffer is junk.
                self.errors += 1
                self.buf = b""
                break
            if len(self.buf) < length:
                break
            out.append(Frame(mseq, aseq, g, i, t, self.buf[HDR.size:length]))
            self.buf = self.buf[length:]
        return out


# ---------------------------------------------------------------- GSM 03.40 PDU
GSM7 = ("@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞ\x1bÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?"
        "¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà")
GSM7_EXT = {0x0A: "\f", 0x14: "^", 0x28: "{", 0x29: "}", 0x2F: "\\", 0x3C: "[",
            0x3D: "~", 0x3E: "]", 0x40: "|", 0x65: "€"}
GSM7_REV = {c: i for i, c in enumerate(GSM7) if i != 0x1B}
GSM7_EXT_REV = {c: i for i, c in GSM7_EXT.items()}


def gsm7_septets(text):
    """Text -> septet list, or None if a char is outside GSM-7 (default + extension)."""
    out = []
    for ch in text:
        if ch in GSM7_REV:
            out.append(GSM7_REV[ch])
        elif ch in GSM7_EXT_REV:
            out += [0x1B, GSM7_EXT_REV[ch]]
        else:
            return None
    return out


def gsm7_text(septets):
    out, esc = [], False
    for s in septets:
        if esc:
            out.append(GSM7_EXT.get(s, GSM7[s]))
            esc = False
        elif s == 0x1B:
            esc = True
        else:
            out.append(GSM7[s])
    return "".join(out)


def pack7(septets, fill=0):
    val, bits = 0, fill
    for s in septets:
        val |= (s & 0x7F) << bits
        bits += 7
    return val.to_bytes((bits + 7) // 8, "little")


def unpack7(data, count, fill=0):
    val = int.from_bytes(data, "little")
    return [(val >> (fill + 7 * k)) & 0x7F for k in range(count)]


def bcd_encode(digits):
    if len(digits) % 2:
        digits += "F"
    return bytes(int(digits[k + 1], 16) << 4 | int(digits[k], 16) for k in range(0, len(digits), 2))


def bcd_decode(data, ndigits=None):
    m = {10: "*", 11: "#", 12: "a", 13: "b", 14: "c"}
    out = []
    for b in data:
        for nib in (b & 0x0F, b >> 4):
            if nib == 0x0F:
                continue
            out.append(m.get(nib, str(nib)))
    s = "".join(out)
    return s[:ndigits] if ndigits is not None else s


def normalize_number(number):
    n = re.sub(r"[\s\-().]", "", str(number or ""))
    if n.startswith("00") and len(n) > 4:
        n = "+" + n[2:]
    return n


def encode_address(number):
    """TP-DA / TP-OA: digit count, TOA, BCD."""
    n = normalize_number(number)
    toa = 0x91 if n.startswith("+") else 0x81
    digits = n.lstrip("+")
    if not re.fullmatch(r"[0-9*#]{1,20}", digits):
        raise ValueError("invalid phone number")
    return bytes([len(digits), toa]) + bcd_encode(digits.replace("*", "A").replace("#", "B"))


def encode_smsc(number):
    """SMSC address without its length octet: TOA + BCD (as IPC_SMS_SVC_CENTER_ADDR returns it)."""
    return encode_address(number)[1:]


def decode_address(data, off):
    """Returns (number, new_offset) for an OA/DA/RA field."""
    ndig, toa = data[off], data[off + 1]
    nbytes = (ndig + 1) // 2
    raw = data[off + 2: off + 2 + nbytes]
    if (toa >> 4) & 0x07 == 5:          # alphanumeric
        text = gsm7_text(unpack7(raw, ndig * 4 // 7))
        return text, off + 2 + nbytes
    num = bcd_decode(raw, ndig)
    if (toa >> 4) & 0x07 == 1:
        num = "+" + num
    return num, off + 2 + nbytes


def decode_smsc(data, off=0):
    n = data[off]
    if n == 0:
        return None, off + 1
    toa, raw = data[off + 1], data[off + 2: off + 1 + n]
    num = bcd_decode(raw)
    return ("+" + num if (toa >> 4) & 7 == 1 else num), off + 1 + n


def decode_scts(b):
    d = [((x & 0x0F) * 10 + (x >> 4)) for x in b[:6]]
    tz = b[6]
    q = (tz & 0x07) * 10 + (tz >> 4)
    sign = "-" if tz & 0x08 else "+"
    return (f"{(2000 if d[0] < 70 else 1900) + d[0]:04d}-{d[1]:02d}-{d[2]:02d}T{d[3]:02d}:{d[4]:02d}:{d[5]:02d}"
            f"{sign}{q // 4:02d}:{(q % 4) * 15:02d}")


def encode_scts(t=None):
    tm = time.gmtime(t if t is not None else time.time())
    vals = [tm.tm_year % 100, tm.tm_mon, tm.tm_mday, tm.tm_hour, tm.tm_min, tm.tm_sec, 0]
    return bytes((v % 10) << 4 | (v // 10) for v in vals)


def split_text(text, max_parts=10):
    """Returns (dcs, [chunk payloads]); chunk is septet list (GSM-7) or bytes (UCS2)."""
    septs = gsm7_septets(text)
    if septs is not None:
        if len(septs) <= 160:
            return 0x00, [septs]
        chunks, cur = [], []
        k = 0
        while k < len(septs):
            step = 2 if septs[k] == 0x1B else 1
            if len(cur) + step > 153:
                chunks.append(cur)
                cur = []
            cur += septs[k:k + step]
            k += step
        chunks.append(cur)
    else:
        units = text.encode("utf-16-be")
        if len(units) <= 140:
            return 0x08, [units]
        chunks, k = [], 0
        while k < len(units):
            end = min(k + 134, len(units))
            if end < len(units) and 0xD8 <= units[end - 2] <= 0xDB:   # don't split a surrogate pair
                end -= 2
            chunks.append(units[k:end])
            k = end
        if len(chunks) > max_parts:
            raise ValueError(f"message too long ({len(chunks)} parts)")
        return 0x08, chunks
    if len(chunks) > max_parts:
        raise ValueError(f"message too long ({len(chunks)} parts)")
    return 0x00, chunks


def _user_data(dcs, chunk, udh=b""):
    if dcs == 0x00:
        if udh:
            hbits = len(udh) * 8
            fill = (7 - hbits % 7) % 7
            udl = (hbits + fill) // 7 + len(chunk)
            return udl, udh + pack7(chunk, fill)
        return len(chunk), pack7(chunk)
    return len(udh) + len(chunk), udh + chunk


def build_submit_pdus(to, text, ref=0, srr=True, max_parts=10):
    """GSM 03.40 SMS-SUBMIT PDUs (without SMSC). Concatenation uses 8-bit-ref UDH."""
    dcs, chunks = split_text(text, max_parts)
    da = encode_address(to)
    total = len(chunks)
    pdus = []
    for seq, chunk in enumerate(chunks, 1):
        udh = bytes([5, 0x00, 3, ref & 0xFF, total, seq]) if total > 1 else b""
        first = 0x01 | 0x10 | (0x20 if srr else 0) | (0x40 if udh else 0)   # SUBMIT, VPF relative
        udl, ud = _user_data(dcs, chunk, udh)
        pdus.append(bytes([first, 0x00]) + da + bytes([0x00, dcs, 0xA7, udl]) + ud)
    return pdus


def build_deliver_pdu(sender, text, smsc="+0000", concat=None, ts=None):
    """SMS-DELIVER with SMSC prefix (used by the simulator and tests)."""
    dcs, chunks = split_text(text, 1)
    udh = b""
    if concat:
        ref, total, seq = concat
        udh = (bytes([6, 0x08, 4]) + struct.pack(">H", ref) + bytes([total, seq])
               if ref > 0xFF else bytes([5, 0x00, 3, ref, total, seq]))
    sc = encode_smsc(smsc)
    udl, ud = _user_data(dcs, chunks[0], udh)
    first = 0x04 | (0x40 if udh else 0)
    return (bytes([len(sc)]) + sc + bytes([first]) + encode_address(sender) +
            bytes([0x00, dcs]) + encode_scts(ts) + bytes([udl]) + ud)


def build_status_report_pdu(recipient, mr, st=0, smsc="+0000"):
    sc = encode_smsc(smsc)
    return (bytes([len(sc)]) + sc + bytes([0x06, mr & 0xFF]) + encode_address(recipient) +
            encode_scts() + encode_scts() + bytes([st]))


def _dcs_alphabet(dcs):
    group = dcs >> 4
    if group & 0x0C == 0:                      # general data coding
        return {0: "gsm7", 1: "8bit", 2: "ucs2"}.get((dcs >> 2) & 3, "gsm7")
    if group == 0x0F:
        return "8bit" if dcs & 0x04 else "gsm7"
    if group == 0x0E:
        return "ucs2"
    return "gsm7"


def _decode_ud(first, dcs, udl, ud):
    concat, udh_len = None, 0
    if first & 0x40 and ud:
        udh_len = ud[0] + 1
        k = 1
        while k + 1 < udh_len:
            iei, ielen = ud[k], ud[k + 1]
            ie = ud[k + 2: k + 2 + ielen]
            if iei == 0x00 and ielen == 3:
                concat = {"ref": ie[0], "total": ie[1], "seq": ie[2]}
            elif iei == 0x08 and ielen == 4:
                concat = {"ref": ie[0] << 8 | ie[1], "total": ie[2], "seq": ie[3]}
            k += 2 + ielen
    alpha = _dcs_alphabet(dcs)
    if alpha == "gsm7":
        hsept = (udh_len * 8 + 6) // 7
        text = gsm7_text(unpack7(ud, udl)[hsept:])
    elif alpha == "ucs2":
        text = ud[udh_len:udl].decode("utf-16-be", "replace")
    else:
        text = ud[udh_len:udl].hex()
    return text, concat, alpha


def decode_pdu(pdu, has_smsc=True):
    """Decode SMS-DELIVER / SMS-STATUS-REPORT / SMS-SUBMIT. Raises ValueError/IndexError on junk."""
    pdu = bytes(pdu)
    smsc, off = decode_smsc(pdu) if has_smsc else (None, 0)
    first = pdu[off]
    mti = first & 0x03
    off += 1
    out = {"smsc": smsc}
    if mti == 0:                                  # DELIVER
        sender, off = decode_address(pdu, off)
        pid, dcs = pdu[off], pdu[off + 1]
        ts = decode_scts(pdu[off + 2: off + 9])
        udl = pdu[off + 9]
        text, concat, alpha = _decode_ud(first, dcs, udl, pdu[off + 10:])
        out.update(kind="deliver", sender=sender, pid=pid, dcs=dcs, scts=ts, text=text,
                   concat=concat, alphabet=alpha, status_report=bool(first & 0x20))
    elif mti == 2:                                # STATUS-REPORT
        mr = pdu[off]
        recipient, off = decode_address(pdu, off + 1)
        scts, dt = decode_scts(pdu[off: off + 7]), decode_scts(pdu[off + 7: off + 14])
        st = pdu[off + 14]
        delivered = True if st < 0x20 else (None if st < 0x40 else False)   # done / pending / failed
        out.update(kind="status_report", mr=mr, recipient=recipient, scts=scts,
                   discharge=dt, st=st, delivered=delivered)
    elif mti == 1:                                # SUBMIT
        mr = pdu[off]
        dest, off = decode_address(pdu, off + 1)
        pid, dcs = pdu[off], pdu[off + 1]
        off += 2
        vpf = (first >> 3) & 3
        off += {0: 0, 2: 1}.get(vpf, 7)
        udl = pdu[off]
        text, concat, alpha = _decode_ud(first, dcs, udl, pdu[off + 1:])
        out.update(kind="submit", mr=mr, to=dest, dcs=dcs, text=text, concat=concat,
                   alphabet=alpha, srr=bool(first & 0x20))
    else:
        raise ValueError(f"unsupported MTI {mti}")
    return out


# ---------------------------------------------------------------- frame decoders (pure)
def _json(data):
    if data[:1] == b"{":
        try:
            return json.loads(data.split(b"\0", 1)[0].decode("utf-8", "replace"))
        except ValueError:
            return None
    return None


def dec_gen(d):
    g, i, t, code = d[0], d[1], d[2], struct.unpack_from("<H", d, 3)[0]
    return {"for": NAMES.get((g, i), f"{g:02x}/{i:02x}"), "for_cmd": [g, i], "for_type": t,
            "code": code, "ok": code == GEN_OK}


def dec_sim(d):
    j = _json(d)
    if j is not None:
        code = next((j[k] for k in ("sim_status", "status", "card_status", "pin_status")
                     if isinstance(j.get(k), int)), None)
        fac = j.get("facility_lock") if isinstance(j.get("facility_lock"), int) else None
        return {"status": code, "facility_lock": fac, "json": j}
    return {"status": d[0], "facility_lock": d[1] if len(d) > 1 else None}


def sim_name(status, fac):
    if status is None:
        return "UNKNOWN"
    if status == 0x03 and fac == FAC_PUK:
        return "LOCK_SC(PUK)"
    if status == 0x03 and fac == FAC_BLOCKED:
        return "BLOCKED"
    return SIM_STATUS.get(status, f"0x{status:02x}")


def dec_regist(d):
    j = _json(d)
    if j is not None:
        return dict(j)
    act, dom, st, edge, lac, cid = struct.unpack_from("<BBBBHI", d)
    return {"act": ACT.get(act, act), "domain": {2: "cs", 3: "ps"}.get(dom, dom),
            "reg_status": REG_STATUS.get(st, st), "edge": edge, "lac": lac, "cid": cid,
            "rej_cause": d[10] if len(d) > 10 else None}


def dec_plmn(d):
    j = _json(d)
    if j is not None:
        name = next((j[k] for k in ("long_name", "short_name", "operator", "name", "spn")
                     if isinstance(j.get(k), str) and j[k]), None)
        plmn = next((str(j[k]) for k in ("plmn", "mcc_mnc", "mccmnc") if j.get(k)), None)
        return {"operator": name or plmn, "plmn": plmn, "json": j}
    m = re.search(rb"[0-9]{5,6}", d)
    plmn = m.group().decode() if m else None
    return {"operator": plmn, "plmn": plmn}


def dec_rssi(d):
    j = _json(d)
    if j is None:
        return {"rssi": d[0]} if d else {}
    if "lte_rsrp" not in j:
        return dict(j)
    return {"lte_rsrp_dbm": -j["lte_rsrp"] if j.get("lte_rsrp", 255) < 255 else None,
            "lte_rsrq_db": -j["lte_rsrq"] if j.get("lte_rsrq", 255) < 255 else None,
            "lte_rssnr_db": j["lte_rssnr"] / 10 if abs(j.get("lte_rssnr", 2**31 - 1)) < 1000 else None,
            "lte_sig_str": j.get("lte_sig_str")}


def dec_icon(d):
    j = _json(d)
    if j is not None:
        return dict(j)
    return {"flags": d[0], "bars": d[1] if len(d) > 1 else None,
            "battery": d[3] if len(d) > 3 else None}


def dec_call_incoming(d):
    j = _json(d)
    if j is not None:
        return {"id": j.get("id", j.get("call_id")), "json": j}
    return {"type": d[1], "id": d[2], "line": d[3] if len(d) > 3 else None}


def dec_call_status(d):
    j = _json(d)
    if j is not None:
        return {"id": j.get("id", j.get("call_id")), "status": j.get("status"),
                "end_cause": j.get("end_cause"), "json": j}
    return {"type": d[1], "id": d[2], "status": d[3],
            "reason": d[4] if len(d) > 4 else None, "end_cause": d[5] if len(d) > 5 else None}


def dec_call_list(d):
    j = _json(d)
    if j is not None:
        return {"calls": j.get("calls", []), "json": j}
    count, off, calls = d[0], 1, []
    for _ in range(count):
        _u, typ, cid, term, st, mpty, nlen, _u2 = d[off: off + 8]
        number = d[off + 8: off + 8 + nlen].split(b"\0", 1)[0].decode("ascii", "replace")
        calls.append({"id": cid, "type": typ, "direction": "out" if term == 1 else "in",
                      "state": CALL_LIST_STATE.get(st, f"0x{st:02x}"), "mpty": mpty, "number": number})
        off += 8 + nlen
    return {"calls": calls}


def dec_sms_incoming(d):
    msg_type, typ, sim_index, mid, length = struct.unpack_from("<BBHBB", d)
    pdu = d[6: 6 + length]
    out = {"msg_type": msg_type, "type": typ, "sim_index": sim_index, "id": mid, "pdu": pdu.hex()}
    try:
        out["sms"] = decode_pdu(pdu, True)
    except (ValueError, IndexError):
        try:
            out["sms"] = decode_pdu(pdu, False)
        except (ValueError, IndexError) as e:
            out["pdu_error"] = str(e) or "short pdu"
    return out


def dec_sms_send(d):
    typ, ack, mid = struct.unpack_from("<BHB", d)
    return {"type": typ, "ack": ack, "id": mid, "ok": ack == 0}


def dec_svc_center(d):
    n = d[0]
    return {"smsc_raw": d[1:1 + n].hex()} if n else {"smsc_raw": ""}


DECODERS = {GEN_PHONE_RES: dec_gen, SEC_PIN_STATUS: dec_sim, NET_REGIST: dec_regist,
            NET_CURRENT_PLMN: dec_plmn, NET_SERVING_NETWORK: dec_plmn,
            DISP_RSSI_INFO: dec_rssi, DISP_ICON_INFO: dec_icon,
            CALL_INCOMING: dec_call_incoming, CALL_STATUS: dec_call_status,
            CALL_LIST: dec_call_list, SMS_INCOMING_MSG: dec_sms_incoming,
            SMS_SEND_MSG: dec_sms_send, SMS_SVC_CENTER_ADDR: dec_svc_center}


def describe(frame):
    """Decode a frame without side effects. Never raises."""
    out = {"name": frame.name, "type": frame.type, "mseq": frame.mseq, "aseq": frame.aseq,
           "hex": frame.data.hex()}
    fn = DECODERS.get(frame.cmd)
    try:
        if fn is not None and frame.data:
            out["decoded"] = fn(frame.data)
        elif frame.data[:1] == b"{":
            out["decoded"] = {"json": _json(frame.data)}
    except Exception as e:  # noqa: BLE001 - decoding must never crash the daemon
        out["decode_error"] = f"{type(e).__name__}: {e}"
    return out


# ---------------------------------------------------------------- small utilities
class JsonlLog:
    def __init__(self, path, max_bytes=4 << 20):
        self.path, self.max_bytes, self.lock = path, max_bytes, threading.Lock()

    def write(self, rec):
        rec = dict(rec, t=now())
        line = json.dumps(rec, ensure_ascii=False) + "\n"
        with self.lock:
            try:
                if os.path.exists(self.path) and os.path.getsize(self.path) >= self.max_bytes:
                    os.replace(self.path, self.path + ".1")
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(line)
            except OSError:
                pass


class JsonlStore:
    """Append-only JSONL keyed by 'id'; later records replace earlier ones."""

    def __init__(self, path, keep=1000):
        self.path, self.lock = path, threading.Lock()
        self.items = collections.OrderedDict()
        self.keep = keep
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    try:
                        rec = json.loads(line)
                        self.items[rec["id"]] = rec
                        self.items.move_to_end(rec["id"])
                    except (ValueError, KeyError, TypeError):
                        continue
        except FileNotFoundError:
            pass
        while len(self.items) > keep:
            self.items.popitem(last=False)

    def put(self, rec):
        with self.lock:
            self.items[rec["id"]] = rec
            while len(self.items) > self.keep:
                self.items.popitem(last=False)
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def values(self):
        with self.lock:
            return list(self.items.values())


class EventBus:
    def __init__(self, maxlen=2000):
        self.cond = threading.Condition()
        self.seq = 0
        self.events = collections.deque(maxlen=maxlen)

    def emit(self, typ, **kw):
        with self.cond:
            self.seq += 1
            ev = {"seq": self.seq, "type": typ, "ts": now(), **kw}
            self.events.append(ev)
            self.cond.notify_all()
            return ev

    def since(self, seq, timeout=0.0):
        end = time.monotonic() + max(0.0, min(timeout, 60.0))
        with self.cond:
            while True:
                evs = [e for e in self.events if e["seq"] > seq]
                left = end - time.monotonic()
                if evs or left <= 0:
                    return self.seq, evs
                self.cond.wait(left)


class RateLimit:
    def __init__(self, limit, window=3600.0):
        self.limit, self.window, self.stamps, self.lock = limit, window, collections.deque(), threading.Lock()

    def take(self, t=None):
        t = time.time() if t is None else t
        with self.lock:
            while self.stamps and self.stamps[0] < t - self.window:
                self.stamps.popleft()
            if len(self.stamps) >= self.limit:
                return False
            self.stamps.append(t)
            return True


def is_emergency(number):
    digits = re.sub(r"\D", "", normalize_number(number))
    return digits in EMERGENCY or len(digits) < MIN_DIGITS


def load_s22_modem():
    """Import tools/hardware/modem/s22-modem.py (or the installed s22-modem) for the RFS server."""
    here = Path(__file__).resolve().parent
    cands = [os.environ.get("S22_MODEM_PY"), here.parent / "modem" / "s22-modem.py",
             here / "s22-modem.py", here / "s22-modem", shutil.which("s22-modem")]
    for c in cands:
        if c and Path(c).is_file():
            loader = importlib.machinery.SourceFileLoader("s22_modem", str(c))
            spec = importlib.util.spec_from_loader("s22_modem", loader)
            mod = importlib.util.module_from_spec(spec)
            loader.exec_module(mod)
            return mod
    return None


class RfsServer:
    """Serves CP NV requests with s22-modem.py's own handlers (private EFS copy only)."""

    def __init__(self, mod, fd, nv, emit):
        self.m, self.rfs, self.nv, self.emit, self.rfs_buf = mod, fd, nv, emit, b""

    def feed(self, buf):
        self.m.Modem.on_rfs(self, buf)

    def handle_rfs(self, cmd, rid, body):
        self.m.Modem.handle_rfs(self, cmd, rid, body)

    def rfs_reply(self, cmd, rid, payload):
        self.m.Modem.rfs_reply(self, cmd, rid, payload)


# ---------------------------------------------------------------- link (I/O + request matching)
class LinkDown(Exception):
    pass


class Pending:
    def __init__(self, cmd, want_resp):
        self.cmd, self.want_resp = cmd, want_resp
        self.event = threading.Event()
        self.gen = None          # decoded GEN_PHONE_RES
        self.resp = None         # RESP frame

    @property
    def ok(self):
        if self.resp is not None:
            return True
        return bool(self.gen and self.gen["ok"])


class Link:
    """Owns the FMT (and optional RFS) fds; one reader thread; thread-safe send/request."""

    def __init__(self, opener, log, on_frame, on_state=None):
        self.opener, self.log, self.on_frame = opener, log, on_frame
        self.on_state = on_state or (lambda up: None)
        self.ipc = self.rfs_fd = None
        self.rfs = None
        self.dec = FrameDecoder()
        self.wlock = threading.Lock()
        self.plock = threading.Lock()
        self.pending = {}
        self.mseq = 0
        self.stop = threading.Event()
        self.wake_r, self.wake_w = os.pipe()
        self.thread = threading.Thread(target=self._run, name="phoned-io", daemon=True)

    @property
    def is_open(self):
        return self.ipc is not None

    def start(self):
        self.thread.start()

    def close(self):
        self.stop.set()
        try:
            os.write(self.wake_w, b"x")
        except OSError:
            pass
        self.thread.join(3)
        self._drop("shutdown")
        for fd in (self.wake_r, self.wake_w):
            try:
                os.close(fd)
            except OSError:
                pass

    def _drop(self, why):
        was = self.ipc is not None
        for fd in (self.ipc, self.rfs_fd):
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
        self.ipc = self.rfs_fd = self.rfs = None
        self.dec = FrameDecoder()
        with self.plock:
            for p in self.pending.values():
                p.event.set()
            self.pending.clear()
        if was:
            self.log.write({"event": "link_down", "why": why})
            self.on_state(False)

    def _open(self):
        try:
            self.ipc, self.rfs_fd, self.rfs = self.opener(self.log)
        except OSError as e:
            self.log.write({"event": "open_failed", "error": str(e)})
            return False
        self.log.write({"event": "link_up"})
        self.on_state(True)
        return True

    def _run(self):
        while not self.stop.is_set():
            if self.ipc is None and not self._open():
                self.stop.wait(10)
                continue
            fds = [self.ipc, self.wake_r] + ([self.rfs_fd] if self.rfs_fd is not None else [])
            try:
                r, _, _ = select.select(fds, [], [], 1.0)
            except (OSError, ValueError):
                self._drop("select")
                continue
            for fd in r:
                if fd == self.wake_r:
                    os.read(fd, 64)
                    continue
                try:
                    buf = os.read(fd, 65536)
                except BlockingIOError:
                    continue
                except OSError as e:
                    self._drop(f"read: {e}")
                    break
                if not buf:
                    if fd == self.ipc and self._is_socket():
                        self._drop("eof")
                        break
                    continue
                if fd == self.rfs_fd and self.rfs is not None:
                    try:
                        self.rfs.feed(buf)
                    except Exception as e:  # noqa: BLE001
                        self.log.write({"dir": "rfs", "error": str(e)})
                elif fd == self.ipc:
                    for fr in self.dec.feed(buf):
                        self._dispatch(fr)

    def _is_socket(self):
        try:
            return stat.S_ISSOCK(os.fstat(self.ipc).st_mode)
        except OSError:
            return True

    def _dispatch(self, fr):
        rec = describe(fr)
        rec["dir"] = "rx"
        self.log.write(rec)
        with self.plock:
            p = self.pending.get(fr.aseq) if fr.aseq else None
            if fr.cmd == GEN_PHONE_RES and p is not None and len(fr.data) >= 5:
                p.gen = dec_gen(fr.data)
                if not (p.want_resp and p.gen["ok"]):
                    p.event.set()
            elif fr.type == RESP:
                if p is None or p.cmd != fr.cmd:      # async RESP with aseq 0: oldest waiter
                    p = next((q for q in self.pending.values()
                              if q.cmd == fr.cmd and q.resp is None), None)
                if p is not None:
                    p.resp = fr
                    p.event.set()
        try:
            self.on_frame(fr)
        except Exception as e:  # noqa: BLE001 - a handler bug must not kill the reader
            self.log.write({"event": "handler_error", "frame": fr.name, "error": repr(e)})

    def send(self, cmd, typ, data=b"", redact=False, pending=None):
        if self.ipc is None:
            raise LinkDown("ipc not open")
        with self.plock:
            for _ in range(255):
                self.mseq = self.mseq % 0xFF + 1
                if self.mseq not in self.pending:
                    break
            mseq = self.mseq
            if pending is not None:
                self.pending[mseq] = pending
        frame = Frame(mseq, 0, cmd[0], cmd[1], typ, data).encode()
        self.log.write({"dir": "tx", "name": NAMES.get(cmd, f"{cmd[0]:02x}/{cmd[1]:02x}"),
                        "type": typ, "mseq": mseq,
                        "hex": frame[:HDR.size].hex() + ("<redacted>" if redact else frame[HDR.size:].hex())})
        with self.wlock:
            deadline = time.monotonic() + 3
            while True:
                try:
                    os.write(self.ipc, frame)
                    break
                except (BlockingIOError, InterruptedError):
                    if time.monotonic() > deadline:
                        self._forget(mseq)
                        raise LinkDown("write timeout")
                    time.sleep(0.05)
                except OSError as e:
                    self._forget(mseq)
                    raise LinkDown(str(e)) from e
        return mseq

    def _forget(self, mseq):
        with self.plock:
            self.pending.pop(mseq, None)

    def request(self, cmd, typ, data=b"", timeout=8.0, want_resp=None, redact=False):
        """Send and wait. Returns Pending (check .ok/.gen/.resp) or None on timeout."""
        if want_resp is None:
            want_resp = typ == GET
        p = Pending(cmd, want_resp)
        mseq = self.send(cmd, typ, data, redact=redact, pending=p)
        got = p.event.wait(timeout)
        self._forget(mseq)
        if not got and p.gen is None:
            return None
        return p


def device_opener(nv_root=None, serve_rfs=True):
    def opener(log):
        ipc = os.open("/dev/umts_ipc0", os.O_RDWR | os.O_NONBLOCK)
        try:
            rfs_fd = os.open("/dev/umts_rfs0", os.O_RDWR | os.O_NONBLOCK)
        except OSError:
            os.close(ipc)
            raise
        rfs = None
        if serve_rfs:
            mod = load_s22_modem()
            if mod is None:
                log.write({"event": "rfs_unavailable", "why": "s22-modem.py not found"})
            else:
                nv = mod.NvStore(nv_root) if nv_root else mod.NvStore()
                rfs = RfsServer(mod, rfs_fd, nv, lambda rec: log.write(dict(rec, dir="rfs")))
        return ipc, rfs_fd, rfs
    return opener


# ---------------------------------------------------------------- phone state machine
class Phone:
    def __init__(self, state_dir, simulate=False, max_sms_per_hour=10, max_dials_per_hour=5,
                 reassembly_timeout=600.0, modem_state_path=MODEM_STATE_PATH, radio_normal=False,
                 smsc=None, req_timeout=8.0):
        self.dir = Path(state_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.dir, 0o700)
        except OSError:
            pass
        self.simulate, self.radio_normal = simulate, radio_normal
        self.modem_state_path = modem_state_path
        self.req_timeout = req_timeout
        self.log = JsonlLog(str(self.dir / "ipc.jsonl"))
        self.events = EventBus()
        self.inbox = JsonlStore(str(self.dir / "inbox.jsonl"))
        self.outbox = JsonlStore(str(self.dir / "outbox.jsonl"))
        self.lock = threading.RLock()
        self.send_lock = threading.Lock()
        self.link = None
        self.sim, self.sim_raw = "UNKNOWN", None
        self.pin_attempted = False
        self.pin_auto_done = False
        self.pin_failures = 0
        self.registration, self.reg_by_domain = None, {}
        self.operator, self.signal = None, None
        self.sms_ready = False
        self.smsc_raw = encode_smsc(smsc) if smsc else None
        self.calls = collections.OrderedDict()
        self.pending_dial = None
        self.partials = {}
        self.sms_rate = RateLimit(max_sms_per_hour)
        self.dial_rate = RateLimit(max_dials_per_hour)
        self.reassembly_timeout = reassembly_timeout
        self.concat_ref = random.randrange(256)
        self.counter = 0
        self.work = queue.Queue()
        self.stopping = threading.Event()
        cutoff = time.time() - 3600
        for rec in self.outbox.values():
            if rec.get("status") in ("sent", "queued") and rec.get("ts", 0) > cutoff:
                self.sms_rate.take(rec["ts"])
        self.worker = threading.Thread(target=self._worker, name="phoned-worker", daemon=True)

    # -- plumbing
    def attach(self, link):
        self.link = link

    def start(self):
        self.worker.start()

    def stop(self):
        self.stopping.set()
        self.work.put(None)

    def defer(self, fn, *a):
        self.work.put((fn, a))

    def _worker(self):
        last_poll = 0.0
        while not self.stopping.is_set():
            try:
                item = self.work.get(timeout=1.0)
            except queue.Empty:
                item = ()
            if item is None:
                break
            if item:
                fn, a = item
                try:
                    fn(*a)
                except LinkDown:
                    pass
                except Exception as e:  # noqa: BLE001
                    self.log.write({"event": "worker_error", "fn": getattr(fn, "__name__", "?"), "error": repr(e)})
            if time.monotonic() - last_poll > 30:
                last_poll = time.monotonic()
                try:
                    self.poll()
                except LinkDown:
                    pass
                except Exception as e:  # noqa: BLE001
                    self.log.write({"event": "poll_error", "error": repr(e)})

    def _req(self, cmd, typ, data=b"", **kw):
        if self.link is None or not self.link.is_open:
            raise LinkDown("ipc not open")
        kw.setdefault("timeout", self.req_timeout)
        return self.link.request(cmd, typ, data, **kw)

    def new_id(self, prefix):
        with self.lock:
            self.counter += 1
            return f"{prefix}-{int(time.time() * 1000)}-{self.counter}"

    def modem_state(self):
        if self.simulate:
            return "ONLINE"
        try:
            return Path(self.modem_state_path).read_text().strip() or "UNKNOWN"
        except OSError:
            return "UNKNOWN"

    def on_link(self, up):
        self.events.emit("modem", ipc_open=up, modem_state=self.modem_state())
        if up:
            self.defer(self.startup)
        else:
            with self.lock:
                self.sms_ready = False

    def startup(self):
        if self.radio_normal:
            self._req(PWR_PHONE_STATE, EXEC, struct.pack("<H", 0x0202))
        self.poll()

    def poll(self):
        if self.link is None or not self.link.is_open:
            return
        if self.sim not in SIM_USABLE:
            self.query_sim()
        for dom in (2, 3):
            self._req(NET_REGIST, GET, bytes([0xFF, dom]))
        self._req(NET_CURRENT_PLMN, GET)
        self._req(DISP_RSSI_INFO, GET)
        if self.sim in SIM_USABLE and not self.sms_ready:
            self.sms_device_ready()
        self.expire_partials()

    def query_sim(self):
        self._req(SEC_PIN_STATUS, GET)

    def sms_device_ready(self):
        p = self._req(SMS_DEVICE_READY, SET)
        if p is not None and p.ok:
            with self.lock:
                self.sms_ready = True

    # -- frame handling (reader thread: never block here)
    def handle_frame(self, fr):
        info = describe(fr)
        dec = info.get("decoded")
        if dec is None:
            return
        h = {SEC_PIN_STATUS: self._on_sim, NET_REGIST: self._on_regist,
             NET_CURRENT_PLMN: self._on_plmn, NET_SERVING_NETWORK: self._on_plmn,
             DISP_RSSI_INFO: self._on_signal, DISP_ICON_INFO: self._on_icon,
             CALL_INCOMING: self._on_call_incoming, CALL_STATUS: self._on_call_status,
             CALL_LIST: self._on_call_list, SMS_INCOMING_MSG: self._on_sms_incoming,
             SMS_SVC_CENTER_ADDR: self._on_smsc}.get(fr.cmd)
        if fr.cmd == SMS_DEVICE_READY and fr.type in (NOTI, INDI):
            self.defer(self.sms_device_ready)
        if h is not None and fr.cmd != GEN_PHONE_RES:
            h(fr, dec)

    def _on_sim(self, fr, d):
        if fr.type not in (RESP, NOTI, INDI):
            return
        name = sim_name(d.get("status"), d.get("facility_lock"))
        with self.lock:
            changed = name != self.sim
            self.sim, self.sim_raw = name, fr.data[:4].hex()
        if changed:
            self.events.emit("sim", sim=name)
            if name in SIM_USABLE and not self.sms_ready:
                self.defer(self.sms_device_ready)
        if name == "LOCK_SC(PIN)" and d.get("facility_lock") in (None, 0, FAC_PIN1):
            self.defer(self.auto_pin)

    def _on_regist(self, fr, d):
        dom = str(d.get("domain", "default"))
        with self.lock:
            changed = d != self.reg_by_domain.get(dom)
            self.reg_by_domain[dom] = d
            self.registration = dict(d, by_domain=dict(self.reg_by_domain))
        if changed:
            self.events.emit("registration", registration=d)

    def _on_plmn(self, fr, d):
        if d.get("operator"):
            with self.lock:
                self.operator = d["operator"]

    def _on_signal(self, fr, d):
        with self.lock:
            self.signal = dict(self.signal or {}, **d)

    def _on_icon(self, fr, d):
        with self.lock:
            self.signal = dict(self.signal or {}, icon=d)

    def _on_smsc(self, fr, d):
        raw = d.get("smsc_raw")
        if raw:
            with self.lock:
                self.smsc_raw = bytes.fromhex(raw)

    # -- SIM PIN
    def _read_pin_file(self):
        path = self.dir / "sim-pin"
        try:
            st = os.stat(path, follow_symlinks=False)
        except FileNotFoundError:
            return None
        if not stat.S_ISREG(st.st_mode) or st.st_mode & 0o077 or st.st_uid != os.geteuid():
            self.log.write({"event": "pin_file_refused", "why": "must be a regular 0600 file owned by the daemon user"})
            return None
        pin = path.read_text().strip()
        return pin if re.fullmatch(r"[0-9]{4,8}", pin) else None

    def auto_pin(self):
        with self.lock:
            if self.pin_auto_done or self.pin_failures or self.sim != "LOCK_SC(PIN)":
                return
            self.pin_auto_done = True        # at most once per lifetime, success or not
        pin = self._read_pin_file()
        if pin is None:
            return
        res = self.enter_pin(pin, source="auto")
        self.log.write({"event": "pin_auto", "ok": res.get("ok")})

    def enter_pin(self, pin, source="api"):
        pin = str(pin or "")
        if not re.fullmatch(r"[0-9]{4,8}", pin):
            return {"ok": False, "error": "pin must be 4-8 digits"}
        with self.lock:
            if self.sim != "LOCK_SC(PIN)":
                return {"ok": False, "error": f"sim is {self.sim}, not waiting for PIN"}
            if self.pin_failures >= 2:
                return {"ok": False, "error": "refused: 2 failed PIN attempts this run (PUK protection)"}
            self.pin_attempted = True
        data = bytes([0x03, len(pin), 0]) + pin.encode().ljust(8, b"\0") + bytes(8)
        try:
            p = self._req(SEC_PIN_STATUS, SET, data, want_resp=False, redact=True, timeout=15)
        except LinkDown as e:
            return {"ok": False, "error": str(e)}
        ok = p is not None and p.ok
        if not ok:
            with self.lock:
                self.pin_failures += 1
        self.events.emit("sim", sim=self.sim, pin_result="ok" if ok else "failed", source=source)
        if ok:
            self.defer(self.query_sim)
            return {"ok": True}
        code = p.gen["code"] if p is not None and p.gen else None
        return {"ok": False, "error": "pin rejected" if code else "no reply from modem",
                **({"code": f"0x{code:04x}"} if code else {})}

    # -- safety gates
    def tx_enabled(self):
        return (self.dir / "tx_enabled").is_file()

    def allowlist(self):
        try:
            lines = (self.dir / "allowlist").read_text().splitlines()
        except OSError:
            return set()
        return {normalize_number(x.split("#", 1)[0]) for x in lines if x.split("#", 1)[0].strip()}

    def gate(self, number):
        """Safety gates for outbound actions. Returns (normalized, error-or-None)."""
        n = normalize_number(number)
        if not re.fullmatch(r"\+?[0-9]{1,20}", n):
            return n, "invalid number"
        if is_emergency(n):
            return n, "refused: emergency or short number"
        if not self.tx_enabled():
            return n, "refused: tx disabled (no tx_enabled file)"
        if n not in self.allowlist():
            return n, "refused: number not in allowlist"
        return n, None

    # -- SMS
    def send_sms(self, to, text):
        text = str(text or "")
        if not text:
            return 400, {"ok": False, "status": "refused", "error": "empty text", "id": None, "parts": 0}
        n, err = self.gate(to)
        mid = self.new_id("out")
        if err is None:
            try:
                with self.lock:
                    self.concat_ref = (self.concat_ref + 1) % 256
                    ref = self.concat_ref
                pdus = build_submit_pdus(n, text, ref=ref)
            except ValueError as e:
                err = str(e)
        if err is None and (self.link is None or not self.link.is_open):
            err = "modem not open"
        if err is None and self.sim not in SIM_USABLE:
            err = f"sim not ready ({self.sim})"
        if err is None and not self.sms_rate.take():
            err = "refused: rate limit"
        rec = {"id": mid, "to": n, "text": text, "ts": now(), "parts": 0, "status": "refused",
               "delivered": None, "mrs": []}
        if err is not None:
            rec["error"] = err
            self.outbox.put(rec)
            code = 429 if "rate" in err else 400 if "invalid" in err or "long" in err or "empty" in err else 403
            if err.startswith(("modem", "sim")):
                code = 503
            return code, {"ok": False, "id": mid, "parts": 0, "status": "refused", "error": err}
        rec["parts"] = len(pdus)
        with self.send_lock:
            status, err = self._send_parts(pdus, rec)
        rec["status"] = status
        if err:
            rec["error"] = err
        self.outbox.put(rec)
        if status in ("sent", "queued"):
            self.events.emit("sms_sent", id=mid, to=n, parts=len(pdus), status=status)
        body = {"ok": status in ("sent", "queued"), "id": mid, "parts": len(pdus),
                "status": status if status in ("sent", "queued") else "refused"}
        if err:
            body["error"] = err
        return (200 if body["ok"] else 502), body

    def _send_parts(self, pdus, rec):
        if self.smsc_raw is None:
            try:
                self._req(SMS_SVC_CENTER_ADDR, GET)
            except LinkDown as e:
                return "failed", str(e)
        smsc = self.smsc_raw or b""
        for k, pdu in enumerate(pdus):
            body = bytes([len(smsc)]) + smsc + pdu
            hdr = bytes([0x02, 0x01 if k < len(pdus) - 1 else 0x02, 0x00, len(body)])
            try:
                p = self._req(SMS_SEND_MSG, EXEC, hdr + body, want_resp=True, timeout=60)
            except LinkDown as e:
                return ("queued" if k else "failed"), str(e)
            if p is None or (p.resp is None and p.gen is not None and p.gen["ok"]):
                return "queued", "no send confirmation yet"
            if p.resp is None:
                return "failed", f"modem error 0x{p.gen['code']:04x}"
            r = dec_sms_send(p.resp.data) if len(p.resp.data) >= 4 else {"ok": True, "id": None}
            if not r["ok"]:
                return "failed", f"network ack 0x{r['ack']:04x}"
            rec["mrs"].append(r["id"])
        return "sent", None

    def _on_sms_incoming(self, fr, d):
        if fr.type not in (NOTI, INDI):
            return
        ack = struct.pack("<BHBB", d.get("type", 1) or 1, 0, d.get("id", 0), 0)
        self.defer(self._ack_sms, ack)                   # ack every PDU so the network stops redelivering
        sms = d.get("sms")
        if sms is None:
            self.log.write({"event": "sms_undecodable", "pdu": d.get("pdu")})
            return
        if sms["kind"] == "status_report":
            self._on_status_report(sms)
        elif sms["kind"] == "deliver":
            self._on_deliver(sms)

    def _ack_sms(self, data):
        self._req(SMS_DELIVER_REPORT, EXEC, data, want_resp=False)

    def _on_deliver(self, sms):
        c = sms.get("concat")
        if not c or c["total"] <= 1:
            self._store_inbox(sms["sender"], sms["text"], 1, sms)
            return
        key = (sms["sender"], c["ref"], c["total"])
        with self.lock:
            entry = self.partials.setdefault(key, {"parts": {}, "first": time.time(), "sms": sms})
            entry["parts"][c["seq"]] = sms["text"]
            done = len(entry["parts"]) >= c["total"]
            if done:
                del self.partials[key]
        if done:
            text = "".join(entry["parts"][k] for k in sorted(entry["parts"]))
            self._store_inbox(sms["sender"], text, c["total"], entry["sms"])

    def expire_partials(self):
        cutoff = time.time() - self.reassembly_timeout
        with self.lock:
            old = [(k, v) for k, v in self.partials.items() if v["first"] < cutoff]
            for k, _ in old:
                del self.partials[k]
        for (sender, _ref, total), v in old:
            text = "".join(v["parts"].get(i, f"[missing part {i}]") for i in range(1, total + 1))
            self._store_inbox(sender, text, total, v["sms"], incomplete=True)

    def _store_inbox(self, sender, text, parts, sms, incomplete=False):
        rec = {"id": self.new_id("in"), "from": sender, "text": text, "ts": now(), "parts": parts,
               "smsc": sms.get("smsc"), "scts": sms.get("scts")}
        if incomplete:
            rec["incomplete"] = True
        self.inbox.put(rec)
        self.events.emit("sms_received", **{k: rec[k] for k in ("id", "from", "text", "parts")})

    def _on_status_report(self, sr):
        for rec in reversed(self.outbox.values()):
            if sr["mr"] in rec.get("mrs", []) and rec.get("status") in ("sent", "queued"):
                rec = dict(rec)
                rep = dict(rec.get("reports", {}))
                rep[str(sr["mr"])] = sr["delivered"]
                rec["reports"] = rep
                vals = [rep.get(str(m)) for m in rec["mrs"]]
                if any(v is False for v in vals):
                    rec["delivered"] = False
                elif len(rec["mrs"]) == rec["parts"] and all(v is True for v in vals):
                    rec["delivered"] = True
                self.outbox.put(rec)
                if rec["delivered"] is not None:
                    self.events.emit("sms_delivered", id=rec["id"], to=rec["to"], delivered=rec["delivered"])
                return
        self.log.write({"event": "status_report_unmatched", "mr": sr["mr"]})

    # -- calls
    def _call(self, cid, **kw):
        with self.lock:
            c = self.calls.get(cid)
            if c is None:
                c = {"id": cid, "number": None, "direction": "in", "state": "incoming",
                     "started": None, "ts": now()}
                self.calls[cid] = c
            c.update(kw, ts=now())
            if c["state"] == "active" and c["started"] is None:
                c["started"] = now()
            while len(self.calls) > 50:
                self.calls.popitem(last=False)
            return dict(c)

    def _on_call_incoming(self, fr, d):
        cid = d.get("id")
        c = self._call(cid, direction="in", state="incoming")
        self.events.emit("call_incoming", call=c)
        self.defer(self.refresh_calls)

    def _on_call_status(self, fr, d):
        cid, st = d.get("id"), d.get("status")
        state = CALL_STATUS_STATE.get(st, st if isinstance(st, str) else f"0x{st:02x}" if isinstance(st, int) else "unknown")
        with self.lock:
            if cid not in self.calls and self.pending_dial is not None:
                pend = self.pending_dial
                self.pending_dial = None
                self.calls[cid] = dict(pend, id=cid)
        extra = {"end_cause": d.get("end_cause")} if state == "released" else {}
        c = self._call(cid, state=state, **extra)
        self.events.emit("call_state", call=c)
        if state in ("dialing", "active"):
            self.defer(self.refresh_calls)

    def _on_call_list(self, fr, d):
        if fr.type != RESP:
            return
        listed = {c["id"]: c for c in d.get("calls", []) if isinstance(c, dict)}
        changed = []
        for cid, c in listed.items():
            with self.lock:
                old = dict(self.calls.get(cid, {}))
            new = self._call(cid, number=c.get("number") or old.get("number"),
                             direction=c.get("direction", old.get("direction", "in")),
                             state=c.get("state", old.get("state")))
            if old.get("state") != new["state"] or old.get("number") != new["number"]:
                changed.append(new)
        with self.lock:
            gone = [cid for cid, c in self.calls.items() if cid not in listed and c["state"] != "released"
                    and c["state"] not in ("dialing",)]
        for cid in gone:
            changed.append(self._call(cid, state="released"))
        for c in changed:
            self.events.emit("call_state", call=c)

    def refresh_calls(self):
        self._req(CALL_LIST, GET)

    def current_calls(self, include_recent=False):
        with self.lock:
            out = [dict(c) for c in self.calls.values() if c["state"] != "released"]
            if include_recent:
                out += [dict(c) for c in list(self.calls.values())[-10:] if c["state"] == "released"]
            if self.pending_dial:
                out.append(dict(self.pending_dial))
        return out

    def dial(self, number):
        n, err = self.gate(number)
        if err is None and (self.link is None or not self.link.is_open):
            err = "modem not open"
        if err is None and not self.dial_rate.take():
            err = "refused: rate limit"
        if err is not None:
            code = 429 if "rate" in err else 400 if "invalid" in err else 503 if "modem" in err else 403
            return code, {"ok": False, "error": err}
        digits = n.encode()[:86]
        data = bytes([0, 0x01, 0x00, len(digits), 0x11 if n.startswith("+") else 0x00]) + digits.ljust(86, b"\0")
        with self.lock:
            self.pending_dial = {"id": None, "number": n, "direction": "out", "state": "dialing",
                                 "started": None, "ts": now()}
        try:
            p = self._req(CALL_OUTGOING, EXEC, data, want_resp=False, timeout=15)
        except LinkDown as e:
            p, err = None, str(e)
        if p is None or not p.ok:
            with self.lock:
                self.pending_dial = None
            if err is None:
                err = f"modem error 0x{p.gen['code']:04x}" if p is not None and p.gen else "no reply from modem"
            return 502, {"ok": False, "error": err}
        with self.lock:
            call = next((dict(c) for c in reversed(self.calls.values()) if c["number"] == n
                         and c["direction"] == "out" and c["state"] != "released"),
                        dict(self.pending_dial or {}, number=n))
        self.events.emit("call_state", call=call)
        return 200, {"ok": True, "call": call}

    def _simple(self, cmd, typ, data=b""):
        try:
            p = self._req(cmd, typ, data, want_resp=False)
        except LinkDown as e:
            return 503, {"ok": False, "error": str(e)}
        if p is None:
            return 504, {"ok": False, "error": "no reply from modem"}
        if not p.ok:
            return 502, {"ok": False, "error": f"modem error 0x{p.gen['code']:04x}"}
        return 200, {"ok": True}

    def answer(self):
        if not any(c["state"] in ("incoming", "waiting") for c in self.current_calls()):
            return 409, {"ok": False, "error": "no incoming call"}
        return self._simple(CALL_ANSWER, EXEC)

    def hangup(self, cid=None):
        # IPC_CALL_RELEASE carries no call id in libsamsung-ipc: it releases the current call.
        if not self.current_calls():
            return 409, {"ok": False, "error": "no call"}
        code, body = self._simple(CALL_RELEASE, EXEC)
        with self.lock:
            self.pending_dial = None
        return code, body

    def dtmf(self, digits):
        digits = str(digits or "")
        if not re.fullmatch(r"[0-9*#A-D]{1,32}", digits):
            return 400, {"ok": False, "error": "digits must match [0-9*#A-D]{1,32}"}
        if not any(c["state"] == "active" for c in self.current_calls()):
            return 409, {"ok": False, "error": "no active call"}
        data = bytes([len(digits)]) + b"".join(bytes([1, ord(ch)]) for ch in digits)
        return self._simple(CALL_BURST_DTMF, EXEC, data)

    # -- status
    def status(self):
        with self.lock:
            return {"ok": True, "modem_state": self.modem_state(),
                    "ipc_open": bool(self.link and self.link.is_open), "sim": self.sim,
                    "pin_attempted": self.pin_attempted, "registration": self.registration,
                    "operator": self.operator, "signal": self.signal, "sms_ready": self.sms_ready,
                    "tx_enabled": self.tx_enabled(), "calls": self.current_calls(),
                    "simulated": self.simulate}


# ---------------------------------------------------------------- simulated CP
class FakeCP:
    """Answers FMT frames like the CP does, over a socketpair. For --simulate and tests."""

    def __init__(self, sock, sim_status=0x00, pin="1234", smsc="+306970000000"):
        self.sock, self.dec = sock, FrameDecoder()
        self.sim_status, self.fac, self.pin, self.smsc = sim_status, (FAC_PIN1 if sim_status == 3 else 0), pin, smsc
        self.mseq, self.mr, self.next_call = 0, 0, 1
        self.calls = {}
        self.sent, self.acks, self.rx = [], [], []
        self.lock = threading.Lock()
        self.thread = threading.Thread(target=self._run, name="fake-cp", daemon=True)
        self.thread.start()

    def out(self, cmd, typ, data=b"", aseq=0):
        with self.lock:
            self.mseq = self.mseq % 0xFF + 1
            try:
                self.sock.sendall(Frame(self.mseq, aseq, cmd[0], cmd[1], typ, data).encode())
            except OSError:
                pass

    def gen(self, fr, code=GEN_OK):
        self.out(GEN_PHONE_RES, RESP, bytes([fr.group, fr.index, fr.type]) + struct.pack("<H", code), fr.mseq)

    def _run(self):
        while True:
            try:
                buf = self.sock.recv(65536)
            except OSError:
                return
            if not buf:
                return
            for fr in self.dec.feed(buf):
                self.rx.append(fr)
                try:
                    self.handle(fr)
                except Exception as e:  # noqa: BLE001
                    print(f"fake-cp error: {e!r}", file=sys.stderr)

    def sim_bytes(self):
        return bytes([self.sim_status, self.fac])

    def handle(self, fr):
        c, t = fr.cmd, fr.type
        if c == SEC_PIN_STATUS and t == GET:
            self.out(c, RESP, self.sim_bytes(), fr.mseq)
        elif c == SEC_PIN_STATUS and t == SET:
            n = fr.data[1]
            if fr.data[3:3 + n].decode() == self.pin:
                self.sim_status, self.fac = 0x00, 0
                self.gen(fr)
                self.out(c, NOTI, self.sim_bytes())
            else:
                self.gen(fr, 0x8010)
        elif c == NET_REGIST and t == GET:
            dom = fr.data[1] if len(fr.data) > 1 else 2
            j = {"act": "lte", "domain": "cs" if dom == 2 else "ps", "reg_status": "home",
                 "tac": 3062, "pci": 334, "cid": 12345, "band": 20}
            self.out(c, RESP, json.dumps(j).encode() + b"\0", fr.mseq)
        elif c == NET_CURRENT_PLMN and t == GET:
            self.out(c, RESP, json.dumps({"plmn": "20201", "short_name": "SIMNET"}).encode(), fr.mseq)
        elif c == DISP_RSSI_INFO and t == GET:
            self.out(c, RESP, json.dumps({"lte_rsrp": 95, "lte_rsrq": 7, "lte_rssnr": 300, "lte_sig_str": 4}).encode(), fr.mseq)
        elif c == SMS_SVC_CENTER_ADDR and t == GET:
            sc = encode_smsc(self.smsc)
            self.out(c, RESP, bytes([len(sc)]) + sc, fr.mseq)
        elif c == SMS_SEND_MSG:
            n = fr.data[4]
            pdu = fr.data[5 + n:]
            self.sent.append(pdu)
            self.mr = (self.mr + 1) % 256
            self.out(c, RESP, struct.pack("<BHBB", 2, 0, self.mr, 0), fr.mseq)
            sub = decode_pdu(pdu, False)
            if sub.get("srr"):
                mr = self.mr
                threading.Timer(0.05, lambda: self.out(SMS_INCOMING_MSG, NOTI, self._incoming(
                    build_status_report_pdu(sub["to"], mr, 0, self.smsc), 2))).start()
        elif c == SMS_DELIVER_REPORT:
            self.acks.append(fr.data)
            self.gen(fr)
        elif c == CALL_OUTGOING:
            n = fr.data[3]
            cid = self._new_call(fr.data[5:5 + n].decode(), 1, 3)
            self.gen(fr)
            self.out(CALL_STATUS, NOTI, bytes([0, 1, cid, 1, 0, 0]))
            self.calls[cid]["status"] = 4
            self.out(CALL_STATUS, NOTI, bytes([0, 1, cid, 5, 0, 0]))
        elif c == CALL_ANSWER:
            self.gen(fr)
            for cid, call in self.calls.items():
                if call["status"] == 5:
                    call["status"] = 1
                    self.out(CALL_STATUS, NOTI, bytes([0, 1, cid, 3, 0, 0]))
        elif c == CALL_RELEASE:
            self.gen(fr)
            for cid in list(self.calls):
                del self.calls[cid]
                self.out(CALL_STATUS, NOTI, bytes([0, 1, cid, 4, 0, 5]))
        elif c == CALL_LIST and t == GET:
            body = bytes([len(self.calls)])
            for cid, call in self.calls.items():
                num = call["number"].encode()
                body += bytes([0, 1, cid, call["term"], call["status"], 0, len(num), 0]) + num
            self.out(c, RESP, body, fr.mseq)
        elif t in (EXEC, SET):
            self.gen(fr)      # DTMF, DEVICE_READY, PWR, ... : generic success

    def _new_call(self, number, term, status):
        cid = self.next_call
        self.next_call += 1
        self.calls[cid] = {"number": number, "term": term, "status": status}
        return cid

    def _incoming(self, pdu, typ=1):
        return struct.pack("<BBHBB", 2, typ, 0xFFFF, random.randrange(1, 255), len(pdu)) + pdu

    def inject_sms(self, sender, text, concat_parts=None):
        if concat_parts:
            ref = random.randrange(256)
            chunks = [text[i:i + 150] for i in range(0, len(text), 150)]
            for k, ch in enumerate(chunks, 1):
                self.out(SMS_INCOMING_MSG, NOTI, self._incoming(
                    build_deliver_pdu(sender, ch, self.smsc, (ref, len(chunks), k))))
            return len(chunks)
        self.out(SMS_INCOMING_MSG, NOTI, self._incoming(build_deliver_pdu(sender, text, self.smsc)))
        return 1

    def inject_call(self, number):
        cid = self._new_call(number, 2, 5)
        self.out(CALL_INCOMING, NOTI, bytes([0, 1, cid, 0]))
        return cid


def sim_opener(fake_holder, **kw):
    def opener(log):
        a, b = socket.socketpair()
        fake_holder["cp"] = FakeCP(b, **kw)
        return a.detach(), None, None
    return opener


# ---------------------------------------------------------------- HTTP API
class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "s22-phoned/1"

    def log_message(self, fmt, *args):
        pass

    def address_string(self):
        return self.client_address[0] if isinstance(self.client_address, tuple) else "unix"

    def _send(self, code, body):
        raw = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > 65536:
            raise ValueError("body too large")
        raw = self.rfile.read(n) if n else b""
        if not raw.strip():
            return {}
        body = json.loads(raw)
        if not isinstance(body, dict):
            raise ValueError("body must be a JSON object")
        return body

    def do_GET(self):
        self._route("GET")

    def do_POST(self):
        self._route("POST")

    def _route(self, method):
        d = self.server.daemon_ref
        ph = d.phone
        u = urllib.parse.urlsplit(self.path)
        q = {k: v[-1] for k, v in urllib.parse.parse_qs(u.query).items()}
        try:
            body = self._body() if method == "POST" else {}
            limit = max(1, min(int(q.get("limit", 50)), 1000))
            route = (method, u.path.rstrip("/") or "/")
            if route == ("GET", "/status"):
                return self._send(200, ph.status())
            if route == ("GET", "/calls"):
                return self._send(200, {"ok": True, "calls": ph.current_calls(include_recent=True)})
            if route == ("GET", "/sms/inbox"):
                msgs = ph.inbox.values()
                since = q.get("since")
                if since:
                    ids = [m["id"] for m in msgs]
                    if since in ids:
                        msgs = msgs[ids.index(since) + 1:]
                    else:
                        msgs = [m for m in msgs if m.get("ts", 0) > float(since)]
                keys = ("id", "from", "text", "ts", "parts", "smsc")
                return self._send(200, {"ok": True, "messages": [{k: m.get(k) for k in keys} for m in msgs[-limit:]]})
            if route == ("GET", "/sms/outbox"):
                keys = ("id", "to", "text", "ts", "parts", "status", "delivered")
                msgs = ph.outbox.values()[-limit:]
                return self._send(200, {"ok": True, "messages": [{k: m.get(k) for k in keys} for m in msgs]})
            if route == ("GET", "/events"):
                seq, evs = ph.events.since(int(q.get("since", 0)), float(q.get("timeout", 0)))
                return self._send(200, {"ok": True, "seq": seq, "events": evs})
            if route == ("POST", "/sim/pin"):
                res = ph.enter_pin(body.get("pin"))
                return self._send(200 if res["ok"] else 400 if "digits" in res.get("error", "") else 409, res)
            if route == ("POST", "/sms/send"):
                return self._send(*ph.send_sms(body.get("to"), body.get("text")))
            if route == ("POST", "/call/dial"):
                return self._send(*ph.dial(body.get("number")))
            if route == ("POST", "/call/answer"):
                return self._send(*ph.answer())
            if route == ("POST", "/call/hangup"):
                return self._send(*ph.hangup(body.get("id")))
            if route == ("POST", "/call/dtmf"):
                return self._send(*ph.dtmf(body.get("digits")))
            if method == "POST" and u.path.startswith("/sim-inject/") and d.simulate:
                cp = d.fake.get("cp")
                if cp is None:
                    return self._send(503, {"ok": False, "error": "simulator not running"})
                if u.path == "/sim-inject/sms":
                    parts = cp.inject_sms(str(body.get("from", "+306900000001")), str(body.get("text", "")),
                                          concat_parts=len(str(body.get("text", ""))) > 150 or None)
                    return self._send(200, {"ok": True, "parts": parts})
                if u.path == "/sim-inject/call":
                    return self._send(200, {"ok": True, "id": cp.inject_call(str(body.get("number", "+306900000001")))})
            return self._send(404, {"ok": False, "error": f"no route {method} {u.path}"})
        except (ValueError, TypeError) as e:
            return self._send(400, {"ok": False, "error": f"bad request: {e}"})
        except Exception as e:  # noqa: BLE001
            return self._send(500, {"ok": False, "error": f"internal error: {type(e).__name__}: {e}"})


class TcpServer(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class UnixServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


class Daemon:
    def __init__(self, state_dir=STATE_DIR, simulate=False, socket_path="/run/s22-phoned.sock",
                 host="127.0.0.1", port=8095, sim_kwargs=None, opener=None, **phone_kw):
        self.simulate = simulate
        self.phone = Phone(state_dir, simulate=simulate, **phone_kw)
        self.fake = {}
        if opener is None:
            opener = sim_opener(self.fake, **(sim_kwargs or {})) if simulate else device_opener()
        self.link = Link(opener, self.phone.log, self.phone.handle_frame, self.phone.on_link)
        self.phone.attach(self.link)
        self.servers = []
        if port is not None:
            self.servers.append(TcpServer((host, port), Handler))
        if socket_path:
            try:
                if stat.S_ISSOCK(os.lstat(socket_path).st_mode):
                    os.unlink(socket_path)
            except FileNotFoundError:
                pass
            srv = UnixServer(socket_path, Handler)
            os.chmod(socket_path, 0o600)
            self.servers.append(srv)
        self.socket_path = socket_path
        for s in self.servers:
            s.daemon_ref = self

    @property
    def port(self):
        return next((s.server_address[1] for s in self.servers if isinstance(s, TcpServer)), None)

    def start(self):
        self.phone.start()
        self.link.start()
        for s in self.servers:
            threading.Thread(target=s.serve_forever, kwargs={"poll_interval": 0.2}, daemon=True).start()
        return self

    def stop(self):
        for s in self.servers:
            s.shutdown()
            s.server_close()
        if self.socket_path:
            try:
                os.unlink(self.socket_path)
            except OSError:
                pass
        self.phone.stop()
        self.link.close()


# ---------------------------------------------------------------- CLI
class UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, path, timeout=10):
        super().__init__("localhost", timeout=timeout)
        self.sock_path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.sock_path)


def cli_get(path, sock_path, port):
    conns = []
    if sock_path and os.path.exists(sock_path):
        conns.append(lambda: UnixHTTPConnection(sock_path))
    conns.append(lambda: http.client.HTTPConnection("127.0.0.1", port, timeout=10))
    err = None
    for mk in conns:
        try:
            c = mk()
            c.request("GET", path)
            return json.loads(c.getresponse().read())
        except OSError as e:
            err = e
    raise SystemExit(f"s22-phoned not reachable: {err}")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="s22-phoned", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("serve")
    sp.add_argument("--simulate", action="store_true")
    sp.add_argument("--socket", default="/run/s22-phoned.sock", help="'' disables the unix socket")
    sp.add_argument("--host", default="127.0.0.1")
    sp.add_argument("--port", type=int, default=8095, help="0 = ephemeral, -1 = no TCP")
    sp.add_argument("--state-dir", default=STATE_DIR)
    sp.add_argument("--radio-normal", action="store_true", help="send PWR_PHONE_STATE normal at startup")
    sp.add_argument("--no-rfs", action="store_true", help="open umts_rfs0 but do not serve NV")
    sp.add_argument("--smsc", help="override SMSC (E.164); default: ask the CP")
    sp.add_argument("--max-sms-per-hour", type=int, default=10)
    sp.add_argument("--max-dials-per-hour", type=int, default=5)
    sp.add_argument("--sim-locked", action="store_true", help="simulate: SIM starts PIN-locked (PIN 1234)")
    st = sub.add_parser("status")
    st.add_argument("--socket", default="/run/s22-phoned.sock")
    st.add_argument("--port", type=int, default=8095)
    dc = sub.add_parser("send-test-frame-decode")
    dc.add_argument("hex")
    a = ap.parse_args(argv)

    if a.cmd == "send-test-frame-decode":
        frames = FrameDecoder().feed(bytes.fromhex(re.sub(r"\s", "", a.hex)))
        if not frames:
            print(json.dumps({"error": "no complete frame"}))
            return 1
        for fr in frames:
            print(json.dumps(describe(fr), ensure_ascii=False))
        return 0
    if a.cmd == "status":
        print(json.dumps(cli_get("/status", a.socket, a.port), indent=2, ensure_ascii=False))
        return 0
    os.umask(0o077)                     # inbox/outbox/ipc logs are private
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    opener = None if a.simulate else device_opener(serve_rfs=not a.no_rfs)
    d = Daemon(state_dir=a.state_dir, simulate=a.simulate, socket_path=a.socket or None,
               host=a.host, port=None if a.port < 0 else a.port, opener=opener,
               sim_kwargs={"sim_status": 3} if a.sim_locked else None,
               radio_normal=a.radio_normal, smsc=a.smsc,
               max_sms_per_hour=a.max_sms_per_hour, max_dials_per_hour=a.max_dials_per_hour)
    d.start()
    print(json.dumps({"event": "serving", "port": d.port, "socket": a.socket or None,
                      "simulate": a.simulate}), flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
    finally:
        d.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
