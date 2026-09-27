# Camera RECOVERY deployment profile — 2026-09-27

## Status and pinned artifacts

This change adds a narrowly scoped host adapter for replacing the `fimc-is.ko`
record in the current HCI-compatible RECOVERY image. It does not rebuild an
image, access a phone, open a camera node, or establish bootability. The
default invocation prints a host-only plan. `--stage` and `--flash` require
both `--execute` and the fixed one-use identity for that direction and mode;
those CLI inputs express requested intent only and are not owner authorization
or evidence that the device is safe to operate.

The adapter verifies the sanitized package receipt in this checkout and the
private package manifest plus image files under `/home/corpunum/s22-linux`.
It pins the complete recovery image hashes and size, so a different file or
manifest fails before the transport helper is reached:

| Role | Exact image | SHA-256 | Module identity |
| --- | --- | --- | --- |
| Current HCI baseline / rollback | `builds/bt-hci-loader-compatible-20260924-repro/recovery.img` | `42da267f3dd9f94f30f62a95fb2ac13f91d4cf98f1a2307f7cc14e45d9c49be5` | `fimc_is` GNU build ID `8286071582b5efedff0e0c6169ba1a23018fb814` |
| Camera candidate | `builds/camera-module-recovery-20260927/recovery.img` | `b10412715756da3cc8ee221368b49f179cc0c64ab7bd2802976480905e6d8d2f` | `fimc_is` GNU build ID `59e54c032c545fff3ba52156f226fb6d69aadf64` |

Both images are 100,663,296 bytes. The private candidate manifest is pinned
to SHA-256
`dc6970230542ab1ab391d2964ff64d88ecba150097de3a4d44f8dc856a1a00b8`; the
checked-in package receipt is pinned to
`841e14b428e7832286cecb768cd14ad1305694f47c232ee8f6881a73978b3385`.
That evidence says only `lib/modules/fimc-is.ko` changed in the archive;
kernel, DTB, DTBO, module exports/versions, dependencies and aliases were
preserved. It records zero partition writes/reboots and explicitly says
bootability, independent hardware rescue, camera-open behavior, and deployment
were not demonstrated.

## Exact directions

| Profile | Required current RECOVERY hash | Exact image staged/written | Stage directory | One-use stage / flash IDs |
| --- | --- | --- | --- | --- |
| `camera-forward` | HCI baseline `42da267f…c49be5` | Camera candidate `b1041271…d8d2f` | `/srv/s22/camera-recovery-forward-20260927` | `camera-recovery-20260927-forward-stage` / `camera-recovery-20260927-forward-flash` |
| `camera-reverse` | Camera candidate `b1041271…d8d2f` | HCI baseline `42da267f…c49be5` | `/srv/s22/camera-recovery-reverse-20260927` | `camera-recovery-20260927-reverse-stage` / `camera-recovery-20260927-reverse-flash` |

The shared `deploy-audio-recovery.py` remote renderer is reused with the
camera-specific exact before/write hashes and separate staging names. Its
existing gates check the RECOVERY alias/device identity and capacity, PID 1,
kernel release, battery temperature, mount state, hashes and readback. Stage
stores a verified rollback copy and candidate but reports
`partition_written: false`; flash writes only the fixed RECOVERY target after
rechecking the exact expected current hash and verifies full readback. The
adapter never retries and never reboots. The reverse profile is a byte-exact
restoration path: it requires the current complete camera-candidate image
hash, but does not require the camera module to be healthy or loaded in order
to restore the HCI image.

Each stage and flash operation has a distinct never-reused identity under the
existing host-global `device-trial-guard`. It writes a durable pending marker
before transport; a timeout, partial transport, malformed receipt, or local
receipt failure leaves pending/unknown state and blocks another operation
until explicit reconciliation. A success receipt is durable before the guard
marker is terminalized. The fixed global lock/state root is not CLI-overridable.
The approved SSH wrapper and known-hosts context are resolved from the pinned
artifact root, while evidence and helper source remain in this checkout.

## Not authorized / next condition

Do not invoke `--stage` or `--flash` in this state. The exact artifacts and
rollback profile are now host-identified, but the independent rescue path is
not demonstrated and there is no exact camera-trial unattended acceptance
record. A recovery-image hash and a CLI opt-in do not supply either condition.

This adapter deliberately has no reboot, reconnect, or post-boot observer.
Its module build IDs are expected artifact identities; it does not measure the
actual live pre-write kernel ID, camera-module ID, or boot ID. A separate
read-only pre-write identity receipt must be bound to the full RECOVERY
readback receipt and boot ID before a reviewed camera-specific observer may
request exactly one verified native `s22-reboot recovery`. That observer must
then perform a bounded read-only reconnect and check the resulting RECOVERY
hash, running kernel/module IDs, and stable system health (including an
explicit candidate-versus-rollback result). If the boot ID changes between
the bound pre-write receipt and observer, reconcile the state; do not issue
another reboot automatically. Do not reuse the consumed HCI trial IDs, HCI
raw reboot observer, or its prior one-shot authorization. Firmware staging
and resource-safe camera open/query/close remain later, separately gated
operations; this profile does not perform them.

Forward acceptance must also require that `fimc_is` is already loaded and its
live GNU build ID is exactly `59e54c032c545fff3ba52156f226fb6d69aadf64`;
the packaged `modules.load` includes `fimc-is`, so an absent or mismatched
module makes candidate acceptance/readiness unknown. The observer must not
run `modprobe`, open a camera node, issue camera ioctls, or infer readiness
from the RECOVERY image hash alone. Rollback remains based on exact full-image
hashes and must not require the candidate module to be healthy before
restoring the HCI image; after rollback, report the loaded old module ID only
if it is observed.

After independent rescue, exact rollback/readback, the observer, and explicit
candidate-specific authorization are reviewed, the operator should stage
first and inspect its no-partition-write receipt. Flash remains a separate
one-shot operation and any uncertain result requires reconciliation rather
than retry. This document records no such approval or device operation.

## Host tests

The executable regression checks exact forward/reverse role mapping, pinned
artifact and manifest rejection, durable receipt/guard semantics, one-use
identities, unknown outcomes, and the exact shared rendered remote body using
the existing fake-filesystem fixture. The rendered stage/flash integration is
synthetic host evidence, not phone functionality.

```sh
python3 -I -B tools/hardware/test-camera-recovery-profile.py
python3 -O -I -B tools/hardware/test-camera-recovery-profile.py
PYTHONOPTIMIZE=1 python3 -B tools/hardware/test-camera-recovery-profile.py
```
