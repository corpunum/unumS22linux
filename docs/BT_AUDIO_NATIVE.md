# Native r0s Bluetooth/audio bring-up preparation

This is a preparation record, not an activation procedure. It deliberately does
not copy files to the phone, load modules, change rfkill, start daemons, reboot,
or play/capture audio. The source-derived reprobe commands below are unexecuted
proposals retained for review, not required next steps.

## Exact vendor closure

The checked-in Lineage device manifests identify the required proprietary inputs.
The exact BT/audio subset has now been recovered read-only from the matching
F2FS vendor image into `rootfs/bt-audio-vendor-assets/`; no stock archive was
copied into this document. Its `manifest.sha256` records the recovered bytes.
The ABOX core pair `calliope_dram.bin` and `calliope_sram.bin` was recovered
from the pristine image separately. The source image SHA-256 is
`6dfe677119792e95a37b016092c327ad62bc0dc7759851424ad05e6bcb31b206`.

The core/topology minimum is `calliope_dram.bin`, `calliope_sram.bin`,
`abox_tplg.bin`, and `abox_tplg.conf`. The DT also declares 22 extra ABOX
blobs, including `a2dpcom.bin`, `AP_AUDIO_SLSI.bin`, `APDV_AUDIO_SLSI.bin`,
`APBiBF_AUDIO_SLSI.bin`, `APBargeIn_AUDIO_SLSI.bin`, `bidirmic.bin`,
`rxse.bin`, `txse1.bin`, `txse2.bin`, and `leaudio_{enc,dec}.bin`. The source
requests these extras with `request_firmware_direct`; missing extras are logged
and skipped unless marked as mixer controls, but complete speaker/microphone,
A2DP, and voice paths should stage the full DT-declared closure from the same
vendor image. `a2dpcom.bin` is therefore a preflight requirement for a
Bluetooth-audio claim even though it is not needed to create the core card.

Bluetooth/QCA6490 firmware names:

```
bt_nvm_loading_2nd.xml bt_nvm_loading.xml hpbtfw20.tlv hpbtfw21.tlv
hpnv20.bin hpnv21.bab hpnv21g.bab htbtfw20.tlv htnv20.bin
```

Wi-Fi firmware is shared by the QCA6490 closure, but is listed here because
Bluetooth and Wi-Fi initialization share the vendor controller path:

```
qca6490/amss20.bin qca6490/bdwlan.elf qca6490/bdwlan.elf1
qca6490/bdwlan.elf10 qca6490/bdwlan.elf2 qca6490/bdwlang.elf
qca6490/bdwlang.elf1 qca6490/bdwlang.elf10 qca6490/bdwlang.elf2
qca6490/m3.bin qca6490/regdb.bin wlan/qcom_cfg.ini
```

ABOX/audio firmware named by the common device manifest includes:

```
abox_tplg.bin abox_tplg.conf a2dpcom.bin
```

The full manifest is the authority for additional codecs, amplifier
calibration, VTS, and camera-adjacent audio assets. Do not substitute generic
linux-firmware files for this device-specific closure.

Run the host-side checker against a separately staged vendor/firmware directory:

```sh
tools/hardware/bluetooth-audio-readiness.sh /path/to/vendor/firmware
```

The checker is intentionally fail-closed and read-only.

## Required kernel and userspace layers

The recovery kernel currently has `btpower`, `cnss2`, and `cfg80211` loaded and
the `bt_qca6490` and `qcom,cnss-qca6490` platform devices bound. That alone did
not create an HCI controller, WLAN interface, or PCM/control nodes.

The module order shipped by the r0s recovery manifest places the QCA controller
closure (`cnss2`, `cnss_utils`, `wlan_firmware_service`, `cnss_plat_ipc_qmi_svc`,
`cnss_nl`, `cnss_prealloc`) before `btpower`; audio requires the ABOX/GIC,
VTS/SLIF, codec, amplifier, and Samsung machine modules near the end of that
same order. Preserve the shipped order and dependency graph when constructing
any future native image.

Android's device init only sets `/dev/btpower` ownership (`bluetooth:system`);
the actual Bluetooth HIDL service and its init rc are separate proprietary
inputs (`android.hardware.bluetooth@1.0-service-qti` and its rc). A native Linux
path therefore needs an explicitly selected QCA HCI transport/daemon compatible
with this kernel, not merely `rfkill unblock` or a generic `bluetoothctl`.

