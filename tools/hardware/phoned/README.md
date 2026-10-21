# s22-phoned

SMS, call and SIM daemon for the Galaxy S22 (Exynos 2200, cpif ss310). It is a single file
that uses only the Python stdlib (3.12+). It owns `/dev/umts_ipc0` (SIPC FMT) and `/dev/umts_rfs0`.
The CP only gets INIT_END when both nodes are open. RFS NV requests are answered from the
PRIVATE EFS copy by `tools/hardware/modem/s22-modem.py`'s own code: the daemon imports it from
`../modem/s22-modem.py`, `$S22_MODEM_PY`, or `s22-modem` on PATH.

Do not run `s22-modem` and `s22-phoned` at the same time. Both open the same nodes.

```
s22-phoned serve [--simulate [--sim-locked]] [--socket /run/s22-phoned.sock] [--port 8095|0|-1]
                 [--state-dir /srv/s22/state/phoned] [--radio-normal] [--no-rfs] [--smsc +30...]
                 [--max-sms-per-hour 10] [--max-dials-per-hour 5]
s22-phoned status [--socket ...] [--port ...]
s22-phoned send-test-frame-decode <hex>      # decode raw FMT frame(s) from ipc.jsonl
```

## HTTP API (unix socket 0600 and TCP 127.0.0.1:8095, JSON)

| Request | Response |
|---|---|
| `GET /status` | `{ok, modem_state, ipc_open, sim, pin_attempted, registration, operator, signal, sms_ready, tx_enabled, calls, radio, nr_last_seen, simulated}` |
| `POST /sim/pin {"pin"}` | `{ok, error?}` |
| `POST /sms/send {"to","text"}` | `{ok, id, parts, status: sent\|queued\|refused, error?}` |
| `GET /sms/inbox?since=<id or ts>&limit=N` | `{ok, messages:[{id, from, text, ts, parts, smsc}]}` |
| `GET /sms/outbox?limit=N` | `{ok, messages:[{id, to, text, ts, parts, status, delivered}]}` |
| `POST /call/dial {"number"}` | `{ok, call}` |
| `POST /call/answer {}`, `POST /call/hangup {"id"?}`, `POST /call/dtmf {"digits"}` | `{ok, error?}` |
| `GET /calls` | `{ok, calls:[{id, number, direction, state, started, ts}]}` |
| `GET /events?since=<seq>&timeout=<s>` | long poll; returns `{ok, seq, events:[{seq, type, ts, ...}]}` |
| `POST /sim-inject/sms {"from","text"}`, `POST /sim-inject/call {"number"}` | `--simulate` only |

- Errors return `{"ok":false,"error":...}` with a 4xx/5xx status: 403 for a refusal by a gate,
  429 when rate limited, 503 when the modem or SIM is not ready.
- `ts` is Unix epoch seconds.
- Event types: `sms_received`, `sms_sent`, `sms_delivered`, `call_incoming`, `call_state`,
  `sim`, `registration`, `modem`.
- `hangup` sends IPC_CALL_RELEASE, which carries no call id. It ends the current call.

## Safety gates and state files

All state lives in `/srv/s22/state/phoned/` (mode 0700):

- `tx_enabled`: SMS send and dial are refused unless this file exists. It is absent by default.
- `allowlist`: one E.164 number per line; `#` starts a comment. The destination must match an
  entry exactly after spaces, dashes and dots are removed and a leading `00` is turned into `+`.
- Emergency numbers (112, 911, 999, 100, 166, 199, …) and any number with fewer than 7 digits
  are always refused.
- Rate limits: 10 SMS and 5 dials per hour. The SMS window is rebuilt from the outbox at startup.
- `sim-pin`: optional. It must be a regular file with mode 0600, owned by the daemon user.
  The daemon enters it automatically **at most once per run**, and only when the CP reports
  LOCK_SC with PIN1 required, never for PUK. It never retries. After 2 failed attempts in one run,
  `/sim/pin` refuses as well, so at least one try is left before PUK. The PIN is never logged:
  the tx frame is written as `<redacted>`.
- `inbox.jsonl` and `outbox.jsonl` are append-only; the last record with a given id wins.
- `ipc.jsonl` logs every tx/rx frame in hex and rotates at 4 MiB to `.1`.
- No attach or mode mutation is automatic. `PWR_PHONE_STATE normal` requires
  `--radio-normal`. Explicit experimental data controls below are operator-only;
  they cannot transmit arbitrary calls, SMS, SIM commands or network-mode SETs.

## SIM day (first run with a SIM)

Since 2026-10-08 the phone runs `modem` (cbd) and `phoned` as `s22-keepalive` services
(`/srv/s22/state/keepalive/config.json`), so nothing needs starting by hand. Keep the phone muted.

1. Insert the SIM (no hot-plug assumed): stop the CP so keepalive re-boots it with the card present:
   `s22-modem-up stop`, wait until keepalive has restarted cbd (`s22-modem-up status` shows ONLINE),
   then stop phoned too (kill its `s22-phoned serve` pid) so keepalive restarts it with fresh IPC nodes.
   Check `curl 127.0.0.1:8095/status`.
