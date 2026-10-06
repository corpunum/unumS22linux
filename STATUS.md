# S22 native Linux status

Updated 2026-10-06 by the Claude lead session (directly on the device).
The 2026-10-02 task board and earlier checkpoints below are historical.

## Current checkpoint — 2026-10-06

**Speaker audio and text-to-speech work on the device.** The new
`s22-say "text"` (espeak-ng → ALSA RDMA6 → stock `media-speaker` route →
CS35L41 amps with the speaker-protection DSP running) speaks through the
loudspeakers. Device evidence: the amps' protection telemetry recorded
speaker excursion and coil temperature, and an on-device mic loopback picked
up a test tone and the spoken phrase. The fixes were boot-time staging of the
missing stock audio firmware (ABOX `sectiongraph_tplg.bin`, amp and haptic
DSP images) plus the stock HAL mixer defaults generated from the vendor
`mixer_paths.xml`. See
[the evidence](evidence/audio-buttons-20261006/README.md).

**Physical buttons: volume keys accepted by the owner's physical test; Power reworked.** `s22-buttons`
autostarts and logs every Power/Volume press to `/srv/s22/buttons/events.jsonl`.
Volume keys step the speaker volume and announce the level. Holding Volume Up
runs the `assistant` hook (agent push-to-talk). A short Power press runs `s22-display toggle`. Off is DPMS off: the kernel
log shows panel sleep-in and the panel regulators switched off; touch is
disabled while off. On is followed by two forced full repaints. Hyprland now
uses synchronous llvmpipe (`LP_NUM_THREADS=0`) because half-drawn frames
reached the command-mode panel. The owner's physical re-test of Power is pending.

**Microphone and earpiece (later the same day):** `s22-rec SECONDS OUT.wav`
records the main mic. The loudspeaker and earpiece are verified end to end:
the phone's own mic recording of `s22-say` output, transcribed with
whisper.cpp, gives the spoken sentence back word for word (the earpiece run
had one misheard word).

**Neural on-device TTS.** `s22-say` now defaults to Supertonic (int8,
sherpa-onnx, CPU, real-time factor 0.16), with Kitten, Piper and Kokoro
available and espeak-ng as the fallback. Verified acoustically: the phone's
mic recording of its own speaker was transcribed by whisper word for word.
[Evidence](evidence/tts-20261006/README.md).

**OpenUnum on the phone, using the rig's Halogen over Tailscale.** The
server and WebUI run in the Arch chroot (127.0.0.1:18880, `s22-openunum
start`). The model is the rig's Halogen at 100.76.5.104:8080, and speech
stays local. Device-verified agent turn: the rig model ran shell tools on the
phone, then `s22-say` spoke "CPU architecture is aarch64 and the modem state
is ONLINE." [Evidence](evidence/openunum-phone-20261006/README.md).

**Bluetooth discovery works (bounded).** The running kernel has the raw-HCI
socket fix (build b2dda820). `s22-bt-scan` brings up the QCA6490 for the
bridge's 20 s window (pinned to the X2 core, which fixed a timing-sensitive
baud switch), confirms BT 5.3, Qualcomm, and finds 6 LE + 2 classic devices
with a passive LE scan and an inquiry. The chip is then powered off with
Wi-Fi unaffected. Pairing and a persistent controller come next.
[Evidence](evidence/bt-scan-20261006/README.md).

**Modem: AP-side init complete, identity/SIM/signal readable (no SIM inserted).**
The CP was looping on PHONE_START because cpif only sends INIT_END once
`umts_ipc0` and `umts_rfs0` are both open. The new `s22-modem` (a minimal
Samsung SIPC client) opens them and queries the CP. Device results: modem SW
`S901BXXSIFYI3`, a Luhn-valid IMEI (masked in the evidence), SIM
`CARD_NOT_PRESENT`, and LTE limited service (`reg_status denied`, RSRP −95 dBm,
SNR 30 dB). The CP stayed ONLINE with no crash. Bring-up is manual
(`s22-modem-up`, no boot hook). CP→AP RFS file writes are logged but not
served. Data, voice and SMS need a SIM, and calls/SMS need the owner's go-ahead.
[Evidence](evidence/modem-20261006/README.md).

Still open: the firmware-graph DMAs (RDMA2/3, most WDMAs) do not advance, so
only the hardware-direct RDMA6/9 playback and WDMA4 capture work. Cameras, NPU and GPU are unchanged
from the checkpoints below. A runtime rebind of `0.abox-tplg` panics the
kernel (recovered automatically); do not repeat it. RECOVERY image unchanged
(`b1041271…`); no partition writes.


## Historical checkpoint header — 2026-09-27

Updated 2026-09-27 from coordinator hardware trials and independent host review.
The current work is tracked in [the September 27 task board](docs/S22_LUNA_TASK_BOARD_2026-09-27.md).
Earlier checkpoints below are historical.

