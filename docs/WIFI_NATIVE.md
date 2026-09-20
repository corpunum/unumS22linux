# Native QCA6490 Wi-Fi investigation

Status at 2026-09-20: **NOT WORKING; activation stopped.** The exact Lineage
`wlan.ko` was loaded during a serialized investigation, but cold-boot
calibration was started incorrectly. CNSS asserted and the phone panicked and
rebooted (`BORE758`, `subsys-restart: Resetting the SoC wlan crashed`). No
automatic retry or WLAN activation is permitted. USB ECM rescue remains the
required recovery path.

## Proven host artifacts

Official build: `lineage-23.2-20260915-nightly-r0s-signed.zip`, SHA256
`0cee68b94e9a47643af5e752d7cb687fd5aa51ce445b74455ad0bee2f206f558`.
The extracted EROFS `vendor_dlkm` yielded the only eligible module:

- `rootfs/wifi-vendor-assets/lineage-23.2-20260915/vendor_dlkm/lib/modules/wlan.ko`
- SHA256 `cbf8932d079e97006a5b7aae0e1b5acfe65b3e8b5113ab8fa095daa36796738d`
- vermagic `5.10.260-g4e5c5ad7d950 SMP preempt mod_unload modversions aarch64`

The stock FYI3 module is ABI-incompatible and must never be loaded. Recovered
QCA firmware is staged under `rootfs/wifi-vendor-assets/vendor/firmware/`.
No EFS, PIT, MISC, bootloader, trust-zone, or raw partition operation is
allowed.

## Official init ordering

Decoded files from the preserved vendor image are under
`rootfs/wifi-init-reference/decoded/`:

- `wlan_vendor_rc`: `on early-init` loads `wlan.ko`; `on post-fs-data` writes
  `/sys/devices/platform/qcom,cnss-qca6490/fs_ready 1` and configures recovery
  policy from the vendor ramdump property.
- `wlan_common_rc`: starts `/vendor/bin/hw/macloader` at `post-fs-data`.
  CNSS completes `macloader_done` when its MAC-address sysfs handler is
  written, not merely when the oneshot service exits. This rc also changes
  EFS file permissions: do not execute it wholesale or run the proprietary
  loader without reviewing its writes. EFS remains off-limits.

Pinned CNSS source shows WLAN registration is deferred while cold-boot
calibration is enabled. The deferred work has a finite
`CNSS_TIMEOUT_CALIBRATION` deadline. `fs_ready=1` must arrive within that
window, and `cnss_cold_boot_cal_start_hdlr()` waits up to 10 seconds for
`macloader_done`. It rejects the start if WLAN is already loading, probed, or
firmware-ready. Successful calibration releases deferred registration early;
the timeout path can also proceed into registration after reporting failure.
With this kernel's nonfatal assertion configuration, that latter path explains
why a delayed calibration request can collide with mission-mode startup.

In the failed run, insertion occurred around monotonic 20150 and `fs_ready=1`
around 20331—approximately 181 seconds later. The preserved panic evidence
shows `macloader_done timeout`, then `WLAN in mission mode before cold boot
calibration`, assertion at `drivers/net/wireless/cnss2/main.c:1864`, CNSS
recovery, and kernel panic. This is a sequencing failure, not proof of a
missing calibration database.

`cnss_cal_db_mem_update()` explicitly returns success when the BDF disables
calibration-file download; therefore `CalDB:0` does not establish that
`wlfw_cal_db.bin` is required. EFS/factory calibration remains untouched.

Evidence: [wifi-panic-sanitized.txt](../evidence/hardware-20260920/wifi-panic-sanitized.txt).

## Current safety state

The staging helpers are host-only for hashes and metadata. They are not an
activation recipe. This document intentionally contains no executable
`insmod`, `fs_ready`, `recovery`, `rmmod`, association, or retry procedure.
Parent serialization is required for any future experiment. Until then:

- do not load or reload `wlan.ko`;
- do not write CNSS sysfs attributes or enable radios;
- do not reboot, flash, alter global firmware paths, or touch EFS/persistent
  calibration data;
- preserve ECM, its routes, Bluetooth/audio state, and the current guardian.

Acceptance has not been achieved: no safe persistent native Wi-Fi bring-up,
`phy`/WLAN interface, association, or Wi-Fi traffic is claimed.
