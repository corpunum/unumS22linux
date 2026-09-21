# Xclipse submission and fence repair — 2026-09-21

**Partial result, not GPU acceleration for the desktop or model.** Four separate
native GPU transfer runs passed exact 256-word readback with real kernel fences.
The compute shader still fails. Hyprland remains software-rendered and the
resident Qwen model remains CPU-only. NPU inference was not tested in this round.

Follow-up: [shader diagnostics at 04:28–04:49 UTC](GPU_SHADER_DIAGNOSTICS_2026-09-21.md)
confirmed CP-visible descriptor/code bytes and setup registers, but the tested
shader changes still failed. No GPU acceleration has been enabled.

Runs were at 21:57–22:12 UTC on September 20 (September 21 in Athens), on the
same native RECOVERY boot, BORE760. No reboot, image flash, module replacement,
partition write, desktop restart or model restart was performed.

## Source and artifacts

- Device: SM-S901B/DS, Exynos 2200 / Xclipse 920, not S22+.
- Kernel: `4e5c5ad7d950e4de0688b5663965f2075654b2ad`; the relevant implementation
  is `drivers/gpu/drm/samsung/sgpu`, not the parallel upstream amdgpu tree.
- Mesa fork: <https://github.com/mxxme-dev/radv-xclipse-patches>, base
  `d1b295e8c61c013c454e8015983a1d6b6df82bf2` (Mesa 24.3.4).
- Cumulative patch, including prior lifecycle repair and opt-in tracing:
  [radv-xclipse-bolist-native-sync.patch](../tools/omarchy-trial/radv-xclipse-bolist-native-sync.patch).
- Final AArch64 musl ICD, local-only:
  `tools/omarchy-trial/radv-xclipse-bolist-native-sync.so`, SHA256
  `ffc1f7ea42afa180d924875b1ef9655511c8a560c46c9077ae8f89e1b7f5f5d0`.
- Transfer-capable probe, local-only: `gpu-compute-probe-transfer-aarch64-musl`,
  SHA256 `2b7aa71ee91aa4f3852f14c601df1908575cd9ffc417b9c76239be9953a78978`.

The candidate lives only under `/srv/s22/gpu-bolist-20260921` on userdata.
Stock Mesa, Hyprland's libraries and boot services were not replaced. Its ICD
and library path are selected per process; nothing enables it at boot. Raw
kernel captures remain private in that directory, not in GitHub.

## Repairs and evidence

1. **Restore the BO list.** The fork populated `bo_list_in` but omitted the
   `AMDGPU_CHUNK_ID_BO_HANDLES` submission chunk. Samsung accepts this chunk;
   `amdgpu_cs_parser_bos()` otherwise creates an empty list. Its VM update path
   iterates that list to validate/update resource mappings. The patch submits
   the real handles. This is a source-backed repair, not proof that all VM
   faults are fixed: a later rejected experiment still hit a CPF fault.

2. **Keep timeline capabilities consistent.** Winsys forcibly set
   `has_timeline_syncobj=false`, while `vk_drm_syncobj_get_type()` retained the
   timeline feature. The submit path classified the application's fence as a
   timeline signal, but selected the binary OUT allocator, which omitted it
   and included only the queue syncobj. This explains the previous `EINVAL`
   even when the kernel reported real completion. Removing that override
   changes OUT chunk 5 to timeline OUT chunk 9 and includes the application
   fence. The native transfer test then passes both wait and readback.

3. **Remove synthetic completion.** There is no forced `expired=1` or manual
   `drmSyncobjSignal()` in the repaired CS path. The real fence query is retained
   as a diagnostic. The ordinary wait again uses `WAIT_FOR_SUBMIT`; the empty
   submit path transfers kernel fences instead of eagerly signaling them, and
   normal queue waits are restored. The prior matched libdrm CPU map/unmap
   lifecycle repair is preserved.

The probe refuses CPU Vulkan devices. Its transfer-only mode initializes
256 words to `0xA5`, submits `vkCmdFillBuffer(0x12345678)`, applies a
transfer-to-host barrier, waits at most three seconds and verifies every word.
This 1 KiB workload uses RADV's CP DMA path, not its compute-fill threshold.
This conclusion comes from the pinned source's `radv_prefer_compute_dma()` and
`radv_fill_buffer()` in `src/amd/vulkan/meta/radv_meta_buffer.c`, not a claim
that transfer correctness establishes shader execution.
The outer process limit is 15 seconds. A failed wait after submission does not
destroy still-pending Vulkan objects; process exit handles resource release.

