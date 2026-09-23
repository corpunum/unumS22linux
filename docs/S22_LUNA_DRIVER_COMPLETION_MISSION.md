# S22 native Linux — driver completion mission for Luna Max

Prepared: 2026-09-23.
Reviewed repository: `corpunum/unumS22linux`.
Reviewed commit: `548a83d2601ce49ee5ddeb670950fc395a215438` on `master`.
Target: Samsung Galaxy **S22 SM-S901B/DS, r0s, Exynos 2200 / s5e9925**.

This mission is based on a remote source/document/curated-evidence review, not a new hardware test. Refresh the local repository and live state before acting. The checkpoint below is historical once a newer measured checkpoint exists. Do not reset ongoing work to this commit.

## 1. Mission and operating rules

Continue the existing native Linux implementation toward a fully usable standalone S22. Preserve the working Samsung/Lineage-kernel + Alpine guardian + persistent Arch ARM + Hyprland/Omarchy architecture. Do not restart on mainline, reformat installed storage, replace the installation with Android-hosted Linux, or treat S22+ g0s as this device.

Implement and test, rather than ending with another broad feasibility report. Start with concrete source changes and hardware-free regressions that remove current blockers. Preserve already successful behavior. When a device experiment needs a permission or physical action not already authorized, queue that specific action and continue independent host work.

Be precise about the architecture: Linux is the running operating environment, with AOSP-derived first-stage bootstrap and selected Samsung/Bionic compatibility components. This is not an Android-hosted chroot, but neither is it a software stack with no Android-derived code. Any proposal to add Android framework/services must identify that dependency and obtain an explicit architecture decision rather than quietly changing the project goal.

One coordinator owns device mutations, firmware activation, driver ownership, deployment and reboot. Parallel workers may inspect sources, implement isolated patches, run host tests and review in separate worktrees. They must not independently access or mutate the phone. Treat GPU, audio, Bluetooth and Wi-Fi experiments as potentially sharing clocks, power, memory or failure domains. Do not overlap state-changing hardware trials.

Use existing authorized host/model routes. Keep the resident phone 4B CPU service unchanged unless an isolated experiment explicitly needs a recorded, reversible pause. Do not replace its production model/backend based on the separate 0.8B GPU result.

### 1.1 Mandatory Luna-only parallel execution

Use Luna Max as the coordinator and **actually spawn Luna-model subagents** for parallel implementation, testing and independent review. This is an execution requirement, not a request to write several role descriptions while doing everything in one agent.

- Use the same Luna model family as the coordinator. Resolve its exact available model ID from the current authorized environment and explicitly select it for each worker where the interface supports model selection. Use the requested Max/highest supported reasoning setting for difficult driver work and review; do not guess model IDs or unsupported reasoning names. Do not substitute Spark, Sol, another model family, the phone model or a local LLM.
- Inspect the available native subagent interface or installed CLI help before launching. Do not invent spawn commands or flags. Verify each worker's reported model, record its actual ID/session handle and worktree, and report any inability to verify model selection. Do not silently accept a default non-Luna worker.
- Start with four independent host-side workers when authorized concurrency and resources permit: (1) deployment/build/AVB hardening and negative tests; (2) Bluetooth HCI lifecycle/bridge repair and tests; (3) synchronized audio DMA diagnostics and cleanup tests; (4) NPU request ownership/unwind and readiness gates. Workers must read the relevant mission sections and actual sources before changing code.
- Queue additional Luna workstreams for userspace/package reliability, touch/power, GPU compute/presentation, cellular, camera/remaining hardware and acceptance/CI. Run these in subsequent waves or add workers only when resources and task independence justify it. Assign an independent Luna reviewer to candidate patches; the author must not supply the only review of their own high-risk change.
- Give each worker a bounded assignment, separate worktree/branch, explicit file ownership, dependencies, acceptance criteria and commands to run. Do not let workers race on shared status files, generated artifacts, deployment tools or a single kernel build directory. Workers hand back commits/patches and receipts; only the coordinator integrates and pushes to the shared branch.
- Keep one shared task board identifying worker ID, verified model, task, worktree, touched files, dependency, current status, tests and result commit. Reuse existing tasks instead of duplicating completed investigations. Stop or reassign workers that are looping without new evidence. Do not recursively spawn unbounded workers.
- Parallelize source analysis, small host tests and independent builds within host limits, but serialize memory-heavy full-kernel/ThinLTO builds unless measured headroom supports more. Limit build jobs for memory and temperature. Do not compete with the host's existing services or the resident phone assistant.
- **Only the coordinator may access the live phone, change hardware state, deploy, reboot or run live experiments.** Workers receive sanitized captured evidence from the coordinator. Parallel agents are not permission for parallel device trials or for bypassing an approval/rescue requirement.

