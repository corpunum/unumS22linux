#!/usr/bin/env python3
"""Host tests for tools/hardware/phoned/s22-phoned.py (synthetic frames only, no /dev access)."""
import contextlib
import http.client
import io
import importlib.util
import json
import os
import struct
import sys
import tempfile
import time
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parent / "s22-phoned.py"
spec = importlib.util.spec_from_file_location("s22_phoned", SRC)
P = importlib.util.module_from_spec(spec)
sys.modules["s22_phoned"] = P
spec.loader.exec_module(P)

HELLO = "07917283010010F5040BC87238880900F10000993092516195800AE8329BFD4697D9EC37"


def wait_for(fn, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(0.02)
    return fn()


class FramingTest(unittest.TestCase):
    def test_roundtrip_multiple_and_partial(self):
        a = P.Frame(1, 0, 0x05, 0x01, P.GET).encode()
        b = P.Frame(2, 1, 0x08, 0x05, P.RESP, b'{"act":"lte"}\0').encode()
        self.assertEqual(a, bytes.fromhex("07000100050102"))
        dec = P.FrameDecoder()
        stream = a + b + a
        got = []
        for k in range(0, len(stream), 3):          # 3-byte dribble
            got += dec.feed(stream[k:k + 3])
        self.assertEqual([f.cmd for f in got], [(5, 1), (8, 5), (5, 1)])
        self.assertEqual(got[1].data, b'{"act":"lte"}\0')
        self.assertEqual((got[1].mseq, got[1].aseq, got[1].type), (2, 1, P.RESP))

    def test_garbage_resync_and_describe_never_raises(self):
        dec = P.FrameDecoder()
        self.assertEqual(dec.feed(b"\x02" + bytes(9)), [])
        self.assertEqual(dec.errors, 1)
        frames = dec.feed(P.Frame(3, 0, 0x80, 1, P.RESP, b"\x05\x01\x03\x00\x80").encode())
        self.assertEqual(frames[-1].cmd, P.GEN_PHONE_RES)
        d = P.describe(frames[-1])["decoded"]
        self.assertTrue(d["ok"])
        self.assertEqual(d["for"], "SEC_SIM_STATUS")
        for cmd in P.DECODERS:                       # truncated payloads must not raise
            out = P.describe(P.Frame(1, 0, cmd[0], cmd[1], P.NOTI, b"\x01"))
            self.assertIn("name", out)

    def test_json_payloads(self):
        fr = P.Frame(1, 0, 8, 5, P.RESP, b'{"act":"lte","reg_status":"denied","tac":3062}\0')
        self.assertEqual(P.describe(fr)["decoded"]["reg_status"], "denied")
        fr = P.Frame(1, 0, 7, 6, P.NOTI, b'{"lte_rsrp":95,"lte_rsrq":7,"lte_rssnr":300}')
        self.assertEqual(P.describe(fr)["decoded"]["lte_rsrp_dbm"], -95)
        fr = P.Frame(1, 0, 5, 1, P.NOTI, b"\x80\x00")
        self.assertEqual(P.sim_name(**{"status": 0x80, "fac": 0}), "CARD_NOT_PRESENT")
        self.assertEqual(P.describe(fr)["decoded"]["status"], 0x80)
        self.assertEqual(P.sim_name(3, 2), "LOCK_SC(PUK)")

    def test_cli_decode(self):
        hexs = P.Frame(9, 0, 5, 1, P.RESP, b"\x80\x00").encode().hex()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(P.main(["send-test-frame-decode", hexs]), 0)
        self.assertEqual(json.loads(buf.getvalue())["decoded"]["status"], 0x80)


class RfsReuseTest(unittest.TestCase):
    def test_serves_nv_read_with_s22_modem_code(self):
        mod = P.load_s22_modem()
        self.assertIsNotNone(mod)
        with tempfile.TemporaryDirectory() as tmp:
            nv = object.__new__(mod.NvStore)
            nv.path = os.path.join(tmp, "nv_data.bin")
            nv.md5 = nv.path + ".md5"
            Path(nv.path).write_bytes(b"\xAB" * 16 + bytes(mod.NV_SIZE - 16))
            r, w = os.pipe()
            logged = []
            srv = P.RfsServer(mod, w, nv, logged.append)
            frame = struct.pack("<IBBII", 14, mod.RFS_NV_READ, 5, 0, 4)
            srv.feed(frame[:5])
            srv.feed(frame[5:])
            self.assertEqual(os.read(r, 64), struct.pack("<IBBBII", 19, 1, 5, 1, 0, 4) + b"\xAB" * 4)
            self.assertEqual(logged[-1]["op"], "nv_read")
            os.close(r)
            os.close(w)


class PduTest(unittest.TestCase):
    def test_hellohello_deliver(self):
        d = P.decode_pdu(bytes.fromhex(HELLO))
        self.assertEqual(d["kind"], "deliver")
        self.assertEqual(d["smsc"], "+27381000015")
        self.assertEqual(d["sender"], "27838890001")
        self.assertEqual(d["text"], "hellohello")
        self.assertEqual(d["scts"], "1999-03-29T15:16:59+02:00")

    def test_pack_known(self):
        self.assertEqual(P.pack7(P.gsm7_septets("hellohello")).hex().upper(), "E8329BFD4697D9EC37")
        self.assertEqual(P.pack7(P.gsm7_septets("€")).hex(), "9b32")

    def test_extension_chars(self):
        text = "Price: 5€ [ok] {x} ~^|\\"
        pdu = P.build_submit_pdus("+306912345678", text, srr=False)[0]
        d = P.decode_pdu(pdu, has_smsc=False)
        self.assertEqual(d["text"], text)
        self.assertEqual(d["dcs"], 0)
        self.assertFalse(d["srr"])
        septs = P.gsm7_septets(text)
        self.assertEqual(pdu[pdu.index(bytes([0x00, 0x00, 0xA7])) + 3], len(septs))   # UDL counts escapes
        self.assertEqual(len(septs), len(text) + 9)

    def test_ucs2_greek(self):
        pdu = P.build_submit_pdus("6912345678", "Γειά σου", srr=True)[0]
        self.assertEqual(pdu[0] & 0x20, 0x20)
        self.assertEqual(pdu[2:4], bytes([10, 0x81]))          # national number
        self.assertTrue(pdu.hex().endswith("10" + "039303b503b903ac002003c303bf03c5"))
        d = P.decode_pdu(pdu, has_smsc=False)
        self.assertEqual((d["text"], d["dcs"], d["to"]), ("Γειά σου", 8, "6912345678"))
        self.assertEqual(P.build_submit_pdus("+306912345678", "x")[0][2:5].hex(), "0c9103")

    def test_concat_three_parts(self):
        text = "".join(chr(ord("a") + k % 26) for k in range(400))
        pdus = P.build_submit_pdus("+306912345678", text, ref=0x42)
        self.assertEqual(len(pdus), 3)
        parts = [P.decode_pdu(p, has_smsc=False) for p in pdus]
        self.assertEqual([p["concat"] for p in parts],
                         [{"ref": 0x42, "total": 3, "seq": k} for k in (1, 2, 3)])
        self.assertEqual([len(p["text"]) for p in parts], [153, 153, 94])
        self.assertEqual("".join(p["text"] for p in parts), text)
        u = P.build_submit_pdus("+306912345678", "λ" * 150)
        self.assertEqual([len(P.decode_pdu(p, False)["text"]) for p in u], [67, 67, 16])
        self.assertEqual(len(P.build_submit_pdus("+306912345678", "x" * 160)), 1)
        with self.assertRaises(ValueError):
            P.build_submit_pdus("+306912345678", "x" * 2000)

    def test_deliver_16bit_ref_and_status_report(self):
        pdu = P.build_deliver_pdu("+306900000001", "part two", "+306970000000", (0x1234, 2, 2))
        d = P.decode_pdu(pdu)
        self.assertEqual(d["concat"], {"ref": 0x1234, "total": 2, "seq": 2})
        self.assertEqual((d["text"], d["sender"], d["smsc"]), ("part two", "+306900000001", "+306970000000"))
        sr = P.decode_pdu(P.build_status_report_pdu("+306912345678", 7, 0))
        self.assertEqual((sr["kind"], sr["mr"], sr["delivered"]), ("status_report", 7, True))
        self.assertIsNone(P.decode_pdu(P.build_status_report_pdu("+3069", 7, 0x21))["delivered"])
        self.assertFalse(P.decode_pdu(P.build_status_report_pdu("+3069", 7, 0x41))["delivered"])


class FakeLink:
    """Records sends; request() answers from a table (cmd -> Pending factory)."""

    def __init__(self, answers=None):
        self.sent, self.answers, self.is_open = [], answers or {}, True

    def request(self, cmd, typ, data=b"", **kw):
        self.sent.append((cmd, typ, data))
        fn = self.answers.get(cmd)
        return fn(data) if fn else None


def gen_pending(ok):
    p = P.Pending((0, 0), False)
    p.gen = {"ok": ok, "code": 0x8000 if ok else 0x8010}
    return p


def mkphone(tmp, **kw):
    ph = P.Phone(tmp, **kw)
    ph.defer = lambda fn, *a: ph._deferred.append((fn, a))
    ph._deferred = []
    return ph


def run_deferred(ph):
    while ph._deferred:
        fn, a = ph._deferred.pop(0)
        try:
            fn(*a)
        except P.LinkDown:
            pass


class CallStateTest(unittest.TestCase):
    def test_incoming_answer_release(self):
        with tempfile.TemporaryDirectory() as tmp:
            ph = mkphone(tmp)
            ph.attach(FakeLink())
            ph.handle_frame(P.Frame(1, 0, 2, 2, P.NOTI, bytes([0, 1, 3, 0])))
            self.assertEqual(ph.current_calls()[0]["state"], "incoming")
            num = b"+306900000001"
            ph.handle_frame(P.Frame(2, 0, 2, 6, P.RESP, bytes([1, 0, 1, 3, 2, 5, 0, len(num), 0]) + num))
            c = ph.current_calls()[0]
            self.assertEqual((c["id"], c["number"], c["direction"]), (3, "+306900000001", "in"))
            ph.handle_frame(P.Frame(3, 0, 2, 5, P.NOTI, bytes([0, 1, 3, 3, 0, 0])))
            self.assertEqual(ph.current_calls()[0]["state"], "active")
            self.assertIsNotNone(ph.current_calls()[0]["started"])
            ph.handle_frame(P.Frame(4, 0, 2, 5, P.NOTI, bytes([0, 1, 3, 4, 0, 5])))
            self.assertEqual(ph.current_calls(), [])
            recent = ph.current_calls(include_recent=True)
            self.assertEqual((recent[0]["state"], recent[0]["end_cause"]), ("released", 5))
            types = [e["type"] for e in ph.events.since(0)[1]]
            self.assertEqual(types[0], "call_incoming")
            self.assertGreaterEqual(types.count("call_state"), 2)

    def test_outgoing_dialing_alerting(self):
        with tempfile.TemporaryDirectory() as tmp:
            ph = mkphone(tmp)
            ph.pending_dial = {"id": None, "number": "+306912345678", "direction": "out",
                               "state": "dialing", "started": None, "ts": 0}
            ph.handle_frame(P.Frame(1, 0, 2, 5, P.NOTI, bytes([0, 1, 1, 1, 0, 0])))
            c = ph.current_calls()[0]
            self.assertEqual((c["id"], c["number"], c["direction"], c["state"]), (1, "+306912345678", "out", "dialing"))
            ph.handle_frame(P.Frame(2, 0, 2, 5, P.NOTI, bytes([0, 1, 1, 5, 0, 0])))
            self.assertEqual(ph.current_calls()[0]["state"], "alerting")


class GateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ph = mkphone(self.tmp.name, max_sms_per_hour=2, max_dials_per_hour=1)
        self.ph.attach(FakeLink({P.CALL_OUTGOING: lambda d: gen_pending(True),
                                 P.SMS_SVC_CENTER_ADDR: lambda d: None}))
        self.ph.sim = "READY"
        self.ph.smsc_raw = P.encode_smsc("+306970000000")

    def tearDown(self):
        self.tmp.cleanup()

    def allow(self, *nums):
        Path(self.tmp.name, "allowlist").write_text("# owner\n" + "\n".join(nums) + "\n")

    def test_tx_disabled_then_allowlist(self):
        code, body = self.ph.send_sms("+306912345678", "hi")
        self.assertEqual((code, body["status"]), (403, "refused"))
        self.assertIn("tx disabled", body["error"])
        Path(self.tmp.name, "tx_enabled").touch()
        code, body = self.ph.send_sms("+306912345678", "hi")
        self.assertIn("allowlist", body["error"])
        code, body = self.ph.dial("+306912345678")
        self.assertEqual(code, 403)
        self.assertEqual(self.ph.link.sent, [])            # nothing was transmitted

    def test_emergency_always_refused(self):
        Path(self.tmp.name, "tx_enabled").touch()
        self.allow("112", "+30112", "911", "166")
        for n in ("112", "911", "166", "+30112", "999"):
            code, body = self.ph.dial(n)
            self.assertEqual(code, 403, n)
            self.assertIn("emergency", body["error"])
            self.assertIn("emergency", self.ph.send_sms(n, "x")[1]["error"])
        self.assertEqual(self.ph.link.sent, [])

    def test_rate_limit(self):
        Path(self.tmp.name, "tx_enabled").touch()
        self.allow("+306912345678")
        self.assertEqual(self.ph.dial("+306912345678")[0], 200)
        code, body = self.ph.dial("+306912345678")
        self.assertEqual(code, 429)
        sms_link = FakeLink({P.SMS_SEND_MSG: lambda d: self._sms_ok()})
        self.ph.attach(sms_link)
        self.assertTrue(self.ph.send_sms("+306912345678", "a")[1]["ok"])
        self.assertTrue(self.ph.send_sms("+306912345678", "b")[1]["ok"])
        code, body = self.ph.send_sms("+306912345678", "c")
        self.assertEqual((code, body["status"]), (429, "refused"))
        self.assertEqual(len(sms_link.sent), 2)
        ph2 = mkphone(self.tmp.name, max_sms_per_hour=2)      # window survives restart via outbox
        self.assertFalse(ph2.sms_rate.take())

    def _sms_ok(self):
        p = P.Pending(P.SMS_SEND_MSG, True)
        p.resp = P.Frame(1, 1, 4, 1, P.RESP, struct.pack("<BHBB", 2, 0, 9, 0))
        return p

    def test_send_frame_layout(self):
        Path(self.tmp.name, "tx_enabled").touch()
        self.allow("+306912345678")
        link = FakeLink({P.SMS_SEND_MSG: lambda d: self._sms_ok()})
        self.ph.attach(link)
        code, body = self.ph.send_sms("+30 691 234 5678", "x" * 200)
        self.assertEqual((code, body["parts"], body["status"]), (200, 2, "sent"))
        (c1, t1, d1), (c2, t2, d2) = link.sent
        self.assertEqual((c1, t1), (P.SMS_SEND_MSG, P.EXEC))
        self.assertEqual(d1[:3], bytes([2, 1, 0]))            # OUTGOING, MULTIPLE
        self.assertEqual(d2[:3], bytes([2, 2, 0]))            # last part: SINGLE
        smsc = self.ph.smsc_raw
        self.assertEqual(d1[3], len(d1) - 4)
        self.assertEqual(d1[4:5 + len(smsc)], bytes([len(smsc)]) + smsc)
        sub = P.decode_pdu(d1[5 + len(smsc):], has_smsc=False)
        self.assertEqual((sub["to"], sub["srr"], sub["concat"]["total"]), ("+306912345678", True, 2))
        out = self.ph.outbox.values()[-1]
        self.assertEqual((out["status"], out["mrs"]), ("sent", [9, 9]))

    def test_dial_and_dtmf_frames(self):
        Path(self.tmp.name, "tx_enabled").touch()
        self.allow("+306912345678")
        self.assertEqual(self.ph.dial("+306912345678")[0], 200)
        cmd, typ, data = self.ph.link.sent[-1]
        self.assertEqual((cmd, typ, len(data)), (P.CALL_OUTGOING, P.EXEC, 91))
        self.assertEqual(data[:5], bytes([0, 1, 0, 13, 0x11]))
        self.assertEqual(data[5:18], b"+306912345678")
        self.assertEqual(self.ph.dtmf("12#")[0], 409)          # no active call
        self.ph.handle_frame(P.Frame(1, 0, 2, 5, P.NOTI, bytes([0, 1, 1, 3, 0, 0])))
        self.ph.link.answers[P.CALL_BURST_DTMF] = lambda d: gen_pending(True)
        self.assertEqual(self.ph.dtmf("12#")[0], 200)
        self.assertEqual(self.ph.link.sent[-1][2], bytes([3, 1, ord("1"), 1, ord("2"), 1, ord("#")]))
        self.assertEqual(self.ph.dtmf("12x")[0], 400)


class PinTest(unittest.TestCase):
    def test_auto_pin_once_and_never_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            ph = mkphone(tmp)
            link = FakeLink({P.SEC_PIN_STATUS: lambda d: gen_pending(False)})
            ph.attach(link)
            pf = Path(tmp, "sim-pin")
            pf.write_text("4321\n")
            os.chmod(pf, 0o600)
            locked = P.Frame(1, 0, 5, 1, P.NOTI, bytes([3, 1]))
            ph.handle_frame(locked)
            run_deferred(ph)
            pin_sets = [s for s in link.sent if s[:2] == (P.SEC_PIN_STATUS, P.SET)]
            self.assertEqual(len(pin_sets), 1)
            self.assertEqual(pin_sets[0][2][:7], bytes([3, 4, 0]) + b"4321")
            self.assertEqual(len(pin_sets[0][2]), 19)
            self.assertTrue(ph.pin_attempted)
            for _ in range(3):                                   # CP re-reports the lock
                ph.handle_frame(locked)
                run_deferred(ph)
            self.assertEqual(len([s for s in link.sent if s[1] == P.SET]), 1)
            self.assertFalse(ph.enter_pin("1111")["ok"])        # 2nd failure (manual)
            res = ph.enter_pin("2222")
            self.assertIn("PUK", res["error"])                   # 3rd attempt refused
            self.assertEqual(len([s for s in link.sent if s[1] == P.SET]), 2)

    def test_puk_and_insecure_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            ph = mkphone(tmp)
            link = FakeLink({P.SEC_PIN_STATUS: lambda d: gen_pending(True)})
            ph.attach(link)
            pf = Path(tmp, "sim-pin")
            pf.write_text("4321")
            os.chmod(pf, 0o644)
            ph.handle_frame(P.Frame(1, 0, 5, 1, P.NOTI, bytes([3, 1])))
            run_deferred(ph)
            self.assertEqual(link.sent, [])                      # world-readable file ignored
            ph2 = mkphone(tmp)
            ph2.attach(link)
            os.chmod(pf, 0o600)
            ph2.handle_frame(P.Frame(1, 0, 5, 1, P.NOTI, bytes([3, 2])))   # PUK required
            run_deferred(ph2)
            self.assertEqual(ph2.sim, "LOCK_SC(PUK)")
            self.assertEqual(link.sent, [])
            self.assertFalse(ph2.enter_pin("1234")["ok"])


class E2ETest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.state = os.path.join(cls.tmp.name, "state")
        cls.sock = os.path.join(cls.tmp.name, "phoned.sock")
        if len(cls.sock) > 100:                     # AF_UNIX path limit (108) under long TMPDIRs
            cls.sockdir = tempfile.TemporaryDirectory(dir="/tmp", prefix="phd-")
            cls.sock = os.path.join(cls.sockdir.name, "p.sock")
        cls.d = P.Daemon(state_dir=cls.state, simulate=True, socket_path=cls.sock, port=0,
                         sim_kwargs={"sim_status": 3, "pin": "2468"}, req_timeout=3).start()

    @classmethod
    def tearDownClass(cls):
        cls.d.stop()
        cls.tmp.cleanup()
        if hasattr(cls, "sockdir"):
            cls.sockdir.cleanup()

    def call(self, method, path, body=None, unix=False):
        c = (P.UnixHTTPConnection(self.sock) if unix
             else http.client.HTTPConnection("127.0.0.1", self.d.port, timeout=15))
        c.request(method, path, json.dumps(body) if body is not None else None,
                  {"Content-Type": "application/json"})
        r = c.getresponse()
        out = json.loads(r.read())
        c.close()
        return r.status, out

    def test_flow(self):
        st = wait_for(lambda: self.call("GET", "/status", unix=True)[1]["sim"] == "LOCK_SC(PIN)"
                      and self.call("GET", "/status")[1])
        st = wait_for(lambda: self.call("GET", "/status")[1]["operator"] and self.call("GET", "/status")[1]) or st
        self.assertTrue(st["simulated"] and st["ipc_open"])
        self.assertEqual(st["modem_state"], "ONLINE")
        self.assertEqual(st["registration"]["reg_status"], "home")
        self.assertEqual(st["operator"], "SIMNET")
        self.assertEqual(st["signal"]["lte_rsrp_dbm"], -95)
        self.assertFalse(st["tx_enabled"])
        # PIN via API (wrong, then right)
        self.assertEqual(self.call("POST", "/sim/pin", {"pin": "0000"})[1]["ok"], False)
        self.assertEqual(self.call("POST", "/sim/pin", {"pin": "2468"})[1], {"ok": True})
        st = wait_for(lambda: self.call("GET", "/status")[1]["sms_ready"] and self.call("GET", "/status")[1])
        self.assertEqual(st["sim"], "READY")
        self.assertTrue(st["pin_attempted"])
        log = Path(self.state, "ipc.jsonl").read_text()
        self.assertNotIn("2468", log)
        self.assertNotIn(b"2468".hex(), log)
        # outbound gated
        code, body = self.call("POST", "/sms/send", {"to": "+306912345678", "text": "hi"})
        self.assertEqual((code, body["status"]), (403, "refused"))
        Path(self.state, "tx_enabled").touch()
        Path(self.state, "allowlist").write_text("+306912345678\n")
        seq0 = self.call("GET", "/events")[1]["seq"]
        code, body = self.call("POST", "/sms/send", {"to": "+306912345678", "text": "Γειά! " + "x" * 100})
        self.assertEqual((code, body["ok"], body["status"], body["parts"]), (200, True, "sent", 2))
        out = wait_for(lambda: [m for m in self.call("GET", "/sms/outbox?limit=5")[1]["messages"]
                                if m["id"] == body["id"] and m["delivered"]])
        self.assertEqual(out[0]["status"], "sent")
        # inbound SMS (single + concatenated)
        self.call("POST", "/sim-inject/sms", {"from": "+306900000001", "text": "hello €"})
        long = "".join(chr(ord("A") + k % 26) for k in range(320))
        self.assertEqual(self.call("POST", "/sim-inject/sms", {"from": "+306900000002", "text": long})[1]["parts"], 3)
        msgs = wait_for(lambda: len(self.call("GET", "/sms/inbox")[1]["messages"]) >= 2
                        and self.call("GET", "/sms/inbox")[1]["messages"])
        self.assertEqual([(m["from"], m["text"], m["parts"]) for m in msgs],
                         [("+306900000001", "hello €", 1), ("+306900000002", long, 3)])
        self.assertEqual(msgs[0]["smsc"], "+306970000000")
        later = self.call("GET", f"/sms/inbox?since={msgs[0]['id']}")[1]["messages"]
        self.assertEqual([m["id"] for m in later], [msgs[1]["id"]])
        self.assertEqual(len(wait_for(lambda: len(self.d.fake["cp"].acks) >= 6 and self.d.fake["cp"].acks)), 6)
        # incoming call: answer, dtmf, hangup
        self.call("POST", "/sim-inject/call", {"number": "+306900000003"})
        calls = wait_for(lambda: [c for c in self.call("GET", "/calls")[1]["calls"]
                                  if c["number"] == "+306900000003" and c["state"] == "incoming"])
        self.assertEqual(calls[0]["direction"], "in")
        self.assertEqual(self.call("POST", "/call/answer", {})[1], {"ok": True})
        wait_for(lambda: any(c["state"] == "active" for c in self.call("GET", "/calls")[1]["calls"]))
        self.assertEqual(self.call("POST", "/call/dtmf", {"digits": "12#"})[1], {"ok": True})
        self.assertEqual(self.call("POST", "/call/hangup", {})[1], {"ok": True})
        wait_for(lambda: not self.call("GET", "/status")[1]["calls"])
        # outgoing call
        code, body = self.call("POST", "/call/dial", {"number": "+306912345678"})
        self.assertEqual((code, body["ok"], body["call"]["direction"]), (200, True, "out"))
        wait_for(lambda: any(c["state"] == "alerting" for c in self.call("GET", "/calls")[1]["calls"]))
        self.assertEqual(self.call("POST", "/call/dial", {"number": "112"})[0], 403)
        self.call("POST", "/call/hangup", {})
        # events
        ev = self.call("GET", f"/events?since={seq0}&timeout=1")[1]
        types = {e["type"] for e in ev["events"]}
        for t in ("sms_sent", "sms_delivered", "sms_received", "call_incoming", "call_state"):
            self.assertIn(t, types)
        self.assertTrue(all(e["seq"] > seq0 for e in ev["events"]))
        t0 = time.time()
        quiet = self.call("GET", f"/events?since={ev['seq'] + 100}&timeout=0.3")[1]
        self.assertEqual(quiet["events"], [])
        self.assertGreaterEqual(time.time() - t0, 0.25)
        # errors
        self.assertEqual(self.call("GET", "/nope")[0], 404)
        self.assertEqual(self.call("POST", "/sms/send", {"to": "+306912345678"})[0], 400)


REAL_PLMN = bytes.fromhex(
    "7b2233677070223a7b22616374223a226567707273222c2264617461223a7b22636964223a32363237382c226c6163223a323638322c22"
    "706369223a36353533352c22726163223a312c22746163223a343638322c2276696c74655f62617272696e675f66223a3235352c227669"
    "6c74655f62617272696e675f74223a302c22766f6c74655f62617272696e675f66223a3235352c22766f6c74655f62617272696e675f74"
    "223a307d2c226d6f6465223a226175746f222c22706c6d6e223a223230323031222c227265675f737461747573223a22686f6d655f3367"
    "7070227d7d")
REAL_STATUS_REPORT = bytes.fromhex(
    "0202ff0001210791039617520044060b0c91039647043137620101616500216201016165002100")


def nfr(cmd, typ, data):
    return P.Frame(1, 0, cmd[0], cmd[1], typ, data)


class MeasuredHardwareTest(unittest.TestCase):
    """Frames captured from the S22 on 2026-10-10 (raw hex from ipc.jsonl)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ph = mkphone(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_operator_decoded_from_nested_plmn_json(self):
        d = P.dec_plmn(REAL_PLMN)
        self.assertEqual((d["plmn"], d["operator"]), ("20201", "Cosmote"))
        self.assertEqual(P.dec_plmn(b'{"plmn":"99999#"}')["operator"], "99999")
        self.ph.handle_frame(nfr(P.NET_CURRENT_PLMN, P.RESP, REAL_PLMN))
        self.assertEqual(self.ph.operator, "Cosmote")

    def test_send_ack_is_a_noti_and_records_mr(self):
        Path(self.tmp.name, "tx_enabled").touch()
        Path(self.tmp.name, "allowlist").write_text("+306974401373\n")
        ph = self.ph
        link = FakeLink({P.SMS_SEND_MSG: lambda d: (
            ph.handle_frame(nfr(P.SMS_SEND_MSG, P.NOTI, bytes.fromhex("0200000b00"))), gen_pending(True))[1]})
        ph.attach(link)
        ph.sim, ph.smsc_raw = "PB_INIT_COMPLETE", P.encode_smsc("+3097100000")
        code, body = ph.send_sms("+306974401373", "hello")
        self.assertEqual((code, body["status"]), (200, "sent"))
        rec = ph.outbox.values()[-1]
        self.assertEqual(rec["mrs"], [11])
        ph.handle_frame(nfr(P.SMS_INCOMING_MSG, P.NOTI, REAL_STATUS_REPORT))
        self.assertIs(ph.outbox.values()[-1]["delivered"], True)

    def test_real_status_report_adopted_when_mr_unknown(self):
        ph = self.ph
        ph.outbox.put({"id": "out-1", "to": "+306974401373", "text": "x", "ts": time.time(), "parts": 1,
                       "status": "queued", "delivered": None, "mrs": [], "error": "no send confirmation yet"})
        ph.handle_frame(nfr(P.SMS_INCOMING_MSG, P.NOTI, REAL_STATUS_REPORT))
        rec = ph.outbox.values()[-1]
        self.assertEqual((rec["delivered"], rec["mrs"], rec["status"]), (True, [11], "sent"))
        self.assertNotIn("error", rec)
        run_deferred(ph)

    def test_status_report_for_other_recipient_not_adopted(self):
        ph = self.ph
        ph.outbox.put({"id": "out-1", "to": "+306900000009", "text": "x", "ts": time.time(), "parts": 1,
                       "status": "queued", "delivered": None, "mrs": []})
        ph.handle_frame(nfr(P.SMS_INCOMING_MSG, P.NOTI, REAL_STATUS_REPORT))
        self.assertIsNone(ph.outbox.values()[-1]["delivered"])

    def test_mem_status_noti_gets_available_report_once(self):
        link = FakeLink({P.SMS_MEM_STATUS: lambda d: gen_pending(True)})
        self.ph.attach(link)
        for _ in range(3):                              # the CP repeats it after each DEVICE_READY
            self.ph.handle_frame(nfr(P.SMS_MEM_STATUS, P.NOTI, b"\x02\x01"))
        run_deferred(self.ph)
        self.assertEqual(link.sent, [(P.SMS_MEM_STATUS, P.SET, b"\x02\x01")])


class SimStoreFake(FakeLink):
    """Answers the stored-SMS commands like the measured CP (40 slots, 5-byte read header)."""

    def __init__(self, slots=None, fail_del=False):
        super().__init__()
        self.slots, self.deleted, self.fail_del = dict(slots or {}), [], fail_del

    def request(self, cmd, typ, data=b"", **kw):
        self.sent.append((cmd, typ, data))
        p = P.Pending(cmd, True)
        if cmd == P.SMS_STORED_MSG_COUNT:
            p.resp = P.Frame(1, 0, 4, 9, P.RESP, bytes([2, 40]) + bytes(259))
        elif cmd == P.SMS_READ_MSG:
            idx = struct.unpack_from("<H", data, 1)[0]
            st, body = self.slots.get(idx, (0, b""))
            p.resp = P.Frame(1, 0, 4, 3, P.RESP, struct.pack("<BHBB", 2, idx, st, len(body)) + body)
        elif cmd == P.SMS_DEL_MSG:
            idx = struct.unpack_from("<H", data, 1)[0]
            if self.fail_del:
                p.gen = {"ok": False, "code": 0x8001}
            else:
                self.deleted.append(idx)
                self.slots.pop(idx, None)
                p.gen = {"ok": True, "code": 0x8000}
        return p


class SimSweepTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ph = mkphone(self.tmp.name)
        self.ph.sim = "PB_INIT_COMPLETE"

    def tearDown(self):
        self.tmp.cleanup()

    def test_sweep_reads_stores_then_deletes_and_is_idempotent(self):
        long_parts = [P.build_deliver_pdu("+306900000002", t, "+306971250044", (7, 2, k + 1))
                      for k, t in enumerate(("first ", "second"))]
        link = SimStoreFake({
            0: (1, bytes.fromhex(HELLO)),
            3: (2, P.build_deliver_pdu("+306974401373", "reply one", "+306971250044")),
            5: (1, long_parts[0]), 9: (1, long_parts[1]),
            12: (1, P.build_status_report_pdu("+306974401373", 11, 0, "+306971250044")),
            20: (1, P.build_deliver_pdu("+306900000003", "half", "+30", (9, 2, 1)))})
        self.ph.attach(link)
        r = self.ph.sweep_sim()
        self.assertEqual((r["ok"], r["slots"], r["found"]), (True, 40, 6))
        texts = sorted(m["text"] for m in self.ph.inbox.values())
        self.assertEqual(texts, sorted(["hellohello", "reply one", "first second"]))
        self.assertEqual(sorted(link.deleted), [0, 3, 5, 9, 12])
        self.assertEqual(list(link.slots), [20])                 # incomplete concat stays on the SIM
        self.assertEqual(r["kept"], 1)
        self.assertEqual(self.ph.sweep_sim()["stored"], 0)       # second run: nothing new

    def test_no_delete_when_inbox_write_fails(self):
        link = SimStoreFake({4: (1, P.build_deliver_pdu("+306974401373", "precious", "+30"))})
        self.ph.attach(link)
        self.ph.inbox.put = lambda rec: (_ for _ in ()).throw(OSError("disk full"))
        r = self.ph.sweep_sim()
        self.assertEqual((r["stored"], r["deleted"], r["kept"]), (0, 0, 1))
        self.assertEqual(link.deleted, [])
        self.assertIn(4, link.slots)

    def test_duplicate_not_restored_when_delete_failed_earlier(self):
        pdu = P.build_deliver_pdu("+306974401373", "again", "+30", ts=None)
        link = SimStoreFake({1: (1, pdu)}, fail_del=True)
        self.ph.attach(link)
        self.assertEqual(self.ph.sweep_sim()["stored"], 1)
        self.assertEqual(self.ph.sweep_sim()["stored"], 0)       # same sender/text/scts: not duplicated
        self.assertEqual(len(self.ph.inbox.values()), 1)

    def test_stored_index_in_incoming_noti_triggers_sweep(self):
        link = SimStoreFake({2: (1, P.build_deliver_pdu("+306974401373", "live", "+30"))})
        self.ph.attach(link)
        pdu = link.slots[2][1]
        noti = struct.pack("<BBHBB", 2, 1, 2, 5, len(pdu)) + pdu
        self.ph.handle_frame(nfr(P.SMS_INCOMING_MSG, P.NOTI, noti))
        run_deferred(self.ph)
        self.assertEqual([m["text"] for m in self.ph.inbox.values()], ["live"])
        self.assertEqual(link.deleted, [2])


class E2ESweepTest(unittest.TestCase):
    def test_boot_reports_memory_and_sweeps_sim(self):
        with tempfile.TemporaryDirectory() as tmp:
            sock = os.path.join(tmp, "p.sock")
            d = P.Daemon(state_dir=os.path.join(tmp, "s"), simulate=True, socket_path=sock, port=0,
                         sim_kwargs={"sim_status": 0}, req_timeout=3).start()
            try:
                cp = wait_for(lambda: d.fake.get("cp"))
                self.assertTrue(wait_for(lambda: d.phone.sms_ready))
                self.assertTrue(wait_for(lambda: cp.mem_sets))
                self.assertEqual(cp.mem_sets[0], b"\x02\x01")
                cp.sim_sms[6] = (1, P.build_deliver_pdu("+306974401373", "from the SIM", cp.smsc))
                r = d.phone.sweep_sim()
                self.assertEqual((r["stored"], r["deleted"]), (1, 1))
                self.assertEqual(cp.deleted, [6])
                self.assertEqual([m["text"] for m in d.phone.inbox.values()], ["from the SIM"])
                self.assertEqual(d.phone.status()["sim_sweep"]["deleted"], 1)
            finally:
                d.stop()


if __name__ == "__main__":
    unittest.main()
