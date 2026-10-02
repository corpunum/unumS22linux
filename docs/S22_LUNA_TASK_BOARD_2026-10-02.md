# S22 continuation — 2026-10-02

Integration remains on `s22/luna-driver-completion-20260923`; starting published
HEAD was `d5532950994fa4cc1444051ffcb90cc6394912b7`. Fetch found no divergence.
The original master checkout and its five tracked modifications are preserved.
The rig restart removed the old temporary integration checkout; committed
history and the durable worker worktrees survived. Integration now uses
the durable `s22-workers/integration-20261002` beneath the rig home. No old worktree record or
historical receipt was deleted, reset or replayed.

## Actual workers

The daemon restart also removed active collaboration sessions. These workers
were newly launched through the supported native interface, explicitly using
`model=gpt-6-luna`, `reasoning_effort=max`, `fork_turns=none`. Evidence is accepted
explicit configuration with no known fallback, not independent backend identity
attestation. The coordinator runtime was not changed by a natural-language
instruction. Only the coordinator accesses the phone.

| Worker | Durable worktree | Narrow deliverable | Result |
| --- | --- | --- | --- |
| `/root/tz_progress_recovery_20261002` | `tz-progress-20261001` | Bounded native procfs progress collector, executed fake-procfs tests, source-wait documentation | Author `50966c3`, `2fbeb12`, `290c03e`, `ff78bb8`; integrated through `4f52e0e`; 17 tests in each Python mode; independent review cleared one read-only capture |
| `/root/npu_transaction_recovery_20261002` | `npu-transaction-20261001` | Transactional ref get/put source patch and actual extracted-C host regressions | Author `58f320e`, `9957257`; integrated `14dac07`, `8246b2f`; independent source review passed; BOOTUP still refused |
| `/root/audio_capture_recovery_20261002` | `audio-capture-20261001` | Executable logging/capture profile validation and regressions | Author `76a0ed1`, integrated `0c681a9`; coordinator review corrections through `5ea67c8`; 9 tests in each Python mode; no audio stream |
| `/root/driver_independent_review_20261002` | `host-review-20261002` | Independent source, executable regression and safety-boundary review | Reviewed through `8246b2f`; author `c6847a4`, `dfc9304`, integrated `f197939`, `930fc05`; no phone access |

Each worker owns separate files. Heavy kernel builds and hardware operations
remain serialized; none is needed to repair these host checks. The independent
reviewer found the newly reachable NPU caller-lock failure before publication;
the author fixed it and added actual extracted-caller C coverage. No host-model
result is upgraded to hardware acceptance.

The same explicitly selected NPU Luna worker completed a separate PM/clock
callback wave in `npu-pm-callback-20261002`, with a new narrow patch, test and
document. Author commits `e118b3a`, `4f9a2a1` are integrated as `06bab71`,
`28154ff`. Independent review `e69b1a0` is integrated as `fed88d7` in the
separate callback review document. Its source/host-test clearance is still
explicitly **not** NPU deployment or BOOTUP acceptance.

## Coordinator implementation

`09125b9` corrects two real readiness bugs: desktop Pi checking no longer counts
the separately configured web-session Pi as a duplicate desktop, and the actual
rendered tmux query uses a shared colon delimiter rather than an escaped tab
that produced an unparsable response. Exact binary inode/UID checks, duplicate
desktop refusal, required application checks and optional on-demand status remain.
No process or installed phone helper was restarted/replaced.

The unchanged pre-fix implementation reproduced three failed role assertions.
The corrected Pi suite passes 13 tests normally, under `-O`, and with
`PYTHONOPTIMIZE=1`. The observer suite passes 31 tests in all three modes, including
execution of its rendered tmux-query function. The web launcher passes 10 normal
tests; its intentional optimization refusal is preserved.

The first full-suite run exposed the Bluetooth adapter's now-stale observer
fingerprint. `512a1c3` pins the corrected source SHA
`5ec8fec300d514ef9e586dfe5635ca38995718acd0437caefe50c34358bd1ac4`;
31 adapter tests pass normally and optimized. That pin change is included in
the pending independent review, not treated as hardware authorization.

