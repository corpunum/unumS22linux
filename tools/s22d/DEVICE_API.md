# s22d device API (`device/1`)

One typed, capability-oriented interface to the phone itself, for the Rust shell's Settings page and
Agent card, and for the OpenUnum agent (`phone_device` tool of the `s22-device` plugin). It sits next to
the older per-endpoint API in [`API.md`](API.md) on the same daemon and shares its transport, error
format and audit log. If this file and `src/device.rs` disagree, the code is wrong.

`device-capabilities.json` is a checked-in snapshot of `GET /v1/device` (a test fails when it is stale;
`UPDATE_CAPABILITIES=1 cargo test` rewrites it). The plugin's contract tests run against it.

## Transport

| Listener | Address | Notes |
|---|---|---|
| Unix socket | `/run/s22d.sock` (0600, root) | **Use this from the shell.** HTTP/1.1 over the socket. |
| TCP | `127.0.0.1:8766` | Loopback only, loopback `Host`, no `Origin` header (see `API.md`). |

Every request may carry `X-S22-Caller: shell | agent | cli` (anything else counts as `cli`). It goes
into the audit log; it grants nothing.

```
GET  /v1/device                 the catalog (below)
POST /v1/device/<id>            run a capability; body = JSON object of arguments ({} or empty for none)
GET  /v1/device/<id>?k=v        read capabilities only (405 for set/action)
```

## Answers

Success, HTTP 200:

```json
{ "ok": true, "cap": "wifi.set", "effect": "set",
  "state": { "on": false, "connected": false, "state": "DISCONNECTED", "uplink": "mobile", ... },
  "note": "Wi-Fi off until wifi.set on:true (or a restart); ..." }
```

- `state` is **read back from the source after the change** (the file, sysfs, `wpa_cli`, `s22-phoned`,
  `s22-bt`), not an echo of the request. Show it; do not assume the request took effect.
- `note` (optional) is one sentence for a human: what happens next or what does not work.
- `result` (optional, `call.dial`, `sms.send`): extra facts such as `owner_confirmed`.

Failure: a 4xx/5xx status and `{ "ok": false, "cap": "...", "effect": "...", "code": "...", "error": "..." }`.
The codes of `API.md` apply, plus:

| Status | `code` | Meaning |
|---|---|---|
| 400 | `bad_request` | Unknown argument, wrong type or out of range. Nothing was done. |
| 403 | `owner_denied` | The owner tapped **No** on the phone. Final. |
| 403 | `calls_disabled` | `calls.set on:false` is in force. |
| 409 | `needs_confirmation` | The number is not on the allowlist and nobody confirmed on the screen in time (or the screen could not ask). Nothing was sent or dialled. Tell the owner; do not retry in a loop. |
| 409 | `would_strand` | Wi-Fi off / mobile data off would leave the phone with no uplink. Pass `force: true` to do it anyway. |
| 409 | `screen_off` | `screenshot` while the screen is off. |
| 503 | `unavailable` | The backend is missing (`error` says which). The catalog shows the same thing as `available:false`. |

## Policy (owner, 2026-10-22)

- **No confirmations**, except: `call.dial` and `sms.send` to a number that is **not** on the private
  allowlist put a yes/no card on the phone (the `s22-touchd` confirm-card protocol, `API.md`) and wait up
  to 60 s (`S22D_CONFIRM_TIMEOUT_S`). Yes proceeds, No is `owner_denied`, silence is `needs_confirmation`.
- The allowlist exists **only on the phone**: `/srv/s22/state/phone/allowlist.json`, mode 0600,
  `{"numbers": ["+30...", {"number": "+30...", "name": "..."}]}`. Numbers are compared after
  `s22-phoned`'s normalisation (spaces, `-`, `.`, `()` dropped, `00` -> `+`). Never commit it.
- After its own policy passes, s22d leaves `s22-phoned` a one-shot grant
  (`/srv/s22/state/phoned/grants/<number>.<sms|dial>`, valid 120 s, consumed on use). phoned still
  enforces `tx_enabled`, the emergency/short-number refusal and its hourly rate limits.
