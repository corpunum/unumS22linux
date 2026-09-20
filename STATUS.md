# S22 native Linux status

Updated 2026-09-20. Older states and experiments remain in `EXPERIMENTS.md`
and Git history; the old claim that nothing custom was flashed is obsolete.

## Current accepted state — 20:06 UTC

Native RECOVERY **BORE758** is running, USB SSH is reachable, and the resident
CPU model reports `ok`. A WLAN initialization experiment caused a real kernel
panic at19:01; the bootloader returned directly to RECOVERY without a requested
button press or rescue flash. The guardian then automatically started the
persistent desktop/model. This does not prove every future crash is remotely
recoverable or that normal cold power-on selects Linux.

- Display stride correction, Omarchy icons/fonts, and battery panel survived
  that reboot. Latest sampled battery temperature30.0C; not a battery-life test.
- Native power-key binding is installed. Controlled compositor DPMS passed
  on/off/on; physical-button delivery is not yet accepted.
- A persistent **Keyboard** bar button was added after the reboot. A synthetic
  tap through `sec_touchscreen` revealed Squeekboard; no physical finger claim.
  The keyboard addition itself has not been reboot-tested.
- Wi-Fi is **connected and internet-tested**. The corrected single activation
  completed calibration; an exact optional-firmware response fixed startup
  waits. WPA2/CCMP, DHCP, native/Arch DNS, and WLAN-forced DNS/HTTPS passed.
  USB routes/SSH and model health remain intact. Credentials/tools persist
  privately on userdata; Wi-Fi autostart is not enabled or reboot-tested.
- ABOX core firmware started during BORE757, but no speaker playback path was
  obtained. Those temporary firmware binds disappeared at reboot; current
  BORE758 has no ALSA soundcards. Bluetooth has no HCI controller.
- Signed Alpine radio/audio tools are installed; they do not establish working
  hardware. CACHE overlay has34,088KiB free; keep bulk assets on userdata.

Evidence: [final live state](evidence/hardware-20260920/final-live-state.txt),
[keyboard screen](evidence/hardware-20260920/keyboard-after.png),
[hardware audit](docs/EVERYDAY_HARDWARE_2026-09-20.md), and
[Wi-Fi history](docs/WIFI_NATIVE.md), plus
[live Wi-Fi acceptance](evidence/wifi-connected-20260920/acceptance.json).
No partition was written during this
daily-hardware round. Preserve the working recovery environment.

## Earlier boot-attempt history (superseded snapshots)

Latest 18:18UTC: owner confirms the physical screen is clean. Workaround startup
configuration is now persistent, with backup/hash readback; no new reboot was
performed. BORE757 remains up nearly five hours with healthy model and USB
internet. Wi-Fi/Bluetooth are not working; audio, cameras, cellular and suspend
are not accepted. See `docs/EVERYDAY_HARDWARE_2026-09-20.md` for the bounded
live audit and next steps. Normal-power-on Linux remains unfinished.

At13:22UTC, native RECOVERY BORE757 returned via physical key selection.
Whole-partition readback verifies the original BOOT restoration and unchanged
RECOVERY/vendor_boot. Persistent desktop/model autostart succeeded; model
health `ok`. The owner reports physical display artifacts while framebuffer
capture is clean. A source-backed, opt-in row-stride workaround is running
in a temporary desktop session; physical correction is not yet confirmed.
See `tools/omarchy-trial/s22-linear-stride.md`. Do not normal-boot Android.

Earlier rollback staging (superseded by the live readback above):

At13:16–13:20UTC, Download USB became reachable. The original 64MiB BOOT
backup (SHA256 `0962dbdd67b748858189b46d464820ec7d1f3ea843cc7c3d033b69c40931b44e`)
was restored with `--no-reboot`; Odin acknowledged all transfers and the
tool exited0. Device-side readback is pending. The phone remains in Download
Mode. Enter unchanged native RECOVERY next, not the restored Android BOOT;
Linux userdata/CACHE were not touched. No corrected candidate was flashed.

Earlier failed attempt:

At approximately 07:48 UTC, BOOT was written and read back with SHA256
`4aeb801486e35e2c71dab0e988c6ac14802b05a29d062ddd93834e74802b5b2e`.
RECOVERY (`1a827b43d29141efb47f530902dd4e4ee2b6d780515893c9ecd676ad27efd7d1`)
and vendor_boot (`383b6f6789e655b070929914db5639e6d9bacc78823ada7bf553962b5b4888be`)
were unchanged. The 07:48:18 UTC normal reboot had no SSH, ADB, or
Download-USB return by 07:56 UTC. Boot mode/runtime are unconfirmed; do not
claim a usable normal boot. Last accepted state is BORE519 recovery with
persistent Omarchy/model startup, readiness at 15.81 seconds and stable at
77 seconds.

## Persistent platform baseline (first accepted at BORE519)

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

- Normal-power-on Linux is unfinished. The failed BOOT candidate was replaced
  by the original Samsung BOOT; Linux currently runs from RECOVERY.
- GPU compute still faults despite a validated CPU-mapping lifecycle fix.
- NPU runtime/firmware integration and inference are unproven.
- Wi-Fi reboot autostart, physical finger sensing, Bluetooth, cellular, audio,
  cameras, suspend and daily use have not passed acceptance.
- The normal Omarchy installer was not run. Native signed pacman transactions
  reproduce a signature-helper hang; the clean root and signed dependency
  delta were installed on the host and transferred with their package records.
  Runtime/package checks work; native package installation is not accepted.

## Migration and next steps

The owner approved userdata conversion, accepted possible Android-data loss,
and then approved BOOT work. Private raw backups of 15 selected partitions
are checksum-verified; personal-file restoration remains unproven. Userdata
conversion and recovery autostart are complete; see the
[migration record](docs/PERSISTENCE_MIGRATION_2026-09-20.md).
The experimental BOOT has been replaced by the original backup, now verified
by device readback. BORE confirms236 normal boot entries between07:48 and13:14;
pstore is empty and last_kmsg is corrupted, so the precise phone-side failure
is still unproven. Native RECOVERY758 is now running. The physical display fix
was accepted by the owner on BORE757; its persistent configuration and clean
framebuffer are verified after the later BORE758 panic reboot.
Do not normal-boot Android or wipe userdata. Further experimental normal
boots need physical rescue availability and are not part of this rollback.

Next Wi-Fi work is supervised startup integration and a separately controlled
recovery-boot test, not more activation of the running driver. The exact
missing optional QDSS response must be available before module insertion;
do not repeat the failed insertion-and-wait sequence. No EFS write or guessed
calibration was required. Audio needs real machine-card/topology registration; Bluetooth needs
a reviewed QCA6490 UART/firmware path. GPU/NPU inference, cameras, cellular and
suspend remain unfinished. This is an experimental handheld, not a daily phone.

## Safety and rollback

BOOT work now has explicit owner approval, with RECOVERY retained as rescue.
Do not reformat installed userdata or CACHE, or modify MISC, PIT, EFS, IMEI,
bootloader or TrustZone. The historical MISC/BCB integrity
failure is documented in the experiment log, not a procedure to repeat.

Known-good Lineage recovery: `lineage/build-20260915/recovery.img`, SHA256
`b5bf01c4a47091eb95078fc69b133b44c2b453b31c23433594c5b605e3747b55`.
It is a local-only artifact. See [NATIVE_LINUX.md](docs/NATIVE_LINUX.md) for
the verified connection, reboot and rollback procedures.
