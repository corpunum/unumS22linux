# What this repo's scripts assume

Part of the [porting guide](../PORTING.md). The tools in this repo were written for **one** phone
and **one** host. Many check the phone's identity and refuse to run on anything else; that is on
purpose ([tools/persistence/README.md](../../tools/persistence/README.md): "Do not generalize
device paths to another phone"). This page lists what is hard-coded, so you know what to change and
what to re-derive for your device. Re-derive means: find the real value on your phone and in your
stock image; don't copy ours.

## Host side

| Assumption | Where | Change it to |
|---|---|---|
| Host user `corpunum`, checkout at `/home/corpunum/s22-linux` | About 36 files under `tools/` (for example [`tools/s22-ssh`](../../tools/s22-ssh), [NATIVE_LINUX.md](../NATIVE_LINUX.md)) | Your checkout path |
| Private backups in `/home/corpunum/s22-private-backups/` | [`tools/persistence/`](../../tools/persistence/README.md) | A private, non-synced directory |
| Pinned phone host key in `evidence/native-linux-20260919/native-v2-known-hosts` (kept local, not published) | [`tools/s22-ssh`](../../tools/s22-ssh), [PUBLICATION.md](../PUBLICATION.md) | Your own phone's verified host key. Never disable host-key checking |
| ADB serial from `S22_ADB_SERIAL` | [`tools/capture-phone-state.sh`](../../tools/capture-phone-state.sh), [`tools/observe-native-boot.sh`](../../tools/observe-native-boot.sh) | Your phone's serial (kept out of the repo) |
| Your SSH public key `~/.ssh/id_ed25519.pub` baked into the root image | [tools/linux-rootfs/README.md](../../tools/linux-rootfs/README.md) | Your key |
| Device trial guard state in `/home/corpunum/.local/state/s22-device-trial-guard` | [`device-trial-guard.py`](../../tools/hardware/device-trial-guard.py) | Your state dir |
| Lineage build `lineage/build-20260915/recovery.img`, stock FYI3 images, their SHA256s | [NATIVE_LINUX.md](../NATIVE_LINUX.md#rollback), [FLASH_LOG.md](../../FLASH_LOG.md), builders in `tools/native-handoff/`, `tools/headless-recovery/` | Your own downloads and hashes |

## Boot image

| Assumption | Where | For another device |
|---|---|---|
| Recovery header v2, page size 2048, load addresses, LZ4 ramdisk, AVB `NONE` footer, 100,663,296-byte partition | [RECOVERY_COMPARISON.md](../RECOVERY_COMPARISON.md), [tools/headless-recovery/README.md](../../tools/headless-recovery/README.md) | Read from your Lineage `BoardConfig*.mk` and your real recovery image |
| Kernel `5.10.260-g4e5c5ad7d950`, ~324 modules in `r0s.load` order, first-stage init layout | [tools/native-handoff/README.md](../../tools/native-handoff/README.md), [MODULES.md](../../MODULES.md) | Your kernel and module list |
| Samsung `RESTART2` targets (`recovery`, `download`, `normal`) via `sec_reboot` | [tools/headless-recovery/README.md](../../tools/headless-recovery/README.md) | Check your kernel's reboot notifier; non-Samsung phones differ |
| Boot-mode log at `/proc/boot_reset` (BORE), needs `/dev/block/by-name/debug` | [NATIVE_LINUX.md](../NATIVE_LINUX.md#boot-image-and-evidence) | Samsung-specific |
| CACHE at `/dev/block/by-name/cache` (ext4, ~583 MiB) for the base overlay | [tools/native-handoff/README.md](../../tools/native-handoff/README.md#optional-cache-persistent-variant) | Many newer phones have no CACHE partition |

## Storage and layout on the phone

| Assumption | Where |
|---|---|
| userdata ext4 with UUID `1dd55c26-…` mounted at `/srv/s22`; the supervisor refuses any other UUID | [`start-persistent-desktop.py`](../../tools/persistence/start-persistent-desktop.py), [`deploy-persistent-files.py`](../../tools/persistence/deploy-persistent-files.py) |
| Everything persistent lives under `/srv/s22/...` (about 140 files refer to it) | `tools/hardware/**`, `tools/unum-fastshell`, `tools/s22d` |
| Arch root at `/srv/s22/arch`, bound at `/mnt/omarchy-trial` | [PERSISTENCE.md](../PERSISTENCE.md) |
| Bionic runtimes at `/srv/s22/android-rt`, `/srv/s22/enn-rt`, `/srv/s22/gnss-rt` | [modem-gnss.md](modem-gnss.md), [gpu-npu.md](gpu-npu.md) |
| Radio partition as `/dev/sda17` for `cbd` | [evidence/modem-20261006](../../evidence/modem-20261006/README.md), `tools/hardware/modem/s22-modem-up.sh` |

Never assume a numeric block device on another phone. Resolve by-name links from sysfs `PARTNAME`
and check the size ([EXPERIMENTS.md](../../EXPERIMENTS.md), "Native handoff V1").

## Network

| Assumption | Where |
|---|---|
| USB ECM: phone `10.55.0.2`, host `10.55.0.1`, host interface `enx027322000001`, NetworkManager profile `s22-linux-usb` | [NATIVE_LINUX.md](../NATIVE_LINUX.md#storage-and-networking), [`tools/s22-ssh`](../../tools/s22-ssh) |
| USB device controller `10b00000.dwc3` (soft disconnect for tests) | [`s22-net-deadman.sh`](../../tools/hardware/network/s22-net-deadman.sh), [`s22-netpolicy.py`](../../tools/hardware/network/s22-netpolicy.py) |
| Interface names `ecm0`, `wlan0`, `rmnet0` | same files |
| Carrier APN (in local config) | [tools/hardware/phoned/README.md](../../tools/hardware/phoned/README.md) |

## Hardware

| Subsystem | Assumptions | Where |
|---|---|---|
| Wi-Fi/BT | QCA6490, CNSS platform device `qcom,cnss-qca6490`, the Lineage `wlan.ko` hash, the firmware file list, the BT UART | [WIFI_NATIVE.md](../WIFI_NATIVE.md), [BT_AUDIO_NATIVE.md](../BT_AUDIO_NATIVE.md), `tools/hardware/bt/` |
| Audio | ALSA card 0, PCM numbers (100/102/110 graph, 6/16 direct), control names from this `mixer_paths.xml`, the 4-slot mic layout | [`s22-say.sh`](../../tools/hardware/s22-say.sh), [`s22-rec.sh`](../../tools/hardware/s22-rec.sh), [tools/hardware/audio/](../../tools/hardware/audio/) |
| GPU | Samsung Xclipse HAL path and its library closure, SGPU device nodes | [tools/gpu-compat/README.md](../../tools/gpu-compat/README.md) |
| NPU | `/dev/vertex10`, `AIE.bin`, the VS4L BOOTUP ioctl, ENN library names | [tools/hardware/npu/](../../tools/hardware/npu/), [evidence/npu-20261007](../../evidence/npu-20261007/README.md) |
| Modem | `cbd` arguments (`-t ss310`), `/dev/umts_ipc0`, `/dev/umts_rfs0`, SIPC 4.1 framing, the NV checksum scheme | [tools/hardware/phoned/README.md](../../tools/hardware/phoned/README.md), [evidence/modem-20261006](../../evidence/modem-20261006/README.md) |
| GNSS | `/dev/gnss_ipc`, the ioctl numbers blocked by the seccomp guard, `gps.cfg` keys | [GNSS.md](../../tools/hardware/gnss/GNSS.md) |
| Camera | `/dev/video101` + `/dev/video210` with their sysfs names, the shot-struct offsets, sensor modes, GBRG order, the colour matrix | [CAMERA.md](../../tools/hardware/camera/CAMERA.md) |
| Display/input | 1080×2340 panel, stride workaround, `sec_touchscreen` evdev | [s22-linear-stride.md](../../tools/omarchy-trial/s22-linear-stride.md), [INPUT_POWER_NATIVE.md](../INPUT_POWER_NATIVE.md) |
| Power | `batt_slate_mode`, `batt_read_raw_soc`, Samsung battery sysfs | [evidence/power-20261022](../../evidence/power-20261022/README.md) |

**The rule for all of these:** the scripts check identities (sysfs names, hashes, sizes) and refuse
on a mismatch. When you port one, keep the check and change the expected value to what you
verified on your phone. Don't delete the check to make it run.

## The pieces that are already portable

- The **Rust touch shell** has a config file with auto-detection for the display, touchscreen,
  battery and thermal sensors, and a guide for other Linux phones:
  [tools/unum-fastshell/PORTING.md](../../tools/unum-fastshell/PORTING.md).
- The **host regression tests** run on any Linux host with no phone:
  `python3 -I -B tools/hardware/run-host-regressions.py --mode both` ([tools/hardware/README.md](../../tools/hardware/README.md)).
- The **methods**: [`gen-mixer-batches.py`](../../tools/hardware/audio/gen-mixer-batches.py) for any
  `mixer_paths.xml`, the dead-man switch pattern, the trial guard, and the GNSS seccomp-sandbox
  pattern.
