# Independent audio deadline-completion review — 2026-10-03

## Verdict

**PASS_LIMITED for the frozen host-side observer and its explicit acceptance
contract.** The provisional receipt is not sufficient by itself; timely
acceptance requires the active stability operation, exact complete/success
marker identity, result path and digest, and a final monotonic check before the
600-second cutoff. Late receipt persistence and late marker completion/readback
do not return acceptance. A synthetic delay after lock cleanup does allow the
method and CLI to return success at fake time 601, but the acceptance check and
marker were already timely. This is return/cleanup latency, not an overrun of
the remote observation bound.

This is host-only evidence and makes no phone, runtime, bootability, or audio
hardware claim. It also does not guarantee that local writes, `fsync`, marker
I/O, lock release, descriptor close, method return, or CLI output complete
within 600 seconds.

## Frozen inputs and scope

Review ran on branch `codex/s22-audio-deadline-review-20261003`, frozen HEAD
`9472c8b1dbf141686ff8c26b15b7904602009bed`, corresponding to author commit
`fb7f5c5ab86b1c8dd3cc6175d10ca3d689426169`. The frozen observer SHA-256 is
`68827be195abf9afbe176b6af7261f9153cc4c435d539d98f1706c2af5f34dce`; its
42-case test file SHA-256 is
`436ef4f97c3ac9b934b7b2ec379d266c89cfef27a41222cc563b1c74325a8cb5`.
The author's receipt SHA-256 is
`4ba6dc761beae7e1a4ae383b84818efef1a4d56ac1e627a38bc4800d35a8c7ef`.
The preceding independent remote-freshness source and test hashes remain
recorded there as `eee1e2a467bf1df57325966b6573e1a8dc5dd031d71ef0e8088bd23272f349ea`
and `8cbfab6fcee0da24b93ed0d75af5b66b7cc09b32c21a24f1997af15607eccca4`.

I reused, rather than reran, the preserved pre-deadline-fix counterexample in
`evidence/s22-audio-deadline-completion-host-20261003.json`: the frozen observer
could persist its result, complete the matching marker, and return success at
601 seconds. The previous remote-freshness review, its receipt, and other
historical records were not edited.

After the source review, I read the separate documentation-only clarification
commit `1239c02cd5419276e89960ef89dd8eaf0f394c58` (clarified document SHA-256
`efd375f8916ac0e0e0c2c899c6f4aaeadfdefcb7fc82fdaaee9762dc2bad68f6`). It
accurately narrows the claim to local receipt/marker finalization before the
acceptance point and explicitly excludes lock cleanup, descriptor close,
method return, and CLI output from a 600-second return-time guarantee. That
document-only addendum does not change the source/test verdict below.

## Independent execution

All three invocations of the complete 42-test host suite passed:

- `AUDIO_EXPECT_PYTHONOPTIMIZE=0 python3 -B tools/hardware/test-audio-coherent-trial-adapter.py -q` — 42 passed; optimization flag 0.
- `AUDIO_EXPECT_PYTHONOPTIMIZE=1 python3 -O -B tools/hardware/test-audio-coherent-trial-adapter.py -q` — 42 passed; optimization flag 1.
- `AUDIO_EXPECT_PYTHONOPTIMIZE=1 PYTHONOPTIMIZE=1 python3 -B tools/hardware/test-audio-coherent-trial-adapter.py -q` — 42 passed; optimization flag 1.

The suite includes forward and reverse synthetic success, exact receipt/marker
digest binding, same-boot advancing uptime, per-gap host/remote reconciliation,
and at least 180 seconds in both host and remote intervals. Success fixtures
sampled at host offsets `[0, 90, 195]` and remote uptimes approximately
`[180.1, 270.1, 375.1]`, recording approximately 195 seconds for both totals.
It also covers replayed/decreasing and implausibly slow uptime, nonfinite,
boolean/string and out-of-range values, changed boot, and a 165-second remote
interval against a 195-second host interval; these remain `UNKNOWN`, with
`retry_allowed=false`. The unchanged quiet current-ring `dmesg` fingerprint is
accepted only with the other readiness and identity checks.

