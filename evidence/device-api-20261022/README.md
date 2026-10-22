# Device API (s22d `/v1/device`) on the phone, 2026-10-22

s22d 0.3.0 (static aarch64, md5 f93f3fa5…) deployed over the keepalive service; s22-phoned with the one-shot
grant gate; s22-device plugin 0.3.0 (`phone_device` tool). Backups: `/srv/s22/state/deploy/devapi-20261022T154518/backup/`
and `/srv/s22/hardware/bin/s22d.pre-devapi-20261022`.

| File | What was verified live |
|---|---|
| `01-settings-live.txt` | brightness.set 40 -> sysfs 204 read back; volume.set 5 / mute / unmute restores 5; vibration off -> haptic.play is a reported no-op, on -> pattern played (daemon `ok`); autosleep off/on (flag + `s22-autosleep status` agree); screen_timeout.set 120 -> shell's file; calls.set off -> call.dial refused `calls_disabled` (nothing dialled); mobile_data.set off refused `would_strand` (no Wi-Fi, no USB); mobile_data.set on -> failover/up; screenshot -> `unavailable` (deployed shell predates the hook); unknown argument -> 400. Pre-test values restored. |
| `02-bluetooth.txt` | deadman armed; bluetooth.set off (hci0 gone) and on (hci0 back in 4 s); Tailscale ping from the rig OK throughout. |
| `03-wifi.txt` | deadman armed; wifi.set off -> DISCONNECTED, saver kill switch held, still DISCONNECTED 40 s later; wifi.set on -> SCANNING (no known network in range here, so the uplink stayed mobile the whole time; failover itself was not exercised), switch released; Tailscale ping OK. |
| `04-phoned-and-sms-confirm.txt` | phoned restarted with grant support (modem ONLINE, sms_ready in 9 s, mobile data stayed up); sms.send to the short code 12345 (phoned refuses short codes anyway) put the confirm card up, nobody tapped -> `needs_confirmation` after 60 s, no grant written, audited with text length only. |
| `05-camera.txt` | camera.capture (rear) -> PNG + gallery copy in 1 s. |
| `06-agent-e2e.txt` | POST /api/chat on session `devapi-test-20261022`: "set brightness to 40 percent" -> agent called `phone_device` brightness.set value 40 (audit caller `agent`), sysfs 204, reply "phone reports brightness at 40%". Brightness restored to 128 after. |

Not tested live: call.dial (owner: not now), sms.send to a real/allowlisted number, the grant being accepted by phoned
(unit tests only), screenshot capture (needs the unum-fastshell build with the `screenshot` hook deployed).
