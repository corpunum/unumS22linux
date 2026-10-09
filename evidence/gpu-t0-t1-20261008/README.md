# GPU T0 and T1 — 2026-10-08 (lead session)

Plan: `openunum-briefs/s22-gpu-plan-20261008.md`. Both trials ran through a copy of
`tools/gpu-compat/run-trial.py` (same isolation helper and kernel-delta gate) whose
receipts live outside the repo in `/home/corpunum/s22-gpu-trials-20261008/`. The CPU
server on :8089 (resident Qwen3.5-4B Q4_K_M, profile `qwen4b`) was not touched. The
volume stayed at 0. Curated receipts: [receipts.json](receipts.json).

## T0: Vulkan capability inventory — PASS
New `--caps` mode in `tools/gpu-compat/vulkan-hal-probe.c` (instance and physical-device
queries only; no `vkCreateDevice`, no allocation, no submission). Staged as the new file
`vulkan-headless/system/bin/vulkan-hal-caps` (sha256 `2811a1bc…66f4`). Exit 0, same boot,
no GPU kernel messages. [Full output](t0-caps.txt).
- Samsung Xclipse 920 (vendor 0x144d, device 0x73a0), Vulkan 1.3.279, driver
  "Samsung Proprietary" 24.3.9 (`e5014ade39`), 141 device extensions.
- `VK_EXT_physical_device_drm`: primary 226:0, render 226:128.
- Every extension a wlroots Vulkan renderer needs is advertised: external_memory_fd,
  external_memory_dma_buf, image_drm_format_modifier, queue_family_foreign,
  external_semaphore_fd, external_fence_fd, timeline_semaphore, synchronization2. No
  `VK_KHR_swapchain` (expected for the HAL without Android WSI).
- B8G8R8A8/R8G8B8A8_UNORM: only the LINEAR modifier (0x0) is offered. A LINEAR
  dma_buf image up to 16384x16384 is exportable and importable (features 0x7). This
  matches the DPU's LINEAR scanout requirement.
- `VK_KHR_shader_integer_dot_product` is advertised (ggml reported "int dot: no"; worth a
  check in the llama build). No `VK_KHR_cooperative_matrix`.
- Memory: one 5980 MiB device-local+host heap (UMA) and a 64 MiB heap.

## T1: Qwen3.5-2B Q4_0, GPU vs CPU (short context) — NOT PASSED
A hash-checked copy (sha256 `cd70221b…9bb1`) was staged in the llama-vulkan root, then
removed afterwards (rollback done). Same accepted `llama-bench`, bridge v4, `-t 4`.

| run | pp128 t/s | tg64 t/s | layers |
|---|---|---|---|
| GPU `-ngl 99` | 7.26 | 14.61 ± 0.61 | 25/25 on Vulkan0 (1148 MiB) |
| CPU control (`--device none -nopo 1 -nkvo 1`) | 77.85 ± 8.81 | 11.69 ± 0.75 | 0/25 |

- Generation on the GPU is 1.25x the CPU. Prompt processing on the GPU is about 10x
  slower at the default batch (512). The accepted 0.8B run used `-b 32 -ub 32` and got
  322 t/s, so the large-batch matrix-matrix path is the suspect.
- The gate failed: 143 new kernel lines during the GPU run. None are faults or resets.
  - `thermal_G3D` capped the GPU's max frequency, stepping between 1306 and 807 MHz
    for the whole run.
  - Samsung's `mm_debug` "low file detected" dump fired, because the GPU run's 1.65 GB
    of UMA plus the resident 4B server (about 2.8 GB) pushed the file cache under
    300 MB. Nothing was killed.
- The 4096-depth runs were not done: the gate failed, and at 7 t/s a 4096-token prefill
  would not fit the deadline.
- Decision for Claude: T2 is not proposed on these numbers. A cheap next candidate is
  T1b: the same runs with `-b 32 -ub 32` (as in the accepted 0.8B run) to separate the
  slow prefill from the thermal cap. It needs a ruling on whether thermal-QoS lines and
  the low-file diagnostic count as gate failures.
