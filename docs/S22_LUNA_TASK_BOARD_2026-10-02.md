# S22 continuation — 2026-10-02

## Ongoing autonomous implementation checkpoint — 17:44 UTC

The historical entries below remain intact. This checkpoint supersedes their
in-progress descriptions, not their evidence or authorization boundaries.

- Revised audio author `f788f87`, integrated `c22e2c3`, now linearizes
  duplicate detection, queue publication and accepted state under the existing
  IPC queue lock. Actual FE/BE overlap, queue-full retry and DAPM caller C pass
  at `-O0`/`-O2`; public and explicit local fixtures pass all three Python
  modes. Independent Luna review has cleared this narrow invariant, while
  retaining the async-send/acknowledgement and swallowed DAPM-error limits.
  The initial `7e82af3` rejection remains recorded.
- NPU author `d50a90464d66c5371de17076ba84402316820123`, integrated
  `afceff8`, retains the POWER_CTL message ID after the precisely identified
  post-publication interrupt timeout. Actual extracted-C baseline failures
  and candidate ownership/STUCKED/64-ID-exhaustion tests pass at both C
  optimization levels across public/local and three Python modes. Root's
  independent local runs pass in all three Python modes. Independent Luna
  review also passes both public/local fixture routes in all three modes and
  clears this narrow ownership invariant; the committed review note follows.
  Unbounded publication drain and close/reopen quiescence remain unresolved;
  BOOTUP remains refused. This ninth patch is **not** in the active build.
- Bluetooth author `844bd3e`, integrated `6340e9a`, fixes bounded write/poll
  error handling and standalone UART termios restoration. Four new tests fail
  against the old source; all 30 pass after correction in normal, `-O` and
  environment-optimized Python, independently rerun by the coordinator.
  The existing deployment fingerprint correctly rejects the changed source;
  no artifact, trial identity or authorization was repinned.
- Native-eight kernel compilation is actually running, serialized at `-j1`,
  from frozen helper author `3c5be372` and source `872bffb8`; the exact native
  config survived `olddefconfig` byte-for-byte with the recorded Clang 18
  recipe. Future builder corrections reject Kconfig/Git redirection and fix
  six/eight applied-versus-excluded receipt inventories. All 29 tests pass in
  three Python modes; independent review has found no remaining source/config
  blocker. Those later helper edits neither restarted nor controlled the
  frozen running invocation. No completed-build result is claimed yet.

Actual accepted Luna/Max workers now include the completed Bluetooth author
`/root/bt_transport_next_20261002` in `bt-transport-next-20261002` and reused
`/root/native_npu_eight_build_20261002` performing **independent Bluetooth
review** in the new `bt-transport-review-20261002` worktree. The separate
`/root/autonomous_driver_review_20261002` reviewer is finishing native/audio/
NPU review in `autonomous-review-followup-20261002`. Both own only their new
review notes; only the coordinator accesses the phone. Accepted explicit
model selection is configuration evidence, not independent backend attestation.

The completed NPU author has also been reused for the next actual drain/lifetime
implementation in new isolated `npu-drain-next-20261002` at `39aa985`, owning
only a separate candidate patch, harness, regression and document. It must
establish safe publisher/waiter ownership before removing the unbounded drain;
a naive timeout/free is expressly forbidden. No active source/build or
previous patch is edited by this subsequent host-only workstream.

Combined independent review `71ee49e`, integrated `dc3ea6f`, is committed for
native profile guards, the revised audio queue invariant and NPU9. A separate
evidence correction `dc5bdf4` preserves its original history: `-I` ignores
`PYTHONOPTIMIZE`, so affected environment-mode claims were rerun **without**
`-I`, first checking `sys.flags.optimize == 1`. Both public/local NPU and audio
environment-mode cases pass, as do all 29 native builder cases. Normal and
explicit `-O` results remain valid; HCI's environment invocation was already
correct. This is a verification correction, not a weakened test or source gate.

The second complete fixed suite finished against `39aa985`, with 50 normal
and 47 optimized invocations and credential-free child environments: 95 passed,
two adapter-script invocations failed (normal and optimized). Each failure has
the same two artifact-corruption/TOCTOU fixture cases: the preserved production
source fingerprint correctly rejects the changed bridge before their intended
checks. This is not a one-shot suite PASS. The Bluetooth author is repairing
only test fixtures in new `bt-ci-fixtures-20261002`; production source/artifact
pins, trial IDs and authorization remain unchanged. NPU9 actually executed and
passed against public pinned source in both suite modes. The new
NPU regression is explicitly listed in both runner inventories and the policy
test; no discovery or live-device script was added.

The fixture repair is frozen at author `69f8733`, integrated `01f20fc`:
all 32 adapter tests pass in three Python modes, using actual validators and
board code, temporary copied sources and one literal test-only bridge pin.
Source-tamper, artifact-corruption, pin restoration and TOCTOU no-stage checks
remain active; default production refusal is explicitly tested. Independent
Luna review is running in `bt-fixture-review-20261002`, owning only its new
review note. A third complete 97-invocation suite is now running against this
frozen code. Earlier failed suite evidence is not erased or relabeled.

Independent fixture review `b1c0740`, integrated `97eea8b`, reproduced both
pre-fix cases and cleared the repair after 32 tests passed in each mode,
including an actual `sys.flags.optimize == 1` environment run. It separately
forced exceptions through the fixture contexts and verified pin restoration.
The third complete suite at `01f20fc` passed 94/97 initially; three public
source downloads timed out before assertions (liveness normal, probe-unwind
optimized, shutdown-error optimized). One bounded **public** rerun of each
then returned zero. All 97 invocations have passing executions across those
runs; no one-shot PASS, hidden local fallback or hosted-CI success is claimed.

Independent Bluetooth source review `f663717`, integrated `71dc06d`, found no
blocking source defect and independently passed all 30 cases in three Python
modes and warning-clean C `-O0`/`-O2`. It records nonblocking injected cleanup
and wall-time measurement gaps, and confirms the production gate's refusal.

