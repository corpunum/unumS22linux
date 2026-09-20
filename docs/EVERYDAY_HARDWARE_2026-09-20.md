# Everyday hardware status — 2026-09-20

## Latest implementation checkpoint — 20:41 UTC

**Wi-Fi now starts automatically after recovery reboot.** BORE759 and760
both restored desktop, resident CPU model, WPA association and DHCP without
host restaging or radio commands. Wi-Fi service/association readiness was
38.686s and38.659s respectively; separate WLAN-bound DNS/HTTPS, native/Arch DNS
and USB rescue checks passed beyond60s uptime on each. No unintended reboot,
flash or physical action occurred in this round. See
[automatic startup evidence](WIFI_AUTOSTART.md).

Current BORE760 has Hyprland/Quickshell/Squeekboard running, empty compositor
configerrors, persisted battery/keyboard configuration and stride marker.
Other hardware limitations remain: physical finger sensing, Bluetooth, usable
audio, GPU/NPU compute, camera/cellular/suspend and normal cold-power-on Linux
are not newly accepted. USB was left connected throughout; physical unplug
behavior and ordinary phone battery life remain untested.

## Previous implementation checkpoint — 20:06 UTC

**Wi-Fi now passes live connectivity checks.** Still RECOVERY BORE758, over
65 minutes uptime, with healthy USB SSH and resident model. WPA2/CCMP, DHCP,
native Alpine and Arch app DNS, explicitly WLAN-bound DNS and HTTPS passed.
The resolver inode is shared with Arch and was preserved during DNS setup.
The WLAN default route uses metric600, leaving the USB rescue route intact.

Calibration completed in the corrected activation; the missing optional QDSS
firmware response caused two WLAN-only recovery cycles before being handled.
No further SoC reboot, partition write or physical action occurred. The owner
approved privately reusing the matching active saved host Wi-Fi profile.
Credentials and tools are persisted on userdata, **not yet boot-autostarted**.
See [acceptance checks](../evidence/wifi-connected-20260920/acceptance.json),
[platform and installed hashes](../evidence/wifi-connected-20260920/platform.json)
and [the Wi-Fi record](WIFI_NATIVE.md). Other hardware limitations below remain.

## Previous implementation checkpoint — 19:22 UTC

Native RECOVERY **BORE758**, SSH/model healthy at20 minutes uptime. This
supersedes the historical18:18 audit below. No partition writes were made in
this implementation round. The Wi-Fi test caused a kernel-panic reboot at
19:01:36; it was not an intentional guardian reset. The phone returned to
RECOVERY and automatically restarted Linux/desktop/model without new host
staging, requested physical input, or flashing.

| Function | Latest result |
| --- | --- |
| Display and UI | Stride fix persisted across reboot; clean framebuffer, icons/fonts restored. Owner's physical acceptance was on BORE757. |
| Battery | Visible bar percentage and popup from native sysfs,98% Charging29.7C. Unsupported profiles hidden; no charge-cycle/lifetime test. |
| Screen power | Lua power-key binding installed; controlled DPMS on/off/on passed with model still healthy. Physical key untested. |
| Touch keyboard | Keyboard bar button installed in active and startup-template config. Synthetic tap at(.92,.011) reveals Squeekboard. Physical finger unproven; button added after the last reboot. |
| USB/model | USB SSH and CPU model health confirmed after reboot. Route remains through USB, not Wi-Fi. |
| Wi-Fi | Exact matching module loaded and firmware reached ready, but delayed calibration collided with normal startup and panicked. Module now unloaded; no autoload. No association/traffic acceptance. |
| Audio | BORE757 ABOX core firmware reached ready/failsafe ONLINE; debug capture channels only, no speaker playback. Temporary firmware binds were lost on reboot; BORE758 has no soundcards. |
| Bluetooth | BlueZ installed; no HCI controller or compatible verified native UART initialization. |
| GPU/NPU | No new acceleration result; GPU compute still faults, NPU inference unproven. |
| Camera/cellular/suspend | No new acceptance; not working as a daily phone. |
| Normal power-on | Still unfinished. Keep RECOVERY; do not normal-boot the restored Android BOOT. |

Signed radio/audio tools were installed without disabling APK signatures.
Remaining CACHE-overlay space is34,088KiB (94% used); firmware/model/package
staging stays on userdata. Persistent userdata has about100GiB available.
No audio playback/recording, camera capture or network credentials were used.

Details: [Wi-Fi](WIFI_NATIVE.md), [Bluetooth/audio](BT_AUDIO_NATIVE.md),
[input/power](INPUT_POWER_NATIVE.md). Evidence:
[final live state](../evidence/hardware-20260920/final-live-state.txt),
[keyboard before](../evidence/hardware-20260920/keyboard-before.png),
[keyboard after](../evidence/hardware-20260920/keyboard-after.png), and
[battery panel after reboot](../evidence/hardware-20260920/power-panel-bore758.png).

