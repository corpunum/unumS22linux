# s22d — Galaxy S22 device daemon (phase 1)

`s22d` exposes a local JSON API for the S22 Linux phone. This first phase is host-buildable and reports hardware operations unavailable until their reviewed backend adapters are connected. It does not access a phone, initiate a radio/camera operation, or bypass the modem allowlist.

## Build and test

```sh
cargo test --manifest-path tools/s22d/Cargo.toml
flock /home/corpunum/.cache/rig-heavy.lock cargo build --manifest-path tools/s22d/Cargo.toml --release --target aarch64-unknown-linux-musl
```

Output: `tools/s22d/target/aarch64-unknown-linux-musl/release/s22d` (static musl aarch64). `S22D_AUDIT` and `S22D_SOCKET` allow test paths. Defaults: `/srv/s22/state/s22d/audit.jsonl`, `/run/s22d.sock`.

## API

HTTP listens on `127.0.0.1:8766`; the same API is exposed over `/run/s22d.sock`. Non-loopback TCP peers must be rejected. Mutating requests append JSONL audit records.

| Method | Path | Risk | Phase-1 behavior |
|---|---|---|---|
| GET | `/v1/capabilities` | read | capability inventory |
| GET | `/v1/status` | read | system summary; unsupported probe fields null |
| GET | `/v1/thermal` | read | thermal zones; adapter pending |
| GET | `/v1/processes/top` | read | top process summary; adapter pending |
| GET | `/v1/wifi/status` | read | Wi-Fi status; adapter pending |
| POST | `/v1/wifi/scan` | read | request/audit; adapter pending |
| POST | `/v1/wifi/connect`, `/v1/wifi/disconnect` | reversible | adapter pending |
| GET | `/v1/bt/status` | read | Bluetooth status; controls report disabled |
| GET | `/v1/modem/status`, `/v1/sms` | read | phoned proxy pending |
| POST | `/v1/sms/send`, `/v1/call/dial` | risky | phoned proxy pending; allowlist remains authoritative |
| POST | `/v1/camera/capture` | read | `{sensor: rear|front, exposure_us?, gain?}`; reviewed camera adapter pending |
| GET | `/v1/camera/status` | read | camera availability |
| GET | `/v1/display` | read | display status; adapter pending |
| POST | `/v1/display/on`, `/off`, `/brightness` | reversible | adapter pending |
| POST | `/v1/audio/volume` | reversible | nonzero refused unless `/etc/s22-audio-unmuted` exists |
| POST | `/v1/services/{name}/restart` | reversible | allowlist: `openunum`, `unumsearch`, `modem`, `phoned`, `llama` |
| POST | `/v1/system/recovery` | risky | operation adapter pending |
| GET | `/v1/logs/dmesg?since=` | read | adapter pending |

Error response: `{ "ok": false, "error": "...", "code": "..." }`. Bluetooth scan/power remain unavailable with reason exactly `HCI raw-socket kernel panic: disabled`.

## Phone deployment note (phone owner-agent)

Copy the built binary to `/srv/s22/hardware/bin/s22d` on the native phone root and supervise it with `s22-keepalive`, not systemd. Add its service definition in `/srv/s22/state/keepalive/config.json` with exact process needle, binary `requires`, and a dedicated log. Deploy/verify on the phone: check loopback and socket access, inspect the audit JSONL, and confirm nonzero volume is refused. No phone deployment evidence is included in this source phase.
