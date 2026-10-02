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

The same explicitly selected NPU Luna worker continues a separate PM/clock
callback implementation in `npu-pm-callback-20261002`, with a new narrow patch,
test and document. That work is not included in the reviewed transaction result
until its own executable tests and independent review pass.

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
build, lockdep run or hardware test. Default PM/clock callback error masking,
ignored shutdown errors and recovery-close `BUG_ON` policies remain explicit
non-deployment limits; NPU BOOTUP is still refused.

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