If actual Luna subagent creation is unavailable, quota-limited or its model cannot be selected/verified, report the exact limitation and continue safe independent coordinator work. Do not pretend workers were spawned, silently fall back to another model, or stop all useful work because one lane is blocked. At each checkpoint report which Luna workers actually ran, what each changed/tested, and which results the coordinator independently verified.

All repository paths below are relative to the repository root. A local checkout directory named `s22-linux` is not an extra repository path prefix.

## 2. First pass: reconcile reality

Record local HEAD, remote HEAD, branch, worktree state and ongoing jobs. Preserve unrelated changes and do not force-push, hard-reset or automatically merge another worker's unfinished patch. Review commits newer than the reference above before using this mission's checkpoint.

Read first:

- `README.md`, `DEVICE.md`, `docs/DRIVER_STATUS_2026-09-23.md`.
- `docs/DRIVER_CHECKPOINT_2026-09-22.md`, `docs/DRIVER_LOOP_CONTINUATION_2026-09-22.md`.
- Relevant newer sections of `EXPERIMENTS.md`; older sections are historical, not current instructions.
- `docs/NATIVE_LINUX.md` and `STATUS.md` for architecture/boot history, while recognizing their 2026-09-20 state is superseded.
- The exact source, tests and receipts for each selected experiment.

Refresh the real device identity, actual RECOVERY boot selection, boot ID, kernel release/config, PID1 and mount namespaces, watchdog/guardian state, native and Arch filesystem space, memory availability, battery/thermal telemetry, services, and independent recovery reachability. Prefer existing bounded probes. A healthy model API is not sufficient proof of complete system health.

Capture which modules are actually loaded and which are supplied by first-stage bootstrap, ramdisk and vendor paths. A missing `/lib/modules` in one root does not establish the complete boot-time module dependency closure. Reconcile the latest report of zero loaded modules with the actual `/proc/modules`, `/sys/module`, kernel configuration and boot sources before using it as a compatibility argument.

Create or update one canonical machine-readable hardware acceptance matrix and a generated/readable `STATUS.md`. Keep historical logs intact. Mark each claim with its source commit, build identity, boot/trial identity and evidence path. Separate:

`not_examined`, `blocked`, `source_checked`, `host_tested`, `built`, `device_tested`, `physically_accepted`, `reboot_verified`, `sustained_verified`.

These are separate dimensions where appropriate, not one misleading percentage. Store current status separately from the strongest historical result. Missing receipts, missing model bytes, a changed kernel or an untested new image must not inherit old acceptance automatically.

## 3. Starting checkpoint to preserve

At the reviewed commit:

