# unumS22linux: history of checkpoints

Every earlier front-page checkpoint, unchanged, newest first. The current state is in the [README](../README.md) and [STATUS.md](../STATUS.md); the full log with failed tests is [EXPERIMENTS.md](../EXPERIMENTS.md).

## Checkpoint — 2026-09-27

The HCI-only native RECOVERY candidate is installed and passed one raw
Bluetooth socket create/close test. A new live trial registered controller
`hci0`; after fixing a firmware-profile/IBS mismatch, it exchanged 41 commands
and 41 events successfully, then detached and powered down with Wi-Fi preserved.
An exact missing ALSA node was repaired for this boot: playback now accepts
buffers, but synchronized samples still show no DMA progress. Desktop/Pi/model
services and networking remain healthy on the same candidate. Persistent
Bluetooth service/pairing, usable audio, NPU inference, cellular and camera
operation remain unfinished; GPU compute
and desktop acceleration have separate acceptance requirements. See
[current status](../STATUS.md), the [HCI trial receipt](../evidence/s22-hci-trial-second-20260924.json),
the [new live results](../evidence/s22-hardware-continuation-20260927.json),
and the [active driver work](S22_LUNA_TASK_BOARD_2026-09-27.md).

## Earlier results — 2026-09-23 and before

**September 23 checkpoint (superseded):** BORE767 was running native Linux with
Hyprland, the 1080×2340 DSI panel, touchscreen input nodes, Wi-Fi/HTTPS,
Tailscale and a healthy local 4B server. The resident 4B remains CPU-only. A
Bluetooth HCI socket repair passed source checks and a full ThinLTO kernel
build; its RECOVERY image is packaged and AVB-verified, but has not been
flashed or claimed as working Bluetooth. Audio DMA and NPU remain blocked;
SIM, camera, physical touch and suspend are not accepted. See the
[measured driver status and exact next gates](DRIVER_STATUS_2026-09-23.md).

**Latest: audio control access now survives reboot, and Bluetooth configuration
transfer is acknowledged.** BORE765 restored the desktop/4B model with20 extra
audio firmware files and18 new codec controls. Arch sees only the ALSA control
node automatically (1,754 controls); the latest Wi-Fi check passed13/13.
Audio streaming still stalls. Bluetooth accepted the full RAM patch plus all29 configuration
packets, then was powered down. A usable Bluetooth address, HCI/pairing,
speaker/microphone, NPU inference and cellular remain unfinished.
See the [latest measured checkpoint](DRIVER_CHECKPOINT_2026-09-22.md),
including prior failed tests and the remaining limitations.

**Audio card startup and Pi subprocess repair are now verified.** An audio-only
RECOVERY update registered Rainbow-Prince with23 playback/53 capture PCMs;
native ALSA enumerates1736 controls after the narrow control-node fix.
Arch can also enumerate the control node read-only. A two-selector audio route
now prepares, but zero-sample streaming stalls and is **not working playback**.
Both selectors were restored; speaker/microphone acceptance remains open.
A targeted close_range
workaround lets Pi's local4B run captured-output subprocess tools; the actual
tool test passed before and after reboot. BORE762 restored desktop,4B,
Wi-Fi and private Pi web without buttons. Bluetooth now acknowledges the full
195,848-byte RAM patch and answers the following board-ID query. Board
configuration, HCI registration and pairing remain unfinished.
See [measured results, rollback and limits](RUNTIME_AUDIO_RECOVERY_2026-09-22.md).
The [latest continuation](DRIVER_LOOP_CONTINUATION_2026-09-22.md) records
the audio DMA stall, fresh GPU shader pass, Bluetooth firmware transfer and host kernel build.

**Private browser access to Pi now works over Tailscale.** The actual agent
uses the existing local4B default, saves separate web history, and continues
after closing the tab through native musl tmux. Real model replies and
reconnects passed; optional startup was observed after recovery reboot BORE762.
See [browser access and limitations](PI_WEB_TAILSCALE_2026-09-21.md).

**Native Linux + Omarchy UI + Pi on a Samsung S22.** Pi now launches through
Omarchy's agent integration with a local Qwen3.5-4B CPU model and an optional
larger Qwen provider over Tailscale. Pi/Qwen are helping investigate GPU/NPU
drivers, including an offline trace checker and supervised evidence review;
**resident 4B model inference and desktop 3D remain CPU/software-rendered**.
Recovery reboot BORE761
restored Pi,4B, Wi-Fi and Tailscale without buttons. Driver contributors welcome!
See the [Pi/agent milestone and limitations](PI_AGENT_STATUS_2026-09-21.md)
and [publication/privacy review](PUBLICATION.md). This is an experimental
community port, not official Omarchy phone support or a complete distribution.

