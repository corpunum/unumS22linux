# S22 mobile data — durable wrap-up (2026-10-14)

## Original task / definition of done

Continue from `49da8ea`: finish `ipc-send` + data-call controls, then obtain mobile data over rmnet with interface-bound curl and ping, OR exhaust the agreed variants with actual raw TX/RX and modem reply codes recorded. That definition of done is **not met**. The owner subsequently requested wrap-up: commit completed work, write a durable handoff, report commits/files/tests and remaining work, preserving side effects without replaying mutations. This checkpoint follows that wrap-up instruction; no new modem experiment was performed.

## Repository and saved work

Assigned checkout: `/home/corpunum/.openunum/worktrees/s22-mobile-data`, branch `s22/mobile-data-20261013`, remote `https://github.com/corpunum/unumS22linux`. Its `.git` points to `/home/corpunum/s22deploy/repo/.git/worktrees/s22-mobile-data`; it is NOT the same git object store as `/home/corpunum/s22-linux`, despite their similar branch names. Use the assigned checkout. Do not merge.

At resume the checkout was clean at `49da8ea4b03bba2ef322c0484eacbf098b19c786`; remote branch was `0e770d1afd99786cc2bab53c7d876b1a79dded5b` (verified by `git ls-remote`). Completed source work was already committed, not uncommitted:

- `f8ff242`: experimental sender + synthetic tests, initially WIP.
- `49da8ea`: stricter matching, sequence-exhaustion guard, GET ACK vs RESP, timeout alignment, CLI/HTTP and bounded notification tests; README and host regression allowlist.
- Earlier `e07066a`, `0e770d1`: reconnaissance/disassembly notes.
- `20acf12`, `ba4bad1`, `1d884ff`: read-only IPC GET implementation, JSON decoding and exposed fields.

Saved implementation files: `tools/hardware/phoned/s22-phoned.py`, `test_ipc_experiments.py`, `README.md`; `tools/hardware/run-host-regressions.py`, `tools/hardware/test-host-regression-runner.py`; `RECON.md`. This wrap-up adds only this handoff and a concise RECON checkpoint. No implementation changes were made in the wrap-up.

## Live state verified, 2026-10-14 14:55 UTC

SSH: `root@100.95.191.103`. Phone muted: `sh /srv/s22/state/touchui-mutecheck.sh` returned `level=0`, left/right Digital PCM Volume `0`. CP `/sys/devices/platform/cpif/modem_state`: ONLINE. Uptime 37062 seconds. TCP 8095 belongs to phoned PID **2210**; TCP 8766 belongs to s22d PID **2294**. Always re-check exact PID before a future restart.

`/proc/2210/cmdline`: `python3 /srv/s22/hardware/bin/s22-phoned serve`. Deployed binary SHA256 **11ce72e9c4e113e0a1304917f7890171d7c2ca3735cf291291488953c56e8762**, matching the source at **20acf12**, NOT `49da8ea` (source SHA256 `811df3d632bc89dee041870c03c075d6495477d9d499a0ce420f05a0ddef1afd`). No experimental sender is deployed. Initial guessed `/usr/local/lib/s22/s22-phoned.py` path was absent; actual executable path was then resolved via `/proc`.

`GET http://127.0.0.1:8095/status`: ONLINE, ipc_open=true, sms_ready=true, SIM PB_INIT_COMPLETE, Vodafone GR, registration EGPRS/home_3gpp, service domain ps, no calls, simulated=false. rmnet0–7 DOWN. rmnet0 RX/TX bytes and packets all zero. No rmnet IP/curl/ping success is claimed.

Preserved links: ecm0 UP, wlan0 UP; tailscale0 operstate UNKNOWN but UP/LOWER_UP flags (normal tunnel state). Defaults: `default via 10.55.0.1 dev ecm0`; `default via 192.168.50.1 dev wlan0 metric 600`. Table 52 tailscale routes were read, not changed.

## Recorded modem side effects / avoid replays

Both retained logs were parsed row-by-row, not inferred from command-name grep:

- `/srv/s22/state/phoned/ipc.jsonl.1`: 10,687 valid rows, 0 malformed; 4,194,569 bytes; timestamp range 1791921038.865–1791958239.584. Includes one historical preferred-mode GET and one historical PDP-context GET.
- `/srv/s22/state/phoned/ipc.jsonl`: 9,085 valid rows, 0 malformed at read; 3,745,110 bytes; timestamp range 1791958239.587–1791989751.231. TX consists of periodic NET_REGIST, NET_CURRENT_PLMN and DISP_RSSI_INFO only.
- No retained TX to experimental data commands `(02,03)` or `(0d,01/03/04/09/0a/14/1b/1d)` except the historical `(0d,04)` GET. No matching experimental GEN reply was observed. Rotation limits historical coverage; do not claim proof of all-time absence.

Historical actual TX/RX (not re-sent in this checkpoint):

1. Preferred mode GET TX `0700c700080a02`, mseq=199, t=1791923344.622. RX payload `7b226d6f64655f73656c656374223a37357d`, mseq=66, aseq=199, type=2, t=1791923344.625; decoded `{"mode_select":75}`. Reconstructed full RX from recorded header fields: `190042c7080a027b226d6f64655f73656c656374223a37357d` (log stores payload, not full RX).
2. PDP-context GET TX `0700c8000d0402`, mseq=200, t=1791923345.226. RX payload `7b226e756d5f616374223a302c227265636f726473223a5b5d7d`, mseq=67, aseq=200, type=2, t=1791923345.229; decoded `{"num_act":0,"records":[]}`. Reconstructed full RX: `210043c80d04027b226e756d5f616374223a302c227265636f726473223a5b5d7d`.