## Historical checkpoint — 2026-09-27

The same HCI-only RECOVERY kernel remains running; no new flash, reboot or raw
HCI socket-test repetition occurred. The new plain-H4 helper fixed the observed
firmware-profile/IBS mismatch: real hci0 registration, 41 commands/41 events,
empty queue, successful detach/power-off and preservation of WLAN were verified
in one bounded trial. Native guardian, desktop/Pi/model, networking and the full
RECOVERY image/build identity passed postflight. The controller was deliberately
detached afterward; a permanent service, pairing and Bluetooth audio are not
accepted. [Sanitized receipts](evidence/s22-hardware-continuation-20260927.json)
preserve the earlier staging refusal and failed IBS-mode trial separately.

Audio's missing exact native PCM node was repaired for this boot. PREPARE and
buffer writes now work, but 19 paired RUNNING samples show no DMA/pointer
advance. The timed diagnostic restored selectors/mute and closed the PCM;
usable sound and persistent node creation remain unfinished. Camera firmware
was recovered privately on the host, and a reviewed LDO error-unwind patch has
host C tests but no build/deployment/capture. A second reviewed camera patch
checks runtime-PM failures and preserves shared owners, with portable host C
regressions. The attempted NPU generic unwind
was rejected and removed after review found shared-STM side effects. NPU BOOTUP
remains refused and its underlying ownership problem unfixed; extracted vendor
callbacks now reproduce its reference/STM/concurrency failures, and missing
source functions cannot incorrectly satisfy the intermediate unwind gate.
Cellular and the
physical/sustained acceptance items below remain unaccepted.

The root overlay remains nearly full; substantial staging stayed on `/srv/s22`,
and logs/firmware stayed private on the rig. Independent hardware rescue is
still unproven. The dirty original checkout and master were preserved.

## Historical checkpoint — 2026-09-26

The SM-S901B/DS r0s remains on the HCI-only native RECOVERY candidate, running
GNU build ID `b2dda820b18d410d9bf12f1bd2584567d545991d`. The completed
[second trial](evidence/s22-hci-trial-second-20260924.json) verified the full
RECOVERY hash, changed boot identity, and one successful raw-HCI socket
create/close. The candidate was not rolled back. This is socket-lifecycle
acceptance; controller registration, radio operation and pairing remain untested.

At approximately 50 hours uptime, the native guardian, 325 loaded modules,
Hyprland, desktop Pi, model API/idleness, browser service and networking were
healthy. The dedicated browser tmux session remained absent on demand. The
available kernel-log ring had no fatal indicators or hung-task warnings;
full-boot log coverage and TrustZone progress are not established.

Audio playback DMA and physical output/input remain unaccepted. NPU BOOTUP
remains disabled pending ownership/liveness and hardware prerequisites.
The modem is still in `INIT`; SIM/data/voice are not accepted. Camera nodes
enumerate but no captured frame is accepted. Physical touch/sensors, suspend,
desktop GPU acceleration and normal cold boot still need their separate tests.
The existing GPU compute result is historical bounded headless evidence; the
resident 4B remains CPU-configured.

The root overlay has about 35 MB free; `/srv/s22` and Arch share a persistent
filesystem with about 102 GB free. No cleanup, package install, flash, reboot,
raw-HCI retry, controller attachment, audio stream or NPU operation occurred in
the September 26 checks. Independent hardware rescue remains unproven.

## Historical checkpoint — 2026-09-23, BORE 767 (superseded)

- Live device tree identifies Samsung R0S / S5E9925 (SM-S901B/DS, Exynos
  2200). Native kernel `5.10.260-g4e5c5ad7d950`, guardian PID 1, RECOVERY
  mode. The live RECOVERY partition is exactly 100,663,296 bytes and hashes
  to the accepted audio-extras baseline
  `758fc9d30491e17b7c829a89d338ba69476efa15a1280deb8a1b9b8009687f4b`.
- Hyprland and the internal 1080×2340 DSI panel are running; 60/120 Hz modes
  are exposed. Touch and 11 evdev nodes enumerate, but physical finger input
  has not been accepted.
- WLAN-bound HTTPS returned 200. `tailscaled` is active, but the current
  `tailscale0` operstate is unknown and this check did not prove peer reachability.
- The local model API returned HTTP 200. The resident Qwen3.5-4B remains
  CPU-only; Samsung GPU compute has separate bounded test evidence, not
  accelerated Hyprland or resident-4B acceptance.
- The live kernel has 325 loaded modules despite lacking a conventional
  `/lib/modules` directory. Their boot-time source/loader closure is not yet
  fully mapped.
