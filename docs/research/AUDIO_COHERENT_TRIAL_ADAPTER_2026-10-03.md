# Audio coherent RECOVERY trial adapter — 2026-10-03

## Result and scope

Implemented a host-side forward/reverse execution adapter and a separate
one-shot RECOVERY reboot/initial-observation adapter for the existing pinned
audio profiles. Both CLIs default to a local artifact plan. The reserved
identity `audio-coherent-20261003-first` is data only: deployment authorization
is `false`, the production trial allowlist is empty, and no command-line option
can change either value. Stage, flash, reboot, and live observation therefore
refuse before creating receipt directories, operation markers, or contacting a
device.

The host tests exercise the actual reviewed shared stage/flash renderer inside
its established temporary fake-filesystem fixture. A test-only synthetic
profile and fake authorization are confined to temporary in-process fixtures;
they do not activate the production identity or alter a live marker. Tests,
plan validation, and source checks made no SSH/ADB/device request, partition
write, reboot, kernel build, package build, or artifact transformation.

This is not owner authorization, deployment approval, rollback execution,
bootability evidence, or audio hardware acceptance. A later owner instruction
and independent review are still required before any production authorization
change. The current adapter intentionally has no mechanism for recording that
future authority.

## Pinned profile behavior

Both directions bind to the exact 100,663,296-byte images and manifest already
validated by the profile helper:

| Profile | Required current image | Target image | Required loaded audio IDs before flash/reboot |
| --- | --- | --- | --- |
| `audio-forward` | Camera recovery baseline, SHA-256 `b10412715756da3cc8ee221368b49f179cc0c64ab7bd2802976480905e6d8d2f` | Audio candidate, SHA-256 `6b788b23f54b9b8e84212187544064949b72af20c167cbb02392cbe53ee5a6ab` | ABOX `34a5354a75980688ee7dbeb6a848e04a7d54558f`; rainbow `510b984887b640ad4ad3c1e9a556ef16c30b38c9`; offloader `4f35c50b0eca0d22b2060f7b8d84f03359feacd1` |
| `audio-reverse` | Audio candidate, SHA-256 `6b788b23f54b9b8e84212187544064949b72af20c167cbb02392cbe53ee5a6ab` | Camera recovery baseline, SHA-256 `b10412715756da3cc8ee221368b49f179cc0c64ab7bd2802976480905e6d8d2f` | ABOX `26347c3373e155fa6badf7883ff162f1d9f6723f`; rainbow `8a7227b58cb7f4faf73ea92974781d34870bbfac`; offloader `8c9b0d4787ea32eae7de0086a4d662b0f351675d` |

The adapter pins the existing profile, shared deployment, trial guard,
camera identity/reboot helpers, audio readiness helper, Pi readiness,
TrustZone classifier, and SSH wrapper by content hash. Before an operation it
rechecks those sources and the candidate manifest/image. Stage and flash use
the pinned shared renderer and its exact full-image readback receipt. Receipts
are private, exclusive, single-use, exact-schema files linked to the existing
trial guard markers. Flash additionally binds the prewrite identity and boot
ID, staged receipt, raw readback, same-boot postwrite identity, and the
direction-specific pre-state audio module IDs. Unknown or malformed outcomes
remain unresolved and are never retried.

The separate reboot path requires the bound flash receipt, unchanged boot and
pre-state module IDs, current RECOVERY image identity, native readiness, and
the exact pinned native reboot-helper identity. It can dispatch the existing
boot-ID-guarded reboot command once. An ACK is not boot proof. A disconnect or
ambiguous result is recorded `UNKNOWN`; it is never resent and its guard marker
is not cleared. One subsequent read-only observation can verify a changed
boot ID, RECOVERY record, full installed image hash, pinned kernel GNU Build ID,
and all three direction-specific post-boot module IDs. For an acknowledged
request, the observation is linked to the guard marker; for an unknown request,
observation does not take or clear the unresolved reboot marker. The result is
only one initial snapshot, not a stability interval.

The initial snapshot's kernel diagnostics are a readiness gate, not just
receipt decoration. The adapter extends the rendered, pinned collector at its
unique JSON output point to export the classifier's actual
`input_available` value. Readiness requires exact boolean `true` for input,
capture completion, and coverage completion; an empty successful `dmesg`
therefore fails because the classifier reports `assessment="incomplete"` and
`coverage_complete=false`. This operation-specific gate also requires the
classifier assessment `no_indicators`, no fatal/hung-task/call-trace counts,
and no unresolved liveness. A `trace_only` assessment is not called fatal or a
proven lockup, but it is not sufficient for this first-observation readiness
decision. The receipt preserves the classifier assessment, availability,
counts, and the exact boolean `full_boot_log_coverage`. That field is retained,
not promoted or required to be either value; the current pinned collector
reports `false`, and a nonempty complete current ring does not imply full-boot
coverage. A future pinned collector may report `true` only if it has an
independent basis for that claim.

The observer still takes exactly one initial post-reboot sample. A separate
host-side bounded stability follow-up is now implemented, but remains
unauthorized by the production adapter. It requires the already successful
one-shot initial-observation receipt and revalidates its boot, image, kernel,
module, readiness, and log fields before starting. The initial sample is not
reused as an interval sample. A successful initial receipt can follow an ACK
or an UNKNOWN reboot outcome; in the latter case the reboot marker remains
unresolved and unchanged, the observer performs read-only queries only, and no
guard marker is cleared or replayed.

