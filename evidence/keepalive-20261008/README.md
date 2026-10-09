# Always-on agent services and mute guard — 2026-10-08 (lead session)

`tools/hardware/s22-keepalive.py`, installed as `/srv/s22/hardware/bin/s22-keepalive`
(sha256 `289889c3…e4d9`), started at boot by `start-persistent-desktop` when
`/srv/s22/state/keepalive/enabled` exists (marker created; the new supervisor is
installed as `/usr/local/bin/start-persistent-desktop` and takes effect on the next
boot; backup `start-persistent-desktop.pre-keepalive-20261008`). The daemon was also
started by hand for this boot.

- **Services:** OpenUnum (`/health` on :18880; started with `s22-openunum start`) and
  unumsearch (TCP :7781).
  - A missing process is restarted at once.
  - A running but unhealthy process gets 180 s before a restart.
  - Restarts back off from 30 s to 10 min, with at most 5 per 30 min.
- **Mute guard:** every pass reads `/srv/s22/buttons/volume` and both CS35L41
  `Digital PCM Volume` controls. Anything above 0 is set back to 0 and logged
  (`enforce_mute`, on by default).
- **Device test (same boot):**
  - unumsearch: killed (pid 3239); restarted about 20 s later.
  - OpenUnum: `kill -9` of node (pid 7706); healthy again 22 s later (`up after 5s`).
  - After both: volume 0, amps `0/0`, default model still `openai/gpt-6-luna`.
- Log: `/srv/s22/state/keepalive/keepalive.jsonl`. Host tests: `tools/hardware/test_s22_keepalive.py`.
- **Rollback:** remove the marker, kill the `s22-keepalive` process, restore the
  `.pre-keepalive-20261008` supervisor.
