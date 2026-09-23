# unumS22linux

Native Linux experiments on the **Samsung Galaxy S22 SM-S901B/DS**,
Exynos 2200, codename `r0s`, unlocked bootloader. This is the S22, not S22+.

## Current result — 2026-09-23

**Latest measured checkpoint:** BORE767 is still running native Linux with
Hyprland, the 1080×2340 DSI panel, touchscreen input nodes, Wi-Fi/HTTPS,
Tailscale and a healthy local 4B server. The resident 4B remains CPU-only. A
Bluetooth HCI socket repair passed source checks and a full ThinLTO kernel
build; its RECOVERY image is packaged and AVB-verified, but has not been
flashed or claimed as working Bluetooth. Audio DMA and NPU remain blocked;
SIM, camera, physical touch and suspend are not accepted. See the
[measured driver status and exact next gates](docs/DRIVER_STATUS_2026-09-23.md).

**Latest: audio control access now survives reboot, and Bluetooth configuration
transfer is acknowledged.** BORE765 restored the desktop/4B model with20 extra
audio firmware files and18 new codec controls. Arch sees only the ALSA control
node automatically (1,754 controls); the latest Wi-Fi check passed13/13.
Audio streaming still stalls. Bluetooth accepted the full RAM patch plus all29 configuration
packets, then was powered down. A usable Bluetooth address, HCI/pairing,
speaker/microphone, NPU inference and cellular remain unfinished.
See the [latest measured checkpoint](docs/DRIVER_CHECKPOINT_2026-09-22.md),
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
See [measured results, rollback and limits](docs/RUNTIME_AUDIO_RECOVERY_2026-09-22.md).
The [latest continuation](docs/DRIVER_LOOP_CONTINUATION_2026-09-22.md) records
the audio DMA stall, fresh GPU shader pass, Bluetooth firmware transfer and host kernel build.

**Private browser access to Pi now works over Tailscale.** The actual agent
uses the existing local4B default, saves separate web history, and continues
after closing the tab through native musl tmux. Real model replies and
reconnects passed; optional startup was observed after recovery reboot BORE762.
See [browser access and limitations](docs/PI_WEB_TAILSCALE_2026-09-21.md).

**Native Linux + Omarchy UI + Pi on a Samsung S22.** Pi now launches through
Omarchy's agent integration with a local Qwen3.5-4B CPU model and an optional
larger Qwen provider over Tailscale. Pi/Qwen are helping investigate GPU/NPU
drivers, including an offline trace checker and supervised evidence review;
**resident 4B model inference and desktop 3D remain CPU/software-rendered**.
Recovery reboot BORE761
restored Pi,4B, Wi-Fi and Tailscale without buttons. Driver contributors welcome!
See the [Pi/agent milestone and limitations](docs/PI_AGENT_STATUS_2026-09-21.md)
and [publication/privacy review](docs/PUBLICATION.md). This is an experimental
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
partition write was needed. See the [OpenCL evidence](docs/research/GPU_OPENCL_WORKING_2026-09-21.md)
and [Vulkan evidence](docs/research/GPU_VULKAN_WORKING_2026-09-21.md).

**Qwen3.5-0.8B Q4_0 also runs on the GPU:** all 25 layers offloaded, six selected
llama.cpp matrix tests passed against CPU references, and a deterministic
24-token continuation exactly matched the CPU output. A short warmed benchmark
(three repeats, four CPU threads, prompt/generation 32 tokens) measured GPU
322.72 prompt / 38.20 decode tokens/s versus CPU-only 212.28 / 31.59.
Cold-start and real completion timings differ; this is not a sustained-speed
claim or a benchmark of the resident 4B model. See [model evidence](docs/research/GPU_LLAMA_VULKAN_2026-09-21.md).

**Hyprland is still software-rendered and the resident 4B service still uses CPU.** The
separate experimental native RADV path has working transfers/fences but its
arithmetic shaders still fail; Samsung driver reuse does not fix RADV. See
[GPU repair evidence and limitations](docs/GPU_SUBMISSION_2026-09-21.md) and
[shader diagnostics](docs/GPU_SHADER_DIAGNOSTICS_2026-09-21.md).

**Earlier Wi-Fi recovery-boot acceptance (2026-09-20 20:41 UTC):** native Linux was running in RECOVERY
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
| Audio DSP | Card and1754 controls work after20extra firmware files; control-only Arch exposure reboot-tested at BORE765. RDMA still does not advance; speaker/microphone unfinished |
| Modem | Dependencies recovered; real Samsung RIL library loads in isolated phone runtime without a RIL call. CPIF remains INIT; SIM/data/calls not working |
| Bluetooth | Full195848-byte RAM patch and7023-byte configuration (29ACKs) accepted at3M; WLAN preserved. Diagnostic address is zero; no HCI/reset/pairing/RF acceptance |
| Connectivity | USB rescue retained; Wi-Fi association, DHCP, DNS and HTTPS passed after two automatic recovery-boot startups |
| Other everyday hardware | Usable audio, cellular, camera, GPS and suspend remain unaccepted |