**Bounded GPU update:** Samsung's OpenCL and Vulkan drivers now execute verified
compute in an isolated Bionic compatibility runtime on native Linux. Each path passed three consecutive
fresh-process shader tests with every output verified and no new GPU faults.
Vulkan ran the original 256-word SPIR-V shader that still fails on RADV.
An initial OpenCL run returned correct results but triggered an automatic GPU
timeout/reset; its cause and cold-start reliability remain unresolved.
OpenCL reports **4 GiB shared GPU memory**, a **1 GiB maximum allocation**,
FP16 and subgroups. This is not dedicated VRAM or reserved memory.
No Android services or property server were started. No phone reboot or
partition write was needed. See the [OpenCL evidence](research/GPU_OPENCL_WORKING_2026-09-21.md)
and [Vulkan evidence](research/GPU_VULKAN_WORKING_2026-09-21.md).

**Qwen3.5-0.8B Q4_0 also runs on the GPU:** all 25 layers offloaded, six selected
llama.cpp matrix tests passed against CPU references, and a deterministic
24-token continuation exactly matched the CPU output. A short warmed benchmark
(three repeats, four CPU threads, prompt/generation 32 tokens) measured GPU
322.72 prompt / 38.20 decode tokens/s versus CPU-only 212.28 / 31.59.
Cold-start and real completion timings differ; this is not a sustained-speed
claim or a benchmark of the resident 4B model. See [model evidence](research/GPU_LLAMA_VULKAN_2026-09-21.md).

**Hyprland is still software-rendered and the resident 4B service still uses CPU.** The
separate experimental native RADV path has working transfers/fences but its
arithmetic shaders still fail; Samsung driver reuse does not fix RADV. See
[GPU repair evidence and limitations](GPU_SUBMISSION_2026-09-21.md) and
[shader diagnostics](GPU_SHADER_DIAGNOSTICS_2026-09-21.md).

**Earlier Wi-Fi recovery-boot acceptance (2026-09-20 20:41 UTC):** native Linux was running in RECOVERY
BORE760 with the persistent desktop and CPU model. **Wi-Fi now autostarts:**
WPA2 association, DHCP, DNS and TLS-verified HTTPS forced through WLAN passed.
DNS also works in both Alpine and Arch/Omarchy. USB SSH remains available.
Two consecutive recovery-target software reboots (BORE759/760) automatically
restored Wi-Fi association and services in about39s. Separate internet checks
passed after more than60s uninterrupted uptime on each. No physical action,
image flash or normal-BOOT selection was needed.
The owner previously confirmed the physical display is clean with the
[narrow display-stride workaround](../tools/omarchy-trial/s22-linear-stride.md),
which now has reboot-observed persistence. Battery telemetry, restored icons,
screen-power control, and a visible **Keyboard** button are installed. A
synthetic touchscreen tap reveals the keyboard; physical finger acceptance
remains unproven. Bluetooth/audio remain unaccepted. Wi-Fi tools and the private
profile are persisted. See [Wi-Fi recovery-boot acceptance](WIFI_AUTOSTART.md);
unplugged USB operation and suspend/resume remain untested.
See the [current screen](../evidence/hardware-20260920/keyboard-after.png) and
[everyday hardware audit](EVERYDAY_HARDWARE_2026-09-20.md).
Normal power-on Linux is still unaccepted; do not boot the restored Android
BOOT or wipe userdata.

The first persistent accepted session, BORE519, ran **Alpine Linux ARM64 from RECOVERY**,
with no Android services. Persistent **Arch Linux ARM + Hyprland + actual
Omarchy Quickshell UI**, terminal, Squeekboard and a local CPU-only
**Qwen3.5-2B** streaming chat model started automatically after reboot.
Readiness was observed at15.81s, and the stable sample passed77s uptime.

[Actual screen capture after reboot](../evidence/persistence-20260920/persistent-after-reboot-chat.png)
and [measured results](DRIVER_MODELS_2026-09-20.md).

