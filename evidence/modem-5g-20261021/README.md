# 5G (NR) on the S22 modem — 2026-10-21/22

Owner: "5G was working". Question: why does s22-phoned only show LTE?

## Answer: 5G NSA already works; phoned did not show it

- **The network-mode preference already includes NR.** `(08,0A)` GET returns `{"mode_select":75}`.
  The stock libsec-ril (S901B FYI3) `IpcProtocol41Net::ConvertPreferredNetTypeToIpcWithBitmask`
  (@0x3dd05c, jump table @0x1749ed) maps Android preferred-network types to this bitmask:
  GSM 0x01, WCDMA 0x02, CDMA 0x04, LTE 0x08, TD-SCDMA 0x10, EVDO 0x20, **NR 0x40**.
  So 75 = 0x4B = NR|LTE|WCDMA|GSM = Android `NR_LTE_GSM_WCDMA` (26), the normal 5G-on setting.
  The stock `IpcProtocol41JsonNet::IpcTxNetSetPreferredNetType` (@0x4151c0) sends exactly
  `{"mode_select":N}` as `(08,0A)` SET. **No SET was sent**: the value is already the 5G one, and
  nothing was written to NV.
- **The CP adds an NR n78 leg (EN-DC, 5G NSA) as soon as the radio is connected.**
  `(08,3C)` physical channel config notifications (stock `IpcRxPhysicalChannelConfig`) show:
  LTE B3 20 MHz primary + LTE B20 10 MHz SCell + **NR n78 100 MHz secondary**
  (NR-ARFCN 643334 ≈ 3650 MHz, `rat` 5). NSA keeps the registration `act` at `lte`. That is why
  `/status.registration` said LTE; Android shows "5G" from this same channel config.
  The phoned logs show NR legs earlier on 10-21, before this session (PCI 234 and 93).
- The NR "disable mode" command `(08,3A)` (stock `IpcTxSetNrModeConfig`, one byte, 0 = none) was not
  needed and not sent. Its GET is a 7-byte binary frame if ever needed.

## Live test (this session, cid 1 internet APN via `s22-phoned mobile-data up`)
Timeline ([radio-timeline.json](radio-timeline.json)):
- t+0.0 s: data call up (`0d/10 data_call_state 1`, QCI 6). The channel config is still LTE B3 only.
- t+0.1 s: B20 SCell added.
- **t+0.6 s: NR n78 100 MHz added (`nr_nsa: true`)**. It stayed for both transfers.

Speed over `rmnet0` (curl `--interface rmnet0`; the default routes via ecm0/wlan0 were untouched;
the mobile route has metric 9000):

| Test | Result |
|---|---|
| ping 1.1.1.1 ×10 | 0 % loss, avg 33.5 ms (min 18.4) |
| upload 4 MB (speed.cloudflare.com) | 1.30 s → **24.6 Mbit/s** |
| download 15 MB (ranged GET, fsn1-speed.hetzner.com, Germany) | 1.23 s incl. 0.33 s TTFB → **97 Mbit/s** (≈130 Mbit/s after first byte) |
| download 15 MB, second run | 2.26 s → 53 Mbit/s |

Egress: Vodafone GR (AS3329). Total mobile data used ≈ 35 MB.
The first download attempt returned HTTP 403 (Cloudflare refuses >10 MB `__down`), which is why it shows 1 byte in `speed.txt`.
End state: data call released, rmnet0 DOWN, routes back to ecm0 + wlan0 metric 600, Wi-Fi and Tailscale up,
modem ONLINE, SMS ready. No calls, no SMS.

## Code change: phoned shows 5G now
`tools/hardware/phoned/s22-phoned.py`:
- decodes `(08,3C)` → `/status.radio` `{cells:[{act,band,conn,dl_khz,ul_khz,arfcn,pci}], nr_nsa, nr_bands,
  total_dl_khz, ts}`, plus `nr_last_seen` and a `radio` event when `nr_nsa` changes;
- `/data/status` gains `nr_nsa` and `phys_channels`;
- `ipc-get 0x08 0x0a` adds `rats` (decoded mode_select bits).
Tests: `test_radio_nr.py` (live 08/3C frame) plus all existing phoned suites pass.
Deployed: sha256 `f08c3f13…`. Backup `/srv/s22/hardware/bin/s22-phoned.pre-nr-radio.20261021T210613Z`
(`1af0a5a1…`). Only phoned was restarted (exact PID; keepalive restarted it, SMS ready in 2 s).
Rollback: `install -m 755 <backup> /srv/s22/hardware/bin/s22-phoned`, then kill the phoned PID.

## Not verified / next
- SA (standalone 5G) was not tested. The CP reports no SA capability in our flows (`5g_sa` PDP fields are omitted),
  and Vodafone GR consumer service is NSA.
- The UI still shows only `act`. Next: show "5G" in unum-fastshell/s22-touchd when `radio.nr_nsa`
  (UI left alone in this task by instruction).
- In idle (RRC released) the n78 leg goes away, as on Android. That is expected, not a fault.
