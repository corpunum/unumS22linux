# S22 native Linux status

Updated 2026-09-20. Older states and experiments remain in `EXPERIMENTS.md`
and Git history; the old claim that nothing custom was flashed is obsolete.

## Working

- SM-S901B/DS (`r0s`), Exynos 2200; unlocked S901BXXSIFYI3 bootloader.
- Native Alpine 3.24.2 ARM64, guardian PID1, kernel 5.10.260-g4e5c5ad7d950.
  AOSP first-stage bootstrap is retained; Android services do not run.
- RECOVERY V3 image, whole-partition SHA256 verified:
  `1a827b43d29141efb47f530902dd4e4ee2b6d780515893c9ecd676ad27efd7d1`.
- CACHE-backed Alpine package/file persistence, USB Ethernet/SSH and fallback
  Weston desktop; targeted software recovery reboot verified through BORE 518.
- RAM-staged Arch ARM, Hyprland 0.56.2, Omarchy v4.0.4 Quickshell bar, foot,
  Squeekboard. Internal 1080x2340 display, llvmpipe software rendering.
- Synthetic touchscreen → keyboard → local chat path; this is not a physical
  finger-sensing test.
- Resident Qwen3.5-2B Q4_0 CPU server, 4096 context, one slot, four fast cores,
  loopback 127.0.0.1:8089. Short first-text latency 0.4–0.7 seconds.

## Not finished

- Arch/Omarchy and models are RAM-only; automatic persistent desktop startup
  and ordinary cold-power-on Linux selection are not implemented.
- GPU compute still faults despite a validated CPU-mapping lifecycle fix.
- NPU runtime/firmware integration and inference are unproven.
- Physical finger sensing, Wi-Fi, cellular, audio, cameras, suspend and daily
  use have not passed acceptance in the native Omarchy environment.
- The normal Omarchy installer was not run. Some host-verified UI package
  payloads were staged without phone-side package database registration after
  its signature-helper startup hung. Do not treat this as a finished distro.

## Next decision

The persistent CACHE overlay has about **74.6 MiB free**. The existing
userdata partition is about **105.5 GiB**, not demonstrated accessible as a
decrypted filesystem from native Linux. Choose an external-storage trial or
an explicitly authorized, backed-up Linux-only userdata conversion; see
[PERSISTENCE.md](docs/PERSISTENCE.md). No such conversion is authorized yet.
Cold-boot routing is a separate boot-image problem, not a desktop setting.

## Safety and rollback

RECOVERY-only raw writes. Do not modify BOOT, MISC, PIT, EFS, IMEI, bootloader
or TrustZone, or format userdata/cache. The historical MISC/BCB integrity
failure is documented in the experiment log, not a procedure to repeat.

Known-good Lineage recovery: `lineage/build-20260915/recovery.img`, SHA256
`b5bf01c4a47091eb95078fc69b133b44c2b453b31c23433594c5b605e3747b55`.
It is a local-only artifact. See [NATIVE_LINUX.md](docs/NATIVE_LINUX.md) for
the verified connection, reboot and rollback procedures.