- BORE767 was running native Linux from RECOVERY, with guardian PID1, persistent Alpine/Arch, Hyprland/Omarchy, Wi-Fi/HTTPS, USB rescue, Tailscale and the resident Qwen3.5-4B CPU server.
- The internal 1080x2340 display worked; 60/120 Hz modes were exposed. Hyprland remained software-rendered. Physical touch, physical keys and refresh-rate performance were not fully accepted.
- Samsung Vulkan/OpenCL headless compute had passed bounded tests. A separate Qwen3.5-0.8B Q4_0 test used 25/25 GPU layers and matched a CPU text control. The resident 4B still used `-ngl 0`.
- The Bluetooth controller accepted matched firmware/configuration and a normal-profile reset/address/version readback. Linux HCI registration/pairing remained blocked after the raw-socket panic.
- Rainbow-Prince registered, and Arch could enumerate 1,754 controls through a control-only bind. Digital-zero playback stalled with `hw_ptr=0` and zero RDMA status. No speaker/microphone acceptance.
- NPU open/close and ENN loading did not establish firmware boot or inference. POWER_NOTIFY request lifetime and boot error unwind remained unsafe.
- Modem firmware was recovered and identified; isolated RIL library loading worked, but CPIF was not an accepted running modem. No SIM/data/SMS/calls acceptance.
- Sensor frames existed, but physical stimulus, calibration and desktop integration remained open. Camera, GNSS and suspend were unaccepted.
- Normal cold-power-on Linux was unaccepted. The original Android BOOT was restored while userdata had become Linux. Do not select that normal Android boot path.

Known native RECOVERY base at review:

`builds/audio-extra-v2-20260922/recovery.img`

SHA-256: `758fc9d30491e17b7c829a89d338ba69476efa15a1280deb8a1b9b8009687f4b`.

Staged, NOT runtime-accepted HCI candidate:

`builds/bt-hci-socket-restore-image-20260923/recovery.img`

SHA-256: `6d7e2a4adefa32a87b4b47bf5eea59ba79b71e169dff8328acbcd3b68e06ae01`.

Both are recorded as 100,663,296-byte RECOVERY images. Recompute local hashes and verify the currently installed baseline; do not assume these artifacts still exist or remain current.

## 4. Priority zero: reliable deployment and honest test gates

### 4.1 Reuse the existing in-session deployment route

The `samloader` CLI lacking a reboot-to-recovery command does not prove every deployment path is blocked. Inspect:

- `tools/hardware/deploy-audio-recovery.py`
- `tools/hardware/deploy-audio-extra-recovery.py`
- `tools/hardware/audio-recovery-reboot-once.py`
- `evidence/main-driver-loop-20260922/audio-extras.json`

The recorded audio update staged a known image and rollback copy, wrote only the identified RECOVERY block device from running Linux, verified the full readback, and separately used `s22-reboot recovery`. That route avoids entering Download Mode merely to update RECOVERY. It does NOT guarantee recovery if a newly rebuilt kernel fails before SSH.

Assess adapting this route for an HCI-only kernel candidate. Build a host-tested manifest-driven deployer rather than changing hard-coded hashes with broad text replacement. Preserve plan/stage/flash/reboot as separate explicit operations. Do not execute a flash or disruptive boot until the existing authorization covers the exact operation and an independent rescue route is available. A rollback file stored on a phone that will not boot is not, by itself, a usable recovery plan.

### 4.2 Harden real write preconditions

Existing deployers use Python `assert` for identity, size, hash, temperature and readback guards. Replace safety-critical assertions with explicit checks raising exceptions or exiting before the write. Check both the host and embedded remote implementation. Tests may use assertions; deployment authorization and device identity may not depend on them.

Add mocked regular-file/block-I/O tests under ordinary Python, `python -O` and `PYTHONOPTIMIZE=1`. Never run negative flash tests on the phone. Cover wrong device/partition identity, incorrect capacity, old-image mismatch, stale receipt, symlink/path replacement, mounted target, short write/read, interrupted staging, failed fsync, corrupt candidate, failed readback and insufficient staging space. Failures must not trigger reboot or automatic retry. Require exclusive device-operation ownership and record partial-write/unknown outcomes honestly.

### 4.3 Build provenance and verification

