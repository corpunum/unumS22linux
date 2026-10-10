# s22d API (version 1)

This file is the contract between `s22d` (the device daemon), the `s22-device` OpenUnum plugin and
`unum-shell`. If the code and this file disagree, the code is wrong.

Two things are generated from the same table in `src/catalog.rs`:

- `GET /v1/capabilities` serves it live, with `available` and `reason` filled in from the phone.
- `capabilities.json` is a checked-in snapshot. A test fails when it is stale
  (`UPDATE_CAPABILITIES=1 cargo test` rewrites it). The plugin's contract tests run against it.

The `s22-device` plugin builds its tools from `/v1/capabilities`. It does not carry its own tool list.

## Transport and access

| Listener | Address | Access control |
|---|---|---|
| TCP | `127.0.0.1:8766` | Loopback peers only. `Host` must be `127.0.0.1`, `localhost` or `[::1]`. A request with an `Origin` header is refused, so a web page in the phone's browser cannot drive the daemon. |
| Unix socket | `/run/s22d.sock` (mode 0600) | Filesystem permissions. |

Both serve the same routes. Bodies are JSON (`Content-Type: application/json`). Environment overrides:
`S22D_ADDR`, `S22D_SOCKET`, `S22D_AUDIT`, `S22D_TOUCH_SOCK`, `S22D_CONFIRM_DIR`, `S22D_CAMERA_CLIENT`,
`S22D_DEVELOP`, `S22D_CAMERA_DIR`, `S22D_CONFIRM_TIMEOUT_S`.

`s22d` runs as root on the native Alpine root, supervised by `s22-keepalive`.

## Answers

Success: HTTP 200 and `{ "ok": true, ... }`.
Failure: a 4xx or 5xx status and `{ "ok": false, "error": "<message>", "code": "<code>" }`.

| Status | `code` | Meaning |
|---|---|---|
| 400 | `bad_request` | Malformed JSON, missing or out-of-range field. Nothing was done. |
| 403 | `policy_denied` | Owner policy forbids it (audio). Final. |
| 403 | `not_allowed` | Not on the allowlist (service restart). Final. |
| 403 | `owner_denied` | The owner tapped no on the phone. Final for this request. |
| 403 | `refused_by_phoned` | `s22-phoned` refused (allowlist, `tx_enabled`, emergency number). Final. |
| 403 | `non_loopback`, `browser_origin`, `bad_host` | Transport guard. |
| 404 / 405 | `not_found`, `method_not_allowed` | No such route. |
| 408 | `owner_timeout` | The owner did not answer in time. Nothing was done. |
| 409 | `confirm_busy` | Another owner question is on screen. |
| 409 | `camera_busy` | A capture is running. |
| 409 | `keepalive_disabled` | `s22-keepalive` is not enabled, so a stopped service would stay stopped. |
| 429 | `camera_cooldown`, `rate_limited` | Camera pause after a capture or a failure; phoned's hourly limits. |
| 502 | `upstream_error`, `camera_failed`, `connect_failed` | A helper answered badly or failed. |
| 503 | `unavailable` | The subsystem is not there or not running. `error` says why. |
| 503 | `confirm_unavailable` | The owner cannot be asked (touch UI not reachable). Treated as a denial. |
| 504 | `timeout`, `camera_wedged` | A helper hung. After `camera_wedged` do not retry. |

Clients must not retry around a 403. A refusal is the answer.

## Risk tiers

| Tier | Behaviour |
|---|---|
| `read` | Observes only. Not audited (except `wifi.scan`). |
| `reversible` | Changes state that can be undone. Always audited. No confirmation. |
| `risky` | Cannot be undone, or costs money or privacy. **`s22d` itself asks the owner on the phone's screen and blocks until the owner taps yes, no, or the timeout passes.** The request is held open meanwhile, so use the capability's `timeout_s`. Only `yes` proceeds. Clients (plugin, unum-shell, curl) do not and cannot confirm on `s22d`'s behalf. |

Risky capabilities: `sms.send`, `call.dial`, `system.reboot-recovery`.

