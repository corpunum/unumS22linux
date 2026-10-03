# Independent review: audio coherent RECOVERY profile — 2026-10-03

## Verdict

**BLOCKED narrowly on strict receipt validation.** The frozen source correctly
keeps its plan path host-only and non-executing, pins the two recovery images
and package manifest, and validates ordinary direction, identity, and hash
cases. However, its receipt checks accept JSON integer `0` where the receipt
schema requires boolean `false`. Until exact JSON types are enforced and
regressed, a receipt must not be described as strictly shape-validated. This
finding blocks reliance on the receipt classifier; it does not reveal or
create a stage/flash transport path.

The four frozen author files were not modified. The review worktree imported
the exact source commit `bddb66ac717b3668898eb78c825d02aadbba254b` unchanged
as `830a7bb2c379ce77bb9d8eb15d4c354febe93d36` on base
`d18459a5591ec64322263fa1cd56aa36d164d153`; the four file contents compare
byte-for-byte equal to the author commit. Selection evidence is the explicit
`gpt-6-luna` / `max` assignment; that configuration is not backend
attestation.

## Image identities and direction mapping

I independently reread and hashed the host artifacts. Both recovery images
are exactly 100,663,296 bytes; the candidate manifest is 26,524 bytes.

| Profile | Before | Target | Staging child / rollback name |
| --- | --- | --- | --- |
| `audio-forward` | B104 camera baseline `b10412715756da3cc8ee221368b49f179cc0c64ab7bd2802976480905e6d8d2f` | audio candidate `6b788b23f54b9b8e84212187544064949b72af20c167cbb02392cbe53ee5a6ab` | `/srv/s22/audio-coherent-forward-20261003` / `camera-baseline-rollback.img` |
| `audio-reverse` | audio candidate `6b788b23f54b9b8e84212187544064949b72af20c167cbb02392cbe53ee5a6ab` | B104 camera baseline `b10412715756da3cc8ee221368b49f179cc0c64ab7bd2802976480905e6d8d2f` | `/srv/s22/audio-coherent-reverse-20261003` / `audio-candidate-rollback.img` |

The candidate-manifest SHA-256 is
`f78bbaddf94ef6d1ae37b059b7455b24a15cbb7c2fab1ec9bbc4e1df5fd1edf8`.
The pinned Lineage comparison image hashes to
`b5bf01c4a47091eb95078fc69b133b44c2b453b31c23433594c5b605e3747b55`.
The actual-artifact test passed for both directions with the optional private
root explicitly configured; the portable test mode also passed with that
root unset and the fixture explicitly skipped.

The manifest records 324 ramdisk modules / 16,569 imported-version rows and
495 selected external WLAN imports. These remain package-static counts, not
module-loader membership or runtime compatibility evidence. The manifest
records AVB algorithm `NONE`; its footer/hash verification is not Samsung
authentication or bootability proof.

The package manifest pins the base kernel Build ID reference
`b2dda820b18d410d9bf12f1bd2584567d545991d`; it does not provide a separate
candidate-kernel Build ID field. I unpacked the B104 and candidate images
read-only and compared the kernel payloads: both are 32,532,992 bytes with
SHA-256
`7738564db77e4a6183ffaa875fc12f8168a58e8135a3bdc86e27b127b47fc05c`.
Thus the candidate retains the same kernel bytes/build identity; this review
does not claim a separate ELF `readelf` Build ID from the Android Image.
The manifest records these old-to-new Build IDs for the three changed audio
modules, and `readelf -n` on the actual candidate `.ko` files confirmed each
candidate value:

| Module | Base Build ID | Candidate Build ID |
| --- | --- | --- |
| `snd-soc-samsung-abox.ko` | `34a5354a75980688ee7dbeb6a848e04a7d54558f` | `26347c3373e155fa6badf7883ff162f1d9f6723f` |
| `rainbow_prince.ko` | `510b984887b640ad4ad3c1e9a556ef16c30b38c9` | `8a7227b58cb7f4faf73ea92974781d34870bbfac` |
| `exynos-usb-audio-offloading.ko` | `4f35c50b0eca0d22b2060f7b8d84f03359feacd1` | `8c9b0d4787ea32eae7de0086a4d662b0f351675d` |

## Source and behavior review

The profile constants and forward/reverse map are in
`tools/hardware/audio-coherent-recovery-profile.py:21-59`. The profile checks
the exact pinned shared helper hash before importing it (`:81-101`), performs
bounded no-follow regular-file reads (`:104-138`), pins the manifest digest
and requires its host-only / no-deployment / no-module-load / no-boot-
authorization facts (`:161-222`), and delegates actual image byte/hash
checks to the pinned helper (`:225-253`). It uses that helper only to render
the two fixed direction-specific scripts (`:256-263`).

Receipt mode, exact key set, profile, trial identity, and direction-specific
hashes are checked at `:266-305`. The defect below is in this receipt path.
The plan path records execution, stage/flash CLI, deployment authority,
independent rescue, marker creation, partition write, reboot, and
boot/audio acceptance as false (`:317-346`). The profile CLI only accepts a
profile, an artifact root, and an optional local receipt, then builds and
prints a plan (`:349-365`); it does not invoke the helper transport or write
an operation marker. Tests mock the transport to fail if called and reject
`--stage` / `--flash` (`tools/hardware/test-audio-coherent-recovery-profile.py:245-272`).