Inspect `tools/hardware/build-bt-hci-recovery.py`. It checks base hashes, payload/header preservation and image size, but its own AVB step reports `info_image`; make explicit `verify_image` part of the supported build path, not only an ad hoc terminal command. Add corrupt-image negative tests. `algorithm NONE` means an unsigned hash footer, not Samsung authentication or a guarantee of successful boot.

Record exact kernel source commit, patch series, config, toolchain, build command, kernel image, module set, relevant symbol CRCs and recovery payload hashes. Preserve the captured ThinLTO/CFI/MODVERSIONS/shadow-call-stack configuration unless a separately justified change is required. Do not remove hardening to obtain an easier build.

A `-dirty` suffix must not be treated as either proof of incompatibility or harmlessness. The existing notes describe matching common symbol CRCs and version-prefix behavior under MODVERSIONS. Inspect the actual loader and every boot-time module dependency. Prefer a clean, committed reproducible source state with an explicit version identity; do not merely relabel a binary to bypass validation.

## 5. Workstream A — Bluetooth: closest driver milestone

Files:

- `tools/hardware/bt-hci-socket-restore.patch`
- `tools/hardware/test-bt-hci-socket-restore.py`
- `tools/hardware/run-bt-hci-bridge-once.py`
- `tools/hardware/bt-h4-ibs-bridge.c`
- `docs/research/BT_ADDRESS_SOURCE_2026-09-22.md`
- `docs/research/BT_RUNTIME_RESET_2026-09-22.md`

The pinned original `net/bluetooth/hci_sock.c` disables socket initialization under `#if 0` while returning success. Restore the complete matching lifecycle, not a NULL check that hides uninitialized state. Review allocation, release, monitor/cookie ownership, bind/ioctl paths and capability checks against the exact base. Current string-based contract tests are useful but not dynamic lifecycle tests.

First prove source application and build provenance. Then, after an authorized boot of the candidate, verify kernel/image identity and baseline health before controller attachment. Start with the smallest raw-socket create/close test, then controlled repetitions and error/permission cases. Do not jump directly from a successful build to scanning or pairing. Retain the disabled live bridge gate until its prerequisites actually pass.

Exercise unsupported socket types, allocation/error cleanup where test infrastructure permits, invalid bind, expected privilege checks, descriptor cleanup, attach/detach, controller disappearance and repeated startup/shutdown. Host- or emulation-based tests must be labeled as such, not on-device proof.

The controller initialization path already has patch/config/reset/address/version evidence. Do not restart firmware guessing or factory-address research. Preserve the private Linux-generated identity and its provenance; do not read/write EFS or claim it is a factory address. Respect the verified initialization ordering: successful patch/configuration before the matched reset sequence.

For the H4/IBS bridge, extend host tests for fragmented H4 frames, malformed lengths, queue bounds, short/nonblocking writes, wake/ACK retries, sleep transitions and cleanup. Inspect the power-vote lifetime so Bluetooth shutdown never removes Wi-Fi's independent vote.

Progress through adapter registration, BlueZ visibility, selected-device discovery, user-approved pairing, HID input and reconnect. Test Bluetooth audio separately after its audio path exists; adapter registration does not prove A2DP, HFP or LE Audio. Finally integrate a supervised persistent service with bounded failure handling and test reboot, idle power and WLAN coexistence. Do not launch aggressive discovery or pair arbitrary nearby devices.

## 6. Workstream B — audio: find the first non-progressing stage

Files:

- `tools/hardware/run-audio-route-prepare-once.py`
- `tools/hardware/audio-progress-snapshot.py`
- `tools/hardware/test-audio-route-assessment.py`
- `docs/DRIVER_LOOP_CONTINUATION_2026-09-22.md`
- `docs/DRIVER_CHECKPOINT_2026-09-22.md`

The current blocker is not merely missing ALSA controls or additional codec firmware. PREPARE can succeed after the known two-selector route, while the stream still shows no RDMA progress. Twenty added firmware files did not resolve that stall.

