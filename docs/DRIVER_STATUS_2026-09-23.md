# Native S22 driver status — 2026-09-23

The phone is currently running the native Alpine/Arch/Hyprland environment in
RECOVERY mode. This is a usable remote Linux session, not yet a complete phone
replacement: several everyday radios and audio paths are still unaccepted.

## Current live checkpoint

- BORE 767, boot ID `3ee56147-4ae3-46ac-aed0-091311b72d6b`; no reboot or
  partition write was made during this checkpoint.
- Hyprland is running. The internal DSI panel reports connected/enabled at
  1080×2340 and exposes 60/120 Hz modes. The `sec_touchscreen` input device
  and evdev nodes exist, but physical finger-to-screen interaction was not
  verified remotely.
- Wi-Fi is associated on `wlan0`; an HTTPS request returned HTTP 200. The
  `tailscale0` interface is up. Battery reports 100%/Full while connected to
  external power.
- The local Qwen3.5-4B server's `/health` endpoint returned HTTP 200. Its
  running command uses `-ngl 0`, so this resident 4B service is CPU-only.

## Driver evidence and limits

| Area | Verified | Not yet verified / blocker |
| --- | --- | --- |
| Display and touch | Hyprland process, connected DSI connector, backlight, touchscreen input device and evdev nodes | No physical touch gesture was observed; keyboard and full touch UX need on-device acceptance |
| Wi-Fi / remote access | Current Wi-Fi association and HTTPS; Tailscale interface up; earlier reboot-persistent tailnet SSH checks | Long-duration roaming and cold-power-on behavior |
| GPU | Samsung Vulkan HAL headless compute; llama.cpp Vulkan backend operations; separate Qwen3.5-0.8B Q4_0 run with 25/25 layers offloaded and CPU-matching text | The resident 4B service remains CPU-only. Native RADV arithmetic shader, compositor acceleration, and sustained thermals remain unaccepted. The 0.8B model bytes are absent from this host, so its recorded hash could not be recomputed in the latest manifest audit. |
| Bluetooth | QCA6490 firmware transfer/configuration acknowledgements and HCI reset/version readback | The first Linux raw-HCI socket attempt hit a kernel NULL-socket panic. A socket-lifecycle repair now has source tests, a full ThinLTO kernel build, and a RECOVERY candidate image, but has not been flashed or runtime-tested. `/sys/class/bluetooth` is currently empty. |
| Audio | Rainbow-Prince card/control registration and tested route preparation | RDMA status remains disabled during the prior stream attempt (`hw_ptr=0`); no physical speaker or microphone acceptance. A synchronized read-only DAPM/RDMA capture during one bounded zero stream is pending approval. |
| NPU | `/dev/vertex10` open/close and ENN library loading | No firmware BOOTUP or inference. POWER_NOTIFY can wait indefinitely; request/session lifetime and bootup unwind are unsafe, so no live BOOTUP is justified. |
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
includes a `-dirty` source-state suffix; the current root has no
`/lib/modules` directory and reports zero loaded modules, but any future
external-module deployment must resolve that release-string mismatch first.

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

The local `samloader` CLI exposes `reboot-download` and flash, but no explicit
software `reboot-recovery` action. Its `--no-reboot` flag only suppresses the
post-flash reboot; it does not select a recovery boot target. Since the phone
is unattended, the candidate remains staged until a reliable post-flash return
to RECOVERY is available.
