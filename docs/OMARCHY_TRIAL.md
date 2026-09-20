# S22 Omarchy trial — 2026-09-20

Updated status: **Hyprland, the real Omarchy Quickshell bar, a terminal and
Squeekboard now render on the S22 display**, using CPU software rendering.
An opt-in Aquamarine display-only patch and a consistent GCC16-libstdc++
build fixed the display startup path. Synthetic taps through the touchscreen
device typed `hi` into the visible chat terminal; physical finger sensing
has not been verified remotely. See the later evidence under
`evidence/driver-model-20260920/` and [the driver/model report](DRIVER_MODELS_2026-09-20.md).

This remains a RAM-staged Arch userspace session on the native Alpine boot,
not a complete persistent Omarchy installation. Alpine/Weston remains the
boot-tested fallback. GPU/NPU inference is not working. Historical failures
below describe the original unpatched trial and must not be read as the
current display result. No partition image was flashed during these trials.

The current terminal uses a resident Qwen3.5-2B CPU model server. It answered
the keyboard-submitted `hi` in 1.3 seconds (first text 0.7 seconds).
Squeekboard is explicitly revealed after startup; continuous compositor
debug logging is disabled for the indefinite handoff session.

## Restore after a native recovery reboot

Run these from the existing host repository with the verified USB SSH link
available. They stage ordinary files in fresh RAM and do not flash images:

```sh
bash tools/model-bench/stage-phone.sh
bash tools/model-bench/start-phone-server.sh
bash tools/omarchy-trial/stage-phone-ui.sh
tools/s22-ssh 'nohup env S22_AQUAMARINE_PATCH=1 S22_MODEL_CHAT=1 S22_TRIAL_SECONDS=session S22_HYPR_CONFIG=/root/hyprland-omarchy-ui.lua sh /tmp/phone-direct-trial.sh </dev/null >/tmp/omarchy-resident-session.log 2>&1 &'
```

The UI helper refuses an already active Arch trial mount; the model helpers
refuse a running model/server. Do not run them on top of the current session.
Each was exercised on the phone, but this final combined command sequence
has not been tested through another reboot. Do not use an untargeted reboot:
the native system's `s22-reboot recovery` is the tested target-selection path.
Cold-power-on BOOT routing remains unchanged.

## Preserved baseline

- RECOVERY V3: `builds/native_handoff_v3.img`, SHA256
  `1a827b43d29141efb47f530902dd4e4ee2b6d780515893c9ecd676ad27efd7d1`.
- Original Lineage rollback: `lineage/build-20260915/recovery.img`, SHA256
  `b5bf01c4a47091eb95078fc69b133b44c2b453b31c23433594c5b605e3747b55`.
- Working Alpine ordinary-files backup, taken before trial diagnostic packages:
  `evidence/omarchy-trial-20260920/alpine-working-root-backup.tar.gz`, SHA256
  `b67bc36440a587691981020fdf56b964c575b878629baf25af7086ca77cb5699`.
  This mode-0600 archive contains device SSH host keys: do not publish it.
- Native PID1 is `/system/bin/native-guardian`; boot-reset record 517 confirms
  RECOVERY. Samsung/Lineage kernel remains 5.10.260-g4e5c5ad7d950.

## Measured graphics results

Installed signed Alpine diagnostic packages: mesa-utils 9.0.0-r6,
mesa-vulkan-ati 26.1.6-r0, vulkan-tools and vulkan-loader 1.4.347-r0.

`vulkaninfo --summary`, forced to the Radeon ICD, exits 1. Mesa rejects the
phone's SGPU DRM interface version 3.40.0 because this Mesa build requires
3.54.0 or newer. No Vulkan physical device is enumerated. Evidence:
`evidence/omarchy-trial-20260920/stock-radv-enumeration.txt`.

`eglinfo -B` exits 3. Hardware device initialization fails; successful software
platforms report **llvmpipe (LLVM 22.1.3, 128 bits)**, not the Xclipse GPU.
Evidence: `evidence/omarchy-trial-20260920/egl-complete-probe.txt`.

This is an actual userspace/kernel ABI mismatch, not proof that every custom
driver is impossible. The Xclipse RADV experiment pins Mesa 24.3.4 and the
same downstream kernel family, but documents broken presentation/freezes:
https://github.com/mxxme-dev/radv-xclipse-patches/tree/d1b295e8c61c013c454e8015983a1d6b6df82bf2
Its Vulkan changes do not by themselves establish Hyprland's EGL/GLES path.

The initial cross-build failed on sysroot header paths. A later corrected
build enumerated the GPU. Its CPU-map lifetime bug was fixed and verified by
a command-recording/teardown test, but numerical GPU compute still faults and
times out. Removing the fork's forced completion flag revealed the real
incomplete fence. No model was offloaded to this driver.

## Omarchy source and scope

Official source staged separately at `tools/omarchy-trial/omarchy-source`,
tag v4.0.4, commit `c668141e9c42b13c80c9ca4ea108e11708c5e8a5`.
The normal disk installer has not been executed. Its partitioning and
encryption workflow is outside this experiment's safety boundary.

Current Omarchy uses Hyprland Lua configuration and Quickshell. Alpine 3.24's
Hyprland 0.54.3 and absent Quickshell package are not a drop-in equivalent.
The trial therefore stages Arch Linux ARM userspace separately. Kernel,
bootloader, and the running rescue/SSH system remain unchanged.

Omarchy's ARM work now includes Snapdragon laptops, not only Apple Silicon:
https://omarchy.org/news/2026/09/introducing-omarchy-dragon/
That announcement does not establish Exynos S22 compatibility.