Prepare one bounded, synchronized diagnostic capturing the same stream's ALSA state, application/hardware pointers, period/IRQ progress, RDMA2 enable/status, DAPM route/power state, backend DAI state, runtime PM, relevant clocks and firmware response. Use narrowly source-validated observation points. Existing register reads target 0x1200, 0x1230 and 0x1238 with metadata checks; avoid unreviewed full MMIO dumps or raw register writes.

Identify whether the first failure lies between frontend stream setup, DSP command handling, RDMA enable, backend DAI/clock activation or codec path. Treat these as hypotheses until the capture distinguishes them. Correlate timestamps rather than collecting unrelated snapshots.

The proposed test is not wholly read-only: a zero-sample stream and route selection still change device state. Inspect the exact runner and existing authorization. Keep known amplifier enables off, do not alter gains to force progress, and restore every changed selector only after the child is reaped and state is understood. If the valid existing route is not active, report that dependency rather than silently adding control writes to a read-only approval.

Do not repeat the same blind stalled experiment. Patch or improve instrumentation first. Keep abort/timeout/partial-write receipts and the existing rule rejecting aplay's misleading zero exit after a signal. Add separate `dma_progress_verified` and `physical_playback_verified` fields; a completed diagnostic is not working audio.

Do not live-unbind/rebind ABOX while its reviewed teardown leaves work or IPC registrations outstanding. Use the supported initialization order and a separately approved clean boot when necessary.

After confirmed DMA progress, validate bounded playback at conservative levels with physical confirmation, then explicit microphone capture with consent, each microphone/speaker path, volume/mute and cleanup. Develop ALSA UCM/PipeWire policy only after the underlying path works. Verify normal-user access, no unintended capture, application playback, repeated close/open, suspend/resume and Bluetooth routing separately.

## 7. Workstream C — userspace reliability, touch and power

The Pi-scoped close_range compatibility wrapper is a useful workaround, not a system-wide kernel fix. Inspect its actual behavior and the existing compiled kernel candidate before expanding scope. Maintain separate patch series so a Bluetooth deployment is not silently coupled to a syscall repair.

Add tests for subprocess creation with captured output, normal close_range and supported flag behavior, descriptor inheritance/CLOEXEC, invalid ranges, concurrency and repeated child cleanup. Fix or explicitly contain the underlying compatibility issue. Do not disable package signature checking to get pacman working.

Acceptance for package management includes an actual signed transaction, failed-signature rejection, package database consistency and clean completion on the phone, rather than host staging alone. Preserve host-verified deployment as fallback until that passes.

For touch and keys, distinguish evdev enumeration and synthetic injection from physical sensing. Prepare one owner-assisted test session: tap targets, drag, scroll, multitouch, keyboard entry, power/volume buttons, orientation changes and screen wake. Correlate real event timestamps with compositor behavior. Test touch mapping under rotation and the existing stride workaround. Confirm actual refresh behavior separately from advertised modes.

For sensors, a changing timestamp is insufficient. Test known orientation changes, light/dark response, proximity and calibrated orientation; expose them through appropriate desktop integration and power policy only after confirming data. Label unexamined sensors explicitly.

For power, record a stable idle baseline before acceleration stress. Verify supported charging/thermal protections remain active. Screen DPMS off is not suspend. Establish source-supported wake sources and an independent rescue plan before an actual suspend test. Then test repeated screen-off/on, suspend/resume, unplugged Wi-Fi use, USB unplug/replug, charge/discharge telemetry and battery drain over stated intervals. Report measured durations and conditions, not estimates disguised as battery-life acceptance.

## 8. Workstream D — GPU: preserve compute, separately solve presentation

Files:

- `docs/research/GPU_LLAMA_VULKAN_2026-09-21.md`
- `docs/research/GPU_VULKAN_WORKING_2026-09-21.md`
- `docs/GPU_SHADER_DIAGNOSTICS_2026-09-21.md`
- `docs/GPU_SUBMISSION_2026-09-21.md`