2. Expect `sim` = `READY` or `LOCK_SC(PIN)`. For the PIN, either `POST /sim/pin` ONCE, or write
   `/srv/s22/state/phoned/sim-pin` (0600) and restart phoned. Never retry a failed PIN automatically.
3. Registration: `registration.reg_status` should become `home`/`roaming`. If it stays `not_reg`/`denied`,
   add `--radio-normal` to phoned's argv via `service_defs.phoned` in the keepalive config (sends
   PWR_PHONE_STATE normal once) and restart phoned.
4. Read `ipc.jsonl` and check these raw frames against the layouts below. These are the first
   things to verify, because IPC 4.1 may differ:
   SEC_SIM_STATUS, NET_REGIST/NET_CURRENT_PLMN (JSON?), SMS_DEVICE_READY,
   SMS_SVC_CENTER_ADDR, and then the SMS_SEND_MSG response.
   Use `s22-phoned send-test-frame-decode <hex>` to decode a frame.
5. Owner's number (only after the owner gives it):
   `echo +30... > /srv/s22/state/phoned/allowlist`, then `touch /srv/s22/state/phoned/tx_enabled`.
   Also set it in the OpenUnum plugin: `/mnt/omarchy-trial/root/.openunum/plugins.json` →
   `{"s22-phone": {"owner_numbers": ["+30..."]}}`, then restart OpenUnum (`s22-openunum stop`; keepalive restarts it).
6. Send one short SMS to the owner (`POST /sms/send` or ask the agent). Confirm `sms_sent`, then `sms_delivered`.
7. Ask the owner to text the phone. Check the inbox, the bridge turn in session `phone:inbox`, and that the CP
   does not redeliver it (DELIVER_REPORT ack works).
8. Calls: dial the owner; test answer, hangup and DTMF, and an incoming call from the owner.
   Call audio (ABOX firmware-graph DMAs) is out of scope; voice conversations use `s22-sip` meanwhile.
9. Afterwards keep `tx_enabled` only if the owner wants the agent able to text/call the allowlist.

Tests: `python3 -I -B tools/hardware/phoned/test_s22_phoned.py`. They use synthetic frames
and simulate mode only, with no /dev access.

## Explicit experimental data IPC (2026-10-14)

```
s22-phoned ipc-send <group> <index> <type> '<hex|JSON-object>'
```

(The CLI expands `\n` escapes so the stock trailing newline can be typed.)

`POST /ipc/send {group,index,type,payload}` uses the same guarded sender. Numbers
on the CLI accept decimal or `0x` notation. Only SET (type 3) to `0d/01`, `0d/03`,
`0d/04`, `0d/14`, `0d/1b`, `0d/1d` is permitted. The documented GETs `08/0a` and
`0d/04` accept type 2 with an empty payload; `0f/3b` (JSON command list) only with the
exact stock body `{"jver":"1.0"}\n`. (An earlier `data-call` command sent `02/03`, which is
CALL_RELEASE; the stock builder really emits `03/02`, the CDMA-data group, so it was removed.) JSON text is encoded
unchanged as UTF-8; hex is decoded verbatim. Limit 4096 payload bytes.

One frame, no retries; max 3 s write deadline plus 6 s reply wait. Matching requires
nonzero ACK sequence and matching command; GEN_PHONE_RES also requires its inner
command and request type. The result preserves the actual sequenced TX frame,
matching RESP and/or full GEN frame and raw error code (`0x8000` = accepted), plus
`timed_out`. An acceptance is **not proof of working data**. JSON RESP is retained
without assigning undocumented success semantics. Concurrent experiments are
refused; at least 30 s between frames. Emergency off (`0d/04` SET with JSON `"state":0`) bypasses cooldown/ONLINE
checks but still requires an open link. Experiments require ONLINE otherwise.
These controls do not alter SMS/call gates and do not change Linux routes.

All RX/TX is logged in `ipc.jsonl`; `/status.data_notifications` and
`data_notification` events expose a bounded 64-record history of all `(0d,*)`
type-3 notifications, including full frame, raw payload and decoded JSON. Binary
notifications without a proven layout are explicitly `unmapped_binary`, not
assigned invented addresses or CIDs. Interface mapping must be verified before
using any notification to configure rmnet. Stock builders and live experiments
are documented in `RECON.md`.

## 5G NSA visibility (2026-10-22)

In EN-DC (5G NSA) the registration `act` stays `lte`. The NR leg only shows in the physical channel
config notification `(08,3C)` (JSON `{num_pch, pch:[{act, band:{band,rat}, conn_state, dlbw, dlch_num, pcid, ulbw, ...}]}`,
stock `IpcRxPhysicalChannelConfig`). phoned decodes it into `/status.radio`
`{cells, nr_nsa, nr_bands, total_dl_khz, ts}` plus `nr_last_seen`, emits a `radio` event when `nr_nsa` changes, and adds
`nr_nsa`/`phys_channels` to `/data/status`. `ipc-get 0x08 0x0a` adds `rats` from the stock
mode_select bit map (GSM 0x01, WCDMA 0x02, CDMA 0x04, LTE 0x08, TD-SCDMA 0x10, EVDO 0x20, NR 0x40).
The CP default is 75 = NR|LTE|WCDMA|GSM. Evidence: `evidence/modem-5g-20261021/`.