Read-only native health at 17:33 UTC confirms the same boot and all 325 loaded
modules, GNU build ID `b2dda820b18d410d9bf12f1bd2584567d545991d`, native control,
desktop, desktop Pi, browser/dedicated Pi and idle model health. Battery is
100%/Full, 26.9 C; maximum sampled thermal reading 43 C. The available ring has
no new severe indicators but is not a full-boot log or measured-progress proof.
No new partition writes, reboots, powered tests or inference requests occurred.
The supported unprivileged host sleep-inhibition request was denied; no bypass
or guaranteed host-awake claim is made. Independent hardware rescue remains
unproven, and consumed single-attempt approvals are not replayed.

## Autonomous continuation after the completed source/build wave

Fresh fetch at 16:10 UTC found review HEAD still `5e2eebf` and no divergence.
No previous worker or heavy kernel build was still running. The original dirty
checkout, completed six-patch build, historical worktrees and phase journals
remain preserved. The mission is not complete; another host implementation
wave is running while specific hardware acceptance remains unresolved.

All three workers below were actually launched using supported explicit
`model=gpt-6-luna`, `reasoning_effort=max`, `fork_turns=none`. The accepted
configuration has no known fallback; independent backend identity is not
exposed. Only the coordinator accesses the phone. Directory names are durable
labels, not evidence of a worker's assignment; the two reused generic names
below are newly created worktrees, not overwritten historical directories.

| Actual worker | Separate worktree | File ownership and deliverable | State |
| --- | --- | --- | --- |
| `/root/native_npu_eight_build_20261002` | `native-npu-eight-20261002` | Existing guarded NPU builder/test plus one build document; explicit preserved-native eight-patch profile and configured kernel/module compilation | Author `f690453`, `f58ba24`, `f975651`; two preparation refusals preserved, build recipe investigation continues before compilation |
| `/root/npu_lifetime_next_20261002` | `audio-driver-next-20261002` | New NPU publication-ownership patch/C regression/document only | Implementation running; unsafe timeout/free and BOOTUP forbidden |
| `/root/audio_driver_path_next_20261002` | `button-actions-20261002` | New ABOX IPC error-path patch/C regression/document only | Implementation running; no powered audio operation |

Audio author `5651aee` is preserved in history and integrated as
`87096f8`: the failed trigger request no longer updates the cached enabled
state and suppresses the next caller-requested START/STOP. Six fixture/Python
mode invocations passed at both C optimization levels. This does not establish
async sender success, firmware acknowledgement, DMA or physical audio.
Independent review `7e82af3` (integrated `33961e2`) **blocked** this initial
fix: concurrent FE/BE calls can lose a transition, and a BE error may be
consumed upstream. The author is implementing duplicate suppression and
successful state publication under the existing queue lock, with actual
queue/caller C and controlled concurrent tests. The initial negative review
is retained; the revised source is not yet cleared.

Coordinator commit `0f643f7` adds actual extracted HCI lifecycle/IDA C coverage
and its explicit CI entry. Baseline create false-success is reproduced;
registration, allocation/type failures, cleanup/error unwind and the real
negative-ID guard are tested. All local/public × normal/optimized/environment-
optimized invocations passed; no new cookie patch is warranted.

Actual independent `/root/autonomous_driver_review_20261002` was launched with
the same explicit Luna/Max selection in `autonomous-review-20261002`, from
`87096f8`, owning only its new review note. Its HCI host scope is cleared;
initial audio scope is blocked as above. Local tests passed in all three
Python modes; all six attempted independent public fetches were unavailable
and are not counted as executed coverage. Builder and revised audio/NPU
ownership review follow as author commits are frozen.

The complete explicit suite at `475a5af` attempted **49 normal and 46
optimized scripts**. It returned failure for two optimized public-source
fetches (exit 77); the other 93 invocations passed. One bounded public rerun
of each unavailable script subsequently returned zero. This is coverage
across a failed initial run and a successful bounded rerun, not a one-shot
PASS. No hidden local fallback or hosted-CI result is claimed.

Native source `872bffb8ea2ea657f94d10b866dc655b5718d6db`, tree
`417e4a55e222b99ecca0f198e081d2d808ef5628`, is a clean single commit of the
eight ordered NPU patches directly on native HCI/camera base `3fca509`.
The actual parent history contains HCI restoration and two camera unwind
fixes, **not** a `fs/file.c` close-range change. The existing scoped Pi
userspace wrapper is preserved; older broad descriptions of a native
close-range kernel patch are not evidence that one is installed.

The first preparation used the historical Clang 21 and correctly refused
the changed config before `Image modules`. Profile-only author `f975651`
(integrated `9eed440`) pins the existing working-HCI manifest's exact Ubuntu
Clang/LLD 18.1.3 bytes, ten LLVM helpers and three GNU cross tools; 22 host
tests pass in each Python mode. A separately authorized fresh **host**
invocation also refused before compilation: explicit `LLVM_IAS=1` made
Kconfig expose `HAS_LTO_CLANG` and two unselected options absent from the
working recipe. Both outputs are preserved. No byte guard was relaxed;
matching the recorded recipe without that explicit override is the next
bounded host investigation, not a device trial.

Coordinator USB read-only health capture at 16:18 UTC verified the same boot,
kernel GNU build ID `b2dda820b18d410d9bf12f1bd2584567d545991d`, unchanged 325
modules, guardian/control, persistent mounts, desktop/Pi and idle model health.
Battery was 100%/Full, 27.7 C; maximum sampled thermal reading was 43 C. Ring
coverage is not full-boot coverage and supplies no new hardware acceptance.
The exact embedded config was separately recovered read-only: 236,183 bytes,
SHA-256 `d762d5fc71e369013ee36657d007063ce1f0faca9707d5f9ccfba2597b7fcd16`.
Host fsync/readback passed; private configuration text was not published.
Native-eight does not substitute the historical six-profile config.