Previous [hardware follow-up and evidence](docs/HARDWARE_FOLLOWUP_2026-09-21.md):
Bluetooth transport, additional sensor frames, isolated RIL loading, and13/13
post-experiment Wi-Fi checks. No reboot or partition write in this round.

New hardware evidence: [sensor hub and real motion samples](docs/research/SENSORHUB_WORKING_2026-09-21.md),
[NPU runtime/graph boundary](docs/research/NPU_REUSE_NEXT_2026-09-21.md),
[Bluetooth transport preflight](docs/research/HARDWARE_REUSE_BT_PREFLIGHT_2026-09-21.md),
and [audio/cellular prerequisites](docs/research/AUDIO_CELLULAR_PREFLIGHT_2026-09-21.md).

Short resident chat tests started streaming in 0.4–0.7 seconds. Those samples
are not sustained agent benchmarks. The chat client does not execute commands.

**This is not a complete Omarchy distribution or a daily-driver phone.** The
Arch desktop, runtime and model now persist on userdata and start automatically
on recovery boot, without host restoration. Normal cold-power-on routing is
unconfirmed after the BOOT attempt. Native signed
package installation remains unaccepted; the close_range workaround is currently
Pi-scoped, not a system-wide kernel repair. Host-verified package deployment
works. See [the migration record](docs/PERSISTENCE_MIGRATION_2026-09-20.md).

## Other phones: native Linux and Omarchy feasibility

**Yes, the approach can be adapted—but this is not a universal phone image.**
Only the **SM-S901B/DS S22** has been exercised by this project. The table is
a researched candidate list, **not a supported-device list or an exhaustive
list of every Linux-capable phone**. Never flash this project's S22 images
onto another model, including an S22+ or Ultra.

Research reviewed **2026-09-21**. Device-specific links below are primary port
documentation. Some postmarketOS wiki pages could only be read through older
indexed snapshots because direct access was blocked; their component status
may be stale. The live [postmarketOS 26.06 release][pmos-release] corroborates
several maintained device families, but does not certify every component.
See [source freshness, selection criteria and porting boundaries](docs/PHONE_PORTABILITY.md).

**Screen / touch / Wi-Fi:** `Y` = reported working, `P` = partial,
`N` = reported broken, `?` = not established in the reviewed evidence.
These are upstream reports, **not our hardware tests**, except the S22 row.
“Candidate” in the last column is our engineering assessment. **No other row
has a verified Arch ARM + Hyprland + Omarchy installation from this project.**