Next driver work must fix the source-backed startup sequence before another
WLAN activation; no automatic retry is configured. The existing vendor rc
includes EFS writes/permission changes and must not be run wholesale. Audio
DSP startup is a milestone, not evidence of usable sound.

## Historical read-only audit — 18:18 UTC

Live native RECOVERY BORE757, guardian PID1, kernel 5.10.260-g4e5c5ad7d950.
No Android services, reboot, partition flash, radio activation, camera/mic
capture or playback test was performed for this audit.

| Function | Current evidence | Acceptance |
| --- | --- | --- |
| Internal display | Owner confirms physical screen is clean after the linear-stride workaround | Physically confirmed; startup configuration now persisted |
| Hyprland/Omarchy UI and model | Same boot uptime 17,751.97s; resident model health `ok`; desktop processes still running | Live operation confirmed, nearly five hours without reboot |
| USB networking/SSH | ecm0, 10.55.0.2/24, default route via 10.55.0.1; pinned SSH works | Confirmed |
| Internet over USB | HTTPS spider to official Alpine v3.24 ARM64 APKINDEX succeeds, including DNS resolution | Confirmed via USB, not Wi-Fi |
| Wi-Fi | No wlan interface; ieee80211 class empty; cfg80211/cnss2 loaded; qcom,cnss-qca6490 platform device bound | Not working in current native environment |
| Bluetooth | btpower and bt_qca6490 platform binding exist; no HCI controller; rfkill soft-block 1/hard-block 0 | Not working in current native environment |
| Touch/buttons | Hyprland enumerates sec_touchscreen and power/volume keys; earlier synthetic touch-to-chat test passed | Physical taps/buttons still require confirmation |
| Sound/microphone | aboxvdma/aboxdump sysfs cards exist; /dev/snd exposes only timer, no PCM/control nodes | No usable normal audio path; playback/recording untested |
| Cameras | Exynos ISP, MFC, JPEG/scaler V4L2 nodes registered | Sensor operation/capture not tested; no camera opened |
| Cellular/SMS/calls | rmnet0–7 and umts_dm0 present but down; no demonstrated modem service | Not working/accepted; no SIM identifiers queried |
| Battery/thermal | Battery 100%, Full, Good, 29.6C; AC online; thermal zones readable | Telemetry confirmed; charging cycle/stress testing not performed |
| Storage | ext4 userdata 104.5GiB, 4.2GiB used, 100.3GiB free; persistence previously recovery-reboot tested | Working; preserve existing filesystem |
| GPU/NPU inference | Previous GPU compute fault unresolved; no NPU inference acceptance | CPU model only |
| Suspend/wake | No controlled test | Unverified; avoid assuming phone-like battery life |
| Normal power-on Linux | Original Samsung BOOT restored after failed candidate; Linux selected through RECOVERY | Not finished; do not normal-boot Android |

Wi-Fi tools (iw, wpa_supplicant, wpa_cli) and Bluetooth userspace/tools are
absent. The inspected mounted firmware directories did not expose matching
radio assets. This narrows the next investigation to firmware, WLAN/HCI
initialization and native userspace integration; it does not prove the chips
are faulty or that booting Android is required. No radios were unblocked or
brought up as part of the status audit.

## Display persistence

After the owner's physical confirmation, installed the updated supervisor
and `/etc/s22-linear-stride-enabled` as ordinary cache-overlay files. The
library already resides on persistent userdata. Full file-hash readback:
`evidence/persistence-20260920/stride-persistent-install-readback.txt`.
The original supervisor remains at
`/usr/local/bin/start-persistent-desktop.pre-stride`.

The marker selects the workaround without a launch-time environment variable;
`S22_LINEAR_STRIDE_TRIAL=0` overrides it for a controlled rescue launch.
Seven mocked supervisor tests (including marker/default/override cases) and
25 C selection assertions passed. A live import confirms marker enabled,
no trial environment, and library present. The existing clean session was
left running. **A new recovery reboot with the persisted configuration has
not yet been tested.** Normal-power-on boot remains a separate problem.

## Next order

1. Bring up Wi-Fi using the exact QCA6490 driver/firmware path, retaining USB
   SSH as rescue. Association plus traffic explicitly routed over Wi-Fi is
   required before calling Wi-Fi working.
2. Confirm physical touch and buttons, then integrate audio and Bluetooth.
3. Continue camera/cellular/suspend work according to actual feasibility.
4. Retry normal-power-on Linux only after packaging checks and with an
   available physical rescue route. Do not sacrifice the working RECOVERY.

The small CACHE overlay has only about 51MiB free; stage bulk packages and
firmware on userdata/host. Native signed pacman transactions remain unaccepted
because of the earlier signature-helper hang. Do not fill CACHE or work around
package signature checks to add hardware tools.