| Test | Actual result |
| --- | --- |
| BO-list repair, old inconsistent fence capabilities | Compute fails; SQC read fault `0x0000800018040000` |
| Instrumented transfer before timeline repair | Real query `expired=1`; Vulkan wait still `-13`; not a pass |
| Native timeline repair, transfer | All 256 values correct; `expired=1`; exit 0 |
| Native timeline repair, compute | Fence completes; first output still `0xA5A5A5A5`, expected 7; TCP write fault `0x0000ff787ad50000`; exit 4 |
| Diagnostic WC shader/descriptor buffers | Timeout, automatic kernel GPU resets and CPF fault; rejected |
| Original repaired candidate restored, three transfer repeats | 3/3 exact passes, checksum `0xd1cc9f9595a48f83`, no new GPU errors in captured kernel delta |

Evidence: [first transfer pass](../evidence/gpu-bolist-20260921/transfer-native-sync.txt),
[three repeats](../evidence/gpu-bolist-20260921/transfer-repeat.txt),
[compute failure](../evidence/gpu-bolist-20260921/compute-native-sync.txt),
[rejected WC experiment](../evidence/gpu-bolist-20260921/compute-wc.txt).
The rejected WC option is absent from the final patch/binary. No manual GPU
reset was requested; resets in failed tests were the kernel's own recovery.
Public captures replace the boot UUID with `BOOT_SESSION_A` and redact CPU
mapping pointers. GPU VAs and shader/descriptor words are retained because
they are necessary to compare the faulting data path.

## Remaining boundary

This is not a conformant or production-ready Vulkan driver. Shader/resource
memory correctness is unresolved; neither `llama.cpp -ngl` nor GPU desktop
rendering is enabled. A completed fence alone is insufficient, as the failed
compute readback demonstrates. No GPU model throughput is claimed.

The optional kernel user-fence chunk remains disabled. Empty submissions,
binary-semaphore waits, multi-queue synchronization, WSI and long-running
workloads have not been accepted. The fork's empty-submit binary-wait merge
and zero-time sync-file fallback also need review before general application
use. The tested workload does not cover those paths.

Address tracing shows a consistent descriptor-set pointer
`0xffff800000040000`, `COMPUTE_USER_DATA_2=0x00040000`,
`address32_hi=0xffff8000`, and output descriptor for
`0xffff800100010000`. The complete encoded shader is recorded. Neither an
address truncation, altered SH_MEM register, low-VA policy nor speculative
cache workaround has been accepted. The subsequent diagnostic round compared
GPU-visible descriptor/code contents and setup registers with these CPU-side
bytes; they matched, but shader correctness remained unresolved. See the
follow-up report above; do not repeat model offload attempts against this
known failure.

## Reproduction on this workstation

The patch applies directly to the pinned base, not on top of the previous
lifecycle patch. `git apply --check` passed in a clean pinned-base worktree.
The existing configured build is `/tmp/radv-cross-arm-build.trZFhU`, using
`/tmp/radv-cross-arm.ini`, the `/tmp/radv-native-root.2ZvJzf` musl sysroot,
Clang/LLD, ACO, and no LLVM or WSI. Rebuild:

```sh
ninja -C /tmp/radv-cross-arm-build.trZFhU -j4
```

The probe source and shader are in `tools/gpu-compute-probe`. After independently
checking the candidate hash and phone health, the accepted transfer invocation
inside its isolated staging directory is:

**Diagnostic only:** even this experimental driver can fault/reset the GPU.
Do not add this invocation to boot services or use it to enable acceleration.

```sh
timeout -s KILL 15 env \
  LD_LIBRARY_PATH=/srv/s22/gpu-bolist-20260921/lib \
  VK_ICD_FILENAMES=/srv/s22/gpu-bolist-20260921/radv-candidate.json \
  MESA_SHADER_CACHE_DISABLE=true S22_GPU_TRANSFER_ONLY=1 \
  ./gpu-compute-probe-transfer-aarch64-musl ./compute.spv
```

Final live checks: same boot ID/BORE760, CPU model health `ok`, WPA state
`COMPLETED`, Hyprland and Quickshell running, battery temperature 30.0 C.
At 22:14:50 UTC, TLS-verified HTTPS explicitly bound to `wlan0` returned HTTP
200; Hyprland, Quickshell and Squeekboard were still running and the staged
ICD hash still matched. See [final health](../evidence/gpu-bolist-20260921/final-health.txt).
These are point checks, not renewed acceptance of all everyday hardware.
Host compilation with `-Wall -Wextra -Werror` and three argument-rejection
checks passed without loading Vulkan or running any host GPU workload.
