# S22 native Linux status

Updated 2026-09-20. Older states and experiments remain in `EXPERIMENTS.md`
and Git history; the old claim that nothing custom was flashed is obsolete.

## Current boot-attempt warning

At approximately 07:48 UTC, BOOT was written and read back with SHA256
`4aeb801486e35e2c71dab0e988c6ac14802b05a29d062ddd93834e74802b5b2e`.
RECOVERY (`1a827b43d29141efb47f530902dd4e4ee2b6d780515893c9ecd676ad27efd7d1`)
and vendor_boot (`383b6f6789e655b070929914db5639e6d9bacc78823ada7bf553962b5b4888be`)
were unchanged. The 07:48:18 UTC normal reboot had no SSH, ADB, or
Download-USB return by 07:56 UTC. Boot mode/runtime are unconfirmed; do not
claim a usable normal boot. Last accepted state is BORE519 recovery with
persistent Omarchy/model startup, readiness at 15.81 seconds and stable at
77 seconds.

## Last accepted working state (BORE519 recovery)

- SM-S901B/DS (`r0s`), Exynos 2200; unlocked S901BXXSIFYI3 bootloader.
- Native Alpine 3.24.2 ARM64, guardian PID1, kernel 5.10.260-g4e5c5ad7d950.
  AOSP first-stage bootstrap is retained; Android services do not run.
- RECOVERY V3 image, whole-partition SHA256 verified:
  `1a827b43d29141efb47f530902dd4e4ee2b6d780515893c9ecd676ad27efd7d1`.
- CACHE-backed Alpine package/file persistence, USB Ethernet/SSH and fallback
  Weston desktop; targeted software recovery reboot verified through BORE 519.
- Persistent Arch ARM, Hyprland 0.56.2, Omarchy v4.0.4 Quickshell bar, foot,
  Squeekboard. Internal 1080x2340 display, llvmpipe software rendering.
- Userdata ext4 holds desktop/runtime/model; all started automatically after
  recovery reboot with no host restaging. 349 registered packages; about
  100 GiB free storage and 4.7 GiB available RAM with model/UI running.
- Synthetic touchscreen → keyboard → local chat path; this is not a physical
  finger-sensing test.
- Resident Qwen3.5-2B Q4_0 CPU server, 4096 context, one slot, four fast cores,
  loopback 127.0.0.1:8089. Short first-text latency 0.4–0.7 seconds.

## Not finished

- Normal-BOOT selection is unconfirmed after the first BOOT write; recovery
  autostart and file/model persistence remain the last accepted state.
- GPU compute still faults despite a validated CPU-mapping lifecycle fix.
- NPU runtime/firmware integration and inference are unproven.
- Physical finger sensing, Wi-Fi, cellular, audio, cameras, suspend and daily
  use have not passed acceptance in the native Omarchy environment.
- The normal Omarchy installer was not run. Native signed pacman transactions
  reproduce a signature-helper hang; the clean root and signed dependency
  delta were installed on the host and transferred with their package records.
  Runtime/package checks work; native package installation is not accepted.

## Next step

The owner approved userdata conversion, accepted possible Android-data loss,
and then approved BOOT work. Private raw backups of 15 selected partitions
are checksum-verified; personal-file restoration remains unproven. Userdata
conversion and recovery autostart are complete; see the
[migration record](docs/PERSISTENCE_MIGRATION_2026-09-20.md).
The normal BOOT attempt read back with the hash above, but no runtime
acceptance exists. A mixed-compression decoder failure was reproduced on the
host, but phone-side boot evidence is unavailable. Enter unchanged RECOVERY,
then capture bounded BORE/pstore evidence before testing a corrected candidate.

## Safety and rollback

BOOT work now has explicit owner approval, with RECOVERY retained as rescue.
Do not reformat installed userdata or CACHE, or modify MISC, PIT, EFS, IMEI,
bootloader or TrustZone. The historical MISC/BCB integrity
failure is documented in the experiment log, not a procedure to repeat.

Known-good Lineage recovery: `lineage/build-20260915/recovery.img`, SHA256
`b5bf01c4a47091eb95078fc69b133b44c2b453b31c23433594c5b605e3747b55`.
It is a local-only artifact. See [NATIVE_LINUX.md](docs/NATIVE_LINUX.md) for
the verified connection, reboot and rollback procedures.
