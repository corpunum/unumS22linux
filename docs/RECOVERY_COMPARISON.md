# Recovery Comparison — Stock FYI3 vs Current LineageOS vs Previous Failed Custom Build

Status: PARTIAL — built from source/config inspection only. Binary-level comparison
(actual FYI3 recovery.img vs downloaded Lineage recovery.img) NOT YET DONE — requires
downloading both artifacts on this host first (next step).

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

## What this session has NOT yet verified (blockers for a complete comparison)
- Actual FYI3 stock recovery.img has not been re-extracted on this Ubuntu host
- Actual current official Lineage r0s recovery.img has not been downloaded on this host
- No SHA256 hashes computed yet for either
- No byte-level header/cmdline/bootconfig/alignment diff performed yet
- Old failed custom image not present on this machine (built on prior Windows laptop) —
  cannot be re-hashed or re-unpacked here; treat its described structure (Alpine/BusyBox
  static init on stock kernel/DTB/DTBO) as reported, not independently verified

## Working conclusion (see MODULES.md and KEXEC_FEASIBILITY.md for detail)
The header-version/DTBO/AVB structural envelope was very likely NOT the cause of the
previous failure (it matches current known-good Lineage practice). The far more likely
cause is the missing kernel module load sequence for USB (PHY + DWC3 + gadget function
drivers) required before any USB gadget can enumerate — a static BusyBox init with no
insmod sequence would never bring up USB on this SoC's modular driver design.

Next action to close this out: download the real FYI3 recovery.img and the current
official Lineage r0s recovery.img, hash both, unpack with `tools/mkbootimg/unpack_bootimg.py`,
and diff headers/ramdisk contents directly.
