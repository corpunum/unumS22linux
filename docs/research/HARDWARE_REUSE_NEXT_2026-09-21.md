# Native hardware reuse inventory — 2026-09-21

Read-only audit of the live SM-S901B/DS (r0s), Exynos 2200/S5E9925 native
boot. No device nodes were opened, no ioctls/rfkill writes/services/reboot or
radio actions were performed, and no SIM identifiers or private identifiers
were queried. Network state was not changed; SSH remained the only transport.

**Subsequent supervised result:** the parent then staged the exact CHUB
firmware, started the hub, and accepted two bounded accelerometer and two
gyroscope sample trials. This later state-changing work is documented in
[sensor evidence](SENSORHUB_WORKING_2026-09-21.md), not part of the read-only
inventory below. Calibration, desktop integration and other sensors remain
unaccepted.

Audio subsequently booted authenticated Calliope firmware, version `6XH0`,
and registered 34 dump/debug capture PCMs, but no usable machine card. A
read-only backup of the existing radio image contains the `S901BXXSIFYI3`
build marker; the modem remains `INIT`. See the separate
[audio/cellular trial results](AUDIO_CELLULAR_PREFLIGHT_2026-09-21.md).

## Initial read-only inventory

This table preserves the pre-startup observations. The subsequent sensor and
audio results above supersede its initial enumeration-only states.

| Function | Kernel/driver evidence | Nodes/userspace | Gap and safe next test |
|---|---|---|---|
| GPU/display | `/sys/class/drm/card0`, `renderD128`: `sgpu`; `card1`, `renderD129`: `exynos-drm`; modules `sgpu`, `exynos_drm` live | Samsung Vulkan/OpenCL arithmetic and bounded Qwen0.8B all-layer Vulkan offload pass | Resident4B remains CPU, Hyprland software-rendered; WSI and sustained reliability remain unaccepted. |
| Wi-Fi | `wlan0` is up and bound to PCI `cnss_pci`; `cfg80211`, `cnss2`, WLAN modules live | `/usr/sbin/iw`, `/sbin/wpa_supplicant`; existing research records WLAN-bound acceptance | No new traffic test here. Preserve USB rescue and do not reload the driver. |
| Bluetooth | Platform `bt_qca6490` is bound to `bt_power`; `btpower` module live; no `/sys/class/bluetooth/hci*` | BlueZ `btattach` supports `qca`; `/dev/ttySAC1` is the transport UART, not an HCI controller | Missing automatic serdev binding does not rule out line-discipline attachment. Exact QTI power/baud/firmware contract is being recovered; see [corrected preflight](HARDWARE_REUSE_BT_PREFLIGHT_2026-09-21.md). |
| Audio | ABOX core/topology/VDMA drivers are bound; current `/sys/class/sound` has `card0=aboxvdma`, `card1=aboxdump` only | `/dev/snd/timer` only; `aplay`, `arecord`, `alsactl` exist; no PCM/control nodes; PipeWire libraries exist but no daemon binary | Stage/verify exact ABOX closure and topology only. A usable speaker/mic path needs machine-card registration and mixer; do not call audio working. |
| Cameras | `exynos-is`, `exynos-is-sensor`, MFC/JPEG/scaler/GDC drivers are bound; sensor devices `2la`, `3k1`, `gn3`, `imx374` appear in platform inventory | Many `/dev/video*` nodes; no `v4l2-ctl` or `media-ctl`; installed libcamera IPA set is generic Mali-C55/RKISP1/RPi, not Samsung ISP | Safe test: source-level media-graph/setfile inventory. A single owner-approved capture is required before claiming a sensor works; do not open a node in this audit. |
| Sensors | `nanohub`, `sensors_core`, `iio` are live; IIO devices 0–40 enumerate accelerometer, gyro, geomagnetic, pressure, light, proximity, rotation/step and gesture names | `/dev/iio:device0..40`; no `sensors` command; no Android sensors HAL in native userspace | Hardware enumeration only. Safe test: read-only sysfs metadata/trigger inventory; physical motion and framework delivery remain unproven. |
| Touch/input | `sec_ts`/`stm_ts_spi` live; `sec_touchscreen` and `sec_touchproximity` are registered; current dmesg shows driver heartbeat | `/dev/input/event0..10`; touchscreen firmware is a separately named vendor asset | Driver/input registration is proven, but physical finger input is not. Require one owner-confirmed physical event; synthetic/compositor events do not count. |
| Cellular/SIM | `cpif`, `dev_ril_bridge`, `gnssif`/GNSS mailbox are present; `cpif` platform device is bound | `/dev/umts_*` and `rmnet0..7` exist but are down; no `rild`, `ofonod`, `mmcli`, `qmicli`, or `mbimcli` | No data, SMS, call, or VoLTE acceptance. Exact Samsung RIL/CP boot/SIPC, radio HAL and vendor services are missing from native userspace. Do not query SIM or activate radio. |
| NPU | `npu`, `npu_exynos`, `hwdev_npu` are live; `/dev/vertex10` exists | No ENN runner/service in native userspace | Kernel exposure is not inference. Keep NPU blocked pending exact ENN/firmware/service closure. |

