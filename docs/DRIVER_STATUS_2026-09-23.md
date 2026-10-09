# Native S22 driver status — 2026-09-23

The phone is currently running the native Alpine/Arch/Hyprland environment in
RECOVERY mode. This is a usable remote Linux session, not yet a complete phone
replacement: several everyday radios and audio paths are still unaccepted.

## Current live checkpoint

- Refreshed by read-only USB SSH on 2026-09-23 18:14 UTC. A bounded 32 KiB
  `/proc/boot_reset` read found BORE 767 at 00:09 UTC selecting RECOVERY; the
  boot ID matched the checkpoint and uptime was about 18.1 hours. No reboot
  or partition write was made during this refresh.
- The running kernel is `5.10.260-g4e5c5ad7d950`; PID 1 is
  `/system/bin/native-guardian`, and PID 1/probe mount namespaces match.
  Kernel config reports `CONFIG_MODULES=y`, `CONFIG_MODVERSIONS=y`, and
  `CONFIG_ARM64_4K_PAGES=y`.
- A read-only full-partition hash of live `/dev/sda16` (100,663,296 bytes)
  is `758fc9d30491e17b7c829a89d338ba69476efa15a1280deb8a1b9b8009687f4b`,
  matching the audio-extras RECOVERY baseline. The lowercase
  `/dev/block/by-name/recovery` link resolves to that partition.
- Hyprland is running. The internal DSI panel reports connected/enabled at
  1080×2340 and exposes 60/120 Hz modes. The `sec_touchscreen` input device
  and 11 evdev nodes exist, but physical finger-to-screen interaction was
  not verified remotely.
- `wlan0` is up and a WLAN-bound HTTPS request returned HTTP 200. `tailscaled`
  is running and `tailscale0` exists, but its current `operstate` is `unknown`;
  tailnet peer reachability was not retested in this refresh.
- Local model port 8089 returned health HTTP 200. The prior accepted service
  configuration remains CPU-only (`-ngl 0`); this probe did not benchmark it.
- Battery reports 100%/Full at 27.8 °C while connected to power; sampled
  thermal zones were 32 °C. `MemAvailable` was about 2.74 GiB. The CACHE-backed
  root has about 34 MiB free (94% used); userdata has about 99.98 GiB free.
  Bluetooth sysfs remains empty.

### Loaded-module reconciliation

The earlier claim that the current root reports zero loaded modules is
superseded: this live refresh counted **325 entries in `/proc/modules`**,
including WLAN, ASoC/audio, charger, NFC and touchscreen modules. The
conventional `/lib/modules` tree and tested vendor module directories were
not present in this root, so the live count proves modules are loaded but does
not yet map their complete boot-time provenance. Do not use missing
`/lib/modules` as a kernel/module compatibility argument. Next, compare the
captured live names against the exact RECOVERY/vendor_boot first-stage module
sources and bootstrap order without reading or publishing firmware payloads.

## Driver evidence and limits

| Area | Verified | Not yet verified / blocker |
| --- | --- | --- |
| Display and touch | Hyprland process, connected DSI connector, backlight, touchscreen input device and evdev nodes | No physical touch gesture was observed; keyboard and full touch UX need on-device acceptance |
| Wi-Fi / remote access | `wlan0` HTTPS returned 200; `tailscaled` and `tailscale0` exist; earlier reboot-persistent tailnet SSH checks | `tailscale0` currently reports `operstate=unknown`, so current peer reachability was not established; long-duration roaming and cold-power-on behavior |
| GPU | Samsung Vulkan HAL headless compute; llama.cpp Vulkan backend operations; separate Qwen3.5-0.8B Q4_0 run with 25/25 layers offloaded and CPU-matching text | The resident 4B service remains CPU-only. Native RADV arithmetic shader, compositor acceleration, and sustained thermals remain unaccepted. The 0.8B model bytes are absent from this host, so its recorded hash could not be recomputed in the latest manifest audit. |
| Bluetooth | QCA6490 firmware transfer/configuration acknowledgements and HCI reset/version readback | The first Linux raw-HCI socket attempt hit a kernel NULL-socket panic. A socket-lifecycle repair now has source tests, a full ThinLTO kernel build, and a RECOVERY candidate image, but has not been flashed or runtime-tested. `/sys/class/bluetooth` is currently empty. |
| Audio | Rainbow-Prince card/control registration and tested route preparation | RDMA status remains disabled during the prior stream attempt (`hw_ptr=0`); no physical speaker or microphone acceptance. A synchronized read-only DAPM/RDMA capture during one bounded zero stream is pending approval. |
| NPU | `/dev/vertex10` open/close and ENN library loading; host-only preflight now separates artifact audit from readiness and always refuses BOOTUP authorization | No firmware BOOTUP or inference. POWER_NOTIFY can wait indefinitely; request/session lifetime and bootup unwind are unsafe, so no live BOOTUP is justified. Exact source ownership repair and lifecycle regression tests remain pending. |
| Modem / SIM | Samsung CPIF device nodes exist; prior isolated RIL-library loading worked | Modem links are down; no SIM registration, mobile data, SMS, or calls. |
| Sensors | Accelerometer/gyro and magnetometer/light frames were sampled in earlier trials | Physical orientation/light acceptance, calibration and auto-rotation integration remain open. |
| Camera / suspend | No accepted functional test | Camera capture and suspend/resume remain open. |

