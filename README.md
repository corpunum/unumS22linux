# unumS22linux

Native Linux experiments on the **Samsung Galaxy S22 SM-S901B/DS**,
Exynos 2200, codename `r0s`, unlocked bootloader. This is the S22, not S22+.

## Current result — 2026-09-20

The phone boots **Alpine Linux ARM64 from RECOVERY**, with no running Android
services. A RAM-staged **Arch Linux ARM + Hyprland + actual Omarchy Quickshell
UI**, terminal and Squeekboard runs on its internal display. A local,
CPU-only **Qwen3.5-2B** model serves streaming terminal chat.

[Actual screen capture](evidence/driver-model-20260920/omarchy-resident-chat.png)
and [measured results](docs/DRIVER_MODELS_2026-09-20.md).

| Component | Verified state |
| --- | --- |
| Native boot | Alpine 3.24.2; guardian PID1; Samsung/Lineage 5.10.260 kernel |
| Persistent base | Alpine package/file overlay in existing CACHE; reboot-tested |
| Desktop | Arch ARM, Hyprland 0.56.2, Omarchy v4.0.4; software-rendered |
| Keyboard/input | Visible Squeekboard; synthetic touchscreen-to-chat test passed; physical finger sensing not verified remotely |
| Local model | Resident Qwen3.5-2B Q4_0, 4K context, four fast CPU cores, loopback-only API |
| CPU benchmark | 0.8B: 20.21 tok/s; 2B: 10.13 short / 5.47 at depth4096 |
| GPU | Experimental RADV enumerates; a mapping bug was fixed, but compute still faults |
| NPU | Vendor assets investigated; no working inference |
| Connectivity | USB Ethernet + SSH; Wi-Fi/cellular/audio/camera/suspend not accepted |

Short resident chat tests started streaming in 0.4–0.7 seconds. Those samples
are not sustained agent benchmarks. The chat client does not execute commands.

**This is not a complete persistent Omarchy installation or a daily-driver
phone.** The Arch desktop, runtime and model currently live in RAM. The base
Linux overlay persists, but the large components need host restoration after
reboot. Normal cold-power-on routing has not been converted to Linux.
See [the persistence plan](docs/PERSISTENCE.md).

## Architecture and safety

AOSP first-stage init performs hardware/module bootstrap, then hands off to
the native guardian before Android services start. Android recovery binaries
remain available for rescue. This is not mainline Linux and not a chroot over
a running Android userspace; the Arch desktop chroot runs over native Alpine.

- Raw image writes are limited to **RECOVERY**. No BOOT, MISC, PIT, EFS,
  IMEI, bootloader or TrustZone modification is authorized.
- Ordinary files under the existing CACHE Linux directory provide the base
  overlay. CACHE and userdata were not reformatted for this installation.
- Keep the hash-verified Lineage recovery rollback available before any
  image experiment. Use the documented flash gate; never bypass tool blocks.
- Targeted `s22-reboot recovery` has been verified without physical buttons.
  A failed software path can still require physical recovery intervention.
- Verify boot mode using bounded `/proc/boot_reset` reads, not the splash screen.

Current RECOVERY V3 SHA256:
`1a827b43d29141efb47f530902dd4e4ee2b6d780515893c9ecd676ad27efd7d1`.
Whole-partition readback matched again after the Omarchy/model experiments.

## Start here

- [EXPERIMENTS.md](EXPERIMENTS.md): canonical chronological log, including failed tests.
- [STATUS.md](STATUS.md): concise current state and outstanding work.
- [Native Linux](docs/NATIVE_LINUX.md): connection, reboot, storage and rollback.
- [Omarchy trial](docs/OMARCHY_TRIAL.md): scoped desktop and host restore procedure.
- [Drivers and models](docs/DRIVER_MODELS_2026-09-20.md): measurements and remaining failures.
- [Persistence](docs/PERSISTENCE.md): internal/external storage and cold-boot decisions.
- [Publication scope](docs/PUBLICATION.md): local-only artifacts and source provenance.

`tools/native-handoff/`, `tools/headless-recovery/`, `tools/linux-rootfs/`,
`tools/omarchy-trial/`, `tools/model-bench/` and `tools/npu-probe/` contain
project sources and experiment helpers. `initramfs/cinit*.c` preserves the
earlier custom-init experiments. `evidence/` holds publishable logs and frames.

The public checkout is **not a one-command installer**: large verified local
builds, downloaded packages/models, vendor firmware and private backups are
deliberately excluded. The full working tree remains on the original host.