- Audio controls and route preparation work; measured RDMA2/hardware pointer
  progress remained zero. No speaker/microphone acceptance. Bluetooth
  firmware/configuration transport was acknowledged, but the HCI kernel
  candidate is unflashed and `/sys/class/bluetooth` is empty. NPU inference,
  SIM/data/calls, cameras, suspend and physical touch remain unaccepted.
- CACHE-backed `/` has about 34 MiB free; do not add packages or bulk files
  there. The persistent userdata filesystem has about 99.98 GiB free.

No reboot or partition write occurred during this refresh. The current
readiness and remaining gates are tracked in the driver ledger, not inferred
from this summary.

## Historical checkpoint — 2026-09-20 (superseded)

Native RECOVERY **BORE760** is running, USB SSH is reachable, and the resident
CPU model reports `ok`. Two intentional recovery-target software reboots
(BORE759/760) automatically restored the desktop/model and Wi-Fi. Association
and service startup completed around39s; separate Wi-Fi DNS/HTTPS checks passed
after more than60s uninterrupted uptime on both. No physical intervention,
image write or normal-BOOT selection was needed. This does not establish normal
cold-power-on Linux or guarantee recovery from every possible driver crash.

- Display stride marker, Omarchy battery/keyboard configuration and all desktop
  components persisted. Hyprland configerrors is empty; battery32.1C at the
  latest sample. Not a physical finger or battery-life test.
- Native power-key binding is installed. Controlled compositor DPMS passed
  on/off/on; physical-button delivery is not yet accepted.
- A persistent **Keyboard** bar button was added after the reboot. A synthetic
  tap through `sec_touchscreen` revealed Squeekboard; no physical finger claim.
  Its configuration now survives the recovery reboot tests.
- Wi-Fi is **connected and internet-tested**. The corrected single activation
  completed calibration; an exact optional-firmware response fixed startup
  waits. WPA2/CCMP, DHCP, native/Arch DNS, and WLAN-forced DNS/HTTPS passed.
  USB routes/SSH and model health remain intact. Credentials/tools persist
  privately on userdata; optional Wi-Fi autostart is enabled and verified
  across two recovery reboots. Failed/incomplete startup inhibits retry.
- ABOX core firmware started during BORE757, but no speaker playback path was
  obtained. Those temporary firmware binds disappeared at reboot; current
  BORE758 has no ALSA soundcards. Bluetooth has no HCI controller.
- Signed Alpine radio/audio tools are installed; they do not establish working
  hardware. CACHE overlay has34,088KiB free; keep bulk assets on userdata.

Evidence: [final live state](evidence/hardware-20260920/final-live-state.txt),
[keyboard screen](evidence/hardware-20260920/keyboard-after.png),
[hardware audit](docs/EVERYDAY_HARDWARE_2026-09-20.md), and
[Wi-Fi history](docs/WIFI_NATIVE.md), plus
[live Wi-Fi acceptance](evidence/wifi-connected-20260920/acceptance.json).
[Recovery-boot Wi-Fi evidence](docs/WIFI_AUTOSTART.md) supersedes that manual
connection checkpoint. No partition was written during this
daily-hardware round. Preserve the working recovery environment.

## Earlier boot-attempt history (superseded snapshots)

Latest 18:18UTC: owner confirms the physical screen is clean. Workaround startup
configuration is now persistent, with backup/hash readback; no new reboot was
performed. BORE757 remains up nearly five hours with healthy model and USB
internet. Wi-Fi/Bluetooth are not working; audio, cameras, cellular and suspend
are not accepted. See `docs/EVERYDAY_HARDWARE_2026-09-20.md` for the bounded
live audit and next steps. Normal-power-on Linux remains unfinished.

At13:22UTC, native RECOVERY BORE757 returned via physical key selection.
Whole-partition readback verifies the original BOOT restoration and unchanged
RECOVERY/vendor_boot. Persistent desktop/model autostart succeeded; model
health `ok`. The owner reports physical display artifacts while framebuffer
capture is clean. A source-backed, opt-in row-stride workaround is running
in a temporary desktop session; physical correction is not yet confirmed.
See `tools/omarchy-trial/s22-linear-stride.md`. Do not normal-boot Android.

Earlier rollback staging (superseded by the live readback above):

At13:16–13:20UTC, Download USB became reachable. The original 64MiB BOOT
backup (SHA256 `0962dbdd67b748858189b46d464820ec7d1f3ea843cc7c3d033b69c40931b44e`)
was restored with `--no-reboot`; Odin acknowledged all transfers and the
tool exited0. Device-side readback is pending. The phone remains in Download
Mode. Enter unchanged native RECOVERY next, not the restored Android BOOT;
Linux userdata/CACHE were not touched. No corrected candidate was flashed.

Earlier failed attempt:

