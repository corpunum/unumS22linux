# S22 Native Linux Project — STATUS

Last updated: 2026-09-18

## Objective
Native Linux (no Android userspace) → Arch Linux ARM → Wayland → Hyprland → Omarchy
on Samsung Galaxy S22 (SM-S901B/DS, Exynos 2200, codename r0s).

## Current phase
Phase 6/9 complete (evidence review + recovery comparison + kexec feasibility).
NOT yet at any physical flash. No flash has occurred this session.

## Verified device state (2026-09-18, re-verified independently, see evidence/)
- Model: SM-S901B, device r0s
- Bootloader: S901BXXSIFYI3, UNLOCKED (flash.locked=0, verifiedbootstate=orange,
  vbmeta.device_state=unlocked) — CONFIRMED genuine, not assumed
- Android 15, build AP3A.240905.015.A2.S901BXXSIFYI3, kernel 5.10.223
- warranty_bit=1 (correction vs prior record of 0 — flag as possibly stale/changed)
- Stock, non-rooted, ro.secure=1 — no su, no adb root, pstore/last_kmsg unreadable

## What's ready
- Ubuntu host toolchain: aarch64-linux-gnu-gcc 13.3.0, clang/lld 18, dtc 1.7.0,
  simg2img/img2simg, mkbootimg/unpack_bootimg (AOSP, tools/mkbootimg),
  avbtool 1.4.0 (AOSP, external/avb), rust/cargo, meson/ninja — see tools/
- adb/fastboot installed + udev rule for Samsung vendor 04e8 + device authorized
- Current LineageOS r0s sources cloned at tools/lineage/, branch lineage-23.2:
  - android_device_samsung_r0s @ 142838b2
  - android_device_samsung_s5e9925-common @ 7be96871
  - android_kernel_samsung_s5e9925 @ 4e5c5ad7 (config change only; check for later
    commits before building)
- Current LineageOS install docs confirm required bootloader = S901BXXSIFYI3,
  exactly matching our phone's current firmware — no firmware up/downgrade needed
- samloader-rs identified as the current recommended Linux flashing tool
  (successor to Heimdall, made by topjohnwu/Magisk) — NOT YET INSTALLED

## Key technical findings
See docs/RECOVERY_COMPARISON.md, docs/KEXEC_FEASIBILITY.md, MODULES.md

1. Recovery uses **boot header v2** (`BOARD_RECOVERY_MKBOOTIMG_ARGS := --header_version 2`)
   while the main boot.img uses **header v4**. This matches the prior team's determination —
   that assumption was correct, not the cause of the previous failure.
2. `CONFIG_KEXEC`, `CONFIG_KEXEC_FILE`, `CONFIG_CRASH_DUMP` are all **disabled** in the
   current upstream s5e9925_defconfig. Kexec-based RAM-boot iteration is NOT available
   out of the box; would require a kernel rebuild (see KEXEC_FEASIBILITY.md).
3. **Most likely cause of the previous failed native recovery boot**: the recovery
   ramdisk must load a strict, ordered list of ~324 kernel modules
   (`RECOVERY_KERNEL_MODULES := $(BOARD_VENDOR_RAMDISK_KERNEL_MODULES_LOAD)`) before
   basic hardware (regulators, IOMMU, USB PHY/dwc3, USB gadget stack) is usable.
   A static BusyBox `/init` with no modprobe/insmod sequence would never bring up
   USB — matching the observed symptom exactly (splash → ~60s → reset, zero USB
   enumeration). See MODULES.md for the exact required load order.
4. pstore/last_kmsg evidence from the previous failed boot could NOT be recovered —
   stock build has no root, SELinux blocks shell access even with DAC group match.
   This is a closed dead end unless a rooted shell is obtained by other means.

## Pre-flash blockers (nothing flashed, none of these block research/build work)
- samloader-rs not yet downloaded/verified on this host
- Official current Lineage recovery.img not yet downloaded/hashed
- FYI3 stock recovery/boot/vendor_boot/dtbo/vbmeta not yet re-extracted on THIS
  Ubuntu machine (rollback route not yet independently verified here)
- No physical flash has been proposed or approved yet

## Next steps (still pre-flash)
1. Install samloader-rs, verify `samloader print-pit` against phone in Download Mode
   (read-only, no flash)
2. Acquire FYI3 stock firmware package on this host, extract boot/recovery/vendor_boot/
   dtbo/vbmeta, hash everything — rebuild independent rollback route
3. Download official current LineageOS r0s recovery.img, hash it, unpack with
   unpack_bootimg, compare header/kernel/ramdisk/DTB against stock FYI3 recovery
4. Present FLASH GATE for the Lineage-recovery control-boot test (Phase 7) — requires
   explicit approval before any partition write
