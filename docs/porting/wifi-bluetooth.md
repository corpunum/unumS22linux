# Wi-Fi and Bluetooth

Part of the [porting guide](../PORTING.md). Both radios on the S22 are one Qualcomm QCA6490
chip, controlled by the kernel's CNSS driver. Wi-Fi works and starts at boot; Bluetooth comes up
at boot and scans, but pairing and audio are not done ([README](../../README.md#what-works-and-what-doesnt-yet)).

## A general lesson first: where firmware is looked up

The kernel looks for firmware in `firmware_class.path=/vendor/firmware`, resolved in **PID 1's
root** (the boot ramdisk), not in the root your SSH shell runs in. From the shell, that place is
`/proc/1/root/vendor/firmware`. Checking `/vendor` in your shell gives the wrong answer
([SENSORHUB_WORKING_2026-09-21.md](../research/SENSORHUB_WORKING_2026-09-21.md),
[evidence/audio-buttons-20261006](../../evidence/audio-buttons-20261006/README.md)). We expose
extra firmware there with read-only bind mounts or copies, file by file, without hiding what the
ramdisk already has ([EXPERIMENTS.md](../../EXPERIMENTS.md), "Recovered ABOX core firmware").

## Wi-Fi

**What we looked at in the stock image** ([WIFI_NATIVE.md](../WIFI_NATIVE.md)):

- the module: the stock `wlan.ko` does not match the Lineage kernel's ABI and **must never be
  loaded**. The LineageOS build's `vendor_dlkm` has one with the exact vermagic;
- the QCA6490 firmware files (`amss20.bin`, `bdwlan.elf*`, `m3.bin`, `regdb.bin`, `qcom_cfg.ini`,
  [BT_AUDIO_NATIVE.md](../BT_AUDIO_NATIVE.md));
- the vendor `.rc` files: `wlan_vendor_rc` loads the module at early-init and writes
  `fs_ready=1` to the CNSS platform device at post-fs-data; `wlan_common_rc` starts `macloader`.

**What went wrong first.** Our first activation wrote `fs_ready` about 181 s after loading the
module. The CNSS driver has a deadline for cold-boot calibration; missing it made the driver enter
"mission mode before calibration", assert, and **panic the kernel**. That was a sequencing bug, not
missing calibration data ([WIFI_NATIVE.md](../WIFI_NATIVE.md#official-init-ordering),
[wifi-panic-sanitized.txt](../../evidence/hardware-20260920/wifi-panic-sanitized.txt)).

**What works** ([wifi-next-experiment.md](../wifi-next-experiment.md), [WIFI_AUTOSTART.md](../WIFI_AUTOSTART.md)):

1. start a small responder **before** loading the module; it answers one optional firmware
   request the driver makes, so the driver doesn't wait out a long fallback;
2. mount the firmware read-only where PID 1 sees it;
3. insert the exact module and write `fs_ready=1` in the same process, well inside the deadline;
4. WPA supplicant, then DHCP; DNS and HTTPS checked bound to `wlan0`.

We **did not** run the vendor `.rc` wholesale or the proprietary `macloader`, because they change
EFS file permissions; EFS stays off-limits ([WIFI_NATIVE.md](../WIFI_NATIVE.md#official-init-ordering)).

At boot the start is a one-shot, optional task: a failed attempt is recorded and blocks automatic
retries until someone looks at it, and it can never take SSH or the desktop down
([WIFI_AUTOSTART.md](../WIFI_AUTOSTART.md#startup-and-isolation)). It passed two consecutive reboots
first; later the phone stayed reachable with USB physically unplugged
([evidence/network-independence-20261022](../../evidence/network-independence-20261022/README.md)).

**For your phone:** read your Wi-Fi driver's `.rc` lines and its source for deadlines and
handshakes (`fs_ready`, MAC address, calibration). Use a module built for the exact kernel you run.

## Bluetooth

On Android, the BT stack loads firmware into the chip over UART from userspace. We do the same
with our own small C programs, not the Android stack ([evidence/bt-scan-20261006](../../evidence/bt-scan-20261006/README.md)):

- the **holder** [`bt-qca6490-hci-hold.c`](../../tools/hardware/bt/bt-qca6490-hci-hold.c) powers the
  chip through the CNSS/regulator votes, downloads the patch and NVM configuration, switches the
  UART to 3 Mbit/s, and then bridges H4 packets to the kernel by attaching the `N_HCI` line
  discipline to a PTY. The kernel then sees a normal `hci0`;
- on stop it detaches, restores the UART and powers the chip off, with Wi-Fi unaffected.

Problems we hit, all documented:

- a raw-HCI socket path in the kernel needed a patch, deployed as a new RECOVERY kernel
  ([evidence/bt-scan-20261006](../../evidence/bt-scan-20261006/README.md),
  `tools/hardware/bt-hci-socket-restore.patch`);
- a firmware-profile/IBS mismatch: one bit in the NVM configuration decides whether the chip
  expects the in-band-sleep protocol, and the bridge must match it
  ([HISTORY.md](../HISTORY.md#checkpoint--2026-09-27), `hold_transport_mode()` in the holder);
- the baud switch only worked reliably with the holder pinned to the fastest core with real-time
  priority ([evidence/bt-scan-20261006](../../evidence/bt-scan-20261006/README.md));
- the holder originally refused to start without the USB link (a deliberate rescue gate); that was
  removed later by an owner decision, by patching one branch in the binary and the source
  ([evidence/audio-20261021](../../evidence/audio-20261021/README.md), "Bluetooth without the USB link").

Not done: BlueZ, pairing, Bluetooth audio ([evidence/bt-scan-20261006](../../evidence/bt-scan-20261006/README.md)).
A2DP will also need the ABOX `a2dpcom.bin` firmware ([BT_AUDIO_NATIVE.md](../BT_AUDIO_NATIVE.md)).

**For your phone:** find the BT firmware names and the UART device in the device tree and the
vendor `.rc`/HAL; check whether your chip speaks plain H4 or needs a vendor sleep protocol.
If your kernel already has the matching `hci_uart` support, use that instead of a holder.
