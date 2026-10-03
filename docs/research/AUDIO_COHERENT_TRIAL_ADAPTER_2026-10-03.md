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

## Verification

The 20-case hardware-free suite passed in normal Python, `python3 -O`, and
`PYTHONOPTIMIZE=1 python3 -B`. Each optimized invocation explicitly checked
`sys.flags.optimize == 1`; `-I` was not combined with the environment mode.
`py_compile` and `git diff --check` also passed.

The fake-filesystem tests execute the actual rendered remote code for both
profile directions with a 4,096-byte synthetic image. Stage produced zero
modeled partition-write bytes; flash performed one exact synthetic write and
full readback. The suite covers exact profile/source-role module IDs before
flash and reboot, bad current partition state, malformed and type-confused
receipts, receipt/marker replay, boot change during flash, partial write,
unknown transport, reboot helper mismatch, one-request disconnect handling,
and rejection of wrong boot/hash/module IDs during observation.

Read-only local plan validation against the actual candidate, baseline, and
manifest passed for both directions. All four default CLI plans (stage/flash
observer, forward/reverse) exited zero and reported `authorized: false`, an
empty allowlist, and no execution, marker, write, reboot, or audio acceptance.
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