- `calls.set` is a gate for `call.dial` (`/srv/s22/state/phone/calls.json`, missing = enabled).
- Two-way call audio does not work yet (issue #63): a dialled call connects silently. Every call answer
  says so.

## Audit

Every device API call (reads included) and every state-changing legacy call appends one line to
`/srv/s22/state/audit/s22d.jsonl` (0600; at 1 MiB it becomes `s22d.jsonl.1`, one generation kept):

```json
{"ts":1792672000,"caller":"agent","action":"sms.send","risk":"action","outcome":"ok",
 "detail":{"args":{"number":"+306*******78","text":"<11 chars>"}}}
```

Phone numbers are masked (first three and last two digits), message text is replaced by its length,
Wi-Fi passwords are never written. `outcome` is `ok`, `refused` (4xx) or `failed` (5xx, with `code`).

## Capabilities

Arguments share one flat namespace: `on` (bool), `value` (int), `muted` (bool), `locked` (int),
`number`, `text`, `pattern`, `sensor`, `force` (bool). Strings such as `"true"`, `"off"` and `"40%"`
are accepted for booleans and integers.

| Capability | Effect | Arguments | `state` | Source |
|---|---|---|---|---|
| `device.status` | read | - | `{battery{percent,charging,status,temp_c}, thermal_max_c, network{uplink, wifi{enabled,on,connected,ssid,ip,rssi_dbm}, mobile_data{on,state,ipv4}, radio:"5G"\|"4G"\|..., operator, signal_bars, usb}, bluetooth, volume, brightness_pct, vibration, autosleep, calls_enabled}`; a source that fails is `null` | all of the below |
| `wifi.get` | read | - | `{enabled, on, connected, state, ssid, bssid, ip, freq_mhz, rssi_dbm, link_mbps, network_id, saver_parked, uplink}`. Bind a Settings toggle to `enabled` (the owner's switch); `on` is the radio as wpa_supplicant reports it (netpolicy's Wi-Fi saver parks an enabled Wi-Fi while the screen is off on mobile data). | `wpa_cli`, netpolicy status |
| `wifi.set` | set | `on`, `force?` | as `wifi.get` | off: holds netpolicy's Wi-Fi saver kill switch (`/srv/s22/state/netpolicy/wifi-saver-off`, only if the owner has not set it; released by `on` or at s22d start after a reboot) so the saver cannot reconnect it, then `wpa_cli disconnect` (runtime; a reboot or `s22-net-deadman restore` turns Wi-Fi back on). on: `enable_network all` + `reconnect`, waits up to 20 s for association. netpolicy moves the uplink (Wi-Fi > mobile > USB) within ~30 s. Off is refused (`would_strand`) when mobile data is off and USB has no carrier. |
| `bluetooth.get` | read | - | `{on, hci0, detail}` | `s22-bt status`, `/sys/class/bluetooth/hci0` |
| `bluetooth.set` | set | `on` | as `bluetooth.get` | `s22-bt up\|down`. Up waits for Wi-Fi/CNSS (shared power rail) and hci0: up to ~2 min. No pairing. |
| `mobile_data.get` | read | - | `{on, mode, state, ipv4, interface, uplink}` | phoned `/data/status`, netpolicy |
| `mobile_data.set` | set | `on`, `force?` | as `mobile_data.get` | on: netpolicy `mobile_mode` `off` -> `failover` (`always` is kept), data call brought up now if Wi-Fi is down. off: `mobile_mode` `off` + phoned `/data/down`; refused (`would_strand`) when Wi-Fi is not connected and USB has no carrier. Mode lives in `/srv/s22/state/netpolicy/config.json`. |
| `calls.get` | read | - | `{enabled, calls, audio}` | `calls.json`, phoned `/calls` |
| `calls.set` | set | `on` | `{enabled, audio}` | `/srv/s22/state/phone/calls.json` |
| `call.dial` | action | `number` | `{calls}`; `result{dial, owner_confirmed}` | policy above, phoned `/call/dial` |
| `call.hangup` | action | - | `{calls}` | phoned `/call/hangup` |
| `sms.send` | action | `number`, `text` (1-1000) | `{id, status, parts}`; `result{owner_confirmed}` | policy above, phoned `/sms/send` |
| `volume.get` | read | - | `{level 0-10, muted, max, source: file\|default}` | `/run/s22-say.level` (absent = s22-say's 6) |
| `volume.set` | set | `value?` 0-10, `muted?` | as `volume.get` | writes `/run/s22-say.level` and `/srv/s22/buttons/volume` (the shell's level). `muted:true` saves the level in `/srv/s22/state/audio/volume.json`, `muted:false` restores it. Applies to the next spoken output. |
| `brightness.get` | read | - | `{state, brightness, max_brightness, percent}` | backlight sysfs |
| `brightness.set` | set | `value` 1-100 | as `brightness.get` | backlight sysfs (0 is refused: switch the screen off instead) |
| `vibration.get` | read | - | `{enabled, daemon}` | `/srv/s22/state/haptics/config.json` (missing = enabled) |
| `vibration.set` | set | `on` | as `vibration.get` | same file, other keys kept; s22-haptic re-reads it |
| `haptic.play` | action | `pattern`: `tick`, `buzz`, `stop` or `ON,OFF,...` ms (<= 16 numbers, each 1-3000) | `{played, daemon: "ok"\|"dropped"}` | one datagram to `/run/s22-haptic.sock` (`docs/S22_HAPTICS.md`); with vibration off `played:false` and nothing is sent |
| `autosleep.get` | read | - | `{enabled, state, cycles}` | `/srv/s22/state/autosleep/disabled`, `/run/s22-autosleep/status.json` |
| `autosleep.set` | set | `on` | as `autosleep.get` | the flag file `s22-autosleep on\|off` uses (obeyed within 2 s) |
| `screen_timeout.get` | read | - | `{unlocked_s, locked_s, never: 0}` | `/srv/s22/state/fastshell/screen-timeout.json` (defaults 60/15) |
| `screen_timeout.set` | set | `value?` (unlocked s), `locked?` (s), 0-3600, 0 = never | as `screen_timeout.get` | same file; unum-fastshell re-reads it every 10 s while the screen is on |
| `camera.capture` | action | `sensor?` `rear`\|`front` (default rear) | `{sensor, path, developed, width, height, ...}` | the reviewed camera path of `camera.capture` in `API.md` (lock, cooldown, gallery copy) |
| `screenshot` | action | - | `{taken, path, file}` | unum-fastshell `shell.sock` `{"fn":"screenshot"}` (same capture as Power + Volume down) into the gallery; needs a shell build with the hook, else `unavailable` |

## Catalog

`GET /v1/device`:

```json
{ "ok": true, "api": "device/1", "capabilities": [
  { "id": "wifi.set", "effect": "set", "confirm": "none", "timeout_s": 40,
    "description": "Turn Wi-Fi on or off. ...", "available": true, "reason": null,
    "args": { "type": "object", "properties": { "on": {"type": "boolean"}, "force": {...} },
              "required": ["on"], "additionalProperties": false } } ] }
```

`timeout_s` is how long a client should wait (Bluetooth up, camera and owner questions are slow).
`available:false` carries a `reason`; calling it anyway answers 503 `unavailable`.

## Examples

```sh
S='curl -s --unix-socket /run/s22d.sock -H content-type:application/json -H x-s22-caller:cli'
$S http://l/v1/device | jq '.capabilities[].id'
$S http://l/v1/device/device.status
$S -X POST http://l/v1/device/brightness.set -d '{"value":40}'
$S -X POST http://l/v1/device/volume.set     -d '{"muted":true}'
$S -X POST http://l/v1/device/wifi.set       -d '{"on":false}'      # arm s22-net-deadman first when remote
$S -X POST http://l/v1/device/haptic.play    -d '{"pattern":"40,80,40"}'
$S -X POST http://l/v1/device/sms.send       -d '{"number":"+306900000000","text":"On my way"}'
```

Rust (shell), std only: one request per connection, `Connection: close`, read to EOF.

```rust
use std::io::{Read, Write};
use std::os::unix::net::UnixStream;

/// POST /v1/device/<id> over s22d's unix socket; returns (HTTP status, JSON body).
fn device(id: &str, args: &serde_json::Value) -> std::io::Result<(u16, serde_json::Value)> {
    let mut s = UnixStream::connect("/run/s22d.sock")?;
    s.set_read_timeout(Some(std::time::Duration::from_secs(150)))?;
    let body = args.to_string();
    write!(s, "POST /v1/device/{id} HTTP/1.1\r\nHost: localhost\r\nX-S22-Caller: shell\r\n\
               Content-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
           body.len())?;
    let mut raw = String::new();
    s.read_to_string(&mut raw)?;
    let (head, json) = raw.split_once("\r\n\r\n").unwrap_or((&raw, ""));
    let status = head.split(' ').nth(1).and_then(|c| c.parse().ok()).unwrap_or(0);
    // s22d answers with Content-Length (no chunking) for these small JSON bodies.
    Ok((status, serde_json::from_str(json).unwrap_or(serde_json::Value::Null)))
}
// device("wifi.set", &json!({"on": false})) -> (200, {"ok":true,"state":{"on":false,...},"note":...})
```

Call slow capabilities (`bluetooth.set`, `camera.capture`, `call.dial`/`sms.send` to an unlisted number,
`wifi.set on`) off the UI thread and show a spinner; the request stays open until there is an answer.

## OpenUnum

The `s22-device` plugin turns this catalog into one tool, `phone_device`
(`{action: <id>, on?, value?, muted?, locked?, number?, text?, pattern?, sensor?, force?}`), and hides
the legacy `/v1/capabilities` tools whose row says `superseded_by: "phone_device"` (plugin config
`legacy_tools: "all"` keeps them). Legacy tools without a device equivalent stay (`wifi_scan`,
`wifi_connect`, `modem_status`, `device_sms_inbox`, `camera_record`, `camera_status`, `display_on`,
`display_off`, `processes_top`, `dmesg_read`, `service_restart`, `recovery_mode`).