After the fingerprint correction, the existing complete explicit allowlist
passed all 30 normal and 27 optimized scripts, with its three documented
optimization exclusions. Per-test public-source/fixture availability skips
remain distinct from executed coverage; this is host-only evidence.

`7897570` adds the three new hardware-free NPU, audio and TrustZone suites to
the explicit CI allowlist, rather than enabling automatic test discovery.
The integrated allowlist at that commit passed 33 normal and 30 optimized
scripts, with the same three documented exclusions. After the routing and
NPU caller fixes, the full suite was rerun successfully against exact source
tree `8246b2f189b0c2790e2b68b3f3a4b4ff002b8908` at 07:28 UTC, again 33 normal
and 30 optimized scripts plus passing runner-policy tests. The sanitized
continuation receipt binds these results and the two read-only captures to
timestamps and opaque private-receipt hashes without publishing private logs.

The published first batch `1d38d39deeb45dbbb643ffdc42fa4a129f26d972` passed
[hosted CI run 36979092992](https://github.com/corpunum/unumS22linux/actions/runs/36979092992).
Its log confirms execution of the NPU patched helper/caller C, not merely a
source-string check. Pre-existing fixture-availability skips and the three
optimization exclusions remain explicit.

`eb6505f` adds only the reviewed callback script to the explicit CI path lists.
The complete suite at exact tree
`eb6505f8d16fd3a54127486902176cc2c7c35309` passed 34 normal and 31 optimized
scripts at 08:03 UTC, with the same three exclusions. Runner policy passes in
normal, `-O` and `PYTHONOPTIMIZE=1` modes. The continuation receipt adds this
second wave without replacing the earlier tested-tree receipt.

## Driver-source changes and limits

The NPU candidate serializes first/final reference callbacks, publishes counts
only after success, preserves failed teardown ownership with a sticky error,
propagates parent errors and uses source-specific pre-STM aborts. Its actual
extracted C regressions preserve a parent dependency after uncertain leaf boot
failure instead of applying a speculative inverse operation.

Independent review also identified that propagating `npu_hwdev_bootup()` errors
made a caller lock leak reachable. `8246b2f` fixes the locked normal-boot error
label while leaving the already-unlocked secure-count timeout label separate.
The test executes the pinned caller C, reproduces the baseline held lock and
checks boot, vref and POWER_NOTIFY errors, timeout balance and unchanged success
at C `-O0`/`-O2`, in normal and optimized Python modes. This is not a kernel
build, lockdep run or hardware test. That transaction-only review left PM/clock
callback error masking, ignored shutdown errors and recovery-close `BUG_ON`
policies as explicit non-deployment limits; NPU BOOTUP was still refused.

The subsequent callback candidate repairs those **tested callback** result
defects: failed resume no longer reaches clocks, successful PM returns are
normalized, clock failure balances its acquired PM reference, and errors are
not published as successful ON/OFF transitions. It uses the pinned API's
established cleanup, marks partial state ERROR/unknown and never retries a
failed release. A missing later clock entry previously returned stale zero
after rolling back earlier clocks; the source now sets `-EINVAL` at that branch.
The exact-C test reproduces this deterministic baseline failure and tests both
first/later missing entries, clock rollback, PM balance and empty-list behavior.
It deliberately does not assert the undefined first-missing-entry baseline value.

This closes the specified source defects, **not the NPU driver**. Independent
review confirms a further pre-existing probe hazard: `npu_clk_get()` fills a
non-zeroed pointer array incrementally, returns on failed clock acquisition,
and `npu_hwdev_probe()` warns but continues registration. Uninitialized entries
need not be NULL, so the new guard is insufficient for that path. The next
source task is checked probe failure and ownership-safe unwind, followed by
shutdown-error/recovery policies. Callback and helper tests do not establish
integrated failure atomicity, lockdep, PM/firmware/MMIO behavior or runtime
acceptance. No new NPU kernel or module was built or deployed.

The audio profile distinguishes historical coverage from current logging
policy, bounds input reads, and executes preprocessing of the pinned public
`dev_dbg` macro fragment. No ABOX static tracepoints cover the missing queue,
sender and selected pointer-handler boundaries. Current policy does not prove
the logging policy during the old 19-sample trial. No trace/Memlogger payload
reader or audio stream was opened.

## Fresh device evidence, not new acceptance

Coordinator read-only capture at 2026-10-02 06:03 UTC verified the same phone boot
as the previous capture, 412954.93 seconds uptime, 325 loaded modules, USB SSH,
native guardian/control, persistent mounts, model health/idleness, desktop,
desktop Pi, browser terminal and exact dedicated Pi session all ready.
The session was already available; zero Pi startups, writes, reboots or inference
requests were issued during this continuation.

The exact current identities remain:

| Component | Verified identity |
| --- | --- |
| Installed camera-enabled RECOVERY, 100663296 bytes | SHA-256 `b10412715756da3cc8ee221368b49f179cc0c64ab7bd2802976480905e6d8d2f` |
| Running kernel | GNU build ID `b2dda820b18d410d9bf12f1bd2584567d545991d` |
| Loaded camera module | GNU build ID `59e54c032c545fff3ba52156f226fb6d69aadf64` |
| Independently readable host HCI rollback, 100663296 bytes | SHA-256 `42da267f3dd9f94f30f62a95fb2ac13f91d4cf98f1a2307f7cc14e45d9c49be5` |

Battery was charging at 39%, 28.6 C; maximum thermal-zone reading was 44 C.
The available ring had no fatal indicators. A separate full available dmesg
capture covered only approximately the newest 198 seconds, not the full boot.
Hung-task timeout remains 120 seconds, panic policy 0, and remaining warning
budget 0. These settings were read, not changed. A quiet exhausted-warning ring
does **not** overturn the completed camera observer's `not_accepted` result.

Destination-specific read-only `statvfs` verified approximately 34.8 MB available
on native `/`, versus 101.57 GB and 1,652,475 free inodes on `/srv/s22` and the
persistent desktop/model mounts. All reported destinations are writable.
`/tmp` has 268.4 MB available; memory availability was approximately 2.72 GB,
without swap. Persistent diagnostic/staging work is not blocked by the small
root overlay. No backing overlay files were changed or deleted.

Private baseline, full available kernel log, identifiers and host-test receipt
are stored on the rig outside the repository. Public documentation contains
only explicitly selected aggregate fields. Independent hardware rescue is
still unproven. Camera powered open/capture and NPU BOOTUP remain blocked;
no consumed flash/reboot/observer authorization is reused.

## Completed read-only progress capture and post-check

The first collector attempt failed on the rig: the isolated source worktree
was supplied as the SSH artifact root and lacked the pinned host-key file.
The sealed wrapper checks that file before executing SSH. The failed marker
is preserved, and a local test executes the actual wrapper with a fake SSH
sentinel to demonstrate this pre-connection failure.

`c003ee9` separates reviewed source from the established host artifact root;
`4f52e0e` makes its tests portable to both isolated and original checkouts.
After independent review, one distinct second **read-only** attempt completed
with bounded procfs enumeration and two samples 2.01 seconds apart. Eight
worker threads, one iwlog thread and one chub-log thread had stable identities
and complete counter coverage, but no scheduler/context-switch changes were
observed. Expected wait stacks were not matched. No TEE request/completion was
measured: absence of change in this short window establishes neither a harmless
idle wait nor a hung request. The original camera `not_accepted` result remains.

A separate read-only embedded-config/trace-metadata capture identified the
same boot and running GNU build ID. The embedded configuration SHA-256 is
`d762d5fc71e369013ee36657d007063ce1f0faca9707d5f9ccfba2597b7fcd16`:
`CONFIG_DYNAMIC_DEBUG=n`, `CONFIG_DYNAMIC_DEBUG_CORE=y`; no matching
`CONFIG_SND_PCM_XRUN_DEBUG` entry was found. Per-module `DEBUG` and
`DYNAMIC_DEBUG_MODULE` flags remain unknown. The visible tracing directory
lacked the queried metadata and hwptr-event files; runtime tracing ownership
and availability remain unresolved. No trace buffer was read or setting written.

At 07:23 UTC the post-capture health check verified the same boot/build,
417758.58 seconds uptime, native guardian, persistent mounts, desktop, healthy
idle model API, desktop Pi, browser terminal and exact dedicated Pi session.
Battery was charging at 80%, 29.4 C; maximum thermal reading 45 C. No serious
fault was identified in the available ring. Zero partition writes, reboots,
Pi startups, inference requests, camera opens and NPU BOOTUP operations were
issued. The short ring and progress sample do not create new driver acceptance.

## Probe, shutdown and audio-observation implementation wave

The next continuation reconciled the clean integration worktree and fetched
origin at `01cf5a106035e9eab1a67421452baf8699cb6f13`, with no divergence.
The original checkout's five tracked changes, its history and all existing
worktrees remain untouched. Host auto-ACK and resident-assistant loop services
remain in place. This is source/host implementation, not renewed authorization
for any consumed device trial.

Three workers were actually launched with the supported native interface and
accepted explicit `gpt-6-luna` selection, `max` reasoning and `fork_turns=none`.
That evidence is explicit configuration, not backend identity attestation.
Only the coordinator accesses the phone; no worker may run device commands.

| Actual worker | Separate worktree | Exclusive files and deliverable | State |
| --- | --- | --- | --- |
| `/root/npu_probe_unwind_20261002` | `npu-probe-unwind-20261002` | `npu-probe-unwind-fix.patch`, its exact-C test and research note: clock acquisition transaction and pre-registration failure cleanup | Author `bd3204a`, integrated `de837b8`; 28 scenarios at C `-O0`/`-O2` pass in all three Python modes; independent source/host review cleared |
| `/root/npu_shutdown_ownership_20261002` | `npu-shutdown-20261002` | `npu-shutdown-ownership-fix.patch`, its exact-C test and research note: shutdown error propagation and ownership-safe recovery/close | Implementing; commit and test receipts pending |
| `/root/audio_ipc_instrument_20261002` | `audio-ipc-instrument-20261002` | `audio-ipc-observation-fix.patch`, its executable test and research note: opt-in, payload-free queue/send/pointer observations | Author `58dd9c8`, integrated `d16a7fd`; nine production-path scenarios at C `-O0`/`-O2` pass in all three Python modes; independent source/host review cleared |
| `/root/source_wave_independent_review_20261002` | `source-wave-review-20261002` | Separate review document; independently execute frozen source suites and audit ownership, trace semantics, combined patch stack and CI | Author `7c833a1`, `244b9a7`, integrated `2001d51`, `0d7ecb1`; probe/audio scope and corrected stack-detector coverage cleared, mandatory stack still blocked, shutdown excluded |

The NPU workers own separate patch files and separate source-function changes;
they do not edit the kernel checkout. Combined application and independent
review are required before integration. The next free slot is reserved for
independent Luna review. No heavy kernel build or deployment is part of this
wave; BOOTUP remains refused. Completed negative camera receipts and liveness
uncertainty remain preserved, not replaced by fresh quiet-ring health checks.

The fresh read-only coordinator baseline at 08:53 UTC verified the same boot,
installed camera image, kernel and camera-module GNU IDs, with 423150.72 seconds
uptime and 325 modules. Native remote control, idle model API, desktop, desktop
Pi, browser terminal and exact dedicated Pi session were ready. Battery was
Full at 100%, 28 C; maximum thermal reading was 42 C. No fatal indicator was
seen in the available ring, which does not cover the full boot. No session was
started and no device mutation or inference request was issued.

Read-only accounting at 09:11 UTC confirmed the same destination split:
34,844,672 available bytes on `/`, versus 101,570,527,232 bytes and 1,652,475
available inodes on `/srv/s22` and the desktop/model mounts. The mount namespace
matches native PID1. The PID-namespace comparison remains unavailable because
`/proc/self/ns/pid` is absent; two preceding read-only queries failed at that
comparison and are not represented as successful captures. No backing overlay
data was altered. The 100,663,296-byte host HCI rollback independently rehashed
to `42da267f…c49be5`. Both host **user-manager** control services are active;
checking the system manager instead would incorrectly report them inactive.

Coordinator independently reran the frozen probe suite normally, under `-O`
and with `PYTHONOPTIMIZE=1`, against the clean hash-verified derived kernel
fixtures. All three runs executed the poisoned-pointer baseline reproduction
and 28 patched input scenarios at both C optimization levels, with no fixture
skip. The shared `npu_clk_get()` keeps its required-property contract; only the
explicit optional API permits the absent direct-clock lists described by the
pinned hwdev DT/type contract. Present malformed/no-data properties and real
provider errors remain fatal. The tests execute production C functions, but
devres/driver-core behavior is shimmed, not a Linux runtime or hardware test.

Coordinator also reran the frozen audio suite in all three Python modes: nine
production-path scenarios at C `-O0`/`-O2` passed without fixture skips. It
executes the actual trace-event payload assignments under host shims. Default
disabled observations add no timestamps, sequence increments or allocations;
existing retries, queue/send returns, stale slot handling and pointer selection
remain tested separately from firmware behavior. The whole ABOX composite must
be rebuilt for its changed internal queue metadata; no module was built or
substituted. Local sender success is not a firmware acknowledgement, and the
pointer event has no fabricated trigger correlation.

After audio completed, the existing explicitly selected probe Luna worker was
reused in a **new** `npu-stack-20261002` worktree for two exclusively owned
integration-regression files. This follow-up keeps the prior probe commit and
worktree intact. It checks the historical session-lifecycle patch plus the new
ref/PM/probe/shutdown series together, including expected rejection of genuine
conflicts. Individually passing snippets are not proof that the full series
applies, compiles or operates safely. Any conflict remains a build/deployment
blocker; no patch or failing hunk may be silently omitted to obtain a PASS.

That follow-up is committed as author `873894f`, integrated `4c1131b`; the
independent reviewer identified a missing-input coverage overstatement.
Author `8c6e4df`, integrated `87bb76a`, now exercises the actual required-patch
manifest reader with the other exact-hash patches present and the required
refcount patch absent. The separate nonexistent-path `git apply` check is
labelled as a CLI diagnostic, not a builder omission test. Coordinator ran all
three Python modes again after the correction.

The full stack is **not ready to build**: ordinary ordered application accepts
the lifecycle patch then rejects the refcount patch at
`drivers/vision/npu/core/npu-vertex.c:1224`. Reversing them fails at `:1188`.
The lifecycle patch replaces the baseline caller error labels with tracked
`lock_held`/`out_unlock` cleanup; the refcount hunk still expects those old
labels. Both original changes are preserved. Later callback/probe hunks and
shutdown remain untested in this mandatory full stack. No failing hunk was
omitted, forced or silently recreated. The host preflight remains status 2,
with BOOTUP unready and unauthorized; unavailable firmware is not synthesized.

`2c28c37` adds only probe, audio-observation and stack-blocker suites to the
explicit host CI allowlist and matching policy tuple. The complete corrected
suite at exact tree `87bb76a1a23dc44e833aed8b79464f37ea351f69` exited zero for
37 normal and 34 optimized scripts, with the same three documented optimization
exclusions. Existing fixture-availability skips remain visible. All three new
suites executed without fixture skips; stack-detector success is explicitly
not a successful stack. Runner policy passes seven tests in each Python mode;
the environment-driven optimization run was separately verified active without
`-I`, which would ignore that environment variable. No device operation was
introduced into CI.

The independent Luna review receipt `7c833a1`, integrated `2001d51`, clears only
the isolated probe and audio source/test scopes. It leaves the full stack
blocked and shutdown unreviewed. The missing-input precision follow-up review
`244b9a7`, integrated `0d7ecb1`, independently executes the corrected actual
manifest-reader case and closes that coverage finding. It also checks the
three-path explicit CI allowlist change. The mandatory source stack remains
blocked. Shutdown implementation continues in its original isolated
worktree; its unreviewed draft is excluded from the first publication and is
not discarded or presented as finished.