I ran additional temporary-filesystem/fake-clock controls against the actual
observer and CLI:

- Delaying final completed-marker readback until fake time 601 raised
  `TimeoutError`; the matching marker was changed to `unknown`/`UNKNOWN`, and
  the original completion was nested for audit.
- Late-completion demotion refused to update a completed marker with a wrong
  receipt path, wrong digest, wrong operation kind, or replaced inode. Each
  case raised `TerminalObservationError` and left the deliberately altered
  marker bytes unchanged.
- With every UNKNOWN marker write forced to fail after a late completion, the
  observer raised `RuntimeError` and returned no success. As expected under
  failed local durability, the temp marker remained `complete`/`success` and
  referenced the provisional receipt. This is an unresolved on-disk state that
  requires separate inspection; a consumer must not infer timely acceptance
  from that marker alone.
- Delaying `_LockedOperation.__exit__` until fake time 601 *after its real
  cleanup* produced a timely matching completion, then a method result with
  `finalization.accepted=true`; the actual CLI printed that result and returned
  0 at 601. The deadline/readback checks occur before this context cleanup.
  Therefore this control does not violate the remote observation bound, but
  proves that method/CLI return latency is unbounded by the implementation.

The deadline control uses a 600-second total, final sample start no earlier
than 195 seconds, query caps of 3/15/30 seconds, and one read-only reconnect.
The prior preserved independent fake-clock worst path is 515 seconds, with a
conservative documented remote query/wait allowance of 520 seconds. The
deadline-fix source changes only finalization/acceptance handling, not that
remote sampling schedule. The default plans still report the 600-second total,
180-second minimum remote interval, and 195-second final sample minimum.

## Guard, identity, and regression checks

All four default plans (deployment and observer, forward and reverse) exited
0 and reported authorization `false`, an empty execution allowlist, and no
execution, marker creation, partition write, or reboot. The suite preserves
the old stability receipt and consumed marker, refuses same-trial replay
without a transport call, keeps unresolved UNKNOWN reboot markers unchanged,
and exercises the nonzero CLI path for an unbound provisional result. The
production one-shot request and initial-observation functions are outside the
changed source hunks; the inactive plan path is unchanged. The only CLI hunk
adds nonzero exit for an unaccepted stability result. No NPU operation or
authorization path is introduced or modified.

Plan validation retained the baseline image SHA-256
`b10412715756da3cc8ee221368b49f179cc0c64ab7bd2802976480905e6d8d2f`, candidate
image SHA-256
`6b788b23f54b9b8e84212187544064949b72af20c167cbb02392cbe53ee5a6ab`, and
candidate manifest SHA-256
`f78bbaddf94ef6d1ae37b059b7455b24a15cbb7c2fab1ec9bbc4e1df5fd1edf8`. The
kernel GNU Build ID remains `b2dda820b18d410d9bf12f1bd2584567d545991d`. The
forward postboot module IDs remain ABOX
`26347c3373e155fa6badf7883ff162f1d9f6723f`, rainbow
`8a7227b58cb7f4faf73ea92974781d34870bbfac`, and offloader
`8c9b0d4787ea32eae7de0086a4d662b0f351675d`; reverse targets the baseline IDs
`34a5354a75980688ee7dbeb6a848e04a7d54558f`,
`510b984887b640ad4ad3c1e9a556ef16c30b38c9`, and
`4f35c50b0eca0d22b2060f7b8d84f03359feacd1`.

The implementation's success consumer in this repository is the observer CLI:
it prints the returned finalization and exits 0 only when
`finalization.accepted` is exactly `true`. I found no separate in-repository
stability receipt/marker parser that accepts `complete`/`success` alone. A
permanent local demotion failure can leave that marker on disk, so marker-only
interpretation is explicitly outside the supported acceptance contract.

No SSH, ADB, USB, phone, device, CI, reboot, partition, service restart,
inference, image build, or hardware operation was performed. The worker was
explicitly selected as `gpt-6-luna/max`; this is a record of requested worker
selection, not attestation of the inference backend. No credentials, boot IDs,
network identifiers, private traces, or images are included.