| Component | Last verified state |
| --- | --- |
| Native boot | Alpine 3.24.2; guardian PID1; Samsung/Lineage 5.10.260 kernel |
| Persistent base | Alpine package/file overlay in existing CACHE; reboot-tested |
| Persistent desktop/model | ext4 userdata; automatic recovery startup verified with4B/Pi at BORE761 |
| Desktop | Arch ARM, Hyprland 0.56.2, Omarchy v4.0.4; software-rendered |
| Keyboard/input | Visible Squeekboard plus Keyboard bar button; synthetic tap reveal passed; physical finger sensing unverified |
| Battery/display power | Native telemetry bar/panel; controlled DPMS off/on passed; no suspend/battery-life acceptance |
| Local model | Qwen3.5-4B Q4_K_M, 4K context, four fast CPU cores; CPU-only;2B retained but stopped |
| Pi/agents | Unprivileged Pi0.86.1 through Omarchy's launcher; local4B and optional rig Qwen; supervised driver review, not autonomous repair |
| Tailscale | Enrolled; remote SSH and Pi-to-rig use verified; recovery-reboot persistence passed |
| CPU benchmark | 0.8B: 20.21 tok/s; 2B: 10.13 short / 5.47 at depth4096 |
| GPU | Samsung OpenCL/Vulkan compute passes; llama.cpp Vulkan Qwen0.8B all-layer offload and CPU-matching text verified. Resident4B remains CPU; RADV/desktop acceleration and sustained stability remain unaccepted |
| NPU | Real ENN loads; vertex10 open/close passed. BOOTUP audit found unbounded waits and unsafe error cleanup; no speculative patch or firmware-boot acceptance |
| Sensors | Accelerometer/gyro and now magnetometer/light frames sampled twice each. Compass accuracy is zero and light response untested; calibration, auto-rotation and autostart remain unaccepted |
| Audio DSP | Card/controls work; missing native PCM node repaired for this boot. PREPARE/write accepted, but 19 RUNNING samples show no RDMA/pointer progress; cleanup verified, speaker/microphone unfinished |
| Modem | Dependencies recovered; real Samsung RIL library loads in isolated phone runtime without a RIL call. CPIF remains INIT in that isolated runtime. Separately, the native `s22-phoned` path (2026-10-17, [RECON](../RECON.md)) has SIM READY and SMS ready; calls are not working and there is no call audio |
| Mobile data | Works over the modem (Vodafone GR, `internet.vodafone.gr`): `s22d` `mobile_data` `up`/`down`/`status` through phoned define and activate the PDP context and configure `rmnet0` with a metric-9000 fallback default route, so Wi-Fi/USB stay preferred. Measured on EGPRS about 10 kB/s with HTTP 200 over `rmnet0`. Reversible and audited. DNS servers are reported but not written to resolv.conf |
| LTE | The initial-attach APN frame (0x0D14, built from the open-source libsamsung-ipc fork) moved the radio from EGPRS to LTE (RSRP about -99 dBm) in the live run. `s22-phoned` now resends it at every modem start (default on; `lte-attach.json`, `--no-lte-attach`, `--lte-attach-apn`). The modem reports no band on LTE, so band is unknown |
| Clock | `s22-clock` (restore, net, save, daemon) keeps the wall clock from booting in 2021: it only moves forward from the RTC, saved time or a build floor, then syncs from an HTTP Date header (NTP fallback) before tailscaled starts |
| Voice (TTS) | `s22-say` speaks Greek text with the Piper voices `el-antonis` (default for Greek text) and `el-female`, installed by `tools/hardware/tts/greek/install-greek-voice.sh` (with rollback); `s22-touchd` uses it. English keeps its previous engines |
| Bluetooth | Real hci0 registration and 41-command/41-event plain-H4 exchange pass after IBS-profile fix; detach/power-off/WLAN preservation verified. Persistent service, pairing and Bluetooth audio unfinished |
| Connectivity | Native USB SSH and Wi-Fi work; independent hardware rescue is not yet demonstrated. Association, DHCP, DNS and HTTPS previously passed after two automatic recovery startups |
| Other everyday hardware | Usable audio, calls, camera, GPS and suspend remain unaccepted |

Previous [hardware follow-up and evidence](HARDWARE_FOLLOWUP_2026-09-21.md):
Bluetooth transport, additional sensor frames, isolated RIL loading, and13/13
post-experiment Wi-Fi checks. No reboot or partition write in this round.

New hardware evidence: [sensor hub and real motion samples](research/SENSORHUB_WORKING_2026-09-21.md),
[NPU runtime/graph boundary](research/NPU_REUSE_NEXT_2026-09-21.md),
[Bluetooth transport preflight](research/HARDWARE_REUSE_BT_PREFLIGHT_2026-09-21.md),
and [audio/cellular prerequisites](research/AUDIO_CELLULAR_PREFLIGHT_2026-09-21.md).

Short resident chat tests started streaming in 0.4–0.7 seconds. Those samples
are not sustained agent benchmarks. The chat client does not execute commands.

**This is not a complete Omarchy distribution or a daily-driver phone.** The
Arch desktop, runtime and model now persist on userdata and start automatically
on recovery boot, without host restoration. Normal cold-power-on routing is
unconfirmed after the BOOT attempt. Native signed
package installation remains unaccepted; the close_range workaround is currently
Pi-scoped, not a system-wide kernel repair. Host-verified package deployment
works. See [the migration record](PERSISTENCE_MIGRATION_2026-09-20.md).