How the owner is asked: `s22d` sends `{"cmd":"ui","args":["confirm", <id>, <question>, <seconds>]}` to
`s22-touchd`'s `ctl.sock` (inside the chroot `/run/s22-touch/ctl.sock`, from the native root
`/mnt/omarchy-trial/run/s22-touch/ctl.sock`) and then waits for `/run/s22-touch/confirm/<id>.json` containing
`{"answer":"yes"|"no"}`. This is the protocol `s22-ui confirm` and the `s22-ui` plugin already use. Only one question can be
pending; a second risky request gets `confirm_busy`.

Every request that changes state appends one JSON line to `/srv/s22/state/s22d/audit.jsonl`:
`{ts, action, risk, outcome: ok|refused|failed, detail}`. Passwords and message text are never written there.
If the audit file cannot be opened at start, `s22d` does not start.

## Capabilities

`GET /v1/capabilities`

```json
{ "ok": true, "api_version": 1, "capabilities": [
  { "id": "sms.send", "tool": "sms_send", "description": "...", "method": "POST", "path": "/v1/sms/send",
    "risk": "risky", "confirm": "owner", "timeout_s": 95,
    "available": true, "reason": null,
    "input_schema": { "type": "object", "properties": { "...": {} }, "required": ["number", "text"], "additionalProperties": false } } ] }
```

- `tool` is the OpenUnum tool name.
- `input_schema` is JSON Schema. For `GET` endpoints the properties are query parameters; for `POST` they are the JSON body.
  A `{name}` segment in `path` is filled from the property of the same name.
- `available: false` carries the `reason`. Availability is live: it follows the control socket, helper binary or daemon.

## Capability and tool index

| Capability | OpenUnum tool | Route | Risk | Client timeout |
|---|---|---|---|---|
| `status` | `device_status` | GET `/v1/status` | read | 15 s |
| `battery` | `device_battery` | GET `/v1/battery` | read | 15 s |
| `thermal` | `device_thermal` | GET `/v1/thermal` | read | 15 s |
| `processes.top` | `processes_top` | GET `/v1/processes/top` | read | 15 s |
| `logs.dmesg` | `dmesg_read` | GET `/v1/logs/dmesg` | read | 15 s |
| `wifi.status` | `wifi_status` | GET `/v1/wifi/status` | read | 20 s |
| `wifi.scan` | `wifi_scan` | POST `/v1/wifi/scan` | read | 25 s |
| `wifi.connect` | `wifi_connect` | POST `/v1/wifi/connect` | reversible | 60 s |
| `wifi.disconnect` | `wifi_disconnect` | POST `/v1/wifi/disconnect` | reversible | 20 s |
| `wifi.reconnect` | `wifi_reconnect` | POST `/v1/wifi/reconnect` | reversible | 20 s |
| `bt.status` | `bt_status` | GET `/v1/bt/status` | read | 15 s |
| `bt.power` | `bt_power` | POST `/v1/bt/power` | reversible | 15 s |
| `bt.scan` | `bt_scan` | POST `/v1/bt/scan` | read | 15 s |
| `modem.status` | `modem_status` | GET `/v1/modem/status` | read | 25 s |
| `sms.list` | `sms_inbox` | GET `/v1/sms` | read | 25 s |
| `sms.send` | `sms_send` | POST `/v1/sms/send` | risky | 95 s |
| `call.list` | `call_list` | GET `/v1/calls` | read | 25 s |
| `call.dial` | `call_dial` | POST `/v1/call/dial` | risky | 95 s |
| `call.hangup` | `call_hangup` | POST `/v1/call/hangup` | reversible | 25 s |
| `camera.status` | `camera_status` | GET `/v1/camera/status` | read | 15 s |
| `camera.capture` | `camera_capture` | POST `/v1/camera/capture` | reversible | 330 s |
| `display.status` | `display_status` | GET `/v1/display` | read | 15 s |
| `display.on` | `display_on` | POST `/v1/display/on` | reversible | 35 s |
| `display.off` | `display_off` | POST `/v1/display/off` | reversible | 35 s |
| `display.brightness` | `display_brightness` | POST `/v1/display/brightness` | reversible | 15 s |
| `audio.volume.get` | `audio_volume` | GET `/v1/audio/volume` | read | 15 s |
| `audio.volume.set` | `audio_mute` | POST `/v1/audio/volume` | reversible | 15 s |
| `service.restart` | `service_restart` | POST `/v1/services/{name}/restart` | reversible | 40 s |
| `system.reboot-recovery` | `recovery_mode` | POST `/v1/system/reboot-recovery` | risky | 95 s |

