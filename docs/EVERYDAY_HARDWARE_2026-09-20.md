# Everyday hardware status — 2026-09-20 18:18 UTC

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