Initial graphics attempts must be bounded and isolated. Do not run Omarchy's
default autostart, first-run provisioning, disk automounter, or system-wide
services in a chroot exposing phone block devices. A software-rendered or
nested desktop must be labeled as such, never as GPU acceleration.

## Actual phone trial results

Verified official Arch Linux ARM base archive SHA256:
`42a4eeaa038994ffd31fa173256ef2f0ef511358eeb41b9ea1f8626391b9b319`.
Detached signature matched documented build-key fingerprint
`68B3537F39A313B3E574D06777193F152BDBE6A6`. Host package installation retained
required package signatures. QEMU could not use pacman's Landlock sandbox, so
the documented `--disable-sandbox` compatibility option was used only for
this disposable host staging root; signature checking was not disabled.

The assembled userspace archive is `rootfs/arch-omarchy-trial.tar.gz`, SHA256
`ecb5ab0c75abb0bbb5e41cb1b9c017229974b87ec061c51110a580435439104d`.
Its hash was checked again on the phone before extraction into a separate
3 GiB tmpfs. Root/alarm passwords were locked. Generic kernel/firmware packages
were removed; no Arch bootloader, systemd PID1, Android service, emulator, or
Omarchy installer ran on the phone. ARM64 binaries executed natively in a
chroot on the existing Samsung kernel. A chroot trial is not a separately
booted or persistent Arch installation.

Measured versions: Hyprland 0.56.2-3, Aquamarine 0.15.1-1, Quickshell 0.3.1-1,
Mesa 26.2.3-1. `arch-phone-versions.txt` records native execution and exact ABI.

1. `Hyprland --verify-config` with the pinned Omarchy Lua configuration:
   **exit 0, config ok**, on the actual phone. Only default autostarts,
   provisioning, and keybindings were disabled for the diagnostic run.
   Evidence: `omarchy-config-on-phone.txt`.
2. Nested over working Weston/Pixman: **exit 134**. Aquamarine reports missing
   Wayland protocols and no allocator. The isolated nested test deliberately
   did not expose DRM or seatd; do not mistake its seatd error for a general
   phone limitation. Evidence: `hyprland-nested-logged.txt`.
3. Direct display: briefly stopped the known-good desktop, exposed only DRM
   devices/read-only udev metadata, and used a separate seatd with VT binding
   disabled. Selected `/dev/dri/card1` explicitly and forced software rendering.
   Hyprland ran for the bounded 20 seconds using **llvmpipe LLVM 22.1.8** and
   detected DSI-1, but repeatedly failed its EGL renderer/device match and
   display commits: `CDRMRenderer(drm): Can't create renderer, no matching
   devices found` and `Failed to update renderer state for DSI-1 on applyCommit`.
   Timeout exit **124** is the planned stop, not a crash or success.
   Evidence: `hyprland-direct-on-phone.txt`, `direct-seatd.txt`.

The direct-test cleanup restored Alpine Weston and its embedded terminal and
keyboard automatically. `after-omarchy-restored.png` captures that restored
screen, **not an Omarchy desktop**; capture occurred after automatic recovery.
BORE remains 517 / RECOVERY. No phone reboot or user button press was needed.
The temporary RAM root is discarded after evidence collection, with its
reproducible host archive retained. Full state is in `final-phone-state.txt`.

All evidence names above are relative to
`evidence/omarchy-trial-20260920/`. This establishes that distro/userspace
selection is not the immediate blocker. A working Exynos graphics buffer/
presentation path is needed before Omarchy can be accepted on this phone.

### Standalone Hyprland control, same date

On user request, repeated the direct test with `hyprland-minimal.lua`: no
Omarchy imports, autostarts, animations, blur, shadows, or Xwayland. It passed
configuration validation and ran under the same bounded software-rendering
setup. It reproduced the llvmpipe / EGL device-match / DSI-1 presentation
failure. This rules out Omarchy configuration as a necessary cause of that
failure. Evidence: `hyprland-minimal-config.txt` and
`hyprland-minimal-direct-retry.txt` (planned timeout exit 124).

The initial control attempt did not reach Hyprland: the Alpine launcher's
shell deferred its TERM trap while waiting for the foreground terminal
controller. The harness now stops the verified owned Weston process as well
as its launcher and does not start a duplicate baseline if one remains alive.
One redundant controller from that failed harness attempt was stopped by its
verified PIDs before retrying. This was a test-harness issue, not a Hyprland
or boot failure.

After the actual control test, exactly one baseline desktop/controller with
xterm and matchbox-keyboard was verified. Trial tmpfs unmounted; BORE still
517, uptime 3434.29 seconds. Evidence: `after-hyprland-minimal.txt`.

The model-speed table supplied by the user contains predictions, not results
from this phone. The live kernel reports `asimddp` and `i8mm`, supporting a
CPU-inference benchmark as a separate next task. Vulkan compute does not
require desktop presentation: a failed compositor is not proof that future
patched headless GPU inference cannot work. No model benchmark was run here.

## NPU boundary

The kernel driver and `/dev/vertex10` exist. Driver-source inspection identifies
Samsung VS4L ioctls and compiler-produced NCP v25 graphs, plus `NPU.bin` and
`vectors.bin` firmware requirements. A matching standalone Linux runtime,
compiler, and agent-model execution have not been demonstrated. ENN entries
in an Android proprietary-files manifest are not evidence that those binaries
are available or usable by native Alpine/Arch.
