# unumS22linux

Native Linux experiments on the **Samsung Galaxy S22 SM-S901B/DS**,
Exynos 2200, codename `r0s`, unlocked bootloader. This is the S22, not S22+.

## Current result — 2026-09-20

**Current state (20:41UTC):** native Linux is running in RECOVERY
BORE760 with the persistent desktop and CPU model. **Wi-Fi now autostarts:**
WPA2 association, DHCP, DNS and TLS-verified HTTPS forced through WLAN passed.
DNS also works in both Alpine and Arch/Omarchy. USB SSH remains available.
Two consecutive recovery-target software reboots (BORE759/760) automatically
restored Wi-Fi association and services in about39s. Separate internet checks
passed after more than60s uninterrupted uptime on each. No physical action,
image flash or normal-BOOT selection was needed.
The owner previously confirmed the physical display is clean with the
[narrow display-stride workaround](tools/omarchy-trial/s22-linear-stride.md),
which now has reboot-observed persistence. Battery telemetry, restored icons,
screen-power control, and a visible **Keyboard** button are installed. A
synthetic touchscreen tap reveals the keyboard; physical finger acceptance
remains unproven. Bluetooth/audio remain unaccepted. Wi-Fi tools and the private
profile are persisted. See [Wi-Fi recovery-boot acceptance](docs/WIFI_AUTOSTART.md);
unplugged USB operation and suspend/resume remain untested.
See the [current screen](evidence/hardware-20260920/keyboard-after.png) and
[everyday hardware audit](docs/EVERYDAY_HARDWARE_2026-09-20.md).
Normal power-on Linux is still unaccepted; do not boot the restored Android
BOOT or wipe userdata.

The first persistent accepted session, BORE519, ran **Alpine Linux ARM64 from RECOVERY**,
with no Android services. Persistent **Arch Linux ARM + Hyprland + actual
Omarchy Quickshell UI**, terminal, Squeekboard and a local CPU-only
**Qwen3.5-2B** streaming chat model started automatically after reboot.
Readiness was observed at15.81s, and the stable sample passed77s uptime.

[Actual screen capture after reboot](evidence/persistence-20260920/persistent-after-reboot-chat.png)
and [measured results](docs/DRIVER_MODELS_2026-09-20.md).

| Component | Last verified state |
| --- | --- |
| Native boot | Alpine 3.24.2; guardian PID1; Samsung/Lineage 5.10.260 kernel |
| Persistent base | Alpine package/file overlay in existing CACHE; reboot-tested |
| Persistent desktop/model | ext4 userdata; automatic recovery startup verified again at BORE759/760 |
| Desktop | Arch ARM, Hyprland 0.56.2, Omarchy v4.0.4; software-rendered |
| Keyboard/input | Visible Squeekboard plus Keyboard bar button; synthetic tap reveal passed; physical finger sensing unverified |
| Battery/display power | Native telemetry bar/panel; controlled DPMS off/on passed; no suspend/battery-life acceptance |
| Local model | Resident Qwen3.5-2B Q4_0, 4K context, four fast CPU cores, loopback-only API |
| CPU benchmark | 0.8B: 20.21 tok/s; 2B: 10.13 short / 5.47 at depth4096 |
| GPU | Experimental RADV enumerates; a mapping bug was fixed, but compute still faults |
| NPU | Vendor assets investigated; no working inference |
| Connectivity | USB rescue retained; Wi-Fi association, DHCP, DNS and HTTPS passed after two automatic recovery-boot startups |
| Other everyday hardware | Bluetooth, usable audio, cellular, camera and suspend remain unaccepted |

Short resident chat tests started streaming in 0.4–0.7 seconds. Those samples
are not sustained agent benchmarks. The chat client does not execute commands.

**This is not a complete Omarchy distribution or a daily-driver phone.** The
Arch desktop, runtime and model now persist on userdata and start automatically
on recovery boot, without host restoration. Normal cold-power-on routing is
unconfirmed after the BOOT attempt. Native signed
package installation currently hits a kernel/runtime helper hang; host-verified
package deployment works. See [the migration record](docs/PERSISTENCE_MIGRATION_2026-09-20.md).

## Architecture and safety

AOSP first-stage init performs hardware/module bootstrap, then hands off to
the native guardian before Android services start. Android recovery binaries
remain available for rescue. This is not mainline Linux and not a chroot over
a running Android userspace; the Arch desktop chroot runs over native Alpine.

- The owner approved userdata conversion after private backup and explicit
  data-loss acceptance, then separately approved BOOT work while preserving
  RECOVERY rescue. No MISC, PIT, EFS, IMEI, bootloader or TrustZone changes.
- Ordinary files under the existing CACHE Linux directory provide the base
  overlay. CACHE was not reformatted; userdata was intentionally converted
  to ext4, replacing Android's old userdata filesystem.
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
