# Audio coherent RECOVERY profiles — 2026-10-03

## Additive receipt correction

The implementation below describes the initial frozen profile. Independent
review subsequently reproduced a JSON boolean/numeric type confusion. The
reviewed correction now requires exact primitive types and pins the shared
helper at SHA-256
`4138413c16b9c90c70a46da6ed3b4f60b0f41f1c6c69b4b2047421895903a47f`.
See `AUDIO_RECEIPT_JSON_TYPE_CORRECTION_2026-10-03.md` and the separate
independent correction review. The initial digest and receipts below remain
historical evidence; the remote rendered scripts and write/reboot operations
were not changed by that correction. No deployment authority follows.

## Result and boundary

Added a host-only validator for the reviewed audio recovery candidate and its
camera-recovery rollback baseline. The CLI requires an explicit forward or
reverse profile, checks the pinned image/manifest inputs, validates an
optional local receipt, and prints a plan. It has no `--stage`, `--flash`,
SSH, reboot, device-file, or operation-marker path. Its plan explicitly keeps
deployment unauthorized and independent hardware rescue unproven. The reserved
future identity `audio-coherent-20261003-first` is displayed as data only; no
trial directory or marker was created.

This is not package execution, deployment permission, flash/boot authorization,
Samsung authentication, bootability evidence, or audio acceptance. Artifact
checking read local host files only. No phone, SSH, ADB, stage, flash, reboot,
or packaging command was run for this profile work.

## Exact profiles

Both profiles use the existing, hash-pinned
`deploy-audio-recovery.py` renderer and receipt validator. Only the direction,
before/target hashes, staging child, and rollback filename differ:

| Profile | Required current image | Target image | Future staging child | Rollback filename | Rendered code SHA-256 |
| --- | --- | --- | --- | --- | --- |
| `audio-forward` | Camera recovery baseline `b10412715756da3cc8ee221368b49f179cc0c64ab7bd2802976480905e6d8d2f` | Audio candidate `6b788b23f54b9b8e84212187544064949b72af20c167cbb02392cbe53ee5a6ab` | `/srv/s22/audio-coherent-forward-20261003` | `camera-baseline-rollback.img` | `7eac798fa9420b2b7275fea94e754558992ba5cceea17d709fff42f1c195e526` |
| `audio-reverse` | Audio candidate `6b788b23f54b9b8e84212187544064949b72af20c167cbb02392cbe53ee5a6ab` | Camera recovery baseline `b10412715756da3cc8ee221368b49f179cc0c64ab7bd2802976480905e6d8d2f` | `/srv/s22/audio-coherent-reverse-20261003` | `audio-candidate-rollback.img` | `115d2f9a5f7961b719db95e767efc8826c5a48a819b16b3d46fb86493d4a9f12` |

The candidate is exactly 100,663,296 bytes; the baseline and pinned Lineage
reference are checked as exact-capacity, non-symlink regular files. The
candidate manifest SHA-256 is
`f78bbaddf94ef6d1ae37b059b7455b24a15cbb7c2fab1ec9bbc4e1df5fd1edf8`. It
binds the package to the stated baseline, three exact module replacements,
host-only/no-phone/no-load/no-boot-authorization flags, AVB footer hash
verification (algorithm `NONE`), and static ABI inventory of 324 ramdisk
modules / 16,569 imports plus 495 selected external WLAN imports. This is
static artifact evidence, not kernel-loader membership or hardware evidence.

The helper also verifies the shared deployment source hash
`d0c54cc306e3a8a80cb79d79958fceedf6bb5945dabc7606e02b12d27dc00fd3` before
importing its helpers. Artifact and manifest reads are bounded/read-only;
manifest and receipt JSON reject duplicate keys. Receipt validation requires
an exact profile, exact reserved trial identity, exact direction-specific
before/target hashes, the shared stage/flash receipt fields, and no unknown
fields. A timeout/unknown outcome, wrong readback, wrong direction, or extra
status field cannot be accepted as a valid receipt.

## Tests and local artifact check

The 13-case hardware-free suite passed in normal Python, `python3 -O`, and
`PYTHONOPTIMIZE=1 python3 -B` with `sys.flags.optimize == 1`. In these local
runs the optional private-artifact check was enabled and independently
validated both directions against the existing image, baseline, lineage, and
manifest bytes. In portable environments without the private artifacts, that
single fixture reports an explicit skip; the other 12 tests still run.

The tests include corrupt image and manifest rejection, symlink/oversize/
duplicate-key receipt refusal, exact identity and profile-direction checks,
unknown-outcome rejection, and CLI rejection of `--stage` / `--flash`. The
existing rendered remote stage body was executed unchanged in its established
temporary fake-filesystem integration fixture for both directions: the staged
candidate and rollback hashes matched, while the modeled RECOVERY partition
write count stayed at zero. The flash body was not executed. The plan path
mocked the shared SSH wrapper to fail if called and verified no fixture files
or operation markers were created.

Both real-artifact CLI plans exited 0. They report `execution: false`,
`host_only: true`, `stage_flash_cli_available: false`,
`current_deployment_authorized: false`,
`independent_hardware_rescue_demonstrated: false`,
`operation_marker_created: false`, `partition_written: false`, and
`reboot_performed: false`. Only these plans and receipt checks are implemented;
separate authorization and a later independently reviewed execution path
would be required for any device operation.