Keep two independent tracks.

**Compute:** recover/locate the exact private 0.8B model or deliberately create a new versioned baseline. The current manifest audit is 99/100 because those bytes were unavailable on the host; do not claim a fully verified artifact closure. Verify real backend selection, numerical operations and output correctness. Then measure cold and warm behavior, first-token latency, prompt processing, decoding, realistic context depths, memory, temperature, throttling and reset/fault deltas. Short warmed 38.2 versus 31.6 tokens/s does not establish sustained 4B performance; the recorded short cold completion actually favored CPU.

Advance from 0.8B to 2B and then 4B only when correctness, memory headroom and sustained tests justify it. Check the actual quantization/operator coverage, not just that model loading succeeds. Do not change the resident 4B service until an isolated service can sustain representative requests and a tested rollback exists. Account for shared memory without double-counting CPU/GPU allocations.

**Desktop:** headless Samsung Vulkan success is not a Hyprland EGL/GLES/GBM/dma-buf/presentation implementation. Identify the exact renderer and buffer-sharing requirements of this pinned compositor. Evaluate reuse of the working vendor stack only through a tested native presentation bridge, or continue the separate open-source path with new discriminating evidence. Do not replace system-wide ICD/EGL libraries or remove the working software renderer as an experiment.

The native RADV path has passed transfers but failed arithmetic after several documented variants: wave32, explicit scalar zero, low addresses and literal descriptors were not fixes. Preserve those negative results. Do not rerun a known-failing model benchmark or merely change flags without a new hypothesis and observable prediction.

Desktop acceleration acceptance requires correct presented frames, actual hardware rendering, synchronization/buffer lifetime, input responsiveness, no new resets and repeated restart/screen-power tests. Keep the stride workaround only where its assumptions remain valid.

## 9. Workstream E — NPU: ownership repair before firmware BOOTUP

Files:

- `tools/hardware/npu-boot-preflight.py`
- `tools/hardware/npu-power-enqueue-result.patch`
- `docs/research/NPU_BOOT_PREFLIGHT_2026-09-22.md`
- `docs/research/NPU_POWER_ENQUEUE_RESULT_2026-09-22.md`
- Exact pinned VS4L/NPU session, vertex, request queue and callback sources.

Resolve the reference to `NPU_CALLBACK_LIFETIME_2026-09-22.md`, which was not found in the published repository. Locate any private local design and publish a sanitized version, or write the missing design based on the exact source. Do not infer that a missing public file means no local analysis exists.

The enqueue-result patch improves error handling but retains an unbounded wait and raw session-pointer callback risk. Implement a coordinated ownership model: request identity, session/request references, completion ownership, cancellation or drain/barrier semantics, late/duplicate callbacks, close synchronization and reverse-order unwind of partially acquired power, vertex and session-manager state. Normalize protocol results and errno at the appropriate boundary after inspecting the actual callers.

A timeout or an automatic reboot timer does not cancel in-kernel work. Do not submit BOOTUP simply because a userspace timeout or sandbox exists. Use kernel-level tests supported by the pinned configuration where feasible; a host state-machine model is supplementary, not equivalent to executing the actual driver.

Cover enqueue failure, missing response, response after timeout, response racing close, duplicate completion, allocation failure, partial boot failure, firmware rejection, teardown/reset and refcount/lock/power balance. Review the patch independently before deployment and keep it separate from HCI.

Separate artifact/source preflight from deployment safety. The current preflight can exit successfully while `live_probe_validated` is false and lifecycle gaps remain. Add explicit machine-enforced readiness fields and negative tests so exit code zero can never be used as authorization to run BOOTUP. Correct the contradictory introductory wording in its documentation.

