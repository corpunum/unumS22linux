# Native QCA6490 Wi-Fi investigation

Status at 2026-09-20, after 19:55 UTC: **connected and internet-tested on
native Linux**. WPA2-PSK/CCMP association, DHCP, DNS explicitly bound to
`wlan0`, and HTTPS explicitly bound to `wlan0` passed. USB ECM rescue and the
resident model remained healthy. This is the same RECOVERY boot, BORE758;
there was no new SoC reboot, flash, EFS write, or physical action.

Wi-Fi credentials and tools are stored privately on persistent userdata,
but **Wi-Fi boot autostart is not enabled or reboot-tested**. Do not rerun
module insertion or calibration on this running connection. The first failed
experiment below remains important history, not the current connectivity state.
See [the corrected startup sequence](wifi-next-experiment.md).

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

## Historical safety stop after the first panic

The staging helpers are host-only for hashes and metadata. They are not an
activation recipe. This document intentionally contains no executable
`insmod`, `fs_ready`, `recovery`, `rmmod`, association, or retry procedure.
Parent serialization is required for any future experiment. Until then:

- do not load or reload `wlan.ko`;
- do not write CNSS sysfs attributes or enable radios;
- do not reboot, flash, alter global firmware paths, or touch EFS/persistent
  calibration data;
- preserve ECM, its routes, Bluetooth/audio state, and the current guardian.

That first run did not achieve Wi-Fi acceptance. The separately reviewed
follow-up below supersedes the radio-activation stop for that single trial;
it does not authorize arbitrary reloads or unattended boot integration.

## Successful follow-up: calibration and optional firmware completion

The parent inserted the exact module and delivered `fs_ready=1` in the same
process, separated by only 0.055 seconds. Four read-only mounts expose the
14 hash-verified files inside **PID1's** `/vendor/firmware`, preserving the
touchscreen assets. The reference normal `recovery=1` policy was applied
first: WLAN failures recover the WLAN subsystem rather than intentionally
escalating to a whole-SoC panic. This is not a calibration bypass.

Calibration completed successfully; its measured calibration phase was
4660ms, about78s after activation including the initial firmware waits.
The normal ten-second macloader timeout did not prevent calibration. The
source's non-provisioned MAC fallback ran; factory MAC identity is not claimed.
The proprietary macloader and EFS were not used.

An additional native-userspace gap then became observable: optional
`qca6490/qdss_trace_config_v2.cfg` requests waited60s for a userspace firmware
response, exceeding the40s mission startup timeout. Two WLAN-only recovery
events occurred. The observer correctly reported failure; initial interface
enumeration alone was not success.

`wifi-optional-firmware.py` now responds with the standard firmware-loader
`loading=-1` result **only** for this absent, exact, CNSS-owned diagnostic
file. It checks the request identity, owning device and firmware absence.
It sends no firmware bytes, handles no other requests, and changes neither
global firmware timeout nor firmware search path. Prompt completion stopped
that wait/recovery cycle. Current CNSS state is `0x420107`, including
`FW_READY`, `DRIVER_PROBED` and `COLD_BOOT_CAL_DONE`.

## Association, privacy and test boundaries

After the owner's explicit approval, the host selected the single active
saved WPA-PSK profile also visible in the phone's passive scan. The passphrase
was derived into a WPA PSK in memory and transferred via pinned SSH stdin;
it was not placed in command arguments, public logs or Git. The resulting
PSK is still a secret. Its directory is0700 and profile0600, under
`/srv/s22/hardware/wifi-private/`.

The native Alpine build does not support wpa_supplicant's `-f` option;
the first invocation printed usage without starting a daemon. The working
foreground daemon is managed by `start-stop-daemon` with private stdout/stderr.
The reviewed DHCP hook changes only `wlan0` addresses and its metric600
default route; the USB route is retained. Resolver integration writes the
existing file in place because Arch has a read-only bind of that inode.
The original resolver is backed up privately; deconfiguration restores it
only when it still matches our generated contents.

`tools/hardware/wifi-acceptance.py` emits sanitized checks, not network
identifiers. It tests association, both Linux resolvers, WLAN-bound DNS and
TLS-verified HTTPS, rescue route/carrier and model health. Credentials,
addresses, SSIDs, BSSIDs and raw scans remain private. USB-disconnected use,
reboot autostart, suspend/resume, roaming and sustained throughput are not
accepted by this test. No Bluetooth/audio or GPU/NPU result follows from Wi-Fi.