| Phone / exact target | SoC | Native Linux evidence / route | Screen / touch / Wi-Fi | Graphics and Omarchy assessment |
| --- | --- | --- | --- | --- |
| **Galaxy S22 SM-S901B/DS** — `r0s` | Exynos 2200 | **Measured here:** vendor-kernel RECOVERY handoff to Alpine + persistent Arch userspace | Y / ? / Y; physical finger input unverified | Software-rendered Hyprland + Omarchy UI; Samsung OpenCL/Vulkan compute and Qwen0.8B GPU inference verified through isolated Bionic runtime |
| Galaxy S22+ SM-S906B — `g0s` | Exynos 2200 | **Hypothesis only:** related kernel target; separate images/bring-up required [details][s22-relatives] | ? / ? / ? | Closest porting relative, not an easier/proven GPU solution |
| Galaxy S22 Ultra SM-S908B — `b0s` | Exynos 2200 | **Hypothesis only:** related kernel target; panel/touch/pen differ [details][s22-relatives] | ? / ? / ? | Related GPU family; no transfer of S22 acceptance |
| [OnePlus 6][op6] — `enchilada` | Snapdragon 845 | Existing native pmOS port; 26.06 community | Y / Y / P | Adreno 630 3D reported working; **first-choice additional trial** |
| [OnePlus 6T][op6t] — `fajita` | Snapdragon 845 | Existing native pmOS port; 26.06 community | Y / Y / Y | 3D reported working; strong trial candidate; check carrier/unlock and audio variant |
| [POCO F1 / Pocophone F1][poco-f1] — `beryllium` | Snapdragon 845 | Existing native pmOS port; 26.06 community | Y / Y / P | 3D reported working; strong trial candidate; match EBBG/Tianma panel |
| [SHIFT6mq][shift6mq] — `axolotl` | Snapdragon 845 | Existing native pmOS/SDM845 port; 26.06 community | ? / ? / ? | Promising platform; component acceptance must be rechecked, not inferred from OP6 |
| [Fairphone 4][fp4] — `fp4` | Snapdragon 750G | Existing native pmOS port; 26.06 community | Y / Y / Y | 3D reported working; **strong trial candidate**; audio/calls/camera still need review |
| [Fairphone 5][fp5] — `fp5` | QCM6490 | Existing native pmOS port; testing in reviewed device snapshot | Y / Y / Y | 3D reported working; newer experimental target, not full phone/Omarchy acceptance |
| [Google Pixel 3a][pixel3a] — `sargo` | Snapdragon 670 | Existing native pmOS port; 26.06 community | Y / Y / P | 3D reported partial; repair/validate graphics before assuming Hyprland |
| [Google Pixel 3a XL][pixel3axl] — `bonito` | Snapdragon 670 | Existing native pmOS port; 26.06 community | Y / Y / P | 3D reported working; conditional trial, not equivalent to smaller model |
| [OnePlus 5][op5] — `cheeseburger` | Snapdragon 835 | Native/close-mainline pmOS testing port | Y / Y / Y | Adreno 540 3D reported working; desktop candidate, **not a Turnip Vulkan target** |
| [OnePlus 5T][op5t] — `dumpling` | Snapdragon 835 | Native/close-mainline pmOS testing port | Y / Y / Y | Same Adreno 540 limitation; telephony/audio/camera are not accepted |
| [Xiaomi Mi 9T / Redmi K20][mi9t] — `davinci` | Snapdragon 730 | Native generic-SM7150 route; 26.06 community | Y / Y / Y | Adreno 618 3D reported working; candidate with audio/camera/power caveats |
| [POCO X3 NFC][poco-x3] — `surya` | Snapdragon 732G | Native generic-SM7150 route; 26.06 community | Y / Y / Y | 3D reported working; touchscreen firmware and charging need attention; not X3 Pro |
| [Xiaomi Mi Mix 2S][mix2s] — `polaris` | Snapdragon 845 | Native pmOS testing port | P / P / P | 3D reported working, but display/touch/Wi-Fi problems make this a secondary target |
| [Xiaomi Mi 8][mi8-family] — `dipper` | Snapdragon 845 | Experimental native family port | Screen/touch/Wi-Fi need per-variant recheck | Driver-development target; not a usable-Omarchy recommendation |
| [Xiaomi Mi 8 Pro][mi8-family] — `equuleus` | Snapdragon 845 | Experimental native family port | Screen/touch/Wi-Fi need per-variant recheck | Separate panel/fingerprint variant; do not inherit Mi 8 results |
| [Xiaomi Mi 8 Explorer Edition][mi8-family] — `ursa` | Snapdragon 845 | Experimental native family port | Screen/touch/Wi-Fi need per-variant recheck | Separate variant; neither desktop nor model acceleration accepted |
| [Galaxy S9 SM-G9600/DS][s9-qcom] — `starqltechn` | Snapdragon 845 | Native/close-mainline port; older snapshot says testing | Y / Y / N | Interesting Samsung alternative, but snapshot Wi-Fi/BT broken; not Exynos or US S9 |
| [PINE64 PinePhone][pinephone] — `pinephone` | Allwinner A64 | Linux-first hardware; native distributions, SD/eMMC route | Y / Y / Y | Mali-400/Lima is limited; prefer a lighter mobile UI over this Omarchy target |
| [PINE64 PinePhone Pro][pinephone-pro] — `pinephonepro` | RK3399S | Linux-first hardware; native distributions, device-specific bootloader | P / Y / Y | Mali-T860/Panfrost; possible desktop trial, not a strong local-LLM/GPU-compute choice |
| [Purism Librem 5][librem5] — `librem5` | NXP i.MX8MQ | Linux-first PureOS and native pmOS port | Y / Y / Y | Vivante 3D reported working; custom desktop possible, modest CPU/RAM platform |
| [Galaxy S9 Exynos][s9-exynos] — `starlte` | Exynos 9810 | Downstream port entry only; **not qualified by this survey** | ? / ? / ? | Do not confuse with the Snapdragon S9 port; new driver assessment required |
| [Galaxy S9+ Exynos][s9plus-exynos] — `star2lte` | Exynos 9810 | Downstream port entry only; **not qualified by this survey** | ? / ? / ? | Research-only lead, not a claim of a working native desktop |

For a second development phone, our shortlist is **OnePlus 6/6T, POCO F1,
or Fairphone 4**, after verifying the exact unit's bootloader and current port
regressions. This is a lower-porting-risk assessment, not a benchmark or a
promise of daily-driver reliability. Existing native ports should use their
own maintained kernel/boot instructions; copying the S22 recovery workaround
is unnecessary and potentially destructive.

