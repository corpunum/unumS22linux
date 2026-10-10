# s22d

The Galaxy S22 device daemon: a small static Rust binary (about 1.3 MB, about 2 MB resident) that gives the phone's agent, the touch shell and
you one local JSON API for status, Wi-Fi, modem/SMS/calls, camera, display, audio policy, service restart and
recovery reboot.

**The contract is [`API.md`](API.md).** `capabilities.json` is its machine-readable snapshot and the plugin's contract tests use it.

Every backend is a trait (`src/backends.rs`) with a phone implementation in `src/real/` and a fake in the tests.
Policy that belongs to the daemon (owner confirmation for risky calls, the audio flag, the service allowlist, the camera lock,
loopback-only access, the audit log) lives in `src/routes.rs` and `src/app.rs`.
`s22-phoned` keeps its own allowlist and rate limits; `s22d` only forwards.

## Build and test

```sh
export PATH=$HOME/.cargo/bin:$PATH
cargo test --manifest-path tools/s22d/Cargo.toml              # hardware-free, fixture trees and fakes
UPDATE_CAPABILITIES=1 cargo test --manifest-path tools/s22d/Cargo.toml   # after changing src/catalog.rs
flock /home/corpunum/.cache/rig-heavy.lock \
  cargo build --manifest-path tools/s22d/Cargo.toml --release --target aarch64-unknown-linux-musl
```

Output: `tools/s22d/target/aarch64-unknown-linux-musl/release/s22d` (static). CI builds it and uploads it as the
artifact `s22d-aarch64-unknown-linux-musl`.

## Deploy (phone owner-agent)

Order for the whole mobile stack:
**s22d, then the `s22-device` plugin, then unum-shell** (see `tools/unum-shell/README.md`).

```sh
# on the phone, native root, as root
sh tools/s22d/install-s22d.sh --binary /path/to/s22d
curl -s http://127.0.0.1:8766/v1/capabilities | head -c 400
tail -n 5 /srv/s22/state/s22d/audit.jsonl
```

The installer backs up `/srv/s22/state/keepalive/config.json`, installs `/srv/s22/hardware/bin/s22d`, registers it as a
keepalive service (health: TCP 8766) and starts it once. It does not touch any other service.
Rollback: `sh tools/s22d/install-s22d.sh --rollback`.

Verify on the phone:

| Check | Expect |
|---|---|
| `curl -s 127.0.0.1:8766/v1/status` | real battery, thermal, memory; `keepalive: true` |
| `curl -s 127.0.0.1:8766/v1/capabilities` | `wifi.*`, `modem.*`, `camera.*`, `display.*` available; `bt.power`, `bt.scan` not |
| `curl -s -XPOST 127.0.0.1:8766/v1/audio/volume -d '{"value":5}' -H 'content-type: application/json'` | 403 `policy_denied` |
| `curl -s -XPOST 127.0.0.1:8766/v1/services/sshd/restart` | 403 `not_allowed` |
| `curl -s -H 'Origin: http://x' 127.0.0.1:8766/v1/status` | 403 `browser_origin` |
| `ps -o rss,args` for s22d | well under 20 MB resident |

Risky calls (`sms.send`, `call.dial`, `system.reboot-recovery`) put a yes/no card on the phone and block until the owner taps.
Test them only with the owner present.

Status of the phone-side evidence: this source tree was built and tested on the rig only. Nothing here has run on the phone yet.