## Endpoints

### Status and diagnostics (read)

| Capability | Request | Answer |
|---|---|---|
| `status` | `GET /v1/status` | `{uptime_s, load[3], cpus, cpu_load, mem{total_mb,available_mb}, disk[{mount,total_mb,avail_mb}], battery{...}, thermal_max_c, network{iface: operstate}, modem, display{state,brightness}, audio{level,muted}, keepalive, hostname, kernel}`. Any source that cannot be read is `null`. |
| `battery` | `GET /v1/battery` | `{name, percent, status, charging, health, temp_c, voltage_mv, current_ua}` from `power_supply`. 503 if there is no battery. |
| `thermal` | `GET /v1/thermal` | `{zones[{zone,type,temp_c}], hottest}` from `/sys/class/thermal`. |
| `processes.top` | `GET /v1/processes/top?sort=cpu\|mem&limit=1..50` | `{sort, processes[{pid,name,cpu_pct,rss_mb,cmd}], total}`. CPU is measured over a 250 ms window. |
| `logs.dmesg` | `GET /v1/logs/dmesg?since=<seq>&limit=1..1000` | `{lines[{seq,ts_s,level,msg}], last_seq, truncated}` from `/dev/kmsg`. Pass `last_seq` as `since` to read only new records. |

### Wi-Fi (`wpa_cli -p /run/wpa_supplicant-s22 -i wlan0`)

| Capability | Request | Answer |
|---|---|---|
| `wifi.status` | `GET /v1/wifi/status` | `{state, connected, ssid, bssid, ip, freq_mhz, rssi_dbm, link_mbps, network_id}` |
| `wifi.scan` | `POST /v1/wifi/scan` | `{networks[{ssid,bssid,freq_mhz,signal_dbm,secured,flags}]}` strongest first; hidden SSIDs omitted. Takes about 3 s. |
| `wifi.connect` | `POST /v1/wifi/connect {ssid, password?}` | `{ssid, ip, network_id}`. SSID 1-32 bytes, password 8-63 printable ASCII, omit it for an open network. Runtime only: the saved profile is untouched and a supplicant restart returns to it. On failure the new network is removed and the previous one is selected again (`connect_failed`). |
| `wifi.disconnect` | `POST /v1/wifi/disconnect` | `{}`. Remote access that rides on Wi-Fi ends. |
| `wifi.reconnect` | `POST /v1/wifi/reconnect` | `{}`. Enables all saved profiles and reconnects. |

### Bluetooth

| Capability | Request | Answer |
|---|---|---|
| `bt.status` | `GET /v1/bt/status` | `{available:false, reason:"HCI raw-socket kernel panic: disabled"}` |
| `bt.power`, `bt.scan` | `POST /v1/bt/power`, `POST /v1/bt/scan` | Always 503 with the same reason. Raw HCI sockets panic the kernel. |

### Modem, SMS and calls (proxy to `s22-phoned`)

`s22-phoned` keeps the real policy: the `tx_enabled` file, the number allowlist, the emergency-number refusal
and the hourly rate limits. `s22d` forwards only well-formed requests and passes every refusal through unchanged.
It talks to `/run/s22-phoned.sock` and falls back to `127.0.0.1:8095`.

| Capability | Request | Answer |
|---|---|---|
| `modem.status` | `GET /v1/modem/status` | phoned's `/status`: `{modem_state, sim, registration, operator, signal, sms_ready, tx_enabled, calls, ...}` |
| `sms.list` | `GET /v1/sms?box=inbox\|outbox&since=<id>&limit=1..100` | `{messages[...]}` as phoned returns them |
| `sms.send` (risky) | `POST /v1/sms/send {number, text}` | `{id, parts, status}`. `number` is E.164-like (`+306900000000`), `text` 1-1000 characters. |
| `call.list` | `GET /v1/calls` | `{calls[...]}` |
| `call.dial` (risky) | `POST /v1/call/dial {number}` | `{call}`. No call audio yet. |
| `call.hangup` | `POST /v1/call/hangup` | `{}` |

The order for `sms.send` and `call.dial` is: validate (400), check phoned is running (503), ask the owner, forward.
The owner is never asked about a request that cannot succeed.

### Camera (`s22-camera.py`, reviewed client)