**Hyprland is not the same as full Omarchy.** Omarchy now has official ARM
initiatives for [Apple machines][omarchy-m] and [Snapdragon computers][omarchy-dragon].
Those announcements concern computers, not blanket smartphone support. The
phone work still needs compatible ARM packages, DRM/EGL, touch/keyboard setup,
power management and phone-safe installation. Phosh, Plasma Mobile or Sxmo
can be a more practical first interface; they are not Omarchy.

**Graphics support is not AI acceleration.** [Freedreno/Turnip][freedreno]
makes supported Adreno 6xx devices promising GPU-compute experiments, but a
working 3D desktop is not proof of correct or fast `llama.cpp` inference.
Adreno 5xx is not supported by Turnip. [Panfrost's API support][panfrost] also
varies by Mali generation. No GPU-model or NPU-inference result is claimed
for any additional phone in this table. Treat GPU memory as shared system RAM,
not extra dedicated VRAM, and benchmark each actual device/runtime.

**Before buying or installing:** verify the exact regional/carrier model,
real bootloader-unlock eligibility (SIM-unlocked is not enough), required
firmware, current touchscreen/panel variant, and a recoverable installation
path. Unlocking may wipe data. Keep firmware, calibration and recovery
backups; do not erase Android-related partitions just to make Linux “clean.”
Our scope is native Linux userspace without running Android services—not a
promise of an entirely open bootloader, kernel or firmware stack.

For the broader, changing inventory, consult the [postmarketOS device catalog][pmos-devices].
The [Ubuntu Touch catalog][ubports-devices] separately identifies Native,
Halium and Legacy ports; a Halium port is not evidence that this project's
Android-service-free Arch/Hyprland path will work.

[pmos-release]: https://postmarketos.org/blog/2026/06/21/v26.06-release/
[pmos-devices]: https://wiki.postmarketos.org/wiki/Devices
[s22-relatives]: docs/PHONE_PORTABILITY.md#closest-s22-relatives
[op6]: https://wiki.postmarketos.org/wiki/OnePlus_6_%28oneplus-enchilada%29
[op6t]: https://wiki.postmarketos.org/wiki/OnePlus_6T_%28oneplus-fajita%29
[poco-f1]: https://wiki.postmarketos.org/wiki/Xiaomi_POCO_F1_%28xiaomi-beryllium%29
[shift6mq]: https://wiki.postmarketos.org/wiki/SHIFT_SHIFT6mq_%28shift-axolotl%29
[fp4]: https://wiki.postmarketos.org/wiki/Fairphone_4_%28fairphone-fp4%29
[fp5]: https://wiki.postmarketos.org/wiki/Fairphone_5_%28fairphone-fp5%29
[pixel3a]: https://wiki.postmarketos.org/wiki/Google_Pixel_3a_%28google-sargo%29
[pixel3axl]: https://wiki.postmarketos.org/wiki/Google_Pixel_3a_XL_%28google-bonito%29
[op5]: https://wiki.postmarketos.org/wiki/OnePlus_5_%28oneplus-cheeseburger%29
[op5t]: https://wiki.postmarketos.org/wiki/OnePlus_5T_%28oneplus-dumpling%29
[mi9t]: https://wiki.postmarketos.org/wiki/Xiaomi_Mi_9T_/_Redmi_K20_%28xiaomi-davinci%29
[poco-x3]: https://wiki.postmarketos.org/wiki/Xiaomi_POCO_X3_NFC_%28xiaomi-surya%29
[mix2s]: https://wiki.postmarketos.org/wiki/Xiaomi_Mi_Mix_2S_%28xiaomi-polaris%29
[mi8-family]: https://wiki.postmarketos.org/wiki/Xiaomi_Mi_8_%28SDM845%29_%28xiaomi-dipper%2C_xiaomi-equuleus%2C_xiaomi-ursa%29
[s9-qcom]: https://wiki.postmarketos.org/wiki/Samsung_Galaxy_S9_%28samsung-starqltechn%29
[s9-exynos]: https://wiki.postmarketos.org/wiki/Samsung_Galaxy_S9_%28samsung-starlte%29
[s9plus-exynos]: https://wiki.postmarketos.org/wiki/Samsung_Galaxy_S9%2B_%28samsung-star2lte%29
[pinephone]: https://wiki.postmarketos.org/wiki/PINE64_PinePhone_%28pine64-pinephone%29
[pinephone-pro]: https://pine64.org/documentation/PinePhone_Pro/_full/
[librem5]: https://wiki.postmarketos.org/wiki/Purism_Librem5_%28purism-librem5%29
[omarchy-m]: https://omarchy.org/news/2026/09/introducing-omarchy-m/
[omarchy-dragon]: https://omarchy.org/news/2026/09/introducing-omarchy-dragon/
[freedreno]: https://docs.mesa3d.org/drivers/freedreno.html
[panfrost]: https://docs.mesa3d.org/drivers/panfrost.html
[ubports-devices]: https://devices.ubuntu-touch.io/

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