The exact local kernel config does contain the required transport support:
`CONFIG_BT`, `CONFIG_BT_HCIUART`, `CONFIG_BT_HCIUART_SERDEV`,
`CONFIG_BT_HCIUART_H4`, and `CONFIG_BT_HCIUART_QCA`. The device init source
assigns Bluetooth to `/dev/ttySAC1` (major 204, minor 65), so a future
future transport review should start from that node and the QCA firmware setup.
`ttySAC0` is not the Bluetooth node in the device configuration. Do not guess a
baud rate or use a generic `hciattach` recipe
until the selected loader is checked against this kernel's QCA setup; the
Android QTI service is the known compatible implementation, but its binary is
not present in the native Alpine root.

The local upstream `hci_qca` driver is not a direct match for this device-tree
node: it binds `qcom,qca6390-bt` (and other listed compatibles), while the live
phone exposes `bt_qca6490` with `qcom,qca6490`. The kernel's `bt_power` driver
therefore binds power/rfkill, but no automatic serdev HCI device is instantiated.
That proves the in-tree automatic path is absent; it does not prove a userspace
UART line-discipline attach is impossible. `btattach`/`hciattach` remains
unapproved and unproven because the QCA6490 firmware/protocol variant, UART
setup, and baud rate have not been identified. The compatible Android QTI
Bluetooth service/driver path or a separately reviewed native driver/DT change
is required before HCI activation.

Likewise ABOX requires `abox_tplg.bin`/`.conf`, the machine/codec modules, ALSA
card registration, and a userspace mixer/ALSA client. The earlier BORE757
debug capture saw `card0=aboxvdma`, `card1=aboxdump`, and sysfs
`controlC0`/`controlC1` (`116:2`/`116:3`); those were historical, ephemeral
runtime state and did not prove speaker playback. The current BORE758 state
has no soundcards and no automatic firmware activation. Topology firmware and
real machine-card registration remain unproven.

The kernel source explicitly requests the DT-named core firmware
`calliope_dram.bin` and `calliope_sram.bin`, and the live DT names those exact
files under `abox-core@18c55000`. A firmware-visible retry/probe is therefore
the minimal next ABOX experiment. The source's runtime-resume path calls
`abox_enable()`, which calls `abox_download_firmware()` and retries the core
firmware requests; the bounded sysfs trigger is writing `on` to
`/sys/devices/platform/18c50000.abox/power/control` after firmware visibility
is verified. This is a state-changing action and remains parent-approved only.
Do not unbind the ABOX core or force a module reload.

The ABOX topology component separately requests `abox_tplg.bin` (and optional
`sectiongraph_tplg.bin`) and retries for 80 seconds in its component probe.
The Rainbow sound-card registration retries ten times on `-EPROBE_DEFER` and
then gives up. If firmware-visible runtime resume succeeds but no PCM appears,
the next narrowly scoped fallback is parent-serialized unbind/bind of only
`0.abox-tplg`, followed by `sound` (`rainbow-sound`) to rerun topology/card
registration; the core device must remain bound. Verify `/proc/asound/pcm`,
`/sys/class/sound`, and dmesg after each individual operation.

`tools/hardware/abox-readiness.sh` performs the prerequisite check and prints
these commands without executing them.

The signed Alpine ARM64 closure is staged separately in
`rootfs/hardware-radio-addon/` and packed as
`rootfs/hardware-radio-addon.tar.gz`. It contains `bluez`, `bluez-openrc`,
`alsa-utils`, `iw`, `wpa_supplicant`, and recursively fetched dependencies.
The host-side reproducible fetcher is
`tools/linux-rootfs/build-hardware-radio-addon.sh`; it copies the base rootfs
only to a temporary QEMU staging tree and never installs into the base rootfs
or phone. The produced archive SHA-256 is recorded by the build output.

## Current blocker and safe next step

The signed BlueZ/ALSA addon is installed in the native root, and the ABOX
runtime-resume test reached “Calliope is ready to sing”; this proved core
firmware startup with failsafe reported ONLINE, but did not produce a usable
PCM path.
The firmware exposure was a temporary PID1-visible bind mount, so it was lost
when the CNSS kernel panic rebooted the SoC; this was not a guardian-triggered
rescue. No persistent firmware overlay or automatic reinitialization exists.
Bluetooth remains unavailable with no HCI class device. The absent automatic
serdev binding is not proof that a userspace UART line-discipline path is
impossible, but no compatible firmware/protocol setup has been established.
Any topology/card reprobe or radio activation requires a new parent-serialized
experiment after the recovered phone is re-audited.