The shared helper has a rendered stage/flash template, including flash writes
(`tools/hardware/deploy-audio-recovery.py:230-347`), and separately contains
transport/CLI routines. In this profile, only the pinned renderer and
read-only validators are called. `render_remote` validates substitution
markers and compiles the generated text (`deploy-audio-recovery.py:350-377`);
the profile CLI only hashes that text. The fake-filesystem test executes the
actual shared rendered **stage** body for both directions and verifies the
rollback/candidate staged hashes while modeled partition-write bytes remain
zero (`test-audio-coherent-recovery-profile.py:340-393`). The flash body was
not executed. This helper/fake-filesystem result is not evidence of a real
provider, transport, rescue path, or device-side deployment.

## Confirmed blocker: JSON bool/int receipt confusion

The wrapper requires exact receipt keys at profile lines `270-283`, then
delegates outcome comparisons at `288-294`. The shared validator checks
expected fields with ordinary equality at
`tools/hardware/deploy-audio-recovery.py:722-737`. Python considers integer
`0` equal to boolean `False`, so malformed JSON values pass these checks:

```python
# Reproduction against the frozen profile module (Python 3.12.3).
import importlib.util

spec = importlib.util.spec_from_file_location(
    "review_profile", "tools/hardware/audio-coherent-recovery-profile.py"
)
profile = importlib.util.module_from_spec(spec)
spec.loader.exec_module(profile)

stage = {
    "mode": "stage", "partition_written": 0,
    "backup_sha256": profile.BASELINE_SHA256,
    "candidate_sha256": profile.CANDIDATE_SHA256,
    "profile": "audio-forward",
    "trial_identity": profile.TRIAL_IDENTITY,
}
flash = {
    "mode": "flash", "partition_written": "recovery",
    "bytes": profile.PARTITION_SIZE,
    "before_sha256": profile.BASELINE_SHA256,
    "readback_sha256": profile.CANDIDATE_SHA256,
    "reboot_performed": 0,
    "profile": "audio-forward",
    "trial_identity": profile.TRIAL_IDENTITY,
}
print(profile.validate_receipt("audio-forward", stage))  # accepted
print(profile.validate_receipt("audio-forward", flash))  # accepted
```

The output for both cases was `receipt_validated: true`; the offending
runtime types were `int`, not JSON `bool`. Corrective work should require
exact JSON types (including `type(value) is bool` for boolean fields), and
add regressions for both stage and flash receipts under normal and optimized
Python. This review did not edit the frozen source.

Additional bounded negative controls rejected: missing stage fields, an
unknown extra outcome, unknown mode, stale trial identity, wrong profile,
wrong flash readback, and a synthetic manifest whose integrity hash was
updated for the test but whose `boot_authorized` was changed to true. The
last control reached the scalar gate and failed specifically because
`boot_authorized` was not false. No source-level manifest-type issue was
demonstrated; the real manifest remains protected by its exact pinned digest.

The published 13-test suite has a coverage gap for the bool/int cases: with
the private artifact root unset, each mode reported 12 passed and the
optional private test explicitly skipped; with the private root configured,
all 13 passed. These test passes do not clear the demonstrated receipt
defect.

## Separate prior artifact evidence and limits

The earlier stripped-artifact review remains `PASS_LIMITED` at commit
`7fedb5ba249d2685ce4595c1c8132c7267bfea17`, scoped to host artifact integrity,
package scope, and static ELF/MODVERSIONS checks. That review explicitly
recorded that private phase JSON/raw logs were unavailable to it and did not
claim independent inspection of those logs. The later coordinator
reconciliation at `61bf29031ff9d4200fbed0292f663bf23719ed52` records a
retained capture with launch ID `201c9d90-71a0-48a9-a8d5-46edea60004f`, exit
0, 13.281 seconds, stdout 22,166 bytes / stderr 0, and recomputed candidate
image and manifest hashes. It sets `historical_review_gap_preserved: true`;
that separate root-side reconciliation does not retroactively erase the
earlier reviewer’s gap or establish bootability, Samsung authentication,
module loading, audio DMA progress, or device acceptance.

No phone, SSH, ADB, build, repack, partition operation, reboot, or hardware
test was performed for this review. Host plan validity is not permission or
live-install evidence. Both `current_deployment_authorized` and
`independent_hardware_rescue_demonstrated` remain false. No audio hardware
acceptance is claimed.

## Frozen inputs and test matrix

- Review source: `830a7bb2c379ce77bb9d8eb15d4c354febe93d36` (base
  `d18459a5591ec64322263fa1cd56aa36d164d153`); author source:
  `bddb66ac717b3668898eb78c825d02aadbba254b`.
- Profile tool SHA-256:
  `40526594559cc839d7d3b5371c4dff3480d6a5c69394476eedd8047abbe5f0cc`.
- Profile test SHA-256:
  `c13b8269d5bb9f4dea7f0862f76203f9a7a8d99aeb79fb1e4f54ab02c60d32d3`.
- Shared deploy helper SHA-256:
  `d0c54cc306e3a8a80cb79d79958fceedf6bb5945dabc7606e02b12d27dc00fd3`.
- Shared fake-filesystem fixture SHA-256:
  `10efaccce960c9fb7a0bc774715b56e16b00debeaf65cdd8ce8c6a59501fb424`.

Python 3.12.3 results with `S22_AUDIO_PROFILE_ARTIFACT_ROOT` unset:

| Run | `sys.flags.optimize` | Result |
| --- | ---: | --- |
| `python3 -B` | 0 | 12 passed, 1 explicit private-artifact skip |
| `python3 -O -B` | 1 | 12 passed, 1 explicit private-artifact skip |
| `PYTHONOPTIMIZE=1 python3 -B` | 1 | 12 passed, 1 explicit private-artifact skip |

With `S22_AUDIO_PROFILE_ARTIFACT_ROOT=/home/corpunum/s22-linux`, the optional
real-artifact check was enabled and all 13 passed, including both direction
validations. This was read-only host validation, not an operation or device
acceptance.
