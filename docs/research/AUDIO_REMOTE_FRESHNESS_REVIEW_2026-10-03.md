# Independent audio remote-freshness review — 2026-10-03

## Verdict

**BLOCKED for a hard 600-second end-to-end deadline claim.** The freshness correction passes the independent host controls below, including both profile directions and the replay, uptime, identity, and interval failures. A separate fake-clock control shows that the observer can still return `bounded-stability-observed` after the configured 600-second deadline when final success-receipt persistence crosses that deadline.

This is host-only evidence. It establishes no phone runtime, boot, powered-audio, or hardware acceptance.

## Frozen inputs and preservation

Review ran in `/home/corpunum/s22-workers/audio-freshness-review-20261003`, branch `codex/s22-audio-freshness-review-20261003`, at review head `ea39e48cb344865fb37ed98a040a0d0ee09dc466` before adding this review. The corrected observer was authored in commit `3f64b3d6e696690923da58ae4bfcc35d5101d0ab`; its imported review head is `ea39e48cb344865fb37ed98a040a0d0ee09dc466`.

The corrected observer and test source hashes matched the frozen receipt: observer `eee1e2a467bf1df57325966b6573e1a8dc5dd031d71ef0e8088bd23272f349ea`, tests `8cbfab6fcee0da24b93ed0d75af5b66b7cc09b32c21a24f1997af15607eccca4`. I verified the older blocked review and observer receipt remained byte-for-byte at their recorded SHA-256 values (`1c606da8…8027b` and `5c8d6827…20247`). The source diff from the pre-correction review head changes only the observer, its test file, the implementation note, and the new author receipt. The one-shot reboot and initial-observation functions are outside the changed hunks.

## Reproduced pre-correction failure

I extracted frozen observer commit `da92be3561449632c7a94e6ca198c218a47cda10` into a temporary directory and executed its actual `observe_stability()` with the preserved synthetic filesystem and fake-transport fixture. The exact frozen observer SHA-256 was `3dadaad14953a67339479b534ddee11710463bc7f4382d916899a0924e5b5f80`.

The fake transport returned the same readiness payload (remote uptime `240.0`) and same full identity payload at host sample starts 0, 90, and 180 seconds. The old observer returned `bounded-stability-observed`, with three samples and a 180-second host interval. It issued one startup, three readiness, and three identity queries. The synthetic one-shot reboot fixture was invoked once; no device connection was possible in this harness.

## Corrected observer controls

I executed the corrected `observe_stability()` directly with its existing fake filesystem and fake clock. Forward and reverse success each recorded sample starts at `[0, 90, 195]`, host gaps `[null, 90, 105]`, and remote uptimes around `[180.1, 270.1, 375.1]`. Both final receipts had host and remote intervals of approximately 195 seconds, with all per-gap remote/host differences within the 15-second capture-skew bound. Each used three readiness and three identity queries plus one startup probe. The same quiet synthetic `dmesg` SHA-256 across samples was accepted.

The success fixtures use a 4,096-byte synthetic partition and patch the image hash to a fixture hash. Their success receipts therefore do not establish the production image digest. Separately, both default plans validated the production baseline/candidate mapping and the candidate manifest. The pinned kernel GNU Build ID was `b2dda820b18d410d9bf12f1bd2584567d545991d`. Forward targets the audio candidate module IDs; reverse targets the baseline module IDs. The exact image and module mappings are recorded in the JSON evidence.

The corrected observer returned `UNKNOWN`, created no success receipt, and set `retry_allowed=false` for replayed and decreasing uptime, NaN, positive infinity, negative and out-of-range values, boolean and numeric-string values, a changed boot ID, and a 165-second remote interval paired with a 195-second host interval. The 37-test suite also covers implausibly slow uptime progression, wrong image/module IDs, and trace/fault/readiness failures.

### Deadline blocker

The code checks the deadline before final receipt creation at `tools/hardware/audio-coherent-recovery-reboot-once.py:1255`, then persists the success receipt and completes the global marker at `:1301-1306` without another clock check. I wrapped the actual `SHARED.persist_receipt()` in the synthetic fixture so it advanced the fake monotonic clock to 601 seconds only after writing the final result. The actual observer returned `bounded-stability-observed` and left a success receipt at `t=601`, despite `STABILITY_TOTAL_DEADLINE_SECONDS == 600`. This witness delays local finalization after the remote samples have finished; it does not show that the remote observation queries or 195-second sample interval exceeded 600 seconds.

The returned success is consumable: `operation.complete()` writes the global marker with `status=complete`, `outcome=success`, and the final receipt path and matching SHA-256. The CLI then prints the result and exits 0 (`:1427-1434`). A same-trial rerun is refused as already consumed. I found no separate stability-result consumer in this repository.

The remote query/wait schedule itself fits the requested 600-second observation bound. I ran capped fake-clock paths with 13 uptime probes, both startup-readiness polls, 15/30-second readiness/identity caps, and the one allowed reconnect. A reconnect on the final startup probe completed successfully at 488 seconds; a reconnect on the final identity query completed at 515 seconds. Both used final sample offset 195 and recorded 195 seconds of host and remote interval. The documented conservative query/wait allowance is 520 seconds, leaving 80 seconds before 600 for local work. Those paths do not cover a slow final receipt or marker write. The blocker is specifically that the observer can publish and return an unqualified success after 600; the current receipt/marker does not identify that late finalization.

The sample field `remote_uptime_valid` is a parse-level indicator for exact finite numeric values. Negative and out-of-range finite values are rejected with `ambiguous_response`, but terminal sample receipts still set that field to `true`; readers must also honor the terminal status/category. This does not produce a successful stability result, but the field name does not express the full accepted range.

## Guards, plans, and test matrix

The complete 37-test host suite passed in all requested modes:

- `AUDIO_EXPECT_PYTHONOPTIMIZE=0 python3 -B tools/hardware/test-audio-coherent-trial-adapter.py -q` — 37 passed; `sys.flags.optimize == 0`.
- `AUDIO_EXPECT_PYTHONOPTIMIZE=1 python3 -O -B tools/hardware/test-audio-coherent-trial-adapter.py -q` — 37 passed; `sys.flags.optimize == 1`.
- `AUDIO_EXPECT_PYTHONOPTIMIZE=1 PYTHONOPTIMIZE=1 python3 -B tools/hardware/test-audio-coherent-trial-adapter.py -q` — 37 passed; `sys.flags.optimize == 1`.

Both deployment plans and both observer plans exited 0 with authorization false, empty allowlist, and execution, marker creation, partition write, reboot, and hardware acceptance fields false. The suite also passed its no-authorization dispatch controls, UNKNOWN reboot-marker preservation/no-reissue check, prior one-shot receipt and global-marker replay refusal, and initial-observation binding tests. No SSH, ADB, USB, phone, service, partition, build, or hardware operation was performed.

The limited pass is for the remote-freshness acceptance logic and its tested host controls. The overall implementation remains blocked until the owner resolves whether final local persistence belongs inside the claimed 600-second deadline and the success/guard state is made consistent with that decision.

Detailed hashes, actual controls, and sanitized execution results are in `evidence/s22-audio-remote-freshness-review-20261003.json`.