| Capability | Request | Answer |
|---|---|---|
| `camera.status` | `GET /v1/camera/status` | `{client_present, develop_available, sensors, dir, last, busy, cooldown_s}` |
| `camera.capture` | `POST /v1/camera/capture {sensor, exposure_us?, gain?, develop?}` | `{sensor, path, raw, developed, width, height, exposure_us, gain, ms, client}` |

`sensor` is `rear` (S5KGN3) or `front` (IMX374). `exposure_us` is 100-30000 and `gain` is analog 1-16, sent as
`--cis-exposure-us` / `--cis-again`, the only exposure path that reaches the sensor. `develop` (default true) runs the
raw to DNG to LibRaw step (draft PR #32) when `/srv/s22/hardware/bin/s22-develop` is installed; otherwise
`developed` is `{ok:false, reason}` and `path` is the client's own PNG.

Safety, because a camera DMA fault can panic the kernel:
- one capture at a time (`camera_busy`);
- at least 10 s between captures and 60 s after any failure (`camera_cooldown`);
- the client always runs with its own `--deadline`; on a hang it gets SIGTERM and 20 s of grace, never SIGKILL, and
  the answer is `camera_wedged`;
- `system.reboot-recovery` is refused while a capture runs;
- only the newest 8 captures are kept in `/srv/s22/state/s22d/camera/`.

### Display (`s22-display`, `s22-touchd`)

| Capability | Request | Answer |
|---|---|---|
| `display.status` | `GET /v1/display` | `{state, brightness, max_brightness, percent}` |
| `display.on`, `display.off` | `POST /v1/display/on`, `POST /v1/display/off` | status plus `via`. Goes through `s22-touchd` (it locks the UI before switching off, like the Power key) and falls back to `s22-display`. |
| `display.brightness` | `POST /v1/display/brightness {percent: 1..100}` | status. Writes the backlight. 0 is refused: use `display.off`. |

### Audio

| Capability | Request | Answer |
|---|---|---|
| `audio.volume.get` | `GET /v1/audio/volume` | `{level, muted, unmute_allowed}` |
| `audio.volume.set` | `POST /v1/audio/volume {value: 0..100}` | `{level, muted}` |

Owner policy (2026-10-08): the phone stays muted. Any non-zero `value` is refused with `policy_denied` unless the
owner has created `/etc/s22-audio-unmuted`. The refusal is checked before any backend is touched, and clients must not
work around it. `s22-keepalive`'s mute guard keeps the amplifier at 0 independently of this endpoint.

### Services and system

| Capability | Request | Answer |
|---|---|---|
| `service.restart` | `POST /v1/services/{name}/restart` | `{service, signalled_pids, was_running, restart_by}` |
| `system.reboot-recovery` (risky) | `POST /v1/system/reboot-recovery` | `{scheduled_in_s, mode:"recovery"}` |

`name` must be one of `openunum`, `unumsearch`, `modem`, `phoned`, `llama`; anything else is `not_allowed` and nothing is
signalled. Restart is `s22-keepalive`'s own mechanism: the process is stopped (SIGTERM on the matching process,
`s22-modem-up stop` for the modem) and keepalive's next pass starts it again with its definition, health check and backoff.
`s22d` never starts a service itself. Without `/srv/s22/state/keepalive/enabled` the request fails with
`keepalive_disabled`.

`system.reboot-recovery` runs `/usr/local/sbin/s22-reboot recovery` 3 s after the answer, in its own process group.
The phone is offline for about 40 s; OpenUnum, buttons and unumsearch start by themselves.

## What is real and what is not

Everything above reads or drives a real source on the phone. Backends that depend on something that may be absent
report `available:false` with the reason in `/v1/capabilities` and fail with `503 unavailable` when called:

| Backend | Needs |
|---|---|
| Wi-Fi | `/run/wpa_supplicant-s22/wlan0` |
| Modem, SMS, calls | `/run/s22-phoned.sock` |
| Camera | `/srv/s22/hardware/bin/s22-camera` (develop step: `/srv/s22/hardware/bin/s22-develop`) |
| Display | `/srv/s22/hardware/bin/s22-display` |
| Service restart | `/srv/s22/state/keepalive/enabled` |
| Reboot | `/usr/local/sbin/s22-reboot` |
| Bluetooth | never (kernel panic) |
