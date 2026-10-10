# S22 OpenUnum mobile edition

`plugins/s22-device/` adds s22d-backed hardware tools. It discovers `/v1/capabilities` at plugin startup and exposes only entries whose method, route, availability and risk match the reviewed local definitions. Unknown or mismatched daemon capabilities are intentionally omitted; restart OpenUnum after changing daemon capabilities.

## Tool inventory and tiers

| Tool names | Tier |
|---|---|
| `device_status`, `device_thermal`, `processes_top`, `wifi_status`, `wifi_scan`, `bt_status`, `modem_status`, `sms_inbox`, `camera_status`, `display_status`, `audio_volume`, `dmesg_read` | Read, free |
| `wifi_connect`, `wifi_disconnect`, `camera_capture`, `display_set`, `service_restart` | Reversible, free and work-log logged |
| `sms_send`, `call_dial`, `recovery_mode` | Risky, phone owner must tap **yes** on the existing touch confirmation card. Missing UI, no, or timeout denies. |

Tool exposure is S22-only: `/srv/s22` marker or explicit `enabled: true`; default is disabled. The daemon remains the second enforcement boundary. In particular audio unmute/nonzero volume is not provided. This feature does not grant shell/partition/module access.

## Install and enable (on the phone)

Copy this repo's tools tree to the phone and run as native host root:

```sh
sh tools/openunum-phone/install-device-plugin.sh
```

This copies only the plugin into `$C/root/.openunum/plugins/s22-device` (`C=/mnt/omarchy-trial` by default). Add the following to the chroot OpenUnum plugin config `$C/root/.openunum/plugins.json`:

```json
{"s22-device":{"enabled":true}}
```

Ensure s22d is listening on `127.0.0.1:8766`, `/srv/s22` exists, and the existing S22 touch UI confirmation service is running. Then restart OpenUnum (`s22-openunum stop && s22-openunum start`). Confirm discovery by checking plugin health/logs for generated tool count. Do not expose s22d on a non-loopback interface.

## Tests and rollback

Hardware-free fake-daemon tests:

```sh
node --test tools/openunum-phone/plugins/s22-device/test/plugin.test.mjs
```

Rollback: remove `$C/root/.openunum/plugins/s22-device` and restart OpenUnum. This leaves s22d, daemon config, and phone state untouched. No phone hardware was available during development; installation and behavior on device remain unverified.
