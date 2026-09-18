# S22 Native Linux Project — STATUS

Last updated: 2026-09-18 (session 2)

## Objective
Native Linux (no Android userspace) → Arch Linux ARM → Wayland → Hyprland → Omarchy
on Samsung Galaxy S22 (SM-S901B/DS, Exynos 2200, codename r0s).

## Current phase
All pre-flash research/build blockers CLOSED. Rollback route READY. Recovery comparison
COMPLETE. NOT yet at any physical flash — nothing has been written to the phone.
Waiting at a FLASH GATE for explicit approval (see bottom of this file).

## Verified device state (2026-09-18, re-verified independently, see evidence/)
- Model: SM-S901B, device r0s
- Bootloader: S901BXXSIFYI3, UNLOCKED (flash.locked=0, verifiedbootstate=orange,
  vbmeta.device_state=unlocked) — CONFIRMED genuine, not assumed
- Android 15, build AP3A.240905.015.A2.S901BXXSIFYI3, kernel 5.10.223
- warranty_bit=1 (correction vs prior record of 0 — flag as possibly stale/changed)
- Stock, non-rooted, ro.secure=1 — no su, no adb root, pstore/last_kmsg unreadable
- CSC region: EUX

## What's ready
- Ubuntu host toolchain: aarch64-linux-gnu-gcc 13.3.0, clang/lld 18, dtc 1.7.0,
  simg2img/img2simg, mkbootimg/unpack_bootimg (AOSP, tools/mkbootimg),
  avbtool 1.4.0 (AOSP, external/avb), rust/cargo, meson/ninja, samloader-rs 2.1.0
- adb/fastboot installed + udev rule for Samsung vendor 04e8 + device authorized
- Current LineageOS r0s sources cloned at lineage/, branch lineage-23.2:
  - android_device_samsung_r0s @ 142838b2
  - android_device_samsung_s5e9925-common @ 7be96871
  - android_kernel_samsung_s5e9925 @ 4e5c5ad7
- Current official LineageOS r0s recovery/boot/vendor_boot/dtbo/vbmeta (build 20260915)
  downloaded and SHA256-verified against LineageOS's own published manifest —
  lineage/build-20260915/
- Stock FYI3 firmware (BL/AP/CP/CSC/HOME_CSC, 11.25GB) downloaded directly from Samsung's
  FUS server via samloader-rs, matching the phone's exact currently-installed version.
  boot/recovery/vendor_boot/dtbo/vbmeta/vbmeta_system extracted, decompressed, and
  SHA256-hashed — stock/FYI3/, hashes in stock/FYI3/fyi3_hashes.txt and FLASH_LOG.md
- Full three-way structural comparison (stock vs Lineage vs prior failed design)
  complete in docs/RECOVERY_COMPARISON.md
- Linux-side rollback route: READY (see FLASH_LOG.md) — no longer dependent on the old
  Windows laptop

## Key technical findings
See docs/RECOVERY_COMPARISON.md, docs/KEXEC_FEASIBILITY.md, MODULES.md

1. Recovery uses **boot header v2** on both stock and Lineage — matches, confirmed twice
   now from real binaries. NOT the cause of the previous failure.
2. **NEW finding**: stock FYI3 recovery uses **4096-byte page size**; current Lineage
   recovery uses **2048-byte**. Both self-describing in-header, but Native Recovery V2
   should match stock's 4096 exactly for this specific device/firmware baseline.
3. `CONFIG_KEXEC`, `CONFIG_KEXEC_FILE`, `CONFIG_CRASH_DUMP` are all **disabled** in the
   current upstream s5e9925_defconfig. Kexec-based RAM-boot iteration is NOT available
   out of the box; would require a kernel rebuild (see KEXEC_FEASIBILITY.md).
4. **Root cause of the previous failed native recovery boot — now PROVEN from two
   independent real binaries (stock AND Lineage)**: both recovery ramdisks carry ~330
   kernel `.ko` files (334 stock / 324 Lineage) including USB PHY (`phy-exynos-usbdrd-super.ko`)
   and DWC3 controller (`dwc3-exynos-usb.ko`) modules, loaded automatically by AOSP
   `init`'s built-in module-loading logic before anything else happens (stock uses
   `modules.dep`-driven loading, Lineage ships an explicit `modules.load` order — both
   valid, different mechanisms, same effect). USB, display, and touch drivers are ALL
   modular on this SoC, not built into the kernel. The previous static BusyBox `/init`
   had no equivalent module-loading step, so USB never came up — matching the observed
   symptom exactly (splash → ~60s → reset, zero USB enumeration).
5. pstore/last_kmsg evidence from the previous failed boot could NOT be recovered —
   stock build has no root, SELinux blocks shell access even with DAC group match.
   Closed dead end unless a rooted shell is obtained by other means later.

## Pre-flash blockers: ALL CLOSED
- ~~samloader-rs not installed~~ → installed, working, verified against Samsung's server
- ~~Lineage recovery not downloaded~~ → downloaded, SHA256-verified
- ~~FYI3 stock artifacts not extracted~~ → extracted, SHA256-hashed
- ~~No binary comparison~~ → complete, see docs/RECOVERY_COMPARISON.md
- No physical flash has occurred or been approved

## FLASH GATE — awaiting approval

Proposed next physical experiment (Phase 7 control test): flash the CURRENT OFFICIAL
LineageOS recovery.img (build 20260915) to the RECOVERY partition ONLY, then boot
directly into recovery without letting Android boot in between (avoiding
vendor_flash_recovery/install-recovery.sh self-healing back to stock).

- Target partition: RECOVERY only
- Artifact: `~/s22-linux/lineage/build-20260915/recovery.img`
- Exact bytes: 100,663,296
- SHA256: `b5bf01c4a47091eb95078fc69b133b44c2b453b31c23433594c5b605e3747b5`
- Why: prove the Samsung S-LK → recovery partition → recovery kernel → recovery ramdisk
  chain works on this exact phone, using a known-good reference, before designing our
  own native recovery
- Command: `samloader flash --partition RECOVERY recovery.img --no-reboot`, then manually
  boot to recovery immediately (Vol Up + Power while connected via USB), never letting
  Android boot first
- Expected result: LineageOS recovery logo/UI appears
- Failure mode: black screen / bootloop / "unlocked software" warning loop with no
  progress, similar to the previously observed failure
- Recovery procedure if it fails: return to Download Mode (Vol Up + Vol Down + USB),
  `samloader flash --partition RECOVERY stock/FYI3/extracted/recovery.img`, confirmed
  byte-identical to what's already running (SHA256
  `e002ec56b306d9e650eb8abd51bd81662e9198bf35bf9dd04e182646c12722ef`)
- This test does NOT install LineageOS itself — no factory reset, no sideload, no
  reboot to system. Recovery boot only.

Waiting for explicit go-ahead before touching any partition.
