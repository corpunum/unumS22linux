# Flash Log

No flash has occurred yet this session. This file records rollback-artifact
provenance and will get an entry per actual flash event once any occur.

## Stock FYI3 rollback artifacts — READY (extracted 2026-09-18)

Source: official Samsung FUS server via samloader-rs 2.1.0, model SM-S901B, region EUX,
version `S901BXXSIFYI3/S901BOXMIFYI3/S901BXXSIFYI3/S901BXXSIFYI3` — this is the CURRENT
firmware already running on the phone (re-confirmed via `adb shell getprop ro.bootloader`
before download).

Path: `~/s22-linux/stock/FYI3/`

```
SHA256 (boot.img)              0962dbdd67b748858189b46d464820ec7d1f3ea843cc7c3d033b69c40931b44e
SHA256 (recovery.img)          e002ec56b306d9e650eb8abd51bd81662e9198bf35bf9dd04e182646c12722ef
SHA256 (vendor_boot.img)       383b6f6789e655b070929914db5639e6d9bacc78823ada7bf553962b5b4888be
SHA256 (dtbo.img)              2bd5faf44c0e4c59dea12e70bda4fa23b75df859ff7328a136bb15c72e2f7bd3
SHA256 (vbmeta.img)            5fe0620f8155e941fa729657d20f2b96b52051da669d23515ca92c986add67f4
SHA256 (vbmeta_system.img)     fb34e62e8bae5b280914da99d4cf2bb316f3645ab941c62b28f7909abad6cf54
SHA256 (source zip)            901f44e3a4ebcf30de30ac613228cb70bae66bea9154e0dba7ad18d627e12f41
SHA256 (AP tar.md5)            1b46f2fdec3408c97a7a4f584cf4e96f90d053d87bab618edfe104efab14df99
```

Full manifest: `stock/FYI3/fyi3_hashes.txt`

Rollback procedure (not yet executed, not yet needed — recorded for readiness):
1. Boot phone into Download Mode: power off, hold Vol Up + Vol Down, connect USB
2. `samloader flash --partition RECOVERY stock/FYI3/extracted/recovery.img` (only if
   recovery was the only thing changed), or restore full AP set if boot/vendor_boot/
   dtbo/vbmeta were also touched
3. Reboot normally — stock `vendor_flash_recovery`/`install-recovery.sh` will otherwise
   also self-heal recovery back to stock automatically on any normal Android boot, as
   already proven in the documented prior history

This closes the "Linux-side rollback" blocker: we no longer depend on the old Windows
laptop for recovery — this Ubuntu host now holds byte-verified stock artifacts and the
tool (`samloader-rs`) to write them back.

## Current LineageOS r0s recovery (control specimen) — READY

Path: `~/s22-linux/lineage/build-20260915/`, build date 2026-09-15, verified against
LineageOS's own published SHA256 manifest (see docs/RECOVERY_COMPARISON.md).

## Flash history

### 2026-11-12 (Athens) — RECOVERY ← close_range kernel fix — SUCCESS

Software path from native Linux, owner present for the download-mode fallback (not needed). Approved by the
owner via corpunum-82; hub U-fd91ceec.

- **Kernel:** the running kernel's source (`4e5c5ad7d950` + `tools/hardware/bt-hci-socket-restore.patch`,
  identical to the recorded candidate commit `f52cbbd7`) plus `tools/hardware/close-range-kernel-fix.patch`
  (fs/file.c `__range_close()` clamps to the fdtable). Built with `tools/hardware/build-recovery-kernel.sh`:
  the phone's `/proc/config.gz`, the KBUILD identity from its `/proc/version`, and `make ARCH=arm64 LLVM=1`
  with **LLVM_IAS unset** and `LOCALVERSION=""`. Only that flag set reproduces the running config, because
  this tree enables the integrated assembler unless LLVM_IAS=0. `LLVM_IAS=1` turns on HAS_LTO_CLANG, and GNU binutils drop RELR.
- **Reproducibility evidence:** the rebuilt baseline matches the running kernel on config, version string, all
  58,633 text-symbol offsets and all 14,707 exported CRCs (the 10-03 symvers). It is **not** byte-identical:
  the build-id hashes DWARF paths, and .rodata has a 3,200-byte shift that was not root-caused (a symbol-table
  detail). Accepted as sufficient by corpunum-82.
- **Image:** `recovery.img` SHA-256 `19aa029333ffcaa94cbc80368329423773e8be649391bda99cd8f797af36a845`, packed with
  `build-bt-hci-recovery.py --base-sha256` against a verified dump of the live partition. Only the kernel
  changed; AVB footer verified. Kernel build-id `f95682b41f6fa118365d95f0a430cac4aeb2247c`.
- **Write:** `dd ... conv=fsync` to `/dev/block/by-name/recovery`, full read-back SHA-256 matched,
  `s22-reboot recovery`, back in 48 s.
- **Acceptance:** `subprocess.run(['true'], capture_output=True)` in the chroot returns at once (it used to hang
  as an unkillable task). Wi-Fi, Tailscale, modem/SIM/LTE, 3 ALSA cards, hci0 (second keepalive attempt),
  camera (developed JPEG), voice turn, OpenUnum and mute all OK.
- **Rollback image:** the exact pre-flash partition, SHA-256
  `b10412715756da3cc8ee221368b49f179cc0c64ab7bd2802976480905e6d8d2f` (private backup on the rig, and staged on
  the phone in `/srv/s22/state/flash-20261112/`). `dd` it back, verify, `s22-reboot recovery`; if the phone does
  not boot, use download mode + `samloader flash -p RECOVERY <image>`.

### 2026-09-18 — Phase 7/8 control-boot test — SUCCESS (with one recovered incident)

1. **RECOVERY** ← `lineage/build-20260915/recovery.img` (100,663,296 bytes,
   SHA256 `b5bf01c4a47091eb95078fc69b133b44c2b453b31c23433594c5b605e3747b5`) — flashed
   successfully via `samloader flash -p RECOVERY`, confirmed by device response
   "RECOVERY flash successful".
2. **MISC** ← `rollback/misc/bcb_boot_recovery.bin` (2048 bytes, raw `boot-recovery`
   BCB command, rest zeroed) — Odin protocol reported "MISC flash successful", but the
   phone's own bootloader secure/integrity check on MISC content FAILED
   (`SECURE CHECK FAIL: (MISC)`), leaving the phone unable to boot normally
   (`DN_FAIL_SECURE_CHECK_FAIL` → recovery-style "Can't load Android system" screen).
3. **RECOVERY INCIDENT FIX**: **MISC** ← `stock/FYI3/extracted/misc.bin` (520,976 bytes,
   exact factory content from the FYI3 AP tar, SHA256
   `97be48ca24a7b307987b750726b7116467a4745ae9d650c9bec882c21c9713b`) — flashed via
   `samloader flash -p MISC`, passed the secure check immediately, phone rebooted
   normally — straight into the already-flashed LineageOS RECOVERY (the factory MISC's
   default command happened to still get us there since RECOVERY itself was untouched
   by the incident/fix).
4. Result: LineageOS recovery (build 20260915) booted successfully with full graphics,
   USB, and touch. Enabled ADB via the recovery's own Advanced menu, got a rooted shell,
   captured evidence (see evidence/lineage_recovery_boot/).

No further partitions touched. Phone was never in a state requiring PIT/EFS/BL/SBL
intervention — the incident was fully contained to MISC and resolved with data already
in hand.