## Bluetooth candidate build

The candidate uses the pinned vendor kernel source at `4e5c5ad7d950`. It
restores the disabled HCI socket lifecycle and keeps the existing capability
checks. Source-contract tests pass. The full kernel was rebuilt with the exact
captured config (`a147841a53f5b10c366a759d0e83525996a0ec5d8227a103b020cf2111400f9e`),
Android Clang 21, ThinLTO, CFI/KCFI, MODVERSIONS and shadow-call-stack. The
resulting arm64 `Image` SHA-256 is
`9a694c093fd24031a7ece26741739ba52cfc6ff54651605533309ff9a5a9c1d6`.

The host-only packer produced
`builds/bt-hci-socket-restore-image-20260923/recovery.img`, SHA-256
`6d7e2a4adefa32a87b4b47bf5eea59ba79b71e169dff8328acbcd3b68e06ae01`.
It is exactly 100,663,296 bytes; AVB footer verification passed. Header
version 2 and OS/patch metadata match the base image; ramdisk, DTB and
recovery-DTBO payloads are byte-identical. The candidate kernel release
includes a `-dirty` source-state suffix. The current root has no conventional
`/lib/modules` directory but does have 325 loaded modules supplied through
boot-time paths; any future external-module deployment must trace the actual
loader/source closure and resolve release identity rather than infer
compatibility from that directory's absence.

This image has **not** been flashed. The phone still runs the prior
audio-extras RECOVERY image. Candidate build success is not live HCI or pairing
acceptance. The source change is in
`tools/hardware/bt-hci-socket-restore.patch`; the contract test accepts an
explicit `--source` path to the patched kernel's `net/bluetooth/hci_sock.c`.

## GPU manifest audit

The read-only audit matched all 92 headless Vulkan manifest entries and all 3
llama Vulkan binary-artifact entries. The llama runtime manifest matched 99
of 100 entries; the absent item is the 0.8B GGUF, whose recorded SHA-256 could
not be recomputed because its bytes are not present on the host. Private raw
trial captures were not copied or published.

## Next gates

1. Keep the HCI candidate isolated until its RECOVERY-only deployment and
   rollback path are explicitly chosen; then check actual boot mode and logs,
   not the splash screen, before attempting a single bounded raw-socket test.
2. If approved, run the one-shot audio diagnostic with amps off and no route,
   gain, or control writes; capture DAPM and RDMA state during the bounded
   stream.
3. Finish an NPU request-lifetime/close-race design and exact-source tests
   before any firmware BOOTUP experiment.
4. Treat SIM/calls, camera, physical touch, audio output, and suspend as
   separate acceptance tests; kernel enumeration alone is not success.

The host `samloader` CLI has no recovery-target selector, but the native
`s22-reboot recovery` path is independently verified from the working Linux
session (BORE 762–767); the current RECOVERY image also hashes correctly.
That is a usable in-session return path, not independent rescue if a new
kernel prevents SSH/PID 1 from reaching the reboot helper. The HCI candidate
therefore remains unflashed while the phone is unattended; candidate
deployment still needs the mission's exact authorization and failure-rescue
gate, not another claim that no software recovery reboot exists.
