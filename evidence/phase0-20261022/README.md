# Phase 0 (stabilise) — 2026-10-22

Owner-approved after the external review. Phone out with the owner on mobile data the whole time,
in active use (screen on/off at random, charged once 10:30–10:55 UTC). All times UTC.

## 1. Wi-Fi saver (issue #71)

`s22-netpolicy` parks Wi-Fi while the screen is off AND mobile data is the uplink AND Wi-Fi is not
connected (60 s grace). Method `disconnect` (`wpa_cli disconnect`: wpa_supplicant stops scanning)
or `down` (`ip link set wlan0 down`). One bounded discovery every 15 min (disconnect: one manual
scan checked against the configured SSIDs; down: link up for 15 s). Unpark at once on screen on,
uplink change, kill switch (`s22-netpolicy saver off`), `s22-netpolicy off`, a dead-man restore
(1 h hold file) or daemon exit/restart. Default: `disconnect` (same effect as `down` on the wakes,
no driver up/down).

Locked + discharging + mobile only (`windows-summary.txt`, from `pull/trace` and `pull/autosleep`):

| Segment | Min | Drain | asleep | cycles | mean sleep | first wake reason |
|---|---|---|---|---|---|---|
| **Before** (#71, 06:55–07:17, Wi-Fi scanning) | 22.0 | 3.9 %/h (130 mA) | 27 % | 130 | 2.8 s | wlan 102, modem 19, abort 9 |
| Saver off today (11:39–11:51) | 11.3 | 5.0 %/h (167 mA) | 56 % | 28 | 13.5 s | modem 19, **wlan 6**, rtc 2 |
| **disconnect** (09:58–10:15) | 16.9 | **2.7 %/h (90 mA)** | 76 % | 40 | 19.4 s | modem 39, wlan 0 |
| **down** a (10:59–11:15, right after charging) | 15.7 | 3.4 %/h (112 mA) | 79 % | 28 | 26.6 s | modem 26, wlan 0 |
| **down** b (11:21–11:35) | 14.3 | **1.9 %/h (61 mA)** | 79 % | 31 | 21.8 s | modem 30, wlan 0 |

* Wi-Fi wakes went to **zero** in every parked window; sleeps are 7–10× longer; time asleep 27 % → 76–79 %.
* Drain roughly 2–3.4 %/h vs 3.9 (#71) / 5.0 (today, unparked). **Caveat:** none of the windows
  reached 20 min clean (the owner unlocked the phone, charged it, and local-model turns ran
  11:52–12:19); raw-SOC resolution is 0.01 % (≈0.33 mAh), so a 15-min window is good to roughly
  ±0.1 %/h on the step count but the gauge relaxes after charging (window "down a"). The wake
  numbers are solid, the drain numbers are indicative.
* Tailscale while locked + parked on mobile: rig `tailscale ping` answered at 10:09 (parked,
  disconnect: 872 ms first = wake, then ~190 ms via DERP), 10:35, 11:35 (869 ms wake) and 12:20 (55 ms direct).
  Unpark on screen-on seen 6 times in the trace (10:15, 10:28, 11:17, 12:37, 12:45 …), within one 5 s poll.
* Dead-man armed for both first parks and for the redeploy; cancelled after rig pings. Found and fixed:
  `s22-net-deadman` called `wpa_cli` without `-p /run/wpa_supplicant-s22`, so its "reconnect" never did anything.
* Found and fixed after the phone's 12:20 reboot: `ip route del` of the kernel line
  `default via 10.55.0.1 dev ecm0 dead linkdown` failed every 5 s (flags in the args); the del is
  now built from parsed fields (the route was removed at 13:16).

## 2. Hard per-turn limits (issue #75)

What OpenUnum already has: `runtime.chatHardTimeoutMs` / `agentTurnTimeoutMs` and a stale-pending
sweeper, and `/api/chat/cancel` (aborts the generation, plan, subagents; durable record so a restart
does not resume it). On the phone both timeouts are 1 728 000 000 ms (20 days, owner's no-guardrails
policy), they are global (cloud work too), and the sweeper spares a turn that still emits events.
`providerRequestTimeoutMs` is 1 h, `executorRetryAttempts` 3. So limits are enforced outside:

* **s22-turnguard** (new keepalive service): polls `/api/chat/diagnostics` every 10 s; voice
  sessions (`voice`, `voice-conv-*`) 300 s, shell/touch (`touch`) 900 s, others not limited here.
  Over → `/api/chat/cancel`; still pending 20 s later → re-cancel (3×) → `POST :8090/_s22/abort`.
  Retry budget: the same message in the same session timed out twice in 30 min → the next attempt is
  cancelled at once. Tracked turns + timeout history in `/srv/s22/state/turnguard/state.json` (survives
  guard and OpenUnum restarts). New: after a cancelled turn ends (and nothing else is pending) the
  OpenUnum server's child processes started after that turn began are stopped by exact PID.
* **s22-llama-ondemand**: streamed request must produce output within 150 s, any request ends at
  420 s → 504 + backend connection closed (llama.cpp cancels); 3 s later the backend's CPU is
  measured, > 30 % with nothing in flight → unloaded. Retry budget (same messages aborted 2× in 30 min
  → 503 at once). `budget.json`, `active.json`, `backend.json` survive a proxy restart: a new proxy kills
  the old one's backend (exact pid, cmdline checked) and counts its in-flight requests as failures.

Fault injection on the phone:

| Test | Result | File |
|---|---|---|
| Isolated proxy (:18090) + fake hung backend burning one core | attempts 1 and 2 cut at **30.3 s / 30.1 s** (limit 30 s) with 504; backend CPU 100 % during → **0.0 %** 4 s after | `fi-proxy.out` |
| Retry budget | attempt 3: **503 in 0.0 s**, backend not touched (CPU 0.2 %) | `fi-proxy.out` |
| Proxy SIGKILLed mid-request (backend that ignores disconnects, 108 % CPU) | orphan still burning after the kill; new proxy at start: `orphan_killed`, `interrupted: 1`; orphan gone | `fi-proxy.out` |
| Real OpenUnum turn, test class limit 45 s, agent runs `sleep 150` | `timeout_cancel` at 51.2 s (10 s poll), turn gone 4 s later; **but the shell child kept running** (`bash -c sleep 150`) | `fi-turn1.out` |
| Same after adding child reaping | cancel at 49.9 s, turn_end at 59.9 s, `reap_children` killed `sleep 40` (pid 15731) 0.6 s later | `fi-turn2.jsonl` |

Not verified on the phone: the real llama-server with an oversized prompt (`fault-inject.py real`):
s22-guardian held the local model the whole afternoon (BIG 76–79 °C, see below) and the proxy
correctly answered 503 "held". The stubborn-backend unload after an abort is verified by the host
test only (`test_stubborn_backend_is_unloaded_after_abort`).

## 3. 72-hour reliability trace

`s22-trace` (keepalive service "trace", nice 10): a sample every 60 s of awake time + events on
display/uplink/Wi-Fi-park changes; copies failovers, restarts, turn events, proxy aborts and errors
from the other daemons' logs; `/srv/s22/state/trace/trace.jsonl`, gzip-rotated at 1 MiB, 30 kept.
Started 09:50; **survived the 12:20 reboot** (keepalive started it at boot); keepalive restarted by
exact PID at 13:24 so it also supervises `turnguard`. Cost: 5 CPU-seconds in its first 63 min after
the reboot (≈0.13 % of one core); ~125 kB of JSONL per 3.4 h.
Rig side: `python3 tools/hardware/power/s22-trace-summary.py --pull` (drain per hour by display/uplink
state, suspend share, wake reasons, failovers, restarts, turn events, errors, boots).

## Incidents seen during the run (not caused by these changes, as far as the data shows)

* **Reboot at ~12:20.** Preceded by 27 min awake with local-model turns (busy 45–58 %, BIG 45 °C).
  `last_kmsg` holds only the new boot (RST_STAT 0x40000000); cause not determined.
* **Stuck `gst-inspect-1.0 x264enc` since 12:27** (started by an OpenUnum shell turn, cwd the Arch chroot):
  R state on the X2 core, kernel stack `alloc_contig_range` (CMA 492 kB free of 380 MB), ignores
  SIGKILL. It blocks every suspend ("tasks refusing to freeze") and keeps BIG at 76–79 °C, so the
  locked drain after the reboot (7.5 %/h in the trace) is not comparable. Only a reboot clears it.
* 10:13–10:20: phone unreachable ~7 min; mobile probes failed and netpolicy redialed while the screen
  was on (Wi-Fi already unparked). Coverage, not the saver.
