# s22-phone skill (install notes)

An OpenUnum skill that lets the rig agent use the S22 through its device API (`tools/s22d`, see `DEVICE_API.md`).

## Install on the rig

1. Copy this folder to `~/.openunum/skills/custom/s22-phone/` (SKILL.md, _meta.json, execute.mjs; tests.mjs optional).
2. Phone address, outside the repo: put the base URL (`http://<phone>:<port>`, one line) in `~/.config/unum/s22-device.url`, or set env `S22_DEVICE_URL`.
3. Token: put the bearer token in `~/.config/unum/s22-device.token` and `chmod 600` it. The skill refuses any file readable by group or others, and never logs or returns the token.
4. Check: `skill_execute` with `{"name":"s22-phone","args":{"action":"catalog"}}`.

## Notes

- `S22_DEVICE_TOKEN_FILE` overrides the token path; `S22_PHONE_TEST=1` enables the test-only args `_timeout_ms` and `_poll_ms`. Do not set either in production.
- Photos go to `~/.openunum/private/s22/` (created 0700; refused if a symlink, not yours, or group/other accessible; override with env `S22_PHONE_PRIVATE_DIR` for tests). Files are 0600; the agent removes them with `forget`.
- The skill declares `needs: ["turn"]` and checks `context.turn.ownerProven === true` for photo, listen, say, task, catalog and haptic.
- Calls and SMS are refused client-side (paused); `uplink.share` is never called.
- Tests: `node tools/openunum-rig/skills/s22-phone/tests.mjs` (fake phone on 127.0.0.1, no real phone, no real token).