Follow the latest compiled firmware route: AIE.bin through the configured imgloader and actual PID1 firmware root. Do not revive the superseded claim that vectors.bin blocks ordinary NPU-only boot. Verify firmware authenticity/compatibility without bypassing its loader checks.

After lifecycle and deployment gates pass, validate firmware boot and shutdown, then one tiny known-compatible graph with deterministic expected output and repeatable teardown. GGUF models are not automatically supported by the Samsung NPU. Any compiler/runtime route must explicitly support this Exynos 2200 ABI; do not assume newer Exynos 2500/2600 delegates apply.

## 10. Workstream F — cellular, camera and remaining phone functions

**Cellular:** reuse the already recovered, identified radio image and library closure. Audit the matching cbd/SIPC/RIL startup and all NV create/write/fsync paths. Do not execute vendor init fragments wholesale. Do not alter EFS, cpefs, IMEI, modem calibration or security partitions. Required protected-state interactions are an explicit design/authorization issue, not a reason to bypass checks.

Proceed through separately measured CP INIT -> BOOTING -> ONLINE, SIM registration, data with DNS/HTTPS forced over the cellular interface, approved SMS, and approved outgoing/incoming calls with two-way audio. LTE data does not prove voice or IMS/VoLTE. Record actual carrier, network and IMS conditions privately as needed; redact identifiers from public evidence. Do not test emergency numbers or contact arbitrary recipients. Telephony architecture must remain explicit about any vendor/binder/service dependency.

**Camera:** inventory the actual sensor, media graph, ISP firmware and matching tuning/calibration requirements for each lens. Video codec/JPEG/ISP nodes alone do not demonstrate a camera. First aim for one real captured frame with owner consent and repeatable stream start/stop, then exposure/focus, each sensor, video and a usable application interface. Choose native V4L2/libcamera integration only if the actual pipeline supports it; otherwise document the exact isolated vendor dependency rather than claiming a generic camera package solves it.

**Other hardware:** create explicit entries for GNSS, proximity, haptics, NFC, fingerprint, USB host/device roles, external display where hardware-supported, sensors and storage health. Mark them `not_examined` or `blocked` until examined. Do not invent support or declare them permanently impossible. Treat biometric security separately from merely reading a sensor; do not weaken secure-world or key-protection boundaries.

**Standalone boot:** normal cold power-on, reboot and power-off charging remain separate from successful recovery-target reboots. Study the failed BOOT packaging/handoff history offline and preserve native RECOVERY as rescue. A new normal-BOOT experiment requires its own reviewed packaging, rollback and physical rescue opportunity; never try the restored Android BOOT against Linux userdata as a shortcut. Finish cold-boot integration without formatting working storage or altering bootloader/security partitions.

## 11. Test infrastructure and acceptance matrix

Implement a hardware-free CI entrypoint with explicit dependencies and no firmware/model/device access. GitHub Actions had no run history at the reviewed checkpoint. Add suitable host CI where the repository permits it; keep proprietary assets and device tests out of public jobs. Private full-kernel builds may use existing authorized local infrastructure.

Keep static/source-contract tests, executable unit tests, full builds, on-device tests and physical acceptance separate. Discover existing tests before adding duplicate suites. Publish exact commands, environment, passed/failed/skipped counts and skip reasons. Do not claim tests were run when only their source was inspected.

Minimum acceptance matrix:

