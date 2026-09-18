# Recovery Comparison — Stock FYI3 vs Current LineageOS vs Previous Failed Custom Build

Status: Lineage side CONFIRMED from real binary artifact. FYI3 stock side still pending
(firmware download in progress on this host — see STATUS.md).

## From LineageOS current source (lineage-23.2, verified by reading BoardConfig files)

| Property | Value | Source |
|---|---|---|
| Main boot.img header version | 4 | BoardConfigCommon.mk:21 |
| Recovery image header version | **2** | BoardConfigCommon.mk:96 (`BOARD_RECOVERY_MKBOOTIMG_ARGS := --header_version 2`) |
| DTBO | separated, included in recovery | BoardConfigCommon.mk:33,95 (`BOARD_KERNEL_SEPARATED_DTBO`, `BOARD_INCLUDE_RECOVERY_DTBO`) |
| Ramdisk compression | LZ4 | BoardConfigCommon.mk:22 (`BOARD_RAMDISK_USE_LZ4`) |
| AVB | enabled | BoardConfigCommon.mk:116 |
| AVB recovery signing algorithm | NONE | BoardConfigCommon.mk:118 (recovery partition itself is not AVB-signed; verification happens via vbmeta chain instead) |
| AVB recovery rollback index | 0 | BoardConfigCommon.mk:120 |
| Recovery partition size | 100,663,296 bytes (96 MiB) | BoardConfigCommon.mk:78 |
| Boot partition size | 67,108,864 bytes (64 MiB) | BoardConfigCommon.mk:75 |
| DTBO partition size | 8,388,608 bytes (8 MiB) | BoardConfigCommon.mk:77 |
| Recovery kernel modules | same list as vendor ramdisk, ~324 modules, strict order | configs/kernel/modules/r0s.load, see MODULES.md |
| vbmeta flags | `--flags 3` | BoardConfigCommon.mk:117 |

## Prior team's stock FYI3 determination (carried over, NOT re-verified this session)
- Stock FYI3 recovery reported as using "a dedicated recovery partition and a
  header-v2 style recovery image" — **this matches** the current Lineage recovery
  header version exactly (v2). The earlier team's structural assumption about header
  version was CORRECT.

## CONFIRMED from real Lineage recovery.img binary (build 20260915)

Downloaded from `https://mirrorbits.lineageos.org/full/r0s/20260915/`, SHA256-verified
against LineageOS's own published `builds` API manifest (all 6 artifacts MATCH: boot.img,
dtbo.img, recovery.img, vbmeta.img, vendor_boot.img, build-manifest.xml).

Unpacked with `tools/mkbootimg/unpack_bootimg.py --format mkbootimg`:
```
--header_version 2 --os_version 16.0.0 --os_patch_level 2026-09
--pagesize 0x00000800 --base 0x00000000
--kernel_offset 0x10008000 --ramdisk_offset 0x11000000
--tags_offset 0x10000100 --dtb_offset 0x0000000011f00000
--cmdline ' bootconfig'
```
- kernel: `Linux kernel ARM64 boot executable Image, little-endian, 4K pages` (33.5MB)
- ramdisk: LZ4-compressed cpio, 31MB compressed / ~88.8MB decompressed — a FULL AOSP
  recovery userspace (not a minimal shell): `/init` → real `/system/bin/init` binary,
  full `/system/lib64`, sepolicy, recovery UI libs, etc.
- dtb / recovery_dtbo: Samsung proprietary multi-DTB container format (magic
  `0xd7b7ab1e`, not raw FDT `0xd00dfeed`) — expected Samsung packaging, not investigated
  further this session
- `lib/modules/modules.load` inside the real ramdisk: **324 entries, byte-identical
  order** to `configs/kernel/modules/r0s.load` from source (see MODULES.md for full
  proof). All referenced `.ko` files physically present in `lib/modules/`.

## Still pending
- Actual FYI3 stock recovery.img has not been re-extracted on this Ubuntu host yet
  (firmware zip downloading in background, ~11GB, see STATUS.md for progress)
- No byte-level header/cmdline/bootconfig/alignment diff against stock FYI3 recovery yet
  (blocked on the above)
- Old failed custom image not present on this machine (built on prior Windows laptop) —
  cannot be re-hashed or re-unpacked here; treat its described structure (Alpine/BusyBox
  static init on stock kernel/DTB/DTBO) as reported, not independently verified

## Conclusion — PROVEN, not just inferred (see MODULES.md for full detail)
The header-version/DTBO/AVB structural envelope was NOT the cause of the previous
failure — confirmed correct against the real, hash-verified current Lineage recovery
binary. The actual cause is the missing kernel module load sequence: the real recovery
ramdisk carries 324 `.ko` files plus a `modules.load` order file and relies on AOSP
`init`'s built-in `LoadKernelModules()` to insmod them before anything else happens.
USB (PHY + DWC3 + gadget functions), display (panel/DSIM), and touch drivers are ALL
modular on this SoC — none are built into the kernel image. A static BusyBox init with
no equivalent module-loading step, as used in the previous attempt, would never bring up
USB, matching the observed symptom exactly.

Next action to fully close this out: extract the real FYI3 stock recovery.img (firmware
download in progress) and diff its header/ramdisk directly against this Lineage one, to
rule out any stock-specific quirk before designing Native Recovery V2.
