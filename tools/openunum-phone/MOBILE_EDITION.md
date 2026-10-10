# S22 OpenUnum mobile edition

`plugins/s22-device/` gives the phone's agent its hardware as tools. **It has no tool list of its own.** At startup (and every
60 s, and on each health check) it reads `GET /v1/capabilities` from `s22d` and generates one tool per usable capability, using the
daemon's tool name, description and input schema. The only source of truth for endpoints, parameters and risk tiers is
[`tools/s22d/API.md`](../s22d/API.md); `tools/s22d/capabilities.json` is its checked-in snapshot and the plugin's contract tests
run against it.

The plugin refuses to expose a tool when the daemon advertises a lower risk tier than the plugin's floor for `sms.send`,
`call.dial` or `system.reboot-recovery`, and it validates every input against the daemon's schema before making a request.

## Tools (from the snapshot)

| Tool | Tier | Route | Available in the snapshot |
|---|---|---|---|
| `device_status` | read | GET `/v1/status` | yes |
| `device_battery` | read | GET `/v1/battery` | yes |
| `device_thermal` | read | GET `/v1/thermal` | yes |
| `processes_top` | read | GET `/v1/processes/top` | yes |
| `dmesg_read` | read | GET `/v1/logs/dmesg` | yes |
| `wifi_status` | read | GET `/v1/wifi/status` | yes |
| `wifi_scan` | read | POST `/v1/wifi/scan` | yes |
| `wifi_connect` | reversible | POST `/v1/wifi/connect` | yes |
| `wifi_disconnect` | reversible | POST `/v1/wifi/disconnect` | yes |
| `wifi_reconnect` | reversible | POST `/v1/wifi/reconnect` | yes |
| `bt_status` | read | GET `/v1/bt/status` | yes |
| `bt_power` | reversible | POST `/v1/bt/power` | no: HCI raw-socket kernel panic: disabled |
| `bt_scan` | read | POST `/v1/bt/scan` | no: HCI raw-socket kernel panic: disabled |
| `modem_status` | read | GET `/v1/modem/status` | yes |
| `sms_inbox` | read | GET `/v1/sms` | yes |
| `sms_send` | risky | POST `/v1/sms/send` | yes |
| `call_list` | read | GET `/v1/calls` | yes |
| `call_dial` | risky | POST `/v1/call/dial` | yes |
| `call_hangup` | reversible | POST `/v1/call/hangup` | yes |
| `camera_status` | read | GET `/v1/camera/status` | yes |
| `camera_capture` | reversible | POST `/v1/camera/capture` | yes |
| `display_status` | read | GET `/v1/display` | yes |
| `display_on` | reversible | POST `/v1/display/on` | yes |
| `display_off` | reversible | POST `/v1/display/off` | yes |
| `display_brightness` | reversible | POST `/v1/display/brightness` | yes |
| `audio_volume` | read | GET `/v1/audio/volume` | yes |
| `audio_mute` | reversible | POST `/v1/audio/volume` | yes |
| `service_restart` | reversible | POST `/v1/services/{name}/restart` | yes |
| `recovery_mode` | risky | POST `/v1/system/reboot-recovery` | yes |

Tool exposure is S22-only: the `/srv/s22` marker exists, or `enabled: true`. Default is disabled. Tools whose backend is
missing on the phone (for example `sms_send` while `s22-phoned` is not running) do not appear until the daemon reports them
available.

## Tiers and confirmation

| Tier | What happens |
|---|---|
| read | Free. |
| reversible | Free; `s22d` writes an audit record and the plugin logs the action. |
| risky | `s22d` puts a yes/no card on the phone and **blocks until the owner taps**. The plugin only forwards the request; it cannot answer the card, and any answer other than yes (no, timeout, touch UI not reachable) is a refusal that must not be retried. |

The confirmation lives in the daemon, so every client (this plugin, `unum-shell`, curl) is gated the same way.
`s22-phoned` still enforces its own allowlist, `tx_enabled` gate and rate limits behind that.
Audio unmute is not provided: `audio_mute` accepts only 0 unless the owner created `/etc/s22-audio-unmuted`.

## Install and enable (on the phone, after s22d is running)

```sh
sh tools/openunum-phone/install-device-plugin.sh
```

This copies the plugin (`plugin.json`, `index.mjs`, `lib/`) into `$C/root/.openunum/plugins/s22-device` (`C=/mnt/omarchy-trial` by default).
Add to `$C/root/.openunum/plugins.json`:

```json
{"s22-device":{"enabled":true}}
```

Then restart OpenUnum (`s22-openunum stop`; keepalive starts it again). Check that tools were generated:
`curl -s 127.0.0.1:18880/api/health` and the plugin health line `generated_tools`. Do not expose s22d on a non-loopback interface.

## Tests and rollback

```sh
node --test tools/openunum-phone/plugins/s22-device/test/plugin.test.mjs
```

The tests run against a fake `s22d` built from `capabilities.json` (no phone, no daemon needed). They check that every tool
calls its method and path with exactly the parameters its schema names, that the three historical mismatches stay fixed
(`sms_send` uses `number`, display is on/off/brightness, audio read is GET and set is POST), and that refusals come back unchanged.

Rollback: remove `$C/root/.openunum/plugins/s22-device` and restart OpenUnum. s22d and phone state are untouched.
No phone hardware was available during development; installation and behaviour on the device are unverified.