This wrap-up performed no deploy, process kill, modem SET/GET request, call, SMS, package install, link/route change or reboot. It invoked the required mute check and read status/logs only. Earlier deployment/restarts and GETs are historical side effects documented in `/home/corpunum/openunum-briefs/s22-ipc-formats.md`; do not repeat them blindly. No task recovery reboots are recorded (original budget 0/2), but independently reconcile any owner/other-worker reboots before spending recovery budget.

## Verification

- `git diff --check`: passed before documentation changes.
- Host regression suite and remote CI results: see final verification checkpoint below (filled after commands complete). Do not infer live modem acceptance from synthetic tests.
- This is the S22 repository, not OpenUnum; OpenUnum's `pnpm verify` does not apply. The S22 hardware-free workflow runs runner-policy tests, Node s22-device plugin tests, and the explicit Python host suite in normal + optimized modes.

## Remaining work / restart procedure

1. Preserve safety rules below; re-read this handoff, `git log -8`, `RECON.md`, `/home/corpunum/openunum-briefs/s22-ipc-formats.md`, and `s22-mobile-data-report.md`. The latter is an older read-only stop report, not current completion evidence. Earlier speculative 0x0b group notes in RECON are superseded by verified stock builders using 0x0d and 0x02.
2. Re-check deployed SHA, daemon PID and all retained ipc logs before sending anything. No bounded experiment variant matrix is yet recorded; do not claim all variants tried.
3. Review exact schemas from stock RIL before APN/profile payloads; relevant builder addresses are in RECON and the external formats brief. NET preferred-mode SET is not allowlisted; observed wire 0x4b does NOT prove an LTE/NR semantic label. Do not guess mode meanings.
4. Back up `/srv/s22/hardware/bin/s22-phoned` and state before deploying the tested sender. Restart only its verified exact PID (not node :18880), let the existing supervisor restore it, verify readiness and deployed hash. A deploy/restart can induce bootstrap traffic: capture that separately.
5. Continue the approved bounded one-frame-at-a-time data experiments with >=30 s separation, raw actual sequenced TX, raw RESP/GEN and code, timeout without retries, notification capture. Exact known stock data-call on/off selectors are `(02,03)` SET payload `01`/`02`; acceptance alone is not working data. If CP leaves ONLINE or SMS readiness fails, stop the experiment and use emergency data-call off if IPC remains open.
6. Verify IP/DNS/CID/interface mapping from actual data notifications before configuring any rmnet interface. Preserve existing ecm0 and tailscale routes and never down wlan0. Prove ping and curl explicitly bound to rmnet; otherwise record the actual rejection/timeout matrix, not fabricated reply codes.
7. Higher-level API/plugin work, mobile-data/failover/Tailscale proof and any PR remain unfinished. Commit+push after each completed step, watch CI for each pushed commit; do not merge.

Safety rules: phone muted; ecm0/tailscale0 up and routes untouched; never down wlan0; exact-PID kills only, no pgrep/pkill -f inside SSH; no phone package installs; no calls; SMS only to allowlist; recovery reboot only `setsid /usr/local/sbin/s22-reboot recovery` if modem breaks, max 2; no merge.

## Final verification checkpoint

Observed before the wrap-up commit:

- Focused host check via `verify_by_running`: 33 phoned tests + 10 experiment tests passed; `git diff --check` passed. A non-failing simulated Unix-socket ResourceWarning occurred.
- Runner policy: 7 tests passed. Node s22-device plugin: 23 tests passed.
- Full assigned-worktree host suite `python3 -I -B tools/hardware/run-host-regressions.py --mode both`: exit 1, **2 failures**, both the same unrelated BT local-fixture test in normal and optimized mode: `test_exact_local_preflight_passes_without_creating_output_when_present`, refusal `pinned source commit is not an ancestor`. No source was changed to bypass the safety refusal. Full output: `/home/corpunum/.openunum/shell-jobs/job-1791989874496-nepu9h.log`. Phoned 33+10 passed in both modes.
- An attempted clean `git archive HEAD` checkout ran the identical runner-policy, Node and normal+optimized host workflow, but did NOT isolate the absolute local fixture paths. It also exited 1 with the same two BT local-fixture failures, this time `cannot identify source worktree HEAD` because the archive has no git metadata. It is NOT a successful CI-equivalent check. Job `job-1791990302385-ggba7m`, full log `/home/corpunum/.openunum/shell-jobs/job-1791990302385-ggba7m.log`. No fixture/source gate was disabled; further local fixture repair is outside this wrap-up.
- Commit `4c13ddd` pushed the saved `f8ff242`/`49da8ea` work and this handoff/RECON checkpoint to `s22/mobile-data-20261013`. Remote CI for `4c13ddd` completed successfully in BOTH runs: push `38586301479` and existing PR #54 `38586304552` (Hardware-free host regressions). No PR was created or merged.
- This correction/checkpoint will be committed and pushed separately. Final HEAD, remote CI and clean-status proof will be recorded in the session-local completion receipt and final response after observation. Do not substitute the green remote gate for the failed local fixture gate.
