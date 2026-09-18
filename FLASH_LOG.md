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
(none yet — this section will be appended to before and after any real flash)
