# Driver and model evidence report — 2026-09-20

This is a bounded evidence report, not a blanket pass. Measurements below are
from the staged phone runs and host-side artifacts; they do not establish
production persistence or complete hardware acceleration.

## Current handoff state

The phone is left running the real Omarchy Quickshell bar, Hyprland, foot and
Squeekboard on its internal 1080x2340 display. Rendering uses llvmpipe, not
the GPU. The native Alpine/guardian boot hosts a RAM-staged Arch userspace;
this is not a complete persistent Omarchy installation. No image was flashed
during this work. One targeted software recovery reboot cleared the stalled
package helper; BORE record **518** confirms RECOVERY at 23:02:18 UTC.

The visible chat now uses a resident, CPU-only Qwen3.5-2B Q4_0 server with
4096 context, one slot and four fast cores. Its listener is restricted to
127.0.0.1:8089. Two short requests produced first text in **0.6 / 0.4 s** and
complete answers in **1.1 / 3.7 s**. A keyboard-submitted `hi` completed in
**1.3 s**, first text **0.7 s**. These are latency samples, not quality or
autonomous-agent benchmarks. The client executes no model-generated commands.

Evidence: [resident-server-chat.txt](../evidence/driver-model-20260920/resident-server-chat.txt),
[screen capture](../evidence/driver-model-20260920/omarchy-resident-chat.png),
and [touch-path test](../evidence/driver-model-20260920/resident-chat-touch.txt).
The keyboard appears automatically after session startup. This verifies
synthetic events through the real input device and GUI, not physical finger
sensing. `/quit` opens a Linux shell in the terminal.

GPU compute still fails after a real userspace lifecycle bug was fixed.
NPU inference has not run. Wi-Fi, cellular, audio, cameras and suspend remain
unaccepted. The approximately 583 MiB CACHE overlay cannot store the full
Arch desktop and 1.2 GB model. Both remain in RAM and require host restoration
after reboot. Existing BOOT, MISC and user data were not repurposed.

## CPU model baseline

The ARM64 CPU runtime is llama.cpp revision
`e613ef2c81bae98d59850d061ac29e6e3e88cb00`. The optimized sibling was built
with `GGML_NATIVE=OFF`, `GGML_CPU_ARM_ARCH=armv8.6-a+dotprod+i8mm`, and CPU
feature use was checked against the phone-reported feature set. Models are
Qwen3.5 Q4_0 GGUF files with provenance and hashes in
[`evidence/model-bench-20260920/MANIFEST.sha256`](../evidence/model-bench-20260920/MANIFEST.sha256).

| Model/runtime | Workload | Measured decode |
| --- | --- | ---: |
| Qwen3.5-0.8B optimized | 4 threads, short decode, 2 repetitions | 20.21 tok/s |
| Qwen3.5-0.8B baseline | 4 threads, short decode, 2 repetitions | 15.65 tok/s |
| Qwen3.5-2B optimized | 4 threads, short decode, 2 repetitions | 10.13 tok/s |
| Qwen3.5-2B optimized | depth 4096, 256 tokens, 3 repetitions | 5.467 tok/s |

The depth-4096 run is a decode-depth benchmark, not proof of a separately
allocated 4K context setting. The 2B depth run's recorded battery temperature
rose from 32.2 C to 39.7 C. Short-run JSON and thermal/memory evidence are in
[`evidence/driver-model-20260920/optimized-08-t4-session.txt`](../evidence/driver-model-20260920/optimized-08-t4-session.txt),
[`optimized-2b-t4-session.txt`](../evidence/driver-model-20260920/optimized-2b-t4-session.txt),
and [`optimized-2b-depth4096-session.txt`](../evidence/driver-model-20260920/optimized-2b-depth4096-session.txt).

The native chat launcher and model runtime are CPU-only and staged in
`rootfs/model-bench*`; RAM staging was verified separately. The model run is
not an agent benchmark and does not claim a persistent installed service.
Entrypoints are [`tools/model-bench/run-arm64-bench.sh`](../tools/model-bench/run-arm64-bench.sh),
[`run-arm64-bench-optimized.sh`](../tools/model-bench/run-arm64-bench-optimized.sh),
and [`run-arm64-completion.sh`](../tools/model-bench/run-arm64-completion.sh).

## Display and desktop evidence

Successful bounded sessions were recorded for Hyprland and the Omarchy
configuration using the GCC16-patched Aquamarine path. The live checks and
screenshots are [`hyprland-gcc16-live-check.txt`](../evidence/driver-model-20260920/hyprland-gcc16-live-check.txt),
[`hyprland-gcc16-display.png`](../evidence/driver-model-20260920/hyprland-gcc16-display.png),
[`omarchy-gcc16-live-check.txt`](../evidence/driver-model-20260920/omarchy-gcc16-live-check.txt),
and [`omarchy-gcc16-display.png`](../evidence/driver-model-20260920/omarchy-gcc16-display.png).
These prove the bounded sessions and captured frames only; they do not prove
reboot persistence, a final default desktop, or all input/session behavior.

Synthetic touchscreen events were deliberately marked non-physical. The
metadata records `physical_touch_verified: false`; see
[`synthetic-touch.txt`](../evidence/driver-model-20260920/synthetic-touch.txt)
and [`synthetic-touch-cleanup.txt`](../evidence/driver-model-20260920/synthetic-touch-cleanup.txt).
The GUI was restored to Alpine between bounded tests. The final handoff
session instead remains running until closed; its harness restores Alpine
Weston if Hyprland exits.

## GPU driver experiments

RADV/Vulkan device enumeration succeeded and the patched compute probe reached
real command submission, but the tested fence path faulted or timed out. The
recorded result is an experiment outcome, not a working GPU inference result:
[`patched-radv-compute.txt`](../evidence/driver-model-20260920/patched-radv-compute.txt),
[`patched-radv-compute-traced.txt`](../evidence/driver-model-20260920/patched-radv-compute-traced.txt),
and [`radv-lifecycle-real-fence-compute.txt`](../evidence/driver-model-20260920/radv-lifecycle-real-fence-compute.txt).

The mapping-lifecycle fix was recorded with exit 0 for its bounded record path,
while the later real-fence path still exposed a GPU page fault/job timeout.
The 32-bit address hypothesis was explicitly recorded and is not supported by
the evidence: [`radv-address32-record-only.txt`](../evidence/driver-model-20260920/radv-address32-record-only.txt).
Any combined-compute result appearing after this report must be treated as a
new measurement; it must not be inferred from enumeration, record-only output,
or the lifecycle fix.

## NPU status

Recovered ENN/NPU assets, ELF dependencies, module strings, and firmware
provenance are documented under [`evidence/npu-audit-20260920`](../evidence/npu-audit-20260920).
The downstream driver source contains an exact `vectors.bin` firmware request,
but that records the driver's requested path—not proof that every execution
mode uses it, nor proof that the file is the only missing runtime input. The
recovered AIE/DSP assets and Android-specific VS4L/imgloader/device ABI remain
insufficient for a standalone Linux ENN inference probe. No NPU inference
success is claimed.

## Reproducibility and gaps

Runtime/model hashes, source revisions, build flags, and glibc closure are in
[`evidence/model-bench-20260920/provenance.txt`](../evidence/model-bench-20260920/provenance.txt)
and [`tools/model-bench/README.md`](../tools/model-bench/README.md). The staged
runtime is an isolated glibc closure and does not replace Alpine system
libraries. Remaining unproven items include reboot persistence, physical touch,
stable GPU compute/fence execution, GPU-backed llama.cpp inference, complete
NPU firmware/runtime closure, and a production desktop service configuration.
