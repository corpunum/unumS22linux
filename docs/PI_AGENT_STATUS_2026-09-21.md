# Omarchy UI and Pi agents on a native-Linux Samsung S22

This is an experimental community port on the **Exynos Galaxy S22
SM-S901B/DS**, not S22+, not an official Omarchy phone release, and not a
complete daily-driver replacement.

## What is running

- Native Alpine ARM64 with a Samsung/Lineage kernel, booting from RECOVERY
  without Android services.
- Persistent Arch Linux ARM, Hyprland and actual Omarchy Quickshell UI,
  with a phone-specific terminal adapter and Squeekboard.
- Pi0.86.1 launched through Omarchy's agent/default-selector commands as an
  unprivileged user. The default local model is Qwen3.5-4B Q4_K_M on CPU;
  the retained2B model is stopped to avoid co-resident memory exhaustion.
- An optional Pi provider reaches the owner's larger Qwen rig over Tailscale.
  Tools still execute on the phone; remote rig inference is not phone GPU use.
- Wi-Fi association, DHCP, DNS and HTTPS; authenticated Tailscale and remote
  OpenSSH access. No personal addresses or account state are included here.

Recorded recovery reboot BORE761 automatically restored the desktop, local4B,
Pi, Wi-Fi and Tailscale without physical intervention. Network checks passed
after more than60s uninterrupted uptime. A post-reboot Pi task actually called
the phone-status tool and correctly reported health, memory and battery in
78.46s. A separate rig-backed task took10.94s before reboot. These are single
observations with different model/context/cache conditions, not a controlled
speed comparison or a general agent-reliability benchmark.

## Agents helping with GPU/NPU work

Through Pi on the phone, Qwen reviewed supplied driver evidence and generated
an offline trace checker, unit tests and a GPU/NPU report. Human-directed
orchestration caught false transfer-pass verdicts and an incorrect description
of completed-but-wrong shader output as a timeout. Both were corrected.

The final focused host suite passed37 tests, including11 independent trace
checks,3 provider checks and existing launcher/model/supervisor checks. Qwen's
ten generated tests separately passed on the host against the reviewed checker.
On-phone,9 in-process tests passed;1 subprocess test was deliberately skipped.
An earlier Python child-creation hang required the recovery reboot. That
runtime problem is unresolved. The broad initial agent task also needed
intervention after repeated reads; this was supervised assistance, not an
autonomous driver repair.

**GPU transfer/readback and real fences work in isolated diagnostics.
Compute shaders still fail; no local-model GPU acceleration is accepted.**
See the [shader evidence](GPU_SHADER_DIAGNOSTICS_2026-09-21.md) and
[submission/fence work](GPU_SUBMISSION_2026-09-21.md). Shader/VM execution-time
evidence is the next useful investigation, not another benchmark of a known
failing compute path.

**NPU inference is not working.** The exact ABI, usable native runtime or
compatible runtime environment, compiled graph format and applicable firmware
remain blockers. Related kernel sources are not proof of binary compatibility.
Missing captured firmware files are not automatically mandatory in every mode.
No guessed ioctl, NPU activation or new GPU experiment was performed by Pi.

Driver contributors are welcome, especially with Samsung SGPU/Xclipse920,
Mesa RADV or Exynos2200 ENN/VS4L experience. Reproducible traces and small
source-level hypotheses are more useful than unverified acceleration claims.

## Limits and publication

Normal cold-power-on routing, cable-free operation and suspend/resume remain
unaccepted. Physical touchscreen acceptance, audio, Bluetooth, cellular calls
and camera are not established by a running desktop or synthetic input.
The desktop remains software-rendered. Do not flash another phone with these
device-specific artifacts.

This is a deliberately small, manually sanitized milestone summary based on
the recorded local test receipts. The integration source and full new agent
transcripts remain in the active development checkout pending their separate
publication review; this update does not claim a complete Pi installer.
No phone changes, runtime tests or driver experiments were made during this
publication-only pass. See [publication/privacy scope](PUBLICATION.md), including
history-cleanup and existing-clone precautions.