## Exact reuse boundaries

The matching Lineage r0s device manifest identifies the Android Bluetooth
service and QCA firmware (`android.hardware.bluetooth@1.0-service-qti`, QCA
firmware files) and the camera setfiles/touch firmware:
[r0s proprietary-files.txt](https://raw.githubusercontent.com/LineageOS/android_device_samsung_r0s/lineage-23.2/proprietary-files.txt).
The common S5E9925 tree packages Android Bluetooth, Samsung audio HAL,
camera provider, sensors HAL, and the RIL stack; its graphics/audio/RIL
package lists are explicit in
[device-common.mk](https://raw.githubusercontent.com/LineageOS/android_device_samsung_s5e9925-common/lineage-23.2/device-common.mk).
The same common proprietary manifest names `rild`, `libsec-ril`, Samsung
radio/sehradio manifests and the vendor radio AIDL libraries:
[common proprietary-files.txt](https://raw.githubusercontent.com/LineageOS/android_device_samsung_s5e9925-common/lineage-23.2/proprietary-files.txt).

Therefore the realistic routes are:

* **Bluetooth:** the exact QCA6490 Android implementation is a source for the
  power, UART and firmware contract. A full Android service is not proven
  mandatory. BlueZ's `btattach` can create an HCI device through the kernel's
  UART line discipline, once this specific controller is initialized correctly.
  Upstream Linux's QCA driver documents
  the HCI UART/IBS protocol, but does not establish this Samsung DT binding or
  firmware variant: [Linux hci_qca.c](https://github.com/torvalds/linux/blob/master/drivers/bluetooth/hci_qca.c).
  BlueZ's own documentation describes `hciattach` as a generic UART attach,
  not a validated QCA6490 recipe: [BlueZ hciattach](https://github.com/bluez/bluez/wiki/hciattach).
* **Cellular/SIM data:** the least-ambiguous reuse path is the exact Android
  Samsung RIL/cbd/SIPC and vendor radio service closure. oFono is a possible
  architecture only behind a working modem backend; its Android-RIL/binder
  integrations are generic and do not provide Samsung's proprietary radio
  implementation: [oFono RIL binder plugin](https://github.com/mer-hybris/ofono-ril-binder-plugin).
  `rmnet` node presence alone is not data-plane proof.
* **Calls/VoLTE/IMS:** strictly downstream of the above RIL/radio service,
  carrier provisioning and call-audio routing. The public phh IMS project
  explicitly warns that it still depends on Samsung RIL behavior, CarrierConfig,
  sepolicy and audio HAL; it is not a drop-in native Linux IMS stack:
  [phh IMS integration boundary](https://github.com/Samsung-Galaxy-A05/vendor_phhims).
  No calls, SMS, IMS registration or VoLTE claim is supported by this audit.
* **Audio/camera/sensors/touch:** kernel registration and node enumeration are
  real, but the Android HAL/service closures are separate. Reuse exact firmware
  and source-matched HALs only; do not substitute generic Linux blobs or infer
  physical operation from node presence.

## Result

The native kernel is substantially hardware-bound. WLAN, USB remote access,
software display and battery reporting have earlier evidence; Samsung GPU
compute and a bounded small-model GPU path now have accepted runtime evidence.
Bluetooth HCI,
normal audio, camera capture, cellular data/SMS/calls/VoLTE, and physical touch
remain unproven or blocked by missing Android/vendor userspace. This note does
not authorize activation; the next experiments must remain individually
bounded and preserve USB rescue.