Previous single-attempt
device authorizations remain consumed. This continuation has issued no new
partition write, reboot, powered hardware trial or inference request.

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

## Hosted portability follow-up

Publication `a5baa605f56d7287c39ccfd5c00336f5311367ac` did **not** pass
[hosted CI run 36992416493](https://github.com/corpunum/unumS22linux/actions/runs/36992416493).
The two failures are the normal and optimized audio-observation test: its
default source-tree path exists only on the rig. This is distinct from the
successful local full suite and the deliberately detected NPU integration
blocker. A green local result was not substituted for hosted acceptance.

The explicitly selected Luna/Max probe worker is reused in another isolated
worktree, `audio-ci-portability-20261002`, owning only the audio test and its
research note. The deliverable is a bounded, exact-hash public-fixture default,
retaining strict checks for an explicitly configured local tree and executable
loader negative tests. Author `c89e208` fixes the rig-only path, and `8e673c5`
corrects a subsequently reproduced negative-test gap: explicitly empty fake
headers had been replaced by a Content-Length header. The corrected body-cap
case proves the header is absent, the bounded read occurs, and the body-cap
diagnostic is reached; the header-cap case separately proves no read occurs.
Integrated commits are `92c561a` and `1a2eb02`.

Independent Luna review `f8ccf00`, integrated `449cbdb`, clears the corrected
portable test. Both public-default and explicit derived-source paths execute
eight loader checks and nine actual-C cases at `-O0`/`-O2`, in all three Python
modes, without fixture skips. Invalid configured paths, wrong HEAD and modified
worktree fixtures fail rather than falling back. The coordinator independently
ran the complete suite at exact author tree `8e673c5`: 37 normal and 34 optimized
scripts exit zero, with three explicit optimization exclusions. The separate
environment-driven optimized audio run also executes public fixtures and C.
The five-second urllib timeout is per blocking operation, not a total fetch
deadline; the host runner independently limits each script to 180 seconds.
Environmental fixture unavailability exits 77, which the runner treats as
nonzero, not successful execution. Existing unrelated fixture skips remain
distinct. No phone operation, kernel build, module replacement or hardware
acceptance is part of this corrective follow-up.

## Continuing isolated NPU source work

Shutdown author `3c6eaff70f720b682cc8d09e598fcaf2269df0bc` is frozen in its
original worktree, not integrated or deployed. A newly spawned, explicitly
selected `gpt-6-luna`/`max` worker,
`/root/npu_shutdown_independent_review_20261002`, uses separate worktree
`npu-shutdown-review-20261002` and owns only its new independent review document.
It reviews actual shutdown/ref-put C, the unequal multi-leaf regression and
retained-session lifetime limits. Source testing is not proof that VFS release,
session-manager/device/module removal or firmware lifetime is safe.

The probe/portability Luna worker is now implementing a transparent reconciled
refcount profile and actual combined-source C tests in another separate
worktree, `npu-stack-reconcile-20261002`. It owns only the new profile, test and
research note. Historical lifecycle/refcount patches and the expected-conflict
regression remain untouched. The already-equivalent lifecycle error-unlock
replacement must be accounted for explicitly, not silently dropped. No new
profile is yet reviewed, build-ready or deployed; shutdown remains a separate
unreconciled source candidate, and NPU BOOTUP remains refused.

## Completed refcount-profile and shutdown-quarantine wave

This entry supersedes only the pending status in the preceding historical
entry. The same explicitly selected Luna/Max implementation workers and two
independent Luna reviewers completed the bounded source work below. Native
spawn selection was accepted as `gpt-6-luna` / `max`, with no known fallback;
this is configuration evidence, not independent backend identity attestation.
Completed workers and all isolated worktrees remain preserved. Only the
coordinator accessed the phone, for read-only health evidence.

| Actual worker | Isolated worktree | Frozen author result | Integrated result |
| --- | --- | --- | --- |
| `/root/npu_probe_unwind_20261002` (reused selection) | `npu-stack-reconcile-20261002` | `1c36220c47219853d18512723e6c20024a486be5`: new explicit refcount/lifecycle profile, combined-source C tests and note | `d35b0d8` |
| `/root/npu_shutdown_ownership_20261002` | `npu-shutdown-20261002` | `3c6eaff70f720b682cc8d09e598fcaf2269df0bc`, corrected by `f2d9a09da6aed6fc39013b637fc65ed4ca850d22`: first-error shutdown ownership and six late-quarantine checks with executable regressions | `8a14f10`, `40d589c` |
| `/root/source_wave_independent_review_20261002` | `source-wave-review-20261002` | `f68f478dd626d61c69118554c21361073e8762ea`: profile review; `519673266a77f66ed5dfb30699c61d6efd3b4339`: bounded CI integration review | `0d1a948`, `0cea5c3` |
| `/root/npu_shutdown_independent_review_20261002` | `npu-shutdown-review-20261002` | `97cf0f769c303546c71f3aede6661a51ddbe45d4`: blocked the initial lock-wait race; `8adf3f6a08b1de3fd146db7534bebfc5884c0638`: re-reviewed the corrected six paths | `4f951fd`, `21985fa` |

The new four-patch profile applies lifecycle, the explicit refcount C/H
profile, default-boot callback and probe-unwind patches with ordinary
`git apply --check --whitespace=error-all` and `git apply`. Historical patches
and their expected-conflict regression are unchanged. The canonical refcount
C/H sections are preserved verbatim; its stale vertex unlock hunk is explicitly
superseded by the lifecycle caller's tracked locking, not silently omitted.
Eleven actual-C ref-helper scenarios and five final-caller scenarios pass at
C `-O0`/`-O2` in all three Python modes. The five caller cases check selected
cleanup counts and lock balance, not every reverse-order cleanup event or all
STM/HWACG/non-warm branches. Host preflight still exits 2 and refuses BOOTUP.
Absent firmware fixtures in the test checkout do not establish absent phone
firmware. This is source composition and extracted-C evidence, not a kernel
build or hardware result.

Shutdown now retains uncertain ownership and propagates errors instead of
panicking or pretending success in the reviewed paths. Independent review
reproduced a quarantine bypass by callers queued on the mutex. The follow-up
rechecks uncertainty after all four boot-control lock acquisitions and both
bootup wait-loop reacquisitions. Actual extracted C reproduces all six old
bypasses and refuses them after the fix, with balanced shim locks and no
modeled follow-on work. All three Python modes and C `-O0`/`-O2` passed for
standalone and refcount-plus-PM source profiles. This deterministic injection
is not Linux concurrency, memory-ordering or lockdep evidence.

The coordinator separately executed the exact **five-patch** application:
the four profile patches pass, then the frozen shutdown patch fails plain
`git apply --check` at `drivers/vision/npu/core/npu-vertex.c:313`. Exact loader
fixtures and frozen patch bytes were checked; no force, fuzz or missing-hunk
workaround was used. The independent shutdown reviewer records this as
coordinator evidence rather than claiming to have rerun it. This full-stack
blocker remains. Further unresolved issues include early-close/protocol-close
errors masked by the pinned shutdown callback, normal-bootdown count/ref
ownership after error, session-close failures and retained lifetime through
VFS/session-manager/device/module teardown. No NPU BOOTUP is authorized or
safe merely because these host tests pass.

Coordinator commit `33315389d2a9c3373cecf23004ae81b8848b6756` adds only the
two reviewed hardware-free tests to the explicit CI allowlist and matching
policy lists, and qualifies shutdown source status. Independent review
`5196732` checked that exact change and ran seven policy tests in each Python
mode. The complete suite at `3331538` passes **39 normal and 36 optimized
scripts**, with the same three documented optimization exclusions. Both new
NPU scripts execute public pinned fixtures without new fixture skips; existing
unrelated skips remain distinct. The subsequent review commit changes prose
only. The sanitized receipt binds exact source/test/log hashes and limits.

Prior portable-fixture publication `3466dcb4294870616b6d52c3635b4af8b981f7f0`
passed [hosted run 36995251510](https://github.com/corpunum/unumS22linux/actions/runs/36995251510).
That run does not cover the new NPU commits; their hosted run must be checked
at the new published SHA. `origin/master` remains at
`20605dbe623e0909cf219c3cae9ef7bb597b15a6`; local `master` remains separately at
`fb60a2c1fd69544f525f48de19e1af266880b2ba`, with its original dirty checkout
unchanged. Neither master ref was moved by this wave.

The 10:26 UTC read-only snapshot still identifies the same native boot/kernel,
guardian, 325 modules, healthy idle model API, Hyprland, desktop Pi, browser
terminal and dedicated Pi session. No new session start, inference request,
flash, reboot, HCI/NPU request or camera/audio stream was made. The available
kernel ring is truncated, not full-boot or TrustZone liveness proof. Current
remote reachability is not independent physical rescue.

Next source action: reconcile the shutdown ownership semantics with the final
lifecycle caller/close paths, then repair and execute callback-error/lifetime
tests before proposing a full kernel build. Audio instrumentation remains
disabled by default and hardware acceptance remains separate. No additional
device authorization is inferred from this source-only wave.

## Portable-assistant and physical-buttons continuation

The owner explicitly reaffirmed the target: native Linux on r0s, with working
physical controls and hardware for a future owner-supplied agent framework.
The device should provide its own SIM internet and phone number, usable
Bluetooth/Wi-Fi, and two-way audio for speaking and calling. This does not
mean enumeration, a web terminal, or hosted model access satisfies telephony.
It does not authorize restoring Android, writing unrelated partitions,
replaying consumed trials, placing calls to arbitrary recipients, or removing
remaining kernel-safety gates. The existing goal tracker has the same hardware
mission but reports `usageLimited`; creating a replacement goal was rejected.
This is an explicitly requested manual continuation, not a claim that the
tracker was reset or an automatic wake mechanism was installed.

Starting review HEAD is `2524ff036a6190bb4a22dc94606a2da5a739ebe4`. Fetch found
no divergence. Original dirty work and master refs remain separate and intact.
Three new workers were actually launched through native `spawn_agent`, each
with accepted explicit `model=gpt-6-luna`, `reasoning_effort=max`,
`fork_turns=none`. Selection is configuration evidence, not backend attestation.

| Actual worker | Separate worktree | Exclusive implementation scope | Status |
| --- | --- | --- | --- |
| `/root/physical_buttons_impl_20261002` | `buttons-assistant-20261002` | Existing input inventory/test; new bounded button-event evidence/test and research note | Implementing; receives coordinator-only live evidence |
| `/root/npu_full_profile_impl_20261002` | `npu-full-profile-20261002` | New shutdown/lifecycle profile, final-combined-C test and note; historical patches untouched | Reconciling the exact vertex `:313` blocker |
| `/root/cellular_readiness_impl_20261002` | `cellular-assistant-20261002` | New bounded passive CPIF readiness collector/classifier, fake-filesystem tests and note | Implementing; no modem/device access |

Only the coordinator accesses the phone. Independent Luna review follows the
frozen implementations in the next available slot; heavy builds and all
hardware operations remain serialized. None of these workers may boot the
modem, load NPU firmware, inject input, flash/reboot, open audio/camera streams,
place calls/SMS, alter the desktop or start another agent session.

Fresh bounded native reads at 11:32–11:36 UTC establish:

- The 64-bit Linux input capability bitmap maps `gpio_keys` event0 to
  `KEY_VOLUMEUP` (115), and `sec-pmic-key` event1 to `KEY_VOLUMEDOWN` (114)
  plus `KEY_POWER` (116). Both are existing character nodes with wake enabled.
- Actual Hyprland 0.56.2 enumerates both physical devices. Its live bindings
  contain the existing locked `XF86PowerOff` display-DPMS Lua action, not a
  shutdown/reboot/suspend action. DSI-1 is enabled. No volume bindings were
  observed; no key was pressed or injected.
- `pactl` exists in checked Arch tool paths, but `wpctl`/`amixer` and expected
  Omarchy volume helpers do not. No PipeWire, WirePlumber or PulseAudio daemon
  was observed in native procfs. Binding buttons to nonexistent tools would
  not establish working volume or speakers.
- The exact pinned CPIF `modem_state_show()` only formats the in-memory state.
  A bounded read reports `INIT`; `cp_interface` is bound, `rmnet0..7` are down
  and `wlan0` is up. This is not SIM detection, registration, mobile internet,
  IMS/VoLTE or voice evidence. No radio activation or identity query occurred.

Button-driver capability and compositor registration are now live-observed;
physical press/release delivery, display wake by a real short press and actual
volume/speech remain unaccepted. The next owner-assisted input session should
correlate short Power, Volume Up and Volume Down events with compositor/audio
state, without long holds or kernel long-press changes. A working audio backend
must precede volume/speech acceptance, and a booted modem plus supported radio
userspace must precede SIM/data/calls. These independent tasks do not wait for
NPU inference, but no host test alone grants a powered hardware trial.

At 11:49 UTC, a separate bounded HTTPS HEAD request explicitly bound to
`wlan0` completed on the native phone: curl exit 0, HTTP 200, TLS verification
result 0. This verifies current IPv4 DNS/TLS/HTTPS through Wi-Fi rather than
merely interface enumeration. It is an active network observation, not a
passive sysfs read and not cellular data evidence. No modem, partition,
service, display, audio, or input control was changed.

### Implemented and independently reviewed button/cellular work

The implementation table above is historical launch state. Subsequent frozen
results are:

| Actual Luna worker | Author commits | Integrated commits | Evidence / remaining boundary |
| --- | --- | --- | --- |
| `/root/physical_buttons_impl_20261002` | `dcfa9754e01d344c2d88b40bcb9471ef92471bd9`, test follow-up `b4a9e021a7b24e1433d946fe4c2817bf15afc09d` | `22cbb36`, `b615b32` | Correct native-word sysfs capability parsing; character-node/sysfs identity rechecked after open; read-only button-only capture; 6 readiness and 16 event tests per Python mode. No physical stimulus or phone installation. |
| `/root/cellular_readiness_impl_20261002` | `827e2d64bbde2cb22f5133f358205920660b3530`, corrective `4c733d2bd3e8d55cf2cd9ed556b4b5ca7b15a697` | `adf2101`, `66a7ade` | Bounded passive CPIF inventory. Independent review reproduced dangling/out-of-namespace driver false positives and silently omitted per-module errors; both corrected. 12 tests per Python mode. No modem activation. |
| `/root/portable_assistant_independent_review_20261002` | `937e96a2325af6b77ee487573f4604c9e20ab356`, `de20994788d6df7763cd869e8c0f43e6ab7d9504`, `c22c084c61cef077fbd1bb1eaada91d4aef10ead` | `af4ca31`, `fcee075`, `55d63a9` | Initial blocking review, corrective re-review, button/CI audit, actual collector-failure test review. Dedicated review worktree; no phone access. |

All executions above used the accepted explicit `gpt-6-luna` / `max` worker
selection; there is no claim of backend attestation. The independent reviewer
actually reran cellular 12, input readiness 6, button-event 16 and runner-policy
7 tests under normal Python, `-O`, and `PYTHONOPTIMIZE=1` (earlier button review
covered the then-current 13-test set).

The new collector-level cases execute controlled `read`/`select`/clock paths
over temporary host files with verified fake input-node identity. Read errors,
partial records followed by EOF, and a raw-record budget reached by discarded
private key input all produce incomplete results and close descriptors. They
never become physical-origin acceptance. Other dropped/unterminated-frame cases
remain analyzer-level tests. Default capture is inventory-only; explicit
capture stores only Power/Volume Up/Volume Down and relevant SYN records.
Inventory retains capability bitmaps, not private keystrokes. Its kernel sysfs
reads are PAGE_SIZE-bounded attributes rather than arbitrary-file capped reads;
the requested event timer starts after inventory and node opens.

The old generic `--events` JSON is intentionally replaced by button-only
evidence. No committed code consumer of the old JSON was found; this is not
a claim of external API compatibility or generic touch capture coverage.
The existing Hyprland Lua Power binding remains the only display action; do
not install a second Power daemon. Volume actions still need a real audio
backend. At 12:05 UTC, read-only sysfs `dev` and character-node stat identities
also matched event0 `13:64` and event1 `13:65`, without opening either node.

`8f74fd80fe4a6b88524350ab48c453ad771cf896` adds precisely the two new
hardware-free button/cellular scripts to the explicit runner and independently
pinned policy list. No discovery or live-device path was added. The initial
full host invocation at that HEAD returned 40 normal and 38 optimized script
passes, with one normal failure and three documented optimized exclusions.
A fully instrumented rerun at `b615b32ca3953f86f986ecd6f8881ea838f5410e`
identified the normal failure: the existing refcount actual-C test returned 77
when a public pinned source download timed out. All other 40 normal scripts and
all 38 optimized scripts passed. This full invocation is **not** reported green.
That exact test subsequently passed both with the verified local derived
source and on one bounded clean-environment public-source retry; the latter
actually fetched source `4e5c5ad7d950e4de0688b5663965f2075654b2ad` and executed C.
The unavailable-source failure was not suppressed or converted to PASS.

### Five-patch NPU composition and next source repair

`/root/npu_full_profile_impl_20261002` froze author commit
`2a7b84b3190c65fb58181fccb82a09d6b40fc2d1`, integrated as `1008565`.
Its new derived shutdown/lifecycle profile applies after the exact four-patch
prefix with ordinary check/application, without editing historical patches.
The frozen standalone ownership patch still fails independently at vertex
`:313`; the derived profile explicitly reconciles that overlap. All six
quarantine checks, lifecycle error propagation and close order are retained.
Author normal/optimized/environment-optimized runs pass actual extracted
combined C at C `-O0` and `-O2`; the coordinator independently reran normal
mode. No kernel or module was built or deployed. The full-five profile SHA-256
is `b986e1896305fda55f1d702ed6f12dde646e4a84b3ce91009203b9d77b7a00e7`.

Independent frozen-code Luna review is committed at author
`84947e3382f4512ddf36bd27228002a0d6146e06`, integrated `937add8`. The reviewer
matched both frozen file hashes before and after three-mode reruns from the
integration tree. No source-composition blocker was found; hardware/lifetime
limits remain. A separate public-fixture portability follow-up is frozen at
author `204973c6a310f92f2810db129a13a07a2e29d263`, integrated `b6042ec`:
default public pinned-source loaders, exact local-root normalization,
overlap/size/hash checks, explicit unavailable-input exit 77, and preflight
against the actual final five-patch temporary tree. The author executed local
and public paths; the source union contains 19 paths / 1214674 bytes.
The profile patch did not change. Independent follow-up review is in progress.
`5ca727fa3fce12d242749dbbeefb7f8b9f425a23` adds only that script to the three
explicit CI lists; policy tests again pass in all three modes. Hosted coverage
of this new profile is pending publication, not inherited from the older CI
result. BOOTUP remains refused; absent local firmware was not fetched,
manufactured or substituted.

After the button worker completed, the next bounded implementation slot was
used for `/root/npu_shutdown_error_impl_20261002`, explicitly selected Luna/Max
in isolated `npu-shutdown-error-20261002` at `1008565`. It exclusively owns a
new shutdown-error-propagation patch, extracted-C regression and research note.
The source defect is masked early/protocol-close errors inside
`npu_device_shutdown()`. The worker must reproduce the false success before
any fix and preserve cleanup order, caller ownership and quarantine. This is
source implementation, not permission for NPU firmware or a device trial;
independent review is required before promotion.

At 12:17 UTC the native phone remains reachable, has GNU kernel build ID
`b2dda820b18d410d9bf12f1bd2584567d545991d`, PID 1 `native-guardian`,
435402.90 seconds uptime, persistent mounts ready, idle healthy model API,
desktop Pi, dedicated Pi session and browser terminal ready. Battery is Full
at 100%, 27.9 C; observed maximum thermal-zone temperature is 40 C. The
available 255756-byte kernel ring contains no classified fatal/hung-task
indicator, but is not full-boot coverage or proof of powered-request liveness.
No fresh full RECOVERY hash was taken in this source wave. No partition write,
reboot, Pi task/inference, modem/SIM/call/SMS action, input injection, physical
press, or audio/camera/HCI/NPU request occurred. Existing desktop/assistant,
historical trials, original dirty checkout and master were preserved.

At 12:24–12:25 UTC, the coordinator also executed the actual reviewed CPIF
collector and input inventory in native Python memory through the pinned USB
SSH route, without installing a phone file or opening event/modem device
nodes. Source hashes were verified before execution. The corrected CPIF
collector reports a valid `cp_interface` binding, seven listed expected
modules without lookup errors, modem `INIT` and eight down rmnet interfaces;
all SIM/data/IMS/voice acceptance remains unknown. The new input implementation
parses the actual 64-bit bitmaps over 11 input nodes, selects exactly the two
button candidates above, and verifies their node/sysfs device identities.
This establishes real execution of passive tooling, not real-button delivery
or a cellular connection. Sanitized follow-up timestamps/hashes are appended
to the existing public baseline receipt without changing its earlier samples.

### Completed full host run and shutdown-error implementation

The complete explicit host suite at
`7d2e4a81001db2cbbbc34e5ab53ada717a37393b` returned zero:
**42 normal / 39 optimized script passes**, no failed script and the same
three documented optimized-mode exclusions. Its combined output SHA-256 is
`6391101fb7308194c4f0dc726b247989be63c63633163b4a926f716899b045af`.
This is a subsequent green invocation; it does not rewrite the earlier
download-timeout receipts, count nested unavailable-fixture skips as execution,
or establish hardware functionality.

The five-patch public-source portability and three-line CI change were
independently reviewed at author
`a8095177b2af26ba70a5d8a437f9037705481dc7`, integrated `ec9fe25`. Public
normal/optimized and local one-root environment-optimized runs actually
passed; missing/mismatched configured roots failed instead of falling back.

The bounded shutdown-error worker froze
`1df641e4ae5c6c60a41e11d63a1e9f7670c73e51`, integrated `ec0dd71`. This
sixth, device-function-only patch keeps existing early-close, protocol-close,
DHCP-deinit and system-suspend order but returns the first nonzero error.
The actual pre-fix extracted C falsely reports success after early/protocol
errors and lets the tested close caller free the session. Final extracted C
reports the error and the tested close path retains session/open-reference
ownership and latches shutdown uncertainty. Its boot ref has already reached
zero: this is not proof of complete lifetime recovery or a repaired normal
count/ref contract. Some alternate/unwind callers still ignore or only log
callback errors, and real failed protocol/suspend behavior remains unproven.

Patch SHA-256:
`08374e96792f24d1e0e4fbca594bfce35537af8acace9296b66f27b531564e43`.
Test SHA-256:
`7af046d7330acaa5d54ff5a7a334f7acbe07b0ace6301194fa9b15704c251a70`.
Author local three-mode and one public-default invocation passed; the
coordinator independently reran all three local Python modes. Each run
executes 30 scenario configurations at C `-O0` and `-O2`: four reproduced
pre-fix behaviors, nine new final cases and 17 inherited ownership cases.
The coordinator output SHA in each mode is
`9b4d17cadfee39a6ccbecc233cefe55c41caf4310df389d61baa4c299baadf2f`.
BOOTUP preflight remains status 2 with readiness and authorization false.

`da658ded6d62a8362127b0c1bee73c0591cff5a8` adds only the new sixth-patch
test to the three explicit CI path lists; all seven runner-policy tests pass
again in each Python mode. Final independent review is in progress. Expected
hosted coverage is now **43 normal / 40 optimized scripts**, not an already
observed hosted result. No kernel build, installation or hardware request is
inferred from this host/source result.

Next device-specific actions remain distinct: owner-confirmed short physical
button event delivery/display response; a reviewed audio route/DMA experiment
for volume and speech; review of protected NV/EFS behavior before any CP
bootstrap; and separate SIM registration, forced mobile internet, IMS and
two-way voice acceptance. NPU publication-drain and retained-object lifetime
remain unresolved. No consumed trial is reopened by this goal or source wave.

Final independent shutdown-error review is now frozen at author
`93aa6cc375f1fb0d8dfbc2a29e0228afc4f1353d`, integrated `f0dc402`. It
independently executed local normal/optimized/environment-optimized and one
public-default run, verified the exact test blob in the three-line CI change,
and found no blocker in this narrow source/error-contract scope. It preserves
the boot-ref-zero and wider lifetime limitations. All implementations in this
publication wave therefore have an independent Luna review receipt. Master
and the dirty original checkout remain unchanged; only the unmerged review
branch is selected for publication. Hosted CI at the final published SHA
still must be checked independently; source review and host passes do not
grant device acceptance.

### Subsequent host build/audio/publication wave

The final previous-wave publication `8c35bacb066bdcbf95678b37ae4ce0d9601df0d7`
was independently verified on the remote review branch. Exact-head hosted run
[37009064396](https://github.com/corpunum/unumS22linux/actions/runs/37009064396)
succeeded: 43 normal and 40 optimized scripts, with the three existing
documented optimized exclusions. This later result does not replace earlier
receipts or imply device acceptance.

The next manual continuation starts from that clean integration HEAD; a fresh
fetch found no review-branch divergence. The original dirty master checkout is
preserved. Native spawn accepted explicit `gpt-6-luna` and `max` reasoning for
each worker below, with no known fallback. This records selected configuration,
not independent backend identity attestation.

| Actual worker | Isolated worktree | Narrow deliverable |
| --- | --- | --- |
| `/root/audio_passive_impl_20261002` | `/home/corpunum/s22-workers/audio-passive-20261002` | Passive default audio metadata, bounded collection and focused executable tests; only audio-control readiness source/new test/note. |
| `/root/npu_publication_impl_20261002` | `/home/corpunum/s22-workers/npu-publication-20261002` | Actual extracted-C stalled-publication ownership/liveness evidence; new test/note, no changes to the six frozen patches. |
| `/root/npu_six_patch_build_20261002` | `/home/corpunum/s22-workers/npu-six-build-20261002` | Clean committed six-patch kernel source and first actual composite/kernel compile; new build helper/test/note. Exclusive heavy-build slot. |

Only the coordinator accesses the phone. A bounded read-only snapshot at the
start of this wave still identifies r0s, native guardian, GNU build ID
`b2dda820b18d410d9bf12f1bd2584567d545991d`, 325 modules and stable uptime
439230 seconds. Persistent mounts, remote control, model API/idleness, desktop,
desktop Pi, browser terminal and dedicated Pi session are healthy. Battery is
100%/Full; maximum readable thermal zone is 44.0 C. The available kernel ring
has no fatal/hung-task indicators, but does not cover the full boot and is not
proof of TrustZone or NPU request progress. No flash, reboot, event-device open,
control/PCM operation, modem request or inference is part of this wave.

Independent rescue remains unproven, consumed trial authorizations stay
consumed, and NPU BOOTUP remains refused. Implementations and any real host
build require independent review before publication; none is permission for
deployment. Results and frozen commit identities will be appended here.

### Actual build/audio/mailbox results

The fourth actual Luna/Max worker,
`/root/host_build_audio_review_20261002`, used the isolated
`host-build-audio-review-20261002` worktree and owned only the review note.
Accepted explicit model/reasoning configuration is the same evidence level
as the three implementation workers above. Completed workers were reused
for follow-up implementation and review; no worker accessed the phone.

- Audio author `eb25aa2` / integrated `5a855f6` makes metadata collection
  passive by default and bounds files, XML and subprocess capture. Independent
  review `787f3c5` / integrated `d15658a` reproduced an exception-path child
  leak. Author fix `6ebc3f6` / integrated `04dc86f` added four fail-before,
  pass-after cases. Review `ccaf1a6` / integrated `5b0472c` clears the fixed
  collector's narrow scope: all 12 tests passed in each Python mode. The
  original negative review remains in history. No mixer/PCM operation or
  installed phone tool was changed.
- NPU author `0faa2c2` / integrated `6be90f2` adds the missing-callback
  message-ID reclamation patch and actual extracted publisher/allocator C
  tests. Independent review `330a205` / integrated `0a29bc4` clears this
  source/host scope only. The BOOT_IOCTL guard preserves the distinct legacy
  POWER_DOWN slot-zero behavior. Callback stalls are released by the host
  harness; the real publication drain remains unbounded and BOOTUP refused.
- Build author `9998ea4` / integrated `a1b7da2` supplies a guarded host
  builder and focused tests. The independent review reproduced a further
  cleanup defect: reaping the make leader did not prove group descendants
  were gone. This finding remains blocking for that frozen helper until the
  separate executable negative-control correction is independently retested.

The single actual `-j1 Image modules` invocation completed successfully. It
built clean source `e5af0ba1cefc959094d03e1a136b8e33ff938b2a`, the exact ordered
six-patch NPU stack on pinned base `4e5c5ad7d950e4de0688b5663965f2075654b2ad`.
The preserved config stayed SHA-256
`a147841a53f5b10c366a759d0e83525996a0ec5d8227a103b020cf2111400f9e`, including
ThinLTO, CFI, MODVERSIONS and shadow call stack. Actual build outputs are:

| Artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| ARM64 `Image` | 33513984 | `86e56e2c65e17c9e8df7c826696c9fd143f089bc305393e1b8b9ad1a8c1eabd6` |
| NPU module | 17323960 | `639e5a3947fe4db360ba21bc3ec3b2f2bc7734fccf24489a4ceeb4b4f05aa2d1` |
| `vmlinux` | 586724072 | `2fb35d4ac9b023cdc6a83650966fb59e3899b921a6ef3a434674fcb097fc1c34` |

The coordinator independently recomputed these hashes. Kernel GNU build ID is
`146a0f11148bd8a46514ecee6f1c09622593f79f`; NPU module build ID is
`8ca6aecea1647a746a27be52b047afd9ea32f844`. The build produced 329 configured
modules and release `5.10.260-ge5af0ba1cefc`. All 234 NPU imported-symbol CRCs
match this build's `Module.symvers`, including `module_layout=0x0e3c515c`.
That is **same-build correspondence**, not compatibility with the phone's
325 loaded modules. The standalone compile profile does not include the
working native kernel's other HCI/close-range/camera changes; it is not a
deploy-ready replacement. No image was packaged, loaded or flashed.

The build actually executed launch-time helper SHA `13cec8b3...`; subsequent
future-helper corrections did not control or restart that invocation. The
build-specific note records the full identities and final receipt separately.

The complete host allowlist at `5a2936133425fab9c71778007951ad2f09bd333a`
attempted 46 normal and 43 optimized scripts, with three existing optimized
exclusions. It returned failure: 45/41 passed, but three public-source fetches
returned exit 77. A bounded public rerun passed audio IPC observation and the
full NPU lifecycle test; optimized shutdown propagation remained unavailable.
An explicitly selected SHA-verified local fixture passed that remaining test
separately. There was no hidden fallback or conversion of the initial failed
public suite into a PASS. Private full/rerun logs are preserved; hosted CI
for the new final source tree still needs verification.

A second coordinator read-only native snapshot at 14:22:45 UTC verifies the
same running GNU build ID `b2dda820b18d410d9bf12f1bd2584567d545991d`, 325
modules, 442934.97 seconds uptime, native guardian/persistent mounts,
remote-control readiness, healthy idle model API, desktop Pi, browser service
and the exact dedicated Pi session. Battery is 100%/Full, maximum readable
thermal zone 42 C. The available ring has no serious indicators but still
does not cover the full boot. Private receipt hash is
`cad0e6e810c56b993d0c25382c3a7bea8351314e1bcbf17fd23c0b8ad5e86da2`.
No fresh RECOVERY partition readback was taken in this host-only wave.
New device writes, reboots, powered trials and inference requests remain zero.

### Final independent retests and source-wave publication

Builder author `d5ba8e0` / integrated `65a85a1` repairs whole-group cleanup;
`3283017` / integrated `2f17e69` also preserves the primary monitor exception
when cleanup itself raises. Independent review `e844b7b` / integrated
`fc60216` clears this final future helper after all 17 tests in each Python
mode, original real-process reproductions and artifact/CRC cross-checks.
Doc-only `6636770` corrects the wording and records each helper identity.
The earlier negative finding remains historical; these fixes did not control
or repeat the one completed build.

The same explicitly selected publication worker implemented the separate
optional diagnostic-walk patch: author `ee7ae49`, integrated `b63c089`.
It compiles the pinned production command-v10 header/dispatcher and mailbox-v9
C, reproduces a constructed baseline non-progress loop, and tests bounds,
producer padding, marked records and rollover refusal. The initial draft's
wrong v9 command/MARK assumption was corrected before freeze and is recorded,
not counted as accepted evidence. Independent Luna review reused the completed
build worker in its separate worktree: author `3f7cde5`, integrated `6ffd8ed`.
Local exact-source tests passed all Python modes with C `-O0`/`-O2`; that
reviewer's public fetch remained unavailable and is explicitly recorded.
This bounds a diagnostic walk, not the full publisher or its unbounded drain.

`f98bbcf` adds exactly three CI allowlist entries for the new diagnostic test;
seven runner-policy tests pass in each mode, independently verified. The
complete 47-normal/44-optimized suite attempted every script, returning two
exit-77 public read timeouts. Both failed invocations passed a separate bounded
public rerun. Thus all 91 invocations are covered across the preserved original
run and retry, **not** a rewritten one-shot suite PASS. Existing individual
AVB/source-audit fixture skips and three optimized exclusions remain visible.
Host-suite logs and retry hashes are bound by the sanitized source-wave JSON.

An ordinary ordered temporary-index check applied all eight exact patches with
no force/fuzz. The six-patch tree matches the actually compiled source; the
eight-patch source tree is `020822e0101392587ebb35d6f37e9dc4ec60bb73`.
The kernel worktree and build outputs were unchanged. Optional patches seven
and eight were **not** included in the completed six-patch kernel build.

This closes the scoped implementation/review wave, not the driver mission.
NPU publication-drain/object lifetime, integrated native-kernel compatibility
and hardware acceptance remain unresolved. No image, firmware, weights,
credentials or private trace is selected for publication. Only the unmerged
review branch will be pushed; master remains unchanged. Exact final-head
hosted CI still requires independent verification after publication.

The code/receipt publication `3cdcef37e0224582661d27df3b3a6983b282ecbd` was
verified on the remote review branch, with remote master still `20605dbe`.
Exact-head hosted [run 37023741073](https://github.com/corpunum/unumS22linux/actions/runs/37023741073)
passed all 47 normal and 44 optimized scripts, retaining the three documented
optimization exclusions and individual optional-fixture skips. Log inspection
confirms both new NPU extracted-C suites executed, the malformed baseline
loop was reproduced, the final 17 builder tests ran, and BOOTUP refusal stayed
active. No public-source exit-77/availability skip occurred in that hosted
run. Its private log SHA-256 is
`9a22c5305d023cbe1cfe4570e29cc0cba69f3d9ad98abcbc982af651bf18c14d`.
This follow-up changes only public result documentation, not tested code.