At approximately 07:48 UTC, BOOT was written and read back with SHA256
`4aeb801486e35e2c71dab0e988c6ac14802b05a29d062ddd93834e74802b5b2e`.
RECOVERY (`1a827b43d29141efb47f530902dd4e4ee2b6d780515893c9ecd676ad27efd7d1`)
and vendor_boot (`383b6f6789e655b070929914db5639e6d9bacc78823ada7bf553962b5b4888be`)
were unchanged. The 07:48:18 UTC normal reboot had no SSH, ADB, or
Download-USB return by 07:56 UTC. Boot mode/runtime are unconfirmed; do not
claim a usable normal boot. Last accepted state is BORE519 recovery with
persistent Omarchy/model startup, readiness at 15.81 seconds and stable at
77 seconds.

## Persistent platform baseline (first accepted at BORE519)

- SM-S901B/DS (`r0s`), Exynos 2200; unlocked S901BXXSIFYI3 bootloader.
- Native Alpine 3.24.2 ARM64, guardian PID1, kernel 5.10.260-g4e5c5ad7d950.
  AOSP first-stage bootstrap is retained; Android services do not run.
- RECOVERY V3 image, whole-partition SHA256 verified:
  `1a827b43d29141efb47f530902dd4e4ee2b6d780515893c9ecd676ad27efd7d1`.
- CACHE-backed Alpine package/file persistence, USB Ethernet/SSH and fallback
  Weston desktop; targeted software recovery reboot verified through BORE 519.
- Persistent Arch ARM, Hyprland 0.56.2, Omarchy v4.0.4 Quickshell bar, foot,
  Squeekboard. Internal 1080x2340 display, llvmpipe software rendering.
- Userdata ext4 holds desktop/runtime/model; all started automatically after
  recovery reboot with no host restaging. 349 registered packages; about
  100 GiB free storage and 4.7 GiB available RAM with model/UI running.
- Synthetic touchscreen → keyboard → local chat path; this is not a physical
  finger-sensing test.
- Resident Qwen3.5-2B Q4_0 CPU server, 4096 context, one slot, four fast cores,
  loopback 127.0.0.1:8089. Short first-text latency 0.4–0.7 seconds.

## Not finished

- Normal-power-on Linux is unfinished. The failed BOOT candidate was replaced
  by the original Samsung BOOT; Linux currently runs from RECOVERY.
- GPU compute still faults despite a validated CPU-mapping lifecycle fix.
- NPU runtime/firmware integration and inference are unproven.
- Wi-Fi with physically unplugged USB, physical finger sensing, Bluetooth, cellular, audio,
  cameras, suspend and daily use have not passed acceptance.
- The normal Omarchy installer was not run. Native signed pacman transactions
  reproduce a signature-helper hang; the clean root and signed dependency
  delta were installed on the host and transferred with their package records.
  Runtime/package checks work; native package installation is not accepted.

## Migration and next steps

The owner approved userdata conversion, accepted possible Android-data loss,
and then approved BOOT work. Private raw backups of 15 selected partitions
are checksum-verified; personal-file restoration remains unproven. Userdata
conversion and recovery autostart are complete; see the
[migration record](docs/PERSISTENCE_MIGRATION_2026-09-20.md).
The experimental BOOT has been replaced by the original backup, now verified
by device readback. BORE confirms236 normal boot entries between07:48 and13:14;
pstore is empty and last_kmsg is corrupted, so the precise phone-side failure
is still unproven. Native RECOVERY760 is now running. The physical display fix
was accepted by the owner on BORE757; its persistent configuration and clean
framebuffer are verified after the later BORE758 panic reboot.
Do not normal-boot Android or wipe userdata. Further experimental normal
boots need physical rescue availability and are not part of this rollback.

Next Wi-Fi work is unplugged/roaming/suspend testing, not more activation of
the running driver. Recovery-boot integration is now tested. The exact
missing optional QDSS response must be available before module insertion;
do not repeat the failed insertion-and-wait sequence. No EFS write or guessed
calibration was required. Audio needs real machine-card/topology registration; Bluetooth needs
a reviewed QCA6490 UART/firmware path. GPU/NPU inference, cameras, cellular and
suspend remain unfinished. This is an experimental handheld, not a daily phone.

## Safety and rollback

BOOT work now has explicit owner approval, with RECOVERY retained as rescue.
Do not reformat installed userdata or CACHE, or modify MISC, PIT, EFS, IMEI,
bootloader or TrustZone. The historical MISC/BCB integrity
failure is documented in the experiment log, not a procedure to repeat.

Known-good Lineage recovery: `lineage/build-20260915/recovery.img`, SHA256
`b5bf01c4a47091eb95078fc69b133b44c2b453b31c23433594c5b605e3747b55`.
It is a local-only artifact. See [NATIVE_LINUX.md](docs/NATIVE_LINUX.md) for
the verified connection, reboot and rollback procedures.