| Area | Functional evidence | Persistence / stress evidence |
|---|---|---|
| Native system | Correct hardware, kernel, PID1 and native userspace; no Android host services | Recovery restart; later normal cold boot, shutdown and recovery path |
| Display / input | Correct presented image, physical touch/keys/keyboard and rotation | Repeated compositor restart and screen power transitions |
| Wi-Fi / remote access | Association, native/Arch DNS and interface-forced HTTPS; independent rescue | Unplugged use, reconnect/roaming, reboot and later suspend/resume |
| Bluetooth | Real HCI, selected-device discovery/pairing, HID; audio separately | Reconnect, repeated start/stop, WLAN coexistence and idle power |
| Audio | DMA/period advance, physically confirmed speaker and consented microphone tests | Close/open, application integration, volume/mute and suspend/restart |
| GPU compute | Device identity, numerical correctness, representative model output | Cold/warm and sustained workloads, memory/thermal/reset checks |
| Desktop acceleration | Hardware renderer and correct presentation/synchronization | UI interaction, restart, screen power and thermal coexistence |
| NPU | Safe lifecycle, firmware boot/shutdown, known graph output | Race/failure tests, repeated inference and clean resource release |
| Sensors / GNSS | Physical stimulus/calibration and real location fix where applicable | Autostart, power behavior and application integration |
| Cellular | Modem online, SIM, forced-interface data, approved SMS/calls, IMS separately | Reconnect, reboot, incoming calls and usable two-way audio |
| Cameras | Actual sensor frames and repeated stream control | Lens/application integration, restart and video where supported |
| Power | Charging/thermal controls, wake sources, actual suspend/resume | Stated unplugged idle/use intervals and repeated cycles |
| Userspace | Reliable subprocesses and signed package transactions | Failed-update handling, package integrity and reboot |
| Security | Least-privileged apps, private credentials, safe device-node access and screen lock | No global exposure of services, secrets or sensitive hardware |

Each experiment needs an objective, one changed variable, an expected observation, before/after health, exact artifact identity, a duration bound and cleanup/readback evidence. A userspace timeout is not kernel cancellation. A fault, panic or unknown cleanup state is a failed/blocked result, not a pass because SSH eventually returned.

Physical stimuli must be labeled accurately. Synthetic touch does not prove finger sensing; zero samples do not prove sound; library load does not prove NPU/cellular; fd open does not prove inference; enumeration does not prove camera; screen off does not prove suspend; a complete GPU fence does not prove shader output.

## 12. Deliverables and immediate execution order

Start with these deliverables, adapting existing filenames rather than duplicating systems:

1. Canonical current acceptance matrix and corrected status pointers.
2. Hardened, host-tested RECOVERY deployment/build checks, including optimization-mode negative tests and explicit AVB validation.
3. HCI patch review, precise candidate provenance and smallest runtime test plan; deployment only when its gates are met.
4. One improved synchronized audio diagnostic with separate DMA/physical acceptance and robust cleanup classification.
5. Independent NPU ownership/unwind design and executable regression plan, not another naive timeout patch.
6. Userspace/package, physical input, GPU and power follow-ups with concrete tests, plus cellular/camera ownership and milestones.

Execute the mandatory Luna-only subagent plan in section 1.1, starting with four bounded host workstreams when supported and using resource-limited waves for the rest. Give every worker a narrow file scope, explicit acceptance and a report format. Independent review can challenge results but is not itself evidence that a driver works. Only the coordinator can integrate/push shared-branch changes, promote a candidate or run device mutation.

Do not bury substantive driver work under endless checklist rewriting. Once the foundational gates exist, implement the next small, testable improvement, record results and move to the next open dependency. When physical availability blocks one lane, continue the other lanes and collect necessary owner actions into one concise request.

Use the user's established branch/push authorization; do not assume a new permission to alter protected branches or publish private assets. Keep patches reviewable and independent, verify pushes against the remote, and record the commit SHA when publishing. No firmware, recovery images, model bytes, raw microphone/camera data, keys, personal network identities or private traces in public Git.

End each checkpoint with:

- What changed, with source/build/commit identities.
- Tests actually executed and outcomes at the correct evidence level.
- Features newly accepted, features unchanged and regressions.
- Current running image/kernel/boot state and independent rescue health.
- Every changed control/firmware/power state and cleanup result.
- Exact next action per blocked subsystem, including the smallest owner-assisted test needed.

The goal remains a fully working standalone S22. Do not promise that a green build or a growing control count achieves that goal; work through the end-to-end acceptance matrix until the evidence does.