The observer has a hard 600-second monotonic total deadline. It first polls
only boot ID and `/proc/uptime` at 15-second offsets from 0 through 180
seconds (at most 13 probes), each with a 3-second query cap. Every probe must
match the boot ID in the successful initial receipt; a contradictory boot is
terminal, not a startup wait. The uptime gate is at least 180 seconds. If the
initial receipt itself was collected after that uptime, the first probe can
pass immediately; otherwise the observer waits for the gate, within the same
600-second budget. After the uptime gate it makes up to two complete,
read-only readiness/full-identity poll pairs, separated by 15 seconds. This
allows ordinary native/model startup progression without calling an absent
service a transport failure. A missing expected service, incomplete current
log ring, or other readiness gate remains unready and cannot pass a stability
sample. No service is restarted and no remote inference is requested.

The first fully ready pair starts the interval. Two more fresh readiness and
full identity pairs are queried at offsets of at least 90 and 180 seconds from
that sample start; the minimum pair-start gap is 60 seconds. Success therefore
requires three separately queried samples and at least 180 seconds from the
first to last sample start—not eleven samples or ten minutes of evidence. Every
sample rechecks the same boot ID, RECOVERY record, complete recovery-image
SHA-256, running kernel GNU Build ID, and exact three profile module GNU Build
IDs, along with native/model-idle/network/power and current-ring no-indicator
gates. Each readiness, identity/full-hash, and uptime SSH query gets the
smaller of its per-query cap (15, 30, or 3 seconds respectively) and actual
remaining total time. A single read-only reconnect is allowed across the whole
observer after a bounded 2-second backoff; no second reconnect or reboot is
attempted. The observer checks the total deadline before every query and
wait. The capped remote-query/wait path is at most 505 seconds: at most 188
seconds for the 180-second uptime schedule and a final probe/reconnect, plus
at most 317 seconds for two readiness polls (including the 15-second gap) and
the 180-second sample spacing with the last pair/reconnect. This leaves 95
seconds within the 600-second total for local processing and durable receipts.
The monotonic deadline remains authoritative if any work consumes that margin;
no query starts with more than the actual remaining time.

Transport unavailability, readiness progression, and terminal contradiction
are recorded separately. One disconnect may consume the single reconnect;
exhausted transport failures, startup timeout, later readiness regression,
wrong identity, changed boot, serious fault, malformed response, or consumed
marker all persist `UNKNOWN` with `retry_allowed=false`. A fatal indicator or
hung-task assessment/count is terminal; `trace_only` is not relabeled fatal,
but is insufficient for a sample and may only be followed by another bounded
read-only startup poll. Duplicate/cached state is not counted as a new sample:
the observer issues separate collection and identity queries for each sample
and stores a private fsynced receipt for each. Old receipts and guard markers
remain exclusive and preserved.

This follow-up establishes only bounded stability during the observed sample
interval. Full-boot log coverage still requires an independent source that
proves collection from boot start without truncation/overrun, and TrustZone
progress still requires a separate relevant progress signal; neither is
implied by a quiet current ring, a recognized thread name, or a call trace.
The new evidence is host-only: the production authorization remains false and
the allowlist remains empty.

## Verification

The 35-case hardware-free suite passed in normal Python, `python3 -O`, and
`PYTHONOPTIMIZE=1 python3 -B`. Each invocation checked the expected
`sys.flags.optimize` value (`0`, `1`, and `1` respectively); `-I` was not
combined with the environment mode. The new tests execute the actual rendered
collector with `dmesg` and local HTTP/process probes stubbed, feed its
classifier output through the actual readiness validator, reject empty and
unavailable captures, exercise malformed boolean/type/assessment fields, and
verify that incomplete diagnostics leave the one-shot observation marker
`unknown` with no success observation receipt. Fake-clock/fake-transport tests
execute the actual bounded stability observer without real waits; they cover
delayed connection and uptime, delayed readiness, fresh same-boot samples,
wrong and later-changed boot/image/module identity, fatal and hung-task
diagnostics, trace-only startup gating, later fault, exhausted transport gap,
deadline exhaustion, empty logs, nonprogressing clock, UNKNOWN reboot
non-replay, and refusal to reuse old stability receipts/markers. `py_compile`
and `git diff --check` also passed.

The fake-filesystem tests execute the actual rendered remote code for both
profile directions with a 4,096-byte synthetic image. Stage produced zero
modeled partition-write bytes; flash performed one exact synthetic write and
full readback. The suite covers exact profile/source-role module IDs before
flash and reboot, bad current partition state, malformed and type-confused
receipts, receipt/marker replay, boot change during flash, partial write,
unknown transport, reboot helper mismatch, one-request disconnect handling,
and rejection of wrong boot/hash/module IDs during observation.

Read-only local plan validation against the actual candidate, baseline, and
manifest passed for both directions. All four default deploy plans and both
observer plans (forward/reverse) exited zero and reported `authorized: false`,
an empty allowlist, and no execution, marker, write, reboot, or audio
acceptance.
The checked candidate manifest remains the one previously reviewed at
SHA-256 `f78bbaddf94ef6d1ae37b059b7455b24a15cbb7c2fab1ec9bbc4e1df5fd1edf8`.

## Limits

The fake remote filesystem is not the device, and these tests do not establish
kernel loader membership, runtime module compatibility, boot success, stable
services, powered audio/PCM/DMA behavior, suspend/resume, or audio quality.
No actual operation was attempted. The `audio_hardware_acceptance` and
`bootability_claim` receipt fields remain false. Unknown state is intentionally
fail-closed; recovery from it requires separate inspection and authority, not
an automatic retry.
